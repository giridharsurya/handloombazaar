from sqlalchemy.orm import Session

from migrations.shared_defaults import ensure_admin_user, ensure_attribute_defaults


def run(session: Session) -> None:
    ensure_admin_user(session)
    ensure_attribute_defaults(session)
