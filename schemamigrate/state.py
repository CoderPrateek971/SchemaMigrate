"""Shared reads and durable progress updates for a migration."""
from uuid import UUID

from psycopg import sql

from schemamigrate.sql_resources import load_sql

META = sql.Identifier("schemamigrate_meta_v1")


def get_migration(connection, migration_id, *, lock=False):
    section = "get_migration" if lock else "get_migration_read"
    query = load_sql("state_api.sql", section).format(meta=META)
    with connection.cursor() as cur:
        cur.execute(query, (UUID(str(migration_id)),))
        row = cur.fetchone()
    if row is None:
        raise ValueError("Migration ID was not found.")
    return row


def get_status(connection, migration_id):
    with connection.cursor() as cur:
        cur.execute(load_sql("state_api.sql", "migration_status").format(meta=META),
                    (UUID(str(migration_id)),))
        row = cur.fetchone()
    if row is None:
        raise ValueError("Migration ID was not found.")
    return {"migration_id": str(row[0]), "source": f"{row[1]}.{row[2]}",
            "shadow_table": f"{row[1]}.{row[3]}", "old_table": row[4],
            "status": row[5], "copied_rows": row[6], "copy_complete": row[7],
            "prepared_at": row[8], "swapped_at": row[9]}
