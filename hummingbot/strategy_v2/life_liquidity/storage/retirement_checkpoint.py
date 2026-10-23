"""Verify a cold, externally pinned File retirement checkpoint without path I/O.

Reconstruct the exact two control commits from the frozen source. No monetary,
identity, recorder, ownership or journal mutation may hide in those commits.
This is a permanent source-consumption proof, not target financial parity/rearm.
"""
import copy
import hashlib
from dataclasses import dataclass, field
from datetime import datetime

from hummingbot.strategy_v2.life_liquidity.checkpoint_archive import decode
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    ResourceVersion,
    StateCut,
    StorageError,
    digest,
    encode,
    fingerprint,
    utc,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
from hummingbot.strategy_v2.life_liquidity.storage.handoff import (
    AuthorizeConfigurationHandoff,
    ConsumeConfigurationHandoff,
    empty_handoff_head,
    handoff_projection,
    session_record,
)
from hummingbot.strategy_v2.life_liquidity.storage.handoff_artifact import artifact_name
from hummingbot.strategy_v2.life_liquidity.storage.service_config import FileServiceConfig
from hummingbot.strategy_v2.life_liquidity.storage.stopped_retirement import StoppedRetirementProof


@dataclass(frozen=True)
class VerifiedFileRetirement:
    body: bytes
    trading_permission: bool = field(default=False, init=False)

    def json(self):
        return decode(self.body)


def verify_retirement_checkpoint(source, raw, *, expected_sha256, target_binding, target_account_id,
                                 database_lineage, target_manifest_digest, max_bytes):
    digest(expected_sha256)
    if type(max_bytes) is not int or not 0 < max_bytes <= 64 * 2**20 or type(raw) is not bytes or len(raw) > max_bytes:
        raise StorageError('STOPPED_RETIREMENT_CHECKPOINT_CAPACITY')
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise StorageError('STOPPED_RETIREMENT_CHECKPOINT_DIGEST')
    try:
        return _verify(source, raw, expected_sha256, target_binding, target_account_id, database_lineage, target_manifest_digest)
    except StorageError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError, RecursionError):
        raise StorageError('STOPPED_RETIREMENT_CHECKPOINT_UNVERIFIED') from None


def _verify(source, raw, sha, target_binding, account_id, database_lineage, manifest):
    original, retired = decode(source.base.core.checkpoint_bytes), decode(raw)
    if (set(retired) != set(original) or {k: v for k, v in retired.items() if k != 'state'}
            != {k: v for k, v in original.items() if k != 'state'}):
        raise StorageError('STOPPED_RETIREMENT_CHECKPOINT_POLICY_CHANGED')
    config = FileServiceConfig.model_validate_json(source.base.service_json)
    verifier = config.control.verifier()
    before, final = original['state'], retired['state']
    old_records, records = before['resources']['control_operations'], final['resources']['control_operations']
    head = final['resources']['configuration_handoff']
    grant_id, consume_id = head['active_operation_id'], head['consumed_operation_id']
    if (grant_id == consume_id or set(records) != set(old_records) | {grant_id, consume_id}
            or grant_id in old_records or consume_id in old_records
            or any(records[k] != v for k, v in old_records.items())):
        raise StorageError('STOPPED_RETIREMENT_CHECKPOINT_HISTORY_CHANGED')
    grant = AuthorizeConfigurationHandoff.from_json(records[grant_id]['payload'])
    command = ConsumeConfigurationHandoff.from_json(records[consume_id]['payload'])
    proof = StoppedRetirementProof.from_json(command.postgres_retirement_json)
    body = proof.verify(command, grant, source.base.core.cut.checkpoint.binding, operator_keys=verifier.keys.values(),
                        facts=source.base.core.facts, source=source)
    if (grant.target_binding != target_binding or command.target_manifest_digest != manifest
            or body['target_account_id'] != str(account_id) or body['database_lineage'] != str(database_lineage)):
        raise StorageError('STOPPED_RETIREMENT_CHECKPOINT_TARGET_CHANGED')
    state = copy.deepcopy(before)
    for value, checked in ((grant, body['frozen_checked_at']), (command, final['checked_at'])):
        record = records[value.operation_id]
        verifier.audit(record, source.base.core.cut.checkpoint.binding)
        cut = FinancialCut.from_json(record['cut'])
        current = StateCut(cut.checkpoint.binding, state['revision'], state['event_version'],
                           tuple(ResourceVersion(k, v) for k, v in sorted(state['versions'].items())), fingerprint(state))
        if (cut.checkpoint != current or dict(cut.components) != dict(source.base.core.cut.components)
                or record['facts'] != source.base.core.facts.json() or record['approval']['role'] != 'HANDOFF'
                or record['state'] != 'APPLIED' or record['revision'] != state['revision'] + 1
                or set(record) != {'payload', 'approval', 'signature', 'cut', 'facts', 'state', 'revision'}):
            raise StorageError('STOPPED_RETIREMENT_CHECKPOINT_CHAIN_CHANGED')
        now = utc(datetime.fromisoformat(checked))
        if (not datetime.fromisoformat(record['approval']['issued_at']) <= now < datetime.fromisoformat(record['approval']['expires_at'])
                or not 0 <= (now - source.base.core.facts.observed_at).total_seconds() * 1000 <= config.recovery_policy.max_fact_age_ms
                or state['checked_at'] is not None and now < datetime.fromisoformat(state['checked_at'])
                or now >= session_record(grant.target_session_json).expires_at):
            raise StorageError('STOPPED_RETIREMENT_CHECKPOINT_TIME_UNQUALIFIED')
        resources = state['resources']
        next_head = handoff_projection(value.json(), resources.get('configuration_handoff', empty_handoff_head()),
                                       state['ownership'], cut.checkpoint.binding, resources['financial_configuration'],
                                       resources['control_operations'])
        changes = {'configuration_handoff': next_head}
        if value is grant:
            if cut != source.base.core.cut:
                raise StorageError('STOPPED_RETIREMENT_CHECKPOINT_SOURCE_CHANGED')
            changes['handoff_artifacts'] = {**resources.get('handoff_artifacts', {}), grant_id: {
                'name': artifact_name(grant, record['approval']), 'sha256': body['artifact_sha256']}}
        else:
            proof.verify(command, grant, cut.checkpoint.binding, operator_keys=verifier.keys.values(),
                         facts=source.base.core.facts, now=now, cut=cut)
        resources['control_operations'][value.operation_id] = record
        state['versions']['control_operations'] = state['versions'].get('control_operations', 0) + 1
        for key, item in changes.items():
            resources[key] = item
            state['versions'][key] = state['versions'].get(key, 0) + 1
        state['revision'] = state['event_version'] = record['revision']
        state['checked_at'] = checked
        state['operations']['control:' + value.operation_id] = dict(
            fingerprint=fingerprint({'approval': record['approval'], 'payload': record['payload']}), revision=record['revision'])
    if state != final:
        raise StorageError('STOPPED_RETIREMENT_CHECKPOINT_STATE_CHANGED')
    result = dict(format='LIFE_VERIFIED_FILE_RETIREMENT_V1', checkpoint_sha256=sha,
                  source_sha256=body['artifact_sha256'], source_digest=source.content_digest,
                  target_manifest_digest=manifest, target_account_id=str(account_id),
                  database_lineage=str(database_lineage), target_binding=target_binding.json(),
                  grant=records[grant_id], consumption=records[consume_id])
    return VerifiedFileRetirement(encode(result))
