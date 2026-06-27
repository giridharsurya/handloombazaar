import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from db.database import get_engine, get_session
from db.db_models import (
    Base,
    attribute_definition,
    attribute_option,
    collection,
    collection_product,
    product,
    product_attribute,
    product_group,
    product_image,
    shop,
    shop_collection,
    shop_collection_product,
)

create_router = APIRouter(prefix="/api/create", tags=["Products"])

SEED_DIR = Path(__file__).resolve().parent.parent / "seed_data"


def _load_seed_rows(file_name: str) -> list[dict]:
    file_path = SEED_DIR / file_name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Seed file not found: {file_path.name}")

    with file_path.open("r", encoding="utf-8") as seed_file:
        rows = json.load(seed_file)

    if not isinstance(rows, list):
        raise HTTPException(status_code=400, detail=f"Invalid JSON format in {file_path.name}")

    for row in rows:
        if "created_at" in row and isinstance(row["created_at"], str):
            row["created_at"] = datetime.fromisoformat(row["created_at"])
        if "updated_at" in row and isinstance(row["updated_at"], str):
            row["updated_at"] = datetime.fromisoformat(row["updated_at"])

    return rows

@create_router.post("/")
def create_product(engine = Depends(get_engine)):
    # Create the database tables if they don't exist
    Base.metadata.create_all(bind=engine)
    return {"message": "Database tables created successfully."}


@create_router.post("/seed")
def seed_database(
    recreate_tables: bool = False,
    engine=Depends(get_engine),
    session: Session = Depends(get_session),
):
    if recreate_tables:
        Base.metadata.drop_all(bind=engine)

    Base.metadata.create_all(bind=engine)

    insert_order = [
        (shop, "shops.json"),
        (product_group, "product_groups.json"),
        (product, "products.json"),
        (collection, "collections.json"),
        (collection_product, "collection_products.json"),
        (shop_collection, "shop_collections.json"),
        (shop_collection_product, "shop_collection_products.json"),
        (product_image, "product_images.json"),
        (attribute_definition, "attribute_definitions.json"),
        (attribute_option, "attribute_options.json"),
        (product_attribute, "product_attributes.json"),
    ]

    delete_order = [
        product_attribute,
        attribute_option,
        attribute_definition,
        product_image,
        shop_collection_product,
        shop_collection,
        collection_product,
        collection,
        product,
        product_group,
        shop,
    ]

    inserted_counts: dict[str, int] = {}

    try:
        for table_model in delete_order:
            session.query(table_model).delete()

        for table_model, file_name in insert_order:
            rows = _load_seed_rows(file_name)
            if rows:
                session.bulk_insert_mappings(table_model, rows)
            inserted_counts[table_model.__tablename__] = len(rows)

        session.commit()
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to seed database: {exc}")

    return {
        "message": "Database seeded successfully.",
        "inserted": inserted_counts,
    }


