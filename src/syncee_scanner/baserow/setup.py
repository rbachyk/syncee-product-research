"""Baserow database/table setup helper (spec §9, Phase 1).

Creates the ``RB Home Product Research`` database with all six tables, their fields, and
single-select options, then returns the resulting table IDs to paste into ``.env``
(spec §16.1). Table creation requires a *user JWT* (email/password) rather than the
database token, so credentials come from ``BASEROW_USER_EMAIL`` / ``BASEROW_USER_PASSWORD``
/ ``BASEROW_WORKSPACE_ID``.

This runs against live Baserow and is intentionally separate from the scan path. It is
idempotent-friendly at the database level only in that it will report what it created; it
does not attempt to reconcile a pre-existing database (create once, then use table IDs).
"""

from __future__ import annotations

import httpx

from ..observability.errors import BaserowAuthError, BaserowError
from ..observability.logging import get_logger
from .schemas import ALL_TABLES, DATABASE_NAME, FieldDef, FieldType, TableDef

log = get_logger(__name__)

_SELECT_COLORS = [
    "light-blue", "light-green", "light-orange", "light-red", "light-purple",
    "blue", "green", "orange", "red", "purple", "brown", "pink", "gray",
]


def _field_payload(f: FieldDef, table_ids: dict[str, int]) -> dict:
    """Translate a FieldDef into a Baserow field-create body."""
    if f.type == FieldType.NUMBER:
        return {
            "name": f.name,
            "type": "number",
            "number_decimal_places": f.number_decimals,
            "number_negative": True,
        }
    if f.type == FieldType.DATE:
        return {"name": f.name, "type": "date", "date_include_time": True, "date_format": "ISO"}
    if f.type == FieldType.SINGLE_SELECT:
        return {
            "name": f.name,
            "type": "single_select",
            "select_options": [
                {"value": v, "color": _SELECT_COLORS[i % len(_SELECT_COLORS)]}
                for i, v in enumerate(f.select_options)
            ],
        }
    if f.type == FieldType.LINK_ROW:
        target = table_ids.get(f.link_table or "")
        if not target:
            raise BaserowError(
                f"Cannot create link field '{f.name}': target table "
                f"'{f.link_table}' not created yet"
            )
        return {"name": f.name, "type": "link_row", "link_row_table_id": target}
    # text / long_text / url / boolean map directly.
    return {"name": f.name, "type": f.type.value}


class BaserowSetup:
    def __init__(self, api_url: str, *, timeout: float = 30.0) -> None:
        self.api_url = api_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout)
        self._jwt: str | None = None

    def close(self) -> None:
        self._client.close()

    # --- Auth ---------------------------------------------------------------------

    def authenticate(self, email: str, password: str) -> None:
        resp = self._client.post(
            f"{self.api_url}/api/user/token-auth/",
            json={"email": email, "password": password},
        )
        if resp.status_code >= 400:
            raise BaserowAuthError(f"Baserow login failed: {resp.status_code} {resp.text[:200]}")
        self._jwt = resp.json().get("access_token") or resp.json().get("token")
        if not self._jwt:
            raise BaserowAuthError("Baserow login returned no token")

    def _headers(self) -> dict[str, str]:
        if not self._jwt:
            raise BaserowAuthError("Not authenticated; call authenticate() first")
        return {"Authorization": f"JWT {self._jwt}"}

    def _post(self, path: str, json: dict) -> dict:
        resp = self._client.post(f"{self.api_url}{path}", json=json, headers=self._headers())
        if resp.status_code >= 400:
            raise BaserowError(f"POST {path} failed: {resp.status_code} {resp.text[:300]}")
        return resp.json()

    def _patch(self, path: str, json: dict) -> dict:
        resp = self._client.patch(f"{self.api_url}{path}", json=json, headers=self._headers())
        if resp.status_code >= 400:
            raise BaserowError(f"PATCH {path} failed: {resp.status_code} {resp.text[:300]}")
        return resp.json()

    def _delete(self, path: str) -> None:
        resp = self._client.delete(f"{self.api_url}{path}", headers=self._headers())
        if resp.status_code >= 400:
            raise BaserowError(f"DELETE {path} failed: {resp.status_code} {resp.text[:300]}")

    # --- Creation -----------------------------------------------------------------

    def create_database(self, workspace_id: str | int) -> int:
        data = self._post(
            f"/api/applications/workspace/{workspace_id}/",
            {"name": DATABASE_NAME, "type": "database"},
        )
        log.info("baserow.database_created", database_id=data["id"], name=DATABASE_NAME)
        return data["id"]

    def _create_table(self, database_id: int, table: TableDef) -> int:
        data = self._post(
            f"/api/database/tables/database/{database_id}/",
            {"name": table.name},
        )
        return data["id"]

    def _reconcile_primary_and_defaults(self, table_id: int, table: TableDef) -> None:
        """Rename the default primary field and remove other default fields."""
        fields = self._client.get(
            f"{self.api_url}/api/database/fields/table/{table_id}/", headers=self._headers()
        ).json()
        primary = next((f for f in fields if f.get("primary")), None)
        if primary:
            self._patch(
                f"/api/database/fields/{primary['id']}/",
                {"name": table.primary_field.name, "type": "text"},
            )
        for f in fields:
            if not f.get("primary"):
                self._delete(f"/api/database/fields/{f['id']}/")

    def create_all(self, database_id: int) -> dict[str, int]:
        """Create every table and its fields; return {table_name: table_id}."""
        table_ids: dict[str, int] = {}
        # First pass: create tables + primary fields (so link targets exist).
        for table in ALL_TABLES:
            tid = self._create_table(database_id, table)
            self._reconcile_primary_and_defaults(tid, table)
            table_ids[table.name] = tid
            log.info("baserow.table_created", table=table.name, table_id=tid)
        # Second pass: create non-primary fields (link fields can now resolve targets).
        for table in ALL_TABLES:
            tid = table_ids[table.name]
            for f in table.fields:
                if f.primary:
                    continue
                self._post(
                    f"/api/database/fields/table/{tid}/",
                    _field_payload(f, table_ids),
                )
            log.info("baserow.fields_created", table=table.name, count=len(table.fields) - 1)
        return table_ids


    def _list_fields(self, table_id: int) -> list[dict]:
        return self._client.get(
            f"{self.api_url}/api/database/fields/table/{table_id}/", headers=self._headers()
        ).json()

    def create_fields_in_existing(self, table_ids: dict[str, int]) -> dict[str, dict]:
        """Add the schema's fields to already-existing tables (idempotent).

        For each table (identified by the IDs the user already put in ``.env``): rename the
        default primary field to the schema's primary field, then create only the fields
        that are missing (matched by name). Existing fields are left untouched; nothing is
        deleted and no tables are created. Returns per-table {created, skipped}.
        """
        # Coerce env-string IDs to ints so link_row_table_id is numeric.
        int_ids = {name: int(tid) for name, tid in table_ids.items()}
        summary: dict[str, dict] = {}
        for table in ALL_TABLES:
            tid = int_ids[table.name]
            existing = {f["name"]: f for f in self._list_fields(tid)}
            created = skipped = 0

            # Reconcile the primary field name (manual tables start with a "Name" primary).
            primary = next((f for f in existing.values() if f.get("primary")), None)
            target = table.primary_field.name
            if primary and primary["name"] != target and target not in existing:
                self._patch(
                    f"/api/database/fields/{primary['id']}/", {"name": target, "type": "text"}
                )
                existing[target] = primary

            for f in table.fields:
                if f.primary or f.name in existing:
                    skipped += 1
                    continue
                self._post(f"/api/database/fields/table/{tid}/", _field_payload(f, int_ids))
                created += 1
            summary[table.name] = {"created": created, "skipped": skipped}
            log.info("baserow.fields_synced", table=table.name, created=created, skipped=skipped)
        return summary

    # --- Views (spec §30) ---------------------------------------------------------

    def create_views(self, table_ids: dict[str, int]) -> int:
        """Create the operational views (spec §30). Returns the count created."""
        from .views import VIEW_SPECS, build_filter_payloads

        field_maps = {
            name: {
                f["name"]: f
                for f in self._client.get(
                    f"{self.api_url}/api/database/fields/table/{tid}/",
                    headers=self._headers(),
                ).json()
            }
            for name, tid in table_ids.items()
        }
        existing = {
            spec_table: {
                v["name"]
                for v in self._client.get(
                    f"{self.api_url}/api/database/views/table/{tid}/",
                    headers=self._headers(),
                ).json()
            }
            for spec_table, tid in table_ids.items()
        }
        created = 0
        for spec in VIEW_SPECS:
            if spec.name in existing.get(spec.table, set()):
                continue  # idempotent — don't duplicate views on re-run
            table_id = table_ids[spec.table]
            view = self._post(
                f"/api/database/views/table/{table_id}/",
                {"name": spec.name, "type": spec.view_type},
            )
            if spec.cover_field:
                cover = field_maps[spec.table].get(spec.cover_field)
                if cover:
                    self._patch(
                        f"/api/database/views/{view['id']}/",
                        {"card_cover_image_field": cover["id"]},
                    )
            for payload in build_filter_payloads(spec, field_maps[spec.table]):
                self._post(f"/api/database/views/{view['id']}/filters/", payload)
            created += 1
            log.info("baserow.view_created", view=spec.name, table=spec.table)
        return created


def run_setup_fields(
    *, api_url: str, email: str, password: str, table_ids: dict[str, int], views: bool = True
) -> dict[str, dict]:
    """Create the schema's fields in existing tables (+ optional views). Returns a summary."""
    setup = BaserowSetup(api_url)
    try:
        setup.authenticate(email, password)
        summary = setup.create_fields_in_existing(table_ids)
        if views:
            setup.create_views(table_ids)
        return summary
    finally:
        setup.close()


def run_views(
    *, api_url: str, email: str, password: str, table_ids: dict[str, int]
) -> int:
    """Create operational views on already-existing tables (spec §30)."""
    setup = BaserowSetup(api_url)
    try:
        setup.authenticate(email, password)
        return setup.create_views(table_ids)
    finally:
        setup.close()


def run_setup(
    *, api_url: str, email: str, password: str, workspace_id: str | int, views: bool = True
) -> dict[str, int]:
    """End-to-end setup: authenticate, create database + tables (+ views), return table IDs."""
    setup = BaserowSetup(api_url)
    try:
        setup.authenticate(email, password)
        database_id = setup.create_database(workspace_id)
        table_ids = setup.create_all(database_id)
        if views:
            setup.create_views(table_ids)
        return table_ids
    finally:
        setup.close()
