# Checkpoint and polling: current-image offline release load

On 2026-11-03 the locally packaged amd64 image `e9845e3fa109`, source
`d68d74e07`, passed the native regression (2,357 tests and 11 subtests), 314
PostgreSQL tests, Swagger and three packaged E2E groups. The image was checked
against 1,156 runtime/config source hashes; 1,223 source/fixture files stayed
unchanged through measurement. Native inputs matched the compiled base.

Unchanged R1 passed warmup (300 s), mixed (900 s), burst (90 s), soak (3,600 s)
and all six injected-fault groups. Independent verification matched 4,885
synthetic fills to recorder entries and positions, with no unexpected API errors.
R2 ordinary-entrypoint control passed its 300 s warmup and 900 s mixed windows,
with 17 shared fixture hashes checked and no unexpected API errors.

| Window | R1 read p99 | R2 read p99 |
| --- | ---: | ---: |
| Warmup | 78.14 ms | 99.20 ms |
| Mixed | 99.60 ms | 82.09 ms |
| Burst | 95.42 ms | — |
| Soak | 218.87 ms | — |

Soak memory retention grew 17,502,208 bytes by the test's cgroup adjusted-median
metric, below its 64 MiB budget; container peak was 766,574,592 bytes. This
finite local run cannot rule out longer-term retention or guarantee uninterrupted
quotes. During healthy windows 21 engine and one API probe failures were reported
and recovered in R1; R2 recorded one recovered engine probe. The reported probe
intervals are not measured quote downtime, and no unique cause is established.

The earlier 5b2 image passed its E2E and first three load windows, but its soak
was intentionally stopped around minute 32 after reproducing the legacy ambiguous
spot-startup risk. It is not a completed R1. Real OKX Demo acceptance and the
remaining unavailable post-backup audit history are recorded separately.
Raw local artifacts remain under `build/checkpoint-poll-release-20261103/attempt-3/`;
[portable evidence](mm-checkpoint-poll-load-evidence.json) contains the measured
summaries and hashes. No Cloud Run deployment or live trading is claimed.
