"""Person B: resumable single-primary-key batch copy and event replay."""
import json
from uuid import UUID

from psycopg import sql
from psycopg.types.json import Jsonb

from schemamigrate.state import META, get_migration
from schemamigrate.sql_resources import load_sql

DEFAULT_BATCH_SIZE = 1000


def copy_batch(connection, migration_id, batch_size=DEFAULT_BATCH_SIZE):
    if not 1 <= batch_size <= 10000:
        raise ValueError("Batch size must be from 1 to 10000.")

    row = get_migration(connection, migration_id, lock=True)
    schema, source_name, shadow_name, _, columns, keys, last_key, copied, done, status = row

    if status not in ("READY_FOR_COPY", "COPYING"):
        if done:
            return {
                "copied_this_batch": 0,
                "copied_total": copied,
                "copy_complete": True
            }
        raise ValueError(f"Cannot copy rows when status is {status}.")

    key = keys[0]
    source = sql.Identifier(schema, source_name)
    shadow = sql.Identifier(schema, shadow_name)
    collist = sql.SQL(", ").join(map(sql.Identifier, columns))

    
    if last_key is None:
        template = "select_first_batch"
        params = (batch_size,)
    else:
        template = "select_after_key"
        params = (Jsonb({key: last_key["value"]}), batch_size)

    query = load_sql("copy_api.sql", template).format(
        columns=collist,
        source=source,
        key=sql.Identifier(key)
    )

    with connection.cursor() as cur:
        cur.execute(query, params)
        batch = cur.fetchall()

       
        if not batch:
            cur.execute(
                load_sql("copy_api.sql", "copy_complete").format(meta=META),
                (UUID(str(migration_id)),)
            )
            return {
                "copied_this_batch": 0,
                "copied_total": copied,
                "copy_complete": True
            }

       
        updates = [c for c in columns if c != key]

        if updates:
            assignments = sql.SQL(", ").join(
                sql.SQL("{} = EXCLUDED.{}").format(
                    sql.Identifier(c), sql.Identifier(c)
                )
                for c in updates
            )
            action = sql.SQL("DO UPDATE SET ") + assignments
        else:
            action = sql.SQL("DO NOTHING")

       
        values = sql.SQL(", ").join(
            sql.Placeholder() for _ in columns
        )

        insert = load_sql("copy_api.sql", "insert_batch").format(
            target=shadow,
            columns=collist,
            values=values,
            key=sql.Identifier(key),
            conflict_action=action
        )
        cur.executemany(insert, batch)

        
        final_key = batch[-1][columns.index(key)]

        cur.execute(
            load_sql("copy_api.sql", "save_copy_progress").format(meta=META),
            (
                Jsonb({"value": final_key}, dumps=lambda v: json.dumps(v, default=str)),
                len(batch),
                UUID(str(migration_id))
            )
        )

    return {
        "copied_this_batch": len(batch),
        "copied_total": copied + len(batch),
        "copy_complete": False
    }



def _typed_key(schema, table, key, value):
    row_type = sql.Identifier(schema, table)
    expr = sql.SQL("(jsonb_populate_record(NULL::{}, %s::jsonb)).{}").format(
        row_type, sql.Identifier(key))
    return expr, Jsonb({key: value})


def _apply_event(connection, migration, direction, old_row, new_row):
    schema, source_name, shadow_name, old_table, columns, keys = migration[:6]
    key = keys[0]
    target_name = shadow_name if direction == "FORWARD" else old_table
    if not target_name:
        raise ValueError("The rollback target table is not set.")
    
    delete_old = old_row is not None and (new_row is None or old_row.get(key) != new_row.get(key))
    with connection.cursor() as cur:
        if delete_old and old_row.get(key) is not None:
            expr, val = _typed_key(schema, target_name, key, old_row[key])
            cur.execute(load_sql("catchup_api.sql", "delete_target_key").format(
                target=sql.Identifier(schema, target_name), key=sql.Identifier(key), typed_key=expr), (val,))
        if new_row is None or new_row.get(key) is None:
            return
        expr, val = _typed_key(schema, source_name, key, new_row[key])
        select = load_sql("catchup_api.sql", "find_current_row").format(
            columns=sql.SQL(", ").join(map(sql.Identifier, columns)),
            source=sql.Identifier(schema, source_name), key=sql.Identifier(key))
        cur.execute(select, (val,))
        current = cur.fetchone()
        if current is None:
            return
        updates = [c for c in columns if c != key]
        action = (sql.SQL("DO UPDATE SET ") + sql.SQL(", ").join(
            sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(c), sql.Identifier(c)) for c in updates
        )) if updates else sql.SQL("DO NOTHING")
        insert = load_sql("catchup_api.sql", "upsert_current_row").format(
            target=sql.Identifier(schema, target_name),
            columns=sql.SQL(", ").join(map(sql.Identifier, columns)),
            values=sql.SQL(", ").join(sql.Placeholder() for _ in columns),
            key=sql.Identifier(key), conflict_action=action)
        cur.execute(insert, current)


def catch_up(connection, migration_id, direction="FORWARD", batch_size=500):
    if direction not in ("FORWARD", "REVERSE"):
        raise ValueError("direction must be FORWARD or REVERSE")
    mid = UUID(str(migration_id))
    total = 0
    while True:
        migration = get_migration(connection, mid, lock=True)
        with connection.cursor() as cur:
            cur.execute(load_sql("catchup_api.sql", "pending_events").format(meta=META),
                        (mid, direction, batch_size))
            events = cur.fetchall()
        if not events:
            return total
        for event_id, old_row, new_row in events:
            _apply_event(connection, migration, direction, old_row, new_row)
            with connection.cursor() as cur:
                cur.execute(load_sql("catchup_api.sql", "mark_event_processed").format(meta=META),
                            (event_id,))
            total += 1
