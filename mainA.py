"""Run Person A's parse, plan, prepare, and Person B handoff in one command."""
import argparse
import json
import os

from schemamigrate.database import connect, inspect_request
from schemamigrate.handoff import create_handoff
from schemamigrate.parser.request_parser import RequestParseError, from_sql
from schemamigrate.planner import build_plan
from schemamigrate.prepare import prepare_migration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sql",
        help="One ALTER TABLE ... ADD COLUMN statement; prompts if omitted.",
    )
    parser.add_argument(
        "--dsn",
        default=os.getenv("SCHEMAMIGRATE_DSN"),
        help="PostgreSQL connection string (or set SCHEMAMIGRATE_DSN).",
    )
    parser.add_argument(
        "--allow-risky",
        action="store_true",
        help="Allow preparation when the planner reports RISKY.",
    )
    args = parser.parse_args()

    if not args.dsn:
        parser.error("Set SCHEMAMIGRATE_DSN or pass --dsn.")

    statement = args.sql or input("Enter ALTER TABLE ... ADD COLUMN SQL: ").strip()
    try:
        request = from_sql(statement)
    except RequestParseError as exc:
        parser.error(str(exc))

    with connect(args.dsn) as connection:
        plan = build_plan(inspect_request(connection, request), request)
        print("\nPerson A — parsed request and migration plan:")
        print(json.dumps(plan, indent=2, default=str))

        if plan["status"] == "BLOCKED":
            raise SystemExit("Stopped: this request is BLOCKED.")
        if plan["status"] == "RISKY" and not args.allow_risky:
            raise SystemExit(
                "Stopped: review the risks, then rerun with --allow-risky if accepted."
            )

        if input("This will create database objects. Type PREPARE to continue: ") != "PREPARE":
            print("Cancelled; no migration was prepared.")
            return

        prepared = prepare_migration(
            connection, request, plan, allow_risky=args.allow_risky
        )
        handoff = create_handoff(connection, prepared["migration_id"])

        print("\nPerson A — preparation complete:")
        print(json.dumps({"preparation": prepared, "handoff_for_person_b": handoff},
                         indent=2, default=str))


if __name__ == "__main__":
    main()
