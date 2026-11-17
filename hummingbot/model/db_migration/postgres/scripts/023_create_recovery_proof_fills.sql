-- Read-only venue evidence only; no financial baseline or application marker.
CREATE TABLE IF NOT EXISTS recovery_proof_fills (
    command_id TEXT NOT NULL,
    scan INTEGER NOT NULL,
    connector TEXT NOT NULL,
    venue_order_id TEXT NOT NULL,
    trade_id TEXT NOT NULL,
    bill_id TEXT NOT NULL,
    CONSTRAINT pk_recovery_proof_fills PRIMARY KEY (command_id, scan, connector, venue_order_id, trade_id),
    CONSTRAINT fk_recovery_proof_fills_command_id FOREIGN KEY (command_id)
        REFERENCES admin_commands (id),
    CONSTRAINT uq_recovery_proof_fills_command_id_scan_connector_bill_id
        UNIQUE (command_id, scan, connector, bill_id)
);
