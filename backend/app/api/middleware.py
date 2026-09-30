"""Request id, security headers, body-size limit and HTTP metrics/access logs."""

from __future__ import annotations

import logging
import re
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import request_id_var
from app.observability import metrics

log = logging.getLogger("mindguard.access")
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (b"cross-origin-opener-policy", b"same-origin"),
]
API_CSP = (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'")


class PayloadTooLarge(Exception):
    pass


class MindGuardMiddleware:
    def __init__(self, app: ASGIApp, max_body_bytes: int):
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        incoming = headers.get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _SAFE_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status_holder = {"status": 500}
        path: str = scope.get("path", "")
        declared = headers.get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_body_bytes:
            await self._reject(send, request_id)
            request_id_var.reset(token)
            return
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    raise PayloadTooLarge()
            return message

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                extra = [(b"x-request-id", request_id.encode()), *SECURITY_HEADERS]
                if not path.startswith(("/docs", "/redoc")):
                    extra.append(API_CSP)
                message["headers"] = list(message.get("headers", [])) + extra
            await send(message)

        try:
            await self.app(scope, limited_receive, send_wrapper)
        except PayloadTooLarge:
            await self._reject(send, request_id)
            status_holder["status"] = 413
        finally:
            elapsed = time.perf_counter() - started
            route = scope.get("route")
            template = getattr(route, "path", None) or "unmatched"
            method = scope.get("method", "GET")
            metrics.HTTP_REQUESTS.labels(method, template, str(status_holder["status"])).inc()
            metrics.HTTP_LATENCY.labels(method, template).observe(elapsed)
            log.info("request", extra={"method": method, "route": template, "status": status_holder["status"],
                                       "latency_ms": round(elapsed * 1000, 2)})
            request_id_var.reset(token)

    @staticmethod
    async def _reject(send: Send, request_id: str) -> None:
        body = b'{"error":{"code":"payload_too_large","message":"request body too large","details":null,"request_id":"' + \
            request_id.encode() + b'"}}'
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"x-request-id", request_id.encode()), *SECURITY_HEADERS]})
        await send({"type": "http.response.body", "body": body})
