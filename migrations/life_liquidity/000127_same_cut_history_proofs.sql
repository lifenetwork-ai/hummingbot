-- Reuse only proofs obtained within the same STABLE MVCC invocation.
-- Standalone verifiers and every post-write/physical-COMMIT audit remain fresh.
-- No permission cache, reduced history, new policy cap or clock change.
DO $migration$
DECLARE expected jsonb:='[{"signature":"life_financial.recorder_event_stage(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,life_financial.identity,jsonb)","sha256":"644bb9ec63318067f74948c13878128455c9f666e5ae6111a51af27039f54308"},{"signature":"life_financial.spot_inventory_commit_guard(uuid,jsonb)","sha256":"448fed763fd8f60570f94c39d055cf5a609280f3b06c9f8b3635066686415890"},{"signature":"life_financial.recorder_stage_executor(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb)","sha256":"b1aeaa4332e71606a44c831221ea6ed3b949958195025095d8cda3741cdccacb"},{"signature":"life_financial.recorder_position_stage(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)","sha256":"1eb7f90eda5a9590b76c85c5b7f48125ce89a304f5be00d20dbc7d9cc5104264"},{"signature":"life_financial.recorder_apply_executor(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.identity)","sha256":"48569fca0fc99f17b901bdb25563caf913e6f4bf31202b72fe45a34d1431eada"},{"signature":"life_financial.spot_inventory_projection_guard()","sha256":"2c84dd3d40502a9175012a639f0180576585a8521ca2832b561dd349f70e892e"},{"signature":"life_financial.spot_body_v13(uuid)","sha256":"3f19f63dc365174233cd2110c742e221d58636c375b6c1046bfa5be0d3970f2f"},{"signature":"life_financial.spot_body_v7(uuid)","sha256":"3981bc3ce0400d6b17f7ac569b09ce1ccc49fdfa57e8b16c37d2a81fce561d52"},{"signature":"life_financial.spot_actions_verified(uuid)","sha256":"eba036f199794fd21f1aa53f5e437deb56b7a450c40b17cbf20ccdd93247beae"},{"signature":"life_financial.spot_body_with_carry_proof(uuid,boolean)","sha256":"6992953546cc8f036dfebb1605bfd7f5d160a4c58b4f93311d41f0b4ffe44c6c"},{"signature":"life_financial.spot_lifecycle_verified(uuid)","sha256":"af9018573c2023c117fd97a9147412af427b515591130dd1ba82587a17d8e607"},{"signature":"life_financial.recorder_event_apply(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.identity)","sha256":"ad0680d300e2c7cc989356610b351b8d2ab2b015c208e6a6600e2573a50d7c54"},{"signature":"life_financial.recorder_position_apply(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.identity)","sha256":"09b580fa8f24f90bef45a77f9711d6b7a6f47a01a18afc59d9c8ac2f70d4184d"},{"signature":"life_financial.recorder_config_stage(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)","sha256":"da55ff24ac9c81b05b12587fe82474f354af8dde5963e4cbf635d267aae3e206"},{"signature":"life_financial.recorder_config_apply(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.identity)","sha256":"38897414811c7e2786ade818cd93d3db536da4b163fcbc9a539cfd909099f34e"}]'::jsonb; item jsonb;p record;
BEGIN
    FOR item IN SELECT value FROM jsonb_array_elements(expected) LOOP
        SELECT f.*,l.lanname INTO p FROM pg_proc f JOIN pg_language l ON l.oid=f.prolang
            WHERE f.oid=to_regprocedure(item->>'signature');
        IF NOT FOUND OR p.lanname<>'plpgsql' OR NOT p.prosecdef OR p.proisstrict
            OR p.proleakproof OR p.proparallel<>'u' OR p.proretset OR p.prokind<>'f' OR p.pronargdefaults<>0
            OR p.proowner<>(SELECT nspowner FROM pg_namespace WHERE nspname='life_financial')
            OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
            OR p.provolatile<>(CASE WHEN p.proname LIKE 'recorder%stage%' OR p.proname LIKE 'recorder%apply%'
                OR p.proname IN ('spot_inventory_commit_guard','spot_inventory_projection_guard') THEN 'v' ELSE 's' END)
            OR p.prorettype<>(CASE WHEN p.proname='spot_inventory_projection_guard' THEN 'trigger'::regtype
                WHEN p.proname='spot_inventory_commit_guard' THEN 'void'::regtype
                WHEN p.proname IN ('spot_actions_verified','spot_lifecycle_verified') THEN 'boolean'::regtype ELSE 'jsonb'::regtype END)
            OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')<>item->>'sha256' THEN
            RAISE EXCEPTION 'LIFE_HISTORY_FLOW_UNKNOWN_PREDECESSOR';END IF;
    END LOOP;
END;
$migration$;

-- spot_body_v7's false aggregate proves its complete spot_actions_verified
-- succeeded at this exact cut. For a retained RECONCILED action that original
-- predicate necessarily includes the full lifecycle/settlement proof. Empty,
-- active, corrupt or NULL projections retain the original independent call.
DO $$ DECLARE definition text;needle text;replacement text;BEGIN
    definition:=pg_get_functiondef('life_financial.spot_body_v13(uuid)'::regprocedure);
    needle:='life_financial.spot_lifecycle_verified(p_account)';
    replacement:='(CASE WHEN body->''unverified'' IS NOT DISTINCT FROM ''false''::jsonb AND EXISTS(
        SELECT 1 FROM life_financial.quote_actions WHERE account_id=p_account AND state=''RECONCILED'')
        THEN true ELSE life_financial.spot_lifecycle_verified(p_account) END)';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_HISTORY_FLOW_UNKNOWN_PREDECESSOR';END IF;
    EXECUTE replace(definition,needle,replacement);
    -- Both original expressions are evaluated in a single SQL invocation.
    -- The pinned lifecycle predicate itself includes the settlement predicate;
    -- invoking it first separately adds no proof. All deferred triggers stay.
    definition:=pg_get_functiondef('life_financial.spot_inventory_commit_guard(uuid,jsonb)'::regprocedure);
    needle:='NOT life_financial.spot_inventory_settlements_verified(p_account) OR NOT life_financial.spot_lifecycle_verified(p_account)';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_HISTORY_FLOW_UNKNOWN_PREDECESSOR';END IF;
    EXECUTE replace(definition,needle,'NOT life_financial.spot_lifecycle_verified(p_account)');
    definition:=pg_get_functiondef('life_financial.spot_inventory_projection_guard()'::regprocedure);
    needle:='NOT life_financial.spot_inventory_settlements_verified(NEW.account_id) OR NOT life_financial.spot_lifecycle_verified(NEW.account_id)';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_HISTORY_FLOW_UNKNOWN_PREDECESSOR';END IF;
    EXECUTE replace(definition,needle,'NOT life_financial.spot_lifecycle_verified(NEW.account_id)');
END; $$;

-- A full spot_state already supplies recorder_executor_integrity as the
-- recorder_verified member. Check both the cut and that original result inside
-- this one STABLE invocation, before the caller performs any monetary write.
-- Neither a caller-supplied Boolean nor an earlier query can satisfy it.
CREATE FUNCTION life_financial.recorder_cut_verified(p_account uuid,p_cut life_financial.digest) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE view jsonb;BEGIN
    view:=life_financial.spot_state(p_account);
    RETURN view->>'digest' IS NOT DISTINCT FROM p_cut
        AND view->'recorder_verified' IS NOT DISTINCT FROM 'true'::jsonb;
END; $$;
DO $$ DECLARE r record;signature text;definition text;needle text;replacement text;BEGIN
    FOR r IN SELECT DISTINCT x.grantee FROM pg_proc p
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
        WHERE p.oid='life_financial.recorder_cut_verified(uuid,life_financial.digest)'::regprocedure AND x.grantee<>p.proowner LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION life_financial.recorder_cut_verified(uuid,life_financial.digest) FROM %s',
            CASE WHEN r.grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(r.grantee)) END);
    END LOOP;
    FOR signature IN SELECT p.oid::regprocedure::text FROM pg_proc p
        WHERE p.pronamespace='life_financial'::regnamespace AND p.proname IN (
            'recorder_stage_executor','recorder_apply_executor','recorder_config_stage','recorder_config_apply',
            'recorder_event_stage','recorder_event_apply','recorder_position_stage','recorder_position_apply') LOOP
        definition:=pg_get_functiondef(to_regprocedure(signature));
        needle:=$n$life_financial.spot_state\(p_account\)->>'digest' IS DISTINCT FROM p_cut\s+OR NOT life_financial.recorder_executor_integrity\(p_account\)$n$;
        IF (SELECT count(*) FROM regexp_matches(definition,needle,'g'))<>1 THEN
            RAISE EXCEPTION 'LIFE_HISTORY_FLOW_UNKNOWN_PREDECESSOR';END IF;
        replacement:='NOT life_financial.recorder_cut_verified(p_account,p_cut)';
        EXECUTE regexp_replace(definition,needle,replacement);
    END LOOP;
END; $$;
