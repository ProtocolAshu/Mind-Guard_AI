"""Domain exceptions mapped to consistent JSON error responses."""

from __future__ import annotations


class MindGuardError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None, details: dict[str, object] | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = details or {}


class NotFoundError(MindGuardError):
    status_code = 404
    code = "not_found"


class ForbiddenError(MindGuardError):
    status_code = 403
    code = "forbidden"


class UnauthorizedError(MindGuardError):
    status_code = 401
    code = "unauthorized"


class ConflictError(MindGuardError):
    status_code = 409
    code = "conflict"


class ValidationFailedError(MindGuardError):
    status_code = 422
    code = "validation_failed"


class RateLimitedError(MindGuardError):
    status_code = 429
    code = "rate_limited"

    def __init__(self, message: str, retry_after: int):
        super().__init__(message)
        self.retry_after = retry_after


class ConsentRequiredError(MindGuardError):
    status_code = 403
    code = "consent_required"


class ServiceUnavailableError(MindGuardError):
    status_code = 503
    code = "service_unavailable"


class InvariantError(MindGuardError):
    """An internal invariant did not hold. Replaces `assert` so the check survives `python -O` (bandit B101)."""

    code = "internal_invariant"
    status_code = 500


def require[T](value: T | None, message: str) -> T:
    """Narrow an optional that must be set at this point, raising instead of asserting."""
    if value is None:
        raise InvariantError(message)
    return value
