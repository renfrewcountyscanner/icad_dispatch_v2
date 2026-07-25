#!/usr/bin/env python3
"""Export and import iCAD Dispatch operational configuration.

This intentionally does not copy call history, audio, runtime state, sessions,
remember-me tokens, or user passwords.  The export contains integration
credentials by default because those are part of an operational configuration;
use --redact-secrets when creating a portable template for sharing.

Run inside the app container so the PostgreSQL environment is already present:

    docker compose -f docker-compose.production.yml exec -T icad_dispatch \
        python scripts/config_migrate.py export --output /app/var/config.json
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor


FORMAT_NAME = "icad-dispatch-config"
FORMAT_VERSION = 1

# Ordered by dependency.  Everything listed here is configuration or a
# configuration lookup table; call/runtime/auth data is intentionally absent.
CONFIG_TABLES = (
    "radio_systems",
    "radio_system_upload_settings",
    "radio_system_tone_settings",
    "radio_system_email_settings",
    "radio_system_pushover_settings",
    "radio_system_telegram_settings",
    "radio_system_discord_settings",
    "radio_system_discord_embed_fields",
    "radio_system_make_settings",
    "radio_system_make_payload_fields",
    "radio_system_emails",
    "radio_system_transcribe_settings",
    "radio_system_n8n_settings",
    "radio_system_ntfy_settings",
    "radio_system_storage_settings",
    "radio_system_incident_classification_settings",
    "radio_system_address_extraction_settings",
    "geocoding_regions",
    "geocoding_cities",
    "geocoding_roads",
    "geocoding_address_aliases",
    "alert_triggers",
    "alert_trigger_pushover_settings",
    "alert_trigger_two_tone_sets",
    "alert_trigger_long_tone_sets",
    "alert_trigger_hi_low_sets",
    "alert_trigger_pulsed_sets",
    "alert_trigger_dtmf_sequences",
    "talkgroup_system_routes",
)

ONE_TO_ONE_TABLES = (
    "radio_system_upload_settings",
    "radio_system_tone_settings",
    "radio_system_email_settings",
    "radio_system_pushover_settings",
    "radio_system_telegram_settings",
    "radio_system_discord_settings",
    "radio_system_make_settings",
    "radio_system_transcribe_settings",
    "radio_system_n8n_settings",
    "radio_system_ntfy_settings",
    "radio_system_storage_settings",
    "radio_system_incident_classification_settings",
    "radio_system_address_extraction_settings",
)

PRIMARY_KEYS = {
    "radio_systems": "radio_system_id",
    "radio_system_upload_settings": "radio_system_id",
    "radio_system_tone_settings": "tone_settings_id",
    "radio_system_email_settings": "email_setting_id",
    "radio_system_pushover_settings": "pushover_setting_id",
    "radio_system_telegram_settings": "telegram_setting_id",
    "radio_system_discord_settings": "discord_setting_id",
    "radio_system_discord_embed_fields": "embed_field_id",
    "radio_system_make_settings": "make_setting_id",
    "radio_system_make_payload_fields": "payload_field_id",
    "radio_system_emails": "email_id",
    "radio_system_transcribe_settings": "transcribe_setting_id",
    "radio_system_n8n_settings": "n8n_setting_id",
    "radio_system_ntfy_settings": "ntfy_setting_id",
    "radio_system_storage_settings": "radio_system_id",
    "radio_system_incident_classification_settings": "incident_classification_setting_id",
    "radio_system_address_extraction_settings": "address_extraction_setting_id",
    "geocoding_regions": "region_id",
    "geocoding_cities": "city_id",
    "geocoding_roads": "road_id",
    "geocoding_address_aliases": "address_alias_id",
    "alert_triggers": "alert_trigger_id",
    "alert_trigger_pushover_settings": "alert_trigger_pushover_setting_id",
    "alert_trigger_two_tone_sets": "two_tone_set_id",
    "alert_trigger_long_tone_sets": "long_tone_set_id",
    "alert_trigger_hi_low_sets": "hi_low_set_id",
    "alert_trigger_pulsed_sets": "pulsed_set_id",
    "alert_trigger_dtmf_sequences": "dtmf_set_id",
    "talkgroup_system_routes": "talkgroup",
}

SYSTEM_CHILD_TABLES = {
    table: "radio_system_id"
    for table in ONE_TO_ONE_TABLES
    if table != "radio_system_upload_settings"
}
SYSTEM_CHILD_TABLES["radio_system_upload_settings"] = "radio_system_id"

SECRET_COLUMNS = {
    "radio_systems": {"api_key"},
    "radio_system_email_settings": {"smtp_password"},
    "radio_system_pushover_settings": {"pushover_group_token", "pushover_app_token"},
    "radio_system_telegram_settings": {"telegram_bot_token"},
    "radio_system_make_settings": {"make_api_key"},
    "radio_system_transcribe_settings": {"transcribe_api_key"},
    "radio_system_n8n_settings": {"jwt_passphrase"},
    "radio_system_ntfy_settings": {"ntfy_token"},
    "radio_system_storage_settings": {
        "sftp_password",
        "sftp_ssh_key",
        "s3_access_key_id",
        "s3_secret_access_key",
    },
    "radio_system_incident_classification_settings": {"openai_api_key"},
    "radio_system_address_extraction_settings": {"openai_api_key", "google_maps_api_key"},
    "alert_trigger_pushover_settings": {"pushover_group_token", "pushover_app_token"},
}


def db_connect():
    return psycopg2.connect(
        host=os.getenv("PG_HOST", "localhost"),
        port=int(os.getenv("PG_PORT", "5432")),
        dbname=os.getenv("PG_DATABASE", "icad_dispatch"),
        user=os.getenv("PG_USER", "icad"),
        password=os.getenv("PG_PASSWORD", ""),
    )


def json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value) if value.as_tuple().exponent else int(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def identifier(name: str) -> sql.Identifier:
    if name not in CONFIG_TABLES:
        raise ValueError(f"Unsupported configuration table: {name}")
    return sql.Identifier(name)


def redact_row(table: str, row: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(row)
    for column in SECRET_COLUMNS.get(table, set()):
        if column in redacted:
            redacted[column] = None
    return redacted


def export_config(output: Path, redact_secrets: bool) -> int:
    with db_connect() as connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
        tables: dict[str, list[dict[str, Any]]] = {}
        for table in CONFIG_TABLES:
            cursor.execute(sql.SQL("SELECT * FROM {} ").format(identifier(table)))
            rows = []
            for row in cursor.fetchall():
                converted = {key: json_value(value) for key, value in dict(row).items()}
                rows.append(redact_row(table, converted) if redact_secrets else converted)
            tables[table] = rows

    payload = {
        "format": FORMAT_NAME,
        "format_version": FORMAT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_database": os.getenv("PG_DATABASE", "icad_dispatch"),
        "includes_secrets": not redact_secrets,
        "redacted_fields": {
            table: sorted(columns)
            for table, columns in SECRET_COLUMNS.items()
            if redact_secrets and columns
        },
        "tables": tables,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(output, flags, stat.S_IRUSR | stat.S_IWUSR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=False)
            stream.write("\n")
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise

    counts = ", ".join(f"{table}={len(rows)}" for table, rows in tables.items() if rows)
    print(f"Exported {sum(map(len, tables.values()))} configuration rows to {output}")
    if counts:
        print(counts)
    if redact_secrets:
        print("Secrets were redacted; configure credentials separately on the target.")
    else:
        print("WARNING: this file contains integration credentials; protect it like a password.")
    return 0


def load_export(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        payload = json.load(stream)
    if payload.get("format") != FORMAT_NAME:
        raise ValueError(f"Unsupported export format: {payload.get('format')!r}")
    if payload.get("format_version") != FORMAT_VERSION:
        raise ValueError(f"Unsupported export version: {payload.get('format_version')!r}")
    if not isinstance(payload.get("tables"), dict):
        raise ValueError("Export is missing its tables object")
    return payload


def rows(payload: dict[str, Any], table: str) -> list[dict[str, Any]]:
    result = payload["tables"].get(table, [])
    if not isinstance(result, list) or not all(isinstance(row, dict) for row in result):
        raise ValueError(f"Invalid rows for table {table}")
    return result


def table_columns(cursor, table: str) -> set[str]:
    cursor.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (table,),
    )
    return {row["column_name"] for row in cursor.fetchall()}


def clean_columns(table: str, row: dict[str, Any], columns: set[str], *, omit_secrets: bool) -> dict[str, Any]:
    unknown = set(row) - columns
    if unknown:
        raise ValueError(f"Export has columns not present in target {table}: {sorted(unknown)}")
    result = {key: value for key, value in row.items() if key in columns}
    if omit_secrets:
        for column in SECRET_COLUMNS.get(table, set()):
            result.pop(column, None)
    return result


def insert_one(cursor, table: str, data: dict[str, Any], conflict_columns: Iterable[str] | None = None) -> dict[str, Any]:
    if not data:
        raise ValueError(f"No columns to import for {table}")
    columns = list(data)
    values = [data[column] for column in columns]
    assignments = [column for column in columns if not conflict_columns or column not in conflict_columns]
    query = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        identifier(table),
        sql.SQL(", ").join(sql.Identifier(column) for column in columns),
        sql.SQL(", ").join(sql.Placeholder() for _ in columns),
    )
    if conflict_columns:
        query += sql.SQL(" ON CONFLICT ({}) DO UPDATE SET ").format(
            sql.SQL(", ").join(sql.Identifier(column) for column in conflict_columns)
        )
        if assignments:
            query += sql.SQL(", ").join(
                sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(column), sql.Identifier(column))
                for column in assignments
            )
        else:
            query += sql.SQL("NOTHING")
    query += sql.SQL(" RETURNING *")
    cursor.execute(query, values)
    result = cursor.fetchone()
    return dict(result) if result else {}


def import_config(path: Path, dry_run: bool) -> int:
    payload = load_export(path)
    omit_secrets = not bool(payload.get("includes_secrets", False))
    stats = {"systems": 0, "triggers": 0, "settings": 0, "children": 0, "routes": 0}

    with db_connect() as connection:
        try:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                available = {table: table_columns(cursor, table) for table in CONFIG_TABLES}
                missing = [table for table in CONFIG_TABLES if not available[table]]
                if missing:
                    raise ValueError(f"Target database is missing migrations for: {', '.join(missing)}")

                system_map: dict[int, int] = {}
                for source in rows(payload, "radio_systems"):
                    source_id = int(source["radio_system_id"])
                    data = clean_columns("radio_systems", source, available["radio_systems"], omit_secrets=omit_secrets)
                    data.pop("radio_system_id", None)
                    cursor.execute(
                        "SELECT radio_system_id FROM radio_systems WHERE system_decimal = %s ORDER BY radio_system_id",
                        (data["system_decimal"],),
                    )
                    existing = cursor.fetchone()
                    if existing:
                        target_id = int(existing["radio_system_id"])
                        assignments = [column for column in data if column != "system_decimal"]
                        update = sql.SQL("UPDATE radio_systems SET {} WHERE radio_system_id = %s").format(
                            sql.SQL(", ").join(
                                sql.SQL("{} = %s").format(sql.Identifier(column)) for column in assignments
                            )
                        )
                        cursor.execute(update, [data[column] for column in assignments] + [target_id])
                    else:
                        target = insert_one(cursor, "radio_systems", data)
                        target_id = int(target["radio_system_id"])
                    system_map[source_id] = target_id
                    stats["systems"] += 1

                setting_map: dict[tuple[str, int], int] = {}
                for table in ONE_TO_ONE_TABLES:
                    pk = PRIMARY_KEYS[table]
                    # Some older databases were created without the intended
                    # UNIQUE(radio_system_id) constraint. Replace the target
                    # row explicitly instead of relying on ON CONFLICT, which
                    # keeps imports idempotent on both old and new schemas.
                    for target_system_id in set(system_map.values()):
                        cursor.execute(
                            sql.SQL("DELETE FROM {} WHERE radio_system_id = %s").format(identifier(table)),
                            (target_system_id,),
                        )
                    for source in rows(payload, table):
                        source_system_id = int(source["radio_system_id"])
                        if source_system_id not in system_map:
                            raise ValueError(f"{table} references unknown radio_system_id {source_system_id}")
                        data = clean_columns(table, source, available[table], omit_secrets=omit_secrets)
                        data.pop(pk, None)
                        data["radio_system_id"] = system_map[source_system_id]
                        target = insert_one(cursor, table, data)
                        setting_map[(table, int(source[pk]))] = int(target[pk])
                        stats["settings"] += 1

                trigger_map: dict[int, int] = {}
                used_trigger_ids: set[int] = set()
                for source in rows(payload, "alert_triggers"):
                    source_system_id = int(source["radio_system_id"])
                    target_system_id = system_map[source_system_id]
                    data = clean_columns("alert_triggers", source, available["alert_triggers"], omit_secrets=omit_secrets)
                    source_trigger_id = int(data.pop("alert_trigger_id"))
                    data["radio_system_id"] = target_system_id

                    name = data.get("alert_trigger_name")
                    cursor.execute(
                        """
                        SELECT alert_trigger_id FROM alert_triggers
                        WHERE radio_system_id = %s
                          AND alert_trigger_name IS NOT DISTINCT FROM %s
                        ORDER BY alert_trigger_id
                        """,
                        (target_system_id, name),
                    )
                    target_id = next(
                        (int(row["alert_trigger_id"]) for row in cursor.fetchall()
                         if int(row["alert_trigger_id"]) not in used_trigger_ids),
                        None,
                    )
                    if target_id is None:
                        inserted = insert_one(cursor, "alert_triggers", data)
                        target_id = int(inserted["alert_trigger_id"])
                    else:
                        assignments = [column for column in data if column != "radio_system_id"]
                        update = sql.SQL("UPDATE alert_triggers SET {} WHERE alert_trigger_id = %s").format(
                            sql.SQL(", ").join(
                                sql.SQL("{} = %s").format(sql.Identifier(column)) for column in assignments
                            )
                        )
                        cursor.execute(update, [data[column] for column in assignments] + [target_id])
                    trigger_map[source_trigger_id] = target_id
                    used_trigger_ids.add(target_id)
                    stats["triggers"] += 1

                # Children use the source surrogate IDs in the export, so map
                # those IDs to the target rows before inserting them.
                for table, parent_table, parent_column in (
                    ("radio_system_discord_embed_fields", "radio_system_discord_settings", "discord_setting_id"),
                    ("radio_system_make_payload_fields", "radio_system_make_settings", "make_setting_id"),
                ):
                    target_parents = {
                        setting_map[(parent_table, int(source[parent_column]))]
                        for source in rows(payload, table)
                    }
                    for target_parent in target_parents:
                        cursor.execute(
                            sql.SQL("DELETE FROM {} WHERE {} = %s").format(
                                identifier(table), sql.Identifier(parent_column)
                            ),
                            (target_parent,),
                        )
                    for source in rows(payload, table):
                        source_parent = int(source[parent_column])
                        target_parent = setting_map[(parent_table, source_parent)]
                        data = clean_columns(table, source, available[table], omit_secrets=omit_secrets)
                        data.pop(PRIMARY_KEYS[table], None)
                        data[parent_column] = target_parent
                        insert_one(cursor, table, data)
                        stats["children"] += 1

                # Email recipient lists are replaced for each imported system,
                # because this table has no natural unique constraint.
                target_systems = set(system_map.values())
                for target_system_id in target_systems:
                    cursor.execute("DELETE FROM radio_system_emails WHERE radio_system_id = %s", (target_system_id,))
                for source in rows(payload, "radio_system_emails"):
                    data = clean_columns("radio_system_emails", source, available["radio_system_emails"], omit_secrets=omit_secrets)
                    data.pop(PRIMARY_KEYS["radio_system_emails"], None)
                    data["radio_system_id"] = system_map[int(source["radio_system_id"])]
                    insert_one(cursor, "radio_system_emails", data)
                    stats["children"] += 1

                for table in ("geocoding_regions", "geocoding_cities", "geocoding_roads", "geocoding_address_aliases"):
                    target_settings = set(setting_map[("radio_system_address_extraction_settings", int(source["address_extraction_setting_id"]))]
                                          for source in rows(payload, table))
                    for setting_id in target_settings:
                        cursor.execute(
                            sql.SQL("DELETE FROM {} WHERE address_extraction_setting_id = %s").format(identifier(table)),
                            (setting_id,),
                        )
                    for source in rows(payload, table):
                        data = clean_columns(table, source, available[table], omit_secrets=omit_secrets)
                        data.pop(PRIMARY_KEYS[table], None)
                        data["address_extraction_setting_id"] = setting_map[
                            ("radio_system_address_extraction_settings", int(source["address_extraction_setting_id"]))
                        ]
                        insert_one(cursor, table, data)
                        stats["children"] += 1

                trigger_child_tables = (
                    "alert_trigger_pushover_settings",
                    "alert_trigger_two_tone_sets",
                    "alert_trigger_long_tone_sets",
                    "alert_trigger_hi_low_sets",
                    "alert_trigger_pulsed_sets",
                    "alert_trigger_dtmf_sequences",
                )
                for table in trigger_child_tables:
                    parent_column = "alert_trigger_id"
                    target_trigger_ids = set(trigger_map.values())
                    for target_trigger_id in target_trigger_ids:
                        cursor.execute(
                            sql.SQL("DELETE FROM {} WHERE alert_trigger_id = %s").format(identifier(table)),
                            (target_trigger_id,),
                        )
                    for source in rows(payload, table):
                        data = clean_columns(table, source, available[table], omit_secrets=omit_secrets)
                        data.pop(PRIMARY_KEYS[table], None)
                        data[parent_column] = trigger_map[int(source[parent_column])]
                        insert_one(cursor, table, data)
                        stats["children"] += 1

                for source in rows(payload, "talkgroup_system_routes"):
                    data = clean_columns("talkgroup_system_routes", source, available["talkgroup_system_routes"], omit_secrets=omit_secrets)
                    data["target_radio_system_id"] = system_map[int(source["target_radio_system_id"])]
                    insert_one(cursor, "talkgroup_system_routes", data, ["talkgroup"])
                    stats["routes"] += 1

                if dry_run:
                    connection.rollback()
                    print("Dry run completed; no changes were committed.")
                else:
                    connection.commit()
        except Exception:
            connection.rollback()
            raise

    print(json.dumps(stats, indent=2, sort_keys=True))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)

    export = sub.add_parser("export", help="export operational configuration to JSON")
    export.add_argument("--output", type=Path, required=True, help="destination JSON file")
    export.add_argument("--redact-secrets", action="store_true", help="omit integration credentials")

    import_cmd = sub.add_parser("import", help="import a JSON configuration export")
    import_cmd.add_argument("--input", type=Path, required=True, help="source JSON file")
    import_cmd.add_argument("--dry-run", action="store_true", help="validate and roll back without committing")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "export":
            return export_config(args.output, args.redact_secrets)
        return import_config(args.input, args.dry_run)
    except (OSError, ValueError, psycopg2.Error) as exc:
        print(f"config migration failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
