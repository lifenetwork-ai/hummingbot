-- Preserve canonical identity bytes and every recorder integrity predicate.
-- Batch executor stream reads in the existing STABLE proof; no saved verdict,
-- reduced history, clock changes, new grants or financial writes.
DO $$ DECLARE p record;BEGIN
    FOR p IN SELECT f.*,l.lanname FROM pg_proc f JOIN pg_language l ON l.oid=f.prolang WHERE f.oid IN (
        'life_financial.recorder_identity_text(jsonb)'::regprocedure,
        'life_financial.recorder_executor_only_integrity(uuid)'::regprocedure) LOOP
        IF p.lanname<>'plpgsql' OR p.proisstrict OR p.proleakproof OR p.proparallel<>'u'
            OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
            OR p.provolatile<>(CASE WHEN p.proname='recorder_identity_text' THEN 'i' ELSE 's' END)
            OR p.prosecdef IS DISTINCT FROM (p.proname<>'recorder_identity_text')
            OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex') IS DISTINCT FROM (CASE p.proname
                WHEN 'recorder_identity_text' THEN '1df8d0863b9040149ae86087b0f3e16dd8f699c97c49fdfb3b1cc6f7e0586b5b'
                WHEN 'recorder_executor_only_integrity' THEN 'cb7c451743f160b9fe0a1b788d293d01afdf25575060cb0a0aedbcb09822938c'
            END) THEN RAISE EXCEPTION 'LIFE_RECORDER_READS_MIGRATION_MISMATCH';END IF;
    END LOOP;
END; $$;

CREATE OR REPLACE FUNCTION life_financial.recorder_identity_text(p jsonb)
 RETURNS text
 LANGUAGE plpgsql
 IMMUTABLE
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE result text; value text; ch text; code integer; i integer;
BEGIN
    CASE jsonb_typeof(p)
    WHEN 'object' THEN
        SELECT '{'||COALESCE(string_agg(life_financial.recorder_identity_text(to_jsonb(fields.key))||':'||
            life_financial.recorder_identity_text(fields.value),',' ORDER BY fields.key COLLATE "C"),'')||'}'
            INTO result FROM jsonb_each(p) fields;
        RETURN result;
    WHEN 'string' THEN
        value:=p::text;
        IF value COLLATE "C" !~ '[^ -~]' THEN RETURN value; END IF;
        result:='';
        FOR i IN 1..length(value) LOOP
            ch:=substr(value,i,1); code:=ascii(ch);
            IF code>65535 THEN
                code:=code-65536;
                result:=result||'\u'||lpad(to_hex(55296+(code/1024)),4,'0')||'\u'||lpad(to_hex(56320+(code%1024)),4,'0');
            ELSIF code>126 THEN result:=result||'\u'||lpad(to_hex(code),4,'0');
            ELSE result:=result||ch; END IF;
        END LOOP;
        RETURN result;
    WHEN 'number' THEN
        IF p::text !~ '^(0|[1-9][0-9]*)$' THEN RAISE EXCEPTION 'LIFE_EVENT_IDENTITY_INVALID' USING ERRCODE='P0001'; END IF;
        RETURN p::text;
    WHEN 'null' THEN RETURN 'null';
    ELSE RAISE EXCEPTION 'LIFE_EVENT_IDENTITY_INVALID' USING ERRCODE='P0001';
    END CASE;
END; $function$
;

CREATE OR REPLACE FUNCTION life_financial.recorder_executor_only_integrity(p_account uuid)
 RETURNS boolean
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE joined record; delivery life_financial.recorder_outbox; prior jsonb; p jsonb; expected bigint:=0; previous_stream text; actual jsonb; retained_op life_financial.operations;
BEGIN
    FOR joined IN SELECT o AS delivery,op AS retained,previous.account_id IS NOT NULL AS has_previous,
        previous.body#>>'{command,payload_json}' AS prior_payload FROM life_financial.recorder_outbox o
        LEFT JOIN life_financial.operations op ON op.account_id=o.account_id AND op.operation_id=o.projection_id
        LEFT JOIN life_financial.recorder_outbox previous ON previous.account_id=o.account_id
            AND previous.stream=o.stream AND previous.sequence=o.sequence-1
        WHERE o.account_id=p_account AND o.stream LIKE 'executor:%' ORDER BY o.stream,o.sequence LOOP
        delivery:=joined.delivery;retained_op:=joined.retained;
        IF delivery.body->>'subtype' NOT IN ('INITIAL','PROGRESS') THEN RETURN false; END IF;
        p:=(delivery.body#>>'{command,payload_json}')::jsonb;
        PERFORM life_financial.recorder_executor_origin(p_account,delivery.body->'binding',p);
        IF retained_op.account_id IS NULL OR retained_op.command IS DISTINCT FROM jsonb_build_object('kind','RECORDER_STAGE',
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
            IF NOT joined.has_previous THEN RETURN false; END IF;
            prior:=joined.prior_payload::jsonb;
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
END; $function$
;
