from sqlalchemy import text
from sqlalchemy.orm import Session


def run(session: Session) -> None:
    session.execute(
        text(
            """
            ALTER TABLE shops
            ADD COLUMN IF NOT EXISTS description VARCHAR(1000)
            """
        )
    )
    session.commit()
