from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import uvicorn
import os

from db.database import db
from api.products import products_router
from api.create import create_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.connect()
    try:
        yield
    finally:
        db.disconnect()


app = FastAPI(title="HandloomBazaar API", version="0.1.0", lifespan=lifespan)

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


app.include_router(products_router)
app.include_router(create_router)

@app.get("/health")
async def health():
    return JSONResponse({"status": "ok"})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=True)
