# SchemaMigrate manual

## 1. Install prerequisites

Use Python 3.11+ and a local PostgreSQL server. In this folder:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

The server must accept connections. Set a maintenance connection string (a role that can create a database). The setup script will ask for your PostgreSQL password in the terminal; input is hidden while you type and the password is not saved:

```sh
export SCHEMAMIGRATE_ADMIN_DSN='host=127.0.0.1 port=5433 dbname=postgres user=postgres'
```

Change the port if your PostgreSQL server uses a different one.

## 2. Create/reset the demo database

```sh
python scripts/create_demo_db.py
```

The script creates `schemamigrate_demo` if missing and runs its embedded PostgreSQL setup statements. **Every run drops and recreates only `schemamigrate_demo` and `schemamigrate_meta_v1` inside that dedicated database**, then inserts exactly 1,000 customers with four columns. Do not point the admin connection at a valuable database for this demo.

Set the application DSN to the same server/user but the demo database:

```sh
export SCHEMAMIGRATE_DSN='host=127.0.0.1 port=5433 dbname=schemamigrate_demo user=postgres'
```

The migration commands will ask for the PostgreSQL password when they connect. You can confirm the seed with psql:

```sql
SELECT count(*) FROM schemamigrate_demo.customers; -- expected 1000
```

## 3. Person A: plan and prepare

```sh
python main.py plan --sql 'ALTER TABLE schemamigrate_demo.customers ADD COLUMN loyalty_level integer DEFAULT 1'
python main.py prepare --sql 'ALTER TABLE schemamigrate_demo.customers ADD COLUMN loyalty_level integer DEFAULT 1'
```

Review the plan. Type `PREPARE` when asked. Save the printed migration UUID. The new column is added to the shadow table only; the original `customers` remains the live table. The PostgreSQL trigger records writes into `schemamigrate_meta_v1.change_events`.

### Run all Person A steps together

For one command that demonstrates Person A's parse → inspect/plan → prepare → handoff flow, run:

```sh
python mainA.py --sql 'ALTER TABLE schemamigrate_demo.customers ADD COLUMN loyalty_level integer DEFAULT 1'
```

It prompts for the database password, prints the plan, requires the exact `PREPARE` confirmation before changing the database, then prints the migration UUID and saved handoff metadata for Person B. Use this on a fresh demo setup; do not run it again while an earlier migration for this table is still active.

## 4. Person B: copy, replay, verify, hand off

To run Person B's whole flow in one command, use the UUID printed by `mainA.py`:

```sh
python mainB.py MIGRATION_UUID
```

It reads Person A's saved handoff, copies rows in committed batches, applies captured forward events, verifies the shadow, and prints whether Person C can proceed. Add `--batch-size 250` to use smaller batches. The individual commands below remain available when demonstrating each sub-step separately.

Copy all batches, with each batch committed independently so the run can resume:

```sh
python main.py copy MIGRATION_UUID --all
```

For one batch at a time, use `python main.py copy MIGRATION_UUID --batch-size 250` and check progress with `python main.py status MIGRATION_UUID`.

To demonstrate concurrent writes, run this in psql while batches are being copied:

```sql
INSERT INTO schemamigrate_demo.customers (id, full_name, email, created_at)
VALUES (1001, 'Concurrent Demo', 'concurrent@example.test', now());
UPDATE schemamigrate_demo.customers SET full_name = 'Customer One Updated' WHERE id = 1;
DELETE FROM schemamigrate_demo.customers WHERE id = 2;
```

Then replay events and check data:

```sh
python main.py catch-up MIGRATION_UUID
python main.py verify MIGRATION_UUID
python main.py handoff MIGRATION_UUID
```

Verification is point-in-time. Cutover does the final full comparison under locks.

## 5. Person C: cutover and rollback

To run Person C's flow with one script, use:

```sh
python mainC.py MIGRATION_UUID
```

For a `COPY_READY` migration, it shows the current state and requires `CUTOVER`, then performs the locked cutover and checks the live row count and requested column. Afterward, type `ROLLBACK` to restore the old table, `FINALIZE` to permanently remove the retained old table, or press Enter to keep the new table active with its rollback window. `ROLLBACK` and `FINALIZE` each require a second exact-word confirmation. If the migration is already `SWAPPED`, rerunning the script lets you choose rollback or finalize without repeating cutover.

```sh
python main.py cutover MIGRATION_UUID
```

Type `CUTOVER`. The program blocks table writes during final replay and comparison, then renames the shadow to the live table name in the same PostgreSQL transaction. Check the new column:

```sql
SELECT count(*), count(loyalty_level) FROM schemamigrate_demo.customers;
```

Reverse capture is active after swap. To demonstrate rollback, make a write to the new live table, then:

```sh
python main.py rollback MIGRATION_UUID
```

Type `ROLLBACK`. It replays reverse events, compares the original columns, then restores the old table name. If keeping the migration, end rollback availability with `python main.py finalize MIGRATION_UUID` and type `FINALIZE`.

Before cutover, `python main.py abort MIGRATION_UUID` removes a prepared shadow and trigger; type `ABORT`. Do not use `abort` after a swap.

## 6. Web Dashboard: Real-Time Browser Console

To run and monitor migrations with a graphical user interface in the browser:

```sh
python mainWeb.py
# or: python main.py dashboard --port 8080
```

Open `http://127.0.0.1:8080` in your web browser.

Features:
- **Live Progress Tracking:** Visual percentage bar, current rows copied, and stage visualizer.
- **Server-Sent Events (SSE):** Streaming log updates and progress changes pushed in real time.
- **Pause & Resume Controls:** Click **Pause** to freeze copying between batches; click **Resume** to restart from the cursor.
- **Cancel Migration:** Click **Cancel** before cutover to trigger cleanup and abort safely.
- **Cutover & Rollback:** Execute atomic swap, view verification results, roll back if needed, or finalize.
- **Demo Mode:** Start an instant simulation migration directly from the UI without requiring an active PostgreSQL database.

## 7. Run local checks

```sh
python -m unittest discover -s tests -v
```

This checks the parser/planner without touching PostgreSQL. The demo commands above are the PostgreSQL integration walkthrough.

## SQL-resource convention

Runtime query templates are stored by API and section in `schemamigrate/sql_templates.py`; `sql_resources.py` retrieves them as composable statements. Person A catalog queries remain next to their adapter functions in `database.py`. Dynamic identifiers are quoted with psycopg and data values use bound parameters. The demo setup statements are embedded in `scripts/create_demo_db.py`; there are no separate `.sql` files to manage.
