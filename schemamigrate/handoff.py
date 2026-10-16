"""Person A -> Person B: durable metadata needed to resume the copy."""
from uuid import UUID

from psycopg import sql
from psycopg.types.json import Jsonb

from schemamigrate.sql_resources import load_sql
from schemamigrate.state import META


def create_handoff(connection, migration_id):
    mid = UUID(str(migration_id))
    with connection.cursor() as cur:
        cur.execute(load_sql("state_api.sql", "handoff_details").format(meta=META), (mid,))
        row = cur.fetchone()
    if row is None:
        raise ValueError("Migration ID was not found.")
    handoff = {
        "migration_id": str(row[0]), "source": {"schema": row[1], "table": row[2]},
        "shadow": {"schema": row[1], "table": row[3]}, "requested_column": row[4],
        "source_columns": row[5], "primary_key_columns": row[6],
        "estimated_rows": row[7], "source_size_bytes": row[8], "plan": row[9],
        "status": row[10], "copy_progress": {"last_key": row[11], "copied_rows": row[12],
                                                "complete": row[13]},
        "change_events_table": "schemamigrate_meta_v1.change_events",
        "event_note": "Use pending events filtered by migration_id and direction; event IDs are not commit-order watermarks.",
        "suggested_batch_size": 1000,
    }
    with connection.cursor() as cur:
        cur.execute(load_sql("state_api.sql", "save_handoff").format(meta=META),
                    (Jsonb(handoff), mid))
    return handoff
