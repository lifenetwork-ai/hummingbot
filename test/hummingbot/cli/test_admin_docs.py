import json

import pytest
from aiohttp import BasicAuth, web
from aiohttp.test_utils import TestClient, TestServer

from hummingbot.cli import swagger
from hummingbot.cli.admin_api import ADMIN_PREFIX, AdminAPI, admin_middleware
from hummingbot.client.config.admin_config import AdminSettings


@pytest.mark.asyncio
@pytest.mark.parametrize("environment,visible", [(None, True), ("DEV", True), ("dev", True), ("development", True),
                                                 ("production", False), ("prod", False), ("staging", False), ("", False)])
async def test_documentation_is_development_only_and_defaults_to_dev(environment, visible):
    values = {"ADMIN_API_ENABLED": "true", "ADMIN_API_USERNAME": "operator", "ADMIN_API_PASSWORD": "test-password"}
    if environment is not None:
        values["ENV"] = environment
    settings = AdminSettings.from_environment(values)
    app = web.Application(middlewares=[admin_middleware(settings)])
    AdminAPI(None, settings).register(app)
    async with TestClient(TestServer(app)) as client:
        for path in ("/docs", "/openapi.json", "/docs/assets/swagger-ui.css"):
            assert (await client.get(ADMIN_PREFIX + path)).status == (401 if visible else 404)
            response = await client.get(ADMIN_PREFIX + path, headers={"Authorization": BasicAuth("operator", "test-password").encode()})
            assert response.status == (200 if visible else 404)
        # Production admin routes still enforce normal Basic Auth.
        assert (await client.get(ADMIN_PREFIX + "/status")).status == 401


@pytest.mark.asyncio
async def test_packaged_schema_and_swagger_ui_work_without_database_or_external_assets(tmp_path, monkeypatch):
    artifact = tmp_path / "openapi.json"
    artifact.write_text(swagger.rendered_spec())
    monkeypatch.setattr(swagger, "SPEC_PATH", artifact)
    settings = AdminSettings(True, "operator", "test-password")
    app = web.Application(middlewares=[admin_middleware(settings)])
    AdminAPI(None, settings).register(app)
    async with TestClient(TestServer(app)) as client:
        for path in ("/docs", "/openapi.json", "/docs/assets/swagger-ui-bundle.js"):
            assert (await client.get(ADMIN_PREFIX + path)).status == 401
        auth = {"Authorization": BasicAuth("operator", "test-password").encode()}
        schema = await (await client.get(ADMIN_PREFIX + "/openapi.json", headers=auth)).json()
        assert schema == json.loads(artifact.read_text())
        page = await (await client.get(ADMIN_PREFIX + "/docs", headers=auth)).text()
        assert "SwaggerUIBundle" in page
        assert "persistAuthorization: false" in page and "validatorUrl: null" in page
        assert 'src="https://' not in page and 'href="https://' not in page
        asset = await client.get(ADMIN_PREFIX + "/docs/assets/swagger-ui-bundle.js", headers=auth)
        assert asset.status == 200 and "SwaggerUIBundle" in await asset.text()
        assert (await client.get(ADMIN_PREFIX + "/docs/assets/unknown", headers=auth)).status == 404
