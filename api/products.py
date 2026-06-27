from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, exists, select
from sqlalchemy.orm import Session

from db.database import get_session
from db.db_models import (
    attribute_definition,
    attribute_option,
    product,
    product_attribute,
    product_image,
    shop,
)


products_router = APIRouter(prefix="/api/products", tags=["Products"])


def _serialize_listing_product(session: Session, item: product):
    shop_row = session.query(shop).filter(shop.id == item.shop_id).first()
    primary_image = (
        session.query(product_image)
        .filter(product_image.product_id == item.id)
        .order_by(product_image.created_at.asc())
        .first()
    )

    return {
        "id": item.id,
        "name": item.name,
        "image_url": primary_image.image_url if primary_image else None,
        "shop_id": item.shop_id,
        "shop_name": shop_row.name if shop_row else None,
        "shop_logo": shop_row.shop_image_url if shop_row else None,
        "price": item.price,
        "discount_price": item.discounted_price,
    }


def _serialize_product_detail(session: Session, item: product):
    shop_row = session.query(shop).filter(shop.id == item.shop_id).first()

    images = (
        session.query(product_image)
        .filter(product_image.product_id == item.id)
        .order_by(product_image.created_at.asc())
        .all()
    )

    attribute_rows = (
        session.query(product_attribute, attribute_definition, attribute_option)
        .join(
            attribute_definition,
            product_attribute.attribute_definition_id == attribute_definition.id,
        )
        .join(attribute_option, product_attribute.attribute_option_id == attribute_option.id)
        .filter(product_attribute.product_id == item.id)
        .all()
    )

    return {
        "id": item.id,
        "shop_id": item.shop_id,
        "shop_product_id": item.shop_product_id,
        "display_id": item.display_id,
        "name": item.name,
        "description": item.description,
        "price": item.price,
        "discounted_price": item.discounted_price,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "is_active": item.is_active,
        "shop": {
            "id": shop_row.id if shop_row else None,
            "name": shop_row.name if shop_row else None,
            "shop_image_url": shop_row.shop_image_url if shop_row else None,
        },
        "images": [img.image_url for img in images],
        "attributes": [
            {
                "definition_id": definition.id,
                "name": definition.attribute_name,
                "option_id": option.id,
                "value": option.option_value,
                "is_filterable": definition.is_filterable,
            }
            for _, definition, option in attribute_rows
        ],
    }


def _apply_attribute_filters(base_query, attribute_filters: list[str]):
    for raw_filter in attribute_filters:
        if ":" not in raw_filter:
            continue

        attribute_name, option_value = raw_filter.split(":", 1)
        attribute_name = attribute_name.strip()
        option_value = option_value.strip()

        if not attribute_name or not option_value:
            continue

        filter_exists = exists(
            select(product_attribute.id)
            .join(
                attribute_definition,
                product_attribute.attribute_definition_id == attribute_definition.id,
            )
            .join(
                attribute_option,
                product_attribute.attribute_option_id == attribute_option.id,
            )
            .where(
                and_(
                    product_attribute.product_id == product.id,
                    attribute_definition.attribute_name == attribute_name,
                    attribute_option.option_value == option_value,
                    attribute_definition.is_filterable.is_(True),
                )
            )
        )

        base_query = base_query.filter(filter_exists)

    return base_query


@products_router.get("/")
def get_products(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    shop_id: Optional[int] = Query(None, ge=1),
    min_price: Optional[float] = Query(None, ge=0),
    max_price: Optional[float] = Query(None, ge=0),
    attribute_filters: list[str] = Query(
        default=[],
        description="Repeat query param as attribute_filters=Color:Red&attribute_filters=Size:M",
    ),
    session: Session = Depends(get_session),
):
    offset = (page - 1) * page_size

    base_query = session.query(product).filter(product.is_active.is_(True))

    if search:
        base_query = base_query.filter(product.name.ilike(f"%{search.strip()}%"))

    if shop_id is not None:
        base_query = base_query.filter(product.shop_id == shop_id)

    if min_price is not None:
        base_query = base_query.filter(product.price >= min_price)

    if max_price is not None:
        base_query = base_query.filter(product.price <= max_price)

    base_query = _apply_attribute_filters(base_query, attribute_filters)

    total_count = base_query.count()
    items = (
        base_query.order_by(product.created_at.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )

    return {
        "page": page,
        "page_size": page_size,
        "total_count": total_count,
        "has_next": offset + page_size < total_count,
        "items": [_serialize_listing_product(session, p) for p in items],
    }


@products_router.get("/{product_id}")
def get_product_with_variants(
    product_id: int,
    session: Session = Depends(get_session),
):
    selected_product = (
        session.query(product)
        .filter(product.id == product_id, product.is_active.is_(True))
        .first()
    )

    if selected_product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    variants = (
        session.query(product)
        .filter(
            product.product_group_id == selected_product.product_group_id,
            product.shop_id == selected_product.shop_id,
            product.is_active.is_(True),
        )
        .order_by(product.created_at.desc())
        .all()
    )

    return {
        "product": _serialize_product_detail(session, selected_product),
        "variants": [_serialize_product_detail(session, p) for p in variants],
    }
