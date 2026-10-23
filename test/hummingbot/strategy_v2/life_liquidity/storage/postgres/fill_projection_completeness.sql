-- Test-only reciprocal existence obligations. Never loaded by migrations.
BEGIN;
-- A hold witness pins values, but cannot prove a row which never existed.
-- Require the original principal and every nonzero fee buffer at the basis.
ALTER TABLE fill_projection_spike.order_basis
    ADD COLUMN principal_id text GENERATED ALWAYS AS (operation_id||':principal') STORED,
    ADD COLUMN base_fee_id text GENERATED ALWAYS AS (CASE WHEN fee_base>0 THEN operation_id||':fee:LIFE' END) STORED,
    ADD COLUMN quote_fee_id text GENERATED ALWAYS AS (CASE WHEN fee_quote>0 THEN operation_id||':fee:USDT' END) STORED,
    ADD CONSTRAINT prototype_principal_exists FOREIGN KEY(account_id,principal_id)
        REFERENCES fill_projection_spike.hold_proof(account_id,reservation_id) DEFERRABLE INITIALLY DEFERRED,
    ADD CONSTRAINT prototype_base_fee_exists FOREIGN KEY(account_id,base_fee_id)
        REFERENCES fill_projection_spike.hold_proof(account_id,reservation_id) DEFERRABLE INITIALLY DEFERRED,
    ADD CONSTRAINT prototype_quote_fee_exists FOREIGN KEY(account_id,quote_fee_id)
        REFERENCES fill_projection_spike.hold_proof(account_id,reservation_id) DEFERRABLE INITIALLY DEFERRED;

-- Exact-value FKs protect covered facts; reverse keys also reject orphan raw
-- SPOT rows, including insertion with all USER triggers disabled. SWAP uses
-- its separate native audit; NULL here deliberately makes no SWAP claim.
ALTER TABLE life_financial.fills
    ADD COLUMN prototype_spot_trade text GENERATED ALWAYS AS (CASE WHEN product='SPOT' THEN trade_id END) STORED,
    ADD CONSTRAINT prototype_raw_fill_complete FOREIGN KEY(account_id,prototype_spot_trade)
        REFERENCES fill_projection_spike.fill_source(account_id,trade_id) DEFERRABLE INITIALLY DEFERRED;
CREATE UNIQUE INDEX prototype_fee_identity ON fill_projection_spike.fill_source(account_id,payment_id);
ALTER TABLE life_financial.fees
    ADD COLUMN prototype_spot_payment text GENERATED ALWAYS AS (CASE WHEN product='SPOT' THEN payment_id END) STORED,
    ADD CONSTRAINT prototype_raw_fee_complete FOREIGN KEY(account_id,prototype_spot_payment)
        REFERENCES fill_projection_spike.fill_source(account_id,payment_id) DEFERRABLE INITIALLY DEFERRED;
COMMIT;
