-- Disposable schema-134 experiment. Derived values are recomputed by PostgreSQL
-- from the CURRENT body on every write, including DISABLE TRIGGER USER writes.
-- No validity, owner, freshness, receipt or permission verdict is stored.
DO $$ DECLARE item jsonb;p pg_proc;BEGIN
    IF (SELECT max(version) FROM life_migrations.schema_migrations)<>134 THEN
        RAISE EXCEPTION 'LIFE_RECORDER_WRITE_UNKNOWN_PREDECESSOR';END IF;
    FOR item IN SELECT value FROM jsonb_array_elements('[
        {"signature":"life_financial.recorder_executor_row(jsonb)","sha256":"72a16c4680f6d0ec8b42ad083733a1c2d3d0eaab5ec41f23f20fa8a9bfffc30c"},
        {"signature":"life_financial.recorder_executor_only_integrity(uuid)","sha256":"c53c2c7e7477f45a89fc71c6d345b5e1cb411719735031c98a80206b06ec41cf"}]'::jsonb) LOOP
        SELECT * INTO p FROM pg_proc WHERE oid=to_regprocedure(item->>'signature');
        IF NOT FOUND OR p.provolatile<>(CASE WHEN p.proname='recorder_executor_row' THEN 'i' ELSE 's' END)
            OR p.prosecdef<>(p.proname<>'recorder_executor_row')
            OR p.proisstrict OR p.proleakproof OR p.proparallel<>'u' OR p.proretset OR p.prokind<>'f'
            OR p.pronargdefaults<>0 OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
            OR p.proowner<>(SELECT nspowner FROM pg_namespace WHERE nspname='life_financial')
            OR p.prolang<>(SELECT oid FROM pg_language WHERE lanname='plpgsql')
            OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')<>item->>'sha256' THEN
            RAISE EXCEPTION 'LIFE_RECORDER_WRITE_UNKNOWN_PREDECESSOR';END IF;
    END LOOP;
END; $$;

-- Preserve original audit refusal for malformed owner-side fault injection:
-- an invalid normalization produces NULL, which the verifier ALWAYS rejects,
-- including a non-head historical delivery. Resource cancellation is not caught.
CREATE FUNCTION life_financial.recorder_payload_value(p jsonb) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
BEGIN
    RETURN life_financial.recorder_executor_row(p);
EXCEPTION WHEN data_exception OR raise_exception THEN RETURN NULL;
END; $$;
REVOKE ALL ON FUNCTION life_financial.recorder_payload_value(jsonb) FROM PUBLIC;
CREATE FUNCTION life_financial.recorder_envelope_hash(p jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
BEGIN
    RETURN encode(sha256(convert_to(p::text,'UTF8')),'hex');
END; $$;
REVOKE ALL ON FUNCTION life_financial.recorder_envelope_hash(jsonb) FROM PUBLIC;

ALTER TABLE life_financial.recorder_outbox
    ADD COLUMN executor_row_value jsonb GENERATED ALWAYS AS (
        CASE WHEN stream LIKE 'executor:%' THEN life_financial.recorder_payload_value(
            (body#>>'{command,payload_json}')::jsonb) END) STORED,
    ADD COLUMN calculated_source_hash text GENERATED ALWAYS AS (
        life_financial.recorder_envelope_hash(body)) STORED;

DO $$ DECLARE definition text;needle text;replacement text;catalog jsonb;BEGIN
    definition:=pg_get_functiondef('life_financial.recorder_executor_only_integrity(uuid)'::regprocedure);
    needle:='parsed.payload,life_financial.recorder_executor_row(parsed.payload) AS target';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_RECORDER_WRITE_UNKNOWN_PREDECESSOR';END IF;
    definition:=replace(definition,needle,'parsed.payload,(parsed.delivery).executor_row_value AS target');
    needle:='delivery:=joined.delivery;retained_op:=joined.retained;';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_RECORDER_WRITE_UNKNOWN_PREDECESSOR';END IF;
    definition:=replace(definition,needle,needle||E'\n        IF joined.target IS NULL THEN RETURN false;END IF;');
    needle:='encode(sha256(convert_to(delivery.body::text,''UTF8'')),''hex'')';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_RECORDER_WRITE_UNKNOWN_PREDECESSOR';END IF;
    definition:=replace(definition,needle,'delivery.calculated_source_hash');

    -- The hot reader refuses changed derivation code/catalogs before using a
    -- stored value. A changed immutable function cannot silently bless old rows.
    SELECT jsonb_object_agg(a.attname,pg_get_expr(d.adbin,d.adrelid)) INTO catalog
        FROM pg_attribute a JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
        WHERE a.attrelid='life_financial.recorder_outbox'::regclass
            AND a.attname IN ('executor_row_value','calculated_source_hash') AND a.attgenerated='s';
    replacement:=format($guard$
    IF (SELECT jsonb_object_agg(a.attname,pg_get_expr(d.adbin,d.adrelid))
        FROM pg_attribute a JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
        WHERE a.attrelid='life_financial.recorder_outbox'::regclass
            AND a.attname IN ('executor_row_value','calculated_source_hash') AND a.attgenerated='s')
            IS DISTINCT FROM %L::jsonb
        OR (SELECT count(*)<>3 OR bool_or(p.provolatile<>'i' OR p.prosecdef OR p.proisstrict
                OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog'] OR p.proretset OR p.prokind<>'f'
                OR p.proowner<>(SELECT nspowner FROM pg_namespace WHERE nspname='life_financial')
                OR p.prolang<>(SELECT oid FROM pg_language WHERE lanname='plpgsql')
                OR p.proleakproof OR p.proparallel<>'u' OR p.pronargdefaults<>0
                OR p.prorettype<>(CASE WHEN p.proname='recorder_envelope_hash' THEN 'text'::regtype ELSE 'jsonb'::regtype END)
                OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')<>(CASE p.proname
                    WHEN 'recorder_executor_row' THEN '72a16c4680f6d0ec8b42ad083733a1c2d3d0eaab5ec41f23f20fa8a9bfffc30c'
                    WHEN 'recorder_envelope_hash' THEN %L
                    ELSE %L END))
            FROM pg_proc p WHERE p.oid IN ('life_financial.recorder_executor_row(jsonb)'::regprocedure,
                'life_financial.recorder_payload_value(jsonb)'::regprocedure,
                'life_financial.recorder_envelope_hash(jsonb)'::regprocedure)) THEN RETURN false;END IF;
$guard$,catalog::text,(SELECT encode(sha256(convert_to(prosrc,'UTF8')),'hex') FROM pg_proc
        WHERE oid='life_financial.recorder_envelope_hash(jsonb)'::regprocedure),
        (SELECT encode(sha256(convert_to(prosrc,'UTF8')),'hex') FROM pg_proc
        WHERE oid='life_financial.recorder_payload_value(jsonb)'::regprocedure));
    definition:=replace(definition,E'BEGIN\n',E'BEGIN\n'||replacement);
    EXECUTE definition;
END; $$;

-- Keep the original provisional-birth branch for the few deliveries without a
-- prepared descriptor. This is a fresh STABLE read, not a stored origin verdict.
CREATE FUNCTION life_financial.recorder_origin_valid(p_account uuid,p_binding jsonb,p jsonb) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    PERFORM life_financial.recorder_executor_origin(p_account,p_binding,p);
    RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
REVOKE ALL ON FUNCTION life_financial.recorder_origin_valid(uuid,jsonb,jsonb) FROM PUBLIC;

-- Evaluate the original per-delivery predicates as one relation. Positive,
-- unique stream sequence constraints plus a required predecessor prove the
-- same contiguous 1..N sequence as the original ordered PL/pgSQL counter.
-- Receipt completeness/applied prefixes below this loop stay unchanged.
DO $$ DECLARE definition text;start_at integer;end_at integer;loop_at integer;constraints jsonb;
    query text;old_loop text;replacement text;BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='life_financial.recorder_outbox'::regclass
            AND conname='recorder_outbox_sequence_check' AND contype='c' AND convalidated
            AND pg_get_expr(conbin,conrelid)='(sequence > 0)')
        OR NOT EXISTS(SELECT 1 FROM pg_constraint c JOIN pg_index i ON i.indexrelid=c.conindid
            WHERE c.conrelid='life_financial.recorder_outbox'::regclass
                AND c.conname='recorder_outbox_account_id_stream_sequence_key' AND c.contype='u'
                AND c.convalidated AND NOT c.condeferrable AND i.indisvalid AND i.indisready
                AND pg_get_constraintdef(c.oid)='UNIQUE (account_id, stream, sequence)') THEN
        RAISE EXCEPTION 'LIFE_RECORDER_WRITE_UNKNOWN_PREDECESSOR';END IF;
    SELECT jsonb_object_agg(c.conname,jsonb_build_array(pg_get_constraintdef(c.oid),c.convalidated,
        c.condeferrable,i.indisvalid,i.indisready)) INTO constraints
        FROM pg_constraint c LEFT JOIN pg_index i ON i.indexrelid=c.conindid
        WHERE c.conrelid='life_financial.recorder_outbox'::regclass
            AND c.conname IN ('recorder_outbox_sequence_check','recorder_outbox_account_id_stream_sequence_key');
    definition:=pg_get_functiondef('life_financial.recorder_executor_only_integrity(uuid)'::regprocedure);
    definition:=replace(definition,E'BEGIN\n',E'BEGIN\n'||format($guard$
    IF (SELECT jsonb_object_agg(c.conname,jsonb_build_array(pg_get_constraintdef(c.oid),c.convalidated,
            c.condeferrable,i.indisvalid,i.indisready))
        FROM pg_constraint c LEFT JOIN pg_index i ON i.indexrelid=c.conindid
        WHERE c.conrelid='life_financial.recorder_outbox'::regclass
            AND c.conname IN ('recorder_outbox_sequence_check','recorder_outbox_account_id_stream_sequence_key'))
        IS DISTINCT FROM %L::jsonb THEN RETURN false;END IF;
$guard$,constraints::text));
    start_at:=strpos(definition,'    FOR joined IN');
    end_at:=strpos(definition,'    IF EXISTS(SELECT 1 FROM life_financial.operations op');
    IF start_at=0 OR end_at<=start_at THEN RAISE EXCEPTION 'LIFE_RECORDER_WRITE_UNKNOWN_PREDECESSOR';END IF;
    old_loop:=substr(definition,start_at,end_at-start_at);
    loop_at:=strpos(old_loop,E' LOOP\n');
    IF loop_at=0 THEN RAISE EXCEPTION 'LIFE_RECORDER_WRITE_UNKNOWN_PREDECESSOR';END IF;
    query:=substr(old_loop,length('    FOR joined IN')+1,loop_at-length('    FOR joined IN')-1);
    replacement:='    IF EXISTS(WITH checked AS MATERIALIZED ('||query||$predicates$
    ) SELECT 1 FROM checked j WHERE j.target IS NULL
        OR (j.delivery).body->>'subtype' NOT IN ('INITIAL','PROGRESS')
        OR CASE WHEN j.descriptor_verified THEN j.origin_invalid ELSE
            NOT life_financial.recorder_origin_valid(p_account,(j.delivery).body->'binding',j.payload) END
        OR j.is_applied_head AND j.actual IS DISTINCT FROM j.target
        OR (j.retained).account_id IS NULL
        OR (j.retained).command IS DISTINCT FROM jsonb_build_object('kind','RECORDER_STAGE',
            'subtype',(j.delivery).body->'subtype','command',(j.delivery).body->'command',
            'binding',(j.delivery).body->'binding','owner',(j.delivery).body->'owner',
            'cut',(j.delivery).body->'cut','version',(j.delivery).body->'version')
        OR (j.delivery).projection_id IS DISTINCT FROM (j.delivery).body#>>'{command,operation_id}'
        OR (j.delivery).body#>'{command,binding}' IS DISTINCT FROM (j.delivery).body->'binding'
        OR (j.retained).committed_version<>((j.delivery).body->>'version')::bigint+1
        OR (j.delivery).source_hash IS DISTINCT FROM (j.delivery).calculated_source_hash
        OR (j.delivery).stream IS DISTINCT FROM 'executor:'||encode(sha256(convert_to(j.payload->>'id','UTF8')),'hex')
        OR (j.retained).receipt IS DISTINCT FROM jsonb_build_object('kind','RECORDER_STAGE',
            'projection_id',(j.delivery).projection_id,'executor_id',j.payload->>'id',
            'sequence',(j.delivery).sequence-1,'state','STAGED','version',(j.retained).committed_version,
            'replayed',false,'trading_permission',false)
        OR (j.delivery).sequence=1 AND ((j.delivery).body->>'subtype'<>'INITIAL'
            OR j.payload->>'status'<>'1' OR (j.payload->>'filled_amount_quote')::numeric<>0
            OR j.payload#>'{custom_info,recovery_order_ids}'<>'[]'::jsonb)
        OR (j.delivery).sequence>1 AND (NOT j.has_previous
            OR (j.delivery).body->>'subtype'<>'PROGRESS'
            OR ((j.delivery).body#>>'{command,expected_sequence}')::bigint<>(j.delivery).sequence-2
            OR ((j.prior_payload::jsonb)-ARRAY['close_type','close_timestamp','status','net_pnl_pct','net_pnl_quote',
                'cum_fees_quote','filled_amount_quote','is_active','is_trading','custom_info']) IS DISTINCT FROM
                (j.payload-ARRAY['close_type','close_timestamp','status','net_pnl_pct','net_pnl_quote',
                'cum_fees_quote','filled_amount_quote','is_active','is_trading','custom_info'])
            OR (j.payload->>'status')::integer<(j.prior_payload::jsonb->>'status')::integer
            OR (j.payload->>'filled_amount_quote')::numeric<(j.prior_payload::jsonb->>'filled_amount_quote')::numeric
            OR NOT (j.payload#>'{custom_info,recovery_order_ids}') @> (j.prior_payload::jsonb#>'{custom_info,recovery_order_ids}'))
    ) THEN RETURN false;END IF;
$predicates$;
    EXECUTE replace(definition,old_loop,replacement);
END; $$;
