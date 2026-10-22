-- Whole-flow profiling finds repeated account operation-family audits in the
-- recorder and financial proofs. Index the common discriminator once instead
-- of decompressing unrelated immutable command JSON for each family lookup.
-- No proof, permission, deadline, receipt or canonical command changes.
CREATE INDEX operation_family_audits
    ON life_financial.operations(account_id,(command->>'kind'),operation_id);
