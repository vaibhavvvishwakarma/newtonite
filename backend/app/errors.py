"""One error type for the whole API.

Every error response has the same shape, so the frontend can handle them in
one place:

    {"error": {"code": "version_conflict", "message": "...", ...extra}}
"""


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra

    def to_body(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, **self.extra}}


def not_found(what: str = "Item") -> ApiError:
    return ApiError(404, "not_found", f"{what} not found")


def forbidden(message: str = "You do not have permission to do this") -> ApiError:
    return ApiError(403, "forbidden", message)
