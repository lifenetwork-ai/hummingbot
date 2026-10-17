-- Decoded private GET response diagnostics. These are untrusted observations,
-- not HTTP wire bytes, financial facts, completeness or trading permission.
CREATE TABLE life_financial.spot_provider_heads (
    account_id uuid NOT NULL REFERENCES life_financial.accounts,
    collection_id life_financial.identity NOT NULL,
    page_count integer NOT NULL CHECK(page_count BETWEEN 1 AND 512),
    body_bytes bigint NOT NULL CHECK(body_bytes BETWEEN 1 AND 16777216),
    tip life_financial.digest NOT NULL,
    PRIMARY KEY(account_id,collection_id)
);
CREATE TABLE life_financial.spot_provider_pages (
    account_id uuid NOT NULL, collection_id life_financial.identity NOT NULL,
    sequence integer NOT NULL CHECK(sequence BETWEEN 1 AND 512),
    body jsonb NOT NULL CHECK(jsonb_typeof(body)='object' AND octet_length(body::text)<=16777216),
    captured_at timestamptz NOT NULL CHECK(isfinite(captured_at)),
    previous_hash life_financial.digest NOT NULL, source_hash life_financial.digest NOT NULL,
    PRIMARY KEY(account_id,collection_id,sequence),
    FOREIGN KEY(account_id,collection_id) REFERENCES life_financial.spot_provider_heads
);
DO $$ DECLARE name text; BEGIN
    FOREACH name IN ARRAY ARRAY['spot_provider_heads','spot_provider_pages'] LOOP
        EXECUTE format('ALTER TABLE life_financial.%I ENABLE ROW LEVEL SECURITY',name);
        EXECUTE format('CREATE POLICY scoped_read ON life_financial.%I FOR SELECT USING(life_financial.can_access(account_id))',name);
    END LOOP;
END; $$;
CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON life_financial.spot_provider_pages
    FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record();
CREATE TRIGGER retained_identity BEFORE DELETE ON life_financial.spot_provider_heads
    FOR EACH ROW EXECUTE FUNCTION life_financial.immutable_record();
CREATE FUNCTION life_financial.spot_provider_head_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
    IF NEW.account_id<>OLD.account_id OR NEW.collection_id<>OLD.collection_id
        OR NEW.page_count<>OLD.page_count+1 OR NEW.body_bytes<=OLD.body_bytes OR NEW.tip=OLD.tip THEN
        RAISE EXCEPTION 'LIFE_PROVIDER_JOURNAL_HEAD_INVALID' USING ERRCODE='P0001'; END IF;
    RETURN NEW;
END; $$;
CREATE TRIGGER monotone_head BEFORE UPDATE ON life_financial.spot_provider_heads
    FOR EACH ROW EXECUTE FUNCTION life_financial.spot_provider_head_guard();

CREATE FUNCTION life_financial.spot_provider_route(p_path text,p_params jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE base jsonb; extra jsonb;
BEGIN
    IF jsonb_typeof(p_params) IS DISTINCT FROM 'object' OR EXISTS(
        SELECT 1 FROM jsonb_each(p_params) x WHERE jsonb_typeof(x.value)<>'string') THEN RETURN false; END IF;
    base:=CASE p_path
        WHEN '/api/v5/account/bills-archive' THEN '{"limit":"100"}'::jsonb
        WHEN '/api/v5/trade/fills-history' THEN '{"instType":"SPOT","limit":"100"}'::jsonb
        WHEN '/api/v5/trade/orders-pending' THEN '{"instType":"SPOT","limit":"100"}'::jsonb
        WHEN '/api/v5/trade/orders-algo-pending' THEN '{"instType":"SPOT","limit":"100"}'::jsonb
        WHEN '/api/v5/trade/order' THEN '{"instId":"LIFE-USDT"}'::jsonb
        WHEN '/api/v5/account/balance' THEN '{}'::jsonb ELSE NULL END;
    IF base IS NULL OR NOT p_params @> base THEN RETURN false; END IF;
    SELECT COALESCE(jsonb_object_agg(key,value),'{}'::jsonb) INTO extra FROM jsonb_each(p_params)
        WHERE NOT base ? key;
    IF p_path IN ('/api/v5/account/bills-archive','/api/v5/trade/fills-history','/api/v5/trade/orders-pending') THEN
        IF extra ? 'after' THEN
            IF extra->>'after' !~ '^[0-9]{1,96}$' THEN RETURN false; END IF;
            extra:=extra-'after';
        END IF;
        IF p_path='/api/v5/trade/fills-history' AND extra ?& ARRAY['instId','ordId']
            AND (SELECT count(*) FROM jsonb_object_keys(extra))=2 THEN
            IF extra->>'instId'<>'LIFE-USDT' THEN RETURN false; END IF;
            PERFORM (extra->>'ordId')::life_financial.identity;
            extra:='{}'::jsonb;
        END IF;
    ELSIF p_path='/api/v5/trade/order' THEN
        IF (SELECT count(*) FROM jsonb_object_keys(extra))<>1 OR NOT extra ? 'clOrdId' THEN RETURN false; END IF;
        PERFORM (extra->>'clOrdId')::life_financial.identity;
        extra:='{}'::jsonb;
    ELSIF p_path='/api/v5/trade/orders-algo-pending' THEN
        IF (SELECT count(*) FROM jsonb_object_keys(extra))<>1 OR NOT extra ? 'ordType'
            OR extra->>'ordType' NOT IN ('conditional,oco','trigger','move_order_stop','iceberg','twap','smart_iceberg') THEN RETURN false; END IF;
        extra:='{}'::jsonb;
    END IF;
    RETURN extra='{}'::jsonb;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;

CREATE FUNCTION life_financial.spot_provider_page_hash(p_body jsonb,p_at timestamptz,p_previous text) RETURNS text
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
    SELECT encode(sha256(convert_to(jsonb_build_object('body',p_body,'captured_at_epoch',extract(epoch FROM p_at),
        'previous_hash',p_previous)::text,'UTF8')),'hex')
$$;

CREATE FUNCTION life_financial.spot_provider_receipt(p_account uuid,p_collection text,p_sequence integer,p_replay boolean) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT jsonb_build_object('kind','PROVIDER_DECODED_PAGE','body',body,'captured_at',captured_at,
        'source_hash',source_hash,'replayed',p_replay,'trading_permission',false)
    FROM life_financial.spot_provider_pages WHERE account_id=p_account AND collection_id=p_collection AND sequence=p_sequence
$$;

CREATE FUNCTION life_financial.spot_capture_provider_page(p_account uuid,p_body jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a life_financial.accounts; h life_financial.spot_provider_heads; r life_financial.spot_provider_pages;
    at timestamptz; id text; n integer; bytes bigint; new_tip text; previous text; actual jsonb;
BEGIN
    IF NOT life_financial.can_access(p_account,true) OR pg_is_in_recovery()
        OR current_setting('transaction_read_only')<>'off' OR current_setting('synchronous_commit')<>'on'
        OR current_setting('fsync')<>'on' OR current_setting('full_page_writes')<>'on' THEN
        RAISE EXCEPTION 'LIFE_PRIMARY_DURABILITY_OR_SCOPE_INVALID' USING ERRCODE='P0001'; END IF;
    IF jsonb_typeof(p_body) IS DISTINCT FROM 'object'
        OR NOT p_body ?& ARRAY['binding','owner','collection_id','sequence','path','params','response_json']
        OR (SELECT count(*) FROM jsonb_object_keys(p_body))<>7
        OR jsonb_typeof(p_body->'binding') IS DISTINCT FROM 'object' OR jsonb_typeof(p_body->'owner') IS DISTINCT FROM 'object'
        OR jsonb_typeof(p_body->'collection_id') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_body->'sequence') IS DISTINCT FROM 'number' OR p_body->>'sequence' !~ '^[1-9][0-9]{0,2}$'
        OR jsonb_typeof(p_body->'path') IS DISTINCT FROM 'string'
        OR jsonb_typeof(p_body->'response_json') IS DISTINCT FROM 'string'
        OR NOT life_financial.spot_provider_route(p_body->>'path',p_body->'params') THEN
        RAISE EXCEPTION 'LIFE_PROVIDER_JOURNAL_CAPTURE_INVALID' USING ERRCODE='P0001'; END IF;
    id:=(p_body->>'collection_id')::life_financial.identity; n:=(p_body->>'sequence')::integer;
    bytes:=octet_length(p_body::text);
    IF n>512 OR bytes>16777216 THEN RAISE EXCEPTION 'LIFE_PROVIDER_JOURNAL_CAPTURE_BOUND' USING ERRCODE='P0001'; END IF;
    -- Parseability is structural only; code/data/amount semantics are not trusted.
    PERFORM (p_body->>'response_json')::jsonb;
    SELECT * INTO STRICT a FROM life_financial.accounts WHERE account_id=p_account FOR UPDATE;
    actual:=jsonb_build_object('environment',a.environment,'exchange',a.exchange,'account_uid',a.account_uid,
        'risk_pool',a.risk_pool,'lineage',a.lineage,'schema_version',a.schema_version,'policy_hash',a.policy_hash,'config_hash',a.config_hash);
    IF p_body->'binding'<>actual OR (SELECT count(*) FROM jsonb_object_keys(p_body->'owner'))<>6
        OR NOT (p_body->'owner') ?& ARRAY['process_id','generation','epoch','session_id','valid_from','expires_at']
        OR EXISTS(SELECT 1 FROM jsonb_each(p_body->'owner') x WHERE (x.key='epoch' AND (jsonb_typeof(x.value)<>'number' OR x.value::text !~ '^[1-9][0-9]*$'))
            OR (x.key<>'epoch' AND jsonb_typeof(x.value)<>'string')) THEN
        RAISE EXCEPTION 'LIFE_PROVIDER_JOURNAL_BINDING_INVALID' USING ERRCODE='P0001'; END IF;
    -- Owner is retained provenance, deliberately not a financial/activation grant.
    -- Late responses from an expired original request must still be diagnosable.
    PERFORM (p_body#>>'{owner,process_id}')::uuid; PERFORM (p_body#>>'{owner,generation}')::uuid;
    PERFORM (p_body#>>'{owner,session_id}')::uuid; PERFORM (p_body#>>'{owner,epoch}')::bigint;
    PERFORM (p_body#>>'{owner,valid_from}')::timestamptz; PERFORM (p_body#>>'{owner,expires_at}')::timestamptz;
    SELECT * INTO h FROM life_financial.spot_provider_heads WHERE account_id=p_account AND collection_id=id;
    IF FOUND THEN
        SELECT * INTO r FROM life_financial.spot_provider_pages WHERE account_id=p_account AND collection_id=id AND sequence=n;
        IF FOUND THEN
            IF r.body IS DISTINCT FROM p_body OR r.source_hash IS DISTINCT FROM life_financial.spot_provider_page_hash(r.body,r.captured_at,r.previous_hash) THEN
                RAISE EXCEPTION 'LIFE_PROVIDER_JOURNAL_IDENTITY_CONFLICT' USING ERRCODE='P0001'; END IF;
            RETURN life_financial.spot_provider_receipt(p_account,id,n,true);
        END IF;
        IF n<>h.page_count+1 OR NOT EXISTS(SELECT 1 FROM life_financial.spot_provider_pages last
            WHERE last.account_id=p_account AND last.collection_id=id AND last.sequence=h.page_count AND last.source_hash=h.tip
                AND last.body->'binding'=p_body->'binding' AND last.body->'owner'=p_body->'owner') THEN
            RAISE EXCEPTION 'LIFE_PROVIDER_JOURNAL_SEQUENCE_CONFLICT' USING ERRCODE='P0001'; END IF;
        IF h.body_bytes+bytes>16777216 THEN RAISE EXCEPTION 'LIFE_PROVIDER_JOURNAL_CAPTURE_BOUND' USING ERRCODE='P0001'; END IF;
        previous:=h.tip;
    ELSE
        IF n<>1 THEN RAISE EXCEPTION 'LIFE_PROVIDER_JOURNAL_SEQUENCE_CONFLICT' USING ERRCODE='P0001'; END IF;
        previous:=repeat('0',64);
    END IF;
    at:=clock_timestamp(); new_tip:=life_financial.spot_provider_page_hash(p_body,at,previous);
    IF n=1 THEN
        INSERT INTO life_financial.spot_provider_heads VALUES(p_account,id,n,bytes,new_tip);
    ELSE
        UPDATE life_financial.spot_provider_heads SET page_count=n,body_bytes=body_bytes+bytes,tip=new_tip
            WHERE account_id=p_account AND collection_id=id;
    END IF;
    INSERT INTO life_financial.spot_provider_pages VALUES(p_account,id,n,p_body,at,previous,new_tip);
    RETURN life_financial.spot_provider_receipt(p_account,id,n,false);
END; $$;

CREATE FUNCTION life_financial.spot_read_provider_collection(p_account uuid,p_collection life_financial.identity,p_rows integer,p_bytes integer) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE h life_financial.spot_provider_heads; r life_financial.spot_provider_pages; n integer:=0;
    bytes bigint:=0; previous text:=repeat('0',64); result jsonb:='[]'::jsonb; original jsonb;
BEGIN
    IF NOT life_financial.can_access(p_account) THEN RAISE EXCEPTION 'LIFE_ACCOUNT_SCOPE_INVALID' USING ERRCODE='P0001'; END IF;
    IF p_rows IS NULL OR p_rows NOT BETWEEN 1 AND 512 OR p_bytes IS NULL OR p_bytes NOT BETWEEN 1 AND 16777216 THEN
        RAISE EXCEPTION 'LIFE_PROVIDER_JOURNAL_READ_BOUND_INVALID' USING ERRCODE='P0001'; END IF;
    SELECT * INTO h FROM life_financial.spot_provider_heads WHERE account_id=p_account AND collection_id=p_collection;
    IF NOT FOUND THEN
        IF EXISTS(SELECT 1 FROM life_financial.spot_provider_pages WHERE account_id=p_account AND collection_id=p_collection) THEN
            RAISE EXCEPTION 'LIFE_PROVIDER_JOURNAL_UNVERIFIED' USING ERRCODE='P0001'; END IF;
        RETURN result;
    END IF;
    IF h.page_count>p_rows OR h.body_bytes>p_bytes OR (SELECT count(*) FROM (
        SELECT 1 FROM life_financial.spot_provider_pages WHERE account_id=p_account AND collection_id=p_collection LIMIT p_rows+1) x)>p_rows THEN
        RAISE EXCEPTION 'LIFE_PROVIDER_JOURNAL_READ_BOUND' USING ERRCODE='P0001'; END IF;
    IF (SELECT COALESCE(sum(octet_length(body::text)),0) FROM life_financial.spot_provider_pages
        WHERE account_id=p_account AND collection_id=p_collection)>p_bytes THEN
        RAISE EXCEPTION 'LIFE_PROVIDER_JOURNAL_READ_BOUND' USING ERRCODE='P0001'; END IF;
    FOR r IN SELECT * FROM life_financial.spot_provider_pages WHERE account_id=p_account AND collection_id=p_collection ORDER BY sequence LOOP
        n:=n+1; bytes:=bytes+octet_length(r.body::text);
        IF original IS NULL THEN original:=r.body; END IF;
        IF r.sequence<>n OR r.body->>'collection_id' IS DISTINCT FROM p_collection
            OR (r.body->>'sequence')::integer IS DISTINCT FROM n OR r.body->'binding' IS DISTINCT FROM original->'binding'
            OR r.body->'owner' IS DISTINCT FROM original->'owner' OR r.previous_hash<>previous
            OR r.source_hash IS DISTINCT FROM life_financial.spot_provider_page_hash(r.body,r.captured_at,r.previous_hash) THEN
            RAISE EXCEPTION 'LIFE_PROVIDER_JOURNAL_UNVERIFIED' USING ERRCODE='P0001'; END IF;
        previous:=r.source_hash;
    END LOOP;
    IF n<>h.page_count OR bytes<>h.body_bytes OR previous<>h.tip THEN
        RAISE EXCEPTION 'LIFE_PROVIDER_JOURNAL_UNVERIFIED' USING ERRCODE='P0001'; END IF;
    SELECT COALESCE(jsonb_agg(jsonb_build_object('kind','PROVIDER_DECODED_PAGE','body',body,'captured_at',captured_at,
        'source_hash',source_hash,'replayed',true,'trading_permission',false) ORDER BY sequence),'[]'::jsonb)
        INTO result FROM life_financial.spot_provider_pages WHERE account_id=p_account AND collection_id=p_collection;
    IF octet_length(result::text)>p_bytes THEN RAISE EXCEPTION 'LIFE_PROVIDER_JOURNAL_READ_BOUND' USING ERRCODE='P0001'; END IF;
    RETURN result;
END; $$;
REVOKE ALL ON life_financial.spot_provider_heads,life_financial.spot_provider_pages FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
