import secrets
from datetime import datetime, timedelta
from typing import Iterable, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from db.database import get_session
from db.db_models import (
    anonymous_visitor,
    attribute_definition,
    attribute_option,
    attribute_view,
    collection,
    collection_product,
    product,
    shop,
    shop_collection,
    unique_visit,
    UserRole,
)

VISITOR_TOKEN_HEADER = "X-Visitor-Token"


def _get_role_value(role: Optional[UserRole] | Optional[str]) -> Optional[str]:
    if role is None:
        return None
    if hasattr(role, "value"):
        return str(role.value)
    return str(role)


def should_track_visit(current_user) -> bool:
    role = _get_role_value(getattr(current_user, "role", None))
    return role is None or role.lower() == "user"


def _generate_visitor_token() -> str:
    return secrets.token_hex(32)


def resolve_visitor(session: Session, request: Request, response: Optional[Response] = None):
    token = request.headers.get(VISITOR_TOKEN_HEADER)
    if isinstance(token, str):
        token = token.strip()

    user_agent = request.headers.get("user-agent") or ""
    is_ssr = isinstance(user_agent, str) and "node" in user_agent.lower()

    if not token:
        if is_ssr:
            return None
        token = _generate_visitor_token()
        if response is not None:
            response.headers[VISITOR_TOKEN_HEADER] = token

    client_host = None
    if getattr(request, "client", None) is not None:
        try:
            client_host = request.client.host
        except Exception:
            client_host = None

    now = datetime.now()

    visitor = (
        session.query(anonymous_visitor)
        .filter(anonymous_visitor.visitor_token == token)
        .first()
    )

    if visitor:
        visitor.last_seen_at = now
        visitor.ip_address = client_host or visitor.ip_address
        visitor.user_agent = user_agent or visitor.user_agent
    else:
        visitor = anonymous_visitor(
            visitor_token=token,
            created_at=now,
            last_seen_at=now,
            ip_address=client_host,
            user_agent=user_agent,
            is_active=True,
        )
        session.add(visitor)
        session.flush()

    return visitor


def record_unique_visit(session: Session, visitor: anonymous_visitor, entity_type: str, entity_id: int):
    if visitor is None or entity_id is None:
        return

    now = datetime.now()
    existing = (
        session.query(unique_visit)
        .filter(
            unique_visit.visitor_id == visitor.id,
            unique_visit.entity_type == entity_type,
            unique_visit.entity_id == entity_id,
        )
        .first()
    )

    if existing:
        existing.last_viewed_at = now
    else:
        session.add(
            unique_visit(
                visitor_id=visitor.id,
                entity_type=entity_type,
                entity_id=entity_id,
                first_viewed_at=now,
                last_viewed_at=now,
                visit_count=1,
            )
        )


def record_attribute_views(
    session: Session,
    visitor: anonymous_visitor,
    entity_type: str,
    entity_id: int,
    attribute_rows: Iterable[tuple[int, int]],
):
    if visitor is None or entity_id is None:
        return

    now = datetime.now()
    unique_rows = set()
    for definition_id, option_id in attribute_rows:
        if definition_id is None or option_id is None:
            continue
        unique_rows.add((int(definition_id), int(option_id)))

    for definition_id, option_id in unique_rows:
        existing = (
            session.query(attribute_view)
            .filter(
                attribute_view.visitor_id == visitor.id,
                attribute_view.attribute_definition_id == definition_id,
                attribute_view.attribute_option_id == option_id,
                attribute_view.entity_type == entity_type,
                attribute_view.entity_id == entity_id,
            )
            .first()
        )
        if existing:
            existing.view_count = existing.view_count + 1
            existing.last_viewed_at = now
        else:
            session.add(
                attribute_view(
                    visitor_id=visitor.id,
                    attribute_definition_id=definition_id,
                    attribute_option_id=option_id,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    first_viewed_at=now,
                    last_viewed_at=now,
                    view_count=1,
                )
            )


def track_entity_view(
    session: Session,
    request: Request,
    response: Response,
    entity_type: str,
    entity_id: int,
    attribute_rows: Optional[Iterable[tuple[int, int]]] = None,
):
    current_user = getattr(request.state, "current_user", None)
    if not should_track_visit(current_user):
        return

    visitor = resolve_visitor(session, request, response)
    if visitor is None:
        return

    record_unique_visit(session, visitor, entity_type, entity_id)
    if attribute_rows:
        record_attribute_views(session, visitor, entity_type, entity_id, attribute_rows)
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise


def get_entity_view_counts(session: Session, entity_type: str, entity_ids: list[int]) -> dict[int, int]:
    if not entity_ids:
        return {}
    rows = (
        session.query(unique_visit.entity_id, func.coalesce(func.sum(unique_visit.visit_count), 0))
        .filter(unique_visit.entity_type == entity_type, unique_visit.entity_id.in_(entity_ids))
        .group_by(unique_visit.entity_id)
        .all()
    )
    return {int(entity_id): int(view_count) for entity_id, view_count in rows}


analytics_router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def _get_period_config(period: Optional[str]) -> dict:
    normalized = (period or "all").lower()
    if normalized == "all" or normalized == "entire" or normalized == "entire-duration" or normalized == "all-time":
        return {"days": None, "bucket_label": "all", "item_count": None}

    presets = {
        "week": {"days": 7, "bucket_label": "day", "item_count": 7},
        "month": {"days": 30, "bucket_label": "day", "item_count": 30},
        "quarter": {"days": 90, "bucket_label": "day", "item_count": 90},
        "halfyear": {"days": 180, "bucket_label": "day", "item_count": 180},
        "year": {"days": 365, "bucket_label": "day", "item_count": 365},
    }
    return presets.get(normalized, presets["month"]) 


def _parse_date_value(value: Optional[str], default: Optional[datetime] = None) -> Optional[datetime]:
    if not value:
        return default
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return default


def _resolve_start_datetime(
    period: str,
    month: Optional[str] = None,
    year: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> Optional[datetime]:
    normalized = (period or "all").lower()
    if normalized in {"all", "entire", "entire-duration", "all-time"}:
        return None

    if normalized == "custom":
        return _parse_date_value(from_date, datetime.now().replace(hour=0, minute=0, second=0, microsecond=0))

    now = datetime.now()
    if normalized == "month":
        month_value = month or now.strftime("%Y-%m")
        try:
            month_year, month_num = month_value.split("-", 1)
            return datetime(int(month_year), int(month_num), 1)
        except ValueError:
            return datetime(now.year, now.month, 1)

    if normalized == "year":
        target_year = year or str(now.year)
        try:
            return datetime(int(target_year), 1, 1)
        except ValueError:
            return datetime(now.year, 1, 1)

    config = _get_period_config(period)
    days = config.get("days")
    if days is None:
        return None
    return now - timedelta(days=int(days) - 1)


def _resolve_end_datetime(
    period: str,
    month: Optional[str] = None,
    year: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> Optional[datetime]:
    normalized = (period or "all").lower()
    if normalized in {"all", "entire", "entire-duration", "all-time"}:
        return None

    if normalized == "custom":
        end_value = _parse_date_value(to_date, datetime.now())
        if end_value is None:
            return None
        return end_value.replace(hour=23, minute=59, second=59, microsecond=999999)

    now = datetime.now()
    if normalized == "month":
        month_value = month or now.strftime("%Y-%m")
        try:
            month_year, month_num = month_value.split("-", 1)
            end_of_month = (datetime(int(month_year), int(month_num) + 1, 1) - timedelta(days=1)) if int(month_num) < 12 else datetime(int(month_year), 12, 31)
            return end_of_month.replace(hour=23, minute=59, second=59, microsecond=999999)
        except ValueError:
            return now.replace(hour=23, minute=59, second=59, microsecond=999999)

    if normalized == "year":
        target_year = year or str(now.year)
        try:
            return datetime(int(target_year), 12, 31, 23, 59, 59, 999999)
        except ValueError:
            return now.replace(hour=23, minute=59, second=59, microsecond=999999)

    config = _get_period_config(period)
    if config.get("days") is None:
        return None
    return now.replace(hour=23, minute=59, second=59, microsecond=999999)


def _get_shop_entity_ids(session: Session, shop_id: int) -> tuple[list[int], list[int]]:
    product_ids = [
        row[0]
        for row in session.query(product.id)
        .filter(product.shop_id == shop_id, product.is_active.is_(True))
        .all()
    ]

    collection_ids = set()
    collection_ids.update(
        row[0]
        for row in session.query(collection.id)
        .join(shop_collection, shop_collection.collection_id == collection.id)
        .filter(shop_collection.shop_id == shop_id, collection.is_active.is_(True))
        .all()
    )
    collection_ids.update(
        row[0]
        for row in session.query(collection.id)
        .join(collection_product, collection_product.collection_id == collection.id)
        .join(product, product.id == collection_product.product_id)
        .filter(product.shop_id == shop_id, collection.is_active.is_(True))
        .all()
    )
    return product_ids, sorted(collection_ids)


def _authorize_shop_access(session: Session, request: Request, display_id: str):
    current_user = getattr(request.state, "current_user", None)
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    selected_shop = session.query(shop).filter(shop.display_id == display_id).first()
    if selected_shop is None:
        raise HTTPException(status_code=404, detail="Shop not found")

    is_admin = current_user.role == UserRole.ADMIN
    is_owner = current_user.role == UserRole.SHOP_OWNER and selected_shop.owner_id == current_user.id
    if not is_admin and not is_owner:
        raise HTTPException(status_code=403, detail="Not authorized to access this shop analytics")

    return selected_shop


class HomepageVisitResponse(BaseModel):
    success: bool
    message: str
    entity_type: str
    entity_id: int


class ShopAnalyticsSummaryResponse(BaseModel):
    shop_display_id: str
    shop_name: str
    period: str
    total_shop_views: int
    total_product_views: int
    total_collection_views: int
    product_count: int
    collection_count: int
    unique_visitor_count: int


class ShopAnalyticsTimelinePoint(BaseModel):
    date: str
    shop_views: int
    product_views: int
    collection_views: int
    total_views: int


class ShopAnalyticsTopItem(BaseModel):
    name: str
    value: str
    view_count: int


class ShopAnalyticsResponse(BaseModel):
    shop_display_id: str
    shop_name: str
    period: str
    summary: ShopAnalyticsSummaryResponse
    timeline: list[ShopAnalyticsTimelinePoint]
    top_attributes: list[ShopAnalyticsTopItem]
    top_collections: list[ShopAnalyticsTopItem]


@analytics_router.post("/homepage/visit", response_model=HomepageVisitResponse)
def track_homepage_visit(request: Request, response: Response, session: Session = Depends(get_session)):
    track_entity_view(session, request, response, "homepage", 1)
    return HomepageVisitResponse(success=True, message="Homepage visit tracked", entity_type="homepage", entity_id=1)


@analytics_router.get("/shop/{display_id}", response_model=ShopAnalyticsResponse)
def get_shop_analytics(
    display_id: str,
    period: str = Query(default="all"),
    month: Optional[str] = Query(default=None),
    year: Optional[str] = Query(default=None),
    from_date: Optional[str] = Query(default=None),
    to_date: Optional[str] = Query(default=None),
    request: Request = None,
    session: Session = Depends(get_session),
):
    selected_shop = _authorize_shop_access(session, request, display_id)
    product_ids, collection_ids = _get_shop_entity_ids(session, selected_shop.id)

    start_datetime = _resolve_start_datetime(period, month=month, year=year, from_date=from_date, to_date=to_date)
    end_datetime = _resolve_end_datetime(period, month=month, year=year, from_date=from_date, to_date=to_date)

    def filter_by_period(query):
        if start_datetime is not None:
            query = query.filter(unique_visit.last_viewed_at >= start_datetime)
        if end_datetime is not None:
            query = query.filter(unique_visit.last_viewed_at <= end_datetime)
        return query

    def filter_attribute_by_period(query):
        if start_datetime is not None:
            query = query.filter(attribute_view.last_viewed_at >= start_datetime)
        if end_datetime is not None:
            query = query.filter(attribute_view.last_viewed_at <= end_datetime)
        return query

    total_shop_views = int(
        filter_by_period(
            session.query(func.coalesce(func.sum(unique_visit.visit_count), 0))
            .filter(
                unique_visit.entity_type == "shop",
                unique_visit.entity_id == selected_shop.id,
            )
        )
        .scalar()
        or 0
    )
    total_product_views = int(
        filter_by_period(
            session.query(func.coalesce(func.sum(unique_visit.visit_count), 0))
            .filter(unique_visit.entity_type == "product", unique_visit.entity_id.in_(product_ids))
        )
        .scalar()
        or 0
    ) if product_ids else 0
    collection_visit_query = (
        filter_by_period(
            session.query(func.coalesce(func.sum(unique_visit.visit_count), 0))
            .filter(
                unique_visit.entity_id.in_(collection_ids),
                unique_visit.entity_type.in_(["shop_collection", "collection"]),
            )
        )
    )
    total_collection_views = int(collection_visit_query.scalar() or 0) if collection_ids else 0

    shop_visitor_query = (
        session.query(unique_visit.visitor_id)
        .filter(unique_visit.entity_type == "shop", unique_visit.entity_id == selected_shop.id)
    )
    if start_datetime is not None:
        shop_visitor_query = shop_visitor_query.filter(unique_visit.last_viewed_at >= start_datetime)
    if end_datetime is not None:
        shop_visitor_query = shop_visitor_query.filter(unique_visit.last_viewed_at <= end_datetime)
    unique_visitor_count = int(shop_visitor_query.distinct().count())

    top_attributes = (
        filter_attribute_by_period(
            session.query(
                attribute_definition.attribute_name.label("attribute_name"),
                attribute_option.option_value.label("option_value"),
                func.sum(attribute_view.view_count).label("view_count"),
            )
            .join(attribute_option, attribute_option.id == attribute_view.attribute_option_id)
            .join(attribute_definition, attribute_definition.id == attribute_view.attribute_definition_id)
            .filter(
                attribute_view.entity_type == "product",
                attribute_view.entity_id.in_(product_ids),
            )
        )
        .group_by(attribute_definition.attribute_name, attribute_option.option_value)
        .order_by(func.sum(attribute_view.view_count).desc(), attribute_definition.attribute_name.asc())
        .limit(10)
        .all()
    )

    top_collections = (
        filter_by_period(
            session.query(
                collection.name.label("collection_name"),
                func.sum(unique_visit.visit_count).label("view_count"),
            )
            .join(collection, collection.id == unique_visit.entity_id)
            .filter(
                unique_visit.entity_type.in_(["shop_collection", "collection"]),
                unique_visit.entity_id.in_(collection_ids),
            )
        )
        .group_by(collection.name)
        .order_by(func.sum(unique_visit.visit_count).desc(), collection.name.asc())
        .limit(10)
        .all()
    )

    return ShopAnalyticsResponse(
        shop_display_id=selected_shop.display_id,
        shop_name=selected_shop.name,
        period=period.lower(),
        summary=ShopAnalyticsSummaryResponse(
            shop_display_id=selected_shop.display_id,
            shop_name=selected_shop.name,
            period=period.lower(),
            total_shop_views=total_shop_views,
            total_product_views=total_product_views,
            total_collection_views=total_collection_views,
            product_count=len(product_ids),
            collection_count=len(collection_ids),
            unique_visitor_count=unique_visitor_count,
        ),
        timeline=[],
        top_attributes=[
            ShopAnalyticsTopItem(name=row.attribute_name, value=row.option_value, view_count=int(row.view_count))
            for row in top_attributes
        ],
        top_collections=[
            ShopAnalyticsTopItem(name=row.collection_name, value="collection", view_count=int(row.view_count))
            for row in top_collections
        ],
    )


@analytics_router.get("/shop/{display_id}/summary")
def get_shop_analytics_summary(
    display_id: str,
    period: str = Query(default="all"),
    month: Optional[str] = Query(default=None),
    year: Optional[str] = Query(default=None),
    from_date: Optional[str] = Query(default=None),
    to_date: Optional[str] = Query(default=None),
    request: Request = None,
    session: Session = Depends(get_session),
):
    return get_shop_analytics(display_id, period=period, month=month, year=year, from_date=from_date, to_date=to_date, request=request, session=session).summary


@analytics_router.get("/shop/{display_id}/timeline")
def get_shop_analytics_timeline(
    display_id: str,
    period: str = Query(default="all"),
    month: Optional[str] = Query(default=None),
    year: Optional[str] = Query(default=None),
    from_date: Optional[str] = Query(default=None),
    to_date: Optional[str] = Query(default=None),
    request: Request = None,
    session: Session = Depends(get_session),
):
    return get_shop_analytics(display_id, period=period, month=month, year=year, from_date=from_date, to_date=to_date, request=request, session=session).timeline


@analytics_router.get("/shop/{display_id}/top-attributes")
def get_shop_top_attributes(
    display_id: str,
    period: str = Query(default="all"),
    month: Optional[str] = Query(default=None),
    year: Optional[str] = Query(default=None),
    from_date: Optional[str] = Query(default=None),
    to_date: Optional[str] = Query(default=None),
    request: Request = None,
    session: Session = Depends(get_session),
):
    return get_shop_analytics(display_id, period=period, month=month, year=year, from_date=from_date, to_date=to_date, request=request, session=session).top_attributes


@analytics_router.get("/shop/{display_id}/top-collections")
def get_shop_top_collections(
    display_id: str,
    period: str = Query(default="all"),
    month: Optional[str] = Query(default=None),
    year: Optional[str] = Query(default=None),
    from_date: Optional[str] = Query(default=None),
    to_date: Optional[str] = Query(default=None),
    request: Request = None,
    session: Session = Depends(get_session),
):
    return get_shop_analytics(display_id, period=period, month=month, year=year, from_date=from_date, to_date=to_date, request=request, session=session).top_collections
