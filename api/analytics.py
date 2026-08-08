import secrets
from datetime import datetime
from typing import Iterable, Optional

from fastapi import Request, Response
from sqlalchemy.orm import Session

from db.db_models import anonymous_visitor, attribute_view, unique_visit, UserRole

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
