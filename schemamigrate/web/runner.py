"""Background migration runner supporting real-time tracking, pause, resume, and cancel."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import json
import queue
import threading
import time
from typing import Any
from uuid import UUID, uuid4

try:
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
except ImportError:
    pass


class MigrationRunner:
    """Manages active migration state, background execution, pause/resume, and SSE broadcast."""

    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn
        self.lock = threading.Lock()
        self.pause_event = threading.Event()
        self.pause_event.set()  # set = running, clear = paused
        self.cancel_event = threading.Event()
        self.worker_thread: threading.Thread | None = None
        self.subscribers: set[queue.Queue[str]] = set()
        self.logs: deque[dict[str, Any]] = deque(maxlen=300)

        # Active migration data
        self.session: dict[str, Any] = {
            "migration_id": None,
            "status": "IDLE",  # IDLE, PLANNING, PREPARED, COPYING, PAUSED, COPY_READY, SWAPPED, ROLLED_BACK, ABORTED, FINALIZED
            "source_table": "schemamigrate_demo.customers",
            "shadow_table": None,
            "old_table": None,
            "requested_column": "loyalty_level integer DEFAULT 1",
            "copied_rows": 0,
            "estimated_rows": 1000,
            "batch_size": 250,
            "throttle": 0.3,
            "copy_complete": False,
            "events_pending": 0,
            "events_applied": 0,
            "verification": None,
            "is_simulation": False,
            "started_at": None,
            "updated_at": None,
            "error": None,
        }

    # -------------------------------------------------------------------------
    # SSE Subscription & Logging
    # -------------------------------------------------------------------------

    def subscribe(self) -> queue.Queue[str]:
        q: queue.Queue[str] = queue.Queue(maxsize=100)
        with self.lock:
            self.subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.Queue[str]) -> None:
        with self.lock:
            self.subscribers.discard(q)

    def broadcast(self, event_type: str, data: dict[str, Any]) -> None:
        message = f"event: {event_type}\ndata: {json.dumps(data, default=str)}\n\n"
        with self.lock:
            dead = set()
            for q in self.subscribers:
                try:
                    q.put_nowait(message)
                except queue.Full:
                    dead.add(q)
            self.subscribers.difference_update(dead)

    def log(self, text: str, level: str = "info") -> None:
        entry = {
            "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "text": text,
            "level": level,
        }
        self.logs.append(entry)
        self.broadcast("log", entry)

    def get_state(self) -> dict[str, Any]:
        with self.lock:
            state = dict(self.session)
            state["logs"] = list(self.logs)
            state["is_paused"] = not self.pause_event.is_set()
            state["is_cancelled"] = self.cancel_event.is_set()
            state["has_dsn"] = bool(self.dsn)
            pct = 0.0
            if state["estimated_rows"] and state["estimated_rows"] > 0:
                pct = min(100.0, round((state["copied_rows"] / state["estimated_rows"]) * 100, 1))
            state["percent"] = pct
            return state

    # -------------------------------------------------------------------------
    # Planning & Preparation
    # -------------------------------------------------------------------------

    def plan_sql(self, sql_statement: str, dsn: str | None = None) -> dict[str, Any]:
        target_dsn = dsn or self.dsn
        if target_dsn:
            try:
                request = from_sql(sql_statement)
                with connect(target_dsn) as conn:
                    facts = inspect_request(conn, request)
                plan = build_plan(facts, request)
                self.log(f"Plan evaluated: {plan['status']} (findings: {len(plan['findings'])})", "info")
                return {"status": "ok", "plan": plan, "request": request.__dict__}
            except Exception as e:
                self.log(f"Plan error: {e}", "error")
                return {"status": "error", "message": str(e)}

        # Simulation fallback plan
        req_col = sql_statement.replace("ALTER TABLE", "").strip()
        sim_plan = {
            "status": "SAFE",
            "findings": ["Primary key is single-column (id)", "No foreign key blockers found"],
            "estimated_copy_seconds": 1.2,
            "estimated_shadow_bytes": 102400,
        }
        return {"status": "ok", "plan": sim_plan, "request": {"sql": sql_statement}}

    def prepare(self, sql_statement: str, dsn: str | None = None, allow_risky: bool = False) -> dict[str, Any]:
        target_dsn = dsn or self.dsn
        with self.lock:
            self.session["requested_column"] = sql_statement
            self.session["started_at"] = datetime.now(timezone.utc).isoformat()
            self.cancel_event.clear()
            self.pause_event.set()

        if target_dsn:
            try:
                request = from_sql(sql_statement)
                with connect(target_dsn) as conn:
                    facts = inspect_request(conn, request)
                    plan = build_plan(facts, request)
                    if plan["status"] == "BLOCKED":
                        raise ValueError(f"Plan is BLOCKED: {plan['findings']}")
                    if plan["status"] == "RISKY" and not allow_risky:
                        raise ValueError("Plan is RISKY; pass allow_risky to proceed.")
                    result = prepare_migration(conn, request, plan, allow_risky)
                    conn.commit()

                    mid = result["migration_id"]
                    handoff = create_handoff(conn, mid)

                with self.lock:
                    self.session.update({
                        "migration_id": str(mid),
                        "status": "READY_FOR_COPY",
                        "source_table": f"{handoff['source']['schema']}.{handoff['source']['table']}",
                        "shadow_table": f"{handoff['source']['schema']}.{handoff['shadow']['table']}",
                        "copied_rows": 0,
                        "estimated_rows": handoff["estimated_rows"] or 1000,
                        "copy_complete": False,
                        "is_simulation": False,
                        "error": None,
                    })
                self.log(f"Migration prepared successfully. UUID: {mid}", "success")
                self.broadcast("state_change", self.get_state())
                return {"status": "ok", "migration_id": str(mid)}
            except Exception as e:
                self.log(f"Preparation failed: {e}", "error")
                with self.lock:
                    self.session["error"] = str(e)
                return {"status": "error", "message": str(e)}

        # Simulation mode prepare
        sim_id = str(uuid4())
        with self.lock:
            self.session.update({
                "migration_id": sim_id,
                "status": "READY_FOR_COPY",
                "source_table": "schemamigrate_demo.customers",
                "shadow_table": f"customers_shadow_{sim_id[:8]}",
                "old_table": None,
                "copied_rows": 0,
                "estimated_rows": 1000,
                "copy_complete": False,
                "is_simulation": True,
                "error": None,
            })
        self.log(f"[Demo] Migration prepared with UUID: {sim_id}", "success")
        self.broadcast("state_change", self.get_state())
        return {"status": "ok", "migration_id": sim_id, "demo": True}

    # -------------------------------------------------------------------------
    # Batch Copying with Pause, Resume, and Cancel
    # -------------------------------------------------------------------------

    def start_copy(self, batch_size: int = 250, throttle: float = 0.3) -> dict[str, Any]:
        with self.lock:
            if self.worker_thread and self.worker_thread.is_alive():
                return {"status": "error", "message": "Copy is already running"}
            if self.session["status"] not in ("READY_FOR_COPY", "COPYING", "PAUSED"):
                return {"status": "error", "message": f"Cannot copy when status is {self.session['status']}"}

            self.session["batch_size"] = max(10, min(10000, batch_size))
            self.session["throttle"] = max(0.0, min(5.0, throttle))
            self.session["status"] = "COPYING"
            self.pause_event.set()
            self.cancel_event.clear()

        self.log(f"Starting batch copy: batch_size={batch_size}, throttle={throttle}s", "info")
        self.broadcast("state_change", self.get_state())

        if self.session.get("is_simulation"):
            self.worker_thread = threading.Thread(target=self._run_simulated_copy, daemon=True)
        else:
            self.worker_thread = threading.Thread(target=self._run_real_copy, daemon=True)
        self.worker_thread.start()
        return {"status": "ok", "message": "Copy started"}

    def pause_copy(self) -> dict[str, Any]:
        with self.lock:
            if self.session["status"] != "COPYING":
                return {"status": "error", "message": f"Cannot pause when status is {self.session['status']}"}
            self.pause_event.clear()
            self.session["status"] = "PAUSED"
        self.log("PAUSED: Batch copy paused by user from web UI. Current progress saved.", "warn")
        self.broadcast("state_change", self.get_state())
        return {"status": "ok", "message": "Copy paused"}

    def resume_copy(self) -> dict[str, Any]:
        with self.lock:
            if self.session["status"] != "PAUSED":
                return {"status": "error", "message": f"Cannot resume when status is {self.session['status']}"}
            self.session["status"] = "COPYING"
            self.pause_event.set()
        self.log("RESUMED: Resuming batch copy from last committed primary key cursor.", "info")
        self.broadcast("state_change", self.get_state())
        return {"status": "ok", "message": "Copy resumed"}

    def cancel_migration(self) -> dict[str, Any]:
        mid = self.session.get("migration_id")
        self.log("CANCEL: Migration cancellation requested from web UI.", "warn")
        self.cancel_event.set()
        self.pause_event.set()  # Unblock if waiting in pause

        # Wait briefly for worker thread to exit cleanly
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=2.0)

        # Database abort cleanup if real DB
        if self.dsn and mid and not self.session.get("is_simulation"):
            try:
                with connect(self.dsn) as conn:
                    abort_preparation(conn, mid)
                    conn.commit()
                self.log(f"Aborted migration {mid}. Dropped shadow table and trigger.", "success")
            except Exception as e:
                self.log(f"Error during abort cleanup: {e}", "error")

        with self.lock:
            self.session["status"] = "ABORTED"
            self.session["copy_complete"] = False

        self.log("Migration ABORTED. Safe cleanup complete.", "info")
        self.broadcast("state_change", self.get_state())
        return {"status": "ok", "message": "Migration cancelled and cleaned up"}

    # -------------------------------------------------------------------------
    # Worker Loops
    # -------------------------------------------------------------------------

    def _run_real_copy(self) -> None:
        mid = self.session["migration_id"]
        batch_size = self.session["batch_size"]
        throttle = self.session["throttle"]
        copied_this_run = 0

        try:
            while not self.cancel_event.is_set():
                # Check pause
                if not self.pause_event.is_set():
                    self.pause_event.wait()
                    if self.cancel_event.is_set():
                        break

                with connect(self.dsn) as conn:
                    result = copy_batch(conn, mid, batch_size)
                    conn.commit()

                copied_this_run += result["copied_this_batch"]
                with self.lock:
                    self.session["copied_rows"] = result["copied_total"]

                total = self.session["estimated_rows"] or 1000
                pct = min(100.0, round((result["copied_total"] / total) * 100, 1))
                self.log(
                    f"Copied batch of {result['copied_this_batch']} rows "
                    f"({result['copied_total']} / {total} - {pct}%)",
                    "info",
                )
                self.broadcast("progress", self.get_state())

                if result["copy_complete"]:
                    break

                if throttle > 0:
                    time.sleep(throttle)

            if self.cancel_event.is_set():
                return

            # Catch up forward change events
            self.log("Catching up forward change capture events...", "info")
            with connect(self.dsn) as conn:
                events = catch_up(conn, mid, "FORWARD")
                conn.commit()
                verif = verify_migration(conn, mid)
                status_rec = get_status(conn, mid)

            with self.lock:
                self.session["events_applied"] = events
                self.session["verification"] = verif
                self.session["status"] = "COPY_READY"
                self.session["copy_complete"] = True

            self.log(
                f"Catch-up complete ({events} events applied). "
                f"Verification: {'PASSED' if verif.get('verified') else 'FAILED'}. "
                "Ready for Person C cutover.",
                "success" if verif.get("verified") else "error",
            )
            self.broadcast("state_change", self.get_state())

        except Exception as e:
            self.log(f"Worker encountered error: {e}", "error")
            with self.lock:
                self.session["status"] = "ERROR"
                self.session["error"] = str(e)
            self.broadcast("state_change", self.get_state())

    def _run_simulated_copy(self) -> None:
        total = self.session["estimated_rows"] or 1000
        batch_size = self.session["batch_size"]

        try:
            while not self.cancel_event.is_set() and self.session["copied_rows"] < total:
                # Handle pause
                if not self.pause_event.is_set():
                    self.pause_event.wait()
                    if self.cancel_event.is_set():
                        break

                throttle = self.session["throttle"]
                if throttle > 0:
                    time.sleep(throttle)
                else:
                    time.sleep(0.1)

                if self.cancel_event.is_set():
                    break

                with self.lock:
                    next_batch = min(batch_size, total - self.session["copied_rows"])
                    self.session["copied_rows"] += next_batch
                    current_copied = self.session["copied_rows"]

                pct = min(100.0, round((current_copied / total) * 100, 1))
                self.log(f"Copied batch: {next_batch} rows ({current_copied} / {total} - {pct}%)", "info")
                self.broadcast("progress", self.get_state())

            if self.cancel_event.is_set():
                return

            time.sleep(0.5)
            self.log("Applying forward captured changes: 3 live writes caught up.", "info")
            time.sleep(0.4)

            verif = {
                "verified": True,
                "source_rows": total,
                "shadow_rows": total,
                "original_columns_match": True,
                "note": "Point-in-time check. Cutover repeats comparison under locks.",
            }
            with self.lock:
                self.session["events_applied"] = 3
                self.session["verification"] = verif
                self.session["status"] = "COPY_READY"
                self.session["copy_complete"] = True

            self.log("Verification PASSED: Source & Shadow row counts match. Ready for Cutover.", "success")
            self.broadcast("state_change", self.get_state())

        except Exception as e:
            self.log(f"Demo worker error: {e}", "error")
            with self.lock:
                self.session["status"] = "ERROR"
                self.session["error"] = str(e)
            self.broadcast("state_change", self.get_state())

    # -------------------------------------------------------------------------
    # Cutover, Rollback, Finalize
    # -------------------------------------------------------------------------

    def cutover(self) -> dict[str, Any]:
        mid = self.session.get("migration_id")
        if self.session["status"] != "COPY_READY":
            return {"status": "error", "message": f"Cannot cutover from status {self.session['status']}"}

        self.log("Executing cutover: Acquiring ACCESS EXCLUSIVE lock on source & shadow...", "warn")

        if self.dsn and mid and not self.session.get("is_simulation"):
            try:
                with connect(self.dsn) as conn:
                    res = cutover(conn, mid)
                    conn.commit()
                with self.lock:
                    self.session["status"] = "SWAPPED"
                    self.session["old_table"] = res.get("old_table")
                self.log("Cutover complete! Table names swapped atomically. Reverse trigger active.", "success")
                self.broadcast("state_change", self.get_state())
                return {"status": "ok", "result": res}
            except Exception as e:
                self.log(f"Cutover failed: {e}", "error")
                return {"status": "error", "message": str(e)}

        # Simulation
        time.sleep(0.6)
        with self.lock:
            self.session["status"] = "SWAPPED"
            self.session["old_table"] = f"customers_old_{str(uuid4())[:8]}"
        self.log("[Demo] Locked cutover succeeded. Table renamed to live. Rollback window open.", "success")
        self.broadcast("state_change", self.get_state())
        return {"status": "ok", "demo": True}

    def rollback(self) -> dict[str, Any]:
        mid = self.session.get("migration_id")
        if self.session["status"] != "SWAPPED":
            return {"status": "error", "message": f"Cannot rollback from status {self.session['status']}"}

        self.log("Executing rollback: Replaying reverse captured writes and restoring table...", "warn")

        if self.dsn and mid and not self.session.get("is_simulation"):
            try:
                with connect(self.dsn) as conn:
                    res = rollback(conn, mid)
                    conn.commit()
                with self.lock:
                    self.session["status"] = "ROLLED_BACK"
                self.log("Rollback complete! Original table restored live.", "success")
                self.broadcast("state_change", self.get_state())
                return {"status": "ok", "result": res}
            except Exception as e:
                self.log(f"Rollback failed: {e}", "error")
                return {"status": "error", "message": str(e)}

        time.sleep(0.6)
        with self.lock:
            self.session["status"] = "ROLLED_BACK"
        self.log("[Demo] Rollback complete. Original schema is live again.", "success")
        self.broadcast("state_change", self.get_state())
        return {"status": "ok", "demo": True}

    def finalize(self) -> dict[str, Any]:
        mid = self.session.get("migration_id")
        if self.session["status"] not in ("SWAPPED", "ROLLED_BACK"):
            return {"status": "error", "message": f"Cannot finalize from status {self.session['status']}"}

        self.log("Finalizing: Dropping retained backup table and ending rollback window...", "warn")

        if self.dsn and mid and not self.session.get("is_simulation"):
            try:
                with connect(self.dsn) as conn:
                    res = finalize_migration(conn, mid)
                    conn.commit()
                with self.lock:
                    self.session["status"] = res.get("status", "FINALIZED")
                self.log(f"Finalized successfully. Removed table: {res.get('removed_table')}.", "success")
                self.broadcast("state_change", self.get_state())
                return {"status": "ok", "result": res}
            except Exception as e:
                self.log(f"Finalize failed: {e}", "error")
                return {"status": "error", "message": str(e)}

        time.sleep(0.4)
        with self.lock:
            self.session["status"] = "FINALIZED"
        self.log("[Demo] Migration finalized. Old table deleted. Rollback permanently closed.", "success")
        self.broadcast("state_change", self.get_state())
        return {"status": "ok", "demo": True}
