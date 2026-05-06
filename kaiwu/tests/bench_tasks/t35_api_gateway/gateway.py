"""API Gateway router and dispatcher."""

from models import GatewayRequest, GatewayResponse, ServiceRoute
from typing import Callable, Any, Optional


class ServiceRegistry:
    """Registry of backend service handlers."""

    def __init__(self):
        self._services: dict[str, Callable] = {}

    def register(self, name: str, handler: Callable) -> None:
        self._services[name] = handler

    def get(self, name: str) -> Optional[Callable]:
        return self._services.get(name)


class Gateway:
    """API Gateway that routes requests to backend services."""

    def __init__(self, registry: ServiceRegistry):
        self._registry = registry
        self._routes: list[ServiceRoute] = []
        self._auth_fn: Optional[Callable] = None
        self._error_handler: Optional[Callable] = None

    def add_route(self, route: ServiceRoute) -> None:
        self._routes.append(route)

    def set_auth(self, auth_fn: Callable[[GatewayRequest], bool]) -> None:
        """Set authentication function. Returns True if request is authorized."""
        self._auth_fn = auth_fn

    def set_error_handler(self, handler: Callable) -> None:
        self._error_handler = handler

    def _find_route(self, path: str) -> Optional[ServiceRoute]:
        """Find the most specific matching route (longest prefix wins)."""
        matches = [r for r in self._routes if r.matches(path)]
        if not matches:
            return None
        # Bug: returns the first match instead of the longest prefix match
        return matches[0]

    def dispatch(self, request: GatewayRequest) -> GatewayResponse:
        """Route and dispatch a request to the appropriate backend service."""
        # Authentication
        if self._auth_fn and not self._auth_fn(request):
            return GatewayResponse(status=401, body={"error": "Unauthorized"})

        route = self._find_route(request.path)
        if route is None:
            return GatewayResponse(status=404, body={"error": "No route found"})

        # Rewrite path
        rewritten_path = route.rewrite_path(request.path)
        request.context["original_path"] = request.path
        request.context["service"] = route.service_name
        request.path = rewritten_path

        # Apply request transform
        if route.transform_request:
            request = route.transform_request(request)

        # Call backend service
        service = self._registry.get(route.service_name)
        if service is None:
            return GatewayResponse(status=502, body={"error": "Service unavailable"})

        try:
            response = service(request)
        except Exception as e:
            if self._error_handler:
                return self._error_handler(e, request)
            return GatewayResponse(status=500, body={"error": str(e)})

        # Apply response transform
        if route.transform_response:
            response = route.transform_response(response)

        return response
