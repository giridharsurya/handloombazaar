import re
from typing import Optional

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from db.database import get_session
from db.db_models import shop


def normalize_slug(value: str | None) -> str:
    if value is None:
        return ""
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower())
    slug = slug.strip("-")
    return slug[:100]


def ensure_shop_slug_column(session: Session) -> None:
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


def make_unique_slug(session: Session, base_name: str, current_shop_id: Optional[int] = None) -> str:
    base = normalize_slug(base_name)
    candidate = base or "shop"
    if len(candidate) < 3:
        candidate = f"shop-{current_shop_id or '1'}"

    suffix = 2
    while True:
        if current_shop_id is not None:
            existing = (
                session.query(shop)
                .filter(shop.shop_slug == candidate)
                .filter(shop.id != current_shop_id)
                .first()
            )
        else:
            existing = session.query(shop).filter(shop.shop_slug == candidate).first()

        if existing is None:
            return candidate

        candidate = f"{base}-{suffix}" if base else f"shop-{suffix}"
        suffix += 1


def backfill_missing_shop_slugs() -> int:
    updated = 0
    session = next(get_session())
    try:
        ensure_shop_slug_column(session)

        rows = session.query(shop).order_by(shop.id).all()
        for row in rows:
            current_slug = (row.shop_slug or "").strip()
            invalid_values = {"", "none", "null"}
            if current_slug and current_slug.lower() not in invalid_values:
                continue

            row.shop_slug = make_unique_slug(session, row.name, row.id)
            updated += 1

        session.commit()
        print(f"Updated {updated} shops with generated slugs.")
        return updated
    except SQLAlchemyError:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    backfill_missing_shop_slugs()
