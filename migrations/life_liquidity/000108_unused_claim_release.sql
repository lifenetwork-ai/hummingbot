-- Explicit capacity retirement of a SPOT claim whose original birth was
-- permanently retired before any preparation. No terminal, cash, position,
-- wallet reservation, budget refund or activation is synthesized.
DO $$ DECLARE item record;p record;BEGIN
    FOR item IN SELECT * FROM (VALUES
        ('life_financial.carry_current_ready(uuid)','b20a41e9e780a319258fe59bbd4c3d56722d8544c1969bdf4cf15c94dbdbf7c2'),
        ('life_financial.check_joint_source_deadline(uuid,text)','4cce48ca55da28c1216af5ccfeaaa5051712c11c2345c2ce82a3d14880d05eb1'),
        ('life_financial.commit_owner_guard()','60fae48449eb6c403eeb046199c9bb82fa9a114330f43401cfb0f8f5cd51db8b'),
        ('life_financial.control_facts_qualified(uuid,jsonb)','42e02b311e487d1fec6fa601ce677b7208190a4d3ae2c04144b3f5518e99258f'),
        ('life_financial.execution_facts_qualified(uuid,jsonb)','ffd9b1e7e86b33f97c788011258340b3c4ae4d7d8eec3e18aaf07ff1328b6feb'),
        ('life_financial.shared_claim_active_at(uuid,text,bigint)','338e22e1d1b55a68ecaf610d37210e273f66ef13ab80aa4af802485a77e93640'),
        ('life_financial.shared_release_verified(uuid)','2c332c151665a95e8d000c0e23143e92b47a9eb74a75f41431b312ee046aac49'),
        ('life_financial.spot_body(uuid)','9e93868d4c9d376f37e95a3b9891aba1e9811650b8b1ab9c22a741bcfec53b2c')
) AS v(signature,source_hash) LOOP
        SELECT * INTO STRICT p FROM pg_proc WHERE oid=item.signature::regprocedure;
        IF NOT p.prosecdef OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
            OR p.provolatile<>(CASE WHEN p.proname IN ('commit_owner_guard','check_joint_source_deadline') THEN 'v' ELSE 's' END)
            OR p.prorettype<>(CASE WHEN p.proname='commit_owner_guard' THEN 'trigger'::regtype WHEN p.proname='check_joint_source_deadline' THEN 'void'::regtype
                WHEN p.proname='spot_body' THEN 'jsonb'::regtype ELSE 'boolean'::regtype END)
            OR (SELECT lanname FROM pg_language WHERE oid=p.prolang)<>(CASE WHEN p.proname='shared_claim_active_at' THEN 'sql' ELSE 'plpgsql' END)
            OR p.proisstrict OR p.proleakproof OR p.proretset OR p.prokind<>'f' OR p.pronargdefaults<>0 OR p.proparallel<>'u'
            OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex') IS DISTINCT FROM item.source_hash THEN
            RAISE EXCEPTION 'LIFE_UNUSED_RELEASE_MIGRATION_MISMATCH';END IF;
    END LOOP;
END; $$;
-- One shared-capacity release lifecycle, with explicitly distinct proofs.
-- NULL means no terminal ever existed; the tagged basis and native verifier
-- require an original retired unused birth, never a fabricated terminal.
ALTER TABLE life_financial.shared_claim_releases ALTER COLUMN terminal_operation_id DROP NOT NULL;
ALTER TABLE life_financial.shared_claim_releases ADD CONSTRAINT shared_release_proof_variant CHECK(
    terminal_operation_id IS NOT NULL AND basis->>'kind' IS DISTINCT FROM 'UNUSED_BIRTH'
    OR terminal_operation_id IS NULL AND basis->>'kind' IS NOT DISTINCT FROM 'UNUSED_BIRTH');
CREATE TABLE life_financial.unused_claim_releases (
    account_id uuid NOT NULL REFERENCES life_financial.shared_release_policies,
    intent_id life_financial.identity NOT NULL,operation_id life_financial.identity NOT NULL,
    ticket_id life_financial.identity NOT NULL,retirement_id life_financial.identity NOT NULL,
    received_at_ms bigint NOT NULL CHECK(received_at_ms>0),
    PRIMARY KEY(account_id,intent_id),UNIQUE(account_id,operation_id),UNIQUE(account_id,ticket_id),
    FOREIGN KEY(account_id,intent_id) REFERENCES life_financial.shared_capital_claims,
    FOREIGN KEY(account_id,intent_id) REFERENCES life_financial.shared_claim_releases,
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.shared_claim_releases(account_id,operation_id),
    FOREIGN KEY(account_id,ticket_id) REFERENCES life_financial.quote_birth_retirements,
    FOREIGN KEY(account_id,retirement_id) REFERENCES life_financial.quote_birth_retirements(account_id,retirement_id),
    FOREIGN KEY(account_id,operation_id) REFERENCES life_financial.operations DEFERRABLE INITIALLY DEFERRED
);
-- Empty-history checks must not fetch/decompress unrelated command JSON.
-- Also covers the orphan-operation audit without caching a financial verdict.
CREATE INDEX unused_release_operation_history ON life_financial.operations(account_id,committed_version,operation_id)
    WHERE command->>'kind'='SHARED_UNUSED_RELEASE';
ALTER TABLE life_financial.unused_claim_releases ENABLE ROW LEVEL SECURITY;
CREATE POLICY scoped_read ON life_financial.unused_claim_releases FOR SELECT USING(life_financial.can_access(account_id));
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.unused_claim_releases
    FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record();
DO $$ DECLARE item record;d text;BEGIN
    FOR item IN SELECT * FROM (VALUES
        ('shared_release_verified','shared_release_before_unused_verified'),
        ('spot_body','spot_body_before_unused_release'),
        ('control_facts_qualified','unused_release_quiescent')) AS x(original,clone) LOOP
        d:=pg_get_functiondef(('life_financial.'||item.original||CASE WHEN item.original='control_facts_qualified' THEN '(uuid,jsonb)' ELSE '(uuid)' END)::regprocedure);
        d:=replace(d,'CREATE OR REPLACE FUNCTION life_financial.'||item.original||'(', 'CREATE FUNCTION life_financial.'||item.clone||'(');
        IF item.original='control_facts_qualified' THEN
            d:=replace(d,E'        OR EXISTS(SELECT 1 FROM life_financial.shared_capital_claims c WHERE account_id=p_account AND NOT EXISTS(\n            SELECT 1 FROM life_financial.shared_claim_releases r WHERE r.account_id=c.account_id AND r.intent_id=c.intent_id))','');
            IF position('shared_capital_claims' IN d)>0 THEN RAISE EXCEPTION 'LIFE_UNUSED_RELEASE_MIGRATION_MISMATCH';END IF;
        END IF;
        EXECUTE d;
    END LOOP;
END; $$;
CREATE FUNCTION life_financial.unused_release_origin(p_account uuid,cmd jsonb,token jsonb,p_version bigint) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b life_financial.quote_births;r life_financial.quote_birth_retirements;c life_financial.shared_capital_claims;o life_financial.operations;BEGIN
    SELECT * INTO STRICT b FROM life_financial.quote_births WHERE account_id=p_account AND ticket_id=cmd->>'ticket_id';
    SELECT * INTO STRICT r FROM life_financial.quote_birth_retirements WHERE account_id=p_account AND ticket_id=b.ticket_id AND retirement_id=cmd->>'retirement_id';
    SELECT * INTO STRICT c FROM life_financial.shared_capital_claims WHERE account_id=p_account AND intent_id=b.intent_id;
    SELECT * INTO STRICT o FROM life_financial.operations WHERE account_id=p_account AND operation_id=c.operation_id;
    RETURN COALESCE(b.intent_id=cmd->>'intent_id' AND c.product='SPOT'
        AND b.body->'owner'=token AND o.command->'owner'=token AND r.body#>'{request,owner}'=token
        AND o.committed_version<=(b.body->>'version')::bigint AND (b.body->>'version')::bigint<=p_version
        AND (r.body#>>'{request,version}')::bigint<=p_version
        AND NOT EXISTS(SELECT 1 FROM life_financial.quote_birth_consumptions WHERE account_id=p_account AND ticket_id=b.ticket_id)
        AND NOT EXISTS(SELECT 1 FROM life_financial.send_attempts WHERE account_id=p_account AND intent_id=c.intent_id)
        AND NOT EXISTS(SELECT 1 FROM life_financial.quote_actions WHERE account_id=p_account AND action_id=c.intent_id)
        AND NOT EXISTS(SELECT 1 FROM life_financial.reservations WHERE account_id=p_account AND intent_id=c.intent_id)
        AND NOT EXISTS(SELECT 1 FROM life_financial.shared_claim_releases WHERE account_id=p_account AND intent_id=c.intent_id AND terminal_operation_id IS NOT NULL),false);
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
-- Recheck original evidence at the historical operation's version/time, never
-- against a later cash balance or a renewed source timestamp.
CREATE FUNCTION life_financial.unused_release_evidence(p_account uuid,q jsonb,p_version bigint,at_ms bigint) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE f jsonb;s jsonb;m jsonb;ids jsonb;maximum integer;at timestamptz;observed timestamptz;age integer;k text;BEGIN
    f:=q#>'{command,facts}';s:=q#>'{command,source}';at:=to_timestamp(at_ms::numeric/1000);
    IF jsonb_typeof(f) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(f))<>13
        OR NOT f ?& ARRAY['binding','observed_at','account_base_life','account_quote_usdt','liability_base_life','liability_quote_usdt',
            'account_trade_ids','account_cashflow_ids','accepted_wire_ids','runner_wire_ids','order_scope_complete','bill_scope_complete','runner_scope_complete']
        OR f->'binding' IS DISTINCT FROM q->'binding'
        OR f->'order_scope_complete' IS DISTINCT FROM 'true'::jsonb OR f->'bill_scope_complete' IS DISTINCT FROM 'true'::jsonb
        OR f->'runner_scope_complete' IS DISTINCT FROM 'true'::jsonb OR f->'accepted_wire_ids' IS DISTINCT FROM '[]'::jsonb
        OR f->'runner_wire_ids' IS DISTINCT FROM '[]'::jsonb
        OR jsonb_typeof(s) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(s))<>6
        OR NOT s ?& ARRAY['operation_id','collection_id','source_hash','observed_at','expires_at','max_age_ms']
        OR s->>'operation_id' IS DISTINCT FROM q#>>'{command,operation_id}'
        OR s->>'source_hash' !~ '^[0-9a-f]{64}$' OR jsonb_typeof(s->'source_hash') IS DISTINCT FROM 'string'
        OR jsonb_typeof(s->'collection_id') IS DISTINCT FROM 'string'
        OR jsonb_typeof(s->'observed_at') IS DISTINCT FROM 'string' OR jsonb_typeof(s->'expires_at') IS DISTINCT FROM 'string'
        OR jsonb_typeof(s->'max_age_ms') IS DISTINCT FROM 'number' OR s->>'max_age_ms' !~ '^[1-9][0-9]{0,4}$'
        OR jsonb_typeof(f->'observed_at') IS DISTINCT FROM 'string' OR f->'observed_at' IS DISTINCT FROM s->'observed_at' THEN RETURN false;END IF;
    PERFORM (s->>'collection_id')::life_financial.identity;
    observed:=(s->>'observed_at')::timestamptz;age:=(s->>'max_age_ms')::integer;
    SELECT max_fact_age_ms INTO STRICT maximum FROM life_financial.spot_order_policies
        WHERE account_id=p_account AND policy_hash=q#>>'{binding,policy_hash}';
    IF NOT isfinite(observed) OR NOT isfinite((s->>'expires_at')::timestamptz) OR age NOT BETWEEN 1 AND least(maximum,60000)
        OR (s->>'expires_at')::timestamptz<>observed+make_interval(secs=>age/1000.0)
        OR at<observed OR at>=(s->>'expires_at')::timestamptz THEN RETURN false;END IF;
    FOREACH k IN ARRAY ARRAY['account_base_life','account_quote_usdt','liability_base_life','liability_quote_usdt'] LOOP
        IF jsonb_typeof(f->k) IS DISTINCT FROM 'string' THEN RETURN false;END IF;
        PERFORM (f->>k)::life_financial.amount;
    END LOOP;
    m:=life_financial.portfolio_money_at(p_account,p_version);
    IF (f->>'account_base_life')::numeric IS DISTINCT FROM (m->>'base')::numeric
        OR (f->>'account_quote_usdt')::numeric IS DISTINCT FROM (m->>'quote')::numeric
        OR (f->>'liability_base_life')::numeric<>0 OR (f->>'liability_quote_usdt')::numeric<>0 THEN RETURN false;END IF;
    SELECT COALESCE(jsonb_agg(DISTINCT id COLLATE "C" ORDER BY id COLLATE "C"),'[]'::jsonb) INTO ids FROM (
        SELECT f.trade_id::text AS id FROM life_financial.spot_fill_facts f JOIN life_financial.operations o ON o.account_id=f.account_id AND o.operation_id=f.source_operation_id
            WHERE f.account_id=p_account AND o.committed_version<=p_version
        UNION ALL SELECT f.trade_id::text FROM life_financial.swap_fill_facts f JOIN life_financial.operations o ON o.account_id=f.account_id AND o.operation_id=f.source_operation_id
            WHERE f.account_id=p_account AND o.committed_version<=p_version) x;
    IF f->'account_trade_ids' IS DISTINCT FROM ids THEN RETURN false;END IF;
    SELECT COALESCE(jsonb_agg(f.bill_id ORDER BY f.bill_id COLLATE "C"),'[]'::jsonb) INTO ids FROM life_financial.spot_cashflow_facts f
        JOIN life_financial.operations o ON o.account_id=f.account_id AND o.operation_id=f.source_operation_id
        WHERE f.account_id=p_account AND f.state='APPLIED' AND o.committed_version<=p_version;
    RETURN f->'account_cashflow_ids' IS NOT DISTINCT FROM ids;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
CREATE FUNCTION life_financial.unused_release_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE p life_financial.shared_release_policies;r life_financial.unused_claim_releases;o life_financial.operations;c life_financial.shared_claim_releases;cmd jsonb;BEGIN
    -- The unchanged normal-release verifier owns its original policy audit.
    -- Keep accounts without this new history on the existing bounded path.
    IF NOT EXISTS(SELECT 1 FROM life_financial.unused_claim_releases WHERE account_id=p_account)
        AND NOT EXISTS(SELECT 1 FROM life_financial.operations WHERE account_id=p_account AND command->>'kind'='SHARED_UNUSED_RELEASE') THEN RETURN true;END IF;
    -- One complete birth/retirement audit per invocation, not per release row.
    IF NOT life_financial.quote_birth_verified(p_account) THEN RETURN false;END IF;
    SELECT * INTO p FROM life_financial.shared_release_policies WHERE account_id=p_account;
    IF NOT FOUND THEN RETURN NOT EXISTS(SELECT 1 FROM life_financial.unused_claim_releases WHERE account_id=p_account)
        AND NOT EXISTS(SELECT 1 FROM life_financial.operations WHERE account_id=p_account AND command->>'kind'='SHARED_UNUSED_RELEASE');END IF;
    IF p.policy_hash IS DISTINCT FROM (SELECT policy_hash FROM life_financial.accounts WHERE account_id=p_account)
        OR (SELECT count(*) FROM life_financial.shared_claim_releases WHERE account_id=p_account)>p.max_releases
        OR (SELECT COALESCE(sum(octet_length(basis::text)),0) FROM life_financial.shared_claim_releases WHERE account_id=p_account)>p.max_total_bytes THEN RETURN false;END IF;
    FOR r IN SELECT * FROM life_financial.unused_claim_releases WHERE account_id=p_account LOOP
        SELECT * INTO STRICT o FROM life_financial.operations WHERE account_id=p_account AND operation_id=r.operation_id;
        SELECT * INTO STRICT c FROM life_financial.shared_claim_releases WHERE account_id=p_account AND intent_id=r.intent_id;
        cmd:=o.command->'command';
        IF c.operation_id IS DISTINCT FROM r.operation_id OR c.terminal_operation_id IS NOT NULL
            OR c.received_at_ms IS DISTINCT FROM r.received_at_ms
            OR c.basis IS DISTINCT FROM jsonb_build_object('kind','UNUSED_BIRTH','request',o.command)
            OR o.command->>'kind' IS DISTINCT FROM 'SHARED_UNUSED_RELEASE' OR (SELECT count(*) FROM jsonb_object_keys(o.command))<>6
            OR NOT o.command ?& ARRAY['kind','binding','owner','version','cut','command']
            OR jsonb_typeof(cmd) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(cmd))<>6
            OR NOT cmd ?& ARRAY['operation_id','intent_id','ticket_id','retirement_id','facts','source']
            OR cmd->>'operation_id' IS DISTINCT FROM r.operation_id OR cmd->>'intent_id' IS DISTINCT FROM r.intent_id
            OR cmd->>'ticket_id' IS DISTINCT FROM r.ticket_id OR cmd->>'retirement_id' IS DISTINCT FROM r.retirement_id
            OR jsonb_typeof(o.command->'version') IS DISTINCT FROM 'number' OR o.command->>'version' !~ '^(0|[1-9][0-9]*)$'
            OR (o.command->>'version')::bigint IS DISTINCT FROM o.committed_version-1
            OR jsonb_typeof(o.command->'cut') IS DISTINCT FROM 'string' OR o.command->>'cut' !~ '^[0-9a-f]{64}$'
            OR o.command->'binding' IS DISTINCT FROM (SELECT body->'binding' FROM life_financial.quote_births WHERE account_id=p_account AND ticket_id=r.ticket_id)
            OR o.fingerprint IS DISTINCT FROM o.canonical_fingerprint
            OR r.received_at_ms IS DISTINCT FROM floor(extract(epoch FROM o.recorded_at)*1000)::bigint
            OR r.received_at_ms<(SELECT (body->>'retired_at_ms')::bigint FROM life_financial.quote_birth_retirements
                WHERE account_id=p_account AND ticket_id=r.ticket_id)
            OR NOT life_financial.unused_release_origin(p_account,cmd,o.command->'owner',o.committed_version-1)
            OR NOT life_financial.unused_release_evidence(p_account,o.command,o.committed_version-1,r.received_at_ms)
            OR o.receipt IS DISTINCT FROM jsonb_build_object('kind','SHARED_UNUSED_RELEASE','operation_id',r.operation_id,'intent_id',r.intent_id,
                'ticket_id',r.ticket_id,'released',true,'version',o.committed_version,'replayed',false,'trading_permission',false) THEN RETURN false;END IF;
    END LOOP;
    RETURN NOT EXISTS(SELECT 1 FROM life_financial.operations o WHERE o.account_id=p_account AND o.command->>'kind'='SHARED_UNUSED_RELEASE'
        AND NOT EXISTS(SELECT 1 FROM life_financial.unused_claim_releases r WHERE r.account_id=o.account_id AND r.operation_id=o.operation_id));
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
CREATE OR REPLACE FUNCTION life_financial.shared_release_verified(p_account uuid)
 RETURNS boolean
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE p life_financial.shared_release_policies;r record;o life_financial.operations;expected jsonb;spot_ok boolean;swap_ok boolean;unused_ok boolean;
BEGIN
    SELECT * INTO p FROM life_financial.shared_release_policies WHERE account_id=p_account;
    IF NOT FOUND THEN RETURN NOT EXISTS(SELECT 1 FROM life_financial.shared_claim_releases WHERE account_id=p_account)
        AND NOT EXISTS(SELECT 1 FROM life_financial.operations WHERE account_id=p_account AND command->>'kind' IN ('SHARED_RELEASE','SHARED_UNUSED_RELEASE'));END IF;
    IF p.policy_hash IS DISTINCT FROM (SELECT policy_hash FROM life_financial.accounts WHERE account_id=p_account)
        OR (SELECT count(*) FROM life_financial.shared_claim_releases WHERE account_id=p_account)>p.max_releases
        OR (SELECT COALESCE(sum(octet_length(basis::text)),0) FROM life_financial.shared_claim_releases WHERE account_id=p_account)>p.max_total_bytes THEN RETURN false;END IF;
    FOR r IN SELECT releases.*,claims.product AS claim_product FROM life_financial.shared_claim_releases releases
        LEFT JOIN life_financial.shared_capital_claims claims USING(account_id,intent_id)
        WHERE releases.account_id=p_account LOOP
        IF r.terminal_operation_id IS NULL THEN
            SELECT * INTO STRICT o FROM life_financial.operations WHERE account_id=p_account AND operation_id=r.operation_id;
            IF o.command->>'kind' IS DISTINCT FROM 'SHARED_UNUSED_RELEASE' OR NOT EXISTS(
                SELECT 1 FROM life_financial.unused_claim_releases x WHERE x.account_id=p_account
                    AND x.intent_id=r.intent_id AND x.operation_id=r.operation_id) THEN RETURN false;END IF;
            -- This complete proof belongs to this STABLE invocation/current
            -- primary snapshot, never to a caller-supplied or persistent cache.
            IF unused_ok IS NULL THEN unused_ok:=life_financial.unused_release_verified(p_account);END IF;
            IF unused_ok IS NOT TRUE THEN RETURN false;END IF;
            CONTINUE;
        END IF;
        -- Local variables belong to this STABLE invocation and this primary
        -- MVCC cut only. Missing claims remain in the loop and fail STRICT
        -- origin checks in the original per-release proof.
        IF r.claim_product='SPOT' AND spot_ok IS NULL THEN
            spot_ok:=life_financial.spot_lifecycle_verified(p_account);
        ELSIF r.claim_product='SWAP' AND swap_ok IS NULL THEN
            swap_ok:=life_financial.swap_terminal_verified(p_account);
        END IF;
        SELECT * INTO STRICT o FROM life_financial.operations WHERE account_id=p_account AND operation_id=r.operation_id;
        expected:=jsonb_build_object('operation_id',r.operation_id,'intent_id',r.intent_id,'terminal_operation_id',r.terminal_operation_id);
        IF o.command->>'kind' IS DISTINCT FROM 'SHARED_RELEASE' OR o.command->'command' IS DISTINCT FROM expected
            OR o.fingerprint<>o.canonical_fingerprint
            OR (o.command->>'version')::bigint<>o.committed_version-1
            OR r.received_at_ms<>floor(extract(epoch FROM o.recorded_at)*1000)::bigint
            OR o.command->'owner' IS DISTINCT FROM (SELECT orig.command->'owner' FROM life_financial.shared_capital_claims c
                JOIN life_financial.operations orig USING(account_id,operation_id) WHERE c.account_id=p_account AND c.intent_id=r.intent_id)
            OR r.basis IS DISTINCT FROM life_financial.shared_release_after_terminal_proofs(p_account,r.intent_id,r.terminal_operation_id,o.committed_version-1,r.received_at_ms,spot_ok,swap_ok)
            OR o.receipt IS DISTINCT FROM jsonb_build_object('kind','SHARED_RELEASE','operation_id',r.operation_id,'intent_id',r.intent_id,
                'released',true,'version',o.committed_version,'replayed',false,'trading_permission',false) THEN RETURN false;END IF;
    END LOOP;
    RETURN NOT EXISTS(SELECT 1 FROM life_financial.operations op WHERE op.account_id=p_account AND op.command->>'kind' IN ('SHARED_RELEASE','SHARED_UNUSED_RELEASE')
        AND NOT EXISTS(SELECT 1 FROM life_financial.shared_claim_releases fact WHERE fact.account_id=op.account_id AND fact.operation_id=op.operation_id));
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $function$
;
CREATE FUNCTION life_financial.unused_release_command(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,cmd jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts;p life_financial.shared_release_policies;o life_financial.operations;q jsonb;v jsonb;result jsonb;at timestamptz;k text;BEGIN
    IF NOT life_financial.can_access(p_account,true) OR jsonb_typeof(cmd) IS DISTINCT FROM 'object'
        OR (SELECT count(*) FROM jsonb_object_keys(cmd))<>6 OR octet_length(cmd::text)>65536
        OR NOT cmd ?& ARRAY['operation_id','intent_id','ticket_id','retirement_id','facts','source'] THEN
        RAISE EXCEPTION 'LIFE_UNUSED_RELEASE_COMMAND_INVALID';END IF;
    FOREACH k IN ARRAY ARRAY['operation_id','intent_id','ticket_id','retirement_id'] LOOP
        IF jsonb_typeof(cmd->k) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'LIFE_UNUSED_RELEASE_COMMAND_INVALID';END IF;
        PERFORM (cmd->>k)::life_financial.identity;
    END LOOP;
    q:=jsonb_build_object('kind','SHARED_UNUSED_RELEASE','binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut,'command',cmd);
    IF p_fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(q),'UTF8')),'hex') THEN RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT';END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,a.version);
    IF NOT life_financial.shared_release_verified(p_account) THEN RAISE EXCEPTION 'LIFE_UNUSED_RELEASE_UNVERIFIED';END IF;
    SELECT * INTO o FROM life_financial.operations WHERE account_id=p_account AND operation_id=cmd->>'operation_id';
    IF FOUND THEN
        IF o.command IS DISTINCT FROM q OR o.fingerprint IS DISTINCT FROM p_fingerprint THEN RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT';END IF;
        RETURN o.receipt||jsonb_build_object('replayed',true);END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    v:=life_financial.spot_state(p_account);
    IF v->>'digest' IS DISTINCT FROM p_cut THEN RAISE EXCEPTION 'LIFE_SPOT_CUT_CONFLICT';END IF;
    IF NOT life_financial.unused_release_origin(p_account,cmd,p_owner,p_version)
        OR NOT life_financial.shared_claim_active_at(p_account,cmd->>'intent_id',p_version) THEN RAISE EXCEPTION 'LIFE_UNUSED_RELEASE_ORIGIN_REQUIRED';END IF;
    at:=clock_timestamp();
    IF NOT life_financial.unused_release_quiescent(p_account,cmd->'facts')
        OR NOT life_financial.unused_release_evidence(p_account,q,p_version,floor(extract(epoch FROM at)*1000)::bigint)
        OR current_setting('life.joint_source_deadline',true)::jsonb IS DISTINCT FROM cmd->'source' THEN
        RAISE EXCEPTION 'LIFE_UNUSED_RELEASE_SOURCE_UNQUALIFIED';END IF;
    SELECT * INTO STRICT p FROM life_financial.shared_release_policies WHERE account_id=p_account AND policy_hash=a.policy_hash;
    INSERT INTO life_financial.shared_claim_releases VALUES(p_account,cmd->>'intent_id',cmd->>'operation_id',NULL,
        jsonb_build_object('kind','UNUSED_BIRTH','request',q),floor(extract(epoch FROM at)*1000)::bigint);
    INSERT INTO life_financial.unused_claim_releases VALUES(p_account,cmd->>'intent_id',cmd->>'operation_id',cmd->>'ticket_id',cmd->>'retirement_id',floor(extract(epoch FROM at)*1000)::bigint);
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    result:=jsonb_build_object('kind','SHARED_UNUSED_RELEASE','operation_id',cmd->>'operation_id','intent_id',cmd->>'intent_id',
        'ticket_id',cmd->>'ticket_id','released',true,'version',p_version+1,'replayed',false,'trading_permission',false);
    INSERT INTO life_financial.operations(account_id,operation_id,fingerprint,command,receipt,committed_version,recorded_at)
        VALUES(p_account,cmd->>'operation_id',p_fingerprint,q,result,p_version+1,at);
    IF NOT life_financial.unused_release_verified(p_account) THEN RAISE EXCEPTION 'LIFE_UNUSED_RELEASE_BOUND_OR_UNVERIFIED';END IF;
    RETURN result;
END; $$;
CREATE FUNCTION life_financial.unused_release_read(p_account uuid,p_operation text,p_rows integer,p_bytes integer) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb;BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_SCOPE_INVALID';END IF;
    IF p_rows IS NULL OR p_rows NOT BETWEEN 1 AND 4096 OR p_bytes IS NULL OR p_bytes NOT BETWEEN 1 AND 16777216 THEN RAISE EXCEPTION 'LIFE_UNUSED_RELEASE_READ_BOUND';END IF;
    IF (SELECT count(*) FROM life_financial.unused_claim_releases WHERE account_id=p_account)>p_rows
        OR (SELECT COALESCE(sum(octet_length(o.command::text)),0) FROM life_financial.unused_claim_releases r JOIN life_financial.operations o USING(account_id,operation_id) WHERE r.account_id=p_account)>p_bytes THEN
        RAISE EXCEPTION 'LIFE_UNUSED_RELEASE_READ_BOUND';END IF;
    IF NOT life_financial.shared_release_verified(p_account) THEN RAISE EXCEPTION 'LIFE_UNUSED_RELEASE_UNVERIFIED';END IF;
    SELECT jsonb_build_object('command',o.command,'receipt',o.receipt) INTO result FROM life_financial.operations o
        JOIN life_financial.unused_claim_releases r USING(account_id,operation_id) WHERE o.account_id=p_account AND o.operation_id=p_operation;
    IF result IS NULL AND EXISTS(SELECT 1 FROM life_financial.operations WHERE account_id=p_account AND operation_id=p_operation) THEN RAISE EXCEPTION 'LIFE_OPERATION_CONFLICT';END IF;
    IF octet_length(result::text)>p_bytes THEN RAISE EXCEPTION 'LIFE_UNUSED_RELEASE_READ_BOUND';END IF;
    RETURN result;
END; $$;
-- A fixed read API supports already provisioned principals. It grants no
-- source/prepare authority and requires no SELECT on the new private tables.
CREATE FUNCTION life_financial.unused_release_origin_read(p_account uuid,p_ticket text) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_SCOPE_INVALID';END IF;
    IF NOT life_financial.quote_birth_verified(p_account) THEN RAISE EXCEPTION 'LIFE_BIRTH_UNVERIFIED';END IF;
    RETURN (SELECT jsonb_build_object('intent_id',b.intent_id,'retirement_id',r.retirement_id)
        FROM life_financial.quote_births b JOIN life_financial.quote_birth_retirements r USING(account_id,ticket_id)
        WHERE b.account_id=p_account AND b.ticket_id=p_ticket);
END; $$;
CREATE FUNCTION life_financial.unused_release_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE o life_financial.operations;BEGIN
    IF NOT life_financial.shared_release_verified(NEW.account_id) THEN RAISE EXCEPTION 'LIFE_UNUSED_RELEASE_UNVERIFIED';END IF;
    SELECT * INTO STRICT o FROM life_financial.operations WHERE account_id=NEW.account_id AND operation_id=NEW.operation_id;
    IF NOT life_financial.unused_release_quiescent(NEW.account_id,o.command#>'{command,facts}') THEN RAISE EXCEPTION 'LIFE_UNUSED_RELEASE_SOURCE_UNQUALIFIED';END IF;
    PERFORM life_financial.check_joint_source_deadline(NEW.account_id,NEW.operation_id);
    RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER unused_release_at_commit AFTER INSERT ON life_financial.unused_claim_releases
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION life_financial.unused_release_commit_guard();
CREATE OR REPLACE FUNCTION life_financial.commit_owner_guard()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
#variable_conflict use_column
DECLARE a life_financial.accounts; expiry timestamptz; at timestamptz; op life_financial.operations;
BEGIN
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=NEW.account_id;
    SELECT * INTO op FROM life_financial.operations WHERE account_id=NEW.account_id AND committed_version=NEW.version;
    IF op.command->>'kind'='NATIVE_CONTROL' THEN
        PERFORM life_financial.control_commit_check(NEW.account_id,op.operation_id);RETURN NULL;END IF;
    IF a.owner_generation IS DISTINCT FROM NEW.owner_generation OR a.owner_process IS DISTINCT FROM NEW.owner_process
        OR a.owner_epoch<>NEW.owner_epoch OR a.current_session IS DISTINCT FROM NEW.current_session
        OR a.lineage<>NEW.lineage OR a.policy_hash<>NEW.policy_hash OR a.config_hash<>NEW.config_hash THEN
        RAISE EXCEPTION 'LIFE_OWNER_EXPIRED_OR_CHANGED_AT_COMMIT' USING ERRCODE='P0001'; END IF;
    IF op.command->>'kind' IN ('SPOT_EXIT_SETTLE','EXECUTION_OBSERVE','STRESS_OBSERVE','RISK_OBSERVE','TERMINAL_REPAIR','HEDGE_PLAN','HEDGE_SETTLE','JOINT_REPAIR','JOINT_BATCH','SHARED_RELEASE','SHARED_UNUSED_RELEASE','SWAP_TERMINAL','PORTFOLIO_NAV','PAID_FUNDING','SWAP_FILL','SWAP_ACK','CARRY_CHECK','RECORDER_POSITION_STAGE','RECORDER_POSITION_APPLY','RECORDER_EVENT_STAGE','RECORDER_EVENT_APPLY','RECORDER_CONFIG_STAGE','RECORDER_CONFIG_APPLY','RECORDER_STAGE','RECORDER_APPLY','SPOT_MARKOUT','SPOT_INVENTORY_SETTLE','SPOT_ACK','SPOT_FILL','SPOT_CASHFLOW','SPOT_NAV','SPOT_SESSION_TICK','SPOT_LIFECYCLE','SPOT_TERMINAL') OR op.command->>'kind' IN ('SPOT_ACTION_TRANSITION','SPOT_ACTION_ABANDON')
        AND op.command#>>'{command,state}'<>'DISPATCHED'
        OR op.command->>'kind'='SHARED_CAPITAL' AND op.command#>>'{command,kind}'='OBSERVE' THEN
        PERFORM life_financial.guard_spot_observation(NEW.account_id,op.command->'binding',op.command->'owner',a.version);
        IF op.command->>'kind' IN ('SPOT_LIFECYCLE','SPOT_TERMINAL') THEN
            PERFORM life_financial.spot_order_commit_guard(NEW.account_id,op.command); END IF;
        IF op.command->>'kind'='SPOT_SESSION_TICK' AND ((op.command#>>'{command,reference_ready}')::boolean
            OR (op.command#>>'{command,all_gates_ready}')::boolean
            OR (op.command#>>'{command,market_reference_ready}')::boolean) THEN
            PERFORM life_financial.guard_account(NEW.account_id,op.command->'binding',op.command->'owner',a.version);
        END IF;
        IF op.command->>'kind'='SPOT_SESSION_TICK' AND op.receipt->>'state'='ACTIVE' THEN
            PERFORM life_financial.guard_spot_session(NEW.account_id); END IF;
    ELSE
        SELECT expires_at INTO STRICT expiry FROM life_financial.sessions WHERE account_id=a.account_id AND session_id=a.current_session;
        at:=clock_timestamp();
        IF a.mode<>'ACTIVE' OR a.halted OR at<a.owner_since OR at>=a.owner_until OR at>=expiry
            OR current_setting('synchronous_commit')<>'on' OR current_setting('fsync')<>'on'
            OR current_setting('full_page_writes')<>'on' OR pg_is_in_recovery() THEN
            RAISE EXCEPTION 'LIFE_OWNER_EXPIRED_OR_CHANGED_AT_COMMIT' USING ERRCODE='P0001'; END IF;
        IF op.receipt->>'kind' IN ('SPOT_PREPARE','SPOT_ARM') THEN PERFORM life_financial.guard_spot_session(NEW.account_id); END IF;
    END IF;
    IF op.command->>'kind'='SPOT_MARKOUT' THEN
        PERFORM life_financial.spot_markout_commit_guard(NEW.account_id,op.command); END IF;
    IF op.command->>'kind'='SPOT_INVENTORY_SETTLE' THEN
        PERFORM life_financial.spot_inventory_commit_guard(NEW.account_id,op.command); END IF;
    IF op.command->>'kind' IN ('SPOT_ACTION_CLAIM','SPOT_ACTION_TRANSITION','SPOT_ACTION_ABANDON') THEN
        PERFORM life_financial.spot_action_commit_guard(NEW.account_id,op.command); END IF;
    IF op.command->>'kind'='SPOT_PREPARE' AND op.command ? 'action' THEN
        PERFORM life_financial.spot_action_prepare_commit_guard(NEW.account_id,op.command); END IF;
    RETURN NULL;
END; $function$
;
CREATE OR REPLACE FUNCTION life_financial.check_joint_source_deadline(p_account uuid, p_operation text)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE j jsonb;o life_financial.operations;observed timestamptz;expires timestamptz;age integer;maximum integer;at timestamptz;
BEGIN
    IF NOT life_financial.can_access(p_account) THEN
        RAISE EXCEPTION 'LIFE_ACCOUNT_SCOPE_INVALID' USING ERRCODE='P0001';END IF;
    j:=current_setting('life.joint_source_deadline',true)::jsonb;
    IF j IS NULL OR jsonb_typeof(j)<>'object' OR (SELECT count(*) FROM jsonb_object_keys(j))<>6
        OR NOT j ?& ARRAY['operation_id','collection_id','source_hash','observed_at','expires_at','max_age_ms']
        OR j->>'operation_id' IS DISTINCT FROM p_operation
        OR jsonb_typeof(j->'max_age_ms')<>'number' OR j->>'max_age_ms' !~ '^[1-9][0-9]{0,4}$'
        OR EXISTS(SELECT 1 FROM jsonb_each(j) x WHERE x.key<>'max_age_ms' AND jsonb_typeof(x.value)<>'string')
        OR j->>'source_hash' !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'LIFE_JOINT_SOURCE_DEADLINE_INVALID' USING ERRCODE='P0001';END IF;
    PERFORM (j->>'collection_id')::life_financial.identity;
    observed:=(j->>'observed_at')::timestamptz;expires:=(j->>'expires_at')::timestamptz;age:=(j->>'max_age_ms')::integer;
    SELECT max_fact_age_ms INTO STRICT maximum FROM life_financial.spot_order_policies
        WHERE account_id=p_account AND policy_hash=(SELECT policy_hash FROM life_financial.accounts WHERE account_id=p_account);
    IF NOT isfinite(observed) OR NOT isfinite(expires) OR age NOT BETWEEN 1 AND least(maximum,60000)
        OR expires<>observed+make_interval(secs=>age/1000.0) THEN
        RAISE EXCEPTION 'LIFE_JOINT_SOURCE_DEADLINE_INVALID' USING ERRCODE='P0001';END IF;
    SELECT * INTO STRICT o FROM life_financial.operations WHERE account_id=p_account AND operation_id=p_operation;
    IF o.command->>'kind' IS NULL OR o.command->>'kind' NOT IN ('SPOT_TERMINAL','SWAP_TERMINAL','SHARED_RELEASE','SHARED_UNUSED_RELEASE') THEN
        RAISE EXCEPTION 'LIFE_JOINT_SOURCE_CONSUMER_INVALID' USING ERRCODE='P0001';END IF;
    at:=clock_timestamp();
    IF at<observed OR at>=expires THEN
        RAISE EXCEPTION 'LIFE_JOINT_SOURCE_EXPIRED_AT_COMMIT' USING ERRCODE='P0001';END IF;
END; $function$
;
-- No PUBLIC/hostile default execution, no runtime DML and no private verifier
-- grants. Preserve only the matching existing command/read API recipients.
DO $$ DECLARE r record;BEGIN
    FOR r IN SELECT DISTINCT x.grantee,p.oid::regprocedure::text AS signature FROM pg_proc p
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE p.pronamespace='life_financial'::regnamespace AND p.proname IN ('shared_release_before_unused_verified',
            'spot_body_before_unused_release','unused_release_quiescent','unused_release_origin','unused_release_evidence',
            'unused_release_verified','unused_release_command','unused_release_read','unused_release_origin_read','unused_release_commit_guard') AND x.grantee<>p.proowner LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %s',r.signature,CASE WHEN r.grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(r.grantee)) END);
    END LOOP;
    FOR r IN SELECT DISTINCT x.grantee FROM pg_class t CROSS JOIN LATERAL aclexplode(COALESCE(t.relacl,acldefault('r',t.relowner))) x
        WHERE t.oid='life_financial.unused_claim_releases'::regclass AND x.grantee<>t.relowner LOOP
        EXECUTE format('REVOKE ALL ON life_financial.unused_claim_releases FROM %s',CASE WHEN r.grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(r.grantee)) END);
    END LOOP;
    FOR r IN SELECT DISTINCT x.grantee,p.proname FROM pg_proc p CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE p.oid IN ('life_financial.shared_release_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'::regprocedure,
            'life_financial.shared_release_read(uuid,integer,integer,text)'::regprocedure)
            AND x.grantee<>p.proowner AND x.privilege_type='EXECUTE' AND x.grantee<>0 LOOP
        EXECUTE format('GRANT EXECUTE ON FUNCTION life_financial.%s TO %I',CASE WHEN r.proname='shared_release_command'
            THEN 'unused_release_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'
            ELSE 'unused_release_read(uuid,text,integer,integer)' END,pg_get_userbyid(r.grantee));
        IF r.proname='shared_release_read' THEN
            EXECUTE format('GRANT EXECUTE ON FUNCTION life_financial.unused_release_origin_read(uuid,text) TO %I',pg_get_userbyid(r.grantee));END IF;
    END LOOP;
END; $$;
