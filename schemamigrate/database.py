"""Person A: PostgreSQL connection and catalog-inspection helpers."""
from contextlib import contextmanager
from getpass import getpass

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from schemamigrate.models import MigrationRequest


def prompt_for_password(dsn: str) -> str:
    """Ask for a hidden password when the connection string has none."""
    if conninfo_to_dict(dsn).get("password"):
        return dsn
    password = getpass("PostgreSQL password: ")
    return make_conninfo(dsn, password=password)


@contextmanager
def connect(dsn: str):
    """Open a transaction-scoped connection and close it when finished."""
    with psycopg.connect(prompt_for_password(dsn)) as connection:
        yield connection


def table_exists(connection, schema: str, table: str) -> bool:
    query = """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = %s
              AND table_name = %s
              AND table_type = 'BASE TABLE'
        )
    """
    with connection.cursor() as cursor:
        cursor.execute(query, (schema, table))
        return cursor.fetchone()[0]


def get_columns(connection, schema: str, table: str) -> list[dict]:
    query = """
        SELECT column_name, data_type, is_nullable, column_default,
               is_identity, is_generated
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
    """
    with connection.cursor() as cursor:
        cursor.execute(query, (schema, table))
        rows = cursor.fetchall()
    return [
        {
            "name": row[0],
            "type": row[1],
            "nullable": row[2] == "YES",
            "default": row[3],
            "identity": row[4] == "YES",
            "generated": row[5] != "NEVER",
        }
        for row in rows
    ]


def get_primary_key_columns(connection, schema: str, table: str) -> list[str]:
    query = """
        SELECT kcu.column_name
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu
          ON kcu.constraint_catalog = tc.constraint_catalog
         AND kcu.constraint_schema = tc.constraint_schema
         AND kcu.constraint_name = tc.constraint_name
         AND kcu.table_schema = tc.table_schema
         AND kcu.table_name = tc.table_name
        WHERE tc.constraint_type = 'PRIMARY KEY'
          AND tc.table_schema = %s AND tc.table_name = %s
        ORDER BY kcu.ordinal_position
    """
    with connection.cursor() as cursor:
        cursor.execute(query, (schema, table))
        return [row[0] for row in cursor.fetchall()]


def get_estimated_rows(connection, schema: str, table: str) -> int | None:
    query = """
        SELECT n_live_tup
        FROM pg_catalog.pg_stat_user_tables
        WHERE schemaname = %s AND relname = %s
    """
    with connection.cursor() as cursor:
        cursor.execute(query, (schema, table))
        row = cursor.fetchone()
    return row[0] if row else None


def get_table_size_bytes(connection, schema: str, table: str) -> int | None:
    query = """
        SELECT pg_catalog.pg_total_relation_size(c.oid)
        FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relname = %s
    """
    with connection.cursor() as cursor:
        cursor.execute(query, (schema, table))
        row = cursor.fetchone()
    return row[0] if row else None


def get_indexes(connection, schema: str, table: str) -> list[dict]:
    query = """
        SELECT indexname, indexdef
        FROM pg_catalog.pg_indexes
        WHERE schemaname = %s AND tablename = %s
        ORDER BY indexname
    """
    with connection.cursor() as cursor:
        cursor.execute(query, (schema, table))
        rows = cursor.fetchall()
    return [{"name": row[0], "definition": row[1]} for row in rows]


def get_constraints(connection, schema: str, table: str) -> list[dict]:
    query = """
        SELECT constraint_name, constraint_type
        FROM information_schema.table_constraints
        WHERE table_schema = %s AND table_name = %s
        ORDER BY constraint_name
    """
    with connection.cursor() as cursor:
        cursor.execute(query, (schema, table))
        rows = cursor.fetchall()
    return [{"name": row[0], "type": row[1]} for row in rows]


def get_incoming_foreign_keys(connection, schema: str, table: str) -> list[dict]:
    query = """
        SELECT tc.constraint_schema, tc.table_name, tc.constraint_name
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.constraint_column_usage AS ccu
          ON ccu.constraint_catalog = tc.constraint_catalog
         AND ccu.constraint_schema = tc.constraint_schema
         AND ccu.constraint_name = tc.constraint_name
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND ccu.constraint_schema = %s AND ccu.table_name = %s
        ORDER BY tc.constraint_schema, tc.table_name, tc.constraint_name
    """
    with connection.cursor() as cursor:
        cursor.execute(query, (schema, table))
        rows = cursor.fetchall()
    return [
        {"schema": row[0], "table": row[1], "constraint": row[2]}
        for row in rows
    ]


def get_triggers(connection, schema: str, table: str) -> list[dict]:
    query = """
        SELECT DISTINCT trigger_name, action_timing, event_manipulation
        FROM information_schema.triggers
        WHERE event_object_schema = %s AND event_object_table = %s
        ORDER BY trigger_name, event_manipulation
    """
    with connection.cursor() as cursor:
        cursor.execute(query, (schema, table))
        rows = cursor.fetchall()
    return [
        {"name": row[0], "timing": row[1], "event": row[2]}
        for row in rows
    ]


def get_table_flags(connection, schema: str, table: str) -> dict:
    query = """
        SELECT c.relrowsecurity,
               c.relforcerowsecurity,
               (c.relkind = 'p' OR c.relispartition OR EXISTS (
                   SELECT 1 FROM pg_catalog.pg_inherits AS i
                   WHERE i.inhrelid = c.oid OR i.inhparent = c.oid
               )) AS partitioned
        FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relname = %s
    """
    with connection.cursor() as cursor:
        cursor.execute(query, (schema, table))
        row = cursor.fetchone()
    if row is None:
        return {
            "row_level_security": False,
            "force_row_level_security": False,
            "partitioned": False,
        }
    return {
        "row_level_security": row[0],
        "force_row_level_security": row[1],
        "partitioned": row[2],
    }


def inspect_request(connection, request: MigrationRequest) -> dict:
    """Gather the facts the Person A planner needs from PostgreSQL."""
    exists = table_exists(connection, request.schema, request.table)
    if not exists:
        return {
            "table_exists": False,
            "columns": [],
            "requested_column_exists": False,
            "primary_key_columns": [],
            "estimated_rows": None,
            "table_size_bytes": None,
            "indexes": [],
            "constraints": [],
            "incoming_foreign_keys": [],
            "triggers": [],
            "row_level_security": False,
            "force_row_level_security": False,
            "partitioned": False,
        }

    columns = get_columns(connection, request.schema, request.table)
    return {
        "table_exists": True,
        "columns": columns,
        "requested_column_exists": any(
            column["name"] == request.column for column in columns
        ),
        "primary_key_columns": get_primary_key_columns(
            connection, request.schema, request.table
        ),
        "estimated_rows": get_estimated_rows(
            connection, request.schema, request.table
        ),
        "table_size_bytes": get_table_size_bytes(
            connection, request.schema, request.table
        ),
        "indexes": get_indexes(connection, request.schema, request.table),
        "constraints": get_constraints(connection, request.schema, request.table),
        "incoming_foreign_keys": get_incoming_foreign_keys(
            connection, request.schema, request.table
        ),
        "triggers": get_triggers(connection, request.schema, request.table),
        **get_table_flags(connection, request.schema, request.table),
    }
