-- Extend the reporting outbox with immutable, non-activating configuration audits.
CREATE FUNCTION life_financial.recorder_executor_only_integrity(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE delivery life_financial.recorder_outbox; prior jsonb; p jsonb; expected bigint:=0; previous_stream text; actual jsonb; retained_op life_financial.operations;
BEGIN
    FOR delivery IN SELECT * FROM life_financial.recorder_outbox WHERE account_id=p_account AND stream LIKE 'executor:%' ORDER BY stream,sequence LOOP
        IF delivery.body->>'subtype' NOT IN ('INITIAL','PROGRESS') THEN RETURN false; END IF;
        p:=(delivery.body#>>'{command,payload_json}')::jsonb;
        PERFORM life_financial.recorder_executor_origin(p_account,delivery.body->'binding',p);
        SELECT * INTO retained_op FROM life_financial.operations WHERE account_id=p_account AND operation_id=delivery.projection_id;
        IF NOT FOUND OR retained_op.command IS DISTINCT FROM jsonb_build_object('kind','RECORDER_STAGE',
                'subtype',delivery.body->'subtype','command',delivery.body->'command','binding',delivery.body->'binding',
                'owner',delivery.body->'owner','cut',delivery.body->'cut','version',delivery.body->'version')
            OR delivery.projection_id IS DISTINCT FROM delivery.body#>>'{command,operation_id}'
            OR delivery.body#>'{command,binding}' IS DISTINCT FROM delivery.body->'binding'
            OR retained_op.committed_version<>(delivery.body->>'version')::bigint+1
            OR delivery.source_hash IS DISTINCT FROM encode(sha256(convert_to(delivery.body::text,'UTF8')),'hex')
            OR delivery.stream IS DISTINCT FROM 'executor:'||encode(sha256(convert_to(p->>'id','UTF8')),'hex')
            OR retained_op.receipt IS DISTINCT FROM jsonb_build_object('kind','RECORDER_STAGE','projection_id',delivery.projection_id,
                'executor_id',p->>'id','sequence',delivery.sequence-1,'state','STAGED','version',retained_op.committed_version,
                'replayed',false,'trading_permission',false) THEN RETURN false; END IF;
        expected:=CASE WHEN previous_stream IS DISTINCT FROM delivery.stream THEN 1 ELSE expected+1 END;
        previous_stream:=delivery.stream;
        IF expected<>delivery.sequence THEN RETURN false; END IF;
        IF delivery.sequence=1 THEN
            IF delivery.body->>'subtype'<>'INITIAL' OR p->>'status'<>'1' OR (p->>'filled_amount_quote')::numeric<>0
                OR p#>'{custom_info,recovery_order_ids}'<>'[]'::jsonb THEN RETURN false; END IF;
        ELSE
            SELECT (x.body#>>'{command,payload_json}')::jsonb INTO STRICT prior FROM life_financial.recorder_outbox x
                WHERE x.account_id=p_account AND x.stream=delivery.stream AND x.sequence=delivery.sequence-1;
            IF delivery.body->>'subtype'<>'PROGRESS' OR (delivery.body#>>'{command,expected_sequence}')::bigint<>delivery.sequence-2 THEN RETURN false; END IF;
            PERFORM life_financial.recorder_executor_transition(prior,p);
        END IF;
    END LOOP;
    IF EXISTS(SELECT 1 FROM life_financial.operations op WHERE op.account_id=p_account AND op.command->>'kind'='RECORDER_STAGE'
        AND NOT EXISTS(SELECT 1 FROM life_financial.recorder_outbox o WHERE o.account_id=p_account AND o.projection_id=op.operation_id))
        OR EXISTS(SELECT 1 FROM life_financial.projection_receipts r JOIN life_financial.recorder_outbox o USING(account_id,projection_id)
            LEFT JOIN life_financial.operations op ON op.account_id=r.account_id AND op.operation_id='reportapply:'||encode(sha256(convert_to(r.projection_id,'UTF8')),'hex')
            WHERE r.account_id=p_account AND o.stream LIKE 'executor:%' AND (r.applied_hash<>o.source_hash OR op.command->>'kind' IS DISTINCT FROM 'RECORDER_APPLY'
                OR op.command->>'projection_id' IS DISTINCT FROM r.projection_id
                OR op.command->'binding' IS DISTINCT FROM o.body->'binding' OR op.command->'owner' IS DISTINCT FROM o.body->'owner'
                OR op.receipt IS DISTINCT FROM jsonb_build_object('kind','RECORDER_APPLY','projection_id',r.projection_id,
                    'executor_id',(o.body#>>'{command,payload_json}')::jsonb->>'id','sequence',o.sequence-1,
                    'state','APPLIED','version',op.committed_version,'replayed',false,'trading_permission',false)
                ))
        -- With contiguous stream sequences, applied count=max(applied sequence)
        -- proves prefix completeness once per stream, not once per old receipt.
        OR EXISTS(SELECT 1 FROM life_financial.recorder_outbox x
            LEFT JOIN life_financial.projection_receipts r USING(account_id,projection_id)
            WHERE x.account_id=p_account AND x.stream LIKE 'executor:%' GROUP BY x.stream
            HAVING COALESCE(max(x.sequence) FILTER(WHERE r.projection_id IS NOT NULL),0)
                <>count(*) FILTER(WHERE r.projection_id IS NOT NULL))
        OR EXISTS(SELECT 1 FROM life_financial.operations op WHERE op.account_id=p_account AND op.command->>'kind'='RECORDER_APPLY'
            AND NOT EXISTS(SELECT 1 FROM life_financial.projection_receipts r WHERE r.account_id=p_account AND r.projection_id=op.command->>'projection_id')) THEN RETURN false; END IF;
    FOR delivery IN SELECT DISTINCT ON(x.stream) x.* FROM life_financial.recorder_outbox x
        JOIN life_financial.projection_receipts r USING(account_id,projection_id)
        WHERE x.account_id=p_account AND x.stream LIKE 'executor:%' ORDER BY x.stream,x.sequence DESC LOOP
        p:=(delivery.body#>>'{command,payload_json}')::jsonb;
        SELECT to_jsonb(e) INTO actual FROM life_recorder."Executors" e WHERE e.id=p->>'id';
        IF actual IS DISTINCT FROM life_financial.recorder_executor_row(p) THEN RETURN false; END IF;
    END LOOP;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.recorder_config_id(p_account uuid,p_controller text) RETURNS text
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
    SELECT 'life-pg-config:'||encode(sha256(convert_to(p_account::text||':'||p_controller,'UTF8')),'hex');
$$;

CREATE FUNCTION life_financial.recorder_config_validate(p_account uuid,p_binding jsonb,p jsonb,prior jsonb) RETURNS void
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE c jsonb; imported jsonb; previous jsonb; k text;
BEGIN
    IF jsonb_typeof(p) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(p))<>11
        OR NOT p ?& ARRAY['operation_id','binding','controller_id','expected_sequence','active_revision','controller_json',
            'policy_hash','candidate_digest','outcome','reason_code','timestamp_ms']
        OR p->'binding' IS DISTINCT FROM p_binding
        OR p->>'policy_hash' IS DISTINCT FROM p_binding->>'policy_hash'
        OR jsonb_typeof(p->'controller_json') IS DISTINCT FROM 'string'
        OR octet_length(p->>'controller_json') NOT BETWEEN 1 AND 65536
        OR jsonb_typeof(p->'operation_id') IS DISTINCT FROM 'string' OR length(p->>'operation_id')>96
        OR jsonb_typeof(p->'controller_id') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p->'candidate_digest') IS DISTINCT FROM 'string'
        OR p->>'candidate_digest' !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'LIFE_CONFIG_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    PERFORM (p->>'operation_id')::life_financial.identity;
    PERFORM (p->>'controller_id')::life_financial.identity;
    FOREACH k IN ARRAY ARRAY['expected_sequence','active_revision','timestamp_ms'] LOOP
        IF jsonb_typeof(p->k) IS DISTINCT FROM 'number' OR p->>k !~ '^(0|[1-9][0-9]*)$' THEN
            RAISE EXCEPTION 'LIFE_CONFIG_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
        PERFORM (p->>k)::bigint;
    END LOOP;
    IF (p->>'active_revision')::bigint<1 OR (p->>'timestamp_ms')::bigint>=9007199254740992 THEN
        RAISE EXCEPTION 'LIFE_CONFIG_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    IF NOT (p->>'outcome'='INITIAL' AND p->>'reason_code'='CONFIG_STARTUP'
        OR p->>'outcome'='APPLIED' AND p->>'reason_code'='CONFIG_UPDATE_APPLIED'
        OR p->>'outcome'='UNCHANGED' AND p->>'reason_code'='CONFIG_UNCHANGED'
        OR p->>'outcome'='REJECTED' AND p->>'reason_code' IN ('CONFIG_UPDATE_UNSUPPORTED','CONFIG_VALIDATION_FAILED','CONFIG_LOAD_FAILED'))
        OR jsonb_typeof(p->'outcome') IS DISTINCT FROM 'string' OR jsonb_typeof(p->'reason_code') IS DISTINCT FROM 'string' THEN
        RAISE EXCEPTION 'LIFE_CONFIG_OUTCOME_INVALID' USING ERRCODE='P0001'; END IF;
    c:=(p->>'controller_json')::jsonb;
    SELECT body->'controller' INTO imported FROM life_financial.configurations
        WHERE account_id=p_account AND config_hash=p_binding->>'config_hash' AND policy_hash=p_binding->>'policy_hash';
    IF imported IS NULL OR jsonb_typeof(imported) IS DISTINCT FROM 'object'
        OR c->>'id' IS DISTINCT FROM p->>'controller_id' OR c->'storage_binding' IS DISTINCT FROM p_binding
        OR c->>'storage_backend' IS DISTINCT FROM 'postgres' OR jsonb_typeof(c->'manual_kill_switch') IS DISTINCT FROM 'boolean'
        OR c-'manual_kill_switch' IS DISTINCT FROM imported-'manual_kill_switch' THEN
        RAISE EXCEPTION 'LIFE_CONFIG_REGISTERED_BODY_REQUIRED' USING ERRCODE='P0001'; END IF;
    IF prior IS NULL THEN
        IF p->>'outcome'<>'INITIAL' OR (p->>'expected_sequence')::bigint<>0 OR (p->>'active_revision')::bigint<>1
            OR imported->'manual_kill_switch'='true'::jsonb AND c->'manual_kill_switch'<>'true'::jsonb THEN
            RAISE EXCEPTION 'LIFE_CONFIG_INITIAL_INVALID' USING ERRCODE='P0001'; END IF;
    ELSE
        previous:=(prior->>'controller_json')::jsonb;
        IF p->>'outcome'='INITIAL' OR (p->>'expected_sequence')::bigint<>(prior->>'expected_sequence')::bigint+1
            OR (p->>'active_revision')::bigint<>(prior->>'active_revision')::bigint+(CASE WHEN p->>'outcome'='APPLIED' THEN 1 ELSE 0 END)
            OR (c<>previous) IS DISTINCT FROM (p->>'outcome'='APPLIED')
            OR (p->>'timestamp_ms')::bigint<(prior->>'timestamp_ms')::bigint THEN
            RAISE EXCEPTION 'LIFE_CONFIG_TRANSITION_INVALID' USING ERRCODE='P0001'; END IF;
    END IF;
END; $$;

CREATE FUNCTION life_financial.recorder_config_integrity(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE item life_financial.recorder_outbox; retained life_financial.operations; command jsonb; prior jsonb;
    stream_id text; seq bigint:=0; expected jsonb; sql_id text; row_count bigint; latched boolean:=false;
BEGIN
    FOR item IN SELECT * FROM life_financial.recorder_outbox WHERE account_id=p_account AND stream LIKE 'configuration:%'
        ORDER BY stream,sequence LOOP
        command:=item.body->'command';
        IF stream_id IS DISTINCT FROM item.stream THEN seq:=0; prior:=NULL; END IF;
        stream_id:=item.stream; seq:=seq+1;
        PERFORM life_financial.recorder_config_validate(p_account,item.body->'binding',command,prior);
        IF item.sequence<>seq OR item.body->>'subtype' IS DISTINCT FROM 'CONFIGURATION'
            OR item.stream IS DISTINCT FROM 'configuration:'||encode(sha256(convert_to(command->>'controller_id','UTF8')),'hex')
            OR item.projection_id IS DISTINCT FROM command->>'operation_id' OR (command->>'expected_sequence')::bigint<>seq-1
            OR item.source_hash IS DISTINCT FROM encode(sha256(convert_to(item.body::text,'UTF8')),'hex') THEN RETURN false; END IF;
        SELECT * INTO retained FROM life_financial.operations WHERE account_id=p_account AND operation_id=item.projection_id;
        IF NOT FOUND OR retained.command IS DISTINCT FROM item.body||'{"kind":"RECORDER_CONFIG_STAGE"}'::jsonb
            OR retained.committed_version<>(item.body->>'version')::bigint+1
            OR retained.receipt IS DISTINCT FROM jsonb_build_object('kind','RECORDER_CONFIG_STAGE','projection_id',item.projection_id,
                'sequence',seq,'active_revision',command->'active_revision','reason_code',command->'reason_code','state','STAGED',
                'version',retained.committed_version,'replayed',false,'trading_permission',false) THEN RETURN false; END IF;
        IF EXISTS(SELECT 1 FROM life_financial.projection_receipts WHERE account_id=p_account AND projection_id=item.projection_id) THEN
            SELECT * INTO retained FROM life_financial.operations WHERE account_id=p_account
                AND operation_id='configapply:'||encode(sha256(convert_to(item.projection_id,'UTF8')),'hex');
            IF NOT FOUND OR retained.command->>'kind' IS DISTINCT FROM 'RECORDER_CONFIG_APPLY'
                OR retained.command->>'projection_id' IS DISTINCT FROM item.projection_id
                OR retained.command->'owner' IS DISTINCT FROM item.body->'owner'
                OR retained.command->'binding' IS DISTINCT FROM item.body->'binding'
                OR retained.receipt IS DISTINCT FROM jsonb_build_object('kind','RECORDER_CONFIG_APPLY','projection_id',item.projection_id,
                    'sequence',seq,'active_revision',command->'active_revision','reason_code',command->'reason_code','state','APPLIED',
                    'version',retained.committed_version,'replayed',false,'trading_permission',false)
                OR NOT EXISTS(SELECT 1 FROM life_financial.projection_receipts WHERE account_id=p_account AND projection_id=item.projection_id AND applied_hash=item.source_hash) THEN RETURN false; END IF;
            sql_id:=life_financial.recorder_config_id(p_account,command->>'controller_id');
            expected:=jsonb_build_object('id',sql_id,'timestamp',(command->>'timestamp_ms')::float8/1000,'type','generic','config',command);
            SELECT count(*) INTO row_count FROM life_recorder."Controllers" r
                WHERE r.id=sql_id AND to_jsonb(r)-'controller_id'=expected;
            IF row_count<>1 THEN RETURN false; END IF;
        END IF;
        latched:=latched OR command->>'outcome'='REJECTED' OR ((command->>'controller_json')::jsonb->>'manual_kill_switch')::boolean;
        prior:=command;
    END LOOP;
    IF latched AND NOT (SELECT halted FROM life_financial.accounts WHERE account_id=p_account) THEN RETURN false; END IF;
    IF EXISTS(SELECT 1 FROM life_financial.operations op WHERE op.account_id=p_account AND op.command->>'kind'='RECORDER_CONFIG_STAGE'
        AND NOT EXISTS(SELECT 1 FROM life_financial.recorder_outbox o WHERE o.account_id=p_account AND o.projection_id=op.operation_id AND o.stream LIKE 'configuration:%'))
        OR EXISTS(SELECT 1 FROM life_financial.operations op WHERE op.account_id=p_account AND op.command->>'kind'='RECORDER_CONFIG_APPLY'
            AND NOT EXISTS(SELECT 1 FROM life_financial.projection_receipts r WHERE r.account_id=p_account AND r.projection_id=op.command->>'projection_id'))
        OR EXISTS(SELECT 1 FROM life_financial.recorder_outbox o LEFT JOIN life_financial.projection_receipts r USING(account_id,projection_id)
            WHERE o.account_id=p_account AND o.stream LIKE 'configuration:%' GROUP BY o.stream
            HAVING COALESCE(max(o.sequence) FILTER(WHERE r.projection_id IS NOT NULL),0)<>count(*) FILTER(WHERE r.projection_id IS NOT NULL))
        OR EXISTS(SELECT 1 FROM life_recorder."Controllers" r WHERE EXISTS(
            SELECT 1 FROM life_financial.recorder_outbox o WHERE o.account_id=p_account AND o.stream LIKE 'configuration:%'
                AND r.id=life_financial.recorder_config_id(p_account,o.body#>>'{command,controller_id}'))
            AND NOT EXISTS(SELECT 1 FROM life_financial.recorder_outbox o JOIN life_financial.projection_receipts d USING(account_id,projection_id)
                WHERE o.account_id=p_account AND o.stream LIKE 'configuration:%' AND to_jsonb(r)-'controller_id'=jsonb_build_object(
                    'id',life_financial.recorder_config_id(p_account,o.body#>>'{command,controller_id}'),
                    'timestamp',(o.body#>>'{command,timestamp_ms}')::float8/1000,'type','generic','config',o.body->'command'))) THEN RETURN false; END IF;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE OR REPLACE FUNCTION life_financial.recorder_executor_integrity(p_account uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT life_financial.recorder_executor_only_integrity(p_account) AND life_financial.recorder_config_integrity(p_account)
        AND NOT EXISTS(SELECT 1 FROM life_financial.recorder_outbox WHERE account_id=p_account
            AND stream NOT LIKE 'executor:%' AND stream NOT LIKE 'configuration:%');
$$;

CREATE FUNCTION life_financial.recorder_config_stage(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,p_command jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE op life_financial.identity; old life_financial.operations; prior jsonb; seq bigint; stream_id text; body jsonb; result jsonb; c jsonb;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR p_command IS NULL OR p_cut IS NULL OR p_fingerprint IS NULL THEN
        RAISE EXCEPTION 'LIFE_CONFIG_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    op:=p_command->>'operation_id';
    PERFORM 1 FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    SELECT * INTO old FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF old.command->>'kind' IS DISTINCT FROM 'RECORDER_CONFIG_STAGE' OR old.command->'command' IS DISTINCT FROM p_command
            OR old.command->'binding' IS DISTINCT FROM p_binding OR old.command->'owner' IS DISTINCT FROM p_owner
            OR NOT life_financial.recorder_executor_integrity(p_account) THEN
            RAISE EXCEPTION 'LIFE_CONFIG_REPLAY_CONFLICT' USING ERRCODE='P0001'; END IF;
        RETURN old.receipt||'{"replayed":true}'::jsonb;
    END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    IF life_financial.spot_state(p_account)->>'digest' IS DISTINCT FROM p_cut OR NOT life_financial.recorder_executor_integrity(p_account)
        OR EXISTS(SELECT 1 FROM life_financial.recorder_outbox o WHERE o.account_id=p_account
            AND NOT EXISTS(SELECT 1 FROM life_financial.projection_receipts r WHERE r.account_id=p_account AND r.projection_id=o.projection_id)) THEN
        RAISE EXCEPTION 'LIFE_CONFIG_CUT_OR_DELIVERY_INVALID' USING ERRCODE='P0001'; END IF;
    stream_id:='configuration:'||encode(sha256(convert_to(p_command->>'controller_id','UTF8')),'hex');
    SELECT COALESCE(max(sequence),0)+1 INTO seq FROM life_financial.recorder_outbox WHERE account_id=p_account AND stream=stream_id;
    SELECT head.body->'command' INTO prior FROM life_financial.recorder_outbox head
        WHERE head.account_id=p_account AND head.stream=stream_id AND head.sequence=seq-1;
    PERFORM life_financial.recorder_config_validate(p_account,p_binding,p_command,prior);
    IF (p_command->>'expected_sequence')::bigint<>seq-1 OR (p_command->>'timestamp_ms')::bigint>floor(extract(epoch FROM clock_timestamp())*1000) THEN
        RAISE EXCEPTION 'LIFE_CONFIG_SEQUENCE_OR_TIME_INVALID' USING ERRCODE='P0001'; END IF;
    IF seq=1 AND EXISTS(SELECT 1 FROM life_recorder."Controllers" WHERE id=life_financial.recorder_config_id(p_account,p_command->>'controller_id')) THEN
        RAISE EXCEPTION 'LIFE_CONFIG_FOREIGN_TARGET' USING ERRCODE='P0001'; END IF;
    body:=jsonb_build_object('subtype','CONFIGURATION','command',p_command,'binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut);
    INSERT INTO life_financial.recorder_outbox VALUES(p_account,op,stream_id,seq,body,encode(sha256(convert_to(body::text,'UTF8')),'hex'));
    result:=jsonb_build_object('kind','RECORDER_CONFIG_STAGE','projection_id',op,'sequence',seq,'active_revision',p_command->'active_revision',
        'reason_code',p_command->'reason_code','state','STAGED','version',p_version+1,'replayed',false,'trading_permission',false);
    INSERT INTO life_financial.operations VALUES(p_account,op,p_fingerprint,body||'{"kind":"RECORDER_CONFIG_STAGE"}'::jsonb,result,p_version+1,clock_timestamp());
    c:=(p_command->>'controller_json')::jsonb;
    UPDATE life_financial.accounts SET version=version+1,halted=halted OR p_command->>'outcome'='REJECTED' OR (c->>'manual_kill_switch')::boolean
        WHERE account_id=p_account;
    RETURN result;
END; $$;

CREATE FUNCTION life_financial.recorder_config_apply(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_projection life_financial.identity) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE op text:='configapply:'||encode(sha256(convert_to(p_projection,'UTF8')),'hex'); old life_financial.operations;
    item life_financial.recorder_outbox; command jsonb; request jsonb; result jsonb;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR p_projection IS NULL OR p_cut IS NULL THEN
        RAISE EXCEPTION 'LIFE_CONFIG_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    PERFORM 1 FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    SELECT * INTO STRICT item FROM life_financial.recorder_outbox WHERE account_id=p_account AND projection_id=p_projection AND stream LIKE 'configuration:%';
    IF item.body->'binding' IS DISTINCT FROM p_binding OR item.body->'owner' IS DISTINCT FROM p_owner THEN
        RAISE EXCEPTION 'LIFE_CONFIG_ORIGIN_INVALID' USING ERRCODE='P0001'; END IF;
    SELECT * INTO old FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF old.command->>'kind' IS DISTINCT FROM 'RECORDER_CONFIG_APPLY' OR old.command->>'projection_id' IS DISTINCT FROM p_projection
            OR old.command->'binding' IS DISTINCT FROM p_binding OR old.command->'owner' IS DISTINCT FROM p_owner
            OR NOT life_financial.recorder_executor_integrity(p_account) THEN
            RAISE EXCEPTION 'LIFE_CONFIG_REPLAY_CONFLICT' USING ERRCODE='P0001'; END IF;
        RETURN old.receipt||'{"replayed":true}'::jsonb;
    END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    IF life_financial.spot_state(p_account)->>'digest' IS DISTINCT FROM p_cut OR NOT life_financial.recorder_executor_integrity(p_account)
        OR EXISTS(SELECT 1 FROM life_financial.recorder_outbox o WHERE o.account_id=p_account AND o.stream=item.stream AND o.sequence<item.sequence
            AND NOT EXISTS(SELECT 1 FROM life_financial.projection_receipts r WHERE r.account_id=p_account AND r.projection_id=o.projection_id)) THEN
        RAISE EXCEPTION 'LIFE_CONFIG_CUT_OR_DELIVERY_INVALID' USING ERRCODE='P0001'; END IF;
    command:=item.body->'command';
    -- A row inserted outside the owned outbox cannot be adopted, even if equal.
    IF EXISTS(SELECT 1 FROM life_recorder."Controllers" WHERE id=life_financial.recorder_config_id(p_account,command->>'controller_id')
        AND config::jsonb->>'operation_id'=p_projection) THEN
        RAISE EXCEPTION 'LIFE_CONFIG_FOREIGN_TARGET' USING ERRCODE='P0001'; END IF;
    INSERT INTO life_recorder."Controllers"(id,timestamp,type,config) VALUES(life_financial.recorder_config_id(p_account,command->>'controller_id'),
        (command->>'timestamp_ms')::float8/1000,'generic',command);
    INSERT INTO life_financial.projection_receipts VALUES(p_account,p_projection,item.source_hash);
    result:=jsonb_build_object('kind','RECORDER_CONFIG_APPLY','projection_id',p_projection,'sequence',item.sequence,
        'active_revision',command->'active_revision','reason_code',command->'reason_code','state','APPLIED',
        'version',p_version+1,'replayed',false,'trading_permission',false);
    request:=jsonb_build_object('kind','RECORDER_CONFIG_APPLY','projection_id',p_projection,'binding',p_binding,'owner',p_owner,'cut',p_cut,'version',p_version);
    INSERT INTO life_financial.operations VALUES(p_account,op,encode(sha256(convert_to(request::text,'UTF8')),'hex'),request,result,p_version+1,clock_timestamp());
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    RETURN result;
END; $$;

CREATE OR REPLACE FUNCTION life_financial.commit_owner_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts; expiry timestamptz; at timestamptz; op life_financial.operations;
BEGIN
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=NEW.account_id;
    SELECT * INTO op FROM life_financial.operations WHERE account_id=NEW.account_id AND committed_version=NEW.version;
    IF a.owner_generation IS DISTINCT FROM NEW.owner_generation OR a.owner_process IS DISTINCT FROM NEW.owner_process
        OR a.owner_epoch<>NEW.owner_epoch OR a.current_session IS DISTINCT FROM NEW.current_session
        OR a.lineage<>NEW.lineage OR a.policy_hash<>NEW.policy_hash OR a.config_hash<>NEW.config_hash THEN
        RAISE EXCEPTION 'LIFE_OWNER_EXPIRED_OR_CHANGED_AT_COMMIT' USING ERRCODE='P0001'; END IF;
    IF op.command->>'kind' IN ('RECORDER_CONFIG_STAGE','RECORDER_CONFIG_APPLY','RECORDER_STAGE','RECORDER_APPLY','SPOT_ACK','SPOT_FILL','SPOT_CASHFLOW','SPOT_NAV','SPOT_SESSION_TICK','SPOT_LIFECYCLE','SPOT_TERMINAL') OR op.command->>'kind' IN ('SPOT_ACTION_TRANSITION','SPOT_ACTION_ABANDON')
        AND op.command#>>'{command,state}'<>'DISPATCHED' THEN
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
    IF op.command->>'kind' IN ('SPOT_ACTION_CLAIM','SPOT_ACTION_TRANSITION','SPOT_ACTION_ABANDON') THEN
        PERFORM life_financial.spot_action_commit_guard(NEW.account_id,op.command); END IF;
    IF op.command->>'kind'='SPOT_PREPARE' AND op.command ? 'action' THEN
        PERFORM life_financial.spot_action_prepare_commit_guard(NEW.account_id,op.command); END IF;
    RETURN NULL;
END; $$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
