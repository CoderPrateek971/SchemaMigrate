"""Shared status, abort, and final cleanup operations."""
from uuid import UUID

from psycopg import sql

from schemamigrate.sql_resources import load_sql
from schemamigrate.state import META, get_status


def abort_preparation(connection, migration_id):
    mid = UUID(str(migration_id))
    with connection.cursor() as cur:
        cur.execute(load_sql("lifecycle_api.sql", "abort_get").format(meta=META), (mid,))
        row = cur.fetchone()
    if row is None:
        raise ValueError("Migration ID was not found.")
    schema, source, shadow, status = row
    if status not in ("READY_FOR_COPY", "COPYING", "COPY_READY"):
        raise ValueError("Abort is available only before cutover.")
    with connection.cursor() as cur:
        cur.execute(load_sql("lifecycle_api.sql", "drop_trigger_if_exists").format(
            trigger_name=sql.Identifier(f"sm_forward_{mid.hex[:12]}"),
            table=sql.Identifier(schema, source)))
        cur.execute(load_sql("lifecycle_api.sql", "drop_table").format(
            table=sql.Identifier(schema, shadow)))
        cur.execute(load_sql("lifecycle_api.sql", "mark_aborted").format(meta=META), (mid,))
    return {"migration_id": str(mid), "status": "ABORTED"}


def finalize_migration(connection, migration_id):
    mid = UUID(str(migration_id))
    with connection.cursor() as cur:
        cur.execute(load_sql("lifecycle_api.sql", "finalize_get").format(meta=META), (mid,))
        row = cur.fetchone()
    if row is None:
        raise ValueError("Migration ID was not found.")
    schema, source, shadow, old, status = row
    if status == "SWAPPED" and old:
        with connection.cursor() as cur:
            cur.execute(load_sql("cutover_api.sql", "lock_table").format(
                table=sql.Identifier(schema, source)))
            cur.execute(load_sql("cutover_api.sql", "drop_trigger").format(
                trigger_name=sql.Identifier(f"sm_reverse_{mid.hex[:12]}"),
                table=sql.Identifier(schema, source)))
            cur.execute(load_sql("lifecycle_api.sql", "drop_table").format(
                table=sql.Identifier(schema, old)))
            cur.execute(load_sql("lifecycle_api.sql", "mark_finalized").format(meta=META),
                        ("FINALIZED", mid))
        final_status, removed = "FINALIZED", old
    elif status == "ROLLED_BACK":
        with connection.cursor() as cur:
            cur.execute(load_sql("lifecycle_api.sql", "drop_table").format(
                table=sql.Identifier(schema, shadow)))
            cur.execute(load_sql("lifecycle_api.sql", "mark_finalized").format(meta=META),
                        ("FINALIZED_AFTER_ROLLBACK", mid))
        final_status, removed = "FINALIZED_AFTER_ROLLBACK", shadow
    else:
        raise ValueError("Finalize is available only after swap or rollback.")
    return {"migration_id": str(mid), "status": final_status,
            "removed_table": f"{schema}.{removed}", "rollback_available": False}
