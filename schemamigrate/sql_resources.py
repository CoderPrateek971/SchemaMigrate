"""Return composable PostgreSQL statements from the in-Python SQL templates."""
from psycopg import sql

from schemamigrate.sql_templates import SQL_TEMPLATES


def load_sql(filename: str, name: str) -> sql.Composable:
    """Look up one SQL section by its API filename and section name."""
    try:
        statement = SQL_TEMPLATES[filename][name]
    except KeyError as exc:
        raise KeyError(f"SQL template {filename}:{name} was not found.") from exc
    return sql.SQL(statement)
