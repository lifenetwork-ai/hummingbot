-- Diagnostic alternatives only. Named parameters: markets, tz, pair, start_ms, end_ms.
-- All arithmetic is NUMERIC. Never run this file as a migration.

-- Baseline
WITH scoped AS (
    SELECT *, (to_timestamp(timestamp::double precision / 1000) AT TIME ZONE :tz)::date AS day,
        (COALESCE((trade_fee->>'percent')::numeric, 0) <> 0 OR EXISTS (
            SELECT 1 FROM json_array_elements(COALESCE(trade_fee->'flat_fees', '[]'::json)) fee
            WHERE (fee->>'amount')::numeric <> 0)) AS has_original_fee
    FROM trade_fills WHERE market = ANY(:markets) AND timestamp >= :start_ms AND timestamp < :end_ms
        AND (:pair IS NULL OR symbol = :pair)
) SELECT market,day,fee.token AS currency,
 SUM(GREATEST(fee.amount,0)) AS paid,SUM(GREATEST(-fee.amount,0)) AS rebates,SUM(fee.amount) AS net
 FROM scoped CROSS JOIN LATERAL json_to_recordset(COALESCE(trade_fee->'flat_fees','[]'::json))
 AS fee(token TEXT,amount NUMERIC) GROUP BY market,day,fee.token ORDER BY market,day,currency;

-- Pre-group payloads (rejected on diverse fees)
WITH scoped AS (
    SELECT *, (to_timestamp(timestamp::double precision / 1000) AT TIME ZONE :tz)::date AS day,
        (COALESCE((trade_fee->>'percent')::numeric, 0) <> 0 OR EXISTS (
            SELECT 1 FROM json_array_elements(COALESCE(trade_fee->'flat_fees', '[]'::json)) fee
            WHERE (fee->>'amount')::numeric <> 0)) AS has_original_fee
    FROM trade_fills WHERE market = ANY(:markets) AND timestamp >= :start_ms AND timestamp < :end_ms
        AND (:pair IS NULL OR symbol = :pair)
), payloads AS (
 SELECT market,day,COALESCE(trade_fee->'flat_fees','[]'::json)::text AS payload,count(*) AS n
 FROM scoped GROUP BY market,day,COALESCE(trade_fee->'flat_fees','[]'::json)::text)
 SELECT market,day,fee.token AS currency,SUM(GREATEST(fee.amount,0)*n) AS paid,
 SUM(GREATEST(-fee.amount,0)*n) AS rebates,SUM(fee.amount*n) AS net FROM payloads
 CROSS JOIN LATERAL json_to_recordset(payload::json) AS fee(token TEXT,amount NUMERIC)
 GROUP BY market,day,fee.token ORDER BY market,day,currency;

-- Text payload / lateral rescan (rejected in exploratory 100k run)
WITH scoped AS (
    SELECT *, (to_timestamp(timestamp::double precision / 1000) AT TIME ZONE :tz)::date AS day,
        (COALESCE((trade_fee->>'percent')::numeric, 0) <> 0 OR EXISTS (
            SELECT 1 FROM json_array_elements(COALESCE(trade_fee->'flat_fees', '[]'::json)) fee
            WHERE (fee->>'amount')::numeric <> 0)) AS has_original_fee
    FROM trade_fills WHERE market = ANY(:markets) AND timestamp >= :start_ms AND timestamp < :end_ms
        AND (:pair IS NULL OR symbol = :pair)
) SELECT market,day,fee.token AS currency,
 SUM(GREATEST(fee.amount,0)) AS paid,SUM(GREATEST(-fee.amount,0)) AS rebates,SUM(fee.amount) AS net
 FROM (SELECT market,day,COALESCE(trade_fee->'flat_fees','[]'::json)::text AS payload FROM scoped OFFSET 0) payloads
 CROSS JOIN LATERAL (SELECT * FROM json_to_recordset(payload::json) AS t(token TEXT,amount NUMERIC) OFFSET 0) fee
 GROUP BY market,day,fee.token ORDER BY market,day,currency;

-- Sign-filtered aggregates
WITH scoped AS (
    SELECT *, (to_timestamp(timestamp::double precision / 1000) AT TIME ZONE :tz)::date AS day,
        (COALESCE((trade_fee->>'percent')::numeric, 0) <> 0 OR EXISTS (
            SELECT 1 FROM json_array_elements(COALESCE(trade_fee->'flat_fees', '[]'::json)) fee
            WHERE (fee->>'amount')::numeric <> 0)) AS has_original_fee
    FROM trade_fills WHERE market = ANY(:markets) AND timestamp >= :start_ms AND timestamp < :end_ms
        AND (:pair IS NULL OR symbol = :pair)
) SELECT market,day,fee.token AS currency,
 COALESCE(SUM(fee.amount) FILTER (WHERE fee.amount > 0),0) AS paid,COALESCE(-SUM(fee.amount) FILTER (WHERE fee.amount < 0),0) AS rebates,SUM(fee.amount) AS net
 FROM scoped CROSS JOIN LATERAL json_to_recordset(COALESCE(trade_fee->'flat_fees','[]'::json))
 AS fee(token TEXT,amount NUMERIC) GROUP BY market,day,fee.token ORDER BY market,day,currency;

-- Two aggregate states; net derived in NUMERIC
WITH scoped AS (
    SELECT *, (to_timestamp(timestamp::double precision / 1000) AT TIME ZONE :tz)::date AS day,
        (COALESCE((trade_fee->>'percent')::numeric, 0) <> 0 OR EXISTS (
            SELECT 1 FROM json_array_elements(COALESCE(trade_fee->'flat_fees', '[]'::json)) fee
            WHERE (fee->>'amount')::numeric <> 0)) AS has_original_fee
    FROM trade_fills WHERE market = ANY(:markets) AND timestamp >= :start_ms AND timestamp < :end_ms
        AND (:pair IS NULL OR symbol = :pair)
) SELECT market,day,fee.token AS currency,
 COALESCE(SUM(fee.amount) FILTER (WHERE fee.amount > 0),0) AS paid,COALESCE(-SUM(fee.amount) FILTER (WHERE fee.amount < 0),0) AS rebates,(COALESCE(SUM(fee.amount) FILTER (WHERE fee.amount > 0),0) + COALESCE(SUM(fee.amount) FILTER (WHERE fee.amount < 0),0)) AS net
 FROM scoped CROSS JOIN LATERAL json_to_recordset(COALESCE(trade_fee->'flat_fees','[]'::json))
 AS fee(token TEXT,amount NUMERIC) GROUP BY market,day,fee.token ORDER BY market,day,currency;
