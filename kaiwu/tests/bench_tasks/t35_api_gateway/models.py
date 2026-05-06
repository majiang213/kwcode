"""API Gateway: routing, auth, and request transformation."""

from typing import Any, Callable, Optional


class GatewayRequest:
    def __init__(self, method: str, path: str, headers: dict = None,
                 body: Any = None, query: dict = None):
        self.method = method.upper()
        self.path = path
        self.headers = headers or {}
        self.body = body
        self.query = query or {}
        self.context: dict = {}


class GatewayResponse:
    def __init__(self, status: int = 200, body: Any = None, headers: dict = None):
        self.status = status
        self.body = body
        self.headers = headers or {}


class ServiceRoute:
    """Maps a gateway path prefix to a backend service."""

    def __init__(self, prefix: str, service_name: str,
                 strip_prefix: bool = True,
                 transform_request: Optional[Callable] = None,
                 transform_response: Optional[Callable] = None):
        self.prefix = prefix.rstrip("/")
        self.service_name = service_name
        self.strip_prefix = strip_prefix
        self.transform_request = transform_request
        self.transform_response = transform_response

    def matches(self, path: str) -> bool:
        return path == self.prefix or path.startswith(self.prefix + "/")

    def rewrite_path(self, path: str) -> str:
        if self.strip_prefix:
            remainder = path[len(self.prefix):]
            return remainder if remainder else "/"
        return path
