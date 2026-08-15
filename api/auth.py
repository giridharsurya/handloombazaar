from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form, Header
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr, Field
from datetime import datetime
from db.database import get_session
from db.db_models import shop, user, UserRole
from utils.auth import hash_password, verify_password, create_user_token, verify_token
from utils.blob_storage import upload_image_to_shop_container
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
    city: str = Field(..., min_length=2, max_length=120)
    phone_number: str = Field(..., min_length=7, max_length=20)
    shop_logo_url: str = Field(..., min_length=3, max_length=1000)
    website_url: str | None = Field(default=None, max_length=255)
    youtube_url: str | None = Field(default=None, max_length=255)
    instagram_url: str | None = Field(default=None, max_length=255)
    facebook_url: str | None = Field(default=None, max_length=255)


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
    """Convert text to a normalized slug, using underscores for spaces."""
    return text.lower().replace(" ", "_").replace("_", "_")


def _generate_display_id(name: str, prefix: str = "shop") -> str:
    """Generate a short unique display ID from a UUID, using the first 8 characters."""
    return str(uuid.uuid4()).replace("-", "")[:8]


@auth_router.post("/shop/register", response_model=ShopRegisterResponse)
def shop_register(
    shop_name: str = Form(...),
    email: EmailStr = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    year_established: int = Form(...),
    address: str = Form(...),
    city: str = Form(...),
    phone_number: str = Form(...),
    website_url: str | None = Form(default=None),
    youtube_url: str | None = Form(default=None),
    instagram_url: str | None = Form(default=None),
    facebook_url: str | None = Form(default=None),
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

    now = datetime.now()
    orig_name = getattr(shop_logo, "filename", "upload")
    shop_display_id = _generate_display_id(shop_name)
    try:
        if hasattr(shop_logo.file, "seek"):
            shop_logo.file.seek(0)
        logo_url = upload_image_to_shop_container(
            shop_logo.file,
            shop_display_id,
            orig_name,
            shop_name=shop_name,
            city=city,
            created_at=now,
        )
    except Exception as ex:
        raise HTTPException(status_code=400, detail=f"Failed to save uploaded logo: {ex}")
    finally:
        try:
            shop_logo.file.close()
        except Exception:
            pass

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
        city=city.strip(),
        phone_number=phone_number,
        website_url=website_url.strip() if website_url else None,
        youtube_url=youtube_url.strip() if youtube_url else None,
        instagram_url=instagram_url.strip() if instagram_url else None,
        facebook_url=facebook_url.strip() if facebook_url else None,
        shop_logo_url=logo_url,
        display_id=shop_display_id,
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
def verify_token_endpoint(token: str, session: Session = Depends(get_session)):
    """Verify a shop token."""

    payload = verify_token(token)
    if not payload:
        return TokenVerifyResponse(valid=False, shop_display_id=None, username=None, role=None)

    username = payload.get("username") or payload.get("sub")
    if not username:
        return TokenVerifyResponse(valid=False, shop_display_id=None, username=None, role=None)

    selected_user = session.query(user).filter(user.username == username).first()
    if not selected_user:
        return TokenVerifyResponse(valid=False, shop_display_id=None, username=None, role=None)

    token_role = payload.get("role")
    user_role = getattr(selected_user.role, "value", str(selected_user.role))
    if token_role != user_role or not getattr(selected_user, "is_active", True):
        return TokenVerifyResponse(valid=False, shop_display_id=None, username=None, role=None)

    return TokenVerifyResponse(
        valid=True,
        shop_display_id=payload.get("shop_display_id"),
        username=username,
        role=token_role,
    )
