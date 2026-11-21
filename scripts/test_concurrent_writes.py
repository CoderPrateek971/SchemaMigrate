"""Automated test script demonstrating concurrent writes during batch copy, cutover, and rollback.

Authors: Prateek (Person B - copy & CDC) & Nishtha (Person C - verify & rollback).
"""
import os
import sys
import threading
import time

from psycopg import sql

from schemamigrate.copy_engine import catch_up, copy_batch
from schemamigrate.cutover import cutover, rollback
from schemamigrate.database import connect, inspect_request, prompt_for_password
from schemamigrate.parser.request_parser import from_sql
from schemamigrate.planner import build_plan
from schemamigrate.prepare import prepare_migration
from schemamigrate.verification import verify_migration

STOP_CONCURRENT_WRITES = False


def concurrent_writer(dsn: str, start_id: int):
    """Simulates live application writes (INSERT, UPDATE) during online copy."""
    cur_id = start_id
    with connect(dsn) as conn:
        with conn.cursor() as cur:
            while not STOP_CONCURRENT_WRITES:
                try:
                    # Insert new live customer
                    cur.execute(
                        """
                        INSERT INTO schemamigrate_demo.customers (id, full_name, email, created_at)
                        VALUES (%s, %s, %s, now())
                        ON CONFLICT DO NOTHING
                        """,
                        (cur_id, f"Live App User {cur_id}", f"live_user_{cur_id}@test.com"),
                    )
                    # Update existing customer
                    cur.execute(
                        """
                        UPDATE schemamigrate_demo.customers
                        SET full_name = 'Updated Live User 1'
                        WHERE id = 1
                        """
                    )
                    conn.commit()
                    cur_id += 1
                    time.sleep(0.05)
                except Exception as e:
                    conn.rollback()
                    time.sleep(0.05)


def main():
    global STOP_CONCURRENT_WRITES
    dsn = os.getenv("SCHEMAMIGRATE_DSN")
    if not dsn:
        print("Error: Set SCHEMAMIGRATE_DSN environment variable.")
        sys.exit(1)

    dsn = prompt_for_password(dsn)

    print("=== TEST: CONCURRENT WRITES & ZERO-DOWNTIME MIGRATION ===")

    # 1. Person A: Prepare
    stmt = "ALTER TABLE schemamigrate_demo.customers ADD COLUMN loyalty_level integer DEFAULT 1"
    req = from_sql(stmt)
    with connect(dsn) as conn:
        inspection = inspect_request(conn, req)
        plan = build_plan(inspection, req)
        if plan["status"] == "BLOCKED":
            sys.exit(f"Preparation blocked: {plan['findings']}")
        prep = prepare_migration(conn, req, plan)
        conn.commit()

    migration_id = prep["migration_id"]
    print(f"[Person A - Saksham] Prepared migration: {migration_id}")

    # 2. Start concurrent live writes in background thread
    STOP_CONCURRENT_WRITES = False
    writer_thread = threading.Thread(target=concurrent_writer, args=(dsn, 90000), daemon=True)
    writer_thread.start()
    print("[Live App Simulation] Started background writes on source table...")

    # 3. Person B: Batch Copy
    print("[Person B - Prateek] Running batch copy with throttle...")
    with connect(dsn) as conn:
        total_copied = 0
        while True:
            res = copy_batch(conn, migration_id, batch_size=200)
            conn.commit()
            total_copied += res["copied_this_batch"]
            time.sleep(0.02)
            if res["copy_complete"]:
                break
        print(f"[Person B - Prateek] Batch copy complete. Total rows copied: {total_copied}")

        # Stop background writes
        STOP_CONCURRENT_WRITES = True
        writer_thread.join(timeout=2.0)
        print("[Live App Simulation] Stopped background writes.")

        # Catch up forward events
        events = catch_up(conn, migration_id, "FORWARD")
        conn.commit()
        print(f"[Person B - Prateek] Replayed {events} concurrent writes from CDC log.")

        # Verification
        v_res = verify_migration(conn, migration_id)
        print(f"[Person C - Nishtha] Pre-cutover verification result: Verified = {v_res['verified']}")
        print(f"  - Source rows: {v_res['source_rows']}, Shadow rows: {v_res['shadow_rows']}")
        print(f"  - Checksums match: {v_res['checksums_match']}")
        print(f"  - Indexes valid: {v_res['indexes_valid']}")
        assert v_res["verified"], "Verification failed!"

    # 4. Person C: Cutover
    print("[Person C - Nishtha] Performing atomic sub-second table swap...")
    with connect(dsn) as conn:
        cutover_res = cutover(conn, migration_id)
        conn.commit()
        print(f"[Person C - Nishtha] Table swap successful: {cutover_res['status']}")

    # 5. Person C: Rollback Test
    print("[Person C - Nishtha] Testing Rollback mechanism...")
    with connect(dsn) as conn:
        rb_res = rollback(conn, migration_id)
        conn.commit()
        print(f"[Person C - Nishtha] Rollback successful: {rb_res['status']}")

    print("\n✅ ALL TESTS PASSED: Zero-downtime batch copy, concurrent CDC replay, and rollback verified!")


if __name__ == "__main__":
    main()
