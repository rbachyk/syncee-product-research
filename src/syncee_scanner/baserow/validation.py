"""Pre-scan schema validation (spec §16, §43.3).

Before any scan the scanner confirms the live Baserow tables contain the required fields
from :mod:`.schemas`. Missing required fields raise BASEROW_SCHEMA_MISMATCH so scans fail
fast and clearly rather than writing to the wrong shape.
"""

from __future__ import annotations

from ..observability.errors import BaserowSchemaMismatch
from ..observability.logging import get_logger
from .client import BaserowClient
from .schemas import ALL_TABLES, TableDef

log = get_logger(__name__)


def validate_table(client: BaserowClient, table_id: str | int, table: TableDef) -> dict[str, dict]:
    """Validate one table; return its {field_name: metadata} map.

    Raises:
        BaserowSchemaMismatch: if any required field is missing.
    """
    live = client.field_map(table_id)
    missing = [f.name for f in table.fields if f.required and f.name not in live]
    if missing:
        raise BaserowSchemaMismatch(
            f"Table '{table.name}' (id={table_id}) is missing required fields: "
            + ", ".join(missing)
        )
    return live


def validate_all(client: BaserowClient, table_ids: dict[str, str | int]) -> dict[str, dict]:
    """Validate every known table. Returns {table_name: field_map}.

    Args:
        table_ids: {table_name: baserow_table_id} for all six tables.
    """
    field_maps: dict[str, dict] = {}
    for table in ALL_TABLES:
        table_id = table_ids.get(table.name)
        if not table_id:
            raise BaserowSchemaMismatch(f"No configured table ID for '{table.name}'")
        field_maps[table.name] = validate_table(client, table_id, table)
        log.info("baserow.schema_ok", table=table.name, table_id=str(table_id))
    return field_maps
