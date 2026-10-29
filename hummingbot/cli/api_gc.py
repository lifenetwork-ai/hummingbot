"""Exclude the API's fixed startup graph from later full-heap GC scans."""
import gc
import logging
from importlib import import_module

_prepared = False


def prepare_api_gc():
    """Run once, before constructing the host, pools or request workers.

    These modules are required by ordinary API startup and stay loaded for the
    process lifetime. Collect import garbage before freezing that initial graph.
    Later request objects keep normal automatic GC and unchanged thresholds.
    A second invocation must never freeze an active request or connection graph.
    """
    global _prepared
    if _prepared:
        return
    for name in ('hummingbot.cli.admin_api', 'hummingbot.model.bot_state_store',
                 'hummingbot.model.db_migration.postgres.connection',
                 'hummingbot.model.db_migration.postgres.runner'):
        import_module(name)
    gc.collect()
    gc.freeze()
    _prepared = True
    logger = logging.getLogger(__name__)
    logger.debug('API startup GC graph prepared objects=%s', gc.get_freeze_count(),
                 extra={'event': 'api_gc_prepared'})
