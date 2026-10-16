-- Reporting delivery is separate from financial observation eligibility.
CREATE OR REPLACE FUNCTION life_financial.spot_state_v7(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts; s life_financial.sessions; p life_financial.spot_policies;
    snapshot_body jsonb; b jsonb; budgets jsonb; policy jsonb; unknown boolean; unverified boolean; today text;
BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_SCOPE_INVALID' USING ERRCODE='P0001'; END IF;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account;
    SELECT * INTO STRICT s FROM life_financial.sessions WHERE account_id=p_account AND session_id=a.current_session;
    SELECT * INTO p FROM life_financial.spot_policies WHERE account_id=p_account AND policy_hash=a.policy_hash;
    IF NOT FOUND THEN RAISE EXCEPTION 'LIFE_SPOT_POLICY_MISSING' USING ERRCODE='P0001'; END IF;
    today := to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD');
    SELECT jsonb_object_agg(asset,jsonb_build_object('total',total::text,'reserved',reserved::text,'liability',liability::text,'deficit',funding_deficit::text,'version',version,
        'fees',COALESCE((SELECT sum(r.remaining) FROM life_financial.reservations r JOIN life_financial.spot_preparations d
            ON d.account_id=r.account_id AND d.intent_id=r.intent_id WHERE r.account_id=p_account AND r.asset=b.asset
            AND r.state<>'TERMINAL' AND r.reservation_id=d.operation_id||':fee:'||r.asset),0)::text)) INTO b
        FROM life_financial.balances b WHERE account_id=p_account;
    IF b IS NULL OR NOT b ?& ARRAY['LIFE','USDT'] THEN RAISE EXCEPTION 'LIFE_SPOT_BALANCES_MISSING' USING ERRCODE='P0001'; END IF;
    SELECT COALESCE(jsonb_agg(jsonb_build_object('family',family,'scope',scope,'key',window_key,
        'limit',budget_limit::text,'committed',committed::text,'incurred',incurred::text,'version',version)
        ORDER BY family,scope),'[]'::jsonb) INTO budgets FROM life_financial.budget_buckets
        WHERE account_id=p_account AND (scope='SESSION' AND window_key=a.current_session::text OR scope='DAY' AND window_key=today
            OR scope='CAMPAIGN' AND window_key=p.campaign_id);
    unknown := EXISTS(SELECT 1 FROM life_financial.send_attempts WHERE account_id=p_account AND state IN ('ARMED','SEND_UNKNOWN'));
    unverified := EXISTS(SELECT 1 FROM life_financial.send_attempts t WHERE t.account_id=p_account AND t.state NOT IN ('TERMINAL','ABORTED_BEFORE_SEND')
        AND (t.product<>'SPOT' OR t.session_id<>a.current_session OR t.epoch<>a.owner_epoch
            OR t.process_id<>a.owner_process OR t.generation<>a.owner_generation
            OR NOT EXISTS(SELECT 1 FROM life_financial.spot_preparations d JOIN life_financial.operations o USING(account_id,operation_id)
                WHERE d.account_id=t.account_id AND d.intent_id=t.intent_id AND (d.request->>'config_version')::bigint=s.config_version
                AND o.command#>>'{binding,policy_hash}'=a.policy_hash)))
        OR EXISTS(SELECT 1 FROM life_financial.capital_claims WHERE account_id=p_account AND state='HELD')
        OR EXISTS(SELECT 1 FROM life_financial.positions WHERE account_id=p_account AND quantity<>0)
        OR NOT life_financial.spot_accounting_verified(p_account)
        OR EXISTS(SELECT 1 FROM life_financial.financial_events e WHERE e.account_id=p_account AND
            (e.product<>'SPOT' AND NOT (e.product='ACCOUNT' AND e.kind='CASHFLOW') OR e.kind NOT IN ('ACK','FILL','CASHFLOW') OR e.kind='ACK' AND NOT EXISTS(
                SELECT 1 FROM life_financial.send_attempts t WHERE t.account_id=p_account AND t.product='SPOT' AND t.exchange_order_id=e.exchange_identity)
            OR e.kind='CASHFLOW' AND NOT EXISTS(SELECT 1 FROM life_financial.spot_cashflow_facts x WHERE x.account_id=p_account AND x.bill_id=e.exchange_identity AND x.state='APPLIED')
            OR e.kind='FILL' AND NOT EXISTS(SELECT 1 FROM life_financial.spot_fill_facts x WHERE x.account_id=p_account AND x.trade_id=e.exchange_identity)))
        OR NOT life_financial.spot_capital_verified(p_account)
        OR NOT life_financial.spot_actions_verified(p_account)
        OR EXISTS(SELECT 1 FROM life_financial.balances b WHERE b.account_id=p_account AND
            b.reserved<>COALESCE((SELECT sum(r.remaining) FROM life_financial.reservations r WHERE r.account_id=p_account AND r.asset=b.asset AND r.state<>'TERMINAL'),0))
        OR EXISTS(SELECT 1 FROM life_financial.reservations r WHERE r.account_id=p_account AND r.state<>'TERMINAL'
            AND NOT EXISTS(SELECT 1 FROM life_financial.spot_preparations d WHERE d.account_id=r.account_id AND d.intent_id=r.intent_id
                AND (r.reservation_id=d.operation_id||':principal' OR r.reservation_id=d.operation_id||':fee:'||r.asset)))
        OR EXISTS(SELECT 1 FROM (
            SELECT w.scope,w.window_key,sum(greatest(d.subsidy,COALESCE(g.execution_loss,0))) AS held FROM life_financial.spot_preparations d
            JOIN life_financial.send_attempts t USING(account_id,intent_id)
            LEFT JOIN life_financial.spot_accounting_progress g USING(account_id,intent_id)
            CROSS JOIN LATERAL (VALUES ('CAMPAIGN',p.campaign_id::text),
                ('DAY',to_char(d.prepared_at AT TIME ZONE 'UTC','YYYY-MM-DD')),('SESSION',t.session_id::text)) AS w(scope,window_key)
            WHERE d.account_id=p_account AND t.state NOT IN ('TERMINAL','ABORTED_BEFORE_SEND') GROUP BY w.scope,w.window_key
        ) expected LEFT JOIN life_financial.budget_buckets b ON b.account_id=p_account AND b.family='SUBSIDY'
            AND b.scope=expected.scope AND b.window_key=expected.window_key WHERE b.account_id IS NULL OR b.committed<expected.held)
        OR EXISTS(SELECT 1 FROM life_financial.spot_preparations d JOIN life_financial.send_attempts t USING(account_id,intent_id)
            LEFT JOIN life_financial.spot_accounting_progress g USING(account_id,intent_id)
            WHERE d.account_id=p_account AND t.state NOT IN ('TERMINAL','ABORTED_BEFORE_SEND') AND (
                NOT EXISTS(SELECT 1 FROM life_financial.reservations r WHERE r.account_id=p_account AND r.reservation_id=d.operation_id||':principal'
                    AND r.intent_id=t.intent_id AND r.state='HELD' AND r.remaining=(t.quantity-COALESCE(g.filled_base,0))*CASE WHEN t.side='BUY' THEN t.price ELSE 1 END
                    AND r.asset=CASE WHEN t.side='BUY' THEN 'USDT' ELSE 'LIFE' END
                    AND r.amount=CASE WHEN t.side='BUY' THEN t.quantity*t.price ELSE t.quantity END)
                OR greatest(0,d.fee_base-COALESCE(g.paid_base,0))<>COALESCE((SELECT remaining FROM life_financial.reservations WHERE account_id=p_account AND reservation_id=d.operation_id||':fee:LIFE' AND state='HELD'),0)
                OR greatest(0,d.fee_quote-COALESCE(g.paid_quote,0))<>COALESCE((SELECT remaining FROM life_financial.reservations WHERE account_id=p_account AND reservation_id=d.operation_id||':fee:USDT' AND state='HELD'),0)));
    SELECT jsonb_object_agg(k,CASE WHEN jsonb_typeof(v)='number' AND k<>'max_quote_levels' THEN to_jsonb(v::text) ELSE v END)
        INTO policy FROM jsonb_each(to_jsonb(p)) AS fields(k,v);
    snapshot_body := jsonb_build_object('account',to_jsonb(a),'session',to_jsonb(s),'policy',policy,'fill_policy',(SELECT to_jsonb(fp) FROM life_financial.spot_fill_policies fp WHERE fp.account_id=p_account AND fp.policy_hash=a.policy_hash),'capital_policy',(SELECT to_jsonb(cp) FROM life_financial.spot_capital_policies cp WHERE cp.account_id=p_account),'balances',b,'budgets',budgets,
        'day',today,'recovery_required',unknown OR unverified
            OR EXISTS(SELECT 1 FROM life_financial.spot_pending_observations WHERE account_id=p_account)
            OR EXISTS(SELECT 1 FROM life_financial.balances WHERE account_id=p_account AND funding_deficit>0)
            OR EXISTS(SELECT 1 FROM life_financial.spot_preparations d JOIN life_financial.spot_accounting_progress g USING(account_id,intent_id)
                WHERE d.account_id=p_account AND (g.paid_base>d.fee_base OR g.paid_quote>d.fee_quote)),'unverified',unverified);
    RETURN snapshot_body||jsonb_build_object('digest',encode(sha256(convert_to(snapshot_body::text,'UTF8')),'hex'),'observed_at',clock_timestamp());
END; $$;

-- The exact immutable report stays in the outbox. These float conversions only
-- describe the legacy reporting table and never establish financial balances.
CREATE FUNCTION life_financial.recorder_executor_row(p jsonb) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE k text; n numeric; result jsonb:=p;
BEGIN
    IF jsonb_typeof(p) IS DISTINCT FROM 'object'
        OR (SELECT count(*) FROM jsonb_object_keys(p))<>15
        OR NOT p ?& ARRAY['id','timestamp','type','close_type','close_timestamp','status','config',
            'net_pnl_pct','net_pnl_quote','cum_fees_quote','filled_amount_quote','is_active','is_trading','custom_info','controller_id']
        OR p->>'type' IS DISTINCT FROM 'order_executor'
        OR jsonb_typeof(p->'id') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p->'controller_id') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p->'config') IS DISTINCT FROM 'object'
        OR jsonb_typeof(p->'custom_info') IS DISTINCT FROM 'object'
        OR jsonb_typeof(p->'is_active') IS DISTINCT FROM 'boolean'
        OR jsonb_typeof(p->'is_trading') IS DISTINCT FROM 'boolean'
        OR p->>'status' NOT IN ('1','2','3','4') OR jsonb_typeof(p->'status') IS DISTINCT FROM 'number'
        OR p->>'id' IS DISTINCT FROM p#>>'{config,id}'
        OR p->>'controller_id' IS DISTINCT FROM p#>>'{config,controller_id}'
        OR p->>'timestamp' IS DISTINCT FROM p#>>'{config,timestamp}'
        OR p#>>'{config,connector_name}' IS DISTINCT FROM 'okx'
        OR p#>>'{config,trading_pair}' IS DISTINCT FROM 'LIFE-USDT'
        OR p->>'status'='4' AND p->'is_active'<>'false'::jsonb THEN
        RAISE EXCEPTION 'LIFE_RECORDER_PAYLOAD_INVALID' USING ERRCODE='P0001'; END IF;
    PERFORM (p->>'id')::life_financial.identity;
    PERFORM (p->>'controller_id')::life_financial.identity;
    FOREACH k IN ARRAY ARRAY['timestamp','net_pnl_pct','net_pnl_quote','cum_fees_quote','filled_amount_quote'] LOOP
        IF jsonb_typeof(p->k) NOT IN ('number','string') OR p->>k IS NULL THEN
            RAISE EXCEPTION 'LIFE_RECORDER_VALUE_INVALID' USING ERRCODE='P0001'; END IF;
        n:=(p->>k)::numeric;
        IF NOT (n>'-Infinity'::numeric AND n<'Infinity'::numeric) OR k='filled_amount_quote' AND n<0 THEN
            RAISE EXCEPTION 'LIFE_RECORDER_VALUE_INVALID' USING ERRCODE='P0001'; END IF;
        result:=jsonb_set(result,ARRAY[k],to_jsonb(n::float8));
    END LOOP;
    IF p->'close_timestamp'<>'null'::jsonb THEN
        n:=(p->>'close_timestamp')::numeric;
        IF NOT (n>'-Infinity'::numeric AND n<'Infinity'::numeric) THEN
            RAISE EXCEPTION 'LIFE_RECORDER_VALUE_INVALID' USING ERRCODE='P0001'; END IF;
        result:=jsonb_set(result,'{close_timestamp}',to_jsonb(n::bigint));
    END IF;
    IF p->'close_type'<>'null'::jsonb THEN PERFORM (p->>'close_type')::integer; END IF;
    RETURN result;
END; $$;

CREATE FUNCTION life_financial.recorder_executor_origin(p_account uuid,p_binding jsonb,p jsonb) RETURNS void
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE wires jsonb;
BEGIN
    PERFORM life_financial.recorder_executor_row(p);
    wires:=p#>'{custom_info,recovery_order_ids}';
    IF p#>'{custom_info,life_storage_binding}' IS DISTINCT FROM p_binding
        OR p#>>'{custom_info,recovery_account_uid}' IS DISTINCT FROM p_binding->>'account_uid'
        OR jsonb_typeof(wires) IS DISTINCT FROM 'array'
        OR jsonb_array_length(wires)>4096
        OR (SELECT count(*)<>count(DISTINCT value) FROM jsonb_array_elements(wires))
        OR EXISTS(SELECT 1 FROM jsonb_array_elements(wires) WHERE jsonb_typeof(value)<>'string')
        OR NOT EXISTS(SELECT 1 FROM life_financial.spot_action_descriptors d WHERE d.account_id=p_account
            AND d.action_id=p->>'id' AND d.descriptor->>'controller_id'=p->>'controller_id'
            AND (d.descriptor->>'payload_json')::jsonb=p->'config')
        OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(wires) w(wire) WHERE NOT EXISTS(
            SELECT 1 FROM life_financial.send_attempts t WHERE t.account_id=p_account AND t.intent_id=p->>'id'
                AND t.wire_id=w.wire AND t.product='SPOT')) THEN
        RAISE EXCEPTION 'LIFE_RECORDER_ORIGIN_INVALID' USING ERRCODE='P0001'; END IF;
END; $$;

CREATE FUNCTION life_financial.recorder_executor_transition(before jsonb,after jsonb) RETURNS void
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
BEGIN
    IF (before-ARRAY['close_type','close_timestamp','status','net_pnl_pct','net_pnl_quote','cum_fees_quote',
            'filled_amount_quote','is_active','is_trading','custom_info']) IS DISTINCT FROM
        (after-ARRAY['close_type','close_timestamp','status','net_pnl_pct','net_pnl_quote','cum_fees_quote',
            'filled_amount_quote','is_active','is_trading','custom_info'])
        OR (after->>'status')::integer<(before->>'status')::integer
        OR (after->>'filled_amount_quote')::numeric<(before->>'filled_amount_quote')::numeric
        OR NOT (after#>'{custom_info,recovery_order_ids}') @> (before#>'{custom_info,recovery_order_ids}') THEN
        RAISE EXCEPTION 'LIFE_RECORDER_TRANSITION_INVALID' USING ERRCODE='P0001'; END IF;
END; $$;

CREATE FUNCTION life_financial.recorder_executor_integrity(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE delivery life_financial.recorder_outbox; prior jsonb; p jsonb; expected bigint:=0; previous_stream text; actual jsonb; retained_op life_financial.operations;
BEGIN
    FOR delivery IN SELECT * FROM life_financial.recorder_outbox WHERE account_id=p_account ORDER BY stream,sequence LOOP
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
            WHERE r.account_id=p_account AND (r.applied_hash<>o.source_hash OR op.command->>'kind' IS DISTINCT FROM 'RECORDER_APPLY'
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
            WHERE x.account_id=p_account GROUP BY x.stream
            HAVING COALESCE(max(x.sequence) FILTER(WHERE r.projection_id IS NOT NULL),0)
                <>count(*) FILTER(WHERE r.projection_id IS NOT NULL))
        OR EXISTS(SELECT 1 FROM life_financial.operations op WHERE op.account_id=p_account AND op.command->>'kind'='RECORDER_APPLY'
            AND NOT EXISTS(SELECT 1 FROM life_financial.projection_receipts r WHERE r.account_id=p_account AND r.projection_id=op.command->>'projection_id')) THEN RETURN false; END IF;
    FOR delivery IN SELECT DISTINCT ON(x.stream) x.* FROM life_financial.recorder_outbox x
        JOIN life_financial.projection_receipts r USING(account_id,projection_id)
        WHERE x.account_id=p_account ORDER BY x.stream,x.sequence DESC LOOP
        p:=(delivery.body#>>'{command,payload_json}')::jsonb;
        SELECT to_jsonb(e) INTO actual FROM life_recorder."Executors" e WHERE e.id=p->>'id';
        IF actual IS DISTINCT FROM life_financial.recorder_executor_row(p) THEN RETURN false; END IF;
    END LOOP;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.recorder_executor_verified(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_SCOPE_INVALID' USING ERRCODE='P0001'; END IF;
    RETURN life_financial.recorder_executor_integrity(p_account);
END; $$;

CREATE FUNCTION life_financial.recorder_stage_executor(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,p_subtype text,p_command jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE op life_financial.identity; request jsonb; previous life_financial.operations; payload jsonb; stream_id text;
    seq bigint; prior jsonb; body jsonb; result jsonb;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR p_subtype IS NULL OR p_subtype NOT IN ('INITIAL','PROGRESS')
        OR p_command IS NULL OR jsonb_typeof(p_command)<>'object' OR p_cut IS NULL OR p_fingerprint IS NULL
        OR (SELECT count(*) FROM jsonb_object_keys(p_command))<>(CASE WHEN p_subtype='INITIAL' THEN 3 ELSE 4 END)
        OR NOT p_command ?& ARRAY['operation_id','binding','payload_json'] OR p_command->'binding' IS DISTINCT FROM p_binding
        OR jsonb_typeof(p_command->'payload_json') IS DISTINCT FROM 'string' OR octet_length(p_command::text)>1048576
        OR jsonb_typeof(p_command->'operation_id') IS DISTINCT FROM 'string' OR length(p_command->>'operation_id')>96 THEN
        RAISE EXCEPTION 'LIFE_RECORDER_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    op:=p_command->>'operation_id';
    PERFORM 1 FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    SELECT * INTO previous FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF previous.command->>'kind' IS DISTINCT FROM 'RECORDER_STAGE' OR previous.command->>'subtype' IS DISTINCT FROM p_subtype
            OR previous.command->'command' IS DISTINCT FROM p_command OR previous.command->'binding' IS DISTINCT FROM p_binding
            OR previous.command->'owner' IS DISTINCT FROM p_owner OR NOT life_financial.recorder_executor_integrity(p_account) THEN
            RAISE EXCEPTION 'LIFE_RECORDER_REPLAY_CONFLICT' USING ERRCODE='P0001'; END IF;
        RETURN previous.receipt||'{"replayed":true}'::jsonb;
    END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    IF life_financial.spot_state(p_account)->>'digest' IS DISTINCT FROM p_cut
        OR NOT life_financial.recorder_executor_integrity(p_account) THEN
        RAISE EXCEPTION 'LIFE_RECORDER_CUT_OR_PROJECTION_INVALID' USING ERRCODE='P0001'; END IF;
    payload:=(p_command->>'payload_json')::jsonb;
    PERFORM life_financial.recorder_executor_origin(p_account,p_binding,payload);
    stream_id:='executor:'||encode(sha256(convert_to(payload->>'id','UTF8')),'hex');
    SELECT COALESCE(max(sequence),0)+1 INTO seq FROM life_financial.recorder_outbox WHERE account_id=p_account AND stream=stream_id;
    IF seq=1 THEN
        IF p_subtype<>'INITIAL' OR payload->>'status'<>'1' OR (payload->>'filled_amount_quote')::numeric<>0
            OR payload#>'{custom_info,recovery_order_ids}'<>'[]'::jsonb
            OR EXISTS(SELECT 1 FROM life_recorder."Executors" WHERE id=payload->>'id') THEN
            RAISE EXCEPTION 'LIFE_RECORDER_INITIAL_INVALID' USING ERRCODE='P0001'; END IF;
    ELSE
        IF p_subtype<>'PROGRESS' OR jsonb_typeof(p_command->'expected_sequence') IS DISTINCT FROM 'number'
            OR (p_command->>'expected_sequence') !~ '^(0|[1-9][0-9]*)$' OR (p_command->>'expected_sequence')::bigint<>seq-2 THEN
            RAISE EXCEPTION 'LIFE_RECORDER_SEQUENCE_CONFLICT' USING ERRCODE='P0001'; END IF;
        SELECT (head.body#>>'{command,payload_json}')::jsonb INTO STRICT prior FROM life_financial.recorder_outbox head
            WHERE head.account_id=p_account AND head.stream=stream_id AND head.sequence=seq-1;
        PERFORM life_financial.recorder_executor_transition(prior,payload);
    END IF;
    body:=jsonb_build_object('subtype',p_subtype,'command',p_command,'owner',p_owner,'binding',p_binding,'cut',p_cut,'version',p_version);
    INSERT INTO life_financial.recorder_outbox VALUES(p_account,op,stream_id,seq,body,encode(sha256(convert_to(body::text,'UTF8')),'hex'));
    result:=jsonb_build_object('kind','RECORDER_STAGE','projection_id',op,'executor_id',payload->>'id','sequence',seq-1,
        'state','STAGED','version',p_version+1,'replayed',false,'trading_permission',false);
    request:=jsonb_build_object('kind','RECORDER_STAGE','subtype',p_subtype,'command',p_command,'owner',p_owner,'binding',p_binding,'cut',p_cut,'version',p_version);
    INSERT INTO life_financial.operations VALUES(p_account,op,p_fingerprint,request,result,p_version+1,clock_timestamp());
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    RETURN result;
END; $$;

CREATE FUNCTION life_financial.recorder_apply_executor(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_projection life_financial.identity) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE op text:='reportapply:'||encode(sha256(convert_to(p_projection,'UTF8')),'hex'); previous life_financial.operations;
    o life_financial.recorder_outbox; payload jsonb; request jsonb; result jsonb;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR p_projection IS NULL OR p_cut IS NULL THEN
        RAISE EXCEPTION 'LIFE_RECORDER_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    PERFORM 1 FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    SELECT * INTO STRICT o FROM life_financial.recorder_outbox WHERE account_id=p_account AND projection_id=p_projection;
    IF o.body->'binding' IS DISTINCT FROM p_binding OR o.body->'owner' IS DISTINCT FROM p_owner THEN
        RAISE EXCEPTION 'LIFE_RECORDER_ORIGIN_INVALID' USING ERRCODE='P0001'; END IF;
    SELECT * INTO previous FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF previous.command->>'kind' IS DISTINCT FROM 'RECORDER_APPLY' OR previous.command->>'projection_id' IS DISTINCT FROM p_projection
            OR previous.command->'binding' IS DISTINCT FROM p_binding OR previous.command->'owner' IS DISTINCT FROM p_owner
            OR NOT life_financial.recorder_executor_integrity(p_account) THEN
            RAISE EXCEPTION 'LIFE_RECORDER_REPLAY_CONFLICT' USING ERRCODE='P0001'; END IF;
        RETURN previous.receipt||'{"replayed":true}'::jsonb;
    END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    IF life_financial.spot_state(p_account)->>'digest' IS DISTINCT FROM p_cut OR NOT life_financial.recorder_executor_integrity(p_account)
        OR EXISTS(SELECT 1 FROM life_financial.recorder_outbox x WHERE x.account_id=p_account AND x.stream=o.stream AND x.sequence<o.sequence
            AND NOT EXISTS(SELECT 1 FROM life_financial.projection_receipts r WHERE r.account_id=p_account AND r.projection_id=x.projection_id)) THEN
        RAISE EXCEPTION 'LIFE_RECORDER_CUT_OR_SEQUENCE_INVALID' USING ERRCODE='P0001'; END IF;
    payload:=life_financial.recorder_executor_row((o.body#>>'{command,payload_json}')::jsonb);
    IF o.sequence=1 AND EXISTS(SELECT 1 FROM life_recorder."Executors" WHERE id=payload->>'id') THEN
        RAISE EXCEPTION 'LIFE_RECORDER_FOREIGN_TARGET' USING ERRCODE='P0001'; END IF;
    IF o.sequence=1 THEN
        -- Never upsert a globally keyed legacy ID: another account may have
        -- inserted it after our existence check. A unique conflict rolls back.
        INSERT INTO life_recorder."Executors" SELECT * FROM jsonb_populate_record(NULL::life_recorder."Executors",payload);
    ELSE
        UPDATE life_recorder."Executors" SET close_type=(payload->>'close_type')::integer,
            close_timestamp=(payload->>'close_timestamp')::bigint,status=(payload->>'status')::integer,
            net_pnl_pct=(payload->>'net_pnl_pct')::float8,net_pnl_quote=(payload->>'net_pnl_quote')::float8,
            cum_fees_quote=(payload->>'cum_fees_quote')::float8,filled_amount_quote=(payload->>'filled_amount_quote')::float8,
            is_active=(payload->>'is_active')::boolean,is_trading=(payload->>'is_trading')::boolean,custom_info=payload->'custom_info'
            WHERE id=payload->>'id';
        IF NOT FOUND THEN RAISE EXCEPTION 'LIFE_RECORDER_TARGET_MISSING' USING ERRCODE='P0001'; END IF;
    END IF;
    INSERT INTO life_financial.projection_receipts VALUES(p_account,p_projection,o.source_hash);
    result:=jsonb_build_object('kind','RECORDER_APPLY','projection_id',p_projection,'executor_id',payload->>'id','sequence',o.sequence-1,
        'state','APPLIED','version',p_version+1,'replayed',false,'trading_permission',false);
    request:=jsonb_build_object('kind','RECORDER_APPLY','projection_id',p_projection,'binding',p_binding,'owner',p_owner,'cut',p_cut,'version',p_version);
    INSERT INTO life_financial.operations VALUES(p_account,op,encode(sha256(convert_to(request::text,'UTF8')),'hex'),request,result,p_version+1,clock_timestamp());
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    RETURN result;
END; $$;

ALTER FUNCTION life_financial.spot_state(uuid) RENAME TO spot_state_v15;
CREATE FUNCTION life_financial.spot_state(p_account uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE body jsonb; verified boolean; pending boolean;
BEGIN
    body:=life_financial.spot_state_v15(p_account)-'digest'-'observed_at';
    verified:=life_financial.recorder_executor_integrity(p_account);
    pending:=EXISTS(SELECT 1 FROM life_financial.recorder_outbox o WHERE o.account_id=p_account
        AND NOT EXISTS(SELECT 1 FROM life_financial.projection_receipts r WHERE r.account_id=p_account AND r.projection_id=o.projection_id));
    body:=body||jsonb_build_object('recorder_verified',verified,'recorder_pending',pending,
        'financial_recovery_required',(body->>'financial_recovery_required')::boolean OR NOT verified OR pending,
        'recovery_required',(body->>'recovery_required')::boolean OR NOT verified OR pending,
        'unverified',(body->>'unverified')::boolean OR NOT verified);
    RETURN body||jsonb_build_object('digest',encode(sha256(convert_to(body::text,'UTF8')),'hex'),'observed_at',clock_timestamp());
END; $$;

CREATE FUNCTION life_financial.recorder_executor_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF NOT life_financial.recorder_executor_integrity(NEW.account_id) THEN
        RAISE EXCEPTION 'LIFE_RECORDER_PROJECTION_UNVERIFIED' USING ERRCODE='P0001'; END IF;
    RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER recorder_executor_at_commit AFTER UPDATE ON life_financial.accounts
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION life_financial.recorder_executor_commit_guard();

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
    IF op.command->>'kind' IN ('RECORDER_STAGE','RECORDER_APPLY','SPOT_ACK','SPOT_FILL','SPOT_CASHFLOW','SPOT_NAV','SPOT_SESSION_TICK','SPOT_LIFECYCLE','SPOT_TERMINAL') OR op.command->>'kind' IN ('SPOT_ACTION_TRANSITION','SPOT_ACTION_ABANDON')
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

DO $$ DECLARE r record; BEGIN
    FOR r IN SELECT p.oid::regprocedure AS signature,x.grantee FROM pg_proc p
        JOIN pg_namespace n ON n.oid=p.pronamespace
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE n.nspname='life_financial' AND p.proname='spot_state_v15' AND x.grantee<>p.proowner LOOP
        IF r.grantee=0 THEN EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',r.signature);
        ELSE
            EXECUTE format('GRANT EXECUTE ON FUNCTION life_financial.spot_state(uuid) TO %I',pg_get_userbyid(r.grantee));
            EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',r.signature,pg_get_userbyid(r.grantee));
        END IF;
    END LOOP;
END; $$;


REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
