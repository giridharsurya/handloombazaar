from datetime import datetime
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from db.database import get_session
from db.db_models import attribute_definition, attribute_option, collection, shop, UserRole


def require_admin(request: Request):
    current_user = getattr(request.state, "current_user", None)
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


admin_router = APIRouter(prefix="/api/admin", tags=["Admin"], dependencies=[Depends(require_admin)])


def _slugify(value: str) -> str:
    return "-".join(value.strip().lower().split())


def _build_display_id(prefix: str, source: str) -> str:
    safe_source = _slugify(source) or "item"
    return f"{prefix}-{safe_source}-{int(datetime.now().timestamp())}"


def _short_display_id() -> str:
    return str(uuid.uuid4().hex)[:8]


class CollectionCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=1000)


class AttributeCreateRequest(BaseModel):
    attribute_name: str = Field(min_length=1, max_length=255)
    options: list[str] = Field(default_factory=list)
    is_filterable: bool = False
    is_required: bool = False
    display_id: Optional[str] = Field(default=None, min_length=1, max_length=255)


class CollectionUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=1000)
    is_active: Optional[bool] = None


class AttributeUpdateRequest(BaseModel):
    attribute_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    is_filterable: Optional[bool] = None
    is_required: Optional[bool] = None


class AttributeOptionUpdateRequest(BaseModel):
    option_value: Optional[str] = Field(default=None, min_length=1, max_length=255)


@admin_router.get("/shops")
def get_shops(session: Session = Depends(get_session)):
    rows = session.query(shop).order_by(shop.created_at.desc()).all()
    return {
        "items": [
            {
                "id": row.id,
                "name": row.name,
                "email": row.email,
                "is_active": row.is_active,
                "created_at": row.created_at,
            }
            for row in rows
        ]
    }


@admin_router.get("/shops/pending")
def get_pending_shops(session: Session = Depends(get_session)):
    rows = (
        session.query(shop)
        .filter(shop.approved.is_(False))
        .order_by(shop.created_at.desc())
        .all()
    )
    return {
        "items": [
            {
                "id": row.id,
                "name": row.name,
                "email": row.email,
                "created_at": row.created_at,
            }
            for row in rows
        ]
    }


@admin_router.post("/shops/{shop_id}/approve")
def approve_shop(shop_id: int, session: Session = Depends(get_session)):
    selected_shop = session.query(shop).filter(shop.id == shop_id).first()
    if selected_shop is None:
        raise HTTPException(status_code=404, detail="Shop not found")

    selected_shop.is_active = True
    selected_shop.approved = True
    selected_shop.updated_at = datetime.now()
    session.commit()
    session.refresh(selected_shop)

    return {
        "message": "Shop approved successfully",
        "shop": {
            "id": selected_shop.id,
            "name": selected_shop.name,
            "is_active": selected_shop.is_active,
            "approved": selected_shop.approved,
        },
    }


@admin_router.post("/shops/{shop_id}/reject")
def reject_shop(shop_id: int, session: Session = Depends(get_session)):
    selected_shop = session.query(shop).filter(shop.id == shop_id).first()
    if selected_shop is None:
        raise HTTPException(status_code=404, detail="Shop not found")

    selected_shop.is_active = False
    selected_shop.approved = False
    selected_shop.updated_at = datetime.now()
    session.commit()
    session.refresh(selected_shop)

    return {
        "message": "Shop rejected successfully",
        "shop": {
            "id": selected_shop.id,
            "name": selected_shop.name,
            "is_active": selected_shop.is_active,
            "approved": selected_shop.approved,
        },
    }


@admin_router.post("/collections")
def create_collection(payload: CollectionCreateRequest, session: Session = Depends(get_session)):
    now = datetime.now()
    
    display_id_candidate = _short_display_id()

    # # Ensure uniqueness for generated IDs
    # if not payload.display_id:
    while session.query(collection).filter(collection.display_id == display_id_candidate).first():
        display_id_candidate = _short_display_id()

    collection_row = collection(
        name=payload.name.strip(),
        description=payload.description.strip() if payload.description else None,
        display_id=display_id_candidate,
        created_at=now,
        updated_at=now,
        is_active=True,
    )

    session.add(collection_row)
    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=f"Failed to create collection: {exc}")

    session.refresh(collection_row)
    return {
        "message": "Collection created successfully",
        "collection": {
            "id": collection_row.id,
            "name": collection_row.name,
            "display_id": collection_row.display_id,
        },
    }


@admin_router.post("/attributes")
def create_attribute(payload: AttributeCreateRequest, session: Session = Depends(get_session)):
    now = datetime.now()
    display_id = _short_display_id()

    definition_row = attribute_definition(
        attribute_name=payload.attribute_name.strip(),
        is_filterable=payload.is_filterable,
        is_required=payload.is_required,
        created_at=now,
        updated_at=now,
    )
    session.add(definition_row)

    try:
        session.flush()
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=f"Failed to create attribute: {exc}")

    options_to_insert = []
    seen_options = set()

    for option in payload.options:
        normalized = option.strip()
        lowered = normalized.lower()
        if not normalized or lowered in seen_options:
            continue
        seen_options.add(lowered)
        options_to_insert.append(
            attribute_option(
                attribute_definition_id=definition_row.id,
                option_value=normalized,
                created_at=now,
                updated_at=now,
            )
        )

    for option_row in options_to_insert:
        session.add(option_row)

    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=f"Failed to save attribute options: {exc}")

    session.refresh(definition_row)
    return {
        "message": "Attribute created successfully",
        "attribute": {
            "id": definition_row.id,
            "name": definition_row.attribute_name,
            "display_id": definition_row.display_id,
            "is_filterable": definition_row.is_filterable,
            "is_required": definition_row.is_required,
            "options_count": len(options_to_insert),
        },
    }


@admin_router.get("/collections")
def get_collections(session: Session = Depends(get_session)):
    rows = session.query(collection).order_by(collection.created_at.desc()).all()
    return {
        "items": [
            {
                "id": row.id,
                "name": row.name,
                "description": row.description,
                "display_id": row.display_id,
                "is_active": row.is_active,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
            for row in rows
        ]
    }


@admin_router.put("/collections/{collection_id}")
def update_collection(
    collection_id: int,
    payload: CollectionUpdateRequest,
    session: Session = Depends(get_session),
):
    selected_collection = session.query(collection).filter(collection.id == collection_id).first()
    if selected_collection is None:
        raise HTTPException(status_code=404, detail="Collection not found")

    if payload.name is not None:
        selected_collection.name = payload.name.strip()
    if payload.description is not None:
        selected_collection.description = payload.description.strip() or None
    if payload.is_active is not None:
        selected_collection.is_active = payload.is_active

    selected_collection.updated_at = datetime.now()
    session.commit()
    session.refresh(selected_collection)

    return {
        "message": "Collection updated successfully",
        "collection": {
            "id": selected_collection.id,
            "name": selected_collection.name,
            "description": selected_collection.description,
            "is_active": selected_collection.is_active,
        },
    }


@admin_router.get("/attributes")
def get_attributes(session: Session = Depends(get_session)):
    rows = session.query(attribute_definition).order_by(attribute_definition.created_at.desc()).all()
    result = []
    for row in rows:
        options_rows = (
            session.query(attribute_option)
            .filter(attribute_option.attribute_definition_id == row.id)
            .order_by(attribute_option.created_at.asc())
            .all()
        )
        result.append(
            {
                "id": row.id,
                "name": row.attribute_name,
                "display_id": row.display_id,
                "is_filterable": row.is_filterable,
                "is_required": row.is_required,
                "is_active": row.is_active,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
                "options": [
                    {
                        "id": opt.id,
                        "value": opt.option_value,
                        "display_id": opt.display_id,
                        "created_at": opt.created_at,
                    }
                    for opt in options_rows
                ],
            }
        )
    return {"items": result}


@admin_router.put("/attributes/{attribute_id}")
def update_attribute(
    attribute_id: int,
    payload: AttributeUpdateRequest,
    session: Session = Depends(get_session),
):
    selected_attribute = (
        session.query(attribute_definition).filter(attribute_definition.id == attribute_id).first()
    )
    if selected_attribute is None:
        raise HTTPException(status_code=404, detail="Attribute not found")

    if payload.attribute_name is not None:
        selected_attribute.attribute_name = payload.attribute_name.strip()
    if payload.is_filterable is not None:
        selected_attribute.is_filterable = payload.is_filterable
    if payload.is_required is not None:
        selected_attribute.is_required = payload.is_required

    selected_attribute.updated_at = datetime.now()
    session.commit()
    session.refresh(selected_attribute)

    return {
        "message": "Attribute updated successfully",
        "attribute": {
            "id": selected_attribute.id,
            "name": selected_attribute.attribute_name,
            "is_filterable": selected_attribute.is_filterable,
            "is_required": selected_attribute.is_required,
        },
    }


@admin_router.put("/attributes/{attribute_id}/options/{option_id}")
def update_attribute_option(
    attribute_id: int,
    option_id: int,
    payload: AttributeOptionUpdateRequest,
    session: Session = Depends(get_session),
):
    selected_option = (
        session.query(attribute_option)
        .filter(
            attribute_option.id == option_id,
            attribute_option.attribute_definition_id == attribute_id,
        )
        .first()
    )
    if selected_option is None:
        raise HTTPException(status_code=404, detail="Option not found")

    if payload.option_value is not None:
        selected_option.option_value = payload.option_value.strip()

    selected_option.updated_at = datetime.now()
    session.commit()
    session.refresh(selected_option)

    return {
        "message": "Option updated successfully",
        "option": {
            "id": selected_option.id,
            "value": selected_option.option_value,
        },
    }

@admin_router.delete("/attributes/{attribute_id}/options/{option_id}")
def delete_attribute_option(
    attribute_id: int,
    option_id: int,
    session: Session = Depends(get_session),
):
    selected_option = (
        session.query(attribute_option)
        .filter(
            attribute_option.id == option_id,
            attribute_option.attribute_definition_id == attribute_id,
        )
        .first()
    )
    if selected_option is None:
        raise HTTPException(status_code=404, detail="Option not found")

    # Database CASCADE will automatically delete related product_attribute records
    session.delete(selected_option)
    session.commit()

    return {"message": "Option and associated product attributes deleted successfully"}

@admin_router.delete("/collections/{collection_id}")
def delete_collection(collection_id: int, session: Session = Depends(get_session)):
    selected_collection = session.query(collection).filter(collection.id == collection_id).first()
    if selected_collection is None:
        raise HTTPException(status_code=404, detail="Collection not found")

    session.delete(selected_collection)
    session.commit()

    return {"message": "Collection deleted successfully"}


@admin_router.put("/attributes/{attribute_id}/toggle")
def toggle_attribute_active(attribute_id: int, session: Session = Depends(get_session)):
    selected_attribute = (
        session.query(attribute_definition).filter(attribute_definition.id == attribute_id).first()
    )
    if selected_attribute is None:
        raise HTTPException(status_code=404, detail="Attribute not found")

    selected_attribute.is_active = not selected_attribute.is_active
    selected_attribute.updated_at = datetime.now()
    session.commit()
    session.refresh(selected_attribute)

    return {
        "message": "Attribute toggled successfully",
        "attribute": {
            "id": selected_attribute.id,
            "is_active": selected_attribute.is_active,
        },
    }


@admin_router.delete("/attributes/{attribute_id}")
def delete_attribute(attribute_id: int, session: Session = Depends(get_session)):
    selected_attribute = (
        session.query(attribute_definition).filter(attribute_definition.id == attribute_id).first()
    )
    if selected_attribute is None:
        raise HTTPException(status_code=404, detail="Attribute not found")

    # Database CASCADE will automatically delete:
    # - attribute_options (via FK constraint)
    # - product_attributes (via FK constraint)
    session.delete(selected_attribute)
    session.commit()

    return {"message": "Attribute and all associated data deleted successfully"}