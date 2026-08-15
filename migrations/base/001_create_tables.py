from sqlalchemy.orm import Session

from db.db_models import Base


def run(session: Session) -> None:
    Base.metadata.create_all(bind=session.bind)
