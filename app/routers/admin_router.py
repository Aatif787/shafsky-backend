from fastapi import APIRouter, HTTPException, Header
from typing import Optional, Dict, Any
from app.services.auth_service import AuthService

router = APIRouter(prefix="/api/admin", tags=["Admin Services"])

@router.get("/dashboard")
async def get_admin_dashboard(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authorization token.")

    token = authorization.split(" ")[1]
    try:
        decoded = AuthService.decode_access_token(token)
        if decoded.get("role") not in ["ADMIN", "SUPER_ADMIN"]:
            raise HTTPException(status_code=403, detail="Access denied. Insufficient role permissions.")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Token expired or invalid.")

    return {
        "success": True,
        "data": {
            "status": "Active",
            "pendingBookings": 3,
            "totalRevenue": 16800.0,
            "engine": "FastAPI Python Microservice"
        }
    }
