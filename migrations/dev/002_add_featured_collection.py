from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.db_models import collection


def ensure_featured_collection(session: Session) -> None:
    existing = session.execute(
        select(collection).where(collection.name.ilike("featured"))
    ).scalar_one_or_none()

    if existing is not None:
        return

    now = datetime.utcnow()
    session.add(
        collection(
            name="Featured",
            description="Featured collection",
            created_at=now,
            updated_at=now,
            is_active=True,
            display_on_homepage=True,
        )
    )
    session.flush()


def run(session: Session) -> None:
    ensure_featured_collection(session)
