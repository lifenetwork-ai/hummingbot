-- Private File-compatible joint NAV arithmetic. This kernel is not an account
-- observation, a current NAV receipt, a wallet writer or a send/release grant.
-- Native joint source qualification must bind settled cash, permanent transfer
-- valuations and replayed leg basis before publishing its result.
CREATE FUNCTION life_financial.portfolio_measure(cash numeric,life numeric,long_base numeric,short_base numeric,
    long_entry numeric,short_entry numeric,mark_price numeric,index_price numeric,external_flows numeric,prior_highwater numeric) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE value numeric;unrealized numeric;nav numeric;adjusted numeric;highwater numeric;drawdown numeric;
BEGIN
    FOREACH value IN ARRAY ARRAY[cash,life,long_base,short_base,long_entry,short_entry,mark_price,index_price,external_flows,prior_highwater] LOOP
        IF value IS NULL OR NOT (value>'-Infinity'::numeric AND value<'Infinity'::numeric)
            OR abs(value)>=1e100::numeric OR scale(value)>200 THEN
            RAISE EXCEPTION 'LIFE_PORTFOLIO_VALUE_INVALID' USING ERRCODE='P0001';END IF;
    END LOOP;
    IF least(cash,life,long_base,short_base,long_entry,short_entry)<0 OR least(mark_price,index_price,prior_highwater)<=0
        OR long_base>0 AND long_entry=0 OR short_base>0 AND short_entry=0 THEN
        RAISE EXCEPTION 'LIFE_PORTFOLIO_VALUE_INVALID' USING ERRCODE='P0001';END IF;
    -- Match each operation of the declared decimal28 half-even File model.
    -- Reported collateral is a risk constraint, not a second cash wallet, and
    -- is deliberately not added to NAV. Paid funding is already in settled cash.
    unrealized:=life_financial.joint_decimal28(
        life_financial.joint_decimal28(long_base*life_financial.joint_decimal28(mark_price-long_entry))
        +life_financial.joint_decimal28(short_base*life_financial.joint_decimal28(short_entry-mark_price)));
    nav:=life_financial.joint_decimal28(life_financial.joint_decimal28(cash+life_financial.joint_decimal28(life*index_price))+unrealized);
    adjusted:=life_financial.joint_decimal28(nav-external_flows);highwater:=greatest(prior_highwater,adjusted);
    drawdown:=life_financial.joint_decimal28(life_financial.joint_decimal28(
        greatest(0,life_financial.joint_decimal28(highwater-adjusted)),highwater)*10000);
    RETURN jsonb_build_object('nav_quote',nav::text,'adjusted_nav_quote',adjusted::text,'highwater_quote',highwater::text,
        'drawdown_bps',drawdown::text,'unrealized_quote',unrealized::text,'external_flows_quote',external_flows::text,'trading_permission',false);
END; $$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA life_financial FROM PUBLIC;
