"""Guarded Alembic stamping for pre-existing schemas.

Rules:
- If version table has a revision, do nothing.
- If DB has no baseline tables, do nothing (fresh DB).
- If DB fully matches required schema signature, stamp head.
- If DB is partially drifted, fail fast with details.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Dict, List, Set
from urllib.parse import urlparse

import pymysql


VERSION_TABLE = "alembic_version_device"
STAMP_REVISION = "add_idle_running_config_and_log"

REQUIRED_SIGNATURE: Dict[str, Set[str]] = {
    "devices": {
        "device_id",
        "device_name",
        "device_type",
        "legacy_status",
        "last_seen_timestamp",
        "deleted_at",
        "phase_type",
        "data_source_type",
        "idle_current_threshold",
    },
    "device_shifts": {"id", "device_id", "shift_name", "shift_start", "shift_end"},
    "parameter_health_config": {"id", "device_id", "parameter_name", "weight"},
    "device_properties": {"id", "device_id", "property_name", "last_seen_at"},
    "device_performance_trends": {"id", "device_id", "bucket_start_utc"},
    "idle_running_log": {"id", "device_id", "period_start"},
}


def _parse_db_url() -> dict:
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is required")

    normalized = db_url
    for prefix in ("mysql+aiomysql://", "mysql+pymysql://", "mysql://"):
        if db_url.startswith(prefix):
            normalized = "mysql://" + db_url[len(prefix) :]
            break
    parsed = urlparse(normalized)
    if parsed.scheme != "mysql":
        raise RuntimeError(f"Unsupported DATABASE_URL scheme: {parsed.scheme}")

    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 3306,
        "user": parsed.username or "",
        "password": parsed.password or "",
        "database": (parsed.path or "/").lstrip("/"),
    }


def _connect():
    cfg = _parse_db_url()
    return pymysql.connect(
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )


def _table_exists(cur, table_name: str) -> bool:
    cur.execute("SHOW TABLES LIKE %s", (table_name,))
    return cur.fetchone() is not None


def _table_columns(cur, table_name: str) -> Set[str]:
    cur.execute(f"SHOW COLUMNS FROM `{table_name}`")
    rows = cur.fetchall()
    return {row["Field"] for row in rows}


def _has_existing_revision(cur) -> bool:
    if not _table_exists(cur, VERSION_TABLE):
        return False
    cur.execute(f"SELECT version_num FROM {VERSION_TABLE} LIMIT 1")
    row = cur.fetchone()
    return bool(row and row.get("version_num"))


def _signature_check(cur) -> tuple[bool, List[str], List[str]]:
    missing_tables: List[str] = []
    drift_details: List[str] = []

    for table_name, required_cols in REQUIRED_SIGNATURE.items():
        if not _table_exists(cur, table_name):
            missing_tables.append(table_name)
            continue
        present_cols = _table_columns(cur, table_name)
        missing_cols = sorted(required_cols - present_cols)
        if missing_cols:
            drift_details.append(f"{table_name}: missing columns {missing_cols}")

    return len(missing_tables) == 0, missing_tables, drift_details


def main() -> int:
    try:
        conn = _connect()
    except Exception as exc:
        print(f"[migration-guard] DB connection failed: {exc}", file=sys.stderr)
        return 1

    with conn:
        with conn.cursor() as cur:
            if _has_existing_revision(cur):
                print("[migration-guard] existing alembic revision found; skipping stamp")
                return 0

            all_tables_exist, missing_tables, drift_details = _signature_check(cur)
            if not all_tables_exist and len(missing_tables) == len(REQUIRED_SIGNATURE):
                print("[migration-guard] fresh schema detected; skipping stamp")
                return 0

            if drift_details or not all_tables_exist:
                print("[migration-guard] schema drift detected; refusing alembic stamp", file=sys.stderr)
                if missing_tables:
                    print(f"[migration-guard] missing tables: {sorted(missing_tables)}", file=sys.stderr)
                for detail in drift_details:
                    print(f"[migration-guard] {detail}", file=sys.stderr)
                return 1

    # Stamp to legacy stable revision so new migrations still run via upgrade head.
    print(f"[migration-guard] schema matches signature; stamping alembic revision '{STAMP_REVISION}'")
    proc = subprocess.run(["alembic", "stamp", STAMP_REVISION], check=False)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
