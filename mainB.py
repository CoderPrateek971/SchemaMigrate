"""Person B (Prateek): handoff read, throttled batch copy with percentage progress, catch-up, and verification."""
import argparse
import json
import os
import time

from schemamigrate.copy_engine import catch_up, copy_batch
from schemamigrate.database import connect
from schemamigrate.handoff import create_handoff
from schemamigrate.state import get_status
from schemamigrate.verification import verify_migration


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
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Rows per committed copy batch (1 to 10000; default: 1000).",
    )
    parser.add_argument(
        "--throttle",
        type=float,
        default=0.0,
        help="Optional pause in seconds between batches (e.g. 0.05) to let app use DB.",
    )
    args = parser.parse_args()

    if not args.dsn:
        parser.error("Set SCHEMAMIGRATE_DSN or pass --dsn.")

    migration_id = args.migration_id or input("Enter migration UUID from mainA.py: ").strip()

    if not 1 <= args.batch_size <= 10000:
        parser.error("--batch-size must be between 1 and 10000.")

    with connect(args.dsn) as connection:
        handoff = create_handoff(connection, migration_id)
        estimated_total = handoff.get("estimated_rows") or 0
        print("Person B (Prateek) received this migration from Person A (Saksham):")
        print(json.dumps({
            "migration_id": handoff["migration_id"],
            "source": handoff["source"],
            "shadow": handoff["shadow"],
            "source_columns": handoff["source_columns"],
            "primary_key_columns": handoff["primary_key_columns"],
            "estimated_rows": handoff["estimated_rows"],
            "status": handoff["status"],
        }, indent=2))

        copied_this_run = 0
        while True:
            result = copy_batch(connection, migration_id, args.batch_size)
            connection.commit()
            copied_this_run += result["copied_this_batch"]

            # Calculate and display percentage progress as required by V1 rubric
            if estimated_total and estimated_total > 0:
                pct = min(100.0, (result["copied_total"] / estimated_total) * 100)
                progress_text = f" ({pct:.1f}% complete)"
            else:
                progress_text = ""

            print(
                f"Copied {result['copied_this_batch']} rows in this batch; "
                f"{result['copied_total']} total{progress_text}."
            )
            if result["copy_complete"]:
                break

            # Pause between batches to prevent locking or starving live app traffic
            if args.throttle > 0:
                time.sleep(args.throttle)

        events_applied = catch_up(connection, migration_id, "FORWARD")
        connection.commit()
        verification = verify_migration(connection, migration_id)
        status = get_status(connection, migration_id)

    print("\nPerson B (Prateek) result:")
    print(json.dumps({
        "migration_id": migration_id,
        "copied_this_run": copied_this_run,
        "forward_events_applied": events_applied,
        "verification": verification,
        "migration_status": status,
        "next": (
            "Ready for Person C (Nishtha)'s locked cutover."
            if verification["verified"]
            else "Do not cut over; investigate the verification result."
        ),
    }, indent=2, default=str))

    if not verification["verified"]:
        raise SystemExit("Person B verification did not pass; cutover is not ready.")


if __name__ == "__main__":
    main()
