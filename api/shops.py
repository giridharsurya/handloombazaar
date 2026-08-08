from datetime import datetime
from pathlib import Path
import shutil
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Depends, Request, UploadFile, File, Response, Query
from sqlalchemy import func
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr, Field
from db.database import get_session
from db.db_models import shop, UserRole, product
from api.analytics import get_entity_view_counts, track_entity_view

shops_router = APIRouter(prefix="/api/shops", tags=["shops"])


class ShopStatusResponse(BaseModel):
    display_id: str
    name: str
    shop_logo_url: str
    approved: bool
    is_active: bool
    view_count: int = 0


class ShopDetailResponse(ShopStatusResponse):
    description: str | None
    email: str
    address: str
    city: str | None
    phone_number: str
    year_established: int
    website_url: str | None
    youtube_url: str | None
    instagram_url: str | None
    facebook_url: str | None


class ShopUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    email: EmailStr | None = None
    year_established: int | None = Field(default=None, ge=1800, le=2100)
    address: str | None = Field(default=None, min_length=3, max_length=500)
    city: str | None = Field(default=None, min_length=2, max_length=120)
    phone_number: str | None = Field(default=None, min_length=7, max_length=20)
    website_url: str | None = Field(default=None, max_length=255)
    youtube_url: str | None = Field(default=None, max_length=255)
    instagram_url: str | None = Field(default=None, max_length=255)
    facebook_url: str | None = Field(default=None, max_length=255)


def _build_shop_detail_response(selected_shop: shop) -> ShopDetailResponse:
    return ShopDetailResponse(
        display_id=selected_shop.display_id,
        name=selected_shop.name,
        shop_logo_url=selected_shop.shop_logo_url,
        approved=bool(selected_shop.approved),
        is_active=bool(selected_shop.is_active),
        description=getattr(selected_shop, "description", None),
        email=selected_shop.email,
        address=selected_shop.address,
        city=selected_shop.city,
        phone_number=selected_shop.phone_number,
        year_established=selected_shop.year_established,
        website_url=selected_shop.website_url,
        youtube_url=selected_shop.youtube_url,
        instagram_url=selected_shop.instagram_url,
        facebook_url=selected_shop.facebook_url,
    )


def _get_shop_product_counts(session: Session, shop_ids: list[int]) -> dict[int, int]:
    if not shop_ids:
        return {}
    rows = (
        session.query(product.shop_id, func.count(product.id))
        .filter(product.shop_id.in_(shop_ids))
        .group_by(product.shop_id)
        .all()
    )
    return {shop_id: count for shop_id, count in rows}


def _get_manageable_shop(display_id: str, request: Request, session: Session) -> shop:
    current_user = getattr(request.state, "current_user", None)
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    selected_shop = session.query(shop).filter(shop.display_id == display_id).first()
    if selected_shop is None:
        raise HTTPException(status_code=404, detail="Shop not found")

    is_admin = current_user.role == UserRole.ADMIN
    is_owner = current_user.role == UserRole.SHOP_OWNER and selected_shop.owner_id == current_user.id
    if not is_admin and not is_owner:
        raise HTTPException(status_code=403, detail="Not authorized to access this shop")

    return selected_shop


@shops_router.get("/{display_id}/status", response_model=ShopStatusResponse)
def shop_status(display_id: str, session: Session = Depends(get_session)):
    selected_shop = session.query(shop).filter(shop.display_id == display_id).first()
    if selected_shop is None:
        raise HTTPException(status_code=404, detail="Shop not found")

    return ShopStatusResponse(
        display_id=selected_shop.display_id,
        name=selected_shop.name,
        shop_logo_url=selected_shop.shop_logo_url,
        approved=bool(selected_shop.approved),
        is_active=bool(selected_shop.is_active),
    )


@shops_router.get("/{display_id}", response_model=ShopDetailResponse)
def shop_detail(display_id: str, request: Request, response: Response, session: Session = Depends(get_session)):
    selected_shop = (
        session.query(shop)
        .filter(
            shop.display_id == display_id,
            shop.approved.is_(True),
            shop.is_active.is_(True),
        )
        .first()
    )
    if selected_shop is None:
        raise HTTPException(status_code=404, detail="Shop not found")

    track_entity_view(session, request, response, "shop", selected_shop.id)
    return _build_shop_detail_response(selected_shop)


@shops_router.get("/{display_id}/manage", response_model=ShopDetailResponse)
def shop_manage_detail(display_id: str, request: Request, session: Session = Depends(get_session)):
    selected_shop = _get_manageable_shop(display_id, request, session)
    return _build_shop_detail_response(selected_shop)


@shops_router.put("/{display_id}", response_model=ShopDetailResponse)
def update_shop_details(
    display_id: str,
    payload: ShopUpdateRequest,
    request: Request,
    session: Session = Depends(get_session),
):
    selected_shop = _get_manageable_shop(display_id, request, session)
    provided_fields = getattr(payload, "model_fields_set", payload.__fields_set__)

    if "email" in provided_fields:
        if payload.email is None:
            raise HTTPException(status_code=400, detail="Email cannot be null")
        normalized_email = payload.email.strip().lower()
        existing_email = (
            session.query(shop)
            .filter(shop.email == normalized_email, shop.id != selected_shop.id)
            .first()
        )
        if existing_email is not None:
            raise HTTPException(status_code=400, detail="Shop email already exists")
        selected_shop.email = normalized_email

    if "name" in provided_fields:
        if payload.name is None:
            raise HTTPException(status_code=400, detail="Shop name cannot be null")
        selected_shop.name = payload.name.strip()
    if "year_established" in provided_fields:
        if payload.year_established is None:
            raise HTTPException(status_code=400, detail="Year established cannot be null")
        selected_shop.year_established = payload.year_established
    if "address" in provided_fields:
        if payload.address is None:
            raise HTTPException(status_code=400, detail="Address cannot be null")
        selected_shop.address = payload.address.strip()
    if "city" in provided_fields:
        if payload.city is None:
            raise HTTPException(status_code=400, detail="City cannot be null")
        selected_shop.city = payload.city.strip()
    if "phone_number" in provided_fields:
        if payload.phone_number is None:
            raise HTTPException(status_code=400, detail="Phone number cannot be null")
        selected_shop.phone_number = payload.phone_number.strip()
    if "website_url" in provided_fields:
        selected_shop.website_url = payload.website_url.strip() if payload.website_url else None
    if "youtube_url" in provided_fields:
        selected_shop.youtube_url = payload.youtube_url.strip() if payload.youtube_url else None
    if "instagram_url" in provided_fields:
        selected_shop.instagram_url = payload.instagram_url.strip() if payload.instagram_url else None
    if "facebook_url" in provided_fields:
        selected_shop.facebook_url = payload.facebook_url.strip() if payload.facebook_url else None

    selected_shop.updated_at = datetime.now()
    session.commit()
    session.refresh(selected_shop)
    return _build_shop_detail_response(selected_shop)


@shops_router.put("/{display_id}/logo", response_model=ShopDetailResponse)
def update_shop_logo(
    display_id: str,
    request: Request,
    shop_logo: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    selected_shop = _get_manageable_shop(display_id, request, session)

    uploads_dir = Path("static") / "uploads" / "shops"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    original_name = getattr(shop_logo, "filename", "upload")
    extension = Path(original_name).suffix or ""
    filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex}{extension}"
    target = uploads_dir / filename

    try:
        with target.open("wb") as out_file:
            shutil.copyfileobj(shop_logo.file, out_file)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to save uploaded logo: {exc}")
    finally:
        try:
            shop_logo.file.close()
        except Exception:
            pass

    selected_shop.shop_logo_url = f"/static/uploads/shops/{filename}"
    selected_shop.updated_at = datetime.now()
    session.commit()
    session.refresh(selected_shop)
    return _build_shop_detail_response(selected_shop)


@shops_router.get("")
def list_shops(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort_by: Literal["newest", "most-viewed", "product-count"] = Query("newest"),
    view_count: bool = Query(False),
    session: Session = Depends(get_session),
):
    rows = session.query(shop).filter(shop.approved.is_(True), shop.is_active.is_(True)).all()
    total_count = len(rows)
    shop_ids = [r.id for r in rows]

    if sort_by == "most-viewed":
        counts = get_entity_view_counts(session, "shop", shop_ids)
        rows.sort(key=lambda r: (-counts.get(r.id, 0), -int(r.created_at.timestamp() if r.created_at else 0), -r.id))
    elif sort_by == "product-count":
        product_counts = _get_shop_product_counts(session, shop_ids)
        rows.sort(key=lambda r: (-product_counts.get(r.id, 0), -int(r.created_at.timestamp() if r.created_at else 0), -r.id))
    else:
        rows.sort(key=lambda r: (-int(r.created_at.timestamp() if r.created_at else 0), -r.id))

    offset = (page - 1) * page_size
    page_rows = rows[offset : offset + page_size]
    view_counts = {}
    if sort_by == "most-viewed" or view_count:
        view_counts = get_entity_view_counts(session, "shop", [r.id for r in page_rows])

    product_counts = _get_shop_product_counts(session, [r.id for r in page_rows])

    return {
        "items": [
            {
                "display_id": row.display_id,
                "name": row.name,
                "shop_logo_url": row.shop_logo_url,
                "approved": bool(row.approved),
                "is_active": bool(row.is_active),
                "view_count": view_counts.get(row.id, 0),
                "product_count": product_counts.get(row.id, 0),
            }
            for row in page_rows
        ],
        "page": page,
        "page_size": page_size,
        "total_count": total_count,
        "has_next": offset + page_size < total_count,
    }
