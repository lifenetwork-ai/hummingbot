-- Only newly explained ACK completions require an independently committed
-- original native ACK. Historical File-only diagnostics remain SEND_UNKNOWN;
-- an upgrade must never infer an ACK or rewrite their original import bodies.
CREATE FUNCTION life_financial.legacy_required_ack_prefix_valid(p_account uuid,basis jsonb,p_version bigint) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE required jsonb;acknowledgements jsonb;j jsonb;o life_financial.operations;h text;total bigint;distinct_ids bigint;
BEGIN
    required:=COALESCE(basis->'spot_required_ack_ids','[]'::jsonb);
    IF jsonb_typeof(required) IS DISTINCT FROM 'array'
        OR EXISTS(SELECT 1 FROM jsonb_array_elements(required) x WHERE jsonb_typeof(x)<>'string' OR x#>>'{}'='')
        OR (SELECT count(DISTINCT x) FROM jsonb_array_elements(required) x)<>jsonb_array_length(required)
        OR basis ? 'spot_acknowledgements' AND jsonb_typeof(basis->'spot_acknowledgements') IS DISTINCT FROM 'array' THEN RETURN false;END IF;
    IF jsonb_array_length(required)=0 THEN RETURN true;END IF;
    SELECT jsonb_object_agg(value->>'operation_id',value),count(*),count(DISTINCT value->>'operation_id')
        INTO acknowledgements,total,distinct_ids FROM jsonb_array_elements(COALESCE(basis->'spot_acknowledgements','[]'::jsonb));
    IF total<>distinct_ids THEN RETURN false;END IF;
    FOR h IN SELECT value#>>'{}' FROM jsonb_array_elements(required) LOOP
        j:=acknowledgements->h;
        IF j IS NULL THEN RETURN false;END IF;
        SELECT * INTO STRICT o FROM life_financial.operations WHERE account_id=p_account AND operation_id=h;
        IF o.command->>'kind' IS DISTINCT FROM 'SPOT_ACK' OR o.command->'command' IS DISTINCT FROM j
            OR o.committed_version>p_version
            OR o.fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(o.command),'UTF8')),'hex')
            OR o.receipt IS DISTINCT FROM jsonb_build_object('kind','SPOT_ACK','operation_id',h,
                'intent_id',j->>'intent_id','exchange_order_id',j->>'exchange_order_id','state','ACKED',
                'version',o.committed_version,'replayed',false,'trading_permission',false)
            OR NOT EXISTS(SELECT 1 FROM life_financial.financial_events e WHERE e.account_id=p_account
                AND e.event_id='ack:'||(j->>'client_order_id') AND e.product='SPOT' AND e.kind='ACK'
                AND e.exchange_identity=j->>'exchange_order_id' AND e.base_delta=0 AND e.quote_delta=0
                AND e.occurred_at=(j->>'observed_at')::timestamptz
                AND e.source_hash=encode(sha256(convert_to((j-ARRAY['operation_id','observed_at'])::text,'UTF8')),'hex'))
            THEN RETURN false;END IF;
    END LOOP;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

DO $$ DECLARE definition text;needle text;r record;BEGIN
    definition:=pg_get_functiondef('life_financial.legacy_ack_prefix_valid(uuid,jsonb,jsonb,bigint)'::regprocedure);
    needle:='    FOR o IN SELECT * FROM life_financial.operations WHERE account_id=p_account AND command->>''kind''=''SPOT_ACK'' AND committed_version<=p_version LOOP';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1
        OR position('STABLE' IN definition)=0 OR position('SECURITY DEFINER' IN definition)=0 THEN
        RAISE EXCEPTION 'LIFE_REQUIRED_ACK_PREFIX_MIGRATION_MISMATCH';END IF;
    EXECUTE replace(definition,needle,
        '    IF NOT life_financial.legacy_required_ack_prefix_valid(p_account,basis,p_version) THEN RETURN false;END IF;
'||needle);
    -- Admission, monetary application and cold verification use the original
    -- prefix proof too. No public OID/ACL, table, money or timeout is changed.
    FOR r IN SELECT x.grantee FROM pg_proc p
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE p.oid='life_financial.legacy_required_ack_prefix_valid(uuid,jsonb,bigint)'::regprocedure
            AND x.grantee<>p.proowner LOOP
        IF r.grantee=0 THEN REVOKE ALL ON FUNCTION life_financial.legacy_required_ack_prefix_valid(uuid,jsonb,bigint) FROM PUBLIC;
        ELSE EXECUTE format('REVOKE ALL ON FUNCTION life_financial.legacy_required_ack_prefix_valid(uuid,jsonb,bigint) FROM %I',pg_get_userbyid(r.grantee));END IF;
    END LOOP;
END; $$;
