"""Small stdlib-only checks; database integration is documented in MANUAL.md."""
import unittest

from schemamigrate.models import MigrationRequest
from schemamigrate.parser.request_parser import RequestParseError, from_sql


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


if __name__ == "__main__":
    unittest.main()
