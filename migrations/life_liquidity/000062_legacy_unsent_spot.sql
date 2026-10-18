-- An original unarmed preparation remains held. IMPORT cannot create an ARM,
-- hide a prior send/observation, or release funds. Prove history at the import
-- cut, independently of a later mutable send-attempt state.
CREATE FUNCTION life_financial.legacy_spot_unsent_valid(p_account uuid,j jsonb,p_version bigint) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE original life_financial.operations;original_cut jsonb;identity_body jsonb;
BEGIN
    SELECT * INTO STRICT original FROM life_financial.operations WHERE account_id=p_account AND operation_id=j->>'operation_id';
    original_cut:=jsonb_build_object('checkpoint',jsonb_build_object('binding',original.command->'binding',
        'revision',original.command->'version','event_version',original.command->'version',
        'resource_versions',jsonb_build_array(jsonb_build_object('resource','account','version',original.command->'version')),
        'state_digest',original.command->'cut'),'components',jsonb_build_array(jsonb_build_array('postgres_account',original.command->'cut')));
    identity_body:=jsonb_build_object('cut',original_cut,'owner',original.command->'owner');
    IF original.command ? 'action' THEN
        identity_body:=identity_body||jsonb_build_object('command',jsonb_build_object('action',original.command->'action',
            'prepare',j,'admission',original.command->'admission'));
    ELSE identity_body:=identity_body||jsonb_build_object('kind','SPOT_PREPARE','command',j);END IF;
    IF original.committed_version>p_version OR original.command->>'kind' IS DISTINCT FROM 'SPOT_PREPARE'
        OR original.command->'command' IS DISTINCT FROM j
        OR original.fingerprint IS DISTINCT FROM encode(sha256(convert_to(life_financial.canonical_json_v1(identity_body),'UTF8')),'hex')
        OR original.receipt IS DISTINCT FROM (jsonb_build_object('kind','SPOT_PREPARE','operation_id',j->>'operation_id',
            'intent_id',j->>'intent_id','state','PREPARED','version',original.committed_version,'replayed',false,'trading_permission',false)
            ||CASE WHEN original.command ? 'action' THEN jsonb_build_object('states',jsonb_build_array(jsonb_build_array(j->>'intent_id','PREPARED'))) ELSE '{}'::jsonb END)
        OR EXISTS(SELECT 1 FROM life_financial.operations o WHERE o.account_id=p_account AND o.committed_version<=p_version
            AND (o.operation_id='arm:'||(j->>'operation_id') OR o.command->>'prepare'=j->>'operation_id'
                OR o.receipt->>'kind' IN ('SPOT_ARM','SPOT_QUOTE_ARM') AND o.receipt->>'intent_id'=j->>'intent_id'
                OR o.command->>'kind' IN ('SPOT_QUOTE_ARM','SPOT_ACK','SPOT_FILL','SPOT_LIFECYCLE','SPOT_TERMINAL')
                    AND (o.command#>>'{command,prepare_operation_id}'=j->>'operation_id' OR o.command#>>'{command,intent_id}'=j->>'intent_id')))
        OR EXISTS(SELECT 1 FROM life_financial.spot_lifecycle_events e LEFT JOIN life_financial.operations o USING(account_id,operation_id)
            WHERE e.account_id=p_account AND e.intent_id=j->>'intent_id' AND (o.operation_id IS NULL OR o.committed_version<=p_version))
        OR EXISTS(SELECT 1 FROM life_financial.spot_fill_facts f LEFT JOIN life_financial.operations o
            ON o.account_id=f.account_id AND o.operation_id=f.source_operation_id
            WHERE f.account_id=p_account AND f.intent_id=j->>'intent_id' AND (o.operation_id IS NULL OR o.committed_version<=p_version))
        OR EXISTS(SELECT 1 FROM life_financial.spot_pending_observations e LEFT JOIN life_financial.operations o USING(account_id,operation_id)
            WHERE e.account_id=p_account AND e.intent_id=j->>'intent_id' AND (o.operation_id IS NULL OR o.committed_version<=p_version))
        OR EXISTS(SELECT 1 FROM life_financial.spot_terminal_facts e LEFT JOIN life_financial.operations o USING(account_id,operation_id)
            WHERE e.account_id=p_account AND e.intent_id=j->>'intent_id' AND (o.operation_id IS NULL OR o.committed_version<=p_version))
        THEN RETURN false;END IF;
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

DO $$ DECLARE definition text;needle text;replacement text;BEGIN
    definition:=pg_get_functiondef('life_financial.legacy_spot_basis_valid(uuid,jsonb,bigint)'::regprocedure);
    needle:='    FOR j IN SELECT value FROM jsonb_array_elements(b->''spot_preparations'') LOOP';
    replacement:=$patch$    IF b ? 'spot_prepared_only_ids' AND (
        jsonb_typeof(b->'spot_prepared_only_ids') IS DISTINCT FROM 'array'
        OR jsonb_array_length(b->'spot_prepared_only_ids') NOT BETWEEN 1 AND 256
        OR EXISTS(SELECT 1 FROM jsonb_array_elements(b->'spot_prepared_only_ids') x WHERE jsonb_typeof(x)<>'string'
            OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(b->'spot_preparations') p WHERE p->'operation_id'=x))
        OR (SELECT count(DISTINCT x) FROM jsonb_array_elements(b->'spot_prepared_only_ids') x)<>jsonb_array_length(b->'spot_prepared_only_ids'))
        THEN RETURN false;END IF;
$patch$||needle;
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_UNSENT_SPOT_MISMATCH';END IF;
    definition:=replace(definition,needle,replacement);
    needle:='            OR NOT EXISTS(SELECT 1 FROM life_financial.operations o WHERE o.account_id=p_account AND o.operation_id=''arm:''||d.operation_id::text';
    replacement:='            OR (CASE WHEN COALESCE(b->''spot_prepared_only_ids'',''[]''::jsonb) @> jsonb_build_array(d.operation_id) THEN
                NOT life_financial.legacy_spot_unsent_valid(p_account,j,p_version)
            ELSE NOT EXISTS(SELECT 1 FROM life_financial.operations o WHERE o.account_id=p_account AND o.operation_id=''arm:''||d.operation_id::text';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_UNSENT_SPOT_MISMATCH';END IF;
    definition:=replace(definition,needle,replacement);
    needle:='AND prepared.command->''binding''=o.command->''binding'')) THEN RETURN false;END IF;';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN RAISE EXCEPTION 'LIFE_LEGACY_UNSENT_SPOT_MISMATCH';END IF;
    EXECUTE replace(definition,needle,'AND prepared.command->''binding''=o.command->''binding'')) END) THEN RETURN false;END IF;');
END; $$;
DO $$ DECLARE r record;BEGIN
    FOR r IN SELECT p.oid::regprocedure AS signature,x.grantee FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE n.nspname='life_financial' AND p.proname='legacy_spot_unsent_valid' AND x.grantee<>p.proowner LOOP
        IF r.grantee=0 THEN EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',r.signature);
        ELSE EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',r.signature,pg_get_userbyid(r.grantee));END IF;
    END LOOP;
END; $$;
