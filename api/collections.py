from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from db.database import get_session
from db.db_models import (
    collection,
    collection_shop,
    shop_collection,
    collection_attribute_option,
    collection_product,
    shop,
    attribute_option,
    attribute_definition,
    product,
)


router = APIRouter(prefix="/api/collections", tags=["Collections"])


class CollectionCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=1000)
    kind: str = Field(default="shop")  # 'system' or 'shop'
    shop_display_id: Optional[str] = None
    allowed_shop_display_ids: List[str] = Field(default_factory=list)


class CollectionUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=1000)
    is_active: Optional[bool] = None


class ProductsModifyRequest(BaseModel):
    product_display_ids: List[str] = Field(default_factory=list)


class RequiredAttributeItem(BaseModel):
    definition_id: int
    option_ids: List[int] = Field(default_factory=list)


class ConstraintsUpdateRequest(BaseModel):
    allowed_shop_display_ids: List[str] = Field(default_factory=list)
    # New frontend format: required_attributes: [{definition_id, option_ids: [id]}]
    required_attributes: List[RequiredAttributeItem] = Field(default_factory=list)


@router.get("")
def list_collections(kind: Optional[str] = None, shop_display_id: Optional[str] = None, session: Session = Depends(get_session)):
    qs = session.query(collection)
    # Prefer explicit `kind` column on collection: 'system' or 'shop'.
    # If caller didn't provide a kind (admin page), default to system collections.
    k = (kind or "").strip().lower()

    # Two storage models:
    # - `collection` table stores system collections (may have constraints in `collection_shops`)
    # - `shop_collections` table stores shop-specific collections (each row belongs to a shop and may reference a collection)
    items = []

    if k == "system":
        # system collections = entries in `collection` that do NOT have a shop binding
        # i.e., skip any collection that has a shop_collection referencing it
        subq = session.query(shop_collection.collection_id).distinct()
        rows = session.query(collection).filter(~collection.id.in_(subq)).order_by(collection.created_at.desc()).all()
        for r in rows:
            item = {"id": r.id, "display_id": r.display_id, "name": r.name, "description": r.description, "is_active": r.is_active}
            # include linked constraint shops (collection_shops) when present
            shops = session.query(collection_shop).filter(collection_shop.collection_id == r.id).all()
            shop_display_ids = [s.shop_display_id for s in shops if s.shop_display_id]
            if shop_display_ids:
                item["shop_display_ids"] = shop_display_ids
            items.append(item)
        return {"items": items}

    # k == 'shop'
    # list shop collections from `shop_collections`. If `shop_display_id` provided, filter by that shop.
    from db.db_models import shop as shop_model

    # k == 'shop'
    # If a shop_display_id is provided, fetch collection_ids from shop_collections for that shop
    # and return the authoritative collection rows. If no shop_display_id provided, return
    # all shop-scoped collections (collections that have a shop_collection row), joining to
    # collection metadata when available.
    if shop_display_id:
        # find the shop id for the display id
        shop_row = session.query(shop_model).filter(shop_model.display_id == shop_display_id).first()
        if not shop_row:
            return {"items": []}
        col_ids = [r.collection_id for r in session.query(shop_collection.collection_id).filter(shop_collection.shop_id == shop_row.id).all()]
        if not col_ids:
            return {"items": []}
        rows = session.query(collection).filter(collection.id.in_(col_ids)).order_by(collection.created_at.desc()).all()
        for r in rows:
            item = {"id": r.id, "display_id": r.display_id, "name": r.name, "description": r.description, "is_active": r.is_active}
            items.append(item)
        return {"items": items}

    # No shop_display_id: return collections that are shop-scoped (have a shop_collection link).
    subq = session.query(shop_collection.collection_id).distinct()
    rows = session.query(collection).filter(collection.id.in_(subq)).order_by(collection.created_at.desc()).all()
    for r in rows:
        item = {"id": r.id, "display_id": r.display_id, "name": r.name, "description": r.description, "is_active": r.is_active}
        # include shop bindings (one or more)
        scs = session.query(shop_collection).filter(shop_collection.collection_id == r.id).all()
        if scs:
            item["shop_bindings"] = [{"shop_id": s.shop_id, "is_active": s.is_active} for s in scs]
        items.append(item)
    return {"items": items}


@router.post("/create")
def create_collection(payload: CollectionCreateRequest, request: Request, session: Session = Depends(get_session)):
    """Create a collection.
    - public: cannot create
    - vendor (shop owner): can create `kind=shop` collections only, bound to their shop
    - admin: can create `kind=system` or `kind=shop`; for `shop` must provide `shop_display_id`
    """
    # Resolve current user and role first
    current_user = getattr(request.state, "current_user", None)
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required to create collections")

    role = getattr(current_user, "role", None)
    role_val = role.value if hasattr(role, "value") else role
    is_admin = str(role_val).lower() == "admin" if role_val else False

    # Resolve vendor's shop (if the user is a shop owner)
    vendor_shop_row = None
    vendor_shop_display_id = None
    try:
        vendor_shop_row = session.query(shop).filter(shop.owner_id == current_user.id).first()
        if vendor_shop_row:
            vendor_shop_display_id = vendor_shop_row.display_id
    except Exception:
        vendor_shop_row = None

    # Validate kind
    kind = (payload.kind or "shop").lower()
    if kind not in ("system", "shop"):
        raise HTTPException(status_code=400, detail="Invalid kind; must be 'system' or 'shop'")

    # Authorization and target shop resolution
    target_shop_id = None
    target_shop_display_id = None

    if kind == "system":
        if not is_admin:
            raise HTTPException(status_code=403, detail="Only admin can create system collections")
    else:
        # kind == shop
        if is_admin:
            # Admin must provide shop_display_id for shop-scoped collections
            if not payload.shop_display_id:
                raise HTTPException(status_code=400, detail="admin must provide shop_display_id when creating shop collections")
            target = session.query(shop).filter(shop.display_id == payload.shop_display_id).first()
            if not target:
                raise HTTPException(status_code=400, detail="shop not found for provided shop_display_id")
            target_shop_id = target.id
            target_shop_display_id = target.display_id
        else:
            # Vendor: must own a shop and may not create collections for other shops
            if vendor_shop_display_id is None or vendor_shop_row is None:
                raise HTTPException(status_code=403, detail="Authenticated user is not a shop owner and cannot create shop collections")
            if payload.shop_display_id and payload.shop_display_id != vendor_shop_display_id:
                raise HTTPException(status_code=403, detail="Cannot create collections for another shop")
            target_shop_id = vendor_shop_row.id
            target_shop_display_id = vendor_shop_display_id

    now = datetime.now()
    created_obj = None

    if kind == "system":
        # create a system collection row
        c = collection(
            name=payload.name.strip(),
            description=payload.description.strip() if payload.description else None,
            created_at=now,
            updated_at=now,
            is_active=True,
        )
        session.add(c)
        session.flush()

        # persist allowed shop display ids as collection_shop constraints
        incoming_allowed = getattr(payload, "allowed_shop_display_ids", None)
        if isinstance(incoming_allowed, list) and incoming_allowed:
            for sdid in incoming_allowed:
                target_shop = session.query(shop).filter(shop.display_id == sdid).first()
                if not target_shop:
                    session.rollback()
                    raise HTTPException(status_code=400, detail=f"allowed_shop_display_id not found: {sdid}")
                shop_id_val = target_shop.id
                cs = collection_shop(
                    collection_id=c.id,
                    shop_id=shop_id_val,
                    shop_display_id=sdid,
                    created_at=now,
                    updated_at=now,
                )
                session.add(cs)

        created_obj = c
    else:
        # kind == shop: create a system `collection` row, then create a shop_collection
        # linking that collection to the target shop. This keeps collection metadata
        # centralized in `collection` while `shop_collection` records the shop binding.
        c = collection(
            name=payload.name.strip(),
            description=payload.description.strip() if payload.description else None,
            created_at=now,
            updated_at=now,
            is_active=True,
        )
        session.add(c)
        session.flush()

        # persist shop-specific link
        sc = shop_collection(
            collection_id=c.id,
            shop_id=target_shop_id,
            created_at=now,
            updated_at=now,
            is_active=True,
        )
        session.add(sc)
        session.flush()
        # return the shop_collection as the created object so the frontend continues
        # to operate using shop_collection ids for shop-scoped collections
        created_obj = sc

    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    # refresh the created object for returned values
    try:
        session.refresh(created_obj)
    except Exception:
        pass

    # normalize result
    # Canonical id everywhere is collection.id
    if kind == "system":
        result = {"id": created_obj.id, "display_id": getattr(created_obj, "display_id", None), "name": created_obj.name}
    else:
        # created_obj is a `shop_collection` instance; try to load the linked collection
        linked_name = None
        linked_display_id = None
        linked_id = getattr(created_obj, "collection_id", None)
        try:
            linked = None
            if getattr(created_obj, "collection_id", None):
                linked = session.query(collection).filter(collection.id == created_obj.collection_id).first()
            if linked:
                linked_id = linked.id
                linked_name = linked.name
                linked_display_id = getattr(linked, "display_id", None)
        except Exception:
            linked = None

        result = {"id": linked_id, "display_id": linked_display_id, "name": linked_name, "shop_display_id": target_shop_display_id}

    return {"message": "Collection created", "collection": result}


@router.put("/{collection_id}/update")
def update_collection(collection_id: int, payload: CollectionUpdateRequest, request: Request, session: Session = Depends(get_session)):
    # auth + RBAC: public cannot update; vendor can update only their shop-bound collections; admin can update any
    current_user = getattr(request.state, "current_user", None)
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required to update collections")

    role = getattr(current_user, "role", None)
    if hasattr(role, "value"):
        role_val = role.value
    else:
        role_val = role
    is_admin = str(role_val).lower() == "admin" if role_val else False

    # Canonical id is always collection.id
    c = session.query(collection).filter(collection.id == collection_id).first()
    if c is None:
        raise HTTPException(status_code=404, detail="Collection not found")

    # Determine scope by existence of shop_collection entry for this collection
    sc = session.query(shop_collection).filter(shop_collection.collection_id == collection_id).first()

    # System collection: only admin can update
    if sc is None:
        if not is_admin:
            raise HTTPException(status_code=403, detail="Only admin can update system collections")

        # perform update on system collection
        if payload.name is not None:
            c.name = payload.name.strip()
        if payload.description is not None:
            c.description = payload.description.strip() or None
        if payload.is_active is not None:
            c.is_active = payload.is_active
        c.updated_at = datetime.now()
        session.commit()
        session.refresh(c)
        return {"message": "Collection updated", "collection": {"id": c.id, "display_id": c.display_id, "name": c.name, "description": c.description, "is_active": c.is_active}}

    # Shop-scoped collection: admin or shop owner

    if not is_admin:
        vendor_shop_row = None
        try:
            vendor_shop_row = session.query(shop).filter(shop.owner_id == current_user.id).first()
        except Exception:
            vendor_shop_row = None

        if vendor_shop_row is None or sc.shop_id != vendor_shop_row.id:
            raise HTTPException(status_code=403, detail="Not authorized to update this collection")

    # Update authoritative collection metadata (name/description)
    if payload.name is not None:
        c.name = payload.name.strip()
    if payload.description is not None:
        c.description = payload.description.strip() or None
    c.updated_at = datetime.now()

    # Keep is_active synchronized for shop-scoped collections
    if payload.is_active is not None:
        c.is_active = payload.is_active
        sc.is_active = payload.is_active
    sc.updated_at = datetime.now()

    session.commit()
    # refresh both
    try:
        session.refresh(c)
    except Exception:
        pass
    try:
        session.refresh(sc)
    except Exception:
        pass

    # return shop-scoped response using canonical collection id
    return {"message": "Shop collection updated", "collection": {"id": c.id, "display_id": c.display_id, "name": c.name, "description": c.description, "is_active": sc.is_active, "shop_id": sc.shop_id}}


@router.delete("/{collection_id}/delete")
def delete_collection(collection_id: int, request: Request, session: Session = Depends(get_session)):
    # auth + RBAC: public cannot delete; vendor can delete only their shop-bound collections; admin can delete any
    current_user = getattr(request.state, "current_user", None)
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required to delete collections")

    role = getattr(current_user, "role", None)
    if hasattr(role, "value"):
        role_val = role.value
    else:
        role_val = role
    is_admin = str(role_val).lower() == "admin" if role_val else False

    # Canonical id is always collection.id
    c = session.query(collection).filter(collection.id == collection_id).first()
    if c is None:
        raise HTTPException(status_code=404, detail="Collection not found")

    # Determine scope by shop_collection links for this collection
    sc_rows = session.query(shop_collection).filter(shop_collection.collection_id == collection_id).all()
    is_shop_scoped = len(sc_rows) > 0

    # Authorization
    if is_shop_scoped and not is_admin:
        # resolve vendor's shop
        vendor_shop_row = None
        try:
            vendor_shop_row = session.query(shop).filter(shop.owner_id == current_user.id).first()
        except Exception:
            vendor_shop_row = None

        # vendor can delete only collections bound to their own shop
        if vendor_shop_row is None:
            raise HTTPException(status_code=403, detail="Not authorized to delete this collection")
        owns_all = all((s.shop_id == vendor_shop_row.id) for s in sc_rows)
        if not owns_all:
            raise HTTPException(status_code=403, detail="Not authorized to delete this collection")

    if not is_shop_scoped and not is_admin:
        # non-admin cannot delete system collections
        raise HTTPException(status_code=403, detail="Only admin can delete system collections")

    # Delete all dependent rows to avoid orphans, then delete collection
    session.query(shop_collection).filter(shop_collection.collection_id == collection_id).delete()
    session.query(collection_shop).filter(collection_shop.collection_id == collection_id).delete()
    session.query(collection_attribute_option).filter(collection_attribute_option.collection_id == collection_id).delete()
    session.query(collection_product).filter(collection_product.collection_id == collection_id).delete()
    session.delete(c)
    session.commit()
    return {"message": "Collection and related records deleted"}


@router.get("/{collection_id}/constraints")
def get_constraints(collection_id: int, request: Request, session: Session = Depends(get_session)):
    c = session.query(collection).filter(collection.id == collection_id).first()
    if c is None:
        raise HTTPException(status_code=404, detail="Collection not found")

    # require authentication: public cannot access constraints
    current_user = getattr(request.state, "current_user", None)
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required to view collection constraints")

    role = getattr(current_user, "role", None)
    if hasattr(role, "value"):
        role_val = role.value
    else:
        role_val = role
    is_admin = str(role_val).lower() == "admin" if role_val else False

    # load allowed-shops constraints and attribute constraints
    shops = (
        session.query(collection_shop)
        .filter(collection_shop.collection_id == collection_id)
        .all()
    )
    # shop-scoped collections are identified by shop_collection links
    sc_links = (
        session.query(shop_collection)
        .filter(shop_collection.collection_id == collection_id)
        .all()
    )
    attrs = (
        session.query(collection_attribute_option)
        .filter(collection_attribute_option.collection_id == collection_id)
        .all()
    )

    shop_display_ids = [s.shop_display_id for s in shops if s.shop_display_id]

    # Admin can access any collection (system or shop, active or inactive)
    if is_admin:
        # convert to frontend shape: required_attributes: [{definition_id, option_ids: [ids]}]
        req_map = {}
        for a in attrs:
            def_id = a.attribute_definition_id
            opt_id = a.attribute_option_id
            req_map.setdefault(def_id, []).append(opt_id)
        required_attributes = [{"definition_id": k, "option_ids": v} for k, v in req_map.items()]
        return {"allowed_shop_display_ids": shop_display_ids, "required_attributes": required_attributes}

    # Non-admin (vendor) access rules:
    # - vendor may access shop-bound collections only if they own the shop
    # - vendor may access system collections only if they are active and either not restricted to shops or the vendor's shop is in allowed_shops
    vendor_shop_row = None
    vendor_shop_display_id = None
    try:
        vendor_shop_row = session.query(shop).filter(shop.owner_id == current_user.id).first()
        if vendor_shop_row:
            vendor_shop_display_id = vendor_shop_row.display_id
    except Exception:
        vendor_shop_row = None

    # Determine if collection is shop-scoped (has shop_collection link)
    is_shop_bound = len(sc_links) > 0

    if is_shop_bound:
        # vendor may access only if they own one of the linked shop_collection shops
        if vendor_shop_row is None:
            raise HTTPException(status_code=403, detail="Not authorized to view this collection constraints")
        owns = any((s.shop_id == vendor_shop_row.id) for s in sc_links)
        if not owns:
            raise HTTPException(status_code=403, detail="Not authorized to view this collection constraints")
    else:
        # system collection: must be active
        if not c.is_active:
            raise HTTPException(status_code=403, detail="Not authorized to view this collection constraints")
        # if system collection has allowed shops restrictions, vendor must be in that list
        if shop_display_ids and vendor_shop_display_id:
            if vendor_shop_display_id not in shop_display_ids:
                raise HTTPException(status_code=403, detail="Not authorized to view this collection constraints")

    # return attribute constraints in frontend shape
    req_map = {}
    for a in attrs:
        def_id = a.attribute_definition_id
        opt_id = a.attribute_option_id
        req_map.setdefault(def_id, []).append(opt_id)
    required_attributes = [{"definition_id": k, "option_ids": v} for k, v in req_map.items()]
    return {"allowed_shop_display_ids": shop_display_ids, "required_attributes": required_attributes}


@router.put("/{collection_id}/constraints")
def update_constraints(collection_id: int, payload: ConstraintsUpdateRequest, request: Request, session: Session = Depends(get_session)):
    c = session.query(collection).filter(collection.id == collection_id).first()
    if c is None:
        raise HTTPException(status_code=404, detail="Collection not found")

    # auth + RBAC: public cannot update; vendor can update only their shop-bound collections; admin can update any
    current_user = getattr(request.state, "current_user", None)
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required to update collection constraints")

    role = getattr(current_user, "role", None)
    if hasattr(role, "value"):
        role_val = role.value
    else:
        role_val = role
    is_admin = str(role_val).lower() == "admin" if role_val else False

    # For vendors, allow updates only for shop-scoped collections they own
    if not is_admin:
        vendor_shop_row = None
        try:
            vendor_shop_row = session.query(shop).filter(shop.owner_id == current_user.id).first()
        except Exception:
            vendor_shop_row = None

        sc_links = session.query(shop_collection).filter(shop_collection.collection_id == collection_id).all()
        if not sc_links:
            # system collections cannot be modified by vendors
            raise HTTPException(status_code=403, detail="Only admin can update system collection constraints")

        if vendor_shop_row is None:
            raise HTTPException(status_code=403, detail="Not authorized to update collection constraints")

        owns = any((s.shop_id == vendor_shop_row.id) for s in sc_links)
        if not owns:
            raise HTTPException(status_code=403, detail="Not authorized to update collection constraints")

    now = datetime.now()

    # normalize incoming payload: support both frontend shape and API shape
    # frontend may send { allowed_shop_display_ids: [...], required_attributes: [{definition_id, option_ids: [id]}] }
    # canonical form: use allowed_shop_display_ids and required_attributes (numeric option ids)
    incoming_shop_ids = getattr(payload, "allowed_shop_display_ids", []) or []
    reqs = getattr(payload, "required_attributes", None) or []
    incoming_option_ids: List[int] = []
    if isinstance(reqs, list) and reqs:
        for r in reqs:
            if isinstance(r, dict):
                incoming_option_ids.extend(r.get("option_ids") or [])
            else:
                incoming_option_ids.extend(getattr(r, "option_ids", []) or [])

    # remove existing (update is idempotent; sending empty lists will remove constraints)
    session.query(collection_shop).filter(collection_shop.collection_id == collection_id).delete()
    session.query(collection_attribute_option).filter(collection_attribute_option.collection_id == collection_id).delete()

    # insert shops by display id when possible
    for sdid in incoming_shop_ids:
        s = session.query(shop).filter(shop.display_id == sdid).first()
        if not s:
            continue
        cs = collection_shop(
            collection_id=collection_id,
            shop_id=s.id,
            shop_display_id=s.display_id,
            created_at=now,
            updated_at=now,
        )
        session.add(cs)

    # insert attribute options by numeric id (incoming_option_ids)
    if incoming_option_ids:
        rows = session.query(attribute_option).filter(attribute_option.id.in_(incoming_option_ids)).all()
        for ao in rows:
            cao = collection_attribute_option(
                collection_id=collection_id,
                attribute_definition_id=ao.attribute_definition_id,
                attribute_option_id=ao.id,
                created_at=now,
                updated_at=now,
            )
            session.add(cao)

    session.commit()
    return {"message": "Constraints updated"}


@router.get("/{collection_id}/products")
def get_products(collection_id: int, request: Request, session: Session = Depends(get_session)):
    c = session.query(collection).filter(collection.id == collection_id).first()
    if c is None:
        raise HTTPException(status_code=404, detail="Collection not found")

    # system constraints from collection_shop
    shops = (
        session.query(collection_shop)
        .filter(collection_shop.collection_id == collection_id)
        .all()
    )
    # shop-scoped collections are identified by shop_collection links
    sc_links = (
        session.query(shop_collection)
        .filter(shop_collection.collection_id == collection_id)
        .all()
    )
    shop_display_ids = [s.shop_display_id for s in shops if s.shop_display_id]
    is_shop_bound = len(sc_links) > 0

    # determine requester and role
    current_user = getattr(request.state, "current_user", None)
    role = getattr(current_user, "role", None) if current_user is not None else None
    if hasattr(role, "value"):
        role_val = role.value
    else:
        role_val = role
    is_admin = str(role_val).lower() == "admin" if role_val else False

    # Public (unauthenticated) rules:
    # - may retrieve active system collections
    # - may retrieve shop collections (only if collection is active)
    if current_user is None and not is_admin:
        if not c.is_active:
            raise HTTPException(status_code=403, detail="Not authorized to view this collection products")

    # Vendor (authenticated non-admin) rules:
    vendor_shop_row = None
    vendor_shop_display_id = None
    if current_user is not None and not is_admin:
        try:
            vendor_shop_row = session.query(shop).filter(shop.owner_id == current_user.id).first()
            if vendor_shop_row:
                vendor_shop_display_id = vendor_shop_row.display_id
        except Exception:
            vendor_shop_row = None

        if is_shop_bound:
            # vendor may retrieve only if they own one of the linked shop_collection shops
            if vendor_shop_row is None:
                raise HTTPException(status_code=403, detail="Not authorized to view this collection products")
            owns = any((s.shop_id == vendor_shop_row.id) for s in sc_links)
            if not owns:
                raise HTTPException(status_code=403, detail="Not authorized to view this collection products")
        else:
            # system collection: must be active
            if not c.is_active:
                raise HTTPException(status_code=403, detail="Not authorized to view this collection products")
            # if system collection has allowed shops restrictions, vendor must be in that list
            if shop_display_ids and vendor_shop_display_id:
                if vendor_shop_display_id not in shop_display_ids:
                    raise HTTPException(status_code=403, detail="Not authorized to view this collection products")

    # Admins may view everything; at this point request is authorized to query products
    rows = (
        session.query(collection_product)
        .filter(collection_product.collection_id == collection_id)
        .order_by(collection_product.created_at.desc())
        .all()
    )
    product_ids = [r.product_id for r in rows]
    if not product_ids:
        return {"items": []}

    # Base product query
    prod_q = session.query(product).filter(product.id.in_(product_ids))

    # Vendors requesting system collections should only receive their own products
    if current_user is not None and not is_admin:
        if not is_shop_bound and vendor_shop_display_id:
            # prefer product.shop_display_id if present, otherwise try joining via shop relationship
            try:
                prod_q = prod_q.filter(product.shop_display_id == vendor_shop_display_id)
            except Exception:
                # fallback: join shop table (if product has shop_id)
                try:
                    prod_q = prod_q.join(shop, product.shop_id == shop.id).filter(shop.display_id == vendor_shop_display_id)
                except Exception:
                    # if model doesn't support shop filtering, return empty set to be safe
                    prod_q = prod_q.filter(False)

    products = prod_q.all()

    return {"items": [{"id": p.id, "display_id": p.display_id, "name": p.name} for p in products]}


@router.post("/{collection_id}/add")
def add_products(collection_id: int, payload: ProductsModifyRequest, request: Request, session: Session = Depends(get_session)):
    c = session.query(collection).filter(collection.id == collection_id).first()
    if c is None:
        raise HTTPException(status_code=404, detail="Collection not found")

    # require authentication
    current_user = getattr(request.state, "current_user", None)
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required to add products to collections")

    role = getattr(current_user, "role", None)
    if hasattr(role, "value"):
        role_val = role.value
    else:
        role_val = role
    is_admin = str(role_val).lower() == "admin" if role_val else False

    # resolve vendor shop if non-admin
    vendor_shop_row = None
    vendor_shop_display_id = None
    if not is_admin:
        try:
            vendor_shop_row = session.query(shop).filter(shop.owner_id == current_user.id).first()
            if vendor_shop_row:
                vendor_shop_display_id = vendor_shop_row.display_id
        except Exception:
            vendor_shop_row = None

    # system constraints from collection_shop
    shops = (
        session.query(collection_shop)
        .filter(collection_shop.collection_id == collection_id)
        .all()
    )
    # shop-scoped collections are identified by shop_collection links
    sc_links = (
        session.query(shop_collection)
        .filter(shop_collection.collection_id == collection_id)
        .all()
    )
    shop_display_ids = [s.shop_display_id for s in shops if s.shop_display_id]
    is_shop_bound = len(sc_links) > 0

    # Non-admin pre-checks
    if not is_admin:
        if is_shop_bound:
            # vendor must own one of the linked shop_collection shops
            if vendor_shop_row is None:
                raise HTTPException(status_code=403, detail="Not authorized to add products to this collection")
            owns = any((s.shop_id == vendor_shop_row.id) for s in sc_links)
            if not owns:
                raise HTTPException(status_code=403, detail="Not authorized to add products to this collection")
        else:
            # system collection: must be active
            if not c.is_active:
                raise HTTPException(status_code=403, detail="Not authorized to add products to this collection")
            # if system collection has allowed shop restrictions, vendor must be in that list
            if shop_display_ids and vendor_shop_display_id:
                if vendor_shop_display_id not in shop_display_ids:
                    raise HTTPException(status_code=403, detail="Not authorized to add products to this collection")

    now = datetime.now()
    added = 0
    for pdid in payload.product_display_ids:
        p = session.query(product).filter(product.display_id == pdid).first()
        if not p:
            continue

        # enforce per-product ownership for non-admins: vendor may only add their own products
        if not is_admin:
            if not vendor_shop_display_id:
                continue
            belongs = False
            try:
                if getattr(p, "shop_display_id", None) and p.shop_display_id == vendor_shop_display_id:
                    belongs = True
                elif getattr(p, "shop_id", None):
                    shop_row = session.query(shop).filter(shop.id == p.shop_id).first()
                    if shop_row and shop_row.display_id == vendor_shop_display_id:
                        belongs = True
            except Exception:
                belongs = False

            if not belongs:
                # skip products that do not belong to the vendor
                continue

        exists = (
            session.query(collection_product)
            .filter(collection_product.collection_id == collection_id, collection_product.product_id == p.id)
            .first()
        )
        if exists:
            continue
        cp = collection_product(collection_id=collection_id, product_id=p.id, created_at=now, updated_at=now, is_active=True)
        session.add(cp)
        added += 1
    session.commit()
    return {"message": "Products added", "added": added}


@router.post("/{collection_id}/remove")
def remove_products(collection_id: int, payload: ProductsModifyRequest, request: Request, session: Session = Depends(get_session)):
    c = session.query(collection).filter(collection.id == collection_id).first()
    if c is None:
        raise HTTPException(status_code=404, detail="Collection not found")

    # require authentication
    current_user = getattr(request.state, "current_user", None)
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required to remove products from collections")

    role = getattr(current_user, "role", None)
    if hasattr(role, "value"):
        role_val = role.value
    else:
        role_val = role
    is_admin = str(role_val).lower() == "admin" if role_val else False

    # resolve vendor shop if non-admin
    vendor_shop_row = None
    vendor_shop_display_id = None
    if not is_admin:
        try:
            vendor_shop_row = session.query(shop).filter(shop.owner_id == current_user.id).first()
            if vendor_shop_row:
                vendor_shop_display_id = vendor_shop_row.display_id
        except Exception:
            vendor_shop_row = None

    # system constraints from collection_shop
    shops = (
        session.query(collection_shop)
        .filter(collection_shop.collection_id == collection_id)
        .all()
    )
    # shop-scoped collections are identified by shop_collection links
    sc_links = (
        session.query(shop_collection)
        .filter(shop_collection.collection_id == collection_id)
        .all()
    )
    shop_display_ids = [s.shop_display_id for s in shops if s.shop_display_id]
    is_shop_bound = len(sc_links) > 0

    # Non-admin pre-checks
    if not is_admin:
        if is_shop_bound:
            # vendor must own one of the linked shop_collection shops
            if vendor_shop_row is None:
                raise HTTPException(status_code=403, detail="Not authorized to remove products from this collection")
            owns = any((s.shop_id == vendor_shop_row.id) for s in sc_links)
            if not owns:
                raise HTTPException(status_code=403, detail="Not authorized to remove products from this collection")
        else:
            # system collection: must be active
            if not c.is_active:
                raise HTTPException(status_code=403, detail="Not authorized to remove products from this collection")
            # if system collection has allowed shop restrictions, vendor must be in that list
            if shop_display_ids and vendor_shop_display_id:
                if vendor_shop_display_id not in shop_display_ids:
                    raise HTTPException(status_code=403, detail="Not authorized to remove products from this collection")

    removed = 0
    for pdid in payload.product_display_ids:
        p = session.query(product).filter(product.display_id == pdid).first()
        if not p:
            continue

        # enforce per-product ownership for non-admins: vendor may only remove their own products
        if not is_admin:
            # vendor must have a shop identity
            if not vendor_shop_display_id:
                continue
            belongs = False
            try:
                if getattr(p, "shop_display_id", None) and p.shop_display_id == vendor_shop_display_id:
                    belongs = True
                elif getattr(p, "shop_id", None):
                    shop_row = session.query(shop).filter(shop.id == p.shop_id).first()
                    if shop_row and shop_row.display_id == vendor_shop_display_id:
                        belongs = True
            except Exception:
                belongs = False

            if not belongs:
                # skip products that do not belong to the vendor
                continue

        deleted = (
            session.query(collection_product)
            .filter(collection_product.collection_id == collection_id, collection_product.product_id == p.id)
            .delete()
        )
        removed += deleted

    session.commit()
    return {"message": "Products removed", "removed": removed}
