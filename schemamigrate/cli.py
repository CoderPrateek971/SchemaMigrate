"""Shared CLI command router used by the root main.py entry point."""
import argparse
import json
import os
import time

from schemamigrate.copy_engine import catch_up, copy_batch
from schemamigrate.cutover import cutover, rollback
from schemamigrate.database import connect, inspect_request
from schemamigrate.handoff import create_handoff
from schemamigrate.lifecycle import abort_preparation, finalize_migration
from schemamigrate.parser.request_parser import from_sql
from schemamigrate.planner import build_plan
from schemamigrate.prepare import prepare_migration
from schemamigrate.state import get_status
from schemamigrate.verification import verify_migration


def _json(value):
    print(json.dumps(value, indent=2, default=str))


def _dsn(args):
    value = args.dsn or os.getenv("SCHEMAMIGRATE_DSN")
    if not value:
        raise SystemExit("Set SCHEMAMIGRATE_DSN or pass --dsn.")
    return value


def _confirm(word):
    return input(f"This changes database objects. Type {word} to continue: ") == word


def build_parser():
    parser = argparse.ArgumentParser(prog="main.py", description="Plan and run the SchemaMigrate V1 demo flow.")
    parser.add_argument("--dsn", help="PostgreSQL connection string (or set SCHEMAMIGRATE_DSN)")
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="Inspect PostgreSQL and print a plan")
    plan.add_argument("--sql", required=True)
    prep = commands.add_parser("prepare", help="Create shadow table and change capture")
    prep.add_argument("--sql", required=True)
    prep.add_argument("--allow-risky", action="store_true")
    cp = commands.add_parser("copy", help="Copy one batch, or resume until finished")
    cp.add_argument("migration_id")
    cp.add_argument("--batch-size", type=int, default=1000)
    cp.add_argument("--all", action="store_true")
    cp.add_argument("--throttle", type=float, default=0.0, help="Pause in seconds between batches (e.g. 0.05)")
    catch = commands.add_parser("catch-up", help="Replay captured writes")
    catch.add_argument("migration_id")
    catch.add_argument("--direction", choices=("FORWARD", "REVERSE"), default="FORWARD")
    for name, help_text in (
        ("verify", "Compare original columns in source and shadow"),
        ("handoff", "Print Person B's persisted input metadata"),
        ("status", "Show migration status and progress"),
        ("cutover", "Verify under locks and swap table names"),
        ("rollback", "Replay reverse writes and restore the old table"),
        ("abort", "Remove a pre-cutover shadow"),
        ("finalize", "Drop the retained table and end rollback availability"),
    ):
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("migration_id")
    dash = commands.add_parser("dashboard", help="Start the real-time Web Dashboard")
    dash.add_argument("--host", default="127.0.0.1", help="Host to bind to (default: 127.0.0.1)")
    dash.add_argument("--port", type=int, default=8080, help="Port to listen on (default: 8080)")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "dashboard":
        from schemamigrate.web.server import start_server
        dsn = args.dsn or os.getenv("SCHEMAMIGRATE_DSN")
        start_server(host=args.host, port=args.port, dsn=dsn)
        return
    dsn = _dsn(args)
    if args.command in ("plan", "prepare"):
        request = from_sql(args.sql)
        with connect(dsn) as conn:
            plan = build_plan(inspect_request(conn, request), request)
            _json(plan)
            if args.command == "plan":
                return
            if plan["status"] == "BLOCKED":
                raise SystemExit("Preparation stopped: plan is BLOCKED.")
            if plan["status"] == "RISKY" and not args.allow_risky:
                raise SystemExit("Review risks; pass --allow-risky only if accepted.")
            if not _confirm("PREPARE"):
                print("Preparation cancelled.")
                return
            _json(prepare_migration(conn, request, plan, args.allow_risky))
        return

    words = {"cutover": "CUTOVER", "rollback": "ROLLBACK", "abort": "ABORT", "finalize": "FINALIZE"}
    if args.command in words and not _confirm(words[args.command]):
        print("Cancelled.")
        return
    if args.command == "copy":
        this_run = 0
        while True:
            # One transaction per batch keeps progress durable and resumable.
            with connect(dsn) as conn:
                result = copy_batch(conn, args.migration_id, args.batch_size)
            this_run += result["copied_this_batch"]
            if result["copy_complete"] or not args.all:
                result["copied_this_run"] = this_run
                _json(result)
                return
            if args.throttle > 0:
                time.sleep(args.throttle)
    with connect(dsn) as conn:
        if args.command == "catch-up":
            _json({"events_applied": catch_up(conn, args.migration_id, args.direction)})
        elif args.command == "verify":
            _json(verify_migration(conn, args.migration_id))
        elif args.command == "handoff":
            _json(create_handoff(conn, args.migration_id))
        elif args.command == "status":
            _json(get_status(conn, args.migration_id))
        elif args.command == "cutover":
            _json(cutover(conn, args.migration_id))
        elif args.command == "rollback":
            _json(rollback(conn, args.migration_id))
        elif args.command == "abort":
            _json(abort_preparation(conn, args.migration_id))
        elif args.command == "finalize":
            _json(finalize_migration(conn, args.migration_id))
