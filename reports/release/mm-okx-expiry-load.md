# Expired-submission fix: offline load evidence

Image `7dca3ce9fedd` contains source commit `5c31730ce` (including fix
`0a619a57f`). The image matches 1,156 runtime/config source hashes; native
regression, 319 PostgreSQL tests, focused expiry tests, Swagger and three
packaged E2E groups pass. No real OKX expired-response payload has yet been
captured on this version.

Full R1 completed, independently verified: 300 s warmup, 900 s mixed, 90 s
burst, 3,600 s soak and six fault groups. In soak, 4,886 synthetic fills matched
recorder fills and positions; there were no unexpected API errors. Adjusted
memory retention increased 14,561,280 bytes against a 64 MiB budget.

A separate disposable DB clone ran briefly during the original R1 warmup. That
window is not an isolated measurement; mixed, burst, soak and fault windows did
not overlap it. A same-image, same-fixture 300 s warmup plus 900 s mixed was
subsequently repeated without that job and passed. A first R2 was interrupted
because another short clone dry run overlapped its warmup. A fresh isolated R2
then passed and was independently checked. These overlaps are test-environment
activities, not application failures; preserve both original artifacts.

| Window | Read p99 |
| --- | ---: |
| Isolated repeat warmup | 95.50 ms |
| Isolated repeat mixed | 119.84 ms |
| R1 mixed | 95.32 ms |
| R1 burst | 90.05 ms |
| R1 soak | 110.88 ms |
| Clean R2 warmup | 79.95 ms |
| Clean R2 mixed | 75.30 ms |

During R1 healthy windows, 13 engine probe failures were reported and recovered;
clean R2 reported three recovered engine probes. A probe recovery interval is
not a direct measurement of quote downtime; no unique cause is established.
Finite local tests do not guarantee uninterrupted quotes or absence of future
memory growth. Exact data and scope are in [the evidence](mm-okx-expiry-load-evidence.json).
The unresolved real Demo incident and its proposed recovery are documented
[separately](../demo/okx-expired-submission-investigation.md). No production or
Cloud Run run is claimed.
