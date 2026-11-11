# Perpetual caps and regular-profile observation — 2026-11-11

Demo is running on revision103 with long **0.05 BTC**, short **0.05 BTC** and
gross **0.1 BTC**. The final policy supersedes earlier proposals. The 30-minute
window has **zero unknown samples**, but the **99% two-sided target is not met**.
[Portable evidence](cloudrun-perpetual-cap-evidence.json) records exact hashes and counts.

## Cap diagnosis and application

On revision102, long 0.0030 BTC plus short 0.0049 BTC used 0.0079 BTC of the former
0.008 BTC gross cap. Only 0.0001 BTC remained. Each 25-USDT entry exceeded that
headroom, so both sides correctly reported `inventory_limit` despite current
positions being within caps. A full short-side cap alone still permits BUY when
gross headroom remains; flat positions and the approved larger caps permit both
sides. Ten selected native controller/cap tests pass. No production cap logic
needed a fix.

Stop68 and the native Disconnect workflow canceled orders and closed the managed
perpetual positions. Same-account Set restored credentials at control70. Two
complete REST scans per market verified 729 fills, flat perpetual positions,
original fees, cash conservation and exact native financial replay. No database
repair or HALT reset was performed; the actual audit principal was Basic bootstrap.

The approved policy was then applied through a catalogue-only image overlay on
revision102's `db1b809be` build. Packaged YAML and all four status/observer source
files match the tested workspace. Revision103's new pinned session at control73
confirmed the exact 0.05/0.05/0.1 BTC values and catalogue hash. Other budgets,
spreads, guards, credentials and risk checkpoints were preserved.

## Complete regular-profile window

The window ran from 02:21:47 to 02:51:48 UTC (09:21:47–09:51:48 Vietnam time).
It contains 899 samples over 1,799.65 sampled seconds; maximum sampling gap 2.314 s.
The same revision, session and owner were retained, without HALT or restart.

| Market | Two-sided | Eligible gaps | Unknown | Guard-excluded | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| Spot | 0 | 0 | 0 | 899 | No eligible denominator; not qualified |
| Perpetual | 874 | 25 | 0 | 0 | 97.219%; fails 99% target |

Spot BUY remained sizing/inventory-skew constrained. These samples are not
credited as quotes. Perpetual retained at least one acknowledged side in every
sample; its 25 missing-side samples form 17 short runs. The longest observed run
span was 2.025 s, and the longest bracket between surrounding two-sided samples
was 6.217 s. These are sampled bounds, not exact exchange downtime.

The missing-side observations coincide with full entries transferring to held
positions and replacements awaiting confirmation. Independent REST proof confirms
BUY and SELL handoff/replacement before the original 60-second refresh deadline,
with original fees and fresh account observations. All 95 actual fills timestamped
inside the window are perpetual maker executions (`execType=M`). This does not
attribute every latency component to a single cause. The failed gate is retained;
no denominator, threshold or classification was relaxed.

The bounded Cloud log slice contains seven warnings: two self-recovering probes
at 300.832/300.865 ms, one local spread-policy rejection and four access-related
records. Access records include a pre-whitelist login denial and a qualification
client request to an unsupported audit path; the correct `/audit` endpoint worked.
No ERROR, child restart, service stop, ownership loss or funding incident appeared.

This regular-profile window has different spreads, inventory eligibility and fill
activity from the earlier temporary-profile revision100 result. It is not a
controlled before/after performance comparison.

## Financial closeout and running state

After Stop74, two full REST scans per market matched **852 fills**: 424 Spot and
428 perpetual. Native Spot journal replay matched all five position fields
exactly. Full perpetual replay, BTC cash conservation, fees and traded volume also
matched; the original 650 fills were unchanged. There were no pending orders,
duplicate fill keys or unapplied journal entries; checkpoint hash and cold
bootstrap verification passed.

Perpetual exposure was long 0.0158 BTC plus short 0.0108 BTC (gross0.0266 BTC),
within the approved caps. Start75 left revision103 **running**, without HALT,
with `cold_recovery_supported=true` and the approved catalogue.

The HEDGE entry-and-hold preset retains filled exposure. Opposite-side entry
quotes open the opposite hedge leg, and refresh does not close held positions.
The higher cap adds capacity but does not provide continuous inventory reduction.
The regular-profile two-sided target, an explicit close/reduce policy and the
broader connected crash/OOM fault matrix remain open.

Private receipts, full venue proof and sample streams remain under
`build/cloudrun-runtime-baseline-20261111/` and
`build/perpetual-cap-qualification-20261111/`. Credentials are excluded here.
