"""Guarded Alembic stamping for analytics-service."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Dict, List, Set

import pymysql


VERSION_TABLE = "alembic_version_analytics"

REQUIRED_SIGNATURE: Dict[str, Set[str]] = {
    "analytics_jobs": {
        "id",
        "job_id",
        "device_id",
        "analysis_type",
        "model_name",
        "date_range_start",
        "date_range_end",
        "status",
        "results",
        "created_at",
        "updated_at",
    }
}


def _connect():
    host = os.getenv("MYSQL_HOST", "mysql")
    port = int(os.getenv("MYSQL_PORT", "3306"))
    user = os.getenv("MYSQL_USER", "energy")
    password = os.getenv("MYSQL_PASSWORD", "energy")
    database = os.getenv("MYSQL_DATABASE", "ai_factoryops")
    return pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
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

    print("[migration-guard] schema matches signature; stamping alembic head")
    proc = subprocess.run(["alembic", "stamp", "head"], check=False)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
