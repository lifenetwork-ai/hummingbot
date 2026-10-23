"""Complete relational File ancestry, before operational native translation.

Original operation/terminal/receipt/session/recorder/product records retain
their identities and values. Materialized segments are evidence, not new
native commands. Neither this seal nor its readback can publish an account.
"""
import hashlib
import json

from hummingbot.strategy_v2.life_liquidity.checkpoint_archive import decode
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, encode, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.postgres.file_financial import PgFileFinancialStaging
from hummingbot.strategy_v2.life_liquidity.storage.postgres.file_staging import FileStageReceipt
from hummingbot.strategy_v2.life_liquidity.storage.stopped_capital import _journal


def _history_json(value):
    # File service/reporting metadata includes finite JSON floats. Preserve
    # those original JSON types; monetary decimal strings are never coerced.
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    except (ValueError, TypeError, RecursionError):
        raise StorageError('PG_FILE_HISTORY_JSON_INVALID') from None


class PgFileHistoryStaging(PgFileFinancialStaging):
    @staticmethod
    def _documents(source):
        archives = dict(source.archives)
        documents = dict(checkpoint=decode(source.base.core.checkpoint_bytes), service=decode(source.base.service_json.encode()),
                         recorder=decode(source.base.recorder.rows_json.encode()))
        documents.update({'journal:' + name: _journal(source, name) for name, _ in source.base.core.journal_images})
        documents.update({'ancillary:' + name: archives['ancillary.' + name].materialize()
                          if 'ancillary.' + name in archives else decode(raw) for name, raw in source.base.ancillary_images})
        documents['manifest'] = dict(format='LIFE_FILE_HISTORY_STAGING_V1', source_digest=source.content_digest,
                                     core_digest=source.base.core.content_digest, recorder_digest=source.base.recorder.content_digest,
                                     archives={name: archive.content_digest for name, archive in source.archives})
        return documents

    @staticmethod
    def _history_rows(documents, command):
        rows, total = [], 0

        def add(document, path, value):
            nonlocal total
            location, body = encode(path), _history_json(value)
            total += len(document.encode()) + len(location) + len(body)
            # Bound paths below the btree entry-size limit as well as payloads.
            if (len(document) > 128 or len(location) > 1024 or len(body) > 1048576
                    or total > command.max_bytes or len(rows) >= command.max_rows):
                raise StorageError('PG_FILE_HISTORY_CAPACITY')
            rows.append((document, location, body))

        def visit(document, path, value):
            # Split collections, not every field of a monetary/receipt record.
            # Each record keeps its exact original body and source identity.
            if document == 'checkpoint':
                boundary = path in ([], ['state'], ['state', 'resources'], ['state', 'operations']) or (
                    len(path) == 3 and path[:2] == ['state', 'resources'])
            elif document == 'recorder':
                boundary = path in ([], ['tables']) or len(path) == 2 and path[0] == 'tables'
            elif document.startswith('journal:'):
                boundary = not path or len(path) == 1 and path[0] in ('records', 'reservations', 'trades', 'account_events', 'events', 'entries')
            elif document.startswith('ancillary:'):
                boundary = path in ([], ['state']) or len(path) == 2 and path[0] == 'state' or len(path) == 1 and path[0] in ('records', 'events')
            else:
                boundary = False
            split = boundary and type(value) in (dict, list)
            add(document, path, ({} if type(value) is dict else []) if split else value)
            if split:
                items = value.items() if type(value) is dict else enumerate(value)
                for key, child in items:
                    visit(document, [*path, key], child)
        for document, value in sorted(documents.items()):
            visit(document, [], value)
        return tuple(sorted(rows))

    @staticmethod
    def _restore_documents(rows):
        documents, paths = {}, {}
        try:
            def order(row):
                path = decode(row[1])
                return row[0], len(path), tuple((type(part) is str, part) for part in path)
            for document, path_bytes, body in sorted(rows, key=order):
                path, value = decode(path_bytes), decode(body)
                if type(path) is not list or len(path) > 4 or encode(path) != path_bytes or _history_json(value) != body:
                    raise ValueError()
                key = (document, path_bytes)
                if key in paths:
                    raise ValueError()
                if not path:
                    if document in documents:
                        raise ValueError()
                    documents[document] = value
                else:
                    parent_key = (document, encode(path[:-1]))
                    # A child may extend only an explicitly stored empty
                    # container, never silently overwrite a complete record.
                    parent_original = paths[parent_key]
                    parent = documents[document]
                    for part in path[:-1]:
                        parent = parent[part]
                    part = path[-1]
                    if type(parent_original) is dict and parent_original == {}:
                        if type(part) is not str or part in parent:
                            raise ValueError()
                        parent[part] = value
                    elif type(parent_original) is list and parent_original == []:
                        if type(part) is not int or part != len(parent):
                            raise ValueError()
                        parent.append(value)
                    else:
                        raise ValueError()
                # Keep the immutable original marker separately from the tree
                # which will acquire children during reconstruction.
                paths[key] = decode(body)
            return documents
        except (ValueError, KeyError, TypeError, IndexError, RecursionError):
            raise StorageError('PG_FILE_HISTORY_PARITY_FAILED') from None

    async def translate_history(self, command, *, batch_rows=128):
        if type(batch_rows) is not int or not 0 < batch_rows <= 512:
            raise StorageError('PG_FILE_HISTORY_BATCH_INVALID')
        return await self._owned(command, self._translate_history, batch_rows)

    async def history_documents(self, command):
        return await self._owned(command, self._history_documents)

    async def _history_expected(self, command):
        # Financial and complete-history seals must bind the same frozen cut.
        financial = await self._financial_rows(command)
        source = await self._read(command)
        documents = await self._in_thread(self._documents, source)
        rows = await self._in_thread(self._history_rows, documents, command)
        return documents, rows, self._manifest(financial)

    async def _history_batch(self, command, offset, rows):
        async def write():
            if (await self._row(command))['state'] != 'SEALED':
                raise StorageError('PG_FILE_STAGE_INCOMPLETE')
            expected = [(offset + i, doc, path.decode(), body, hashlib.sha256(body).hexdigest())
                        for i, (doc, path, body) in enumerate(rows)]
            old = await self.connection.fetch('''SELECT sequence,document,path,body,sha256 FROM life_storage.file_history_rows
                WHERE operation_id=$1 AND sequence>=$2 AND sequence<$3 ORDER BY sequence''', command.operation_id, offset, offset + len(rows))
            known = {r['sequence']: (r['sequence'], r['document'], encode(json.loads(r['path'])).decode(), r['body'], r['sha256']) for r in old}
            if any(known.get(r[0], r) != r for r in expected):
                raise StorageError('PG_FILE_HISTORY_CONFLICT')
            missing = [(command.operation_id, *r) for r in expected if r[0] not in known]
            if missing:
                if await self.connection.fetchval('SELECT EXISTS(SELECT 1 FROM life_storage.file_history_seals WHERE operation_id=$1)', command.operation_id):
                    raise StorageError('PG_FILE_HISTORY_CONFLICT')
                await self.connection.executemany('INSERT INTO life_storage.file_history_rows VALUES($1,$2,$3,$4::jsonb,$5,$6)', missing)
        await self._transaction(command, write)

    @staticmethod
    def _history_manifest(rows):
        return fingerprint([[doc, decode(path), hashlib.sha256(body).hexdigest()] for doc, path, body in rows])

    @staticmethod
    def _history_bytes(rows):
        return sum(len(doc.encode()) + len(path) + len(body) for doc, path, body in rows)

    async def _history_compare(self, command, expected, documents):
        stored = await self.connection.fetch('''SELECT sequence,document,path,body,sha256 FROM life_storage.file_history_rows
            WHERE operation_id=$1 ORDER BY sequence LIMIT $2''', command.operation_id, len(expected) + 1)
        actual = tuple((r['document'], encode(json.loads(r['path'])), r['body']) for r in stored)
        if (actual != expected or any(r['sequence'] != i or hashlib.sha256(r['body']).hexdigest() != r['sha256'] for i, r in enumerate(stored))):
            raise StorageError('PG_FILE_HISTORY_PARITY_FAILED')
        # Independently rebuild the logical source; hashes alone cannot prove
        # complete namespaces, original receipts or container/list coverage.
        restored = await self._in_thread(self._restore_documents, actual)
        if _history_json(restored) != _history_json(documents):
            raise StorageError('PG_FILE_HISTORY_PARITY_FAILED')
        return restored

    async def _translate_history(self, command, batch_rows):
        documents, expected, financial_hash = await self._history_expected(command)
        # A hint only: skip batch round-trips after sealing, then repeat the
        # complete guarded parity check below. This cannot adopt missing rows.
        sealed = await self.connection.fetchval('SELECT EXISTS(SELECT 1 FROM life_storage.file_history_seals WHERE operation_id=$1)', command.operation_id)
        if not sealed:
            for offset in range(0, len(expected), batch_rows):
                await self._history_batch(command, offset, expected[offset:offset + batch_rows])

        async def seal():
            await self._history_compare(command, expected, documents)
            values = (command.operation_id, self._history_manifest(expected), financial_hash, len(expected), self._history_bytes(expected))
            old = await self.connection.fetchrow('SELECT * FROM life_storage.file_history_seals WHERE operation_id=$1', command.operation_id)
            if old is not None and tuple(old.values()) != values:
                raise StorageError('PG_FILE_HISTORY_PARITY_FAILED')
            if old is None:
                await self.connection.execute('INSERT INTO life_storage.file_history_seals VALUES($1,$2,$3,$4,$5)', *values)
            return FileStageReceipt(command.operation_id, 'HISTORY_STAGED', old is not None)
        return await self._transaction(command, seal, seal=True)

    async def _history_documents(self, command):
        documents, expected, financial_hash = await self._history_expected(command)

        async def read():
            seal = await self.connection.fetchrow('SELECT * FROM life_storage.file_history_seals WHERE operation_id=$1', command.operation_id)
            if (seal is None or seal['manifest_sha256'] != self._history_manifest(expected) or seal['financial_sha256'] != financial_hash
                    or seal['row_count'] != len(expected) or seal['total_bytes'] != self._history_bytes(expected)):
                raise StorageError('PG_FILE_HISTORY_INCOMPLETE')
            return await self._history_compare(command, expected, documents)
        return await self._transaction(command, read)
