from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from config import get_settings
from database import init_db

from auth.routes import router as auth_router
from api.stores import router as stores_router
from api.products import router as products_router
from api.sales import router as sales_router
from api.profit import router as profit_router
from sync.sync_fx import router as sync_fx_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create tables and seed data
    await init_db()
    yield
    # Shutdown: nothing to clean up


app = FastAPI(
    title="ProfitLens API",
    description="Multi-store Amazon seller profit analytics",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
settings = get_settings()
origins = [o.strip() for o in settings.CORS_ORIGINS.split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(auth_router)
app.include_router(stores_router)
app.include_router(products_router)
app.include_router(sales_router)
app.include_router(profit_router)
app.include_router(sync_fx_router)


@app.get("/health", tags=["health"])
async def health_check():
    return {"status": "ok", "service": "profitlens"}
