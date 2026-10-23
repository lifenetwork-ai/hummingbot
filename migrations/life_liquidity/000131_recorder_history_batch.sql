-- Read native recorder origins and applied heads in the original STABLE cut.
-- Retain structural validation for EVERY delivery, original provisional-birth
-- fallback, all receipt/sequence checks, and fresh post-write COMMIT proofs.
DO $$ DECLARE p record;expected jsonb:= '[{"signature": "life_financial.recorder_executor_row(jsonb)", "sha256": "72a16c4680f6d0ec8b42ad083733a1c2d3d0eaab5ec41f23f20fa8a9bfffc30c"}, {"signature": "life_financial.recorder_executor_origin(uuid,jsonb,jsonb)", "sha256": "ce1ca9bc144101d8ec1adc8158113e583a0093159464e623d10649fe599d66f4"}, {"signature": "life_financial.recorder_executor_transition(jsonb,jsonb)", "sha256": "fa0074daffde21b6c221b804edd1b90734e761422f8ab8d307560a9f9162be6b"}, {"signature": "life_financial.recorder_executor_only_integrity(uuid)", "sha256": "eae7692555a1139e726ea73019176b76fbdeb85bb5088c74910c20d28040ade7"}]'::jsonb;item jsonb;BEGIN
    FOR item IN SELECT value FROM jsonb_array_elements(expected) LOOP
        SELECT f.*,l.lanname INTO p FROM pg_proc f JOIN pg_language l ON l.oid=f.prolang
            WHERE f.oid=to_regprocedure(item->>'signature');
        IF NOT FOUND OR p.lanname<>'plpgsql' OR p.proisstrict OR p.proleakproof OR p.proparallel<>'u'
            OR p.proretset OR p.prokind<>'f' OR p.pronargdefaults<>0
            OR p.proowner<>(SELECT nspowner FROM pg_namespace WHERE nspname='life_financial')
            OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
            OR p.prosecdef<>(p.proname IN ('recorder_executor_only_integrity','recorder_executor_origin'))
            OR p.provolatile<>(CASE WHEN p.proname IN ('recorder_executor_row','recorder_executor_transition') THEN 'i' ELSE 's' END)
            OR p.prorettype<>(CASE p.proname WHEN 'recorder_executor_only_integrity' THEN 'boolean'::regtype
                WHEN 'recorder_executor_row' THEN 'jsonb'::regtype ELSE 'void'::regtype END)
            OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')<>item->>'sha256' THEN
            RAISE EXCEPTION 'LIFE_RECORDER_BATCH_UNKNOWN_PREDECESSOR';END IF;
    END LOOP;
END; $$;
DO $$ DECLARE definition text;needle text;replacement text;BEGIN
    definition:=pg_get_functiondef('life_financial.recorder_executor_only_integrity(uuid)'::regprocedure);
    needle:=$old$FOR joined IN SELECT o AS delivery,op AS retained,previous.account_id IS NOT NULL AS has_previous,
        previous.body#>>'{command,payload_json}' AS prior_payload FROM life_financial.recorder_outbox o
        LEFT JOIN life_financial.operations op ON op.account_id=o.account_id AND op.operation_id=o.projection_id
        LEFT JOIN life_financial.recorder_outbox previous ON previous.account_id=o.account_id
            AND previous.stream=o.stream AND previous.sequence=o.sequence-1
        WHERE o.account_id=p_account AND o.stream LIKE 'executor:%' ORDER BY o.stream,o.sequence LOOP$old$;
    replacement:=$new$FOR joined IN
        WITH parsed AS MATERIALIZED (
            SELECT o AS delivery,(o.body#>>'{command,payload_json}')::jsonb AS payload
                FROM life_financial.recorder_outbox o WHERE o.account_id=p_account AND o.stream LIKE 'executor:%'
        ), normalized AS NOT MATERIALIZED (
            SELECT parsed.delivery,parsed.payload,life_financial.recorder_executor_row(parsed.payload) AS target FROM parsed
        ), applied_heads AS MATERIALIZED (
            SELECT DISTINCT ON(o.stream) o.stream,o.projection_id FROM life_financial.recorder_outbox o
                JOIN life_financial.projection_receipts r USING(account_id,projection_id)
                WHERE o.account_id=p_account AND o.stream LIKE 'executor:%' ORDER BY o.stream,o.sequence DESC
        )
        SELECT n.delivery,op AS retained,previous.account_id IS NOT NULL AS has_previous,
            previous.body#>>'{command,payload_json}' AS prior_payload,n.payload,n.target,
            d.account_id IS NOT NULL AS descriptor_verified,h.projection_id IS NOT NULL AS is_applied_head,
            to_jsonb(e) AS actual,
            n.payload#>'{custom_info,life_storage_binding}' IS DISTINCT FROM (n.delivery).body->'binding'
                OR n.payload#>>'{custom_info,recovery_account_uid}' IS DISTINCT FROM (n.delivery).body#>>'{binding,account_uid}'
                OR CASE WHEN jsonb_typeof(n.payload#>'{custom_info,recovery_order_ids}')='array' THEN (
                    SELECT count(*)>4096 OR count(*)<>count(DISTINCT w.value)
                        OR COALESCE(bool_or(jsonb_typeof(w.value)<>'string' OR t.account_id IS NULL),false)
                    FROM jsonb_array_elements(n.payload#>'{custom_info,recovery_order_ids}') w(value)
                    LEFT JOIN life_financial.send_attempts t ON t.account_id=p_account AND t.intent_id=n.payload->>'id'
                        AND t.wire_id=w.value#>>'{}' AND t.product='SPOT') ELSE true END AS origin_invalid
        FROM normalized n
        LEFT JOIN life_financial.operations op ON op.account_id=p_account AND op.operation_id=(n.delivery).projection_id
        LEFT JOIN life_financial.recorder_outbox previous ON previous.account_id=p_account
            AND previous.stream=(n.delivery).stream AND previous.sequence=(n.delivery).sequence-1
        LEFT JOIN life_financial.spot_action_descriptors d ON d.account_id=p_account AND d.action_id=n.payload->>'id'
            AND d.descriptor->>'controller_id'=n.payload->>'controller_id'
            AND (d.descriptor->>'payload_json')::jsonb=n.payload->'config'
        LEFT JOIN applied_heads h ON h.projection_id=(n.delivery).projection_id
        LEFT JOIN life_recorder."Executors" e ON h.projection_id IS NOT NULL AND e.id=n.payload->>'id'
        ORDER BY (n.delivery).stream,(n.delivery).sequence LOOP$new$;
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_RECORDER_BATCH_UNKNOWN_PREDECESSOR';END IF;
    definition:=replace(definition,needle,replacement);
    needle:=$old$p:=(delivery.body#>>'{command,payload_json}')::jsonb;
        PERFORM life_financial.recorder_executor_origin(p_account,delivery.body->'binding',p);$old$;
    replacement:=$new$p:=joined.payload;
        IF joined.descriptor_verified THEN
            IF joined.origin_invalid THEN RETURN false;END IF;
        ELSE
            PERFORM life_financial.recorder_executor_origin(p_account,delivery.body->'binding',p);
        END IF;
        IF joined.is_applied_head AND joined.actual IS DISTINCT FROM joined.target THEN RETURN false;END IF;$new$;
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_RECORDER_BATCH_UNKNOWN_PREDECESSOR';END IF;
    definition:=replace(definition,needle,replacement);
    needle:=$old$    FOR delivery IN SELECT DISTINCT ON(x.stream) x.* FROM life_financial.recorder_outbox x
        JOIN life_financial.projection_receipts r USING(account_id,projection_id)
        WHERE x.account_id=p_account AND x.stream LIKE 'executor:%' ORDER BY x.stream,x.sequence DESC LOOP
        p:=(delivery.body#>>'{command,payload_json}')::jsonb;
        SELECT to_jsonb(e) INTO actual FROM life_recorder."Executors" e WHERE e.id=p->>'id';
        IF actual IS DISTINCT FROM life_financial.recorder_executor_row(p) THEN RETURN false; END IF;
    END LOOP;$old$;
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_RECORDER_BATCH_UNKNOWN_PREDECESSOR';END IF;
    EXECUTE replace(definition,needle,'');
END; $$;
