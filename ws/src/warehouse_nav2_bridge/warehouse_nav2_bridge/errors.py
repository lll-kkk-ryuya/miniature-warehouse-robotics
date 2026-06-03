"""Nav2 Bridge REST error model (doc mode-a/12a §エラーコード, 12a:345-363).

A single exception type carries the documented ``error_code`` + HTTP status so the
pure core (``core.py``) can ``raise`` validation failures while staying free of
FastAPI; the thin app layer (``app.py``) turns it into the canonical error body::

    {"status": "error", "error_code": "INVALID_LOCATION", "detail": "..."}

Pure stdlib — unit-testable without FastAPI / rclpy.
"""


class Nav2BridgeError(Exception):
    """A REST-level failure with the doc12a ``error_code`` and HTTP status.

    ``error_code`` is one of the doc12a:347-354 codes (INVALID_ROBOT,
    INVALID_LOCATION, INVALID_VIA, INVALID_DURATION, ALREADY_NAVIGATING,
    NAV2_NOT_READY); ``http_status`` is its 400/409/503 mapping.
    """

    def __init__(self, error_code: str, detail: str, http_status: int) -> None:
        """Store the doc12a error code, human detail, and HTTP status."""
        super().__init__(detail)
        self.error_code = error_code
        self.detail = detail
        self.http_status = http_status

    def to_payload(self) -> dict[str, str]:
        """Render the canonical error response body (doc12a:357-363)."""
        return {"status": "error", "error_code": self.error_code, "detail": self.detail}
