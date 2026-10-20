"""Move an existing-operation READ before a new source collection.

This local one-use token skips only the duplicate Python read. The financial SQL
still verifies the original cut, owner, permanent identity and request at COMMIT.
It never restores an unknown operation, refreshes facts or grants HTTP authority.
"""
import asyncio
from dataclasses import dataclass

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, identifier


@dataclass(frozen=True)
class _OperationRead:
    repository: object
    runtime: object
    owner: object
    binding: object
    operation_id: str
    task: object


async def preflight_new_operation(repository, operation_id):
    identifier(operation_id)
    repository._original()
    registry = repository.__dict__.setdefault('_operation_preflights', {})
    if operation_id in registry or len(registry) >= repository.max_rows:
        raise StorageError('PG_OPERATION_PREFLIGHT_CAPACITY_OR_USED')
    if await repository.lookup(operation_id, repository.binding) is not None:
        return None
    repository._original()
    token = _OperationRead(repository, repository.runtime, repository.owner, repository.binding,
                           operation_id, asyncio.current_task())
    registry[operation_id] = token
    return token


def consume_operation_read(repository, operation_id, token):
    original = repository.__dict__.get('_operation_preflights', {}).pop(operation_id, None)
    if (type(token) is not _OperationRead or original is not token
            or token.repository is not repository or token.runtime is not repository.runtime
            or token.owner != repository.owner or token.binding != repository.binding
            or token.operation_id != operation_id or token.task is not asyncio.current_task()):
        raise StorageError('PG_OPERATION_PREFLIGHT_ORIGIN_REQUIRED')
