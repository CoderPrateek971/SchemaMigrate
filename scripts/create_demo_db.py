"""Create/reset the isolated demo database with exactly 1,000 demo rows."""
import argparse
import os

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from schemamigrate.database import prompt_for_password

DEMO_DATABASE = "schemamigrate_demo"

# These statements are scoped to the dedicated demo database. Each setup run
# recreates only the schemas owned by this project and inserts 1,000 sample rows.
DEMO_SETUP = (
    "DROP SCHEMA IF EXISTS schemamigrate_demo CASCADE",
    "DROP SCHEMA IF EXISTS schemamigrate_meta_v1 CASCADE",
    "CREATE SCHEMA schemamigrate_demo",
    """
    CREATE TABLE schemamigrate_demo.customers (
        id integer PRIMARY KEY,
        full_name text NOT NULL,
        email text NOT NULL UNIQUE,
        created_at timestamptz NOT NULL
    )
    """,
    """
    INSERT INTO schemamigrate_demo.customers (id, full_name, email, created_at)
    SELECT id,
           'Customer ' || id,
           'customer' || id || '@example.test',
           timestamptz '2025-01-01 00:00:00+00' + (id * interval '1 minute')
    FROM generate_series(1, 1000) AS id
    """,
    "ANALYZE schemamigrate_demo.customers",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--admin-dsn",
        default=os.getenv("SCHEMAMIGRATE_ADMIN_DSN"),
        help="Admin connection to a maintenance database; or set SCHEMAMIGRATE_ADMIN_DSN",
    )
    args = parser.parse_args()
    if not args.admin_dsn:
        parser.error("provide --admin-dsn or set SCHEMAMIGRATE_ADMIN_DSN")

    admin_db = conninfo_to_dict(args.admin_dsn).get("dbname", "postgres")
    if admin_db == DEMO_DATABASE:
        parser.error(
            "Use a maintenance database such as 'postgres' for --admin-dsn; "
            "the setup resets schemas inside schemamigrate_demo."
        )

    admin_dsn = prompt_for_password(args.admin_dsn)

    # CREATE DATABASE must run outside a transaction, so use autocommit here.
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM pg_catalog.pg_database WHERE datname = %s",
                (DEMO_DATABASE,),
            )
            database_exists = cursor.fetchone() is not None
            if not database_exists:
                cursor.execute(
                    sql.SQL("CREATE DATABASE {}").format(sql.Identifier(DEMO_DATABASE))
                )
                print(f"Created database {DEMO_DATABASE}.")
            else:
                print(f"Using existing dedicated database {DEMO_DATABASE}.")

    demo_dsn = make_conninfo(admin_dsn, dbname=DEMO_DATABASE)
    with psycopg.connect(demo_dsn) as connection:
        with connection.cursor() as cursor:
            for statement in DEMO_SETUP:
                cursor.execute(statement)
            cursor.execute("SELECT count(*) FROM schemamigrate_demo.customers")
            row_count = cursor.fetchone()[0]
            cursor.execute(
                """
                SELECT count(*)
                FROM information_schema.columns
                WHERE table_schema = 'schemamigrate_demo'
                  AND table_name = 'customers'
                """
            )
            column_count = cursor.fetchone()[0]

    if row_count != 1000 or column_count != 4:
        raise RuntimeError(
            "Demo setup check failed: expected 1,000 rows and 4 columns, "
            f"got {row_count} rows and {column_count} columns."
        )

    print(f"Demo table ready: {row_count} rows, {column_count} columns.")
    print(
        "Set SCHEMAMIGRATE_DSN to the same connection with "
        f"dbname={DEMO_DATABASE}."
    )
    print(
        "Example request: ALTER TABLE schemamigrate_demo.customers "
        "ADD COLUMN loyalty_level integer DEFAULT 1"
    )


if __name__ == "__main__":
    main()
