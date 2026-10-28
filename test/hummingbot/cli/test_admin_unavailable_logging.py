"""Operational 503s need a safe cause and the response's correlation identity."""
import asyncio
import logging
from test.hummingbot.cli.test_admin_api import client

import pytest
from sqlalchemy.exc import SQLAlchemyError, TimeoutError as PoolTimeout

from hummingbot.cli.admin_api import ADMIN_PREFIX, APIUnavailable


@pytest.mark.asyncio
@pytest.mark.parametrize('error_type', [SQLAlchemyError, PoolTimeout, asyncio.TimeoutError, APIUnavailable])
async def test_unavailable_response_logs_safe_qualified_cause_and_request_id(caplog, error_type):
    secret = 'sensitive-parameters-must-not-be-logged'
    async with client() as (http, storage):
        storage.daily.side_effect = error_type(secret)
        with caplog.at_level(logging.WARNING, logger='hummingbot.cli.admin_api'):
            response = await http.get(ADMIN_PREFIX + '/statistics/daily')
        body = await response.json()
    assert response.status == 503 and body['code'] == 'unavailable'
    records = [r for r in caplog.records if getattr(r, 'event', None) == 'admin_request_unavailable']
    assert len(records) == 1
    record = records[0]
    assert record.levelno == logging.WARNING
    assert record.error_type == error_type.__module__ + '.' + error_type.__name__
    assert response.headers['X-Request-ID'] == body['request_id']
    assert body['request_id'] in record.getMessage()
    assert not record.exc_info and secret not in caplog.text and secret not in str(body)
