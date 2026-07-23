from typing import List
from fastapi import HTTPException, Header, Depends
from app.services.auth_service import AuthService

def require_roles(allowed_roles: List[str]):
    def role_checker(authorization: str = Header(...)):
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing or invalid authorization token.")
        token = authorization.split(" ")[1]
        decoded = AuthService.decode_access_token(token)
        user_role = str(decoded.get("role", "")).upper()
        
        normalized_allowed = [r.upper() for r in allowed_roles]
        
        # SUPER_ADMIN has access to everything
        if user_role == "SUPER_ADMIN":
            return decoded
            
        if user_role not in normalized_allowed:
            raise HTTPException(status_code=403, detail="Access denied. Insufficient role permissions.")
        return decoded
    return role_checker
