from sqlalchemy import text
from sqlalchemy.orm import Session


def run(session: Session) -> None:
    session.execute(
        text(
            """
            ALTER TABLE shops
            ADD COLUMN IF NOT EXISTS shop_slug VARCHAR(100)
            """
        )
    )

    session.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_shops_shop_slug
            ON shops (shop_slug)
            WHERE shop_slug IS NOT NULL
            """
        )
    )

    session.commit()
