import asyncio
from dataclasses import dataclass

from sqlalchemy import text

from src.config import settings
from src.database import get_db_session


BLOCKED_KEYWORDS = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "TRUNCATE",
    "CREATE",
    "GRANT",
    "REVOKE",
    "EXEC",
    "EXECUTE",
    "CALL",
    "LOAD",
    "OUTFILE",
    "DUMPFILE",
}


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list]
    row_count: int
    error: str | None = None
    reason: str | None = None


class QueryEngine:
    @staticmethod
    def validate_sql(sql: str) -> tuple[bool, str]:
        raw = (sql or "").strip()
        upper = raw.upper()

        if not upper:
            return False, "Empty query"
        if not upper.startswith("SELECT"):
            return False, "Only SELECT queries are allowed"
        if len(raw) > 4000:
            return False, "Query too long"

        semicolon_count = raw.count(";")
        if semicolon_count > 1:
            return False, "Multiple statements are not allowed"
        if semicolon_count == 1 and not raw.endswith(";"):
            return False, "Multiple statements are not allowed"

        for keyword in BLOCKED_KEYWORDS:
            if keyword in upper:
                return False, f"Blocked keyword: {keyword}"

        return True, "ok"

    async def execute_query(self, sql: str) -> QueryResult:
        valid, reason = self.validate_sql(sql)
        if not valid:
            return QueryResult(columns=[], rows=[], row_count=0, error="QUERY_BLOCKED", reason=reason)

        cleaned_sql = sql.rstrip(";")

        async def _run() -> QueryResult:
            async with get_db_session() as db:
                result = await db.execute(text(cleaned_sql))
                rows = result.fetchmany(settings.max_query_rows)
                columns = list(result.keys())
                return QueryResult(
                    columns=columns,
                    rows=[list(r) for r in rows],
                    row_count=len(rows),
                )

        try:
            return await asyncio.wait_for(_run(), timeout=settings.query_timeout_sec)
        except asyncio.TimeoutError:
            return QueryResult(columns=[], rows=[], row_count=0, error="QUERY_TIMEOUT", reason="Query timed out")
        except Exception as exc:
            return QueryResult(columns=[], rows=[], row_count=0, error="QUERY_FAILED", reason=str(exc))
