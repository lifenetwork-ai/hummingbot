-- Bounded operational read of the newest recorded audit checkpoint.
-- This index is not signature verification or financial authority.
CREATE INDEX audit_checkpoint_sequence
    ON life_financial.audit_checkpoints(account_id,through_sequence DESC,checkpoint_id);
