from database import db


def test_database_connection_supports_supabase_transaction_pooler(monkeypatch):
    connection = object()
    options = {}

    def fake_connect(dsn, **kwargs):
        options["dsn"] = dsn
        options.update(kwargs)
        return connection

    monkeypatch.setattr(db.psycopg, "connect", fake_connect)

    result = db.connect_database()

    assert result is connection
    assert options["connect_timeout"] == 10
    assert options["sslmode"] == "require"
    assert options["prepare_threshold"] is None
    assert options["row_factory"] is db.dict_row
