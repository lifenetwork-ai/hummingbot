-- PostgreSQL expands BETWEEN to a nested Boolean AND at parse analysis; a
-- pg_dump/reparse flattens that tree. Spell the identical bounds explicitly so
-- independently restored catalog definitions have the same canonical image.
-- Never accept an unknown/weakened predecessor merely to make a backup pass.
DO $$ DECLARE original text;BEGIN
    SELECT pg_get_constraintdef(oid,false) INTO STRICT original FROM pg_constraint
        WHERE conrelid='life_financial.spot_markout_policies'::regclass
          AND conname='spot_markout_policies_horizons_ms_check';
    IF original<>'CHECK ((((cardinality(horizons_ms) >= 1) AND (cardinality(horizons_ms) <= 16)) AND (0 < ALL (horizons_ms))))' THEN
        RAISE EXCEPTION 'LIFE_RESTORE_UNKNOWN_MARKOUT_PREDECESSOR' USING ERRCODE='P0001';
    END IF;
END; $$;
ALTER TABLE life_financial.spot_markout_policies DROP CONSTRAINT spot_markout_policies_horizons_ms_check;
ALTER TABLE life_financial.spot_markout_policies ADD CONSTRAINT spot_markout_policies_horizons_ms_check
    CHECK(cardinality(horizons_ms)>=1 AND cardinality(horizons_ms)<=16 AND 0<ALL(horizons_ms));
