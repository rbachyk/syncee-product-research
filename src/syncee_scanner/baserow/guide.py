"""Manual Baserow setup guide (spec §9, Phase 1 "table creation script *or* setup guide").

Renders step-by-step instructions to create the database, tables, fields and views by hand
in the Baserow UI, so the scanner can run with only a database token — no user password is
ever needed. Generated from the same schema definitions the auto-setup uses, so it can't
drift from them.
"""

from __future__ import annotations

from .schemas import ALL_TABLES, DATABASE_NAME, FieldDef, FieldType
from .views import VIEW_SPECS

_TYPE_LABEL = {
    FieldType.TEXT: "Single line text",
    FieldType.LONG_TEXT: "Long text",
    FieldType.URL: "URL",
    FieldType.BOOLEAN: "Boolean",
    FieldType.NUMBER: "Number",
    FieldType.DATE: "Date (include time, ISO)",
    FieldType.SINGLE_SELECT: "Single select",
    FieldType.LINK_ROW: "Link to table",
    FieldType.FILE: "File / attachment",
}


def _field_line(f: FieldDef) -> str:
    label = _TYPE_LABEL[f.type]
    extra = ""
    if f.type == FieldType.SINGLE_SELECT:
        extra = f" — options: {', '.join(f.select_options)}"
    elif f.type == FieldType.LINK_ROW:
        extra = f" — link to **{f.link_table}**"
    elif f.type == FieldType.NUMBER and f.number_decimals:
        extra = f" — {f.number_decimals} decimals"
    req = " *(required)*" if f.required else ""
    return f"  - `{f.name}` — {label}{extra}{req}"


def render_setup_guide() -> str:
    """Return the full manual-setup guide as Markdown."""
    lines = [
        f"# Baserow manual setup — {DATABASE_NAME}",
        "",
        "You only need a **database token** to run the scanner. If you'd rather not give the",
        "setup helper your Baserow password, create the schema by hand using this guide, then",
        "set `BASEROW_DATABASE_TOKEN` and the six `*_TABLE_ID` values in `.env`.",
        "",
        "## 1. Create the database",
        f"In your workspace, create a new database application named **{DATABASE_NAME}**.",
        "",
        "## 2. Create the tables and fields",
        "Create each table below. Rename its default primary field to the **first** field",
        "listed and delete any other default fields. Field IDs don't matter — the scanner",
        "resolves fields by name.",
        "",
    ]
    for table in ALL_TABLES:
        lines.append(f"### Table: {table.name}")
        lines.append(f"- Primary field: `{table.primary_field.name}`")
        lines.append("- Fields:")
        for f in table.fields:
            lines.append(_field_line(f))
        lines.append("")

    lines += [
        "## 3. Create the operational views (optional, spec §30)",
        "Add these grid views (with the noted filter) for review:",
        "",
    ]
    for spec in VIEW_SPECS:
        filt = "; ".join(
            f"{fl.field_name} {fl.type.replace('_', ' ')}"
            + (f" = {fl.value}" if fl.value not in (None, True, False) else
               (" = true" if fl.value is True else " = false" if fl.value is False else ""))
            for fl in spec.filters
        )
        lines.append(f"- **{spec.name}** on `{spec.table}` — filter: {filt or '(none)'}")

    lines += [
        "",
        "## 4. Configure the scanner",
        "Create a database token (Settings → Database tokens) with read/write on this",
        "database, then in `.env`:",
        "```",
        "BASEROW_DATABASE_TOKEN=...",
        "BASEROW_SUPPLIERS_TABLE_ID=...        # from each table's URL or API docs",
        "BASEROW_PRODUCTS_TABLE_ID=...",
        "BASEROW_SCAN_RUNS_TABLE_ID=...",
        "BASEROW_PRODUCT_CHANGES_TABLE_ID=...",
        "BASEROW_MANUAL_DECISIONS_TABLE_ID=...",
        "BASEROW_SELECTION_BATCHES_TABLE_ID=...",
        "```",
        "Then `syncee-scanner scan full --limit 50` validates the schema before scanning.",
        "",
    ]
    return "\n".join(lines) + "\n"
