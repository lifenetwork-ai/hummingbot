-- Original ACKs carry zero money. Retain their actual identities, receipts and
-- economic order scope at the historical cut; never infer an ACK from a balance.
CREATE FUNCTION life_financial.legacy_ack_prefix_valid(p_account uuid,basis jsonb,b jsonb,p_version bigint) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE o life_financial.operations;e life_financial.financial_events;j jsonb;prepared life_financial.operations;hash text;
BEGIN
    IF jsonb_typeof(b->'spot_fills') IS DISTINCT FROM 'array'
        OR basis ? 'spot_acknowledgements' AND jsonb_typeof(basis->'spot_acknowledgements') IS DISTINCT FROM 'array' THEN RETURN false;END IF;
    FOR o IN SELECT * FROM life_financial.operations WHERE account_id=p_account AND command->>'kind'='SPOT_ACK' AND committed_version<=p_version LOOP
        j:=o.command->'command';
        SELECT * INTO STRICT prepared FROM life_financial.operations WHERE account_id=p_account AND operation_id=j->>'prepare_operation_id';
        hash:=encode(sha256(convert_to((j-ARRAY['operation_id','observed_at'])::text,'UTF8')),'hex');
        IF o.operation_id::text IS DISTINCT FROM j->>'operation_id'
            OR o.fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(o.command),'UTF8')),'hex')
            OR prepared.command->>'kind' IS DISTINCT FROM 'SPOT_PREPARE' OR prepared.committed_version>=o.committed_version
            OR prepared.command->'binding' IS DISTINCT FROM o.command->'binding' OR prepared.command->'owner' IS DISTINCT FROM o.command->'owner'
            OR (j->>'observed_at')::timestamptz<(SELECT d.prepared_at FROM life_financial.spot_preparations d WHERE d.account_id=p_account AND d.operation_id=prepared.operation_id)
            OR (j->>'observed_at')::timestamptz>o.recorded_at
            OR jsonb_build_array(j->'intent_id',j->'client_order_id',j->'market',j->'side',j->'session_id',j->'epoch')
                IS DISTINCT FROM jsonb_build_array(prepared.command#>'{command,intent_id}',prepared.command#>'{command,client_order_id}',
                    prepared.command#>'{command,market}',prepared.command#>'{command,side}',prepared.command#>'{command,session_id}',prepared.command#>'{command,epoch}')
            OR o.receipt IS DISTINCT FROM jsonb_build_object('kind','SPOT_ACK','operation_id',o.operation_id,
                'intent_id',j->>'intent_id','exchange_order_id',j->>'exchange_order_id','state','ACKED',
                'version',o.committed_version,'replayed',false,'trading_permission',false)
            OR NOT (EXISTS(SELECT 1 FROM jsonb_array_elements(b->'spot_fills') x WHERE
                    x-ARRAY['operation_id','fills','cumulative_base_life','observed_at']=j-ARRAY['operation_id','observed_at'])
                OR EXISTS(SELECT 1 FROM jsonb_array_elements(COALESCE(basis->'spot_acknowledgements','[]'::jsonb)) x WHERE
                    x-ARRAY['operation_id','observed_at']=j-ARRAY['operation_id','observed_at']))
            OR NOT EXISTS(SELECT 1 FROM life_financial.financial_events x WHERE x.account_id=p_account
                AND x.event_id='ack:'||(j->>'client_order_id') AND x.product='SPOT' AND x.kind='ACK'
                AND x.exchange_identity=j->>'exchange_order_id' AND x.source_hash=hash
                AND x.base_delta=0 AND x.quote_delta=0)
            THEN RETURN false;END IF;
    END LOOP;
    FOR e IN SELECT * FROM life_financial.financial_events x WHERE x.account_id=p_account AND x.product='SPOT' AND x.kind='ACK'
        AND (NOT EXISTS(SELECT 1 FROM life_financial.operations z WHERE z.account_id=p_account AND z.command->>'kind'='SPOT_ACK'
                AND x.event_id='ack:'||(z.command#>>'{command,client_order_id}'))
            OR EXISTS(SELECT 1 FROM life_financial.operations z WHERE z.account_id=p_account AND z.command->>'kind'='SPOT_ACK'
                AND x.event_id='ack:'||(z.command#>>'{command,client_order_id}') AND z.committed_version<=p_version)) LOOP
        IF e.base_delta<>0 OR e.quote_delta<>0 OR NOT EXISTS(SELECT 1 FROM life_financial.operations z WHERE z.account_id=p_account
            AND z.command->>'kind'='SPOT_ACK' AND z.committed_version<=p_version
            AND e.event_id='ack:'||(z.command#>>'{command,client_order_id}') AND e.exchange_identity=z.command#>>'{command,exchange_order_id}'
            AND e.occurred_at=(z.command#>>'{command,observed_at}')::timestamptz
            AND e.source_hash=encode(sha256(convert_to(((z.command->'command')-ARRAY['operation_id','observed_at'])::text,'UTF8')),'hex'))
            THEN RETURN false;END IF;
    END LOOP;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

DO $$ DECLARE definition text;needle text;BEGIN
    definition:=pg_get_functiondef('life_financial.legacy_applying_pending(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'::regprocedure);
    needle:='OR NOT life_financial.legacy_parent_prefix_valid(p_account,cmd,p_version)';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_ACK_PREFIX_MISMATCH';END IF;
    definition:=replace(definition,needle,needle||' OR NOT life_financial.legacy_ack_prefix_valid(p_account,r.body->''source_basis'',cmd,p_version)');
    needle:='ELSE true END';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_ACK_PREFIX_MISMATCH';END IF;
    EXECUTE replace(definition,needle,'WHEN e.product=''SPOT'' AND e.kind=''ACK'' THEN false ELSE true END');
    definition:=pg_get_functiondef('life_financial.legacy_spot_verified(uuid)'::regprocedure);
    needle:='AND NOT life_financial.legacy_spot_prefix_valid(p_account,i.body#>''{payload,batch}'',(i.body#>>''{cut,checkpoint,revision}'')::bigint))';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_ACK_PREFIX_MISMATCH';END IF;
    EXECUTE replace(definition,needle,'AND (NOT life_financial.legacy_spot_prefix_valid(p_account,i.body#>''{payload,batch}'',(i.body#>>''{cut,checkpoint,revision}'')::bigint)
        OR NOT life_financial.legacy_ack_prefix_valid(p_account,i.body->''source_basis'',i.body#>''{payload,batch}'',(i.body#>>''{cut,checkpoint,revision}'')::bigint)))');
    -- Retain bounded enum diagnostics when an atomic member refuses. No raw
    -- request/provider content is copied into a parent reason or exception.
    definition:=pg_get_functiondef('life_financial.joint_batch_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'::regprocedure);
    needle:='RAISE EXCEPTION ''LIFE_JOINT_MEMBER_PENDING'' USING ERRCODE=''P0001'';';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_ACK_PREFIX_MISMATCH';END IF;
    EXECUTE replace(definition,needle,'RAISE EXCEPTION ''%'',CASE WHEN member_receipt->>''reason'' ~ ''^[A-Z_]{1,48}$''
        THEN ''LIFE_JOINT_MEMBER_PENDING_''||kind||''_''||(member_receipt->>''reason'') ELSE ''LIFE_JOINT_MEMBER_PENDING'' END USING ERRCODE=''P0001'';');
END; $$;
DO $$ DECLARE r record;BEGIN
    FOR r IN SELECT p.oid::regprocedure AS signature,x.grantee FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE n.nspname='life_financial' AND p.proname='legacy_ack_prefix_valid' AND x.grantee<>p.proowner LOOP
        IF r.grantee=0 THEN EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',r.signature);
        ELSE EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',r.signature,pg_get_userbyid(r.grantee));END IF;
    END LOOP;
END; $$;
