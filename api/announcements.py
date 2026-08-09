from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from db.database import get_session
from db.db_models import (
    announcement_banner,
    announcement_banner_order,
    collection,
    shop,
    shop_collection,
    collection_shop,
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


class AnnouncementOrderRequest(BaseModel):
    banner_ids: list[int] = Field(..., min_items=1)
    shop_display_id: Optional[str] = None
    visibility: Optional[dict[str, bool]] = None


def _resolve_shop(session: Session, shop_display_id: Optional[str]):
    if not shop_display_id:
        return None
    return session.query(shop).filter(shop.display_id == shop_display_id).first()


def _collection_has_shop_products(session: Session, collection_id: int, shop_id: int) -> bool:
    canonical_count = (
        session.query(collection_product)
        .join(product, collection_product.product_id == product.id)
        .filter(collection_product.collection_id == collection_id, product.shop_id == shop_id)
        .count()
    )
    if canonical_count > 0:
        return True

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


def _is_announcement_visible_for_shop(session: Session, announcement_row, collection_obj, shop_id: int) -> bool:
    if announcement_row.shop_id is not None:
        return announcement_row.shop_id == shop_id
    return _collection_has_shop_products(session, announcement_row.collection_id, shop_id)


@announcements_router.get("")
def list_announcements(
    shop_display_id: Optional[str] = None,
    include_inactive: bool = False,
    include_hidden: bool = False,
    session: Session = Depends(get_session),
):
    q = session.query(announcement_banner)
    if not include_inactive:
        q = q.filter(announcement_banner.is_active == True)  # noqa: E712

    rows = q.order_by(announcement_banner.updated_at.desc()).all()

    requested_shop = _resolve_shop(session, shop_display_id)
    requested_shop_id = requested_shop.id if requested_shop else None

    order_map = {}
    banner_ids = [row.id for row in rows]
    if banner_ids:
        order_query = session.query(announcement_banner_order).filter(
            announcement_banner_order.announcement_banner_id.in_(banner_ids)
        )
        if requested_shop_id is None:
            order_query = order_query.filter(announcement_banner_order.shop_id.is_(None))
        else:
            order_query = order_query.filter(
                or_(announcement_banner_order.shop_id == requested_shop_id, announcement_banner_order.shop_id.is_(None))
            )

        for order_row in order_query.all():
            if order_row.shop_id == requested_shop_id:
                order_map[order_row.announcement_banner_id] = {
                    "position": order_row.position,
                    "is_visible": order_row.is_visible,
                }
            elif order_row.shop_id is None and order_row.announcement_banner_id not in order_map:
                order_map[order_row.announcement_banner_id] = {
                    "position": order_row.position,
                    "is_visible": order_row.is_visible,
                }

    items = []
    sorted_items = []
    shop_collection_map = {r.collection_id for r in rows if r.shop_id == requested_shop_id}

    for row in rows:
        c = session.query(collection).filter(collection.id == row.collection_id).first()
        if c is None or not c.is_active:
            continue

        if requested_shop_id is not None:
            if row.shop_id is not None and row.shop_id != requested_shop_id:
                continue

            # system banner is eligible only if the shop has allowed access to the underlying collection
            if row.shop_id is None:
                allowed_shop_ids = [
                    s.shop_id
                    for s in session.query(collection_shop).filter(collection_shop.collection_id == c.id).all()
                ]
                if allowed_shop_ids and requested_shop_id not in allowed_shop_ids:
                    continue

            if not _collection_has_shop_products(session, c.id, requested_shop_id):
                continue
            if row.shop_id is None and row.collection_id in shop_collection_map:
                continue

        banner_scope = "system" if row.shop_id is None else "shop"
        collection_scope = "shop" if session.query(shop_collection).filter(shop_collection.collection_id == c.id).first() else "system"
        order_data = order_map.get(row.id)
        position = order_data["position"] if isinstance(order_data, dict) else order_data
        is_visible = True
        if isinstance(order_data, dict) and order_data.get("is_visible") is not None:
            is_visible = order_data["is_visible"]

        if requested_shop_id is not None and row.shop_id is None and not is_visible and not include_hidden:
            continue

        sorted_items.append(
            (
                position if position is not None else 999999,
                row.updated_at,
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
                    "position": position,
                    "is_visible_in_shop": is_visible if requested_shop_id is not None else None,
                },
            )
        )

    sorted_items.sort(key=lambda item: (item[0] == 999999, item[0], -item[1].timestamp()))
    items = [entry[2] for entry in sorted_items]

    return {"items": items}


@announcements_router.post("/order")
def order_announcements(
    payload: AnnouncementOrderRequest,
    request: Request,
    session: Session = Depends(get_session),
):
    current_user = getattr(request.state, "current_user", None)
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    role = getattr(current_user, "role", None)
    role_val = role.value if hasattr(role, "value") else role
    is_admin = str(role_val).lower() == "admin" if role_val else False

    if len(payload.banner_ids) != len(set(payload.banner_ids)):
        raise HTTPException(status_code=400, detail="banner_ids must contain unique values")

    target_shop = _resolve_shop(session, payload.shop_display_id)
    target_shop_id = target_shop.id if target_shop else None

    if payload.shop_display_id and not target_shop:
        raise HTTPException(status_code=400, detail="Invalid shop_display_id")

    if not is_admin and target_shop_id is None:
        raise HTTPException(status_code=403, detail="Only admins can update global banner order")

    if not is_admin and target_shop_id is not None:
        owner_shop = session.query(shop).filter(shop.owner_id == current_user.id).first()
        if not owner_shop or owner_shop.id != target_shop_id:
            raise HTTPException(status_code=403, detail="Only shop owners can update their own shop banner order")

    banner_rows = session.query(announcement_banner).filter(announcement_banner.id.in_(payload.banner_ids)).all()
    banner_by_id = {row.id: row for row in banner_rows}
    if len(banner_rows) != len(payload.banner_ids):
        raise HTTPException(status_code=400, detail="One or more banner_ids are invalid")

    if target_shop_id is None:
        for row in banner_rows:
            if row.shop_id is not None:
                raise HTTPException(status_code=400, detail="Global banner order can only include system banners")
    else:
        visible_ids = set(
            r.id
            for r in session.query(announcement_banner).all()
            if _is_announcement_visible_for_shop(session, r, session.query(collection).filter(collection.id == r.collection_id).first(), target_shop_id)
        )
        if not set(payload.banner_ids).issubset(visible_ids):
            raise HTTPException(status_code=400, detail="One or more banners are not visible for the selected shop")

    existing_orders = session.query(announcement_banner_order).filter(
        announcement_banner_order.announcement_banner_id.in_(payload.banner_ids)
    )
    if target_shop_id is None:
        existing_orders = existing_orders.filter(announcement_banner_order.shop_id.is_(None))
    else:
        existing_orders = existing_orders.filter(announcement_banner_order.shop_id == target_shop_id)

    existing_map = {row.announcement_banner_id: row for row in existing_orders.all()}
    now = datetime.now()

    visibility_map = payload.visibility or {}
    for index, banner_id in enumerate(payload.banner_ids):
        visibility_value = None
        if str(banner_id) in visibility_map:
            visibility_value = visibility_map[str(banner_id)]
        elif banner_id in visibility_map:
            visibility_value = visibility_map[banner_id]

        if banner_id in existing_map:
            row = existing_map[banner_id]
            row.position = index
            if visibility_value is not None:
                row.is_visible = visibility_value
            row.updated_at = now
        else:
            row_visibility = visibility_value if visibility_value is not None else True
            session.add(
                announcement_banner_order(
                    announcement_banner_id=banner_id,
                    shop_id=target_shop_id,
                    position=index,
                    is_visible=row_visibility,
                    created_at=now,
                    updated_at=now,
                )
            )

    session.commit()
    return {"message": "Banner order saved"}


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
        q = q.filter(or_(announcement_banner.shop_id == target_shop_id, announcement_banner.shop_id.is_(None)))
        q = q.order_by(announcement_banner.shop_id.desc())

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
        if not is_shop_collection:
            raise HTTPException(status_code=403, detail="Vendors cannot create or update banners for system collections")
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
