-- Reuse only the predecessor row already read in this same STABLE invocation.
-- Index the actual status wire/version predicate; preserve its timestamp sort
-- and malformed-value behavior. No result/permission is cached between reads.
DO $$ DECLARE p record;BEGIN
    IF to_regclass('life_financial.recorder_status_by_wire_version') IS NOT NULL THEN
        RAISE EXCEPTION 'LIFE_RECORDER_HISTORY_MIGRATION_MISMATCH';END IF;
    FOR p IN SELECT f.*,l.lanname FROM pg_proc f JOIN pg_language l ON l.oid=f.prolang
        WHERE f.oid IN (
            'life_financial.recorder_event_effects(uuid,jsonb,jsonb,bigint)'::regprocedure,
            'life_financial.recorder_effect(uuid,text,jsonb,jsonb,bigint)'::regprocedure,
            'life_financial.recorder_previous_effect(uuid,text,jsonb,bigint)'::regprocedure) LOOP
        IF p.lanname<>(CASE WHEN p.proname='recorder_previous_effect' THEN 'sql' ELSE 'plpgsql' END)
            OR p.provolatile<>'s' OR NOT p.prosecdef OR p.proisstrict OR p.proleakproof OR p.proparallel<>'u'
            OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']
            OR encode(sha256(convert_to(p.prosrc,'UTF8')),'hex') IS DISTINCT FROM (CASE p.proname
                WHEN 'recorder_event_effects' THEN '05d80525c662da6b8a67bdc98836d3ea04975f0e37367d3f3d6de39b0e5a6e04'
                WHEN 'recorder_effect' THEN '8955a9d60aa8f74a1e659b3109684f269d77153017b4a7dfefaccfe24884f279'
                WHEN 'recorder_previous_effect' THEN 'e8fcd10b53d0bb3e6941e663001a7c2eb04ac4fd57fcda2f38663d9b296a63e2'
            END) THEN RAISE EXCEPTION 'LIFE_RECORDER_HISTORY_MIGRATION_MISMATCH';END IF;
    END LOOP;
END; $$;

CREATE INDEX recorder_status_by_wire_version
    ON life_financial.recorder_projection_effects(account_id,(after_row->>'order_id'),source_version)
    WHERE table_kind='STATUS';

CREATE OR REPLACE FUNCTION life_financial.recorder_event_effects(p_account uuid, p_binding jsonb, p_command jsonb, p_version bigint)
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE p jsonb; src jsonb; key jsonb; value jsonb; effects jsonb:='[]'::jsonb;
    previous life_financial.recorder_projection_effects; latest jsonb; status text; status_at bigint;
BEGIN
    p:=life_financial.recorder_event_validate(p_account,p_binding,p_command); src:=p_command->'source';
    status:=p->>'kind'; status_at:=(p->>'timestamp_ms')::bigint;
    IF p ? 'wire' THEN
        SELECT e.after_row INTO latest FROM life_financial.recorder_projection_effects e
            WHERE e.account_id=p_account AND e.table_kind='STATUS' AND e.after_row->>'order_id'=p->>'wire'
                AND e.source_version<=p_version
            ORDER BY (e.after_row->>'timestamp')::bigint DESC,e.source_version ASC LIMIT 1;
        IF latest IS NOT NULL AND (latest->>'timestamp')::bigint>=status_at THEN
            status:=latest->>'status'; status_at:=(latest->>'timestamp')::bigint; END IF;
        key:=jsonb_build_object('id',p->>'wire');
        previous:=life_financial.recorder_previous_effect(p_account,'ORDER',key,p_version);
        IF p->>'kind' IN ('BuyOrderCreated','SellOrderCreated') THEN
            value:=jsonb_build_object('id',p->'wire','config_file_path',src->'config_file_path','strategy',src->'strategy_name',
                'market',src->'market','symbol','LIFE-USDT','base_asset','LIFE','quote_asset','USDT',
                'creation_timestamp',p->'timestamp_ms','order_type',p->'order_type','amount',life_financial.recorder_scaled(p->>'amount'),
                'leverage',p->'leverage','price',life_financial.recorder_scaled(p->>'price'),'last_status',status,
                'last_update_timestamp',status_at,'exchange_order_id',p->'exchange_id','position',p->'position');
        ELSE
            value:=previous.after_row;
            IF value IS NOT NULL THEN value:=value||jsonb_build_object('last_status',status,'last_update_timestamp',status_at); END IF;
        END IF;
        effects:=effects||jsonb_build_array(jsonb_build_object('table_kind','ORDER',
            'key_hash',encode(sha256(convert_to(key::text,'UTF8')),'hex'),'target_key',key,
            'previous_projection',previous.projection_id,'before_row',previous.after_row,'after_row',value));
        key:=jsonb_build_object('order_id',p->'wire','timestamp',p->'timestamp_ms','status',p->'kind');
        effects:=effects||jsonb_build_array(life_financial.recorder_effect(p_account,'STATUS',key,key,p_version));
    END IF;
    IF p->>'kind'='OrderFilled' THEN
        key:=jsonb_build_object('market',src->'market','order_id',p->'wire','exchange_trade_id',p->'trade_id');
        value:=key||jsonb_build_object('config_file_path',src->'config_file_path','strategy',src->'strategy_name',
            'symbol','LIFE-USDT','base_asset','LIFE','quote_asset','USDT','timestamp',p->'timestamp_ms','trade_type',p->'side',
            'order_type',p->'order_type','price',life_financial.recorder_scaled(p->>'price'),
            'amount',life_financial.recorder_scaled(p->>'amount'),'leverage',p->'leverage','position',p->'position',
            'trade_fee',(p->>'fee_json')::jsonb,'trade_fee_in_quote',life_financial.recorder_scaled(p->>'fee_quote'));
        effects:=effects||jsonb_build_array(life_financial.recorder_effect(p_account,'TRADE',key,value,p_version));
    END IF;
    IF p->>'kind'='FundingPaymentCompleted' THEN
        key:=jsonb_build_object('timestamp',p->'timestamp_ms');
        value:=key||jsonb_build_object('config_file_path',src->'config_file_path','market',src->'market','symbol','LIFE-USDT',
            'rate',(p->>'rate')::float8,'amount',(p->>'amount')::float8);
        effects:=effects||jsonb_build_array(life_financial.recorder_effect(p_account,'FUNDING',key,value,p_version));
    END IF;
    key:=jsonb_build_object('config_file_path',src->'config_file_path','market',src->'market');
    previous:=life_financial.recorder_previous_effect(p_account,'STATE',key,p_version);
    value:=key||jsonb_build_object('timestamp',greatest((p->>'timestamp_ms')::bigint,COALESCE((previous.after_row->>'timestamp')::bigint,0)),
        'saved_state',(p_command->>'tracking_json')::jsonb);
    effects:=effects||jsonb_build_array(jsonb_build_object('table_kind','STATE',
            'key_hash',encode(sha256(convert_to(key::text,'UTF8')),'hex'),'target_key',key,
            'previous_projection',previous.projection_id,'before_row',previous.after_row,'after_row',value));
    SELECT jsonb_agg(entries.entry ORDER BY entries.entry->>'table_kind',entries.entry->>'key_hash')
        INTO effects FROM jsonb_array_elements(effects) AS entries(entry);
    RETURN effects;
END; $function$
;
