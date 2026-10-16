"""Small stdlib-only checks; database integration is documented in MANUAL.md."""
import unittest

from schemamigrate.models import MigrationRequest
from schemamigrate.parser.request_parser import RequestParseError, from_sql
from schemamigrate.planner import build_plan


class ParserTests(unittest.TestCase):
    def test_supported_request(self):
        request = from_sql("ALTER TABLE demo.users ADD COLUMN age integer NOT NULL DEFAULT 18")
        self.assertEqual((request.schema, request.table, request.column), ("demo", "users", "age"))
        self.assertFalse(request.nullable)
        self.assertEqual(request.default_sql, "18")

    def test_rejects_multiple_statements(self):
        with self.assertRaises(RequestParseError):
            from_sql("ALTER TABLE users ADD COLUMN age int; SELECT 1")

    def test_quoted_schema_and_table_name(self):
        request = from_sql('ALTER TABLE "sales-data"."UserData" ADD COLUMN age int')
        self.assertEqual((request.schema, request.table), ("sales-data", "UserData"))


class PlannerTests(unittest.TestCase):
    def snapshot(self, **changes):
        facts = {"table_exists": True, "requested_column_exists": False,
                 "primary_key_columns": ["id"], "columns": [{"name": "id", "default": None,
                 "identity": False, "generated": False}], "triggers": [],
                 "row_level_security": False, "force_row_level_security": False,
                 "partitioned": False, "constraints": [], "incoming_foreign_keys": [],
                 "table_size_bytes": 1024}
        facts.update(changes)
        return facts

    def test_supported_table_safe(self):
        self.assertEqual(build_plan(self.snapshot())["status"], "SAFE")

    def test_partitioned_table_blocked(self):
        self.assertEqual(build_plan(self.snapshot(partitioned=True))["status"], "BLOCKED")

    def test_composite_key_blocked(self):
        self.assertEqual(build_plan(self.snapshot(primary_key_columns=["tenant", "id"]))["status"], "BLOCKED")

    def test_existing_column_blocked(self):
        self.assertEqual(build_plan(self.snapshot(requested_column_exists=True))["status"], "BLOCKED")

    def test_not_null_without_default_blocked(self):
        req = MigrationRequest("demo", "users", "ADD_COLUMN", "age", "integer", False, None)
        self.assertEqual(build_plan(self.snapshot(), req)["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
