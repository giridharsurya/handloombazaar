import json
import os
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.db_models import UserRole, attribute_definition, attribute_option, user
from utils.auth import hash_password


_MIGRATIONS_ROOT = Path(__file__).resolve().parent


def get_default_attribute_definitions_path() -> Path:
    env_name = os.getenv("APP_ENV", "dev").strip().lower()
    if env_name not in {"dev", "prd"}:
        env_name = "dev"

    path = _MIGRATIONS_ROOT / env_name / "default_attribute_definitions.json"
    if not path.exists():
        path = _MIGRATIONS_ROOT / "default_attribute_definitions.json"

    return path


def load_default_attribute_definitions() -> list[dict]:
    path = get_default_attribute_definitions_path()
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    if not isinstance(data, list):
        raise ValueError("Default attribute definitions JSON must be a list.")

    return data


def ensure_admin_user(session: Session, username: str = "admin", email: str = "admin@handloombazaar.local", password: str = "Admin@123!") -> None:
    existing = session.execute(
        select(user).where((user.username == username) | (user.email == email))
    ).scalar_one_or_none()

    if existing is not None:
        if existing.username != username:
            existing.username = username
        if existing.email != email:
            existing.email = email
        if existing.role != UserRole.ADMIN:
            existing.role = UserRole.ADMIN
        if not existing.is_active:
            existing.is_active = True
        existing.updated_at = datetime.utcnow()
        session.flush()
        return

    now = datetime.utcnow()
    session.add(
        user(
            username=username,
            email=email,
            password_hash=hash_password(password),
            role=UserRole.ADMIN,
            created_at=now,
            updated_at=now,
            is_active=True,
        )
    )
    session.flush()


def ensure_attribute_defaults(session: Session) -> None:
    now = datetime.utcnow()
    default_definitions = load_default_attribute_definitions()

    for definition_data in default_definitions:
        definition_name = definition_data["attribute_name"]
        existing_definition = session.execute(
            select(attribute_definition).where(attribute_definition.attribute_name == definition_name)
        ).scalar_one_or_none()

        if existing_definition is None:
            definition_record = attribute_definition(
                attribute_name=definition_name,
                is_filterable=bool(definition_data.get("is_filterable", False)),
                is_required=bool(definition_data.get("is_required", False)),
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            session.add(definition_record)
            session.flush()
            definition_id = definition_record.id
        else:
            definition_id = existing_definition.id
            existing_definition.is_filterable = bool(definition_data.get("is_filterable", existing_definition.is_filterable))
            existing_definition.is_required = bool(definition_data.get("is_required", existing_definition.is_required))
            existing_definition.is_active = True
            existing_definition.updated_at = now
            session.flush()

        for option_value in definition_data.get("options", []):
            existing_option = session.execute(
                select(attribute_option).where(
                    attribute_option.attribute_definition_id == definition_id,
                    attribute_option.option_value == option_value,
                )
            ).scalar_one_or_none()

            if existing_option is None:
                session.add(
                    attribute_option(
                        attribute_definition_id=definition_id,
                        option_value=option_value,
                        is_active=True,
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                existing_option.is_active = True
                existing_option.updated_at = now

    session.flush()
