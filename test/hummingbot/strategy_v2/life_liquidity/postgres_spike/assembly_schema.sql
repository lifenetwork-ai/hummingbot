-- Disposable recorder projection operation, bound to an already durable attempt.
CREATE TABLE IF NOT EXISTS recorder_operations (
    account TEXT NOT NULL, intent TEXT NOT NULL, wire TEXT NOT NULL,
    payload JSONB NOT NULL, payload_sha TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('READY','APPLIED')),
    PRIMARY KEY (account,intent),
    FOREIGN KEY (account,intent) REFERENCES attempts(account,intent)
);
