"""Conversão centralizada de exceções para o contrato HTTP da API."""

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from rotalog.api.middleware.request_id import REQUEST_ID_HEADER
from rotalog.core.errors import ErrorContent, ErrorResponse, RotaLogError

logger = logging.getLogger(__name__)

DEFAULT_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
    status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
    status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    status.HTTP_409_CONFLICT: {"model": ErrorResponse},
    status.HTTP_410_GONE: {"model": ErrorResponse},
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
    status.HTTP_429_TOO_MANY_REQUESTS: {"model": ErrorResponse},
    status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
}


def _request_id(request: Request) -> str:
    return request.state.request_id


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = _request_id(request)
    payload = ErrorResponse(
        error=ErrorContent(
            code=code,
            message=message,
            details=details or {},
            request_id=request_id,
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
        headers={REQUEST_ID_HEADER: request_id, **(headers or {})},
    )


async def _handle_rotalog_error(
    request: Request,
    exception: RotaLogError,
) -> JSONResponse:
    return _error_response(
        request,
        status_code=exception.status_code,
        code=exception.code,
        message=exception.message,
        details=exception.details,
        headers={"WWW-Authenticate": "Bearer"}
        if exception.status_code == 401
        else None,
    )


async def _handle_http_error(
    request: Request,
    exception: HTTPException,
) -> JSONResponse:
    message = (
        exception.detail
        if isinstance(exception.detail, str)
        else "Requisição inválida."
    )
    details = (
        {}
        if isinstance(exception.detail, str)
        else {"detail": jsonable_encoder(exception.detail)}
    )
    return _error_response(
        request,
        status_code=exception.status_code,
        code="HTTP_ERROR",
        message=message,
        details=details,
        headers=exception.headers,
    )


async def _handle_validation_error(
    request: Request,
    exception: RequestValidationError,
) -> JSONResponse:
    return _error_response(
        request,
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="REQUEST_VALIDATION_ERROR",
        message="A requisição possui campos inválidos.",
        details={
            "errors": [
                {"loc": list(error["loc"]), "type": error["type"], "msg": error["msg"]}
                for error in exception.errors()
            ],
        },
    )


async def _handle_unexpected_error(
    request: Request,
    exception: Exception,
) -> JSONResponse:
    logger.error(
        "Erro inesperado na API",
        extra={"request_id": _request_id(request), "path": request.url.path},
        exc_info=exception,
    )
    return _error_response(
        request,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="INTERNAL_SERVER_ERROR",
        message="Ocorreu um erro interno.",
    )


def register_exception_handlers(application: FastAPI) -> None:
    """Registra os handlers que aplicam o contrato canônico de erros."""

    application.add_exception_handler(RotaLogError, _handle_rotalog_error)
    application.add_exception_handler(HTTPException, _handle_http_error)
    application.add_exception_handler(RequestValidationError, _handle_validation_error)
    application.add_exception_handler(Exception, _handle_unexpected_error)
