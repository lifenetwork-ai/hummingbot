"""Backend-neutral controller application boundary.

Loop callbacks only enqueue work or inspect local fences. Financial checks and
native compatibility helpers require the repository's owning execution context.
No path, file journal, SQL session or repository cache crosses this port.
"""
from functools import wraps
from typing import Literal, Protocol

FinancialGate = Literal['risk', 'loss', 'attribution', 'markout', 'mm', 'bounded', 'reduction']


class BoundedPlannerPort(Protocol):
    def authorizes_config(self, config) -> bool:
        ...


class ControllerApplicationPort(Protocol):
    def enqueue_runner_event(self, kind: str, event) -> None:
        ...

    def update_controller_config(self, config):
        ...

    def config_load_failed(self):
        ...

    def request_halt(self) -> None:
        ...

    async def settle_inventory(self, cycle_id: str, intent_ids: tuple[str, ...]) -> bool:
        ...

    def check_financial_gate(self, gate: FinancialGate) -> bool:
        ...

    def planner_for(self, config) -> BoundedPlannerPort | None:
        ...

    def stop_local(self, action) -> None:
        ...

    def rejected(self, action) -> bool:
        ...

    def dispatched(self, action) -> bool:
        ...

    def recognizes_config(self, config) -> bool:
        ...

    def on_runner_safety_tick(self, timestamp: float) -> None:
        ...

    def on_safety_tick(self, timestamp: float) -> None:
        ...

    async def refresh_processed_data(self) -> None:
        ...

    async def plan(self) -> list:
        ...

    def status_lines(self) -> list[str]:
        ...


def repository_context(method):
    """Keep legacy native helpers worker-only when a repository is installed.

    File construction before runtime installation remains owned by its adapter.
    The original non-repository controller keeps its existing synchronous mode.
    """
    @wraps(method)
    def checked(self, *args, **kwargs):
        runtime = self._storage_runtime
        if runtime is not None:
            runtime.assert_native_context()
        return method(self, *args, **kwargs)
    return checked
