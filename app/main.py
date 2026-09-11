import os
import uvicorn
from datetime import datetime, timezone
from fastapi import FastAPI, Depends, Response, Request, HTTPException
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import engine, Base, get_db
from sqlalchemy import text
import app.models.schema  # Ensure models are loaded
import app.models.shared_domain  # Phase B.5 Shared Domain models
import app.models.airport  # Phase C.1 Airport Meet & Assist models
import app.models.journey_models  # Phase 1 Journey Detection Engine models
import app.models.charter_models  # Private Charter Engine models
from app.security.middleware import SecurityMiddleware
from app.security.dependencies import get_required_admin
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
    crm_router
)
from app.flight import router as clean_flight_router, flights_router as clean_flights_router
from app.disaster_recovery import dr_router

# Validate Secrets on Startup
validate_secrets_on_startup()


_prod = settings.is_production

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="2.0.0",
    description="Enterprise FastAPI Backend Engine for Shafsky Aviation Concierge Platform",
    docs_url=None if _prod else "/docs",
    redoc_url=None if _prod else "/redoc",
    openapi_url=None if _prod else "/openapi.json",
)


@app.on_event("startup")
async def startup_checks():
    """Run lightweight startup checks: DB connectivity and basic readiness.

    Fail fast if DB is unreachable in non-development environments.
    """
    env = getattr(settings, "ENVIRONMENT", "development").lower()
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            try:
                # Prefer Alembic migrations. Opt-in only for emergency column patches.
                if (os.getenv("RUN_STARTUP_SCHEMA_PATCHES") or "").strip().lower() in ("1", "true", "yes"):
                    conn.execute(text("ALTER TABLE payment_transactions ADD COLUMN IF NOT EXISTS is_duplicate BOOLEAN DEFAULT FALSE"))
                    conn.execute(text("ALTER TABLE payment_transactions ADD COLUMN IF NOT EXISTS notes TEXT"))
                    conn.execute(text("ALTER TABLE user_auth ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE"))
                    conn.execute(text("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS deleted_by_user_id UUID"))
                    conn.execute(text("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS deleted_by_email VARCHAR"))
                    conn.execute(text("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS deleted_by_role VARCHAR"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_bookings_deleted_at ON bookings (deleted_at)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_bookings_deleted_by_email ON bookings (deleted_by_email)"))
                    conn.commit()
            except Exception:
                pass
    except Exception as err:
        structured_logger.critical("Database connectivity check failed on startup", extra={"error": str(err)})
        # In production/staging, fail fast
        if env not in ["development", "dev", "testing", "test"]:
            raise RuntimeError(f"Database connectivity check failed: {err}")
        else:
            structured_logger.warning("Continuing startup in development despite DB connectivity check failure.")

    try:
        from app.flight.csv_airports import preload_global_airports, resolve_airports_csv_path

        count = preload_global_airports()
        structured_logger.info(
            "Loaded global airports.csv for origin/destination search only",
            extra={"count": count, "path": str(resolve_airports_csv_path())},
        )
    except Exception as csv_err:
        structured_logger.warning(
            "airports.csv could not be preloaded; global airport search will retry on first query",
            extra={"error": str(csv_err)},
        )

from app.middleware.idempotency import IdempotencyMiddleware

# Observability, Security & Idempotency Middlewares
app.add_middleware(ObservabilityMiddleware)
app.add_middleware(SecurityMiddleware)
app.add_middleware(IdempotencyMiddleware)

# CORS Middleware
# The permissive tunnel/localhost origin regex is a DEVELOPMENT convenience and is
# omitted entirely in production, so only explicit ALLOWED_ORIGINS are honoured and
# credentialed cross-origin requests cannot come from arbitrary ngrok/vercel origins.
_CORS_DEV_ORIGIN_REGEX = (
    r"^https?://(localhost|127\.0\.0\.1|.*\.ngrok-free\.(dev|app)|.*\.ngrok\.io|.*\.vercel\.app)(:\d+)?$"
)
_cors_kwargs = {
    "allow_origins": getattr(settings, "ALLOWED_ORIGINS", []),
    "allow_credentials": getattr(settings, "CORS_ALLOW_CREDENTIALS", False),
    "allow_methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    "allow_headers": [
        "Authorization",
        "Content-Type",
        "Accept",
        "Origin",
        "X-Device-ID",
        "Idempotency-Key",
        "X-Idempotency-Key",
        "X-Correlation-ID",
    ],
}
if not _prod:
    _cors_kwargs["allow_origin_regex"] = _CORS_DEV_ORIGIN_REGEX
app.add_middleware(CORSMiddleware, **_cors_kwargs)

@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_exception_handler(_request, _exc: SQLAlchemyError):
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "code": "ERR_500",
            "error": "A database error occurred. Please try again later.",
            "detail": "A database error occurred. Please try again later.",
        }
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request, exc: RequestValidationError):
    content = {
        "success": False,
        "code": "ERR_422",
        "error": "Validation error in request payload.",
        "detail": "Validation error in request payload.",
    }
    if not settings.is_production:
        content["details"] = jsonable_encoder(exc.errors())
    return JSONResponse(
        status_code=422,
        content=content,
    )

@app.exception_handler(HTTPException)
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(_request: Request, exc: StarletteHTTPException):
    headers = getattr(exc, "headers", None)
    detail = exc.detail
    error_msg = detail if isinstance(detail, str) else "Request error"
    content = {
        "success": False,
        "code": f"ERR_{exc.status_code}",
        "error": error_msg,
        "detail": detail,
    }
    if not isinstance(detail, str):
        content["details"] = detail
    return JSONResponse(
        status_code=exc.status_code,
        content=content,
        headers=headers,
    )

from app.routers import workflow_router
from app.routers import shared_domain_router
from app.routers import workflow_admin_router
from app.routers import airport_router
from app.routers import config_router
from app.routers import ticketing_router
from app.routers import payment_router
from app.routers import journey_router
from app.routers import operations_router
from app.routers import charter_router
from app.ai import router as ai_router
from app.integrations.whatsapp.router import router as whatsapp_router

# Include Routers
app.include_router(auth_router.router)
app.include_router(clean_flight_router)
app.include_router(clean_flights_router)
app.include_router(admin_router.router)
app.include_router(booking_router.router)
app.include_router(notification_router.router)
app.include_router(crm_router.router)
app.include_router(dr_router.router)
app.include_router(workflow_router.router)
app.include_router(shared_domain_router.router)
app.include_router(workflow_admin_router.router)
app.include_router(airport_router.router)
app.include_router(config_router.router)
app.include_router(ticketing_router.router)
app.include_router(payment_router.router)
app.include_router(ai_router.router)
app.include_router(whatsapp_router)
app.include_router(journey_router.router)
app.include_router(operations_router.router)
app.include_router(charter_router.router)

# Direct Razorpay Standard Checkout Root Endpoints
@app.post("/api/create-order", tags=["Razorpay Checkout"], status_code=201)
async def root_create_order(payload: payment_router.RazorpayCreateOrderRequest, db: Session = Depends(get_db)):
    return await payment_router.create_order_endpoint(payload, db)

@app.post("/api/verify-payment", tags=["Razorpay Checkout"], status_code=200)
async def root_verify_payment(payload: payment_router.PaymentVerifyRequest, db: Session = Depends(get_db)):
    return await payment_router.verify_payment_endpoint(payload, db)


@app.get("/api/global-airports", tags=["Journey Detection Engine"])
def search_global_airports_csv(q: str = ""):
    """Origin/unrestricted airport search from tools/airports.csv only. Not used for Shafsky service availability."""
    from app.flight.csv_airports import search_global_csv_airports

    try:
        rows = search_global_csv_airports(q or "")
    except FileNotFoundError as exc:
        return {"success": False, "source": "airports.csv", "error": str(exc), "data": []}
    return {"success": True, "source": "airports.csv", "data": rows}

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
async def deep_health_check(_admin=Depends(get_required_admin)):
    """Deep health for ops. Returns 503 when the database is unhealthy (ALB/ECS safe)."""
    payload = HealthCheckSuite.run_deep_health()
    db_status = (payload.get("subsystems") or {}).get("database", {}).get("status")
    status_code = 503 if db_status == "UNHEALTHY" else 200
    return JSONResponse(content=payload, status_code=status_code)

@app.get("/ready", tags=["Observability & Health"])
async def readiness_check():
    """Readiness probe — 503 when not ready so load balancers stop sending traffic."""
    payload = HealthCheckSuite.run_readiness()
    status_code = 200 if payload.get("ready") else 503
    return JSONResponse(content=payload, status_code=status_code)

@app.get("/live", tags=["Observability & Health"])
async def liveness_check():
    """Liveness probe — process is up (always 200 if this handler runs)."""
    return HealthCheckSuite.run_liveness()

@app.get("/metrics", tags=["Observability & Health"])
async def prometheus_metrics(
    request: Request,
    _admin=Depends(get_required_admin),
):
    metrics_text = PrometheusMetricsCollector.generate_metrics_text()
    return Response(content=metrics_text, media_type="text/plain; version=0.0.4")

@app.get("/api/admin/observability/dashboard", tags=["Observability & Health"])
async def observability_dashboard(
    db: Session = Depends(get_db),
    _admin=Depends(get_required_admin),
):
    return {"success": True, "data": ObservabilityDashboard.get_dashboard_metrics(db)}

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8003"))
    env = getattr(settings, "ENVIRONMENT", "development").lower()
    # Only enable auto-reload in development/test environments. Bind host is configurable.
    reload_flag = env in ["development", "dev", "testing", "test"]
    host = os.getenv("BIND_HOST", "127.0.0.1")
    uvicorn.run("app.main:app", host=host, port=port, reload=reload_flag)

