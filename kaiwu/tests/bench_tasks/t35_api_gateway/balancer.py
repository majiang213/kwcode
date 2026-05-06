"""Load balancer for API gateway backend services."""

from typing import Callable, Any, Optional
import itertools


class BackendInstance:
    def __init__(self, name: str, handler: Callable, weight: int = 1):
        self.name = name
        self.handler = handler
        self.weight = weight
        self.healthy = True
        self.request_count = 0
        self.error_count = 0

    def call(self, request: Any) -> Any:
        self.request_count += 1
        try:
            return self.handler(request)
        except Exception:
            self.error_count += 1
            raise


class RoundRobinBalancer:
    """Round-robin load balancer across backend instances."""

    def __init__(self):
        self._instances: list[BackendInstance] = []
        self._index = 0

    def add_instance(self, instance: BackendInstance) -> None:
        self._instances.append(instance)

    def remove_instance(self, name: str) -> bool:
        before = len(self._instances)
        self._instances = [i for i in self._instances if i.name != name]
        return len(self._instances) < before

    def next_healthy(self) -> Optional[BackendInstance]:
        """Return the next healthy instance in round-robin order."""
        healthy = [i for i in self._instances if i.healthy]
        if not healthy:
            return None
        # Bug: uses self._index against self._instances but iterates healthy subset
        # so index can be out of range or skip instances
        instance = healthy[self._index % len(self._instances)]
        self._index = (self._index + 1) % max(len(self._instances), 1)
        return instance

    def __call__(self, request: Any) -> Any:
        instance = self.next_healthy()
        if instance is None:
            raise RuntimeError("No healthy backend instances")
        return instance.call(request)
