# SchemaMigrate SETUP Guide
*Course Project 5: Zero-Downtime PostgreSQL Column Migrations*

This document provides setup instructions matching the professor's **V1 Requirements & Deliverables Checklist**.

---

## 1. System Requirements

* **Python:** 3.11 or newer (Python 3.13 recommended)
* **PostgreSQL:** PostgreSQL 16, 17, or 18 running locally
* **Operating System:** macOS, Linux, or Windows WSL

---

## 2. Environment Installation

In the root directory of this project:

```bash
# 1. Clear any OS quarantine attributes (macOS users)
xattr -d -r com.apple.quarantine . 2>/dev/null || true

# 2. Create a clean virtual environment
rm -rf .venv
python3 -m venv .venv

# 3. Activate virtual environment
# macOS/Linux:
source .venv/bin/activate
# Windows PowerShell:
# .venv\Scripts\Activate.ps1

# 4. Install dependencies in editable mode
pip install --upgrade pip
pip install -e .
```

Verify that `schemamigrate` CLI and unit tests are working:
```bash
python -m unittest discover tests
python main.py --help
```

---

## 3. Database Configuration

You need a PostgreSQL role with rights to create databases.

### Step 3.1: Maintenance / Admin Connection
```bash
# Example for local PostgreSQL (default port 5432 or 5433)
export SCHEMAMIGRATE_ADMIN_DSN='host=127.0.0.1 port=5432 dbname=postgres user=postgres'
```

### Step 3.2: Initialize the Demo Database (Person B — Prateek)
Run the automated seed script to create `schemamigrate_demo` and seed the `customers` table:
```bash
# Standard 1,000 rows:
python scripts/create_demo_db.py

# Or scale up for large-table demonstration (e.g. 100,000 rows):
# python scripts/create_demo_db.py --rows 100000
```

### Step 3.3: Set Application Connection DSN
```bash
export SCHEMAMIGRATE_DSN='host=127.0.0.1 port=5432 dbname=schemamigrate_demo user=postgres'
```

---

## 4. Running the 3-Person Migration Workflow

### Step 1: Person A (Saksham) — Analyze Risk & Prepare Shadow Table
```bash
# 1. Analyze and plan:
python main.py plan --sql "ALTER TABLE schemamigrate_demo.customers ADD COLUMN loyalty_level integer DEFAULT 1"

# 2. Run Person A end-to-end flow:
python mainA.py --sql "ALTER TABLE schemamigrate_demo.customers ADD COLUMN loyalty_level integer DEFAULT 1"
```
*Person A inspects catalog, creates shadow table, attaches forward CDC trigger, and outputs a **Migration UUID**.*

---

### Step 2: Person B (Prateek) — Keyset Batch Copy & Forward CDC Replay
Using the UUID from Person A:
```bash
# Full flow with percentage progress tracking and batch throttling:
python mainB.py <MIGRATION_UUID> --batch-size 250 --throttle 0.02
```
*Person B copies rows using keyset pagination (`WHERE id > last_key`), calculates % complete, throttles to prevent DB lockup, and replays concurrent writes.*

---

### Step 3: Person C (Nishtha) — Verification, Atomic Cutover & Rollback
```bash
# Run Person C cutover:
python mainC.py <MIGRATION_UUID>
```
*Person C verifies row counts, MD5 row checksums, index validity, and constraint validity. Executes sub-second atomic table swap, installs reverse CDC, and allows KEEP, ROLLBACK, or FINALIZE.*

---

## 5. Automated Verification & Concurrent Write Testing

To test concurrent writes and rollback programmatically:
```bash
python scripts/test_concurrent_writes.py
```
This tests background writes (`INSERT`, `UPDATE`, `DELETE`) happening during batch copying, verifies zero data loss, performs cutover, and executes rollback.
