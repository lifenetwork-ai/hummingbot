-- The common release verifier audits both original terminal releases and
-- explicitly unused births. An unused-only index cannot serve this combined
-- predicate: it otherwise reads/decompresses unrelated operation commands.
-- Keep the exact proof and MVCC visibility; only its access path changes.
CREATE INDEX shared_release_operation_history
    ON life_financial.operations(account_id,operation_id)
    WHERE command->>'kind' IN ('SHARED_RELEASE','SHARED_UNUSED_RELEASE');
