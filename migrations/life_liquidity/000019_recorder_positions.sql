-- Reporting snapshots retain original signed legs and wire IDs in immutable outbox commands.
-- Native six-decimal rows are never financial position/balance authority.
ALTER TABLE life_financial.recorder_projection_effects DROP CONSTRAINT recorder_projection_effects_table_kind_check;
ALTER TABLE life_financial.recorder_projection_effects ADD CHECK(table_kind IN ('ORDER','STATUS','TRADE','FUNDING','STATE','POSITION'));
ALTER FUNCTION life_financial.recorder_native_get(text,jsonb) RENAME TO recorder_native_get_v18;
CREATE FUNCTION life_financial.recorder_native_get(p_kind text,p_key jsonb) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb;
BEGIN
    IF p_kind='POSITION' THEN SELECT to_jsonb(r) INTO result FROM life_recorder."Position" r WHERE id=p_key->>'id'; RETURN result; END IF;
    RETURN life_financial.recorder_native_get_v18(p_kind,p_key);
END; $$;
ALTER FUNCTION life_financial.recorder_native_put(text,jsonb,jsonb,jsonb) RENAME TO recorder_native_put_v18;
CREATE FUNCTION life_financial.recorder_native_put(p_kind text,p_key jsonb,p_before jsonb,p_after jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF p_kind<>'POSITION' THEN PERFORM life_financial.recorder_native_put_v18(p_kind,p_key,p_before,p_after); RETURN; END IF;
    IF p_after IS NULL OR life_financial.recorder_native_get(p_kind,p_key) IS DISTINCT FROM p_before THEN
        RAISE EXCEPTION 'LIFE_POSITION_NATIVE_CONFLICT' USING ERRCODE='P0001'; END IF;
    IF p_before IS NULL THEN
        INSERT INTO life_recorder."Position" SELECT * FROM jsonb_populate_record(NULL::life_recorder."Position",p_after);
    ELSE
        UPDATE life_recorder."Position" r SET timestamp=n.timestamp,volume_traded_quote=n.volume_traded_quote,
            amount=n.amount,breakeven_price=n.breakeven_price,unrealized_pnl_quote=n.unrealized_pnl_quote,
            realized_pnl_quote=n.realized_pnl_quote,cum_fees_quote=n.cum_fees_quote,side=n.side
            FROM jsonb_populate_record(NULL::life_recorder."Position",p_after) n WHERE r.id=n.id;
    END IF;
END; $$;

CREATE FUNCTION life_financial.recorder_position_scaled(p numeric) RETURNS bigint
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE result numeric:=trunc(p*1000000);
BEGIN
    -- Do not round an intermediate product to the financial NUMERIC(56,18)
    -- domain: that could cross the legacy truncation boundary.
    IF p IS NULL OR NOT result BETWEEN -9223372036854775808::numeric AND 9223372036854775807::numeric THEN
        RAISE EXCEPTION 'LIFE_POSITION_VALUE_INVALID' USING ERRCODE='P0001'; END IF;
    RETURN result::bigint;
END; $$;

CREATE FUNCTION life_financial.recorder_position_validate(p_account uuid,p_binding jsonb,p jsonb,prior jsonb) RETURNS void
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE pos jsonb; old jsonb; k text; wire text; n numeric;
BEGIN
    IF jsonb_typeof(p) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(p))<>6
        OR NOT p ?& ARRAY['operation_id','binding','controller_id','expected_sequence','timestamp_ms','positions']
        OR p->'binding' IS DISTINCT FROM p_binding OR jsonb_typeof(p->'operation_id') IS DISTINCT FROM 'string'
        OR length(p->>'operation_id')>96 OR jsonb_typeof(p->'controller_id') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p->'positions') IS DISTINCT FROM 'array' OR octet_length(p::text)>1048576
        OR jsonb_array_length(p->'positions')>128 THEN
        RAISE EXCEPTION 'LIFE_POSITION_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    PERFORM (p->>'operation_id')::life_financial.identity; PERFORM (p->>'controller_id')::life_financial.identity;
    FOREACH k IN ARRAY ARRAY['expected_sequence','timestamp_ms'] LOOP
        IF jsonb_typeof(p->k) IS DISTINCT FROM 'number' OR p->>k !~ '^(0|[1-9][0-9]*)$' THEN
            RAISE EXCEPTION 'LIFE_POSITION_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
        PERFORM (p->>k)::bigint;
    END LOOP;
    IF NOT EXISTS(SELECT 1 FROM life_financial.configurations c CROSS JOIN LATERAL jsonb_array_elements(c.body->'recorder_sources') r(value)
        WHERE c.account_id=p_account AND c.config_hash=p_binding->>'config_hash' AND c.policy_hash=p_binding->>'policy_hash'
            AND r.value->>'controller_id'=p->>'controller_id') THEN
        RAISE EXCEPTION 'LIFE_POSITION_CONTROLLER_INVALID' USING ERRCODE='P0001'; END IF;
    IF EXISTS(SELECT 1 FROM jsonb_array_elements(p->'positions') x
        GROUP BY x->>'connector_name',x->>'trading_pair',x->>'side' HAVING count(*)<>1) THEN
        RAISE EXCEPTION 'LIFE_POSITION_KEY_CONFLICT' USING ERRCODE='P0001'; END IF;
    FOR pos IN SELECT jsonb_array_elements(p->'positions') LOOP
        IF jsonb_typeof(pos) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(pos))<>14
            OR NOT pos ?& ARRAY['connector_name','trading_pair','side','order_ids','net_amount_base','avg_entry_price',
                'realized_pnl_quote','volume_traded_quote','cum_fees_quote','buy_amount_base','buy_amount_quote',
                'sell_amount_base','sell_amount_quote','mark_price']
            OR jsonb_typeof(pos->'connector_name') IS DISTINCT FROM 'string' OR pos->>'connector_name' NOT IN ('okx','okx_perpetual') OR pos->>'trading_pair' IS DISTINCT FROM 'LIFE-USDT'
            OR NOT (pos->'side'='null'::jsonb OR pos->>'side' IN ('BUY','SELL'))
            OR jsonb_typeof(pos->'order_ids') IS DISTINCT FROM 'array'
            OR EXISTS(SELECT 1 FROM jsonb_array_elements(pos->'order_ids') x WHERE jsonb_typeof(x)<>'string') THEN
            RAISE EXCEPTION 'LIFE_POSITION_BODY_INVALID' USING ERRCODE='P0001'; END IF;
        IF pos->'order_ids' IS DISTINCT FROM COALESCE((SELECT jsonb_agg(w ORDER BY w COLLATE "C") FROM jsonb_array_elements_text(pos->'order_ids') w),'[]'::jsonb) THEN
            RAISE EXCEPTION 'LIFE_POSITION_WIRE_INVALID' USING ERRCODE='P0001'; END IF;
        FOREACH k IN ARRAY ARRAY['net_amount_base','avg_entry_price','realized_pnl_quote','volume_traded_quote','cum_fees_quote',
                'buy_amount_base','buy_amount_quote','sell_amount_base','sell_amount_quote','mark_price'] LOOP
            IF jsonb_typeof(pos->k) IS DISTINCT FROM 'string' OR length(pos->>k)>160 THEN
                RAISE EXCEPTION 'LIFE_POSITION_VALUE_INVALID' USING ERRCODE='P0001'; END IF;
            n:=(pos->>k)::numeric;
            IF NOT n>'-Infinity'::numeric OR NOT n<'Infinity'::numeric
                OR k NOT IN ('net_amount_base','realized_pnl_quote','cum_fees_quote') AND n<0 THEN
                RAISE EXCEPTION 'LIFE_POSITION_VALUE_INVALID' USING ERRCODE='P0001'; END IF;
        END LOOP;
        FOR wire IN SELECT jsonb_array_elements_text(pos->'order_ids') LOOP
            PERFORM wire::life_financial.identity;
            IF NOT EXISTS(SELECT 1 FROM life_financial.send_attempts a JOIN life_financial.spot_action_descriptors d
                ON d.account_id=a.account_id AND d.action_id=a.intent_id
                WHERE a.account_id=p_account AND a.wire_id=wire AND a.product=CASE pos->>'connector_name' WHEN 'okx' THEN 'SPOT' ELSE 'SWAP' END
                    AND d.descriptor->>'controller_id'=p->>'controller_id') THEN
                RAISE EXCEPTION 'LIFE_POSITION_WIRE_INVALID' USING ERRCODE='P0001'; END IF;
        END LOOP;
    END LOOP;
    IF EXISTS(SELECT 1 FROM jsonb_array_elements(p->'positions') entries(item) CROSS JOIN LATERAL jsonb_array_elements_text(entries.item->'order_ids') w(wire)
        GROUP BY w.wire HAVING count(*)<>1) THEN RAISE EXCEPTION 'LIFE_POSITION_WIRE_CONFLICT' USING ERRCODE='P0001'; END IF;
    IF prior IS NOT NULL THEN
        IF (p->>'timestamp_ms')::bigint<(prior->>'timestamp_ms')::bigint THEN
            RAISE EXCEPTION 'LIFE_POSITION_HISTORY_REGRESSION' USING ERRCODE='P0001'; END IF;
        FOR old IN SELECT jsonb_array_elements(prior->'positions') LOOP
            SELECT x INTO pos FROM jsonb_array_elements(p->'positions') x WHERE x->'connector_name'=old->'connector_name'
                AND x->'trading_pair'=old->'trading_pair' AND x->'side'=old->'side';
            IF pos IS NULL OR NOT (pos->'order_ids') @> (old->'order_ids') THEN
                RAISE EXCEPTION 'LIFE_POSITION_HISTORY_REGRESSION' USING ERRCODE='P0001'; END IF;
        END LOOP;
    END IF;
END; $$;

CREATE FUNCTION life_financial.recorder_position_effects(p_account uuid,p_binding jsonb,p_command jsonb,p_version bigint) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE pos jsonb; key jsonb; value jsonb; effects jsonb:='[]'; identity jsonb;
BEGIN
    FOR pos IN SELECT jsonb_array_elements(p_command->'positions') LOOP
        identity:=jsonb_build_array(pos->'connector_name',pos->'trading_pair',pos->'side');
        key:=jsonb_build_object('id','life-pg-position:'||encode(sha256(convert_to(
            p_account::text||':'||(p_command->>'controller_id')||':'||identity::text,'UTF8')),'hex'));
        value:=key||jsonb_build_object('controller_id',p_command->'controller_id','connector_name',pos->'connector_name',
            'trading_pair',pos->'trading_pair','side',CASE WHEN (pos->>'net_amount_base')::numeric>=0 THEN 'BUY' ELSE 'SELL' END,
            'timestamp',p_command->'timestamp_ms','volume_traded_quote',life_financial.recorder_position_scaled((pos->>'volume_traded_quote')::numeric),
            'amount',life_financial.recorder_position_scaled(abs((pos->>'net_amount_base')::numeric)),
            'breakeven_price',life_financial.recorder_position_scaled((pos->>'avg_entry_price')::numeric),
            'unrealized_pnl_quote',life_financial.recorder_position_scaled(((pos->>'mark_price')::numeric-(pos->>'avg_entry_price')::numeric)*(pos->>'net_amount_base')::numeric),
            'realized_pnl_quote',life_financial.recorder_position_scaled((pos->>'realized_pnl_quote')::numeric),
            'cum_fees_quote',life_financial.recorder_position_scaled((pos->>'cum_fees_quote')::numeric));
        effects:=effects||jsonb_build_array(life_financial.recorder_effect(p_account,'POSITION',key,value,p_version));
    END LOOP;
    RETURN COALESCE((SELECT jsonb_agg(x ORDER BY x->>'table_kind',x->>'key_hash') FROM jsonb_array_elements(effects) x),'[]'::jsonb);
END; $$;

CREATE OR REPLACE FUNCTION life_financial.recorder_event_integrity(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE item life_financial.recorder_outbox; retained life_financial.operations; expected jsonb; actual jsonb;
    e life_financial.recorder_projection_effects; parent life_financial.recorder_projection_effects; head record;
BEGIN
    FOR item IN SELECT * FROM life_financial.recorder_outbox WHERE account_id=p_account AND stream LIKE 'event:%' LOOP
        expected:=life_financial.recorder_event_effects(p_account,item.body->'binding',item.body->'command',(item.body->>'version')::bigint);
        SELECT jsonb_agg(to_jsonb(x)-ARRAY['account_id','projection_id','source_version'] ORDER BY x.table_kind,x.key_hash)
            INTO actual FROM life_financial.recorder_projection_effects x WHERE x.account_id=p_account AND x.projection_id=item.projection_id;
        SELECT * INTO retained FROM life_financial.operations WHERE account_id=p_account AND operation_id=item.projection_id;
        IF NOT FOUND OR item.body->>'subtype' IS DISTINCT FROM 'EVENT' OR item.sequence<>1
            OR item.projection_id IS DISTINCT FROM item.body->>'operation_id'
            OR item.projection_id IS DISTINCT FROM life_financial.recorder_event_id(item.body->'command')
            OR item.stream IS DISTINCT FROM 'event:'||(item.body->>'economic_id')
            OR actual IS DISTINCT FROM expected
            OR item.body->>'effects_hash' IS DISTINCT FROM encode(sha256(convert_to(expected::text,'UTF8')),'hex')
            OR item.source_hash IS DISTINCT FROM encode(sha256(convert_to(item.body::text,'UTF8')),'hex')
            OR retained.command IS DISTINCT FROM item.body||'{"kind":"RECORDER_EVENT_STAGE"}'::jsonb
            OR retained.committed_version<>(item.body->>'version')::bigint+1
            OR retained.receipt IS DISTINCT FROM jsonb_build_object('kind','RECORDER_EVENT_STAGE','projection_id',item.projection_id,
                'event_id',substr(item.projection_id,7),'state','STAGED','version',retained.committed_version,'replayed',false,'trading_permission',false) THEN RETURN false; END IF;
        IF EXISTS(SELECT 1 FROM life_financial.projection_receipts WHERE account_id=p_account AND projection_id=item.projection_id) THEN
            SELECT * INTO retained FROM life_financial.operations WHERE account_id=p_account
                AND operation_id='eventapply:'||encode(sha256(convert_to(item.projection_id,'UTF8')),'hex');
            IF NOT FOUND OR retained.command->>'kind' IS DISTINCT FROM 'RECORDER_EVENT_APPLY'
                OR retained.command->>'projection_id' IS DISTINCT FROM item.projection_id
                OR retained.command->'binding' IS DISTINCT FROM item.body->'binding' OR retained.command->'owner' IS DISTINCT FROM item.body->'owner'
                OR retained.receipt IS DISTINCT FROM jsonb_build_object('kind','RECORDER_EVENT_APPLY','projection_id',item.projection_id,
                    'event_id',substr(item.projection_id,7),'state','APPLIED','version',retained.committed_version,'replayed',false,'trading_permission',false)
                OR NOT EXISTS(SELECT 1 FROM life_financial.projection_receipts WHERE account_id=p_account AND projection_id=item.projection_id AND applied_hash=item.source_hash) THEN RETURN false; END IF;
        END IF;
    END LOOP;
    FOR e IN SELECT * FROM life_financial.recorder_projection_effects WHERE account_id=p_account LOOP
        IF NOT EXISTS(SELECT 1 FROM life_financial.recorder_outbox o WHERE o.account_id=p_account AND o.projection_id=e.projection_id
            AND o.body->>'subtype' IN ('EVENT','POSITION') AND (o.body->>'version')::bigint+1=e.source_version) THEN RETURN false; END IF;
        IF e.previous_projection IS NOT NULL THEN
            SELECT * INTO parent FROM life_financial.recorder_projection_effects WHERE account_id=p_account
                AND projection_id=e.previous_projection AND table_kind=e.table_kind AND key_hash=e.key_hash;
            IF NOT FOUND OR parent.source_version>=e.source_version OR parent.after_row IS DISTINCT FROM e.before_row
                OR NOT EXISTS(SELECT 1 FROM life_financial.operations op WHERE op.account_id=p_account
                    AND op.operation_id=(CASE WHEN e.table_kind='POSITION' THEN 'positionapply:' ELSE 'eventapply:' END)||encode(sha256(convert_to(parent.projection_id,'UTF8')),'hex')
                    AND op.committed_version<e.source_version) THEN RETURN false; END IF;
        ELSIF e.before_row IS NOT NULL THEN RETURN false;
        END IF;
    END LOOP;
    IF EXISTS(SELECT 1 FROM life_financial.operations op WHERE op.account_id=p_account AND op.command->>'kind'='RECORDER_EVENT_STAGE'
            AND NOT EXISTS(SELECT 1 FROM life_financial.recorder_outbox o WHERE o.account_id=p_account AND o.projection_id=op.operation_id AND o.stream LIKE 'event:%'))
        OR EXISTS(SELECT 1 FROM life_financial.operations op WHERE op.account_id=p_account AND op.command->>'kind'='RECORDER_EVENT_APPLY'
            AND NOT EXISTS(SELECT 1 FROM life_financial.projection_receipts r WHERE r.account_id=p_account AND r.projection_id=op.command->>'projection_id')) THEN RETURN false; END IF;
    FOR head IN SELECT DISTINCT ON(x.table_kind,x.key_hash) x.*,count(*) OVER(PARTITION BY x.table_kind,x.key_hash) AS expected_version
        FROM life_financial.recorder_projection_effects x JOIN life_financial.projection_receipts r USING(account_id,projection_id)
        WHERE x.account_id=p_account ORDER BY x.table_kind,x.key_hash,x.source_version DESC LOOP
        IF NOT EXISTS(SELECT 1 FROM life_financial.recorder_projection_heads h WHERE h.account_id=p_account
                AND h.table_kind=head.table_kind AND h.key_hash=head.key_hash AND h.projection_id=head.projection_id AND h.version=head.expected_version)
            OR life_financial.recorder_native_get(head.table_kind,head.target_key) IS DISTINCT FROM head.after_row THEN RETURN false; END IF;
    END LOOP;
    IF EXISTS(SELECT 1 FROM life_financial.recorder_projection_heads h WHERE h.account_id=p_account AND NOT EXISTS(
        SELECT 1 FROM life_financial.projection_receipts r WHERE r.account_id=p_account AND r.projection_id=h.projection_id)) THEN RETURN false; END IF;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;


CREATE FUNCTION life_financial.recorder_position_integrity(p_account uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE item life_financial.recorder_outbox; retained life_financial.operations; command jsonb; prior jsonb;
    stream_id text; seq bigint:=0; expected jsonb; actual jsonb;
BEGIN
    FOR item IN SELECT * FROM life_financial.recorder_outbox WHERE account_id=p_account AND stream LIKE 'position:%' ORDER BY stream,sequence LOOP
        command:=item.body->'command';
        IF stream_id IS DISTINCT FROM item.stream THEN seq:=0; prior:=NULL; END IF;
        stream_id:=item.stream; seq:=seq+1;
        PERFORM life_financial.recorder_position_validate(p_account,item.body->'binding',command,prior);
        expected:=life_financial.recorder_position_effects(p_account,item.body->'binding',command,(item.body->>'version')::bigint);
        SELECT COALESCE(jsonb_agg(to_jsonb(x)-ARRAY['account_id','projection_id','source_version'] ORDER BY x.table_kind,x.key_hash),'[]'::jsonb)
            INTO actual FROM life_financial.recorder_projection_effects x WHERE x.account_id=p_account AND x.projection_id=item.projection_id;
        SELECT * INTO retained FROM life_financial.operations WHERE account_id=p_account AND operation_id=item.projection_id;
        IF NOT FOUND OR item.sequence<>seq OR item.body->>'subtype' IS DISTINCT FROM 'POSITION'
            OR item.stream IS DISTINCT FROM 'position:'||encode(sha256(convert_to(command->>'controller_id','UTF8')),'hex')
            OR item.projection_id IS DISTINCT FROM command->>'operation_id' OR (command->>'expected_sequence')::bigint<>seq-1
            OR item.source_hash IS DISTINCT FROM encode(sha256(convert_to(item.body::text,'UTF8')),'hex')
            OR actual IS DISTINCT FROM expected OR item.body->>'effects_hash' IS DISTINCT FROM encode(sha256(convert_to(expected::text,'UTF8')),'hex')
            OR retained.command IS DISTINCT FROM item.body||'{"kind":"RECORDER_POSITION_STAGE"}'::jsonb
            OR retained.committed_version<>(item.body->>'version')::bigint+1
            OR retained.receipt IS DISTINCT FROM jsonb_build_object('kind','RECORDER_POSITION_STAGE','projection_id',item.projection_id,
                'sequence',seq,'state','STAGED','version',retained.committed_version,'replayed',false,'trading_permission',false) THEN RETURN false; END IF;
        IF EXISTS(SELECT 1 FROM life_financial.projection_receipts WHERE account_id=p_account AND projection_id=item.projection_id) THEN
            SELECT * INTO retained FROM life_financial.operations WHERE account_id=p_account
                AND operation_id='positionapply:'||encode(sha256(convert_to(item.projection_id,'UTF8')),'hex');
            IF NOT FOUND OR retained.command->>'kind' IS DISTINCT FROM 'RECORDER_POSITION_APPLY'
                OR retained.command->>'projection_id' IS DISTINCT FROM item.projection_id
                OR retained.command->'owner' IS DISTINCT FROM item.body->'owner' OR retained.command->'binding' IS DISTINCT FROM item.body->'binding'
                OR retained.receipt IS DISTINCT FROM jsonb_build_object('kind','RECORDER_POSITION_APPLY','projection_id',item.projection_id,
                    'sequence',seq,'state','APPLIED','version',retained.committed_version,'replayed',false,'trading_permission',false)
                OR NOT EXISTS(SELECT 1 FROM life_financial.projection_receipts WHERE account_id=p_account AND projection_id=item.projection_id AND applied_hash=item.source_hash) THEN RETURN false; END IF;
        END IF;
        prior:=command;
    END LOOP;
    IF EXISTS(SELECT 1 FROM life_financial.operations op WHERE op.account_id=p_account AND op.command->>'kind'='RECORDER_POSITION_STAGE'
            AND NOT EXISTS(SELECT 1 FROM life_financial.recorder_outbox o WHERE o.account_id=p_account AND o.projection_id=op.operation_id AND o.stream LIKE 'position:%'))
        OR EXISTS(SELECT 1 FROM life_financial.operations op WHERE op.account_id=p_account AND op.command->>'kind'='RECORDER_POSITION_APPLY'
            AND NOT EXISTS(SELECT 1 FROM life_financial.projection_receipts r WHERE r.account_id=p_account AND r.projection_id=op.command->>'projection_id'))
        OR EXISTS(SELECT 1 FROM life_financial.recorder_outbox o LEFT JOIN life_financial.projection_receipts r USING(account_id,projection_id)
            WHERE o.account_id=p_account AND o.stream LIKE 'position:%' GROUP BY o.stream
            HAVING COALESCE(max(o.sequence) FILTER(WHERE r.projection_id IS NOT NULL),0)<>count(*) FILTER(WHERE r.projection_id IS NOT NULL)) THEN RETURN false; END IF;
    -- Catch unowned extra rows as well as changed/missing expected rows.
    IF EXISTS(SELECT 1 FROM life_recorder."Position" p WHERE EXISTS(
        SELECT 1 FROM life_financial.recorder_outbox o WHERE o.account_id=p_account AND o.stream LIKE 'position:%'
            AND o.body#>>'{command,controller_id}'=p.controller_id)
        AND NOT EXISTS(SELECT 1 FROM life_financial.recorder_projection_heads h JOIN life_financial.recorder_projection_effects e
            USING(account_id,projection_id,table_kind,key_hash)
            WHERE h.account_id=p_account AND h.table_kind='POSITION' AND e.after_row=to_jsonb(p))) THEN RETURN false; END IF;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE OR REPLACE FUNCTION life_financial.recorder_executor_integrity(p_account uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT life_financial.recorder_executor_only_integrity(p_account) AND life_financial.recorder_config_integrity(p_account)
        AND life_financial.recorder_event_integrity(p_account) AND life_financial.recorder_position_integrity(p_account)
        AND NOT EXISTS(SELECT 1 FROM life_financial.recorder_outbox WHERE account_id=p_account
            AND stream NOT LIKE 'executor:%' AND stream NOT LIKE 'configuration:%' AND stream NOT LIKE 'event:%' AND stream NOT LIKE 'position:%');
$$;

CREATE FUNCTION life_financial.recorder_position_stage(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_fingerprint life_financial.digest,p_command jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p_operation life_financial.identity:=p_command->>'operation_id'; prior jsonb; seq bigint; stream_id text; old life_financial.operations; effects jsonb; effect jsonb; body jsonb; result jsonb;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR p_operation IS NULL OR length(p_operation)>96
        OR p_cut IS NULL OR p_fingerprint IS NULL OR p_command IS NULL THEN
        RAISE EXCEPTION 'LIFE_POSITION_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    PERFORM 1 FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    SELECT * INTO old FROM life_financial.operations WHERE account_id=p_account AND operation_id=p_operation;
    IF FOUND THEN
        IF old.command->>'kind' IS DISTINCT FROM 'RECORDER_POSITION_STAGE' OR old.command->'command' IS DISTINCT FROM p_command
            OR old.command->'binding' IS DISTINCT FROM p_binding OR old.command->'owner' IS DISTINCT FROM p_owner
            OR NOT life_financial.recorder_executor_integrity(p_account) THEN
            RAISE EXCEPTION 'LIFE_POSITION_REPLAY_CONFLICT' USING ERRCODE='P0001'; END IF;
        RETURN old.receipt||'{"replayed":true}'::jsonb;
    END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    IF life_financial.spot_state(p_account)->>'digest' IS DISTINCT FROM p_cut OR NOT life_financial.recorder_executor_integrity(p_account)
        OR EXISTS(SELECT 1 FROM life_financial.recorder_outbox o WHERE o.account_id=p_account AND NOT EXISTS(
            SELECT 1 FROM life_financial.projection_receipts r WHERE r.account_id=p_account AND r.projection_id=o.projection_id)) THEN
        RAISE EXCEPTION 'LIFE_POSITION_CUT_OR_DELIVERY_INVALID' USING ERRCODE='P0001'; END IF;
    stream_id:='position:'||encode(sha256(convert_to(p_command->>'controller_id','UTF8')),'hex');
    SELECT COALESCE(max(sequence),0)+1 INTO seq FROM life_financial.recorder_outbox WHERE account_id=p_account AND stream=stream_id;
    SELECT head.body->'command' INTO prior FROM life_financial.recorder_outbox head WHERE head.account_id=p_account AND head.stream=stream_id AND head.sequence=seq-1;
    PERFORM life_financial.recorder_position_validate(p_account,p_binding,p_command,prior);
    IF (p_command->>'expected_sequence')::bigint<>seq-1 THEN
        RAISE EXCEPTION 'LIFE_POSITION_SEQUENCE_INVALID' USING ERRCODE='P0001'; END IF;
    IF (p_command->>'timestamp_ms')::bigint>floor(extract(epoch FROM clock_timestamp())*1000) THEN
        RAISE EXCEPTION 'LIFE_POSITION_TIME_INVALID' USING ERRCODE='P0001'; END IF;
    effects:=life_financial.recorder_position_effects(p_account,p_binding,p_command,p_version);
    FOR effect IN SELECT jsonb_array_elements(effects) LOOP
        -- Stable global target lock order also covers legacy tables without a
        -- natural unique index. Account CAS alone cannot serialize two accounts.
        PERFORM pg_advisory_xact_lock(hashtextextended((effect->>'table_kind')||':'||(effect->'target_key')::text,0));
        IF life_financial.recorder_native_get(effect->>'table_kind',effect->'target_key') IS DISTINCT FROM NULLIF(effect->'before_row','null'::jsonb) THEN
            RAISE EXCEPTION 'LIFE_POSITION_NATIVE_CONFLICT' USING ERRCODE='P0001'; END IF;
    END LOOP;
    body:=jsonb_build_object('subtype','POSITION','command',p_command,
        'binding',p_binding,'owner',p_owner,'version',p_version,'cut',p_cut,
        'effects_hash',encode(sha256(convert_to(effects::text,'UTF8')),'hex'));
    INSERT INTO life_financial.recorder_outbox VALUES(p_account,p_operation,stream_id,seq,body,encode(sha256(convert_to(body::text,'UTF8')),'hex'));
    FOR effect IN SELECT jsonb_array_elements(effects) LOOP
        INSERT INTO life_financial.recorder_projection_effects VALUES(p_account,p_operation,effect->>'table_kind',effect->>'key_hash',
            effect->'target_key',effect->>'previous_projection',NULLIF(effect->'before_row','null'::jsonb),NULLIF(effect->'after_row','null'::jsonb),p_version+1);
    END LOOP;
    result:=jsonb_build_object('kind','RECORDER_POSITION_STAGE','projection_id',p_operation,'sequence',seq,
        'state','STAGED','version',p_version+1,'replayed',false,'trading_permission',false);
    INSERT INTO life_financial.operations VALUES(p_account,p_operation,p_fingerprint,body||'{"kind":"RECORDER_POSITION_STAGE"}'::jsonb,result,p_version+1,clock_timestamp());
    UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;
    RETURN result;
END; $$;


CREATE FUNCTION life_financial.recorder_position_apply(p_account uuid,p_binding jsonb,p_owner jsonb,p_version bigint,
    p_cut life_financial.digest,p_projection life_financial.identity) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE op text:='positionapply:'||encode(sha256(convert_to(p_projection,'UTF8')),'hex'); old life_financial.operations;
    item life_financial.recorder_outbox; effect life_financial.recorder_projection_effects; request jsonb; result jsonb;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR p_projection IS NULL OR p_cut IS NULL THEN
        RAISE EXCEPTION 'LIFE_POSITION_COMMAND_INVALID' USING ERRCODE='P0001'; END IF;
    PERFORM 1 FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    SELECT * INTO STRICT item FROM life_financial.recorder_outbox WHERE account_id=p_account AND projection_id=p_projection AND stream LIKE 'position:%';
    IF item.body->'binding' IS DISTINCT FROM p_binding OR item.body->'owner' IS DISTINCT FROM p_owner THEN
        RAISE EXCEPTION 'LIFE_POSITION_ORIGIN_INVALID' USING ERRCODE='P0001'; END IF;
    SELECT * INTO old FROM life_financial.operations WHERE account_id=p_account AND operation_id=op;
    IF FOUND THEN
        IF old.command->>'kind' IS DISTINCT FROM 'RECORDER_POSITION_APPLY' OR old.command->>'projection_id' IS DISTINCT FROM p_projection
            OR old.command->'binding' IS DISTINCT FROM p_binding OR old.command->'owner' IS DISTINCT FROM p_owner
            OR NOT life_financial.recorder_executor_integrity(p_account) THEN
            RAISE EXCEPTION 'LIFE_POSITION_REPLAY_CONFLICT' USING ERRCODE='P0001'; END IF;
        RETURN old.receipt||'{"replayed":true}'::jsonb;
    END IF;
    PERFORM life_financial.guard_spot_observation(p_account,p_binding,p_owner,p_version);
    IF life_financial.spot_state(p_account)->>'digest' IS DISTINCT FROM p_cut OR NOT life_financial.recorder_executor_integrity(p_account) THEN
        RAISE EXCEPTION 'LIFE_POSITION_CUT_OR_DELIVERY_INVALID' USING ERRCODE='P0001'; END IF;
    FOR effect IN SELECT * FROM life_financial.recorder_projection_effects WHERE account_id=p_account AND projection_id=p_projection
        ORDER BY table_kind,key_hash LOOP
        PERFORM pg_advisory_xact_lock(hashtextextended(effect.table_kind||':'||effect.target_key::text,0));
        IF (SELECT h.projection_id FROM life_financial.recorder_projection_heads h WHERE h.account_id=p_account
            AND h.table_kind=effect.table_kind AND h.key_hash=effect.key_hash) IS DISTINCT FROM effect.previous_projection THEN
            RAISE EXCEPTION 'LIFE_POSITION_PREDECESSOR_INVALID' USING ERRCODE='P0001'; END IF;
        PERFORM life_financial.recorder_native_put(effect.table_kind,effect.target_key,effect.before_row,effect.after_row);
        INSERT INTO life_financial.recorder_projection_heads VALUES(p_account,effect.table_kind,effect.key_hash,p_projection,1)
        ON CONFLICT(account_id,table_kind,key_hash) DO UPDATE SET projection_id=EXCLUDED.projection_id,version=recorder_projection_heads.version+1;
    END LOOP;
    INSERT INTO life_financial.projection_receipts VALUES(p_account,p_projection,item.source_hash);
    request:=jsonb_build_object('kind','RECORDER_POSITION_APPLY','projection_id',p_projection,'binding',p_binding,'owner',p_owner,'cut',p_cut,'version',p_version);
    result:=jsonb_build_object('kind','RECORDER_POSITION_APPLY','projection_id',p_projection,'sequence',item.sequence,
        'state','APPLIED','version',p_version+1,'replayed',false,'trading_permission',false);
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
    IF op.command->>'kind' IN ('RECORDER_POSITION_STAGE','RECORDER_POSITION_APPLY','RECORDER_EVENT_STAGE','RECORDER_EVENT_APPLY','RECORDER_CONFIG_STAGE','RECORDER_CONFIG_APPLY','RECORDER_STAGE','RECORDER_APPLY','SPOT_ACK','SPOT_FILL','SPOT_CASHFLOW','SPOT_NAV','SPOT_SESSION_TICK','SPOT_LIFECYCLE','SPOT_TERMINAL') OR op.command->>'kind' IN ('SPOT_ACTION_TRANSITION','SPOT_ACTION_ABANDON')
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
