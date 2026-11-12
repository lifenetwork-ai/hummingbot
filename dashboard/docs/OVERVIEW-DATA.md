# Overview: API observations

Overview uses the admin API's persisted observations. A successful HTTP response is
not proof that the owner, market or individual observation is current.

| Section | Source | Display contract |
| --- | --- | --- |
| Runtime and risk policies | `GET /status` | Require owner availability, a fresh observation and matching session before treating policies as current. |
| Working orders | `GET /statistics/liquidity?market=all` | Require observed state, matching session ID and generation, and a valid market sample. A null order list is unavailable; an empty observed list confirms no acknowledged own orders. |
| Coverage | Same liquidity response | `two_sided_fraction_of_elapsed` is cumulative sampled two-sided time divided by elapsed session time. It is not a rolling or eligible-time SLA. Per-minute history is unsupported. |
| Account equity | `GET /balances` | Show current USDT equity for one account. Cash is not an equity fallback. Multiple accounts remain separate in Shared Balances. |
| Cash balances | Same balances response | Null is unknown, distinct from zero. Owner unavailable, stale or absent-from-latest rows are retained observations, not current balances. Native currency balances cannot form a portfolio allocation without valuation rates. |
| Derivative positions | `GET /positions` | Ignore spot `not_applicable` coverage. Flat requires a current observed perpetual scope with no positions. Normalize signed quantity to magnitude and apply LONG/SHORT direction once. Do not net different instruments. |

Loading and API errors are independent for each source. Source retries perform only
GET requests; they do not submit trading commands. A failed refresh may retain the
previous response, which must not be presented as current exposure or equity.
Backend unavailable responses and unsupported fields are different from API errors.
