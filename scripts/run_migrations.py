import importlib
import os
from pathlib import Path

from dotenv import load_dotenv
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


def run_migrations() -> None:
    env_name = _get_env_name()
    db.connect()

    with Session(db.get_engine()) as session:
        # base table creation must happen before env-specific setup
        base_dir = MIGRATIONS_ROOT / "base"
        for migration_file in _iter_migration_modules(base_dir):
            module_name = f"migrations.base.{migration_file.stem}"
            module = importlib.import_module(module_name)
            module.run(session)
            session.commit()

        env_dir = MIGRATIONS_ROOT / env_name
        for migration_file in _iter_migration_modules(env_dir):
            module_name = f"migrations.{env_name}.{migration_file.stem}"
            module = importlib.import_module(module_name)
            module.run(session)
            session.commit()

    print(f"Migrations completed for environment: {env_name}")


if __name__ == "__main__":
    run_migrations()
