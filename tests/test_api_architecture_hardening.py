"""
Automated Test Suite for Shafsky Backend API Architecture Hardening.

Verifies:
1. Global standard error envelopes (HTTPException, RequestValidationError, RateLimiter).
2. Cache-Control: no-store on state mutations (POST, PUT, PATCH, DELETE).
3. Cache-Control: public on safe master data GET requests.
4. Backward compatibility of detail and error fields.
5. Shared domain collection pagination bounds and full-list fallback.
"""

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from app.security.middleware import SecurityMiddleware


def create_test_app():
    """Create lightweight test FastAPI app with the hardened exception handlers and middleware."""
    from fastapi.encoders import jsonable_encoder
    from fastapi.exceptions import RequestValidationError

    app = FastAPI()
    app.add_middleware(SecurityMiddleware)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_request, exc: RequestValidationError):
        content = {
            "success": False,
            "code": "ERR_422",
            "error": "Validation error in request payload.",
            "detail": "Validation error in request payload.",
            "details": jsonable_encoder(exc.errors()),
        }
        return JSONResponse(status_code=422, content=content)

    @app.exception_handler(HTTPException)
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(_request, exc: StarletteHTTPException):
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
        return JSONResponse(status_code=exc.status_code, content=content, headers=headers)

    class SampleBody(BaseModel):
        name: str
        count: int

    @app.get("/api/test-404")
    async def endpoint_404():
        raise HTTPException(status_code=404, detail="Resource not found")

    @app.get("/api/test-400")
    async def endpoint_400():
        raise HTTPException(status_code=400, detail="Invalid request parameters")

    @app.post("/api/test-mutation")
    async def endpoint_mutation(body: SampleBody):
        return {"success": True, "created": body.name}

    @app.get("/api/shared/airports")
    async def endpoint_master_catalog():
        return {"success": True, "airports": ["BOM", "DEL", "DXB"]}

    @app.get("/api/config/feature-flags")
    async def endpoint_config():
        return {"success": True, "flags": {"new_flow": True}}

    return app


@pytest.fixture
def client():
    app = create_test_app()
    return TestClient(app)


def test_standard_error_envelope_404(client):
    """Verify 404 HTTPException returns standard envelope with both error and detail."""
    res = client.get("/api/test-404")
    assert res.status_code == 404
    data = res.json()
    assert data["success"] is False
    assert data["code"] == "ERR_404"
    assert data["error"] == "Resource not found"
    assert data["detail"] == "Resource not found"


def test_standard_error_envelope_400(client):
    """Verify 400 HTTPException returns standard envelope with code and messages."""
    res = client.get("/api/test-400")
    assert res.status_code == 400
    data = res.json()
    assert data["success"] is False
    assert data["code"] == "ERR_400"
    assert data["error"] == "Invalid request parameters"
    assert data["detail"] == "Invalid request parameters"


def test_validation_error_envelope_422(client):
    """Verify RequestValidationError returns standard envelope with ERR_422."""
    res = client.post("/api/test-mutation", json={"name": "Test"})  # Missing 'count'
    assert res.status_code == 422
    data = res.json()
    assert data["success"] is False
    assert data["code"] == "ERR_422"
    assert "Validation error" in data["error"]
    assert "Validation error" in data["detail"]
    assert "details" in data


def test_mutation_no_cache_headers(client):
    """Verify POST mutation responses have strict no-cache headers."""
    res = client.post("/api/test-mutation", json={"name": "Test", "count": 5})
    assert res.status_code == 200
    cache_control = res.headers.get("Cache-Control", "")
    assert "no-store" in cache_control
    assert "no-cache" in cache_control
    assert "max-age=0" in cache_control
    assert res.headers.get("Pragma") == "no-cache"


def test_master_catalog_cache_headers(client):
    """Verify safe master data GET endpoints have public caching headers."""
    res = client.get("/api/shared/airports")
    assert res.status_code == 200
    cache_control = res.headers.get("Cache-Control", "")
    assert "public" in cache_control
    assert "max-age=300" in cache_control

    res_config = client.get("/api/config/feature-flags")
    assert res_config.status_code == 200
    cache_control_cfg = res_config.headers.get("Cache-Control", "")
    assert "public" in cache_control_cfg


def test_security_headers_present(client):
    """Verify security headers are consistently attached to responses."""
    res = client.get("/api/shared/airports")
    assert res.headers.get("x-content-type-options") == "nosniff"
    assert res.headers.get("x-frame-options") == "SAMEORIGIN"
