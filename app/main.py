from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timezone
from app.config import settings
from app.routers import auth_router, flight_router, admin_router, booking_router

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="2.0.0",
    description="Enterprise FastAPI Backend Engine for Shafsky Aviation Concierge Platform",
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers
app.include_router(auth_router.router)
app.include_router(flight_router.router)
app.include_router(admin_router.router)
app.include_router(booking_router.router)

@app.get("/health", tags=["Health"])
async def health_check():
    return {
        "status": "healthy",
        "service": "shafsky-backend-fastapi",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.getenv("PORT", "8001"))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)

