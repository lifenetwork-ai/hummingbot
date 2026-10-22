"""Independent fake exchange key/revocation/accepted-write state for PG4 tests."""
import base64
import hashlib
import hmac
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


class OfflineGenerationProvider:
    def __init__(self):
        self.resources, self.keys, self.pending, self.accepted = {}, {}, {}, {}
        self.sequence = 0
        self.before_accept = self.after_accept = None
        self.observed = []

    def register(self, resource, credentials):
        if resource in self.resources or credentials.api_key in self.keys:
            raise AssertionError('fixture generation key reuse')
        self.keys[credentials.api_key] = resource
        self.resources[resource] = dict(credentials=credentials, requested=None, effective=None, rest=False, ws=False, caa=False)

    async def revoke(self, resource):
        self.resources[resource]['requested'] = datetime.now(timezone.utc)

    def effect(self, resource, *, rest=True, ws=True, caa=True):
        r = self.resources[resource]
        assert r['requested'] is not None
        r.update(effective=datetime.now(timezone.utc), rest=rest, ws=ws, caa=caa)

    async def exclusion(self, resource):
        r = self.resources[resource]
        return dict(requested_at=r['requested'], effective_at=r['effective'] or r['requested'], observed_at=datetime.now(timezone.utc),
                    accepted_request_ids=sorted(self.accepted.get(resource, ())), rest_denied=r['rest'], ws_denied=r['ws'], caa_disabled=r['caa'],
                    inflight_reconciled=not any(v == resource for v in self.pending.values()))

    def check_ws(self, api_key, op):
        resource = self.keys[api_key]
        if self.resources[resource]['ws']:
            raise StorageError('OFFLINE_PROVIDER_WS_REVOKED')
        self.observed.append(('WS', op, resource))
        return True

    def wrap(self, http):
        async def request(**kwargs):
            headers = {k.lower(): v for k, v in (kwargs.get('headers') or {}).items()}
            key = headers.get('ok-access-key')
            if key is None:
                return await http(**kwargs)
            resource = self.keys[key]
            r = self.resources[resource]
            credentials = r['credentials']
            path = urlsplit(kwargs['url']).path
            params = kwargs.get('params') or {}
            signed_path = path + ('?' + urlencode(params) if params else '')
            body = kwargs.get('data') or ''
            if type(body) is bytes:
                body = body.decode()
            message = headers['ok-access-timestamp'] + kwargs['method'] + signed_path + body
            expected = base64.b64encode(hmac.new(credentials.secret.encode(), message.encode(), hashlib.sha256).digest()).decode()
            if headers['ok-access-sign'] != expected or headers.get('ok-access-passphrase') != credentials.passphrase:
                raise StorageError('OFFLINE_PROVIDER_SIGNATURE_INVALID')
            write = kwargs['method'] != 'GET'
            if write and self.before_accept is not None:
                await self.before_accept(resource, kwargs)
            if r['rest']:
                raise StorageError('OFFLINE_PROVIDER_REST_REVOKED')
            token = None
            if write:
                self.sequence += 1
                token = 'accepted:' + str(self.sequence)
                self.pending[token] = resource
                self.accepted.setdefault(resource, []).append(token)
                self.observed.append(('REST', path, resource))
                if self.after_accept is not None:
                    await self.after_accept(resource, kwargs)
            try:
                return await http(**kwargs)
            finally:
                if token is not None:
                    self.pending.pop(token, None)
        return request
