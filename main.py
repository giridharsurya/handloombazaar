from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import uvicorn
import os

from db.database import db
from api.products import products_router
from api.create import create_router
from api.admin import admin_router
from api.auth import auth_router
from api.shops import shops_router
from fastapi import Request
from utils.auth import verify_token
from db.db_models import user as UserModel, shop as ShopModel

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.connect()
    try:
        yield
    finally:
        db.disconnect()


app = FastAPI(title="HandloomBazaar API", version="0.1.0", lifespan=lifespan)

# Serve uploaded static files from ./static
app.mount("/static", StaticFiles(directory="static"), name="static")

# Configure CORS origins via environment variable `CORS_ORIGINS`
# Default allows Next.js dev server origins
raw_origins = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
origins = [o.strip() for o in raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def ensure_db_connection(request: Request, call_next):
    if request.url.path != "/health":
        try:
            db.ensure_connection()
        except RuntimeError as exc:
            return JSONResponse(
                status_code=503,
                content={"detail": f"Database unavailable: {exc}"},
            )
    return await call_next(request)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Decode bearer token (if present) and attach `current_user` to `request.state`.

    Uses `utils.auth.verify_token` to decode and then loads a `user` row when possible.
    For legacy shop tokens (payload contains `shop_id`) we resolve the shop owner.
    """
    request.state.current_user = None
    authorization = request.headers.get("authorization")
    token = None
    if authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1]
        else:
            token = parts[-1]

    if token:
        payload = verify_token(token)
        if payload:
            session = None
            try:
                session = db.get_session()
                # prefer explicit user id in token
                username = payload.get("username") or payload.get("sub")
                if username:
                    u = session.query(UserModel).filter(UserModel.username == username).first()
                    request.state.current_user = u
            finally:
                if session:
                    session.close()

    return await call_next(request)


app.include_router(products_router)
app.include_router(create_router)
app.include_router(admin_router)
app.include_router(auth_router)
app.include_router(shops_router)

@app.get("/health")
async def health():
    return JSONResponse({"status": "ok"})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=True)
