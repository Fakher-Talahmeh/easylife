from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


class ServiceError(Exception):
    """Business-rule violation raised from services; rendered as a 4xx."""

    status_code = status.HTTP_400_BAD_REQUEST

    def __init__(self, code, detail="", status_code=None, fields=None):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail or code
        self.fields = fields or {}
        if status_code:
            self.status_code = status_code


def exception_handler(exc, context):
    """Uniform error body: {code, detail, fields?} (docs/04-api-spec.md)."""
    if isinstance(exc, ServiceError):
        body = {"code": exc.code, "detail": exc.detail}
        if exc.fields:
            body["fields"] = exc.fields
        return Response(body, status=exc.status_code)

    response = drf_exception_handler(exc, context)
    if response is None:
        return None
    data = response.data
    # simplejwt's InvalidToken carries its own machine code ("token_not_valid").
    if isinstance(data, dict) and "detail" in data and data.get("code") == "token_not_valid":
        response.data = {"code": "token_not_valid", "detail": "Token is invalid or expired."}
    elif isinstance(data, dict) and "detail" in data and len(data) == 1:
        code = getattr(data["detail"], "code", "error")
        response.data = {"code": code, "detail": str(data["detail"])}
    else:
        response.data = {"code": "validation_error", "detail": "Invalid input.", "fields": data}
    return response
