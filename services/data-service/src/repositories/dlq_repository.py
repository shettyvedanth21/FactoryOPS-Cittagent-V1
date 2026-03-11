"""Dead Letter Queue repository with pluggable durable backends."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Protocol

import pymysql

from src.config import settings
from src.models import DLQEntry
from src.utils import get_logger

logger = get_logger(__name__)


class DLQBackend(Protocol):
    """Protocol for DLQ backend implementations."""

    def send(self, entry: DLQEntry) -> bool:
        """Send entry to DLQ."""
        ...

    def get_operational_stats(self) -> Dict[str, Any]:
        """Return backend-specific DLQ stats."""
        ...

    def close(self) -> None:
        """Close backend resources."""
        ...


class FileBasedDLQBackend:
    """File-based DLQ backend with rotation support."""

    def __init__(
        self,
        directory: str = "./dlq",
        max_file_size: int = 10 * 1024 * 1024,
        max_files: int = 10,
    ):
        self.directory = Path(directory)
        self.max_file_size = max_file_size
        self.max_files = max_files
        self._lock = threading.Lock()
        self._current_file: Optional[Path] = None
        self._file_handle: Optional[Any] = None
        self._entries_written = 0
        self.directory.mkdir(parents=True, exist_ok=True)
        self._open_current_file()
        logger.info(
            "FileBasedDLQBackend initialized",
            directory=str(self.directory),
            max_file_size=self.max_file_size,
            max_files=self.max_files,
        )

    def _open_current_file(self) -> None:
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        self._current_file = self.directory / f"dlq_{timestamp}.jsonl"
        self._file_handle = open(self._current_file, "a", encoding="utf-8")

    def _rotate_if_needed(self) -> None:
        if self._current_file is None:
            return
        if self._current_file.stat().st_size < self.max_file_size:
            return
        if self._file_handle:
            self._file_handle.close()
        self._open_current_file()
        self._cleanup_old_files()

    def _cleanup_old_files(self) -> None:
        dlq_files = sorted(
            self.directory.glob("dlq_*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if len(dlq_files) <= self.max_files:
            return
        for file_path in dlq_files[self.max_files :]:
            try:
                file_path.unlink()
            except OSError as exc:
                logger.error("Failed to remove old DLQ file", file=str(file_path), error=str(exc))

    def send(self, entry: DLQEntry) -> bool:
        with self._lock:
            try:
                self._rotate_if_needed()
                if self._file_handle is None:
                    self._open_current_file()
                payload = json.dumps(entry.model_dump(), default=str)
                self._file_handle.write(payload + "\n")
                self._file_handle.flush()
                self._entries_written += 1
                return True
            except Exception as exc:
                logger.error("Failed to write DLQ entry", error=str(exc), error_type=entry.error_type)
                return False

    def get_operational_stats(self) -> Dict[str, Any]:
        return {
            "backend": "file",
            "entries_written": self._entries_written,
            "active_file": str(self._current_file) if self._current_file else None,
        }

    def close(self) -> None:
        with self._lock:
            if self._file_handle:
                try:
                    self._file_handle.close()
                except Exception as exc:
                    logger.error("Error closing DLQ file handle", error=str(exc))


class MySQLDLQBackend:
    """MySQL-backed DLQ backend for durable storage."""

    TABLE_NAME = "dlq_messages"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries_written = 0
        self._write_failures = 0
        self._ensure_schema()
        logger.info("MySQLDLQBackend initialized", table=self.TABLE_NAME)

    def _connect(self):
        return pymysql.connect(
            host=settings.mysql_host,
            port=settings.mysql_port,
            user=settings.mysql_user,
            password=settings.mysql_password,
            database=settings.mysql_database,
            autocommit=True,
            cursorclass=pymysql.cursors.DictCursor,
        )

    def _ensure_schema(self) -> None:
        create_table_sql = f"""
        CREATE TABLE IF NOT EXISTS {self.TABLE_NAME} (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            timestamp DATETIME(6) NOT NULL,
            error_type VARCHAR(128) NOT NULL,
            error_message TEXT NOT NULL,
            retry_count INT NOT NULL DEFAULT 0,
            original_payload JSON NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'pending',
            created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
        index_statements = [
            f"CREATE INDEX idx_dlq_messages_created_at ON {self.TABLE_NAME}(created_at)",
            f"CREATE INDEX idx_dlq_messages_error_type ON {self.TABLE_NAME}(error_type)",
            f"CREATE INDEX idx_dlq_messages_status_created ON {self.TABLE_NAME}(status, created_at)",
        ]
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(create_table_sql)
                for statement in index_statements:
                    try:
                        cur.execute(statement)
                    except Exception:
                        # Index already exists.
                        pass

    def send(self, entry: DLQEntry) -> bool:
        payload = entry.model_dump()
        payload_json = json.dumps(payload["original_payload"], default=str)
        timestamp = entry.timestamp.astimezone(timezone.utc).replace(tzinfo=None)
        with self._lock:
            try:
                with self._connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            f"""
                            INSERT INTO {self.TABLE_NAME}
                              (timestamp, error_type, error_message, retry_count, original_payload, status)
                            VALUES
                              (%s, %s, %s, %s, %s, 'pending')
                            """,
                            (
                                timestamp,
                                entry.error_type,
                                entry.error_message,
                                entry.retry_count,
                                payload_json,
                            ),
                        )
                self._entries_written += 1
                return True
            except Exception as exc:
                self._write_failures += 1
                logger.error("Failed to persist DLQ entry in MySQL", error=str(exc), error_type=entry.error_type)
                return False

    def get_operational_stats(self) -> Dict[str, Any]:
        stats = {
            "backend": "mysql",
            "entries_written": self._entries_written,
            "write_failures": self._write_failures,
            "backlog_count": None,
            "oldest_pending_created_at": None,
        }
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        SELECT COUNT(*) AS backlog_count, MIN(created_at) AS oldest_pending_created_at
                        FROM {self.TABLE_NAME}
                        WHERE status='pending'
                        """
                    )
                    row = cur.fetchone() or {}
                    stats["backlog_count"] = row.get("backlog_count", 0)
                    stats["oldest_pending_created_at"] = row.get("oldest_pending_created_at")
        except Exception as exc:
            logger.warning("Failed to query MySQL DLQ operational stats", error=str(exc))
        return stats

    def close(self) -> None:
        # Connections are short-lived per operation.
        return None


class DLQRepository:
    """DLQ repository with pluggable backend selection."""

    def __init__(self, backend: Optional[DLQBackend] = None):
        if backend is not None:
            self.backend = backend
        else:
            if settings.dlq_backend == "mysql":
                self.backend = MySQLDLQBackend()
            else:
                self.backend = FileBasedDLQBackend(
                    directory=settings.dlq_directory,
                    max_file_size=settings.dlq_max_file_size,
                    max_files=settings.dlq_max_files,
                )
        logger.info("DLQRepository initialized", backend=type(self.backend).__name__)

    def send(
        self,
        original_payload: Dict[str, Any],
        error_type: str,
        error_message: str,
        retry_count: int = 0,
    ) -> bool:
        entry = DLQEntry(
            original_payload=original_payload,
            error_type=error_type,
            error_message=error_message,
            retry_count=retry_count,
        )
        success = self.backend.send(entry)
        if success:
            logger.info(
                "Message sent to DLQ",
                error_type=error_type,
                device_id=original_payload.get("device_id", "unknown"),
            )
        else:
            logger.error(
                "Failed to send message to DLQ",
                error_type=error_type,
                device_id=original_payload.get("device_id", "unknown"),
            )
        return success

    def get_operational_stats(self) -> Dict[str, Any]:
        return self.backend.get_operational_stats()

    def close(self) -> None:
        self.backend.close()
