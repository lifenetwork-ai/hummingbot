-- Candidate full replay: batch source reads, retain original ordered arithmetic.
-- No cache, retained-history pruning, policy or physical-COMMIT change.
CREATE FUNCTION life_financial.spot_inventory_evidence_history(p_account uuid)
RETURNS TABLE(operation_id life_financial.identity,evidence jsonb)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_column
DECLARE r record;j jsonb;key life_financial.identity;actuals jsonb;held jsonb;identities jsonb;fills jsonb;
    base numeric;quote numeric;gross numeric;fee numeric;sign numeric;quantity numeric;price numeric;loss numeric;reference numeric;last_intent text;
BEGIN
    FOR r IN
        WITH cycles AS MATERIALIZED (
            SELECT s.operation_id,s.command->'intent_ids' AS members,
                CASE WHEN jsonb_typeof(s.command->'intent_ids')='array'
                    AND jsonb_array_length(s.command->'intent_ids') BETWEEN 1 AND 64
                    THEN s.command->'intent_ids' ELSE '[]'::jsonb END AS accepted
            FROM life_financial.spot_inventory_settlements s WHERE s.account_id=p_account
        ), keys AS MATERIALIZED (
            SELECT c.operation_id,v.value AS item,v.value#>>'{}' AS key FROM cycles c
                CROSS JOIN LATERAL jsonb_array_elements(c.accepted) v
        ), selected AS MATERIALIZED (SELECT DISTINCT key FROM keys),
        source_fills AS MATERIALIZED (
            SELECT f.*,a.side,a.exchange_order_id AS accepted_order FROM life_financial.spot_fill_facts f
                JOIN selected s ON s.key=f.intent_id
                JOIN life_financial.send_attempts a ON a.account_id=f.account_id AND a.intent_id=f.intent_id
                WHERE f.account_id=p_account
        ), totals AS MATERIALIZED (
            SELECT intent_id,sum((observation->>'quantity_base_life')::numeric) AS filled,
                bool_or(exchange_order_id<>accepted_order) AS wrong_order FROM source_fills GROUP BY intent_id
        ), holds AS MATERIALIZED (
            SELECT h.intent_id,bool_or(h.state<>'TERMINAL' OR h.remaining<>0) AS held
                FROM life_financial.reservations h JOIN selected s ON s.key=h.intent_id
                WHERE h.account_id=p_account GROUP BY h.intent_id
        ), member_rows AS MATERIALIZED (
            SELECT k.operation_id,k.key,jsonb_typeof(k.item)='string' AS string_key,
                jsonb_build_object('key',k.key,'identity',jsonb_build_array(k.key,a.wire_id,a.exchange_order_id,a.session_id,a.epoch),
                    'held',greatest(d.subsidy,g.execution_loss)::text,'error',
                    CASE WHEN a.account_id IS NULL OR a.product<>'SPOT' OR a.state<>'TERMINAL' OR a.exchange_order_id IS NULL THEN 'terminal'
                    WHEN d.account_id IS NULL THEN 'preparation'
                    WHEN g.account_id IS NULL OR g.filled_base<=0 OR d.request->>'budget_scope'='EXIT'
                        OR terminal.account_id IS NULL OR COALESCE(h.held,false) OR g.filled_base<>COALESCE(f.filled,0)
                        OR COALESCE(f.wrong_order,false) THEN 'member' END) AS body
            FROM keys k LEFT JOIN life_financial.send_attempts a ON a.account_id=p_account AND a.intent_id=k.key
                LEFT JOIN life_financial.spot_preparations d ON d.account_id=p_account AND d.intent_id=k.key
                LEFT JOIN life_financial.spot_accounting_progress g ON g.account_id=p_account AND g.intent_id=k.key
                LEFT JOIN life_financial.spot_terminal_facts terminal ON terminal.account_id=p_account AND terminal.intent_id=k.key
                LEFT JOIN totals f ON f.intent_id=k.key LEFT JOIN holds h ON h.intent_id=k.key
        ), member_groups AS MATERIALIZED (
            SELECT operation_id,count(*) AS n,count(DISTINCT key) AS distinct_n,bool_and(string_key) AS all_strings,
                jsonb_agg(body ORDER BY key COLLATE "C") AS bodies FROM member_rows GROUP BY operation_id
        ), fill_groups AS MATERIALIZED (
            SELECT k.operation_id,jsonb_agg(jsonb_build_array(f.trade_id,f.intent_id,f.side,f.observation)
                ORDER BY (f.observation->>'fill_at_ms')::bigint,f.trade_id COLLATE "C") AS bodies
            FROM keys k JOIN source_fills f ON f.intent_id=k.key GROUP BY k.operation_id
        ) SELECT c.operation_id,c.members,m.n,m.distinct_n,m.all_strings,m.bodies AS member_rows,
            COALESCE(f.bodies,'[]'::jsonb) AS fill_rows
            FROM cycles c LEFT JOIN member_groups m USING(operation_id) LEFT JOIN fill_groups f USING(operation_id)
            ORDER BY c.operation_id COLLATE "C"
    LOOP
        IF r.members IS NULL OR jsonb_typeof(r.members)<>'array' OR r.n IS NULL OR r.n NOT BETWEEN 1 AND 64
            OR r.n<>r.distinct_n OR r.all_strings IS NOT TRUE THEN
            RAISE EXCEPTION 'LIFE_SETTLEMENT_MEMBERS_INVALID' USING ERRCODE='P0001';END IF;
        actuals:='{}';held:='{}';identities:='[]';fills:=r.fill_rows;
        base:=0;quote:=0;gross:=0;last_intent:=NULL;
        FOR j IN SELECT value FROM jsonb_array_elements(r.member_rows) LOOP
            key:=j->>'key';
            IF j->>'error'='terminal' THEN RAISE EXCEPTION 'LIFE_SETTLEMENT_TERMINAL_REQUIRED' USING ERRCODE='P0001';
            ELSIF j->>'error'='preparation' THEN RAISE EXCEPTION 'query returned no rows' USING ERRCODE='P0002';
            ELSIF j->>'error'='member' THEN RAISE EXCEPTION 'LIFE_SETTLEMENT_MEMBER_UNVERIFIED' USING ERRCODE='P0001';END IF;
            identities:=identities||jsonb_build_array(j->'identity');
            actuals:=actuals||jsonb_build_object(key,'0');held:=held||jsonb_build_object(key,j->>'held');
        END LOOP;
        FOR j IN SELECT value FROM jsonb_array_elements(fills) LOOP
            quantity:=(j#>>'{3,quantity_base_life}')::numeric;price:=(j#>>'{3,price_usdt}')::numeric;
            fee:=(j#>>'{3,signed_fee}')::numeric;reference:=(j#>>'{3,independent_price_usdt}')::numeric;
            sign:=CASE j->>2 WHEN 'BUY' THEN 1 ELSE -1 END;
            base:=base+sign*quantity+CASE WHEN j#>>'{3,fee_currency}'='LIFE' THEN fee ELSE 0 END;
            quote:=quote-sign*quantity*price+CASE WHEN j#>>'{3,fee_currency}'='USDT' THEN fee ELSE 0 END;
            loss:=greatest(0,-fee*CASE WHEN j#>>'{3,fee_currency}'='LIFE' THEN reference ELSE 1 END-sign*(reference-price)*quantity);
            gross:=gross+loss;last_intent:=j->>1;
            actuals:=actuals||jsonb_build_object(last_intent,((actuals->>last_intent)::numeric+loss)::text);
        END LOOP;
        IF last_intent IS NULL OR base<>0 THEN RAISE EXCEPTION 'LIFE_SETTLEMENT_INVENTORY_NOT_FLAT' USING ERRCODE='P0001';END IF;
        actuals:=actuals||jsonb_build_object(last_intent,((actuals->>last_intent)::numeric+greatest(0,-quote-gross))::text);
        FOR key IN SELECT value FROM jsonb_array_elements_text(r.members) LOOP PERFORM (actuals->>key)::life_financial.amount;END LOOP;
        operation_id:=r.operation_id;
        evidence:=jsonb_build_object('actuals',actuals,'held_before',held,'proof',encode(sha256(convert_to(
            life_financial.canonical_json_v1(jsonb_build_object('identities',identities,'fills',fills)),'UTF8')),'hex'));
        RETURN NEXT;
    END LOOP;
END; $$;
REVOKE ALL ON FUNCTION life_financial.spot_inventory_evidence_history(uuid) FROM PUBLIC;

DO $$ DECLARE definition text;needle text;replacement text;BEGIN
    definition:=pg_get_functiondef('life_financial.spot_inventory_settlements_verified(uuid)'::regprocedure);
    needle:='s.*,life_financial.spot_inventory_evidence(p_account,s.command->''intent_ids'') AS fresh,';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_SETTLEMENT_EVIDENCE_BATCH_UNKNOWN_PREDECESSOR';END IF;
    definition:=replace(definition,needle,'s.*,e.evidence AS fresh,');
    needle:='WITH cycles AS MATERIALIZED (';
    definition:=replace(definition,needle,'WITH all_evidence AS MATERIALIZED (SELECT * FROM life_financial.spot_inventory_evidence_history(p_account)), cycles AS MATERIALIZED (');
    needle:='FROM life_financial.spot_inventory_settlements s LEFT JOIN life_financial.operations op USING(account_id,operation_id)';
    IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
        RAISE EXCEPTION 'LIFE_SETTLEMENT_EVIDENCE_BATCH_UNKNOWN_PREDECESSOR';END IF;
    EXECUTE replace(definition,needle,needle||' LEFT JOIN all_evidence e USING(operation_id)');
END; $$;
