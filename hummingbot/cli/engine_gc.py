"""Keep the engine's process-lifetime imports out of full GC scans."""
import gc
from importlib import import_module

_prepared = False


def prepare_engine_gc():
    """Prepare fixed dependencies before creating the loop and service host."""
    global _prepared
    if _prepared:
        return
    for name in ('hummingbot.client.runner', 'hummingbot.core.bot_ownership',
                 'hummingbot.model.sql_connection_manager'):
        import_module(name)
    # Reclaim any import-time cycles before freezing the remaining graph.
    gc.collect()
    gc.freeze()
    _prepared = True
