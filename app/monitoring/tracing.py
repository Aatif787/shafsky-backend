import uuid
import contextvars
from typing import Optional

_correlation_id_ctx_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("correlation_id", default=None)
_request_id_ctx_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("request_id", default=None)

class TracingEngine:
    @staticmethod
    def generate_id(prefix: str = "id") -> str:
        return f"{prefix}_{uuid.uuid4().hex[:12]}"

    @classmethod
    def set_correlation_id(cls, cid: str):
        _correlation_id_ctx_var.set(cid)

    @classmethod
    def get_correlation_id(cls) -> str:
        cid = _correlation_id_ctx_var.get()
        if not cid:
            cid = cls.generate_id("corr")
            _correlation_id_ctx_var.set(cid)
        return cid

    @classmethod
    def set_request_id(cls, rid: str):
        _request_id_ctx_var.set(rid)

    @classmethod
    def get_request_id(cls) -> str:
        rid = _request_id_ctx_var.get()
        if not rid:
            rid = cls.generate_id("req")
            _request_id_ctx_var.set(rid)
        return rid
