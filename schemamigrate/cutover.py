"""Person C: locked rename, reverse change capture, and rollback."""
from uuid import UUID

from psycopg import sql

from schemamigrate.copy_engine import catch_up
from schemamigrate.sql_resources import load_sql
from schemamigrate.state import META, get_migration
from schemamigrate.verification import compare_tables


def _lock(connection, schema, table_names):
    with connection.cursor() as cur:
        for name in sorted(table_names):
            cur.execute(load_sql("cutover_api.sql", "lock_table").format(
                table=sql.Identifier(schema, name)))


def cutover(connection, migration_id):
    mid = UUID(str(migration_id))
    row = get_migration(connection, mid, lock=True)
    schema, source, shadow, _old, columns, _keys, _last, _copied, done, status = row
    if not done or status != "COPY_READY":
        raise ValueError("Complete the base copy before cutover.")
    old = f"sm_old_{mid.hex[:16]}"
    forward = f"sm_forward_{mid.hex[:12]}"
    reverse = f"sm_reverse_{mid.hex[:12]}"
    _lock(connection, schema, (source, shadow))
    catch_up(connection, mid, "FORWARD")
    if not compare_tables(connection, schema, source, shadow, columns):
        raise ValueError("Locked source/shadow comparison failed; swap cancelled.")
    with connection.cursor() as cur:
        cur.execute(load_sql("cutover_api.sql", "drop_trigger").format(
            trigger_name=sql.Identifier(forward), table=sql.Identifier(schema, source)))
        cur.execute(load_sql("cutover_api.sql", "rename_table").format(
            table=sql.Identifier(schema, source), new_name=sql.Identifier(old)))
        cur.execute(load_sql("cutover_api.sql", "rename_table").format(
            table=sql.Identifier(schema, shadow), new_name=sql.Identifier(source)))
        cur.execute(load_sql("cutover_api.sql", "create_reverse_trigger").format(
            trigger_name=sql.Identifier(reverse), table=sql.Identifier(schema, source), meta=META,
            migration_id=sql.Literal(str(mid)), direction=sql.Literal("REVERSE")))
        cur.execute(load_sql("cutover_api.sql", "mark_swapped").format(meta=META), (old, mid))
    return {"migration_id": str(mid), "status": "SWAPPED", "old_table": f"{schema}.{old}"}


def rollback(connection, migration_id):
    mid = UUID(str(migration_id))
    row = get_migration(connection, mid, lock=True)
    schema, source, shadow, old, columns, _keys, _last, _copied, _done, status = row
    if status != "SWAPPED" or not old:
        raise ValueError("Only a swapped migration can be rolled back.")
    reverse = f"sm_reverse_{mid.hex[:12]}"
    _lock(connection, schema, (source, old))
    catch_up(connection, mid, "REVERSE")
    if not compare_tables(connection, schema, source, old, columns):
        raise ValueError("Locked rollback comparison failed; table names were not changed.")
    with connection.cursor() as cur:
        cur.execute(load_sql("cutover_api.sql", "drop_trigger").format(
            trigger_name=sql.Identifier(reverse), table=sql.Identifier(schema, source)))
        cur.execute(load_sql("cutover_api.sql", "rename_table").format(
            table=sql.Identifier(schema, source), new_name=sql.Identifier(shadow)))
        cur.execute(load_sql("cutover_api.sql", "rename_table").format(
            table=sql.Identifier(schema, old), new_name=sql.Identifier(source)))
        cur.execute(load_sql("cutover_api.sql", "mark_rolled_back").format(meta=META), (mid,))
    return {"migration_id": str(mid), "status": "ROLLED_BACK", "live_table": f"{schema}.{source}"}
