from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


MESSAGES = {
    "invalid_request": "Some required information is missing or wrong.",
    "invalid_image": "The image could not be opened. Please choose or take the picture again.",
    "model_unavailable": "The X-ray reader is not running. Please try again in a few minutes.",
    "inference_timeout": "Reading the X-ray took too long. Please submit it again.",
    "scan_not_found": "No scan was found with this ID.",
    "not_xray": "This does not look like an X-ray. Please upload the X-ray image itself.",
    "unsupported_body_part": "This kind of X-ray cannot be read here yet. Please send it to a clinician.",
    "not_frontal_view": "This looks like a side view. Please upload the front (PA) view of the chest.",
    "invalid_model_output": "The X-ray could not be read. Please submit it again.",
    "internal_error": "Something went wrong while reading the X-ray. Please submit it again.",
}


class ApiError(Exception):
    def __init__(self, status_code: int, error: str, message: str | None = None, detail: str | None = None):
        self.status_code = status_code
        self.error = error
        self.message = message or MESSAGES[error]
        self.detail = detail


def register(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        body = {"error": exc.error, "message": exc.message}
        if exc.detail:
            body["detail"] = exc.detail
        return JSONResponse(body, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        detail = "; ".join(f"{'.'.join(map(str, e['loc'][1:]))}: {e['msg']}" for e in exc.errors())
        body = {"error": "invalid_request", "message": MESSAGES["invalid_request"], "detail": detail}
        return JSONResponse(body, status_code=422)
