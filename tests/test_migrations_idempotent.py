from types import SimpleNamespace

from scripts import run_migrations


class FakeSession:
    def __init__(self):
        self.applied = {"migrations.base.001_create_tables"}
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params))

        if "CREATE TABLE IF NOT EXISTS schema_migrations" in sql:
            return SimpleNamespace(fetchone=lambda: None)

        if "SELECT 1 FROM schema_migrations WHERE migration_name" in sql:
            name = params["migration_name"] if params else None
            return SimpleNamespace(fetchone=lambda: (1,) if name in self.applied else None)

        if "INSERT INTO schema_migrations" in sql:
            self.applied.add(params["migration_name"])
            return None

        return SimpleNamespace(fetchone=lambda: None)

    def commit(self):
        pass


def test_run_migrations_skips_already_applied(monkeypatch):
    executed = []

    def fake_import_module(name):
        def noop(session):
            executed.append(name)

        return SimpleNamespace(run=noop)

    def fake_iter_migration_modules(base_dir):
        if base_dir.name == "base":
            return [SimpleNamespace(stem="001_create_tables")]
        if base_dir.name == "dev":
            return [SimpleNamespace(stem="001_init_default_filter_data")]
        return []

    class FakeDb:
        def connect(self):
            return None

        def get_engine(self):
            return object()

    monkeypatch.setattr(run_migrations, "db", FakeDb())
    monkeypatch.setattr(run_migrations, "Session", lambda engine: FakeSession())
    monkeypatch.setattr(run_migrations, "_iter_migration_modules", fake_iter_migration_modules)
    monkeypatch.setattr(run_migrations.importlib, "import_module", fake_import_module)

    run_migrations.run_migrations()

    assert "migrations.base.001_create_tables" not in executed
    assert "migrations.dev.001_init_default_filter_data" in executed
