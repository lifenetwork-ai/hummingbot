"""Resumable verified financial facts in owner-only import staging.

This is a relational translation of File economic facts, not operational native
account history. Publication must still map policies/product roots/recorder
ancestry and independently prove native parity. A seal cannot enable trading.
"""
import hashlib

from hummingbot.strategy_v2.life_liquidity.checkpoint_archive import decode
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, encode, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.postgres.file_staging import FileStageReceipt, PgFileStaging
from hummingbot.strategy_v2.life_liquidity.storage.stopped_capital import project_stopped_capital


class PgFileFinancialStaging(PgFileStaging):
    @staticmethod
    def _financial_facts(source, command):
        projection = project_stopped_capital(source, max_rows=command.max_rows).json()
        resources = decode(source.base.core.checkpoint_bytes)['state']['resources']
        rows = []
        total_bytes = 0

        def add(family, key, value):
            nonlocal total_bytes
            body = encode(value)
            total_bytes += len(body)
            if (type(key) is not str or not 0 < len(key) <= 256 or len(body) > 1048576
                    or len(rows) >= command.max_rows or total_bytes > command.max_bytes):
                raise StorageError('PG_FILE_FINANCIAL_CAPACITY')
            rows.append((family, key, body))
        for key in ('accounting_policy', 'capital'):
            add(key.upper(), 'original', projection[key])
        add('SOURCE', 'original', dict(translation_format='LIFE_FILE_FINANCIAL_STAGING_V1',
                                       **{k: projection[k] for k in ('format', 'source_digest', 'source_facts_sha256')}))
        for asset, value in projection['balances'].items():
            add('BALANCE', asset, value)
        for family, key in (('PREPARE', 'commands'), ('FILL', 'fills'), ('CASHFLOW', 'cashflows'),
                            ('JOINT_CASH', 'joint_cashflows'), ('NAV', 'nav_operations'),
                            ('CASHFLOW_APPROVAL', 'additional_cashflow_approvals')):
            for identity, value in resources.get(key, {}).items():
                add(family, identity, value)
        for name, budget in projection['budgets'].items():
            family = name.upper()
            if budget is None:
                add(family + '_POLICY', 'original', None)
                continue
            journal = budget['journal']
            branch = 'events' if name == 'loss' else 'entries'
            add(family + '_POLICY', 'original', {k: v for k, v in journal.items() if k != branch})
            for identity, value in journal[branch].items():
                add(family + '_EVENT', identity, value)
            add(family + '_CAMPAIGN_TOTAL', 'original', budget['totals']['campaign'])
            for scope in ('by_session', 'by_day'):
                for identity, value in budget['totals'][scope].items():
                    add(family + '_' + scope.upper(), identity, value)
        if len(rows) > command.max_rows or sum(len(body) for _, _, body in rows) > command.max_bytes:
            raise StorageError('PG_FILE_FINANCIAL_CAPACITY')
        return tuple(sorted(rows))

    async def translate_financial(self, command, *, batch_rows=128):
        if type(batch_rows) is not int or not 0 < batch_rows <= 512:
            raise StorageError('PG_FILE_FINANCIAL_BATCH_INVALID')
        return await self._owned(command, self._translate_financial, batch_rows)

    async def financial_rows(self, command):
        return await self._owned(command, self._financial_rows)

    async def _expected(self, command):
        source = await self._read(command)
        return await self._in_thread(self._financial_facts, source, command)

    @staticmethod
    def _manifest(rows):
        return fingerprint([[family, key, hashlib.sha256(body).hexdigest()] for family, key, body in rows])

    async def _financial_batch(self, command, offset, rows):
        async def write():
            stage = await self._row(command)
            if stage['state'] != 'SEALED':
                raise StorageError('PG_FILE_STAGE_INCOMPLETE')
            expected = [(offset + i, family, key, body, hashlib.sha256(body).hexdigest())
                        for i, (family, key, body) in enumerate(rows)]
            old = await self.connection.fetch('''SELECT sequence,family,identity,body,sha256 FROM life_storage.file_financial_rows
                WHERE operation_id=$1 AND sequence>=$2 AND sequence<$3 ORDER BY sequence''', command.operation_id, offset, offset + len(rows))
            known = {r['sequence']: tuple(r.values()) for r in old}
            if any(known.get(r[0], r) != r for r in expected):
                raise StorageError('PG_FILE_FINANCIAL_CONFLICT')
            missing = [(command.operation_id, *r) for r in expected if r[0] not in known]
            if missing:
                if await self.connection.fetchval('SELECT EXISTS(SELECT 1 FROM life_storage.file_financial_seals WHERE operation_id=$1)', command.operation_id):
                    raise StorageError('PG_FILE_FINANCIAL_CONFLICT')
                await self.connection.executemany('INSERT INTO life_storage.file_financial_rows VALUES($1,$2,$3,$4,$5,$6)', missing)
        await self._transaction(command, write)

    async def _compare(self, command, expected):
        stored = await self.connection.fetch('''SELECT sequence,family,identity,body,sha256 FROM life_storage.file_financial_rows
            WHERE operation_id=$1 ORDER BY sequence LIMIT $2''', command.operation_id, len(expected) + 1)
        actual = tuple((r['family'], r['identity'], r['body']) for r in stored)
        if (actual != expected or any(r['sequence'] != i or hashlib.sha256(r['body']).hexdigest() != r['sha256'] for i, r in enumerate(stored))):
            raise StorageError('PG_FILE_FINANCIAL_PARITY_FAILED')
        return actual

    async def _translate_financial(self, command, batch_rows):
        expected = await self._expected(command)
        for offset in range(0, len(expected), batch_rows):
            await self._financial_batch(command, offset, expected[offset:offset + batch_rows])

        async def seal():
            await self._compare(command, expected)
            values = (command.operation_id, self._manifest(expected), len(expected), sum(len(r[2]) for r in expected))
            old = await self.connection.fetchrow('SELECT * FROM life_storage.file_financial_seals WHERE operation_id=$1', command.operation_id)
            if old is not None and tuple(old.values()) != values:
                raise StorageError('PG_FILE_FINANCIAL_PARITY_FAILED')
            if old is None:
                await self.connection.execute('INSERT INTO life_storage.file_financial_seals VALUES($1,$2,$3,$4)', *values)
            return FileStageReceipt(command.operation_id, 'FINANCIAL_STAGED', old is not None)
        return await self._transaction(command, seal, seal=True)

    async def _financial_rows(self, command):
        expected = await self._expected(command)

        async def read():
            seal = await self.connection.fetchrow('SELECT * FROM life_storage.file_financial_seals WHERE operation_id=$1', command.operation_id)
            if (seal is None or seal['manifest_sha256'] != self._manifest(expected) or seal['row_count'] != len(expected)
                    or seal['total_bytes'] != sum(len(r[2]) for r in expected)):
                raise StorageError('PG_FILE_FINANCIAL_INCOMPLETE')
            return await self._compare(command, expected)
        return await self._transaction(command, read)
