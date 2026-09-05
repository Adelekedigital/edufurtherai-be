from fastapi import Request
from fastapi.responses import JSONResponse


def problem(
    request: Request, status: int, title: str, code: str, detail: str, retryable: bool = False
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "")
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        content={
            "type": "about:blank",
            "title": title,
            "status": status,
            "detail": detail,
            "code": code,
            "retryable": retryable,
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id},
    )
