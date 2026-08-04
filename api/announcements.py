from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from db.database import get_session
from db.db_models import (
    announcement_banner,
    collection,
    shop,
    shop_collection,
    collection_product,
    shop_collection_product,
    product,
)


announcements_router = APIRouter(prefix="/api/announcements", tags=["Announcements"])


class AnnouncementUpsertRequest(BaseModel):
    collection_id: int
    title: str = Field(..., min_length=1, max_length=255)
    subtitle: Optional[str] = Field(default=None, max_length=500)
    background_color: str = Field(default="#F43F5E", min_length=4, max_length=20)
    text_color: str = Field(default="#FFFFFF", min_length=4, max_length=20)
    is_active: bool = True
    # admin may target a specific shop banner
    shop_display_id: Optional[str] = None


@announcements_router.get("")
def list_announcements(
    shop_display_id: Optional[str] = None,
    include_inactive: bool = False,
    session: Session = Depends(get_session),
):
    q = session.query(announcement_banner)
    if not include_inactive:
        q = q.filter(announcement_banner.is_active == True)  # noqa: E712

    rows = q.order_by(announcement_banner.updated_at.desc()).all()

    requested_shop = None
    if shop_display_id:
        requested_shop = session.query(shop).filter(shop.display_id == shop_display_id).first()

    def _collection_has_shop_products(collection_id: int, shop_id: int) -> bool:
        # canonical members
        canonical_count = (
            session.query(collection_product)
            .join(product, collection_product.product_id == product.id)
            .filter(collection_product.collection_id == collection_id, product.shop_id == shop_id)
            .count()
        )
        if canonical_count > 0:
            return True

        # shop-scoped members
        sc_ids = [
            r.id
            for r in session.query(shop_collection)
            .filter(shop_collection.collection_id == collection_id)
            .all()
        ]
        if not sc_ids:
            return False

        scoped_count = (
            session.query(shop_collection_product)
            .join(product, shop_collection_product.product_id == product.id)
            .filter(shop_collection_product.shop_collection_id.in_(sc_ids), product.shop_id == shop_id)
            .count()
        )
        return scoped_count > 0

    items = []
    for row in rows:
        c = session.query(collection).filter(collection.id == row.collection_id).first()
        if c is None or not c.is_active:
            continue

        # Scope of banner itself
        banner_scope = "system" if row.shop_id is None else "shop"

        # Optional filtering for shop page:
        # include system banners + shop banner for same shop
        if requested_shop is not None:
            if row.shop_id is not None and row.shop_id != requested_shop.id:
                continue
            if row.shop_id is None and not _collection_has_shop_products(c.id, requested_shop.id):
                continue

        # Collection scope inferred by shop_collection links
        collection_scope = "shop" if session.query(shop_collection).filter(shop_collection.collection_id == c.id).first() else "system"

        items.append(
            {
                "id": row.id,
                "display_id": row.display_id,
                "title": row.title,
                "subtitle": row.subtitle,
                "background_color": row.background_color,
                "text_color": row.text_color,
                "is_active": row.is_active,
                "collection_id": c.id,
                "collection_name": c.name,
                "collection_scope": collection_scope,
                "banner_scope": banner_scope,
                "shop_id": row.shop_id,
                "target": f"/sarees?collection_id={c.id}",
            }
        )

    return {"items": items}


@announcements_router.get("/by-collection/{collection_id}")
def get_announcement_for_collection(
    collection_id: int,
    shop_display_id: Optional[str] = None,
    session: Session = Depends(get_session),
):
    target_shop_id = None
    if shop_display_id:
        target_shop = session.query(shop).filter(shop.display_id == shop_display_id).first()
        if not target_shop:
            return {"item": None}
        target_shop_id = target_shop.id

    q = session.query(announcement_banner).filter(announcement_banner.collection_id == collection_id)
    if target_shop_id is None:
        q = q.filter(announcement_banner.shop_id.is_(None))
    else:
        q = q.filter(announcement_banner.shop_id == target_shop_id)

    row = q.first()
    if not row:
        return {"item": None}

    return {
        "item": {
            "id": row.id,
            "display_id": row.display_id,
            "title": row.title,
            "subtitle": row.subtitle,
            "background_color": row.background_color,
            "text_color": row.text_color,
            "is_active": row.is_active,
            "collection_id": row.collection_id,
            "shop_id": row.shop_id,
        }
    }


@announcements_router.post("/upsert")
def upsert_announcement(
    payload: AnnouncementUpsertRequest,
    request: Request,
    session: Session = Depends(get_session),
):
    current_user = getattr(request.state, "current_user", None)
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    role = getattr(current_user, "role", None)
    role_val = role.value if hasattr(role, "value") else role
    is_admin = str(role_val).lower() == "admin" if role_val else False

    c = session.query(collection).filter(collection.id == payload.collection_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Collection not found")

    target_shop_id = None
    sc_links = session.query(shop_collection).filter(shop_collection.collection_id == payload.collection_id).all()
    is_shop_collection = len(sc_links) > 0

    if is_admin:
        if payload.shop_display_id:
            target_shop = session.query(shop).filter(shop.display_id == payload.shop_display_id).first()
            if not target_shop:
                raise HTTPException(status_code=400, detail="Invalid shop_display_id")
            target_shop_id = target_shop.id
            # Admin shop banner must use a shop-scoped collection bound to that shop.
            if not is_shop_collection:
                raise HTTPException(status_code=400, detail="Shop banners can only be created from shop collections")
            if not any(link.shop_id == target_shop_id for link in sc_links):
                raise HTTPException(status_code=400, detail="Selected collection is not bound to the target shop")
        else:
            # Admin system banner must use a system collection.
            if is_shop_collection:
                raise HTTPException(status_code=400, detail="System banners can only be created from system collections")
            target_shop_id = None
    else:
        owner_shop = session.query(shop).filter(shop.owner_id == current_user.id).first()
        if not owner_shop:
            raise HTTPException(status_code=403, detail="Only shop owners or admins can manage announcements")
        # Vendor can create banners only from own shop collections.
        if not is_shop_collection:
            raise HTTPException(status_code=403, detail="Vendors can create banners only from their shop collections")
        if not any(link.shop_id == owner_shop.id for link in sc_links):
            raise HTTPException(status_code=403, detail="Collection does not belong to your shop")
        target_shop_id = owner_shop.id

    now = datetime.now()

    q = session.query(announcement_banner).filter(announcement_banner.collection_id == payload.collection_id)
    if target_shop_id is None:
        q = q.filter(announcement_banner.shop_id.is_(None))
    else:
        q = q.filter(announcement_banner.shop_id == target_shop_id)

    existing = q.first()

    if existing:
        existing.title = payload.title.strip()
        existing.subtitle = payload.subtitle.strip() if payload.subtitle else None
        existing.background_color = payload.background_color.strip() or "#F43F5E"
        existing.text_color = payload.text_color.strip() or "#FFFFFF"
        existing.is_active = payload.is_active
        existing.updated_at = now
        obj = existing
    else:
        obj = announcement_banner(
            collection_id=payload.collection_id,
            shop_id=target_shop_id,
            title=payload.title.strip(),
            subtitle=payload.subtitle.strip() if payload.subtitle else None,
            background_color=payload.background_color.strip() or "#F43F5E",
            text_color=payload.text_color.strip() or "#FFFFFF",
            is_active=payload.is_active,
            created_at=now,
            updated_at=now,
        )
        session.add(obj)

    session.commit()
    session.refresh(obj)

    return {
        "message": "Announcement saved",
        "item": {
            "id": obj.id,
            "display_id": obj.display_id,
            "collection_id": obj.collection_id,
            "shop_id": obj.shop_id,
            "title": obj.title,
            "subtitle": obj.subtitle,
            "background_color": obj.background_color,
            "text_color": obj.text_color,
            "is_active": obj.is_active,
        },
    }


@announcements_router.delete("/by-collection/{collection_id}")
def delete_announcement_by_collection(
    collection_id: int,
    request: Request,
    shop_display_id: Optional[str] = None,
    session: Session = Depends(get_session),
):
    current_user = getattr(request.state, "current_user", None)
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    role = getattr(current_user, "role", None)
    role_val = role.value if hasattr(role, "value") else role
    is_admin = str(role_val).lower() == "admin" if role_val else False

    target_shop_id = None
    if is_admin:
        if shop_display_id:
            s = session.query(shop).filter(shop.display_id == shop_display_id).first()
            if not s:
                raise HTTPException(status_code=400, detail="Invalid shop_display_id")
            target_shop_id = s.id
    else:
        owner_shop = session.query(shop).filter(shop.owner_id == current_user.id).first()
        if not owner_shop:
            raise HTTPException(status_code=403, detail="Only shop owners or admins can delete announcements")
        target_shop_id = owner_shop.id

    q = session.query(announcement_banner).filter(announcement_banner.collection_id == collection_id)
    if target_shop_id is None:
        q = q.filter(announcement_banner.shop_id.is_(None))
    else:
        q = q.filter(announcement_banner.shop_id == target_shop_id)

    row = q.first()
    if row:
        session.delete(row)
        session.commit()

    return {"message": "Announcement removed"}
