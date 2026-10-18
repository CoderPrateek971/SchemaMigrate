"""Person B: check original-column equality and row counts."""
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


def verify_migration(connection, migration_id):
    row = get_migration(connection, migration_id)
    schema, source, shadow, _old, columns, _keys, _last, _copied, done, status = row
    if not done:
        return {"verified": False, "reason": "Base copy has not completed.", "status": status}
    with connection.cursor() as cur:
        cur.execute(load_sql("verify_api.sql", "row_count").format(
             table=sql.Identifier(schema, source)))
        source_count = cur.fetchone()[0]
        cur.execute(load_sql("verify_api.sql", "row_count").format(
            table=sql.Identifier(schema, shadow)))
        shadow_count = cur.fetchone()[0]
    match = compare_tables(connection, schema, source, shadow, columns)
    return {"verified": source_count == shadow_count and match,
            "source_rows": source_count, "shadow_rows": shadow_count,
            "original_columns_match": match,
            "note": "Point-in-time check. Cutover repeats the comparison while writes are locked."}
