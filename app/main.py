import os
import uvicorn
from datetime import datetime, timezone
from fastapi import FastAPI, Depends, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import engine, Base, get_db
import app.models.schema  # Ensure models are loaded
from app.security.middleware import SecurityMiddleware
from app.security.secrets import validate_secrets_on_startup
from app.monitoring.middlewares import ObservabilityMiddleware
from app.monitoring.health import HealthCheckSuite
from app.monitoring.metrics import PrometheusMetricsCollector
from app.monitoring.dashboard import ObservabilityDashboard
from app.monitoring.logging import structured_logger
from app.routers import (
    auth_router,
    admin_router,
    booking_router,
    notification_router,
    crm_router,
    migration_router
)
from app.flight import router as clean_flight_router, flights_router as clean_flights_router
from app.disaster_recovery import dr_router

# Create all missing database tables on startup in Neon PostgreSQL
try:
    Base.metadata.create_all(bind=engine)
    from sqlalchemy import text
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE refresh_tokens ADD COLUMN IF NOT EXISTS family_id UUID;"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_refresh_tokens_family_id ON refresh_tokens (family_id);"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_refresh_tokens_family_revoked ON refresh_tokens (family_id, revoked);"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_refresh_tokens_user_revoked ON refresh_tokens (user_id, revoked);"))
        conn.execute(text("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS version INTEGER DEFAULT 1;"))
        conn.commit()
except Exception as err:
    print(f"[Startup Warning] Could not auto-create tables or apply schema migrations: {err}")

# Validate Secrets on Startup
validate_secrets_on_startup()

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="2.0.0",
    description="Enterprise FastAPI Backend Engine for Shafsky Aviation Concierge Platform",
    docs_url="/docs",
    redoc_url="/redoc"
)

from app.middleware.idempotency import IdempotencyMiddleware

# Observability, Security & Idempotency Middlewares
app.add_middleware(ObservabilityMiddleware)
app.add_middleware(SecurityMiddleware)
app.add_middleware(IdempotencyMiddleware)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_origin_regex=r"https?://.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_exception_handler(_request, _exc: SQLAlchemyError):
    return JSONResponse(
        status_code=500,
        content={"success": False, "error": "A database error occurred. Please try again later."}
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"success": False, "error": "Validation error in request payload.", "details": exc.errors()}
    )

# Include Routers
app.include_router(auth_router.router)
app.include_router(clean_flight_router)
app.include_router(clean_flights_router)
app.include_router(admin_router.router)
app.include_router(booking_router.router)
app.include_router(notification_router.router)
app.include_router(crm_router.router)
app.include_router(dr_router.router)
app.include_router(migration_router.router)  # Supabase→FastAPI migration endpoints

# Production Observability & Health Routes
@app.get("/api/health", tags=["Observability & Health"], status_code=200)
async def backend_connectivity_health_check():
    """
    Backend connectivity verification endpoint.
    Unauthenticated public endpoint returning system status and UTC timestamp.
    """
    structured_logger.info(
        "Backend connectivity verification endpoint reached",
        extra={"endpoint": "/api/health", "service": "Shafsky Aviation Backend"}
    )
    return {
        "status": "ok",
        "backend": "connected",
        "service": "Shafsky Aviation Backend",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

@app.get("/health", tags=["Observability & Health"])
async def deep_health_check():
    return HealthCheckSuite.run_deep_health()

@app.get("/ready", tags=["Observability & Health"])
async def readiness_check():
    return HealthCheckSuite.run_readiness()

@app.get("/live", tags=["Observability & Health"])
async def liveness_check():
    return HealthCheckSuite.run_liveness()

@app.get("/metrics", tags=["Observability & Health"])
async def prometheus_metrics():
    metrics_text = PrometheusMetricsCollector.generate_metrics_text()
    return Response(content=metrics_text, media_type="text/plain; version=0.0.4")

@app.get("/api/admin/observability/dashboard", tags=["Observability & Health"])
async def observability_dashboard(db: Session = Depends(get_db)):
    return {"success": True, "data": ObservabilityDashboard.get_dashboard_metrics(db)}

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8001"))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)

