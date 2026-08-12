from collections import defaultdict
from typing import Optional, List, Literal
import json
from pydantic import BaseModel

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from starlette.datastructures import UploadFile
from pathlib import Path
import shutil
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from db.database import get_session
from db.db_models import (
    attribute_definition,
    attribute_option,
    product,
    product_attribute,
    product_group,
    product_image,
    shop,
    unique_visit,
    user as UserModel,
    UserRole,
)
from api.analytics import get_entity_view_counts, track_entity_view
import uuid
from datetime import datetime

allowed_image_mime_types = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/avif"}
allowed_image_extensions = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}


def _build_product_search_conditions(search: Optional[str]) -> list[str]:
    if not search:
        return []

    normalized = " ".join(search.strip().split())
    if not normalized:
        return []

    normalized = normalized.lower()
    terms = [term.lower() for term in normalized.split() if term]
    return [normalized, *terms]


def _build_product_search_filters(search: Optional[str]):
    if not search:
        return None, None, None, None

    normalized = " ".join(search.strip().split())
    if not normalized:
        return None, None, None, None

    normalized_lower = normalized.lower()
    terms = [term.lower() for term in normalized.split() if term]

    exact_match = or_(
        func.lower(product.name) == normalized_lower,
        func.lower(product.display_id) == normalized_lower,
    )

    phrase_match = or_(
        product.name.ilike(f"%{normalized}%"),
        product.display_id.ilike(f"%{normalized}%"),
    )

    if len(terms) == 1:
        word_match = or_(
            product.name.ilike(f"%{terms[0]}%"),
            product.display_id.ilike(f"%{terms[0]}%"),
        )
        return exact_match, phrase_match, word_match, word_match

    all_words_match = and_(
        *[
            or_(
                product.name.ilike(f"%{term}%"),
                product.display_id.ilike(f"%{term}%"),
            )
            for term in terms
        ]
    )

    any_words_match = or_(
        *[
            or_(
                product.name.ilike(f"%{term}%"),
                product.display_id.ilike(f"%{term}%"),
            )
            for term in terms
        ]
    )

    return exact_match, phrase_match, all_words_match, any_words_match


def validate_upload_file(uf: UploadFile):
    content_type = getattr(uf, "content_type", "") or ""
    if content_type.lower() in allowed_image_mime_types:
        return True
    suffix = Path(getattr(uf, "filename", "")).suffix.lower()
    return suffix in allowed_image_extensions


products_router = APIRouter(prefix="/api/products", tags=["Products"])


class ProductListItem(BaseModel):
    display_id: str
    name: str
    image_url: str
    shop_display_id: str
    shop_name: str
    shop_logo_url: str
    price: int
    discount_price: int | None
    stock_quantity: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
    view_count: int = 0
    attributes: list[dict] = []


class FilterAttributeOption(BaseModel):
    id: int
    value: str


class FilterAttribute(BaseModel):
    id: int
    name: str
    is_filterable: bool = False
    is_required: bool = False
    options: list[FilterAttributeOption]


class ProductsResponseData(BaseModel):
    page: int
    page_size: int
    total_count: int
    has_next: bool
    items: List[ProductListItem]


class ProductsResponse(BaseModel):
    success: bool
    message: str
    data: ProductsResponseData


class ProductAttributeItem(BaseModel):
    definition_id: int
    name: str
    option_id: int
    value: str
    is_filterable: bool


class ShopSummary(BaseModel):
    display_id: str
    name: str
    shop_logo_url: str
    email: str
    phone_number: str
    address: str
    city: Optional[str] = None
    website_url: Optional[str] = None
    youtube_url: Optional[str] = None
    instagram_url: Optional[str] = None
    facebook_url: Optional[str] = None


class ProductDetail(BaseModel):
    display_id: str
    name: str
    description: Optional[str]
    price: int
    discount_price: Optional[int]
    stock_quantity: int
    product_group_id: Optional[int]
    group_product_count: int
    video_url: Optional[str]
    created_at: datetime
    updated_at: datetime
    is_active: bool
    view_count: int = 0
    shop: ShopSummary
    images: List[str]
    attributes: List[ProductAttributeItem]


class ProductDetailResponse(BaseModel):
    success: bool
    message: str
    product: ProductDetail


class ProductVariantsResponse(BaseModel):
    success: bool
    message: str
    data: List[ProductListItem]


class ProductCreateRequest(BaseModel):
    shop_display_id: str
    name: str
    description: Optional[str]
    price: int
    discount_price: Optional[int]
    stock_quantity: int
    images: List[str]
    attributes: Optional[List[dict]] = None


class ProductCreateDetail(BaseModel):
    product_display_id: str
    shop_display_id: str
    name: str


class ProductCreateResponse(BaseModel):
    success: bool
    message: str
    data: ProductCreateDetail


class ProductUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[int] = None
    discount_price: Optional[int] = None
    stock_quantity: Optional[int] = None
    video_url: Optional[str] = None
    product_group_id: Optional[int] = None
    is_active: Optional[bool] = None
    attributes: Optional[List[dict]] = None
    image_urls: Optional[List[str]] = None
    primary_image_index: Optional[int] = None


class BulkAttributeUpdateItem(BaseModel):
    definition_id: int
    option_id: Optional[int] = None
    remove: bool = False


class BulkUpdateProductAttributesRequest(BaseModel):
    product_display_ids: List[str]
    updates: List[BulkAttributeUpdateItem]


class BulkUpdateProductAttributesResponse(BaseModel):
    success: bool
    message: str
    updated_count: int


class BulkProductActionRequest(BaseModel):
    product_display_ids: List[str]
    action: Literal[
        "set_active",
        "set_inactive",
        "change_price_percent",
        "set_discount_percent",
        "delete_products",
        "set_quantity",
    ]
    percentage: Optional[float] = None
    quantity: Optional[int] = None


class BulkProductActionResponse(BaseModel):
    success: bool
    message: str
    affected_count: int


def _serialize_listing_product(session: Session, item: product, view_count: int = 0):
    shop_row = session.query(shop).filter(shop.id == item.shop_id).first()
    primary_image = (
        session.query(product_image)
        .filter(product_image.product_id == item.id)
        .filter(product_image.primary_image.is_(True))
    )

    primary_image_row = primary_image.first()
    image_url = primary_image_row.image_url if primary_image_row else None
    product_attribute_rows = (
        session.query(product_attribute, attribute_option)
        .join(attribute_option, product_attribute.attribute_option_id == attribute_option.id)
        .filter(product_attribute.product_id == item.id)
        .all()
    )

    return {
        "display_id": item.display_id,
        "name": item.name,
        "image_url": image_url or "",
        "shop_display_id": shop_row.display_id,
        "shop_name": shop_row.name,
        "shop_logo_url": shop_row.shop_logo_url or "",
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "price": item.price,
        "discount_price": item.discount_price,
        "stock_quantity": item.stock_quantity,
        "is_active": item.is_active,
        "view_count": view_count,
        "attributes": [
            {
                "definition_id": attr.attribute_definition_id,
                "option_id": attr.attribute_option_id,
                "option_value": opt.option_value,
            }
            for attr, opt in product_attribute_rows
        ],
    }


def _serialize_product_detail(session: Session, item: product, view_count: int = 0):
    shop_row = session.query(shop).filter(shop.id == item.shop_id).first()

    group_count = 0
    if item.product_group_id is not None:
        # Count all products in the group, then subtract 1 (the main product itself)
        total_in_group = (
            session.query(product)
            .filter(
                product.shop_id == item.shop_id,
                product.product_group_id == item.product_group_id,
            )
            .count()
        )
        # group_count should be the number of variants (excluding the main product)
        group_count = max(0, total_in_group - 1)

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
        "display_id": item.display_id,
        "name": item.name,
        "description": item.description,
        "price": item.price,
        "discount_price": item.discount_price,
        "stock_quantity": item.stock_quantity,
        "product_group_id": item.product_group_id,
        "group_product_count": group_count,
        "video_url": item.video_url,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "is_active": item.is_active,
        "view_count": view_count,
        "shop": {
            "display_id": shop_row.display_id,
            "name": shop_row.name,
            "shop_logo_url": shop_row.shop_logo_url,
            "email": shop_row.email,
            "phone_number": shop_row.phone_number,
            "address": shop_row.address,
            "city": shop_row.city,
            "website_url": shop_row.website_url,
            "youtube_url": shop_row.youtube_url,
            "instagram_url": shop_row.instagram_url,
            "facebook_url": shop_row.facebook_url,
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


def _apply_attribute_option_filters(base_query, attribute_option_ids: list[int], session: Session):
    normalized_ids = sorted({int(v) for v in attribute_option_ids if isinstance(v, int) or str(v).isdigit()})
    if not normalized_ids:
        return base_query

    option_rows = (
        session.query(attribute_option.id, attribute_option.attribute_definition_id)
        .filter(attribute_option.id.in_(normalized_ids), attribute_option.is_active.is_(True))
        .all()
    )
    if not option_rows:
        return base_query

    options_by_definition: dict[int, set[int]] = defaultdict(set)
    for option_id, definition_id in option_rows:
        options_by_definition[int(definition_id)].add(int(option_id))

    # AND across definitions, OR across selected options within each definition.
    for definition_id, option_ids_for_definition in options_by_definition.items():
        filter_exists = exists(
            select(product_attribute.id).where(
                and_(
                    product_attribute.product_id == product.id,
                    product_attribute.attribute_definition_id == definition_id,
                    product_attribute.attribute_option_id.in_(list(option_ids_for_definition)),
                )
            )
        )
        base_query = base_query.filter(filter_exists)

    return base_query


def _build_attribute_match_exists(attribute_rows: list[tuple[int, int]]):
    if not attribute_rows:
        return None

    conditions = [
        and_(
            product_attribute.attribute_definition_id == definition_id,
            product_attribute.attribute_option_id == option_id,
        )
        for definition_id, option_id in attribute_rows
    ]

    return exists(
        select(product_attribute.id).where(
            and_(
                product_attribute.product_id == product.id,
                or_(*conditions),
            )
        )
    )


def _apply_sort(base_query, sort_by: Literal["newest", "price-low", "price-high", "most-viewed"]):
    if sort_by == "price-low":
        return base_query.order_by(func.coalesce(product.discount_price, product.price).asc(), product.id.desc())
    if sort_by == "price-high":
        return base_query.order_by(func.coalesce(product.discount_price, product.price).desc(), product.id.desc())
    if sort_by == "most-viewed":
        return base_query.order_by(product.id.desc())
    return base_query.order_by(product.created_at.desc(), product.id.desc())


@products_router.get("", response_model=ProductsResponse)
def get_products(
    request: Request,
    response: Response,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    shop_display_id: Optional[str] = Query(None),
    track_shop_view: bool = Query(False),
    min_price: Optional[float] = Query(None, ge=0),
    max_price: Optional[float] = Query(None, ge=0),
    sort_by: Literal["newest", "price-low", "price-high", "most-viewed"] = Query("newest"),
    view_count: bool = Query(False),
    attribute_option_ids: list[int] = Query(
        default=[],
        description="Repeat query param as attribute_option_ids=11&attribute_option_ids=24",
    ),
    attribute_filters: list[str] = Query(
        default=[],
        description="Repeat query param as attribute_filters=Color:Red&attribute_filters=Size:M",
    ),
    product_group_id: Optional[int] = Query(None, ge=1),
    session: Session = Depends(get_session),
):
    offset = (page - 1) * page_size

    current_user: Optional[UserModel] = getattr(request.state, "current_user", None)

    # base query depends on role
    if current_user is None:
        base_query = session.query(product).filter(product.is_active.is_(True), product.stock_quantity > 0)
        if shop_display_id is not None:
            shop_row = session.query(shop).filter(shop.display_id == shop_display_id).first()
            if not shop_row:
                return ProductsResponse(success=True, message="Products retrieved successfully", data=ProductsResponseData(page=page, page_size=page_size, total_count=0, has_next=False, items=[]))
            base_query = base_query.filter(product.shop_id == shop_row.id)
            if track_shop_view:
                track_entity_view(session, request, response, "shop", shop_row.id)
    elif current_user.role == UserRole.USER:
        # authenticated public users should follow public visibility rules
        base_query = session.query(product).filter(product.is_active.is_(True), product.stock_quantity > 0)
        if shop_display_id is not None:
            shop_row = session.query(shop).filter(shop.display_id == shop_display_id).first()
            if not shop_row:
                return ProductsResponse(success=True, message="Products retrieved successfully", data=ProductsResponseData(page=page, page_size=page_size, total_count=0, has_next=False, items=[]))
            base_query = base_query.filter(product.shop_id == shop_row.id)
            if track_shop_view:
                track_entity_view(session, request, response, "shop", shop_row.id)
    elif current_user.role == UserRole.SHOP_OWNER:
        # single-shop vendor: find the shop owned by this user
        shop_row = session.query(shop).filter(shop.owner_id == current_user.id).first()
        if not shop_row:
            return {"page": page, "page_size": page_size, "total_count": 0, "has_next": False, "items": []}
        vendor_shop_display_id = shop_row.display_id
        if shop_display_id is not None and shop_display_id != vendor_shop_display_id:
            raise HTTPException(status_code=403, detail="Not authorized for requested shop")
        base_query = session.query(product).filter(product.shop_id == shop_row.id)
    elif current_user.role == UserRole.ADMIN:
        # admin: full access
        base_query = session.query(product)
        if shop_display_id is not None:
            shop_row = session.query(shop).filter(shop.display_id == shop_display_id).first()
            if not shop_row:
                return ProductsResponse(success=True, message="Products retrieved successfully", data=ProductsResponseData(page=page, page_size=page_size, total_count=0, has_next=False, items=[]))
            base_query = base_query.filter(product.shop_id == shop_row.id)
    else:
        raise HTTPException(status_code=403, detail="Invalid role")

    if search:
        search_text = " ".join(search.strip().split())
        if search_text:
            exact_match, phrase_match, all_words_match, any_words_match = _build_product_search_filters(search_text)
            if exact_match is not None:
                exact_exists = session.query(exists().where(exact_match)).scalar()
                if exact_exists:
                    base_query = base_query.filter(exact_match)
                elif phrase_match is not None:
                    phrase_exists = session.query(exists().where(phrase_match)).scalar()
                    if phrase_exists:
                        base_query = base_query.filter(phrase_match)
                    elif all_words_match is not None:
                        all_words_exists = session.query(exists().where(all_words_match)).scalar()
                        if all_words_exists:
                            base_query = base_query.filter(all_words_match)
                        elif any_words_match is not None:
                            base_query = base_query.filter(any_words_match)

    if min_price is not None:
        base_query = base_query.filter(func.coalesce(product.discount_price, product.price) >= min_price)

    if max_price is not None:
        base_query = base_query.filter(func.coalesce(product.discount_price, product.price) <= max_price)

    if product_group_id is not None:
        base_query = base_query.filter(product.product_group_id == product_group_id)

    base_query = _apply_attribute_option_filters(base_query, attribute_option_ids, session)
    base_query = _apply_attribute_filters(base_query, attribute_filters)

    total_count = base_query.count()

    if sort_by == "most-viewed":
        views_subq = (
            session.query(
                unique_visit.entity_id.label("entity_id"),
                func.sum(unique_visit.visit_count).label("view_count"),
            )
            .filter(unique_visit.entity_type == "product")
            .group_by(unique_visit.entity_id)
            .subquery()
        )

        items = (
            base_query.outerjoin(views_subq, views_subq.c.entity_id == product.id)
            .order_by(func.coalesce(views_subq.c.view_count, 0).desc(), product.id.desc())
            .offset(offset)
            .limit(page_size)
            .all()
        )
    else:
        items = (
            _apply_sort(base_query, sort_by)
            .offset(offset)
            .limit(page_size)
            .all()
        )

    product_ids = [p.id for p in items]
    view_counts = get_entity_view_counts(session, "product", product_ids) if product_ids else {}

    items_out = []
    for p in items:
        row = _serialize_listing_product(session, p, view_count=view_counts.get(int(p.id), 0))
        items_out.append(row)

    temp = ProductsResponseData(
        page=page,
        page_size=page_size,
        total_count=total_count,
        has_next=(offset + page_size < total_count),
        items=[ProductListItem(**it) for it in items_out],
    )

    return ProductsResponse(success=True, message="Products retrieved successfully", data=temp)


@products_router.get("/filters/attributes", response_model=list[FilterAttribute])
def get_filterable_attributes(session: Session = Depends(get_session)):
    rows = (
        session.query(attribute_definition)
        .filter(
            attribute_definition.is_active.is_(True),
            attribute_definition.is_filterable.is_(True),
        )
        .order_by(attribute_definition.attribute_name.asc())
        .all()
    )

    result: list[FilterAttribute] = []
    for row in rows:
        options = (
            session.query(attribute_option)
            .filter(
                attribute_option.attribute_definition_id == row.id,
                attribute_option.is_active.is_(True),
            )
            .order_by(attribute_option.option_value.asc())
            .all()
        )
        if not options:
            continue
        result.append(
            FilterAttribute(
                id=row.id,
                name=row.attribute_name,
                is_filterable=row.is_filterable,
                is_required=row.is_required,
                options=[FilterAttributeOption(id=opt.id, value=opt.option_value) for opt in options],
            )
        )

    return result


@products_router.get("/attributes", response_model=list[FilterAttribute])
def get_editable_attributes(session: Session = Depends(get_session)):
    rows = (
        session.query(attribute_definition)
        .filter(attribute_definition.is_active.is_(True))
        .order_by(attribute_definition.attribute_name.asc())
        .all()
    )

    result: list[FilterAttribute] = []
    for row in rows:
        options = (
            session.query(attribute_option)
            .filter(
                attribute_option.attribute_definition_id == row.id,
                attribute_option.is_active.is_(True),
            )
            .order_by(attribute_option.option_value.asc())
            .all()
        )
        if not options:
            continue

        result.append(
            FilterAttribute(
                id=row.id,
                name=row.attribute_name,
                is_filterable=row.is_filterable,
                is_required=row.is_required,
                options=[FilterAttributeOption(id=opt.id, value=opt.option_value) for opt in options],
            )
        )

    return result


@products_router.get("/{product_id}", response_model=ProductDetailResponse)
def get_product_details(
    request: Request,
    response: Response,
    product_id: str,
    session: Session = Depends(get_session),
):
    current_user: Optional[UserModel] = getattr(request.state, "current_user", None)

    # base selection depends on role
    q = session.query(product).filter(product.display_id == product_id)
    if current_user is None or current_user.role == UserRole.USER:
        q = q.filter(product.is_active.is_(True))
    elif current_user.role == UserRole.SHOP_OWNER:
        # vendor can view product only if it belongs to their shop
        shop_row = session.query(shop).filter(shop.owner_id == current_user.id).first()
        if not shop_row:
            raise HTTPException(status_code=404, detail="Product not found")
        q = q.filter(product.shop_id == shop_row.id)
    elif current_user.role == UserRole.ADMIN:
        # admin: no additional filters
        q = q
    else:
        raise HTTPException(status_code=403, detail="Invalid role")

    selected_product = q.first()

    if selected_product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    attribute_rows = (
        session.query(product_attribute.attribute_definition_id, product_attribute.attribute_option_id)
        .filter(product_attribute.product_id == selected_product.id)
        .all()
    )
    track_entity_view(session, request, response, "product", selected_product.id, attribute_rows=attribute_rows)

    view_counts = get_entity_view_counts(session, "product", [selected_product.id])
    detail = _serialize_product_detail(
        session,
        selected_product,
        view_count=view_counts.get(selected_product.id, 0),
    )

    product_model = ProductDetail(
        display_id=detail["display_id"],
        name=detail["name"],
        description=detail.get("description"),
        price=detail["price"],
        discount_price=detail.get("discount_price"),
        stock_quantity=detail.get("stock_quantity"),
        product_group_id=detail.get("product_group_id"),
        group_product_count=detail.get("group_product_count", 1),
        video_url=detail.get("video_url"),
        created_at=detail.get("created_at"),
        updated_at=detail.get("updated_at"),
        is_active=detail.get("is_active"),
        view_count=detail.get("view_count", 0),
        shop=ShopSummary(**detail.get("shop", {})),
        images=detail.get("images", []),
        attributes=[ProductAttributeItem(**a) for a in detail.get("attributes", [])],
    )

    return ProductDetailResponse(success=True, message="Product details retrieved successfully", product=product_model)


@products_router.get("/{product_id}/variants",response_model=ProductVariantsResponse)
def get_product_variants(
    request: Request,
    product_id: str,
    session: Session = Depends(get_session),
):
    current_user: Optional[UserModel] = getattr(request.state, "current_user", None)

    # fetch selected product with role-based access
    q = session.query(product).filter(product.display_id == product_id)
    if current_user is None or current_user.role == UserRole.USER:
        q = q.filter(product.is_active.is_(True))
    elif current_user.role == UserRole.SHOP_OWNER:
        shop_row = session.query(shop).filter(shop.owner_id == current_user.id).first()
        if not shop_row:
            raise HTTPException(status_code=404, detail="Product not found")
        q = q.filter(product.shop_id == shop_row.id)
    elif current_user.role == UserRole.ADMIN:
        q = q
    else:
        raise HTTPException(status_code=403, detail="Invalid role")

    selected_product = q.first()
    if selected_product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    # If the product doesn't have a group, it has no variants
    if not selected_product.product_group_id:
        return ProductVariantsResponse(success=True, message="Product variants retrieved successfully", data=[])

    vq = session.query(product).filter(
        product.product_group_id == selected_product.product_group_id,
        product.shop_id == selected_product.shop_id,
        product.id != selected_product.id,  # Exclude the main product itself
    )
    if current_user is None or current_user.role == UserRole.USER:
        vq = vq.filter(product.is_active.is_(True))

    variants = vq.order_by(product.created_at.desc()).all()
    variant_ids = [p.id for p in variants]
    view_counts = get_entity_view_counts(session, "product", variant_ids) if variant_ids else {}

    items = []
    for p in variants:
        row = _serialize_listing_product(session, p, view_count=view_counts.get(p.id, 0))
        if current_user is None:
            row.pop("is_active", None)
        items.append(ProductListItem(**row))

    return ProductVariantsResponse(success=True, message="Product variants retrieved successfully", data=items)


@products_router.get("/{product_id}/similar-from-shop", response_model=ProductVariantsResponse)
def get_similar_products_from_shop(
    request: Request,
    product_id: str,
    session: Session = Depends(get_session),
):
    current_user: Optional[UserModel] = getattr(request.state, "current_user", None)

    q = session.query(product).filter(product.display_id == product_id)
    if current_user is None or current_user.role == UserRole.USER:
        q = q.filter(product.is_active.is_(True), product.stock_quantity > 0)
    elif current_user.role == UserRole.SHOP_OWNER:
        shop_row = session.query(shop).filter(shop.owner_id == current_user.id).first()
        if not shop_row:
            raise HTTPException(status_code=404, detail="Product not found")
        q = q.filter(product.shop_id == shop_row.id)
    elif current_user.role == UserRole.ADMIN:
        pass
    else:
        raise HTTPException(status_code=403, detail="Invalid role")

    selected_product = q.first()
    if selected_product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    attribute_rows = (
        session.query(product_attribute.attribute_definition_id, product_attribute.attribute_option_id)
        .filter(product_attribute.product_id == selected_product.id)
        .all()
    )

    similar_query = session.query(product).filter(
        product.id != selected_product.id,
        product.shop_id == selected_product.shop_id,
    )
    if current_user is None or current_user.role == UserRole.USER:
        similar_query = similar_query.filter(product.is_active.is_(True), product.stock_quantity > 0)

    attribute_exists = _build_attribute_match_exists(attribute_rows)
    if attribute_exists is not None:
        similar_query = similar_query.filter(attribute_exists)

    similar_products = similar_query.order_by(product.created_at.desc()).limit(12).all()
    similar_ids = [p.id for p in similar_products]
    view_counts = get_entity_view_counts(session, "product", similar_ids) if similar_ids else {}

    items = [ProductListItem(**_serialize_listing_product(session, p, view_count=view_counts.get(p.id, 0))) for p in similar_products]

    return ProductVariantsResponse(success=True, message="Similar products retrieved successfully", data=items)


@products_router.get("/{product_id}/similar-from-other-shops", response_model=ProductVariantsResponse)
def get_similar_products_from_other_shops(
    request: Request,
    product_id: str,
    session: Session = Depends(get_session),
):
    current_user: Optional[UserModel] = getattr(request.state, "current_user", None)

    q = session.query(product).filter(product.display_id == product_id)
    if current_user is None or current_user.role == UserRole.USER:
        q = q.filter(product.is_active.is_(True), product.stock_quantity > 0)
    elif current_user.role == UserRole.SHOP_OWNER:
        shop_row = session.query(shop).filter(shop.owner_id == current_user.id).first()
        if not shop_row:
            raise HTTPException(status_code=404, detail="Product not found")
        q = q.filter(product.shop_id == shop_row.id)
    elif current_user.role == UserRole.ADMIN:
        pass
    else:
        raise HTTPException(status_code=403, detail="Invalid role")

    selected_product = q.first()
    if selected_product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    attribute_rows = (
        session.query(product_attribute.attribute_definition_id, product_attribute.attribute_option_id)
        .filter(product_attribute.product_id == selected_product.id)
        .all()
    )

    similar_query = session.query(product).filter(
        product.id != selected_product.id,
        product.shop_id != selected_product.shop_id,
    )
    if current_user is None or current_user.role == UserRole.USER:
        similar_query = similar_query.filter(product.is_active.is_(True), product.stock_quantity > 0)

    attribute_exists = _build_attribute_match_exists(attribute_rows)
    if attribute_exists is not None:
        similar_query = similar_query.filter(attribute_exists)

    similar_products = similar_query.order_by(product.created_at.desc()).limit(12).all()
    if not similar_products:
        fallback_query = session.query(product).filter(
            product.id != selected_product.id,
            product.shop_id != selected_product.shop_id,
        )
        if current_user is None or current_user.role == UserRole.USER:
            fallback_query = fallback_query.filter(product.is_active.is_(True), product.stock_quantity > 0)
        similar_products = fallback_query.order_by(product.created_at.desc()).limit(12).all()

    similar_ids = [p.id for p in similar_products]
    view_counts = get_entity_view_counts(session, "product", similar_ids) if similar_ids else {}

    items = [ProductListItem(**_serialize_listing_product(session, p, view_count=view_counts.get(p.id, 0))) for p in similar_products]

    return ProductVariantsResponse(success=True, message="Similar products retrieved successfully", data=items)


@products_router.post("/bulk-update-attributes", response_model=BulkUpdateProductAttributesResponse)
async def bulk_update_product_attributes(
    payload: BulkUpdateProductAttributesRequest,
    request: Request,
    session: Session = Depends(get_session),
):
    current_user: Optional[UserModel] = getattr(request.state, "current_user", None)
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    product_ids = list(dict.fromkeys([str(pid).strip() for pid in payload.product_display_ids if str(pid).strip()]))
    if not product_ids:
        raise HTTPException(status_code=422, detail="At least one product is required")

    if not payload.updates:
        raise HTTPException(status_code=422, detail="At least one attribute update is required")

    target_products = session.query(product).filter(product.display_id.in_(product_ids)).all()
    if len(target_products) != len(product_ids):
        raise HTTPException(status_code=404, detail="One or more products were not found")

    shop_ids = {p.shop_id for p in target_products}

    if current_user.role == UserRole.SHOP_OWNER:
        if len(shop_ids) != 1:
            raise HTTPException(status_code=400, detail="Selected products must belong to the same shop")
        target_shop_id = next(iter(shop_ids))
        owner_shop = session.query(shop).filter(shop.owner_id == current_user.id).first()
        if not owner_shop or owner_shop.id != target_shop_id:
            raise HTTPException(status_code=403, detail="Not authorized to update products from this shop")
    elif current_user.role == UserRole.ADMIN:
        # Admin may update attributes across multiple shops.
        target_shop_id = None
    else:
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    normalized: dict[int, dict] = {}
    for item in payload.updates:
        definition_id = int(item.definition_id)
        if item.remove:
            normalized[definition_id] = {"remove": True, "option_id": None}
            continue
        if item.option_id is None:
            raise HTTPException(status_code=422, detail=f"option_id required for definition {definition_id}")
        normalized[definition_id] = {"remove": False, "option_id": int(item.option_id)}

    definition_ids = list(normalized.keys())
    option_ids = [v["option_id"] for v in normalized.values() if not v["remove"] and v["option_id"] is not None]

    defs = session.query(attribute_definition).filter(attribute_definition.id.in_(definition_ids)).all()
    def_map = {d.id: d for d in defs}
    missing_defs = [d for d in definition_ids if d not in def_map]
    if missing_defs:
        raise HTTPException(status_code=400, detail=f"Attribute definitions not found: {missing_defs}")

    options = []
    if option_ids:
        options = session.query(attribute_option).filter(attribute_option.id.in_(option_ids)).all()
    option_map = {o.id: o for o in options}

    for definition_id, op in normalized.items():
        if op["remove"]:
            continue
        option_id = op["option_id"]
        option_row = option_map.get(option_id)
        if option_row is None:
            raise HTTPException(status_code=400, detail=f"Attribute option not found: {option_id}")
        if option_row.attribute_definition_id != definition_id:
            raise HTTPException(
                status_code=400,
                detail=f"Attribute option {option_id} does not belong to definition {definition_id}",
            )

    now = datetime.now()

    for prod in target_products:
        existing_rows = (
            session.query(product_attribute)
            .filter(
                product_attribute.product_id == prod.id,
                product_attribute.attribute_definition_id.in_(definition_ids),
            )
            .all()
        )
        existing_by_def = {row.attribute_definition_id: row for row in existing_rows}

        for definition_id, op in normalized.items():
            if op["remove"]:
                existing = existing_by_def.get(definition_id)
                if existing:
                    session.delete(existing)
                continue

            option_id = op["option_id"]
            existing = existing_by_def.get(definition_id)
            if existing:
                existing.attribute_option_id = option_id
                existing.updated_at = now
                session.add(existing)
            else:
                session.add(
                    product_attribute(
                        product_id=prod.id,
                        attribute_definition_id=definition_id,
                        attribute_option_id=option_id,
                        created_at=now,
                        updated_at=now,
                    )
                )

        prod.updated_at = now
        session.add(prod)

    session.commit()

    return BulkUpdateProductAttributesResponse(
        success=True,
        message="Product attributes updated successfully",
        updated_count=len(target_products),
    )


@products_router.post("/bulk-product-action", response_model=BulkProductActionResponse)
async def bulk_product_action(
    payload: BulkProductActionRequest,
    request: Request,
    session: Session = Depends(get_session),
):
    current_user: Optional[UserModel] = getattr(request.state, "current_user", None)
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    product_ids = list(dict.fromkeys([str(pid).strip() for pid in payload.product_display_ids if str(pid).strip()]))
    if not product_ids:
        raise HTTPException(status_code=422, detail="At least one product is required")

    target_products = session.query(product).filter(product.display_id.in_(product_ids)).all()
    if len(target_products) != len(product_ids):
        raise HTTPException(status_code=404, detail="One or more products were not found")

    shop_ids = {p.shop_id for p in target_products}
    target_shop_ids = shop_ids

    if current_user.role == UserRole.SHOP_OWNER:
        if len(shop_ids) != 1:
            raise HTTPException(status_code=400, detail="Selected products must belong to the same shop")
        target_shop_id = next(iter(shop_ids))
        owner_shop = session.query(shop).filter(shop.owner_id == current_user.id).first()
        if not owner_shop or owner_shop.id != target_shop_id:
            raise HTTPException(status_code=403, detail="Not authorized to update products from this shop")
        target_shop_ids = {target_shop_id}
    elif current_user.role == UserRole.ADMIN:
        # Admin may act across products from multiple shops.
        target_shop_ids = shop_ids
    else:
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    now = datetime.now()
    affected_count = len(target_products)

    if payload.action == "set_active":
        for p in target_products:
            p.is_active = True
            p.updated_at = now
            session.add(p)

    elif payload.action == "set_inactive":
        for p in target_products:
            p.is_active = False
            p.updated_at = now
            session.add(p)

    elif payload.action == "change_price_percent":
        if payload.percentage is None:
            raise HTTPException(status_code=422, detail="percentage is required for change_price_percent")
        if payload.percentage < -100:
            raise HTTPException(status_code=422, detail="percentage must be greater than or equal to -100")

        ratio = (100.0 + payload.percentage) / 100.0
        for p in target_products:
            p.price = max(0, int(round(p.price * ratio)))
            if p.discount_price is not None and p.discount_price > p.price:
                p.discount_price = p.price
            p.updated_at = now
            session.add(p)

    elif payload.action == "set_discount_percent":
        if payload.percentage is None:
            raise HTTPException(status_code=422, detail="percentage is required for set_discount_percent")
        if payload.percentage < 0 or payload.percentage > 100:
            raise HTTPException(status_code=422, detail="percentage must be between 0 and 100")

        ratio = (100.0 - payload.percentage) / 100.0
        for p in target_products:
            next_discount = int(round(p.price * ratio))
            next_discount = max(0, min(next_discount, p.price))
            p.discount_price = next_discount
            p.updated_at = now
            session.add(p)

    elif payload.action == "set_quantity":
        if payload.quantity is None:
            raise HTTPException(status_code=422, detail="quantity is required for set_quantity")
        if payload.quantity < 0:
            raise HTTPException(status_code=422, detail="quantity must be non-negative")

        for p in target_products:
            p.stock_quantity = payload.quantity
            p.updated_at = now
            session.add(p)

    elif payload.action == "delete_products":
        for p in target_products:
            session.delete(p)

    session.commit()

    # Cleanup empty groups in that shop after bulk operations (especially delete)
    try:
        empty_groups = (
            session.query(product_group.id)
            .filter(product_group.shop_id == target_shop_id)
            .outerjoin(product, (product_group.id == product.product_group_id) & (product_group.shop_id == product.shop_id))
            .group_by(product_group.id)
            .having(func.count(product.id) == 0)
            .all()
        )

        for (group_id,) in empty_groups:
            session.query(product_group).filter(product_group.id == group_id).delete(synchronize_session=False)

        if empty_groups:
            session.commit()
    except Exception:
        session.rollback()

    return BulkProductActionResponse(
        success=True,
        message="Bulk product action completed successfully",
        affected_count=affected_count,
    )


class UpdateVariantsRequest(BaseModel):
    variant_display_ids: List[str]  # List of product display_ids to set as variants


@products_router.post("/{product_id}/update-variants", response_model=ProductDetailResponse)
async def update_product_variants(
    product_id: str,
    payload: UpdateVariantsRequest,
    request: Request,
    session: Session = Depends(get_session),
):
    """Update product variants. Creates product group if needed."""
    current_user: Optional[UserModel] = getattr(request.state, "current_user", None)

    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    # Get the main product
    main_product = session.query(product).filter(product.display_id == product_id).first()
    if not main_product:
        raise HTTPException(status_code=404, detail="Product not found")

    # Authorization check
    if current_user.role == UserRole.SHOP_OWNER:
        owner_shop = session.query(shop).filter(shop.owner_id == current_user.id).first()
        if not owner_shop or owner_shop.id != main_product.shop_id:
            raise HTTPException(status_code=403, detail="Not authorized to update this product")
    elif current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    now = datetime.now()

    # Check if main product already has a group
    if not main_product.product_group_id:
        # Create a new product group for this shop
        new_group = product_group(
            shop_id=main_product.shop_id,
            created_at=now,
            updated_at=now,
            is_active=True,
        )
        session.add(new_group)
        session.flush()  # Flush to get the auto-generated id
        group_id = new_group.id
    else:
        group_id = main_product.product_group_id

    # Update main product to have this group
    main_product.product_group_id = group_id
    main_product.updated_at = now

    # Get all products to update (both main and variants)
    product_display_ids_set = set(payload.variant_display_ids)
    product_display_ids_set.add(product_id)  # Include main product

    # Get current variants before update (for cleanup)
    current_variants = (
        session.query(product).filter(
            product.product_group_id == group_id,
            product.shop_id == main_product.shop_id,
        )
    ).all()
    current_variant_ids = {p.display_id for p in current_variants}

    # Update selected products to have this group_id
    for display_id in product_display_ids_set:
        p = session.query(product).filter(product.display_id == display_id).first()
        if p and p.shop_id == main_product.shop_id:
            p.product_group_id = group_id
            p.updated_at = now

    # Remove products that are no longer variants (set to null)
    for display_id in current_variant_ids:
        if display_id not in product_display_ids_set:
            p = session.query(product).filter(product.display_id == display_id).first()
            if p:
                p.product_group_id = None
                p.updated_at = now

    session.add(main_product)
    session.commit()

    # Clean up empty product groups (groups with no products)
    try:
        empty_groups = (
            session.query(product_group.id)
            .filter(product_group.shop_id == main_product.shop_id)
            .outerjoin(product, (product_group.id == product.product_group_id) & (product_group.shop_id == product.shop_id))
            .group_by(product_group.id)
            .having(func.count(product.id) == 0)
            .all()
        )

        for (empty_group_id,) in empty_groups:
            session.query(product_group).filter(product_group.id == empty_group_id).delete(synchronize_session=False)

        if empty_groups:
            session.commit()
    except Exception:
        session.rollback()

    session.refresh(main_product)

    detail = _serialize_product_detail(session, main_product)
    product_model = ProductDetail(
        display_id=detail["display_id"],
        name=detail["name"],
        description=detail.get("description"),
        price=detail["price"],
        discount_price=detail.get("discount_price"),
        stock_quantity=detail.get("stock_quantity"),
        product_group_id=detail.get("product_group_id"),
        group_product_count=detail.get("group_product_count", 1),
        video_url=detail.get("video_url"),
        created_at=detail.get("created_at"),
        updated_at=detail.get("updated_at"),
        is_active=detail.get("is_active"),
        shop=ShopSummary(**detail.get("shop", {})),
        images=detail.get("images", []),
        attributes=[ProductAttributeItem(**a) for a in detail.get("attributes", [])],
    )

    return ProductDetailResponse(success=True, message="Variants updated successfully", product=product_model)


@products_router.put("/{product_id}", response_model=ProductDetailResponse)
async def update_product(
    product_id: str,
    request: Request,
    session: Session = Depends(get_session),
):
    current_user: Optional[UserModel] = getattr(request.state, "current_user", None)

    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required to update products")

    target_product = session.query(product).filter(product.display_id == product_id).first()
    if not target_product:
        raise HTTPException(status_code=404, detail="Product not found")

    if current_user.role == UserRole.SHOP_OWNER:
        owner_shop = session.query(shop).filter(shop.owner_id == current_user.id).first()
        if not owner_shop or owner_shop.id != target_product.shop_id:
            raise HTTPException(status_code=403, detail="Not authorized to update this product")
    elif current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Insufficient privileges to update products")

    content_type = request.headers.get("content-type", "")
    now = datetime.now()
    parsed_attributes: Optional[list[dict]] = None
    parsed_image_urls: Optional[list[str]] = None
    uploaded_image_urls: list[str] = []
    primary_image_index: Optional[int] = None

    if content_type.startswith("multipart/form-data"):
        form = await request.form()

        if "name" in form:
            name = str(form.get("name") or "").strip()
            if not name:
                raise HTTPException(status_code=422, detail="Product name cannot be empty")
            target_product.name = name

        if "description" in form:
            description = form.get("description")
            target_product.description = str(description).strip() if description not in (None, "") else None

        if "price" in form:
            try:
                price = int(str(form.get("price")))
            except Exception:
                raise HTTPException(status_code=422, detail="Invalid price value")
            if price < 0:
                raise HTTPException(status_code=422, detail="Price must be non-negative")
            target_product.price = price

        if "discount_price" in form or "discounted_price" in form:
            discount_raw = form.get("discount_price") or form.get("discounted_price")
            if discount_raw in (None, ""):
                target_product.discount_price = None
            else:
                try:
                    discount_price = int(str(discount_raw))
                except Exception:
                    raise HTTPException(status_code=422, detail="Invalid discount_price value")
                if discount_price < 0:
                    raise HTTPException(status_code=422, detail="Discount price must be non-negative")
                target_product.discount_price = discount_price

        if "stock_quantity" in form:
            try:
                stock_quantity = int(str(form.get("stock_quantity")))
            except Exception:
                raise HTTPException(status_code=422, detail="Invalid stock_quantity value")
            if stock_quantity < 0:
                raise HTTPException(status_code=422, detail="Stock quantity must be non-negative")
            target_product.stock_quantity = stock_quantity

        if "video_url" in form:
            video_url = form.get("video_url")
            target_product.video_url = str(video_url).strip() if video_url not in (None, "") else None

        if "product_group_id" in form:
            raw_group_id = form.get("product_group_id")
            if raw_group_id in (None, ""):
                target_product.product_group_id = None
            else:
                try:
                    parsed_group_id = int(str(raw_group_id))
                except Exception:
                    raise HTTPException(status_code=422, detail="Invalid product_group_id value")

                # Check if group exists; if not, create it
                group_row = (
                    session.query(product_group)
                    .filter(product_group.id == parsed_group_id, product_group.shop_id == target_product.shop_id)
                    .first()
                )
                if not group_row:
                    # Auto-create the product group
                    group_row = product_group(
                        id=parsed_group_id,
                        shop_id=target_product.shop_id,
                        created_at=now,
                        updated_at=now,
                        is_active=True,
                    )
                    session.add(group_row)
                    session.flush()
                
                target_product.product_group_id = parsed_group_id

        if "is_active" in form:
            raw_active = str(form.get("is_active") or "").strip().lower()
            if raw_active in {"true", "1", "yes"}:
                target_product.is_active = True
            elif raw_active in {"false", "0", "no"}:
                target_product.is_active = False
            else:
                raise HTTPException(status_code=422, detail="Invalid is_active value")

        if "attributes" in form:
            attributes_raw = form.get("attributes")
            if attributes_raw in (None, ""):
                parsed_attributes = []
            else:
                try:
                    loaded_attributes = json.loads(str(attributes_raw))
                except Exception:
                    raise HTTPException(status_code=422, detail="Invalid attributes JSON")
                if not isinstance(loaded_attributes, list):
                    raise HTTPException(status_code=422, detail="Attributes must be a list")
                parsed_attributes = loaded_attributes

        if "image_urls" in form:
            image_urls_raw = form.get("image_urls")
            if image_urls_raw in (None, ""):
                parsed_image_urls = []
            else:
                try:
                    loaded_image_urls = json.loads(str(image_urls_raw))
                except Exception:
                    raise HTTPException(status_code=422, detail="Invalid image_urls JSON")
                if not isinstance(loaded_image_urls, list) or any(not isinstance(item, str) for item in loaded_image_urls):
                    raise HTTPException(status_code=422, detail="image_urls must be a string list")
                parsed_image_urls = [item for item in loaded_image_urls if item]
                if len(parsed_image_urls) > 5:
                    raise HTTPException(status_code=400, detail="Maximum 5 images can be uploaded.")

        if "primary_image_index" in form:
            primary_raw = form.get("primary_image_index")
            if primary_raw in (None, ""):
                primary_image_index = 0
            else:
                try:
                    primary_image_index = int(str(primary_raw))
                except Exception:
                    raise HTTPException(status_code=422, detail="Invalid primary_image_index value")

        upload_files: list[UploadFile] = []
        for key, value in form.multi_items():
            if key == "images" and hasattr(value, "filename"):
                upload_files.append(value)

        if upload_files:
            if len(upload_files) > 5:
                raise HTTPException(status_code=400, detail="Maximum 5 images can be uploaded.")

            uploads_dir = Path("static") / "uploads"
            uploads_dir.mkdir(parents=True, exist_ok=True)
            for uf in upload_files:
                if not validate_upload_file(uf):
                    raise HTTPException(status_code=400, detail="Only JPG, PNG, WebP, GIF, and AVIF image formats are allowed.")
                orig = getattr(uf, "filename", "upload")
                ext = Path(orig).suffix or ""
                fname = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex}{ext}"
                safe_path = uploads_dir / fname
                try:
                    with safe_path.open("wb") as out_file:
                        shutil.copyfileobj(uf.file, out_file)
                    uploaded_image_urls.append(f"/static/uploads/{fname}")
                finally:
                    try:
                        uf.file.close()
                    except Exception:
                        pass

    else:
        body = await request.json()
        if "discounted_price" in body and "discount_price" not in body:
            body["discount_price"] = body.pop("discounted_price")

        try:
            ProductUpdateRequest(**body)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc))

        if "name" in body:
            name = str(body.get("name") or "").strip()
            if not name:
                raise HTTPException(status_code=422, detail="Product name cannot be empty")
            target_product.name = name

        if "description" in body:
            description = body.get("description")
            target_product.description = str(description).strip() if description not in (None, "") else None

        if "price" in body:
            price = body.get("price")
            if not isinstance(price, int) or price < 0:
                raise HTTPException(status_code=422, detail="Price must be a non-negative integer")
            target_product.price = price

        if "discount_price" in body:
            discount_price = body.get("discount_price")
            if discount_price in (None, ""):
                target_product.discount_price = None
            elif not isinstance(discount_price, int) or discount_price < 0:
                raise HTTPException(status_code=422, detail="Discount price must be a non-negative integer")
            else:
                target_product.discount_price = discount_price

        if "stock_quantity" in body:
            stock_quantity = body.get("stock_quantity")
            if not isinstance(stock_quantity, int) or stock_quantity < 0:
                raise HTTPException(status_code=422, detail="Stock quantity must be a non-negative integer")
            target_product.stock_quantity = stock_quantity

        if "video_url" in body:
            video_url = body.get("video_url")
            target_product.video_url = str(video_url).strip() if video_url not in (None, "") else None

        if "product_group_id" in body:
            incoming_group_id = body.get("product_group_id")
            if incoming_group_id in (None, ""):
                target_product.product_group_id = None
            elif not isinstance(incoming_group_id, int):
                raise HTTPException(status_code=422, detail="product_group_id must be an integer")
            else:
                # Check if group exists; if not, create it
                group_row = (
                    session.query(product_group)
                    .filter(product_group.id == incoming_group_id, product_group.shop_id == target_product.shop_id)
                    .first()
                )
                if not group_row:
                    # Auto-create the product group
                    group_row = product_group(
                        id=incoming_group_id,
                        shop_id=target_product.shop_id,
                        created_at=now,
                        updated_at=now,
                        is_active=True,
                    )
                    session.add(group_row)
                    session.flush()
                
                target_product.product_group_id = incoming_group_id

        if "is_active" in body:
            is_active = body.get("is_active")
            if not isinstance(is_active, bool):
                raise HTTPException(status_code=422, detail="is_active must be a boolean")
            target_product.is_active = is_active

        if "attributes" in body:
            attrs = body.get("attributes")
            if attrs is None:
                parsed_attributes = []
            elif not isinstance(attrs, list):
                raise HTTPException(status_code=422, detail="Attributes must be a list")
            else:
                parsed_attributes = attrs

        if "image_urls" in body:
            image_urls = body.get("image_urls")
            if image_urls is None:
                parsed_image_urls = []
            elif not isinstance(image_urls, list) or any(not isinstance(item, str) for item in image_urls):
                raise HTTPException(status_code=422, detail="image_urls must be a string list")
            else:
                parsed_image_urls = [item for item in image_urls if item]
                if len(parsed_image_urls) > 5:
                    raise HTTPException(status_code=400, detail="Maximum 5 images can be uploaded.")

        if "primary_image_index" in body:
            incoming_primary_index = body.get("primary_image_index")
            if incoming_primary_index is None:
                primary_image_index = 0
            elif not isinstance(incoming_primary_index, int):
                raise HTTPException(status_code=422, detail="primary_image_index must be an integer")
            else:
                primary_image_index = incoming_primary_index

    if parsed_attributes is not None:
        normalized_pairs: list[tuple[int, int]] = []
        for row in parsed_attributes:
            if not isinstance(row, dict):
                continue
            definition_id = row.get("definition_id")
            option_id = row.get("option_id")
            if definition_id is None or option_id is None:
                continue
            try:
                normalized_pairs.append((int(definition_id), int(option_id)))
            except Exception:
                raise HTTPException(status_code=422, detail="Invalid attribute identifiers")

        dedup_by_definition: dict[int, int] = {}
        for definition_id, option_id in normalized_pairs:
            dedup_by_definition[definition_id] = option_id

        if dedup_by_definition:
            option_rows = (
                session.query(attribute_option)
                .filter(attribute_option.id.in_(list(dedup_by_definition.values())))
                .all()
            )
            option_by_id = {opt.id: opt for opt in option_rows}

            for definition_id, option_id in dedup_by_definition.items():
                option_row = option_by_id.get(option_id)
                if option_row is None:
                    raise HTTPException(status_code=400, detail=f"Attribute option not found: {option_id}")
                if option_row.attribute_definition_id != definition_id:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Attribute option {option_id} does not belong to definition {definition_id}",
                    )

        session.query(product_attribute).filter(product_attribute.product_id == target_product.id).delete(
            synchronize_session=False
        )

        for definition_id, option_id in dedup_by_definition.items():
            session.add(
                product_attribute(
                    product_id=target_product.id,
                    attribute_definition_id=definition_id,
                    attribute_option_id=option_id,
                    created_at=now,
                    updated_at=now,
                )
            )

    images_update_requested = parsed_image_urls is not None or len(uploaded_image_urls) > 0
    if images_update_requested:
        final_urls = (parsed_image_urls or []) + uploaded_image_urls
        if len(final_urls) == 0:
            raise HTTPException(status_code=422, detail="At least one image is required")

        if primary_image_index is None:
            primary_image_index = 0
        if primary_image_index < 0 or primary_image_index >= len(final_urls):
            raise HTTPException(status_code=422, detail="primary_image_index is out of range")

        session.query(product_image).filter(product_image.product_id == target_product.id).delete(
            synchronize_session=False
        )

        for idx, url in enumerate(final_urls):
            session.add(
                product_image(
                    product_id=target_product.id,
                    image_url=url,
                    primary_image=(idx == primary_image_index),
                    created_at=now,
                    updated_at=now,
                    is_active=True,
                )
            )

    target_product.updated_at = now
    session.add(target_product)
    session.commit()
    session.refresh(target_product)
    
    # Clean up empty product groups (groups with no products) - only in this shop
    try:
        # Find all product groups in this shop that have no associated products
        empty_groups = (
            session.query(product_group.id)
            .filter(product_group.shop_id == target_product.shop_id)
            .outerjoin(product, (product_group.id == product.product_group_id) & (product_group.shop_id == product.shop_id))
            .group_by(product_group.id)
            .having(func.count(product.id) == 0)
            .all()
        )
        
        for (group_id,) in empty_groups:
            session.query(product_group).filter(product_group.id == group_id).delete(synchronize_session=False)
        
        if empty_groups:
            session.commit()
    except Exception:
        # If cleanup fails, continue anyway - it's not critical
        session.rollback()
    
    session.refresh(target_product)

    detail = _serialize_product_detail(session, target_product)
    product_model = ProductDetail(
        display_id=detail["display_id"],
        name=detail["name"],
        description=detail.get("description"),
        price=detail["price"],
        discount_price=detail.get("discount_price"),
        stock_quantity=detail.get("stock_quantity"),
        product_group_id=detail.get("product_group_id"),
        group_product_count=detail.get("group_product_count", 1),
        video_url=detail.get("video_url"),
        created_at=detail.get("created_at"),
        updated_at=detail.get("updated_at"),
        is_active=detail.get("is_active"),
        shop=ShopSummary(**detail.get("shop", {})),
        images=detail.get("images", []),
        attributes=[ProductAttributeItem(**a) for a in detail.get("attributes", [])],
    )

    return ProductDetailResponse(success=True, message="Product updated successfully", product=product_model)


@products_router.post("/create", response_model=ProductCreateResponse)
@products_router.post("", response_model=ProductCreateResponse)
async def create_product(request: Request, session: Session = Depends(get_session)):
    current_user: Optional[UserModel] = getattr(request.state, "current_user", None)

    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required to create products")

    # Support both JSON body and multipart/form-data uploads.
    content_type = request.headers.get("content-type", "")
    images_urls: list[str] = []
    parsed_attributes: list[dict] = []

    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        # Read fields (form values are strings)
        shop_display_id = form.get("shop_display_id")
        name = form.get("name")
        description = form.get("description")
        price_raw = form.get("price")
        # accept alias 'discounted_price' as well
        discount_raw = form.get("discount_price") or form.get("discounted_price")
        stock_raw = form.get("stock_quantity")
        attributes_raw = form.get("attributes")

        # collect uploaded files from repeated 'images' fields
        upload_files: list[UploadFile] = []
        for k, v in form.multi_items():
            if k == "images" and hasattr(v, "filename"):
                upload_files.append(v)

        if len(upload_files) > 5:
            raise HTTPException(status_code=400, detail="Maximum 5 images can be uploaded.")

        # save uploaded files into single uploads folder
        uploads_dir = Path("static") / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)

        for uf in upload_files:
            if not validate_upload_file(uf):
                raise HTTPException(status_code=400, detail="Only JPG, PNG, WebP, GIF, and AVIF image formats are allowed.")
            orig = getattr(uf, "filename", "upload")
            ext = Path(orig).suffix or ""
            fname = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex}{ext}"
            safe_path = uploads_dir / fname
            try:
                with safe_path.open("wb") as out_file:
                    shutil.copyfileobj(uf.file, out_file)
                images_urls.append(f"/static/uploads/{fname}")
            finally:
                try:
                    uf.file.close()
                except Exception:
                    pass

        # validate minimal required fields
        if not shop_display_id or not name or not price_raw or not stock_raw:
            raise HTTPException(status_code=422, detail="Missing required form fields")

        # coerce numeric values
        try:
            price = int(price_raw)
        except Exception:
            raise HTTPException(status_code=422, detail="Invalid price value")
        try:
            stock_quantity = int(stock_raw)
        except Exception:
            raise HTTPException(status_code=422, detail="Invalid stock_quantity value")

        discount_price = None
        if discount_raw not in (None, ""):
            try:
                discount_price = int(discount_raw)
            except Exception:
                raise HTTPException(status_code=422, detail="Invalid discount_price value")

        if attributes_raw not in (None, ""):
            try:
                loaded_attributes = json.loads(str(attributes_raw))
            except Exception:
                raise HTTPException(status_code=422, detail="Invalid attributes JSON")
            if not isinstance(loaded_attributes, list):
                raise HTTPException(status_code=422, detail="Attributes must be a list")
            parsed_attributes = loaded_attributes

    else:
        # JSON body path
        body = await request.json()
        # accept alias
        if "discounted_price" in body and "discount_price" not in body:
            body["discount_price"] = body.pop("discounted_price")

        try:
            payload = ProductCreateRequest(**body)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc))

        shop_display_id = payload.shop_display_id
        name = payload.name
        description = payload.description
        price = int(payload.price)
        discount_price = int(payload.discount_price) if payload.discount_price not in (None, "") else None
        stock_quantity = int(payload.stock_quantity)
        images_urls = payload.images or []
        parsed_attributes = payload.attributes or []

        if len(images_urls) > 5:
            raise HTTPException(status_code=400, detail="Maximum 5 images can be uploaded.")

    shop_row = session.query(shop).filter(shop.display_id == shop_display_id).first()
    if not shop_row:
        raise HTTPException(status_code=404, detail="Shop not found")

    # role checks: vendor can create only for their own shop; admin can create for any
    if current_user.role == UserRole.SHOP_OWNER:
        # ensure this vendor owns the shop
        if shop_row.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not authorized to create product for this shop")
    elif current_user.role == UserRole.ADMIN:
        pass
    else:
        raise HTTPException(status_code=403, detail="Insufficient privileges to create products")

    now = datetime.now()

    if not images_urls or len(images_urls) == 0:
        raise HTTPException(status_code=400, detail="At least one image is required to create a product")

    p = product(
        shop_id=shop_row.id,
        name=name,
        description=description,
        price=price,
        discount_price=discount_price,
        stock_quantity=stock_quantity,
        created_at=now,
        updated_at=now,
        is_active=True,
    )
    session.add(p)
    session.flush()

    for idx, url in enumerate(images_urls):
        img = product_image(
            product_id=p.id,
            image_url=url,
            primary_image=(idx == 0),
            created_at=now,
            updated_at=now,
            is_active=True,
        )
        session.add(img)

    # Persist attribute selections when provided.
    normalized_pairs: list[tuple[int, int]] = []
    for row in parsed_attributes:
        if not isinstance(row, dict):
            continue
        definition_id = row.get("definition_id")
        option_id = row.get("option_id")
        if definition_id is None or option_id is None:
            continue
        try:
            normalized_pairs.append((int(definition_id), int(option_id)))
        except Exception:
            raise HTTPException(status_code=422, detail="Invalid attribute identifiers")

    # De-duplicate by definition to satisfy unique constraint (keep the last selected option).
    dedup_by_definition: dict[int, int] = {}
    for definition_id, option_id in normalized_pairs:
        dedup_by_definition[definition_id] = option_id

    if dedup_by_definition:
        option_rows = (
            session.query(attribute_option)
            .filter(attribute_option.id.in_(list(dedup_by_definition.values())))
            .all()
        )
        option_by_id = {opt.id: opt for opt in option_rows}

        for definition_id, option_id in dedup_by_definition.items():
            option_row = option_by_id.get(option_id)
            if option_row is None:
                raise HTTPException(status_code=400, detail=f"Attribute option not found: {option_id}")
            if option_row.attribute_definition_id != definition_id:
                raise HTTPException(
                    status_code=400,
                    detail=f"Attribute option {option_id} does not belong to definition {definition_id}",
                )

            session.add(
                product_attribute(
                    product_id=p.id,
                    attribute_definition_id=definition_id,
                    attribute_option_id=option_id,
                    created_at=now,
                    updated_at=now,
                )
            )

    session.commit()
    data = {
        "product_display_id": p.display_id,
        "shop_display_id": shop_row.display_id,
        "name": p.name,
    }
    product_create_detail = ProductCreateDetail(**data)
    return ProductCreateResponse(success=True, message="Product created", data=product_create_detail)