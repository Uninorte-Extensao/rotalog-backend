"""Correlação de requisições por UUID."""

from uuid import UUID, uuid4

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from rotalog.core.data_mode import synthetic_data

REQUEST_ID_HEADER = "X-Request-Id"


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Propaga um request ID válido ou gera um UUID4."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        received_request_id = request.headers.get(REQUEST_ID_HEADER)
        request_id = self._resolve_request_id(received_request_id)
        request.state.request_id = request_id

        token = synthetic_data.set(request.app.state.settings.demo_enabled)
        try:
            response = await call_next(request)
        finally:
            synthetic_data.reset(token)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    @staticmethod
    def _resolve_request_id(received_request_id: str | None) -> str:
        if received_request_id:
            try:
                return str(UUID(received_request_id))
            except ValueError:
                pass

        return str(uuid4())
