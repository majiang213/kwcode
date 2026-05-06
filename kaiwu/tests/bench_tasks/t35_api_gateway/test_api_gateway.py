"""Tests for API gateway system."""

import pytest
from models import GatewayRequest, GatewayResponse, ServiceRoute
from gateway import Gateway, ServiceRegistry
from balancer import RoundRobinBalancer, BackendInstance


def make_service(name: str):
    def handler(req):
        return GatewayResponse(200, {"service": name, "path": req.path})
    return handler


class TestServiceRoute:
    def test_exact_match(self):
        r = ServiceRoute("/api/users", "users-svc")
        assert r.matches("/api/users") is True

    def test_prefix_match(self):
        r = ServiceRoute("/api/users", "users-svc")
        assert r.matches("/api/users/123") is True

    def test_no_match(self):
        r = ServiceRoute("/api/users", "users-svc")
        assert r.matches("/api/posts") is False

    def test_strip_prefix(self):
        r = ServiceRoute("/api/users", "users-svc", strip_prefix=True)
        assert r.rewrite_path("/api/users/123") == "/123"

    def test_strip_prefix_root(self):
        r = ServiceRoute("/api/users", "users-svc", strip_prefix=True)
        assert r.rewrite_path("/api/users") == "/"

    def test_no_strip_prefix(self):
        r = ServiceRoute("/api/users", "users-svc", strip_prefix=False)
        assert r.rewrite_path("/api/users/123") == "/api/users/123"


class TestGatewayRouting:
    def _make_gateway(self):
        reg = ServiceRegistry()
        reg.register("users-svc", make_service("users"))
        reg.register("posts-svc", make_service("posts"))
        gw = Gateway(reg)
        return gw, reg

    def test_routes_to_correct_service(self):
        gw, _ = self._make_gateway()
        gw.add_route(ServiceRoute("/api/users", "users-svc"))
        gw.add_route(ServiceRoute("/api/posts", "posts-svc"))
        resp = gw.dispatch(GatewayRequest("GET", "/api/users/42"))
        assert resp.status == 200
        assert resp.body["service"] == "users"

    def test_404_on_no_match(self):
        gw, _ = self._make_gateway()
        resp = gw.dispatch(GatewayRequest("GET", "/unknown"))
        assert resp.status == 404

    def test_longest_prefix_wins(self):
        """More specific route should win over less specific."""
        reg = ServiceRegistry()
        reg.register("api-svc", make_service("api"))
        reg.register("users-svc", make_service("users"))
        gw = Gateway(reg)
        gw.add_route(ServiceRoute("/api", "api-svc"))
        gw.add_route(ServiceRoute("/api/users", "users-svc"))
        resp = gw.dispatch(GatewayRequest("GET", "/api/users/1"))
        assert resp.body["service"] == "users"

    def test_auth_rejects_unauthorized(self):
        gw, _ = self._make_gateway()
        gw.add_route(ServiceRoute("/api/users", "users-svc"))
        gw.set_auth(lambda req: "Authorization" in req.headers)
        resp = gw.dispatch(GatewayRequest("GET", "/api/users"))
        assert resp.status == 401

    def test_auth_allows_authorized(self):
        gw, _ = self._make_gateway()
        gw.add_route(ServiceRoute("/api/users", "users-svc"))
        gw.set_auth(lambda req: "Authorization" in req.headers)
        resp = gw.dispatch(GatewayRequest("GET", "/api/users",
                                          headers={"Authorization": "Bearer token"}))
        assert resp.status == 200

    def test_path_rewritten_before_service(self):
        reg = ServiceRegistry()
        received_paths = []
        def capture_handler(req):
            received_paths.append(req.path)
            return GatewayResponse(200, {})
        reg.register("svc", capture_handler)
        gw = Gateway(reg)
        gw.add_route(ServiceRoute("/api/v1", "svc", strip_prefix=True))
        gw.dispatch(GatewayRequest("GET", "/api/v1/resource"))
        assert received_paths == ["/resource"]

    def test_502_when_service_not_registered(self):
        reg = ServiceRegistry()
        gw = Gateway(reg)
        gw.add_route(ServiceRoute("/api", "missing-svc"))
        resp = gw.dispatch(GatewayRequest("GET", "/api/test"))
        assert resp.status == 502


class TestRoundRobinBalancer:
    def test_distributes_across_instances(self):
        balancer = RoundRobinBalancer()
        calls = []
        for i in range(3):
            inst = BackendInstance(f"inst-{i}", lambda req, n=i: calls.append(n) or f"resp-{n}")
            balancer.add_instance(inst)
        for _ in range(6):
            balancer(GatewayRequest("GET", "/"))
        # Each instance should be called twice
        assert calls.count(0) == 2
        assert calls.count(1) == 2
        assert calls.count(2) == 2

    def test_skips_unhealthy_instances(self):
        balancer = RoundRobinBalancer()
        calls = []
        inst0 = BackendInstance("inst-0", lambda req: calls.append(0) or "ok")
        inst1 = BackendInstance("inst-1", lambda req: calls.append(1) or "ok")
        inst0.healthy = False
        balancer.add_instance(inst0)
        balancer.add_instance(inst1)
        for _ in range(3):
            balancer(GatewayRequest("GET", "/"))
        assert 0 not in calls
        assert calls.count(1) == 3

    def test_raises_when_no_healthy(self):
        balancer = RoundRobinBalancer()
        inst = BackendInstance("inst-0", lambda req: "ok")
        inst.healthy = False
        balancer.add_instance(inst)
        with pytest.raises(RuntimeError):
            balancer(GatewayRequest("GET", "/"))
