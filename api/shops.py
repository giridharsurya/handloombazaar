from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from db.database import get_session
from db.db_models import shop

shops_router = APIRouter(prefix="/api/shops", tags=["shops"])


class ShopStatusResponse(BaseModel):
    display_id: str
    name: str
    shop_logo_url: str
    approved: bool
    is_active: bool


class ShopDetailResponse(ShopStatusResponse):
    description: str | None
    email: str
    address: str
    phone_number: str
    year_established: int
    website_url: str | None
    youtube_url: str | None
    instagram_url: str | None
    facebook_url: str | None


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
def shop_detail(display_id: str, session: Session = Depends(get_session)):
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

    return ShopDetailResponse(
        display_id=selected_shop.display_id,
        name=selected_shop.name,
        shop_logo_url=selected_shop.shop_logo_url,
        approved=bool(selected_shop.approved),
        is_active=bool(selected_shop.is_active),
        description=getattr(selected_shop, "description", None),
        email=selected_shop.email,
        address=selected_shop.address,
        phone_number=selected_shop.phone_number,
        year_established=selected_shop.year_established,
        website_url=selected_shop.website_url,
        youtube_url=selected_shop.youtube_url,
        instagram_url=selected_shop.instagram_url,
        facebook_url=selected_shop.facebook_url,
    )


@shops_router.get("/", response_model=list[ShopStatusResponse])
def list_shops(session: Session = Depends(get_session)):
    rows = session.query(shop).filter(shop.approved.is_(True), shop.is_active.is_(True)).order_by(shop.created_at.desc()).all()
    return [
        ShopStatusResponse(
            display_id=row.display_id,
            name=row.name,
            shop_logo_url=row.shop_logo_url,
            approved=bool(row.approved),
            is_active=bool(row.is_active),
        )
        for row in rows
    ]
