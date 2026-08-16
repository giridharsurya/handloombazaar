import os
import random
import re
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form, Header
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from db.database import get_session
from db.db_models import shop, user, UserRole
from utils.auth import hash_password, verify_password, create_user_token, verify_token
from utils.blob_storage import upload_image_to_shop_container

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])

_forgot_password_store: dict[tuple[str, str], dict[str, object]] = {}
FORGOT_PASSWORD_OTP_TTL_MINUTES = 20


def _normalize_phone_number(phone_number: str) -> str:
    digits = re.sub(r"\D", "", (phone_number or "").strip())
    if not digits:
        return ""
    return f"+{digits}" if not digits.startswith("+") else digits


def _normalize_otp_identifier(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""

    lowered = text.lower()
    if "@" in lowered:
        return lowered

    normalized_phone = _normalize_phone_number(lowered)
    return normalized_phone or lowered


def _generate_otp_code() -> str:
    return str(random.randint(100000, 999999))


def _send_otp_via_azure_email(email_address: str, subject: str, message: str) -> bool:
    connection_string = os.getenv("AZURE_COMMUNICATION_CONNECTION_STRING")
    sender = os.getenv("AZURE_COMMUNICATION_EMAIL_SENDER")

    if not connection_string or not sender:
        print(f"[Email] Azure Communication Services email not configured. Email fallback disabled. Message: {message}")
        return False

    try:
        from azure.communication.email import EmailClient

        client = EmailClient.from_connection_string(connection_string)
        email_message = {
            "senderAddress": sender,
            "recipients": {"to": [{"address": email_address}]},
            "content": {
                "subject": subject,
                "plainText": message,
                "html": f"<html><body><p>{message}</p></body></html>",
            },
        }

        poller = client.begin_send(email_message)
        result = poller.result()
        return bool(getattr(result, "message_id", None))
    except Exception as exc:
        print(f"[Email] Failed to send OTP email: {exc}")
        return False


def _find_user_by_phone_number(session: Session, phone_number: str):
    normalized_phone = _normalize_phone_number(phone_number)
    for shop_row in session.query(shop).all():
        if not shop_row.phone_number:
            continue
        if _normalize_phone_number(shop_row.phone_number) == normalized_phone:
            owner = session.query(user).filter(user.id == shop_row.owner_id).first()
            return owner, shop_row
    return None, None


def _find_user_by_email(session: Session, email: str):
    normalized_email = (email or "").strip().lower()
    if not normalized_email:
        return None, None

    owner = session.query(user).filter(user.email == normalized_email).first()
    if owner:
        shop_row = session.query(shop).filter(shop.owner_id == owner.id).first()
        return owner, shop_row

    shop_row = session.query(shop).filter(shop.email == normalized_email).first()
    if shop_row:
        owner = session.query(user).filter(user.id == shop_row.owner_id).first()
        return owner, shop_row

    return None, None


def _find_otp_record(identifier: str, otp_code: str | None = None):
    normalized_identifier = _normalize_otp_identifier(identifier)
    if not normalized_identifier:
        return None, None

    for (stored_username, stored_identifier), record in list(_forgot_password_store.items()):
        if _normalize_otp_identifier(str(stored_identifier)) != normalized_identifier:
            continue

        record_data = record if isinstance(record, dict) else {"code": record}
        expiry = record_data.get("expires_at")
        if isinstance(expiry, datetime) and datetime.utcnow() > expiry:
            _forgot_password_store.pop((stored_username, stored_identifier), None)
            continue

        if otp_code is not None and str(record_data.get("code", record)) != str(otp_code):
            continue

        return (stored_username, stored_identifier), record_data

    return None, None


def verify_forgot_password_otp(username: str | None, identifier: str, otp_code: str) -> bool:
    key, record = _find_otp_record(identifier, otp_code)
    if key is None or record is None:
        return False

    if username is not None and key[0] != username:
        return False

    record["verified"] = True
    _forgot_password_store[key] = record
    return True


def reset_password_with_otp(account: user, identifier: str, otp_code: str, new_password: str) -> str:
    normalized_identifier = _normalize_otp_identifier(identifier)
    if not normalized_identifier or not new_password or len(new_password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters long.")

    key, record = _find_otp_record(normalized_identifier, otp_code)
    if key is None or record is None:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP.")

    if key[0] != account.username:
        raise HTTPException(status_code=400, detail="OTP does not match the account.")

    if record.get("verified") is not True:
        raise HTTPException(status_code=400, detail="OTP has not been verified yet.")

    account.password_hash = hash_password(new_password)
    account.updated_at = datetime.utcnow()
    _forgot_password_store.pop(key, None)
    return account.username


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ForgotPasswordVerifyRequest(BaseModel):
    email: EmailStr
    otp_code: str = Field(..., min_length=6, max_length=6)


class ForgotPasswordRetrieveRequest(BaseModel):
    email: EmailStr
    otp_code: str = Field(..., min_length=6, max_length=6)


class ForgotPasswordOtpResponse(BaseModel):
    success: bool
    message: str


class ForgotPasswordCredentialResponse(BaseModel):
    success: bool
    username: str | None = None
    message: str


class SetNewPasswordRequest(BaseModel):
    email: EmailStr
    otp_code: str = Field(..., min_length=6, max_length=6)
    new_password: str = Field(..., min_length=6)


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


@auth_router.post("/forgot-password/request-otp", response_model=ForgotPasswordOtpResponse)
def request_forgot_password_otp(payload: ForgotPasswordRequest, session: Session = Depends(get_session)):
    """Send an OTP to the registered email address for account recovery."""
    owner, _ = _find_user_by_email(session, str(payload.email))
    if owner is None:
        raise HTTPException(status_code=404, detail="No account found for this email address.")

    email_address = str(payload.email).strip().lower()
    otp_code = _generate_otp_code()
    otp_key = (owner.username, email_address)

    message = f"Your HandloomBazaar OTP is {otp_code}. It is valid for {FORGOT_PASSWORD_OTP_TTL_MINUTES} minutes."
    email_sent = _send_otp_via_azure_email(email_address, "HandloomBazaar OTP Verification", message)

    if not email_sent:
        _forgot_password_store.pop(otp_key, None)
        raise HTTPException(
            status_code=500,
            detail="Unable to send OTP email. Please try again later or contact support.",
        )

    _forgot_password_store[otp_key] = {
        "code": otp_code,
        "expires_at": datetime.utcnow() + timedelta(minutes=FORGOT_PASSWORD_OTP_TTL_MINUTES),
    }

    return ForgotPasswordOtpResponse(
        success=True,
        message="OTP has been sent to your registered email address.",
    )


@auth_router.post("/forgot-password/verify-otp", response_model=ForgotPasswordOtpResponse)
def verify_forgot_password_otp_endpoint(payload: ForgotPasswordVerifyRequest):
    """Validate the recovery OTP sent to the registered email address."""
    if verify_forgot_password_otp(None, str(payload.email), payload.otp_code):
        return ForgotPasswordOtpResponse(
            success=True,
            message="OTP verified successfully. Please set your new password.",
        )

    raise HTTPException(status_code=400, detail="Invalid or expired OTP.")


@auth_router.post("/forgot-password/retrieve-credentials", response_model=ForgotPasswordCredentialResponse)
def retrieve_forgot_password_credentials(payload: ForgotPasswordRetrieveRequest, session: Session = Depends(get_session)):
    """Validate the OTP and return the username so the user can set a real password via the app."""
    email_address = str(payload.email).strip().lower()
    matching_key = None
    for (stored_username, stored_email), record in list(_forgot_password_store.items()):
        if stored_email != email_address:
            continue
        if str(record.get("code", "")) == str(payload.otp_code):
            expiry = record.get("expires_at")
            if isinstance(expiry, datetime) and datetime.utcnow() > expiry:
                _forgot_password_store.pop((stored_username, stored_email), None)
                continue
            matching_key = (stored_username, stored_email)
            break

    if matching_key is None:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP.")

    username = matching_key[0]
    account = session.query(user).filter(user.username == username).first()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found.")

    _forgot_password_store.pop(matching_key, None)

    return ForgotPasswordCredentialResponse(
        success=True,
        username=account.username,
        message="OTP verified. Please set your new password.",
    )


@auth_router.post("/forgot-password/reset-password", response_model=ForgotPasswordCredentialResponse)
def reset_forgot_password(payload: SetNewPasswordRequest, session: Session = Depends(get_session)):
    """Set a new password after successful OTP verification."""
    owner, _ = _find_user_by_email(session, str(payload.email))
    if owner is None:
        raise HTTPException(status_code=404, detail="Account not found for this email address.")

    username = reset_password_with_otp(owner, str(payload.email), payload.otp_code, payload.new_password)
    session.add(owner)
    session.commit()

    return ForgotPasswordCredentialResponse(
        success=True,
        username=username,
        message="Your password has been reset successfully. Please login with your new password.",
    )
