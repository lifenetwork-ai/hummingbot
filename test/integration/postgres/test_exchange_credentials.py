import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError

from hummingbot.core.credential_encryption import CredentialCipher, CredentialContext, LocalKeyProvider
from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres
from hummingbot.model.exchange_credential import ExchangeCredential


def test_encrypted_bundle_survives_repeat_migration_without_plaintext_in_rows(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    BotStateStore(pg_engine, "hummingbot").ensure()
    context = CredentialContext("hummingbot", "okx", "okx", "demo", 1, "demo-account")
    cipher = CredentialCipher(LocalKeyProvider(b"K" * 32, "local-v1"))
    secret = {"api_key": "CANARY-API-KEY", "secret_key": "CANARY-SECRET-KEY", "passphrase": "CANARY-PASSPHRASE"}
    envelope = cipher.encrypt(secret, context)
    with pg_engine.begin() as connection:
        connection.execute(ExchangeCredential.__table__.insert().values(
            bot_id="hummingbot", exchange="okx", connector="okx", environment="demo", version=1,
            account_id="demo-account", state="active", envelope=envelope.data,
            changed_by="test-operator", changed_at=func.clock_timestamp()))
    bootstrap_postgres(pg_engine, auto_migrate=True)
    with pg_engine.connect() as connection:
        row = connection.execute(select(ExchangeCredential)).mappings().one()
        serialized = connection.execute(text("SELECT row_to_json(exchange_credentials)::text FROM exchange_credentials")).scalar_one()
    assert all(value not in serialized for value in secret.values())
    restarted = CredentialCipher(LocalKeyProvider(b"K" * 32, "local-v1"))
    assert restarted.decrypt(row["envelope"], context) == secret

    with pg_engine.begin() as connection:
        connection.execute(update(ExchangeCredential).values(state="revoked", envelope=None))
    with pg_engine.connect() as connection:
        assert connection.execute(text("SELECT envelope IS NULL FROM exchange_credentials")).scalar_one()
        assert connection.execute(select(ExchangeCredential.account_id)).scalar_one() == "demo-account"


def test_credential_scope_is_unique_and_revoked_rows_cannot_retain_an_envelope(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    BotStateStore(pg_engine, "hummingbot").ensure()
    values = dict(bot_id="hummingbot", exchange="okx", connector="okx", environment="demo", version=1,
                  account_id="account", state="active", envelope={"opaque": "encrypted"}, changed_by="operator",
                  changed_at=func.clock_timestamp())
    with pg_engine.begin() as connection:
        connection.execute(ExchangeCredential.__table__.insert().values(**values))
        connection.execute(ExchangeCredential.__table__.insert().values(**{**values, "connector": "okx_perpetual"}))
    for invalid in (values, {**values, "connector": "other", "version": 0},
                    {**values, "connector": "other", "state": "revoked"},
                    {**values, "connector": "other", "envelope": None}):
        with pytest.raises(IntegrityError):
            with pg_engine.begin() as connection:
                connection.execute(ExchangeCredential.__table__.insert().values(**invalid))
    with pg_engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(ExchangeCredential)).scalar_one() == 2
