import json
from test.e2e.offline.security_fixture import create_identity_fixture

import pytest

from hummingbot.cli.admin_identity import GoogleTokenVerifier
from hummingbot.core.admin_identity import AccessDenied


def test_offline_load_identities_are_signed_verified_and_write_no_private_key(tmp_path):
    fixture = create_identity_fixture(tmp_path)
    certs = json.loads((tmp_path / 'google-certificates.json').read_text())
    verifier = GoogleTokenVerifier(fixture['client_id'], certificates=certs)
    assert set(fixture['tokens']) == {'admin', 'write', 'read'}
    for role, token in fixture['tokens'].items():
        claims = verifier.verify(token)
        assert claims['email'] == f'load-{role}@gmail.com'
        assert claims['sub'] == f'load-{role}'
    assert not any('PRIVATE KEY' in file.read_text() for file in tmp_path.iterdir())
    with pytest.raises(AccessDenied):
        GoogleTokenVerifier('different-audience', certificates=certs).verify(fixture['tokens']['admin'])


@pytest.mark.asyncio
async def test_mixed_fixture_tracks_denials_and_restores_writer_without_changing_mm_workload(tmp_path):
    import asyncio
    from test.e2e.offline.security_fixture import mixed_security_workload

    from aiohttp import ClientSession, web
    from aiohttp.test_utils import TestServer
    fixture = create_identity_fixture(tmp_path)
    roles = {'Bearer ' + token: role for role, token in fixture['tokens'].items()}
    writer = dict(id='writer', email='load-write@gmail.com', version=2, role='write')

    async def handle(request):
        role = roles.get(request.headers.get('Authorization'))
        if role is None:
            return web.json_response({}, status=401)
        effective = writer['role'] if role == 'write' else role
        if request.path.endswith('/access/accounts'):
            return web.json_response({'items': [writer]})
        if request.path.endswith('/access/accounts/writer'):
            data = await request.json()
            assert data['expected_version'] == writer['version']
            writer.update(role=data['role'], version=writer['version'] + 1)
            return web.json_response(writer)
        if request.method == 'POST' and effective == 'read':
            return web.json_response({}, status=403)
        return web.json_response({'role': effective})

    app = web.Application()
    app.router.add_route('*', '/{path:.*}', handle)
    summary = {}
    async with TestServer(app) as server, ClientSession() as client:
        await mixed_security_workload(client, tmp_path, str(server.make_url('/api/v1/admin')),
                                      asyncio.get_running_loop().time() + .05, summary)
    assert summary['role_cycles'] == 1 and summary['requests'] == 9 and summary['expected_denials'] == 3
    assert writer['role'] == 'write' and writer['version'] == 4
