import importlib
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.orm import Session

from db.database import db
from db.db_models import Base

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent.parent
MIGRATIONS_ROOT = ROOT_DIR / "migrations"


def _get_env_name() -> str:
    env_name = os.getenv("APP_ENV", "dev").strip().lower()
    return env_name if env_name in {"dev", "prd"} else "dev"


def _iter_migration_modules(base_dir: Path):
    for file_path in sorted(base_dir.glob("*.py")):
        if file_path.name.startswith("__"):
            continue
        yield file_path


def _ensure_migration_log_table(session: Session) -> None:
    session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                migration_name VARCHAR(255) PRIMARY KEY,
                applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    session.commit()


def _migration_is_applied(session: Session, migration_name: str) -> bool:
    result = session.execute(
        text("SELECT 1 FROM schema_migrations WHERE migration_name = :migration_name LIMIT 1"),
        {"migration_name": migration_name},
    ).fetchone()
    return result is not None


def _mark_migration_applied(session: Session, migration_name: str) -> None:
    session.execute(
        text(
            "INSERT INTO schema_migrations (migration_name, applied_at) VALUES (:migration_name, CURRENT_TIMESTAMP)"
        ),
        {"migration_name": migration_name},
    )
    session.commit()


def run_migrations() -> None:
    env_name = _get_env_name()
    db.connect()

    with Session(db.get_engine()) as session:
        _ensure_migration_log_table(session)

        # base table creation must happen before env-specific setup
        base_dir = MIGRATIONS_ROOT / "base"
        for migration_file in _iter_migration_modules(base_dir):
            migration_name = f"migrations.base.{migration_file.stem}"
            if _migration_is_applied(session, migration_name):
                print(f"[migration] skipped: {migration_name} (already applied)")
                continue
            print(f"[migration] applying: {migration_name}")
            module = importlib.import_module(migration_name)
            module.run(session)
            _mark_migration_applied(session, migration_name)
            print(f"[migration] applied: {migration_name}")

        env_dir = MIGRATIONS_ROOT / env_name
        for migration_file in _iter_migration_modules(env_dir):
            migration_name = f"migrations.{env_name}.{migration_file.stem}"
            if _migration_is_applied(session, migration_name):
                print(f"[migration] skipped: {migration_name} (already applied)")
                continue
            print(f"[migration] applying: {migration_name}")
            module = importlib.import_module(migration_name)
            module.run(session)
            _mark_migration_applied(session, migration_name)
            print(f"[migration] applied: {migration_name}")

    print(f"[migration] completed for environment: {env_name}")


if __name__ == "__main__":
    run_migrations()
