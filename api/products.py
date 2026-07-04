from typing import Optional, List
from pydantic import BaseModel

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.datastructures import UploadFile
from pathlib import Path
import shutil
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
    user as UserModel,
    UserRole,
)
import uuid
from datetime import datetime


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
    is_active: bool
    created_at: datetime
    updated_at: datetime


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


class ProductDetail(BaseModel):
    display_id: str
    name: str
    description: Optional[str]
    price: int
    discount_price: Optional[int]
    created_at: datetime
    updated_at: datetime
    is_active: bool
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


class ProductCreateDetail(BaseModel):
    product_display_id: str
    shop_display_id: str
    name: str


class ProductCreateResponse(BaseModel):
    success: bool
    message: str
    data: ProductCreateDetail


def _serialize_listing_product(session: Session, item: product):
    shop_row = session.query(shop).filter(shop.id == item.shop_id).first()
    primary_image = (
        session.query(product_image)
        .filter(product_image.product_id == item.id)
        .filter(product_image.primary_image.is_(True))
    )

    primary_image_row = primary_image.first()
    image_url = primary_image_row.image_url if primary_image_row else None

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
        "is_active": item.is_active,
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
        "display_id": item.display_id,
        "name": item.name,
        "description": item.description,
        "price": item.price,
        "discount_price": item.discount_price,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "is_active": item.is_active,
        "shop": {
            "display_id": shop_row.display_id,
            "name": shop_row.name,
            "shop_logo_url": shop_row.shop_logo_url,
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


@products_router.get("/", response_model=ProductsResponse)
def get_products(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    shop_display_id: Optional[str] = Query(None),
    min_price: Optional[float] = Query(None, ge=0),
    max_price: Optional[float] = Query(None, ge=0),
    attribute_filters: list[str] = Query(
        default=[],
        description="Repeat query param as attribute_filters=Color:Red&attribute_filters=Size:M",
    ),
    session: Session = Depends(get_session),
):
    offset = (page - 1) * page_size

    current_user: Optional[UserModel] = getattr(request.state, "current_user", None)

    # base query depends on role
    if current_user is None:
        base_query = session.query(product).filter(product.is_active.is_(True))
        if shop_display_id is not None:
            shop_row = session.query(shop).filter(shop.display_id == shop_display_id).first()
            if not shop_row:
                return ProductsResponse(success=True, message="Products retrieved successfully", data=ProductsResponseData(page=page, page_size=page_size, total_count=0, has_next=False, items=[]))
            base_query = base_query.filter(product.shop_id == shop_row.id)
    elif current_user.role == UserRole.SHOP_OWNER:
        # single-shop vendor: find the shop owned by this user
        shop_row = session.query(shop).filter(shop.owner_id == current_user.id).first()
        if not shop_row:
            return {"page": page, "page_size": page_size, "total_count": 0, "has_next": False, "items": []}
        vendor_shop_display_id = shop_row.display_id
        if shop_display_id is not None and shop_display_id != vendor_shop_display_id:
            raise HTTPException(status_code=403, detail="Not authorized for requested shop")
        base_query = session.query(product).filter(product.shop_id == shop_row.id)
    else:
        # admin: full access
        base_query = session.query(product)
        if shop_display_id is not None:
            shop_row = session.query(shop).filter(shop.display_id == shop_display_id).first()
            if not shop_row:
                return ProductsResponse(success=True, message="Products retrieved successfully", data=ProductsResponseData(page=page, page_size=page_size, total_count=0, has_next=False, items=[]))
            base_query = base_query.filter(product.shop_id == shop_row.id)

    if search:
        base_query = base_query.filter(product.name.ilike(f"%{search.strip()}%"))


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

    items_out = []
    for p in items:
        row = _serialize_listing_product(session, p)
        items_out.append(row)

    temp = ProductsResponseData(
        page=page,
        page_size=page_size,
        total_count=total_count,
        has_next=(offset + page_size < total_count),
        items=[ProductListItem(**it) for it in items_out],
    )

    return ProductsResponse(success=True, message="Products retrieved successfully", data=temp)


@products_router.get("/{product_id}", response_model=ProductDetailResponse)
def get_product_details(
    request: Request,
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

    detail = _serialize_product_detail(session, selected_product)

    product_model = ProductDetail(
        display_id=detail["display_id"],
        name=detail["name"],
        description=detail.get("description"),
        price=detail["price"],
        discount_price=detail.get("discount_price"),
        created_at=detail.get("created_at"),
        updated_at=detail.get("updated_at"),
        is_active=detail.get("is_active"),
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

    vq = session.query(product).filter(
        product.product_group_id == selected_product.product_group_id,
        product.shop_id == selected_product.shop_id,
    )
    if current_user is None or current_user.role == UserRole.USER:
        vq = vq.filter(product.is_active.is_(True))

    variants = vq.order_by(product.created_at.desc()).all()

    items = []
    for p in variants:
        row = _serialize_listing_product(session, p)
        if current_user is None:
            row.pop("is_active", None)
        items.append(ProductListItem(**row))

    return ProductVariantsResponse(success=True, message="Product variants retrieved successfully", data=items)


@products_router.post("/create", response_model=ProductCreateResponse)
@products_router.post("/", response_model=ProductCreateResponse)
async def create_product(request: Request, session: Session = Depends(get_session)):
    current_user: Optional[UserModel] = getattr(request.state, "current_user", None)

    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required to create products")

    # Support both JSON body and multipart/form-data uploads.
    content_type = request.headers.get("content-type", "")
    images_urls: list[str] = []

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

        # collect uploaded files from repeated 'images' fields
        upload_files: list[UploadFile] = []
        for k, v in form.multi_items():
            if k == "images" and hasattr(v, "filename"):
                upload_files.append(v)

        # save uploaded files into single uploads folder
        uploads_dir = Path("static") / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)

        for uf in upload_files:
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

    session.commit()
    data = {
        "product_display_id": p.display_id,
        "shop_display_id": shop_row.display_id,
        "name": p.name,
    }
    product_create_detail = ProductCreateDetail(**data)
    return ProductCreateResponse(success=True, message="Product created", data=product_create_detail)