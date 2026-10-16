"""Person B: resumable single-primary-key batch copy."""
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
    schema, source_name, shadow_name, _old, columns, keys, last_key, copied, done, status = row

    if status not in ("READY_FOR_COPY", "COPYING"):
        if done:
            return {"copied_this_batch": 0, "copied_total": copied, "copy_complete": True}
        raise ValueError(f"Cannot copy rows when status is {status}.")

    key = keys[0]
    source = sql.Identifier(schema, source_name)
    shadow = sql.Identifier(schema, shadow_name)
    collist = sql.SQL(", ").join(map(sql.Identifier, columns))

    if last_key is None:
        query = load_sql("copy_api.sql", "select_first_batch").format(
            columns=collist, source=source, key=sql.Identifier(key))
        params = (batch_size,)
    else:
        query = load_sql("copy_api.sql", "select_after_key").format(
            columns=collist, source=source, key=sql.Identifier(key))
        params = (Jsonb({key: last_key["value"]}), batch_size)

    with connection.cursor() as cur:
        cur.execute(query, params)
        batch = cur.fetchall()
        if not batch:
            cur.execute(load_sql("copy_api.sql", "copy_complete").format(meta=META),
                        (UUID(str(migration_id)),))
            return {"copied_this_batch": 0, "copied_total": copied, "copy_complete": True}
        updates = [c for c in columns if c != key]
        action = (sql.SQL("DO UPDATE SET ") + sql.SQL(", ").join(
            sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(c), sql.Identifier(c)) for c in updates
        )) if updates else sql.SQL("DO NOTHING")
        values = sql.SQL(", ").join(sql.Placeholder() for _ in columns)
        insert = load_sql("copy_api.sql", "insert_batch").format(
            target=shadow, columns=collist, values=values, key=sql.Identifier(key), conflict_action=action)
        cur.executemany(insert, batch)
        final_key = batch[-1][columns.index(key)]
        cur.execute(load_sql("copy_api.sql", "save_copy_progress").format(meta=META), (
            Jsonb({"value": final_key}, dumps=lambda v: json.dumps(v, default=str)),
            len(batch), UUID(str(migration_id))))
    return {"copied_this_batch": len(batch), "copied_total": copied + len(batch), "copy_complete": False}
