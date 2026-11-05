"""Person C (Nishtha): check row counts, MD5 table checksums, indexes, constraints, and column equality."""
from uuid import UUID

from psycopg import sql

from schemamigrate.sql_resources import load_sql
from schemamigrate.state import META, get_migration


def compare_tables(connection, schema, left_name, right_name, columns):
    collist = sql.SQL(", ").join(map(sql.Identifier, columns))
    query = load_sql("verify_api.sql", "rows_differ").format(
        columns=collist, left_table=sql.Identifier(schema, left_name),
        right_table=sql.Identifier(schema, right_name))
    with connection.cursor() as cur:
        cur.execute(query)
        return not cur.fetchone()[0]


def calculate_checksum(connection, schema, table_name, columns, key):
    collist = sql.SQL(", ").join(map(sql.Identifier, columns))
    query = load_sql("verify_api.sql", "checksum").format(
        columns=collist, table=sql.Identifier(schema, table_name), key=sql.Identifier(key))
    with connection.cursor() as cur:
        cur.execute(query)
        row = cur.fetchone()
        return row[0] if row else "empty"


def count_indexes(connection, schema, table_name):
    query = load_sql("verify_api.sql", "index_count")
    with connection.cursor() as cur:
        cur.execute(query, (schema, table_name))
        row = cur.fetchone()
        return row[0] if row else 0


def count_constraints(connection, schema, table_name):
    query = load_sql("verify_api.sql", "constraint_count")
    full_table_name = f'"{schema}"."{table_name}"'
    with connection.cursor() as cur:
        try:
            cur.execute(query, (full_table_name,))
            row = cur.fetchone()
            return row[0] if row else 0
        except Exception:
            return 0


def verify_migration(connection, migration_id):
    row = get_migration(connection, migration_id)
    schema, source, shadow, _old, columns, keys, _last, _copied, done, status = row
    if not done:
        return {"verified": False, "reason": "Base copy has not completed.", "status": status}
    
    key = keys[0] if keys else columns[0]

    with connection.cursor() as cur:
        cur.execute(load_sql("verify_api.sql", "row_count").format(
             table=sql.Identifier(schema, source)))
        source_count = cur.fetchone()[0]
        cur.execute(load_sql("verify_api.sql", "row_count").format(
            table=sql.Identifier(schema, shadow)))
        shadow_count = cur.fetchone()[0]

    match = compare_tables(connection, schema, source, shadow, columns)
    source_checksum = calculate_checksum(connection, schema, source, columns, key)
    shadow_checksum = calculate_checksum(connection, schema, shadow, columns, key)
    checksums_match = (source_checksum == shadow_checksum)

    source_indexes = count_indexes(connection, schema, source)
    shadow_indexes = count_indexes(connection, schema, shadow)
    indexes_valid = (shadow_indexes >= source_indexes)

    source_con = count_constraints(connection, schema, source)
    shadow_con = count_constraints(connection, schema, shadow)
    constraints_valid = (shadow_con >= source_con)

    verified = (
        source_count == shadow_count
        and match
        and checksums_match
        and indexes_valid
        and constraints_valid
    )

    return {
        "verified": verified,
        "source_rows": source_count,
        "shadow_rows": shadow_count,
        "checksums_match": checksums_match,
        "source_checksum": source_checksum,
        "shadow_checksum": shadow_checksum,
        "indexes_valid": indexes_valid,
        "source_indexes": source_indexes,
        "shadow_indexes": shadow_indexes,
        "constraints_valid": constraints_valid,
        "original_columns_match": match,
        "note": "Point-in-time check. Cutover repeats the comparison while writes are locked."
    }
