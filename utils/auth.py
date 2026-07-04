import jwt
import os
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import bcrypt

# In production, use environment variables for these
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode(), salt).decode()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against its hash."""
    return bcrypt.checkpw(plain_password.encode(), hashed_password.encode())


def create_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT token."""
    to_encode = data.copy()
    
    if expires_delta:
        expire = datetime.now() + expires_delta
    else:
        expire = datetime.now() + timedelta(hours=TOKEN_EXPIRE_HOURS)
    
    to_encode.update({"exp": expire})
    
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def verify_jwt_token(token: str) -> Optional[Dict[str, Any]]:
    """Verify and decode a JWT token."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def create_user_token(username: str, role: str, shop_display_id: Optional[str] = None) -> str:
    """Create a user token with role and optional external shop identifier."""
    data: Dict[str, Any] = {
        "username": username,
        "role": role,
    }
    if shop_display_id:
        data["shop_display_id"] = shop_display_id
    return create_token(data)


def verify_token(token: str) -> Optional[Dict[str, Any]]:
    """Verify a shop owner token and return the payload."""
    payload = verify_jwt_token(token)
    if payload and payload.get("role") in ["shop_owner", "admin"]:
        return payload
    return None
