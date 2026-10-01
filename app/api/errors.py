from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, status_code: int, error: str, detail: str | None = None):
        self.status_code = status_code
        self.error = error
        self.detail = detail


def register(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        body = {"error": exc.error}
        if exc.detail:
            body["detail"] = exc.detail
        return JSONResponse(body, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        detail = "; ".join(f"{'.'.join(map(str, e['loc'][1:]))}: {e['msg']}" for e in exc.errors())
        return JSONResponse({"error": "invalid_request", "detail": detail}, status_code=422)
