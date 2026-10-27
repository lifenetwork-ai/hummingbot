# Foreground service logging

`hbot serve` writes console logs to stdout. It defaults to one JSON object per
line with `severity`, `message`, UTC `timestamp`, logger name, bot ID, config,
owner UUID/generation and revision. The current MM session ID is included when
available. Event, reason, scope, error type and command ID are included when
the logging call supplies them. Multiline messages and tracebacks stay in one
physical JSON line.

Cloud Run collects console output. Its structured-log ingestion recognizes
`severity` and `message`; no sidecar or Google logging dependency is required.
See [Google's service logging guide](https://docs.cloud.google.com/run/docs/logging).

```dotenv
LOG_FORMAT=json
LOG_LEVEL=INFO
```

Local `.env` uses the existing environment loader. On Cloud Run inject these
settings into the revision environment. A settings change requires a process
restart/new revision; there is no runtime log-level admin endpoint.

| Level | Meaning / use |
|---|---|
| `DEBUG` | Detailed connector/executor diagnostics; temporarily enable for investigation |
| `INFO` | Normal startup, lifecycle, successful recovery and shutdown |
| `WARNING` | Degradation or transient failure that can recover/retry |
| `ERROR` | Failed operation or incident requiring operator intervention |
| `CRITICAL` | Available for an unrecoverable process-wide failure; ordinary waiting/retry is not critical |

`LOG_LEVEL` is a minimum threshold: `INFO` emits INFO through CRITICAL; `DEBUG`
emits all five. Existing call sites keep their explicit levels. The service
promotes Hummingbot's `network()` calls to WARNING before filtering, so they are
visible at INFO. Custom EVENT/METRIC records remain diagnostic verbosity and
render as DEBUG when admitted. Legacy interactive/file YAML logging keeps its
original NETWORK level and configuration.

SQLAlchemy keeps its WARNING threshold even when the service uses DEBUG, so
routine queries and bound row values do not flood console output. SQLAlchemy
warnings/errors remain visible according to the service threshold. Python
warnings, including Pydantic serializer warnings, go through the same JSON
formatter as WARNING instead of writing multiline text directly to stderr.

`LOG_FORMAT=text` provides readable local output with the same level threshold,
UTC time and context; use JSON for Cloud Logging severity extraction. The service
reinstalls its console formatter after the shared application bootstrap resets
logging. No runtime log files are opened by console-only initialization.

Availability incidents emit WARNING or ERROR consistently with their API
severity. A changed reason or operator-action flag logs immediately; repeated
unchanged incidents remain limited to once per 60 seconds per scope. Recovery
emits INFO. This limits retry noise without hiding escalation.

Known configured secret values are replaced with `[REDACTED]` in messages,
context and exception output: environment keys containing PASSWORD, PASSPHRASE,
SECRET, API_KEY or DATABASE_URL, plus the encoded/decoded DSN password. Values
shorter than four characters are excluded to avoid corrupting routine text.
This is defense in depth, not detection of every possible secret: avoid logging
credentials, headers, raw requests or arbitrary private payloads, including at
DEBUG. The outer service failure boundary continues to log only sanitized reason
and exception type rather than raw driver/Pydantic exceptions.

Example Cloud Logging filters, with the actual service name substituted:

```text
resource.type="cloud_run_revision"
resource.labels.service_name="YOUR_SERVICE"
severity>=ERROR
```

```text
resource.type="cloud_run_revision"
jsonPayload.event="service_incident"
jsonPayload.reason_code="reporting_unavailable"
```

Use the revision label, `jsonPayload.bot_id`, `owner_generation` and `session_id`
to distinguish restarts, ownership handoffs and sessions. These application logs
do not yet propagate HTTP trace context; Cloud Run request logs remain separate.
The existing application access logger stays disabled, and no Authorization
header or Basic Auth credential is added to these context fields.
