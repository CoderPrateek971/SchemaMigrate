from pglast import ast, parse_sql
from pglast.enums.parsenodes import AlterTableType, ConstrType
from pglast.stream import RawStream

from schemamigrate.models import MigrationRequest


class RequestParseError(ValueError):
    """The request is invalid or outside the supported V1 syntax."""


def from_sql(statement: str) -> MigrationRequest:
    """Parse one ALTER TABLE ... ADD COLUMN statement."""
    try:
        statements = parse_sql(statement)
    except Exception as exc:
        raise RequestParseError(
            f"PostgreSQL could not parse this SQL: {exc}"
        ) from exc

    if len(statements) != 1:
        raise RequestParseError("Enter exactly one SQL statement.")

    alter = statements[0].stmt

    if not isinstance(alter, ast.AlterTableStmt):
        raise RequestParseError("V1 expects ALTER TABLE ... ADD COLUMN.")

    if (
        len(alter.cmds) != 1
        or alter.cmds[0].subtype != AlterTableType.AT_AddColumn
    ):
        raise RequestParseError("V1 supports one ADD COLUMN action at a time.")

    table_ref = alter.relation

    if table_ref.catalogname:
        raise RequestParseError(
            "A PostgreSQL table request cannot target another database."
        )

    if table_ref.relpersistence == "t":
        raise RequestParseError("Temporary tables are not supported.")

    if table_ref.inh is False:
        raise RequestParseError("V1 does not support ALTER TABLE ONLY.")

    schema = table_ref.schemaname or "public"
    table = table_ref.relname

    command = alter.cmds[0]

    if command.missing_ok:
        raise RequestParseError(
            "V1 does not support ADD COLUMN IF NOT EXISTS."
        )

    column_def = command.def_

    if not isinstance(column_def, ast.ColumnDef):
        raise RequestParseError("Expected a normal column definition.")

    parsed_constraints = column_def.constraints or ()

    if any(
        constraint.contype in {
            ConstrType.CONSTR_IDENTITY,
            ConstrType.CONSTR_GENERATED,
        }
        for constraint in parsed_constraints
    ):
        raise RequestParseError(
            "V1 does not support identity or generated new columns yet."
        )

    supported_constraints = {
        ConstrType.CONSTR_NOTNULL,
        ConstrType.CONSTR_DEFAULT,
    }

    if any(
        constraint.contype not in supported_constraints
        for constraint in parsed_constraints
    ):
        raise RequestParseError(
            "V1 does not support extra column constraints yet."
        )

    if column_def.collClause:
        raise RequestParseError(
            "V1 does not support COLLATE clauses on the new column yet."
        )

    not_null = any(
        constraint.contype == ConstrType.CONSTR_NOTNULL
        for constraint in parsed_constraints
    )

    default_constraints = [
        constraint
        for constraint in parsed_constraints
        if constraint.contype == ConstrType.CONSTR_DEFAULT
    ]

    if len(default_constraints) > 1:
        raise RequestParseError("Only one DEFAULT expression is supported.")

    default_sql = (
        RawStream()(default_constraints[0].raw_expr)
        if default_constraints
        else None
    )

    return MigrationRequest(
        schema=schema,
        table=table,
        operation="ADD_COLUMN",
        column=column_def.colname,
        type_sql=RawStream()(column_def.typeName),
        nullable=not not_null,
        default_sql=default_sql,
    )