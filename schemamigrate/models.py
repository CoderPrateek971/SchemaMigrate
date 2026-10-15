from dataclasses import dataclass

# dataclass helps code make a bit clean a bit short,  
# frozen=True: Makes the class immutable. Any attempt to modify a field raises a FrozenInstanceError, & it generates a __hash__ method so the instance can be used as a dictionary key or in a set
@dataclass(frozen=True)
class MigrationRequest:
    schema: str
    table: str
    operation: str
    column: str
    type_sql: str
    nullable: bool = True
    default_sql: str | None = None