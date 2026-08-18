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
from sqlalchemy import text
import app.models.schema  # Ensure models are loaded
import app.models.shared_domain  # Phase B.5 Shared Domain models
import app.models.airport  # Phase C.1 Airport Meet & Assist models
import app.models.journey_models  # Phase 1 Journey Detection Engine models
import app.models.operations_models  # Phase 6 Operations & Communication Engine models
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
    crm_router
)
from app.flight import router as clean_flight_router, flights_router as clean_flights_router
from app.disaster_recovery import dr_router

# Validate Secrets on Startup
validate_secrets_on_startup()


app = FastAPI(
    title=settings.PROJECT_NAME,
    version="2.0.0",
    description="Enterprise FastAPI Backend Engine for Shafsky Aviation Concierge Platform",
    docs_url="/docs",
    redoc_url="/redoc"
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
    except Exception as err:
        structured_logger.critical("Database connectivity check failed on startup", extra={"error": str(err)})
        # In production/staging, fail fast
        if env not in ["development", "dev", "testing", "test"]:
            raise RuntimeError(f"Database connectivity check failed: {err}")
        else:
            structured_logger.warning("Continuing startup in development despite DB connectivity check failure.")

from app.middleware.idempotency import IdempotencyMiddleware

# Observability, Security & Idempotency Middlewares
app.add_middleware(ObservabilityMiddleware)
app.add_middleware(SecurityMiddleware)
app.add_middleware(IdempotencyMiddleware)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=getattr(settings, "ALLOWED_ORIGINS", []),
    # Do NOT use a permissive origin regex in production; origin list is configurable via ALLOWED_ORIGINS
    allow_credentials=getattr(settings, "CORS_ALLOW_CREDENTIALS", False),
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
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

from app.routers import workflow_router
from app.routers import shared_domain_router
from app.routers import workflow_admin_router
from app.routers import airport_router
from app.routers import config_router
from app.routers import ticketing_router
from app.routers import payment_router
from app.routers import journey_router
from app.routers import operations_router
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
    env = getattr(settings, "ENVIRONMENT", "development").lower()
    # Only enable auto-reload in development/test environments. Bind host is configurable.
    reload_flag = env in ["development", "dev", "testing", "test"]
    host = os.getenv("BIND_HOST", "127.0.0.1")
    uvicorn.run("app.main:app", host=host, port=port, reload=reload_flag)

