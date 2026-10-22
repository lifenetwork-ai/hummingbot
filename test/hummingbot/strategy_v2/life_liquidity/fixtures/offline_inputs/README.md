# Offline test inputs

These are reviewed regression inputs, not results of the current run and not
trading permission. Restored byte-for-byte from Git revision `a5257452802f8ea7c1227f9a9773cbfb7c5694ca`
before the old `docs/plans/evidence` directory was removed. Current gates must
still execute their original workflows and assertions. Budget ceilings are unchanged.

| Fixture | Original file | SHA-256 |
| --- | --- | --- |
| spot_replay.json | life_o5_spot_replay_v2.json | eff08e3de1b687723eeeb99f38a6efa52585317ee640d2ed403a3337ecc88998 |
| spot_acceptance.json | life_o6_acceptance_manifest.json | f212fe3426c70e9075c0474e7fb7ac2b0740bf93c333f25c5014b79cd8504870 |
| combined_replay.json | life_o7_combined_replay.json | b8857a3607af798f6f98bec834e2f27ae4bf6090fdd497e728ec5a57df0f5974 |
| full_acceptance.json | life_o7_acceptance_manifest.json | 8c538d954f593a938932558d4671b64eb3ac3998d562f9120de62bbf57972f21 |
| review_acceptance.json | life_o8_acceptance_manifest.json | c9d9c4df2ed8e5ddddd4238e4b512549ab3a98246c03a756e0ca52e597e5343e |
| budgets.json | life_o8_offline_budgets.json | e1be70f238efdaad68086e3ae43fdc8ca4447a4c6da6dc36c03260ca7e6be5c3 |
