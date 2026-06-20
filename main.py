from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import os


app = FastAPI(title="HandloomBazaar API", version="0.1.0")

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


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok"})


# Placeholder: include routers when implemented. Example:
# try:
#     from routers.retailers import router as retailers_router
#     app.include_router(retailers_router, prefix="/api/retailers")
# except Exception:
#     pass


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=True)
