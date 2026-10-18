-- Preserve canonical v1 bytes, hashes, OID and ACL. Scalar children already
-- serialize exactly as jsonb::text; only containers need recursive sorting.
-- No proof cache, stored hash replacement, policy or financial state change.
CREATE OR REPLACE FUNCTION life_financial.canonical_json_v1(p_value jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
DECLARE value text;
BEGIN
    CASE jsonb_typeof(p_value)
        WHEN 'object' THEN SELECT '{'||COALESCE(string_agg(to_jsonb(key)::text||':'||
            CASE WHEN jsonb_typeof(item) IN ('object','array') THEN life_financial.canonical_json_v1(item)
                ELSE item::text END,',' ORDER BY key COLLATE "C"),'')||'}'
            INTO value FROM jsonb_each(p_value) AS e(key,item);
        WHEN 'array' THEN SELECT '['||COALESCE(string_agg(
            CASE WHEN jsonb_typeof(item) IN ('object','array') THEN life_financial.canonical_json_v1(item)
                ELSE item::text END,',' ORDER BY ordinal),'')||']'
            INTO value FROM jsonb_array_elements(p_value) WITH ORDINALITY AS e(item,ordinal);
        ELSE value:=p_value::text;
    END CASE;
    RETURN value;
END; $$;
