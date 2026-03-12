from src.db.query_engine import QueryEngine


def test_validate_sql_accepts_select():
    ok, _ = QueryEngine.validate_sql("SELECT * FROM devices LIMIT 1")
    assert ok


def test_validate_sql_blocks_update():
    ok, reason = QueryEngine.validate_sql("UPDATE devices SET device_name='x'")
    assert not ok
    assert "Only SELECT" in reason


def test_validate_sql_blocks_multi_statement():
    ok, reason = QueryEngine.validate_sql("SELECT * FROM devices; SELECT * FROM rules;")
    assert not ok
    assert "Multiple statements" in reason


def test_validate_sql_allows_created_at_identifier():
    ok, reason = QueryEngine.validate_sql("SELECT created_at FROM alerts ORDER BY created_at DESC LIMIT 10")
    assert ok
    assert reason == "ok"


def test_validate_sql_allows_updated_at_identifier():
    ok, reason = QueryEngine.validate_sql("SELECT updated_at FROM rules ORDER BY updated_at DESC LIMIT 10")
    assert ok
    assert reason == "ok"
