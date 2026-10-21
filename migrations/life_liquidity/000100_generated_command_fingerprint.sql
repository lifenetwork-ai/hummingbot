-- Store only a deterministic canonical-v1 command digest, never a permission.
-- PostgreSQL recomputes this GENERATED value on INSERT or command UPDATE.
-- Every verifier still compares it with the retained ORIGINAL fingerprint;
-- all source/owner/version/policy/economic/COMMIT checks remain independent.
DO $migration$
DECLARE expected jsonb:='[{"signature":"life_financial.carry_check_verified(uuid,text)","sha256":"3c6be0c930b74756bb10d20fd46c82494f27514bcb1b187e6dbd2c5c869266d5","aliases":{"op":1}},{"signature":"life_financial.cashflow_history_verified(uuid)","sha256":"422b527dac6b85ddc31c17e25ca8770ea1047e868a908733925f3f95a9e58fbc","aliases":{"o":2}},{"signature":"life_financial.hedge_verified(uuid)","sha256":"f71632694074f6da8557db77425a60e97d7a9bbe583d848048cb5222d3201edb","aliases":{"o":3}},{"signature":"life_financial.joint_batch_verified(uuid)","sha256":"d4b908917e86bb505535dbbf6ad65cb4fc0d87f64f824d36f79cb548d741e360","aliases":{"child":1,"o":1}},{"signature":"life_financial.joint_repair_verified(uuid)","sha256":"0f301358cbe9ebd4dc7f66b43b61536e03622fedd28cb277feef73682884d3c3","aliases":{"o":1}},{"signature":"life_financial.portfolio_after_carry_verification(uuid,boolean)","sha256":"c419f3a303bafd5aab793432e3a6cfaf48bdbb1bde9534d78d478a939a3d10f0","aliases":{"o":1}},{"signature":"life_financial.portfolio_after_financial_verification(uuid,boolean,boolean,boolean)","sha256":"9f7e5cc3a64c526f6921edfb44aa17d701808cbe948d8690727c17a95174165d","aliases":{"o":1}},{"signature":"life_financial.portfolio_after_snapshot_verification(uuid,boolean,boolean)","sha256":"7db3a255289f69dbc9fe132eaaa4f07a93bfdfadd6b23e85012bef029abde71c","aliases":{"o":1}},{"signature":"life_financial.portfolio_verified(uuid)","sha256":"0029fb012806fc1b5128e863d6ebf1bb365f23e383a70a0165dce89c2ad6de27","aliases":{"o":1}},{"signature":"life_financial.shared_capital_verified(uuid)","sha256":"23705077bbe8105dafbafc9ff8985d22648b623ff3b46b0bdca30d5cc1bf74a3","aliases":{"op":2,"origin":1}},{"signature":"life_financial.shared_operation_verified(uuid,text)","sha256":"86a667c442c1f21ebaab3fee6017f5197da82b73a9f56e9f2f465bd15aeac606","aliases":{"op":1}},{"signature":"life_financial.shared_release_verified(uuid)","sha256":"a1289a5444648a375e80f87b97aaa4b839a893b7463e25354b37ae9da0aa0abc","aliases":{"o":1}},{"signature":"life_financial.spot_markouts_verified(uuid)","sha256":"299b40fc7d5790d17e030cea6e6153f3ff7bd57c8ac68e695b6b9988fed8570f","aliases":{"op":1}},{"signature":"life_financial.spot_pending_verified(uuid)","sha256":"586ba8c76c4c5085ab216b28043611e722983f58747106c1e43029ef8cea7371","aliases":{"o":1}},{"signature":"life_financial.swap_accounting_verified(uuid)","sha256":"0741577729f7a36dcf6dcd47c152511edbf7b5450fe47c8c55c900fef09df835","aliases":{"o":1}},{"signature":"life_financial.swap_boundary_verified(uuid)","sha256":"a2a84e3fd2c862a4c0f3989ee8fe290a5bd456db3678841bc0ec2fa2934eafbe","aliases":{"o":1}},{"signature":"life_financial.swap_terminal_verified(uuid)","sha256":"995aaedcf907b536892d34e1a5f7d630554c3c690dd59e2edd42258e858f4865","aliases":{"op":1}},{"signature":"life_financial.swap_verified_before_boundary(uuid)","sha256":"61a27002c088c3da626aad67355e2901531d53e736366e13edd599d3d451b956","aliases":{"o":2}},{"signature":"life_financial.terminal_repair_verified(uuid)","sha256":"e66226e160c4695806688c1e80b73f25d860fb513f695f5a36b0cee36b9c4a39","aliases":{"o":1}}]'::jsonb;item jsonb;part record;p record;
    definition text;needle text;replacement text;seen integer;r record;
BEGIN
    SELECT f.*,l.lanname INTO STRICT p FROM pg_proc f JOIN pg_language l ON l.oid=f.prolang
        WHERE f.oid='life_financial.canonical_json_v1(jsonb)'::regprocedure;
    IF p.lanname<>'plpgsql' OR p.provolatile<>'i' OR p.prosecdef OR NOT p.proisstrict
        OR p.proleakproof OR p.proparallel<>'u' OR p.proretset OR p.prokind<>'f'
        OR p.prorettype<>'text'::regtype OR p.pronargdefaults<>0
        OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
        OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')<>'1ed0433191b81f7b4f54b6bab26c585bd0802cbec96335dd7afdf7238fd3eb7a' THEN
        RAISE EXCEPTION 'LIFE_COMMAND_HASH_MIGRATION_MISMATCH';END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(expected) LOOP
        SELECT f.*,l.lanname INTO p FROM pg_proc f JOIN pg_language l ON l.oid=f.prolang
            WHERE f.oid=to_regprocedure(item->>'signature');
        IF NOT FOUND OR p.lanname<>'plpgsql' OR p.provolatile<>'s' OR NOT p.prosecdef
            OR p.proisstrict OR p.proleakproof OR p.proparallel<>'u' OR p.proretset OR p.prokind<>'f'
            OR p.prorettype<>'boolean'::regtype OR p.pronargdefaults<>0
            OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
            OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')<>item->>'sha256' THEN
            RAISE EXCEPTION 'LIFE_COMMAND_HASH_MIGRATION_MISMATCH';END IF;
    END LOOP;
    EXECUTE $ddl$CREATE FUNCTION life_financial.command_fingerprint_v1(p_command jsonb) RETURNS life_financial.digest
        LANGUAGE plpgsql IMMUTABLE STRICT SET search_path=pg_catalog AS $body$
        BEGIN RETURN encode(sha256(convert_to(life_financial.canonical_json_v1(p_command),'UTF8')),'hex'); END;
        $body$;$ddl$;
    -- Hostile deployment defaults must not expose this private write-time helper.
    FOR r IN SELECT DISTINCT a.grantee FROM pg_proc f
        CROSS JOIN LATERAL aclexplode(COALESCE(f.proacl,acldefault('f',f.proowner))) a
        WHERE f.oid='life_financial.command_fingerprint_v1(jsonb)'::regprocedure AND a.grantee<>f.proowner LOOP
        IF r.grantee=0 THEN
            REVOKE ALL ON FUNCTION life_financial.command_fingerprint_v1(jsonb) FROM PUBLIC;
        ELSE
            EXECUTE format('REVOKE ALL ON FUNCTION life_financial.command_fingerprint_v1(jsonb) FROM %I',pg_get_userbyid(r.grantee));
        END IF;
    END LOOP;
    ALTER TABLE life_financial.operations ADD COLUMN canonical_fingerprint life_financial.digest
        GENERATED ALWAYS AS (life_financial.command_fingerprint_v1(command)) STORED;
    FOR item IN SELECT value FROM jsonb_array_elements(expected) LOOP
        definition:=pg_get_functiondef(to_regprocedure(item->>'signature'));
        FOR part IN SELECT key,value FROM jsonb_each_text(item->'aliases') LOOP
            needle:=format('encode(sha256(convert_to(life_financial.canonical_json_v1(%s.command),''UTF8'')),''hex'')',part.key);
            seen:=(length(definition)-length(replace(definition,needle,'')))/length(needle);
            IF seen<>part.value::integer THEN RAISE EXCEPTION 'LIFE_COMMAND_HASH_MIGRATION_MISMATCH';END IF;
            replacement:=part.key||'.canonical_fingerprint';
            definition:=replace(definition,needle,replacement);
        END LOOP;
        -- CREATE OR REPLACE retains existing OIDs, security attributes and ACLs.
        EXECUTE definition;
    END LOOP;
END;
$migration$;
