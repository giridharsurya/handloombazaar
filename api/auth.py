from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form, Header
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr, Field
from datetime import datetime
from db.database import get_session
from db.db_models import shop, user, UserRole
from utils.auth import hash_password, verify_password, create_user_token, verify_token
import uuid

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])


class ShopRegisterRequest(BaseModel):
    """Request model for shop registration (kept for docs compatibility)."""
    shop_name: str = Field(..., min_length=1, max_length=255)
    email: EmailStr
    username: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=6)
    year_established: int = Field(..., ge=1800, le=2100)
    address: str = Field(..., min_length=3, max_length=500)
    phone_number: str = Field(..., min_length=7, max_length=20)
    shop_logo_url: str = Field(..., min_length=3, max_length=1000)


class ShopLoginRequest(BaseModel):
    """Request model for shop login."""
    username: str
    password: str


class ShopRegisterResponse(BaseModel):
    """Response model for shop registration."""
    shop_display_id: str
    username: str
    email: str
    role: str
    token: str
    approved: bool
    message: str


class ShopLoginResponse(BaseModel):
    """Response model for shop login."""
    shop_display_id: str | None
    username: str
    email: str
    role: str
    shop_name: str | None
    token: str
    approved: bool
    message: str


class TokenVerifyResponse(BaseModel):
    """Response model for token verification."""
    valid: bool
    shop_display_id: str | None
    username: str | None
    role: str | None


def _slugify(text: str) -> str:
    """Convert text to kebab-case slug."""
    return text.lower().replace(" ", "-").replace("_", "-")


def _generate_display_id(name: str, prefix: str = "shop") -> str:
    """Generate a unique display ID with timestamp."""
    from datetime import datetime as dt
    slug = _slugify(name)
    timestamp = dt.now().strftime("%Y%m%d%H%M%S")
    return f"{prefix}-{slug}-{timestamp}"


@auth_router.post("/shop/register", response_model=ShopRegisterResponse)
def shop_register(
    shop_name: str = Form(...),
    email: EmailStr = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    year_established: int = Form(...),
    address: str = Form(...),
    phone_number: str = Form(...),
    shop_logo: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    """Register a new shop owner and accept an optional shop logo upload."""

    # Check if username already exists
    existing_username = session.query(user).filter(user.username == username).first()
    if existing_username:
        raise HTTPException(status_code=400, detail="Username already exists")

    # Check if email already exists
    existing_email = session.query(user).filter(user.email == email).first()
    if existing_email:
        raise HTTPException(status_code=400, detail="Email already registered")

    # Save uploaded logo to local static folder and store a web path for API consumers.
    import os
    from pathlib import Path
    import shutil

    uploads_dir = Path("static") / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    # Use timestamp + uuid to avoid collisions and keep original extension
    orig_name = getattr(shop_logo, "filename", "upload")
    ext = Path(orig_name).suffix or ""
    filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex}{ext}"
    safe_path = uploads_dir / filename
    try:
        with safe_path.open("wb") as out_file:
            shutil.copyfileobj(shop_logo.file, out_file)
        logo_path = f"/static/uploads/{filename}"
    except Exception as ex:
        raise HTTPException(status_code=400, detail=f"Failed to save uploaded logo: {ex}")
    finally:
        try:
            shop_logo.file.close()
        except Exception:
            pass

    now = datetime.now()

    # Create user first so shop can reference owner_id.
    new_user = user(
        username=username,
        email=email,
        password_hash=hash_password(password),
        role=UserRole.SHOP_OWNER,
        created_at=now,
        updated_at=now,
        is_active=True,
    )
    session.add(new_user)
    session.flush()

    # Create new shop linked to this user.
    new_shop = shop(
        owner_id=new_user.id,
        name=shop_name,
        email=email,
        year_established=year_established,
        address=address,
        phone_number=phone_number,
        shop_logo_url=logo_path,
        display_id=str(uuid.uuid4().hex)[:8],
        created_at=now,
        updated_at=now,
        is_active=True,
        approved=False,  # Requires admin approval
    )
    
    session.add(new_shop)
    try:
        session.commit()
    except Exception as e:
        print(f"Error committing new shop and user: {e}")
        session.rollback()
        raise HTTPException(status_code=400, detail="Failed to register shop")
    session.refresh(new_shop)

    # Create token
    token = create_user_token(
        username=new_user.username,
        role="shop_owner",
        shop_display_id=new_shop.display_id,
    )

    return ShopRegisterResponse(
        shop_display_id=new_shop.display_id,
        username=new_user.username,
        email=new_shop.email,
        role="shop_owner",
        token=token,
        approved=new_shop.approved,
        message="Shop registered successfully. Please wait for admin approval.",
    )


@auth_router.post("/login", response_model=ShopLoginResponse)
def shop_login(request: ShopLoginRequest, session: Session = Depends(get_session), authorization: str | None = Header(None)):
    """Login a shop owner. If a valid Bearer token is presented and belongs
    to the same username, reuse that token instead of issuing a new one."""
    
    # Find shop by username
    selected_user = session.query(user).filter(user.username == request.username).first()
    if not selected_user:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    # Verify password
    if not verify_password(request.password, selected_user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    
    # Get shop owned by this user
    selected_shop = (
        session.query(shop)
        .filter(shop.owner_id == selected_user.id)
        .first()
    )
    
    # If the client sent an Authorization header with a Bearer token, and the
    # token verifies and belongs to this username, reuse it. Otherwise create
    # a new token.
    token = None
    if authorization and authorization.startswith("Bearer "):
        presented = authorization.split(" ", 1)[1]
        payload = verify_token(presented)
        if payload and payload.get("username") == request.username:
            token = presented

    if not token:
        token = create_user_token(
            username=selected_user.username,
            role=selected_user.role.value,
            shop_display_id=selected_shop.display_id if selected_shop else None,
        )

    return ShopLoginResponse(
        shop_display_id=selected_shop.display_id if selected_shop else None,
        username=selected_user.username,
        email=selected_user.email,
        role=selected_user.role.value,
        shop_name=selected_shop.name if selected_shop else None,
        token=token,
        approved=selected_shop.approved if selected_shop else False,
        message="Login successful",
    )


@auth_router.post("/shop/verify", response_model=TokenVerifyResponse)
def verify_token_endpoint(token: str):
    """Verify a shop token."""

    payload = verify_token(token)
    if payload:
        return TokenVerifyResponse(
            valid=True,
            shop_display_id=payload.get("shop_display_id"),
            username=payload.get("username"),
            role=payload.get("role"),
        )
    else:
        return TokenVerifyResponse(
            valid=False,
            shop_display_id=None,
            username=None,
            role=None,
        )
