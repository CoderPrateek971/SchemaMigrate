"""Person A: straightforward risk rules and rough sizing estimates."""
from math import ceil

COPY_BYTES_PER_SECOND = 25 * 1024 * 1024
SHADOW_DISK_SAFETY_FACTOR = 1.5


def build_plan(inspection: dict, request=None) -> dict:
    findings, risks = [], []
    status = "SAFE"
    if not inspection["table_exists"]:
        status = "BLOCKED"
        findings.append("The target table does not exist.")
    elif inspection["requested_column_exists"]:
        status = "BLOCKED"
        findings.append("The requested column already exists.")
    elif not inspection["primary_key_columns"]:
        status = "BLOCKED"
        findings.append("A primary key is required for row copying.")
    elif len(inspection["primary_key_columns"]) != 1:
        status = "BLOCKED"
        findings.append("This V1 copy path supports a single-column primary key.")
    elif request and not request.nullable and request.default_sql in (None, "NULL"):
        status = "BLOCKED"
        findings.append("A NOT NULL column needs a non-NULL default for copied and future rows.")
    else:
        findings.append("Basic table and requested-column checks passed.")
        if inspection["triggers"]:
            status = "BLOCKED"
            findings.append("Existing triggers need manual integration before migration.")
        if inspection["row_level_security"] or inspection["force_row_level_security"]:
            status = "BLOCKED"
            findings.append("Tables with row-level security are outside this V1 path.")
        if inspection["partitioned"]:
            status = "BLOCKED"
            findings.append("Partitioned tables are outside this V1 path.")
        if any(c["type"] == "FOREIGN KEY" for c in inspection["constraints"]):
            status = "BLOCKED"
            findings.append("Foreign-key constraints need explicit integration.")
        if inspection.get("incoming_foreign_keys"):
            status = "BLOCKED"
            findings.append("Other tables reference this table with foreign keys.")
        if any(c.get("identity") or c.get("generated") for c in inspection["columns"]):
            status = "BLOCKED"
            findings.append("Existing identity/generated columns are outside this V1 copy path.")
        if any("nextval(" in (c["default"] or "") for c in inspection["columns"]):
            risks.append("Sequence-backed defaults need review.")
        if inspection["table_size_bytes"] is None:
            risks.append("PostgreSQL did not provide a table-size estimate.")
        if status != "BLOCKED":
            if risks:
                status = "RISKY"
                findings.extend(risks)
            else:
                findings.append("No additional risk flags were found by these checks.")

    size = inspection["table_size_bytes"]
    seconds = ceil(size / COPY_BYTES_PER_SECOND) if size is not None else None
    disk = ceil(size * SHADOW_DISK_SAFETY_FACTOR) if size is not None else None
    return {
        "status": status, "findings": findings,
        "estimated_copy_seconds": seconds, "estimated_shadow_disk_bytes": disk,
        "inspection": inspection,
        "estimate_note": "Rough estimates only; actual copy time and disk use depend on workload and indexes.",
    }
