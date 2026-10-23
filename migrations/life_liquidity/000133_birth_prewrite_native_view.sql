-- One fresh pre-write state proves both birth provenance and financial state.
-- The view is constructed under the account lock inside the existing entrypoint;
-- no client-supplied or cross-command proof is accepted. Independent post-write
-- and physical-COMMIT guards, source timestamps, cuts and limits remain intact.
DO $migration$
DECLARE expected jsonb:='[
 {"signature":"guard_account(uuid,jsonb,jsonb,bigint)","sha256":"c348514f9ab4ccaca93e8cbeafa80a72463043776d3858ed83141020984932d6"},
 {"signature":"guard_spot_session(uuid)","sha256":"89fb08816a81276039a057a822611dce4e20c54d26f3365b2e946ea826fb7933"},
 {"signature":"guard_quote_admission(uuid,jsonb,jsonb,jsonb,jsonb)","sha256":"5674bd5e68cf3f5bcbe11d23891656be3d619d6b76e74c38aafdc4ab8b972333"},
 {"signature":"spot_prepare_action_core(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb,jsonb)","sha256":"d704afb3948163ebbb3502eab24f69f7fe002883135b35cd24b7b8c35be4c99a"},
 {"signature":"spot_state(uuid)","sha256":"6e8521a2ce1436fb5c33fbca2b4b33bfd82ed5463e11a6f95f76ee68bcc96fd7"},
 {"signature":"quote_birth_verified(uuid)","sha256":"a912a9fdebcea74533aa66204bbf568a626651250736e66e62cc2644c8adc660"},
 {"signature":"quote_birth_prepare(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)","sha256":"fe32089ecce2c96241203d62a015da0d54a11e3e114bcc57e90124c79bd15ccf"},
 {"signature":"quote_birth_issue(uuid,jsonb,jsonb,bigint,jsonb)","sha256":"3c9a0ee8f97fbde2ecdc5c3ac5f72fb91d47a978e8af921bd7be82fa57e33a1f"},
 {"signature":"spot_body(uuid)","sha256":"6419b85807b1e2cf228122ab32f77c5a801c6798c35efc99a82553b26da05f2d"}
]'::jsonb; item jsonb;p pg_proc;definition text;needle text;replacement text;r record;
BEGIN
    FOR item IN SELECT value FROM jsonb_array_elements(expected) LOOP
        SELECT * INTO p FROM pg_proc WHERE oid=to_regprocedure('life_financial.'||(item->>'signature'));
        IF NOT FOUND OR p.prolang<>(SELECT oid FROM pg_language WHERE lanname='plpgsql')
            OR NOT p.prosecdef OR p.proisstrict OR p.proleakproof OR p.proparallel<>'u'
            OR p.proretset OR p.prokind<>'f' OR p.pronargdefaults<>0
            OR p.proowner<>(SELECT nspowner FROM pg_namespace WHERE nspname='life_financial')
            OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
            OR p.provolatile<>(CASE WHEN p.proname IN ('spot_state','spot_body','quote_birth_verified') THEN 's' ELSE 'v' END)
            OR p.prorettype<>(CASE WHEN p.proname LIKE 'guard_%' THEN 'void'::regtype
                WHEN p.proname='quote_birth_verified' THEN 'boolean'::regtype ELSE 'jsonb'::regtype END)
            OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex') IS DISTINCT FROM item->>'sha256' THEN
            RAISE EXCEPTION 'LIFE_BIRTH_VIEW_UNKNOWN_PREDECESSOR';END IF;
    END LOOP;

    -- Preserve the original public/core OID and generic preparation protocol.
    -- This private copy replaces only the fresh-state acquisition; every
    -- original cut, replay, owner, balance, exposure, budget and write stays.
    definition:=pg_get_functiondef('life_financial.spot_prepare_action_core(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb,jsonb)'::regprocedure);
    needle:='CREATE OR REPLACE FUNCTION life_financial.spot_prepare_action_core(p_account uuid, p_binding jsonb, p_owner jsonb, p_version bigint, p_cut life_financial.digest, p_fingerprint life_financial.digest, p_command jsonb, p_context jsonb)';
    replacement:='CREATE FUNCTION life_financial.spot_prepare_birth_from_view(p_account uuid, p_binding jsonb, p_owner jsonb, p_version bigint, p_cut life_financial.digest, p_fingerprint life_financial.digest, p_command jsonb, p_context jsonb, p_native_view jsonb)';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1
        OR (length(definition)-length(replace(definition,'view := life_financial.spot_state(p_account);','')))
            /length('view := life_financial.spot_state(p_account);')<>1 THEN
        RAISE EXCEPTION 'LIFE_BIRTH_VIEW_UNKNOWN_PREDECESSOR';END IF;
    EXECUTE replace(replace(definition,needle,replacement),
        'view := life_financial.spot_state(p_account);','view := p_native_view;');
    FOR r IN SELECT DISTINCT x.grantee FROM pg_proc f
        CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) x
        WHERE f.oid='life_financial.spot_prepare_birth_from_view(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb,jsonb,jsonb)'::regprocedure
            AND x.grantee<>f.proowner LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION life_financial.spot_prepare_birth_from_view(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb,jsonb,jsonb) FROM %s',
            CASE WHEN r.grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(r.grantee)) END);
    END LOOP;

    definition:=pg_get_functiondef('life_financial.quote_birth_prepare(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'::regprocedure);
    needle:='DECLARE r life_financial.quote_births;';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_BIRTH_VIEW_UNKNOWN_PREDECESSOR';END IF;
    definition:=replace(definition,needle,'DECLARE native_view jsonb;r life_financial.quote_births;');
    needle:='IF NOT life_financial.quote_birth_verified(p_account) THEN RAISE EXCEPTION ''LIFE_BIRTH_UNVERIFIED'';END IF;';
    replacement:='native_view:=life_financial.spot_state(p_account);
    IF native_view#>''{quote_births,verified}'' IS DISTINCT FROM ''true''::jsonb THEN RAISE EXCEPTION ''LIFE_BIRTH_UNVERIFIED'';END IF;';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_BIRTH_VIEW_UNKNOWN_PREDECESSOR';END IF;
    definition:=replace(definition,needle,replacement);
    needle:='result:=life_financial.spot_prepare_action_core(';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1
        OR (length(definition)-length(replace(definition,'prep,context);','')))/length('prep,context);')<>1 THEN
        RAISE EXCEPTION 'LIFE_BIRTH_VIEW_UNKNOWN_PREDECESSOR';END IF;
    EXECUTE replace(replace(definition,needle,'result:=life_financial.spot_prepare_birth_from_view('),
        'prep,context);','prep,context,native_view);');

    -- Capture the full preflight BEFORE the one original birth timestamp.
    -- Return an optimistic post-birth digest by projecting only the known
    -- metadata delta. Preparation independently reads and hashes the actual
    -- current state: a wrong/stale proposal is denied, never trusted as proof.
    -- This moves an existing full read out of the quote-age interval; it does
    -- not make total-history work constant or relax any physical deadline.
    definition:=pg_get_functiondef('life_financial.quote_birth_issue(uuid,jsonb,jsonb,bigint,jsonb)'::regprocedure);
    needle:='DECLARE a life_financial.accounts;';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_BIRTH_VIEW_UNKNOWN_PREDECESSOR';END IF;
    definition:=replace(definition,needle,'DECLARE native_view jsonb;a life_financial.accounts;');
    needle:='IF NOT life_financial.quote_birth_verified(p_account) THEN RAISE EXCEPTION ''LIFE_BIRTH_UNVERIFIED'';END IF;';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_BIRTH_VIEW_UNKNOWN_PREDECESSOR';END IF;
    definition:=replace(definition,needle,replacement);
    needle:='RETURN jsonb_build_object(''body'',body,''version'',p_version,''replayed'',false,''trading_permission'',false);';
    replacement:=$projection$native_view:=native_view-'digest'-'observed_at';
    native_view:=jsonb_set(jsonb_set(native_view,'{quote_births,pending}','true'::jsonb),'{quote_births,hashes}',
        COALESCE((SELECT jsonb_agg(body_hash ORDER BY ticket_id) FROM life_financial.quote_births WHERE account_id=p_account),'[]'::jsonb))
        ||jsonb_build_object('recovery_required',true);
    RETURN jsonb_build_object('body',body,'version',p_version,'replayed',false,'trading_permission',false,
        'preparation_cut',jsonb_build_object('version',p_version,'digest',encode(sha256(convert_to(
            'life-persistent-cut-v2:'||((native_view-'recovery_required')#-'{carry,ready}')::text,'UTF8')),'hex')));$projection$;
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_BIRTH_VIEW_UNKNOWN_PREDECESSOR';END IF;
    EXECUTE replace(definition,needle,replacement);
END;
$migration$;
