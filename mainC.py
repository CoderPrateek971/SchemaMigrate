"""Run Person C's cutover and optional rollback/finalize actions."""
import argparse
import json
import os

from psycopg import sql

from schemamigrate.cutover import cutover, rollback
from schemamigrate.database import connect
from schemamigrate.lifecycle import finalize_migration
from schemamigrate.state import META, get_status


def _print_json(value):
    print(json.dumps(value, indent=2, default=str))


def _details(connection, migration_id):
    query = sql.SQL(
        "SELECT source_schema, source_table, requested_column "
        "FROM {}.migrations WHERE migration_id = %s"
    ).format(META)
    with connection.cursor() as cursor:
        cursor.execute(query, (migration_id,))
        row = cursor.fetchone()
    if row is None:
        raise ValueError("Migration ID was not found.")
    return row


def _post_cutover_check(connection, migration_id):
    schema, table, requested_column = _details(connection, migration_id)
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(schema, table))
        )
        row_count = cursor.fetchone()[0]
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s AND column_name = %s
            )
            """,
            (schema, table, requested_column),
        )
        column_exists = cursor.fetchone()[0]
    return {
        "live_table": f"{schema}.{table}",
        "live_rows": row_count,
        "requested_column": requested_column,
        "requested_column_exists": column_exists,
    }


def _next_action():
    return input(
        "Choose ROLLBACK to restore the old table, FINALIZE to drop it, "
        "or press Enter to keep the new table and rollback window: "
    ).strip().upper() or "KEEP"


def _finish_action(connection, migration_id, action):
    if action == "KEEP":
        print("Keeping the new table active; rollback remains available.")
        return

    if action == "ROLLBACK":
        if input("This restores the old table. Type ROLLBACK to continue: ") != "ROLLBACK":
            print("Rollback cancelled; the new table remains active.")
            return
        result = rollback(connection, migration_id)
        connection.commit()
        print("Person C rollback result:")
        _print_json(result)
        return

    if action == "FINALIZE":
        if input(
            "This permanently removes the retained old table and ends rollback. "
            "Type FINALIZE to continue: "
        ) != "FINALIZE":
            print("Finalize cancelled; the old table is retained.")
            return
        result = finalize_migration(connection, migration_id)
        connection.commit()
        print("Person C finalize result:")
        _print_json(result)
        return

    print("No follow-up action taken; the new table remains active.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "migration_id",
        nargs="?",
        help="Migration UUID printed by mainA.py; prompts if omitted.",
    )
    parser.add_argument(
        "--dsn",
        default=os.getenv("SCHEMAMIGRATE_DSN"),
        help="PostgreSQL connection string (or set SCHEMAMIGRATE_DSN).",
    )
    args = parser.parse_args()
    if not args.dsn:
        parser.error("Set SCHEMAMIGRATE_DSN or pass --dsn.")
    migration_id = args.migration_id or input("Enter migration UUID: ").strip()

    with connect(args.dsn) as connection:
        status = get_status(connection, migration_id)
        print("Person C received migration status:")
        _print_json(status)

        if status["status"] == "COPY_READY":
            if input("This locks and swaps tables. Type CUTOVER to continue: ") != "CUTOVER":
                print("Cutover cancelled; the original table remains active.")
                return
            result = cutover(connection, migration_id)
            connection.commit()
            check = _post_cutover_check(connection, migration_id)
            current_status = get_status(connection, migration_id)
            print("Person C cutover and post-migration check:")
            _print_json({"cutover": result, "post_cutover_check": check,
                         "migration_status": current_status})
            if not check["requested_column_exists"]:
                print("The requested column was not found; investigate before finalizing.")
                return
            _finish_action(connection, migration_id, _next_action())
            return

        if status["status"] == "SWAPPED":
            print("The cutover is already complete.")
            _finish_action(connection, migration_id, _next_action())
            return

        if status["status"] == "ROLLED_BACK":
            print("The old table is live again.")
            action = input(
                "Type FINALIZE to remove the unused migrated table, "
                "or press Enter to leave it: "
            ).strip().upper()
            if action == "FINALIZE":
                _finish_action(connection, migration_id, action)
            return

        raise SystemExit(
            f"Person C cannot cut over status {status['status']}. "
            "Person B must finish copying and verification first."
        )


if __name__ == "__main__":
    main()
