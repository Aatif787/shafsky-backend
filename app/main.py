from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timezone
from app.config import settings
from app.routers import auth_router, flight_router, admin_router, booking_router, notification_router, crm_router
from app.security.middleware import SecurityMiddleware
from app.security.secrets import validate_secrets_on_startup
from app.monitoring.middlewares import ObservabilityMiddleware
from app.monitoring.health import HealthCheckSuite
from app.monitoring.metrics import PrometheusMetricsCollector
from app.monitoring.dashboard import ObservabilityDashboard
from app.database import get_db
from sqlalchemy.orm import Session
from fastapi import Depends, Response

from app.database import engine, Base
import app.models.schema  # Ensure models are loaded

# Create all missing database tables on startup in Neon PostgreSQL
try:
    Base.metadata.create_all(bind=engine)
except Exception as e:
    print(f"[Startup Warning] Could not auto-create tables: {e}")

# Validate Secrets on Startup
validate_secrets_on_startup()

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="2.0.0",
    description="Enterprise FastAPI Backend Engine for Shafsky Aviation Concierge Platform",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Observability & Security Middlewares
app.add_middleware(ObservabilityMiddleware)
app.add_middleware(SecurityMiddleware)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.routers import auth_router, flight_router, admin_router, booking_router, notification_router, crm_router
from app.routers import migration_router
from app.disaster_recovery import dr_router

# Include Routers
app.include_router(auth_router.router)
app.include_router(flight_router.router)
app.include_router(flight_router.flights_router)
app.include_router(admin_router.router)
app.include_router(booking_router.router)
app.include_router(notification_router.router)
app.include_router(crm_router.router)
app.include_router(dr_router.router)
app.include_router(migration_router.router)  # Supabase→FastAPI migration endpoints

# Production Observability & Health Routes
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
    import uvicorn
    import os
    port = int(os.getenv("PORT", "8001"))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)

