"""Person A: save the plan, create the shadow, and install forward capture."""
from uuid import uuid4

from psycopg import sql
from psycopg.types.json import Jsonb

from schemamigrate.database import inspect_request
from schemamigrate.planner import build_plan
from schemamigrate.sql_resources import load_sql

META = sql.Identifier("schemamigrate_meta_v1")


def create_internal_tables(connection):
    with connection.cursor() as cur:
        for section in ("create_schema", "create_migrations", "create_events", "create_pending_index"):
            cur.execute(load_sql("state_api.sql", section).format(meta=META))
        cur.execute(load_sql("capture_api.sql", "capture_function").format(meta=META))


def prepare_migration(connection, request, plan, allow_risky=False):
    if plan["status"] == "BLOCKED":
        raise ValueError("The migration plan is BLOCKED.")
    if plan["status"] == "RISKY" and not allow_risky:
        raise ValueError("Review the RISKY plan and pass --allow-risky to proceed.")
    migration_id = uuid4()
    token = migration_id.hex[:16]
    shadow_name = f"sm_shadow_{token}"
    trigger_name = f"sm_forward_{token[:12]}"
    source = sql.Identifier(request.schema, request.table)
    shadow = sql.Identifier(request.schema, shadow_name)
    create_internal_tables(connection)
    with connection.cursor() as cur:
        cur.execute(load_sql("prepare_api.sql", "lock_source_for_prepare").format(source=source))
        cur.execute(load_sql("prepare_api.sql", "requested_column_exists"),
                    (request.schema, request.table, request.column))
        if cur.fetchone()[0]:
            raise ValueError("Requested column appeared after planning. Re-run plan.")
        fresh = build_plan(inspect_request(connection, request), request)
        if fresh["status"] == "BLOCKED" or (fresh["status"] == "RISKY" and not allow_risky):
            raise ValueError("Fresh catalog inspection no longer permits preparation.")
        if not request.nullable and request.default_sql in (None, "NULL"):
            raise ValueError("NOT NULL needs a non-NULL default in this V1 flow.")
        cur.execute(load_sql("prepare_api.sql", "create_shadow").format(shadow=shadow, source=source))
        coldef = sql.SQL("{} {}").format(sql.Identifier(request.column), sql.SQL(request.type_sql))
        if request.default_sql is not None:
            coldef += sql.SQL(" DEFAULT ") + sql.SQL(request.default_sql)
        if not request.nullable:
            coldef += sql.SQL(" NOT NULL")
        cur.execute(load_sql("prepare_api.sql", "add_requested_column").format(
            shadow=shadow, column_definition=coldef))
        cur.execute(load_sql("prepare_api.sql", "create_capture_trigger").format(
            trigger_name=sql.Identifier(trigger_name), source=source, meta=META),
            (str(migration_id), "FORWARD"))
        info = fresh["inspection"]
        cur.execute(load_sql("prepare_api.sql", "insert_migration").format(meta=META), (
            migration_id, request.schema, request.table, shadow_name, request.column,
            Jsonb([c["name"] for c in info["columns"]]), Jsonb(info["primary_key_columns"]),
            info["estimated_rows"], info["table_size_bytes"], Jsonb(fresh),
        ))
    return {"migration_id": str(migration_id), "source": f"{request.schema}.{request.table}",
            "shadow_table": f"{request.schema}.{shadow_name}", "status": "READY_FOR_COPY"}
