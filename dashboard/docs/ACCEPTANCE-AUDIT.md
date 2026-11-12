# Nghiệm thu frontend theo TODO T-01–35

> Bản audit dưới đây là mốc trước UI completion. Trạng thái tích hợp mới nhất ở [UI-COMPLETION-INTEGRATION.md](UI-COMPLETION-INTEGRATION.md); không dùng số tổng hợp cũ làm kết luận hiện tại.

Sau UI integration, các tiêu chí PARTIAL do thiếu UI/binding (T-05/06/08/11/15/17/22/23/25/27/28/29) có bằng chứng component/page tương ứng và được chuyển PASS. T-19 cũng chuyển PASS: server `connected` là bằng chứng owner/version khớp và còn mới; frontend poll status và hết hạn evidence cũ. T-07 đã được bổ sung projection backend và OpenAPI; chờ backend test và Swagger regeneration trong môi trường đủ dependencies. T-09 vẫn thiếu nguồn phân loại eligibility rõ ràng và lịch sử interval rolling 30 phút. T-32–35 còn thiếu một phần bằng chứng browser/visual/quality. A11y được bỏ qua theo yêu cầu.

## Phạm vi và cách kết luận

Nghiệm thu frontend local/mock trên branch `dev`, HEAD nền `4b4188e1d` cộng các thay đổi chưa commit. Hosting, deployment và nghiệm thu hệ thống thật không thuộc phạm vi. Không dùng các hạng mục đó làm lý do để giữ mở lỗi implementation frontend.

- **PASS**: hành vi frontend tương ứng có trong source và có bằng chứng local/model/controller/component liên quan. Không chứng minh tác động exchange thật.
- **PARTIAL**: có module/đường đi chính nhưng còn thiếu tiêu chí implementation hoặc bằng chứng nghiệm thu cụ thể.
- **CONTRACT**: frontend biểu đạt unknown đúng nhưng contract chưa đủ field để hoàn tất tiêu chí gốc.

Gate PASS không đồng nghĩa tất cả task PASS. Các checkbox workflow trong TODO chỉ đóng khi toàn bộ tiêu chí của task đã được kiểm chứng; không đánh dấu hoàn tất bằng cách bỏ phần còn thiếu.

Đợt component UI đã được ghép và kiểm tra theo [báo cáo tích hợp](UI-COMPLETION-INTEGRATION.md). Các dòng PASS bên dưới phản ánh source và bằng chứng component/page/controller sau tích hợp; không mở rộng sang contract chưa có hoặc browser/runtime chưa chạy.
## Ma trận từng task

| Task | Kết luận | Source / bằng chứng và phần còn thiếu |
|---|---|---|
| T-01 | PASS | Requirement, generated schema, API adapter và tài liệu integration cùng mô tả scope operational/recovery. `api:check` đối chiếu artifact. |
| T-02 | PASS | `core/config`, `operational-context/context.ts`, ContextBar và tests giữ identity/environment/account verification; không suy environment từ URL. |
| T-03 | PASS | Auth persistence/cache/expiry/401/403, deep-link và receipt scope tests; browser login/new-tab/reload/logout không replay. Body không bí mật cho retry được tách khỏi secret. |
| T-04 | PASS | Permissions kết hợp server actions; page/controller tests cho Viewer và quản trị; backend availability vẫn được revalidate khi POST. |
| T-05 | PASS | Overview hiển thị Random cycle, runtime service/owner, requested/observed và trạng thái riêng; xem `SessionSummaryCard.tsx` và UI integration evidence. |
| T-06 | PASS | Attention banner trình bày normal/waiting/stale, backend detail/next_action và hướng xử lý có callback hoặc hướng dẫn; xem `OperatorAttentionBanner.tsx`. |
| T-07 | PARTIAL | Backend projection bổ sung giá, lượng còn lại và notional từng lệnh limit OPEN/PARTIALLY_FILLED đã được xác nhận; pending create/cancel được đếm riêng. Frontend đọc đúng khóa BUY/SELL chữ hoa và phân biệt rows, unknown, empty. Component/model tests dashboard PASS; backend pytest và Swagger regeneration SKIP vì môi trường Python thiếu pytest/pandas. |
| T-08 | PASS | View hiển thị BUY/SELL riêng, activity/reason/diagnosis, pending và giới hạn evidence; không suy diễn fill→replacement. Component tests được ghi trong UI completion report. |
| T-09 | CONTRACT | Có tỷ lệ two-sided cộng dồn và thời gian eligible/unknown theo readiness, nhưng chưa có lịch sử interval hoặc provenance xác nhận hai phía đủ vốn/không bị giới hạn. Không thể suy numerator/denominator rolling eligible hay thời gian guard-restricted lịch sử. UI ghi rõ coverage cộng dồn, tách observed-but-ineligible khỏi unknown, hiện warning threshold cấu hình và thời gian thiếu từng phía; không tuyên bố đạt 99% rolling. |
| T-10 | PASS | Query key/session isolation, source states độc lập, timestamps, background polling pause/backoff và cancellation có test. Browser quan sát unmount qua 15-second status cadence. |
| T-11 | PASS | Form catalogue-backed và read-only current/pinned configuration hiển thị riêng references, weights, budgets, spreads, guards; xem `ConfigurationReadOnlyView.tsx` và page integration. |
| T-12 | PASS | Version/hash/session đã review, exact receipt polling, terminal states và invalidation/readback; 202 chỉ là tiếp nhận. Controller/page/receipt tests. |
| T-13 | PASS | Journal exact non-secret DTO/key theo API/principal/bot, restored receipt không replay, same-request retry và conflict/review; duplicate lifecycle tests. Không phải distributed browser lock. |
| T-14 | PASS | Stop lane riêng, refresh đúng target, availability/permission, receipt/unknown và residual exposure; tests Stop khi Switch pending và global dialog. |
| T-15 | PASS | Field/global feedback, request ID, retry/review guidance và unknown outcome được nối vào session, credentials và accounts; progress/receipt giữ độc lập với toast. |
| T-16 | PASS | Balance accounts riêng, cash/equity/available không thay thế nhau; exact strings, source/stale và LONG/SHORT riêng; AssetRibbon/Overview/decimal tests. |
| T-17 | PASS | Overview hiển thị market policies, inventory skew, vị thế và phía/asset bị hạn chế khi có evidence; không dựng utilization/threshold giả. |
| T-18 | PASS | Bounded check, permission/cooldown, exact receipt và matched/mismatch/incomplete/unavailable độc lập với receipt success; retry/remount tests. |
| T-19 | PASS | Backend chỉ trả `connected` sau fresh heartbeat/availability, owner UUID/generation match và `credential_versions[connector] == slot.version` (`admin_credentials.py`, integration test `test_connected_requires_fresh_matching_owner_evidence_not_only_saved_credentials`). Frontend polls credential status every 5s and expires either cached source after 5s; the slot observation must also be within 5s of runtime `server_time`. Set vẫn không tự Start. |
| T-20 | PASS | Disconnect confirmation tách Stop, root identity và bốn final proofs, giữ ý nghĩa spot/key chưa revoke; Connections model/controller tests. |
| T-21 | PASS | Blocked stage/reason/next retry, root/current version domains, repair/retry và secret re-entry; unknown lock không tự gửi lại; tests partial proof, timeout, ordering và duplicate. |
| T-22 | PASS | Recovery entry giải thích quyền/availability, hướng non-Admin liên hệ Admin; scoped fence giữ Stop và chặn Start/Switch/connection đến khi xác minh. |
| T-23 | PASS | Exact check, blocked/can_recover, verification, server preview expiry và đủ bảy cặp before/after đã nối trong `AdminRecoveryView.tsx`. |
| T-24 | PASS | Check ID/token/version/key mới, drift/expiry recheck, recovered proof và trading stopped; UI token không render/persist; reset retry/permission/scope tests. |
| T-25 | PASS | Recovery view giữ valuation basis, blocker/next action và giải thích reconstructed PnL=0 khác live PnL; hai nguồn không bị trộn. |
| T-26 | PASS | Filtered cursor chains, on-demand selected order/fills, independent errors, recorded progress/basis/fees; page/hook tests. |
| T-27 | PASS | Activity có filters/cursor, pinned session detail, command receipt inspect và tra audit theo operation/actor; xem `ActivityPage.tsx` và integration tests. |
| T-28 | PASS | Account version/conflict flows giữ nguyên; audit filters được nối đúng endpoint và quyền đọc. |
| T-29 | PASS | Daily reporting giữ range/timezone/fees/funding/balances; controller performance panel được bật độc lập để lỗi panel không che daily data. |
| T-30 | PASS | Exact decimal calculations/formatting, gaps/null, timestamps, stale retry, source isolation và range tests. Chart numbers chỉ để vẽ. |
| T-31 | PASS | Chỉ dùng series contract; không tạo OHLCV/reference history/benchmark từ fills. |
| T-32 | PARTIAL | Unit/controller tests và integration record cover các case đã liệt kê về failure/retry/order/scope/reload. Chưa có bằng chứng đầy đủ cho concurrency/restart và mọi side effect độc lập; không suy độ phủ từ tỷ lệ dòng. |
| T-33 | PARTIAL | Browser suite hiện có kiểm tra auth/workspace/receipt/feedback. Chưa có trọn luồng UI Start → quotes → fill/thiếu lệnh → Stop/residual; Switch superseded và cleanup blocked/repair ở browser còn thiếu. Component/controller tests không thay các case browser này. Chính sách hiện tại hoãn viết mới E2E khi UI còn biến động. |
| T-34 | PARTIAL | Light/dark/mobile, keyboard/focus và reduced-motion có component/source checks. Theo yêu cầu, a11y suite bị bỏ qua; contrast và visual review/baseline vẫn chưa được kiểm tra. |
| T-35 | PARTIAL | API check, lint, typecheck, unit/coverage và build có bằng chứng mới trong [UI integration](UI-COMPLETION-INTEGRATION.md). E2E chưa chạy được do pnpm bị registry DNS; a11y được bỏ qua theo yêu cầu; visual baseline chưa có. |

## Các sửa lỗi bảo toàn ngữ nghĩa trong audit

- Risk Guards không báo “An toàn/Cho phép” khi thiếu observation và không vẽ các tỷ lệ 8%, 12%, 15%, 80% hay 90% không có nguồn policy.
- Session, Disconnect, recovery Check/Reset chỉ thông báo đã nhận receipt sau POST; completion do receipt/business proof quyết định.
- Stop chỉ có một nơi phát thông báo chấp nhận. Error inline không bị lặp lại trong error toast.
- HALT banner giữ backend detail/next_action; không tự biến risk stop thành sự cố kỹ thuật có thể recovery.

Các test regression bổ sung khóa các hành vi này. Chúng không tự đóng các tiêu chí PARTIAL/CONTRACT phía trên.

## Lệnh kiểm chứng

Từ `dashboard/`:

```bash
pnpm run api:check
pnpm run lint
pnpm run typecheck
node node_modules/vitest/vitest.mjs run --coverage --coverage.reportsDirectory=/private/tmp/hummingbot-acceptance-coverage --reporter=verbose
pnpm run check
pnpm exec playwright test --grep-invert @visual --reporter=list --timeout=30000
pnpm exec playwright test e2e/accessibility.spec.ts --reporter=list --timeout=30000
```

Node 26.8.2/pnpm 12.4.1 là runtime local; Node 22 chưa được xác minh. Không sửa package/dependency/backend hoặc commit trong audit. Gate nào bị chặn bởi môi trường/permission được ghi SKIP, không thay bằng PASS từ lần chạy cũ.

## Kết quả audit local

Tổng kết sau contract review: **29 PASS, 5 PARTIAL, 1 CONTRACT**. Các task đã PASS được tick trong [TODO.md](../TODO.md); T-32–35, T-07 verification và T-09 contract vẫn mở. Đây là trạng thái của 35 task, không phải tỷ lệ coverage. Các mục PARTIAL gồm cả thiếu implementation và thiếu bằng chứng đúng lớp test; chi tiết nằm trong từng hàng.

| Kết quả | Command / phạm vi | Bằng chứng |
|---|---|---|
| PASS | `pnpm run api:check` | Schema artifact đồng bộ. |
| PASS | `pnpm run lint` | Không lỗi hoặc warning. |
| PASS | `pnpm run typecheck` | Strict TypeScript không có diagnostic. |
| PASS | `node node_modules/vitest/vitest.mjs run --coverage --coverage.reportsDirectory=/private/tmp/hummingbot-acceptance-coverage` | 83 files / 399 tests PASS; statements/lines 90.83%, branches 81.72%, functions 96.55%; không hạ threshold. |
| PASS | `git diff --check` | Không lỗi whitespace trong tracked diff. |
| SKIP | `pnpm run check` trong audit này | Hai lần requested execution không chạy: dịch vụ automatic approval review bị ngắt kết nối rồi trả 503/cooling down. Không phải kết luận command không an toàn, cũng không phải PASS từ lần trước. |
| SKIP | `pnpm exec playwright test --grep-invert @visual --reporter=list --timeout=30000` | Lượt thử hiện tại bị pnpm dừng ở bước lấy package từ registry do DNS lookup failure; E2E chưa khởi chạy. Lượt 30/30 cũ không thay T-33 đầy đủ. |
| SKIP | `pnpm exec playwright test e2e/accessibility.spec.ts --reporter=list --timeout=30000` | Bỏ qua theo yêu cầu; lượt 4/4 cũ có ngoại lệ contrast và không chứng nhận WCAG đầy đủ. |
| SKIP | `pnpm run test:visual`, contrast riêng và Node 22 | Chưa có baseline/review hoặc bằng chứng runtime tương ứng. |

Khi dịch vụ duyệt local execution hoạt động lại, rerun nguyên các command/selector/timeout ở trên. Không cần quyền hay môi trường production để tiếp tục các gate frontend này. Không thêm mới E2E workflow khi chính sách UI hiện tại vẫn đang hoãn việc đó.

## Follow-up T-07/T-09/T-19

| Kết quả | Command / phạm vi | Bằng chứng |
|---|---|---|
| PASS | `node node_modules/vitest/vitest.mjs run src/features/overview/model.test.ts src/features/overview/completion-model.test.ts src/features/overview/WorkingOrdersSection.test.tsx src/features/overview/OperatingTargetsCard.test.tsx src/features/overview/RiskGuardsCard.test.tsx src/pages/HomePage.completion.test.tsx src/pages/HomePage.test.tsx src/features/connections/connections.test.ts src/features/connections/useConnectionsController.test.tsx src/core/api/queries.test.ts src/pages/AdminPage.test.tsx src/features/operational-context/useOperationalController.test.tsx` | 12 files / 61 tests pass. |
| PASS | `node node_modules/typescript/bin/tsc --noEmit`; `node --import tsx scripts/generate-api.ts --check`; targeted ESLint; `git diff --check` | Strict typecheck, checked-in OpenAPI→TypeScript artifact, touched frontend lint and whitespace pass. |
| PASS | `python3.11 -m py_compile hummingbot/core/mm_liquidity.py hummingbot/core/mm_quote_activity.py hummingbot/cli/admin_models.py test/hummingbot/core/test_mm_liquidity.py test/hummingbot/cli/test_admin_quote_activity.py` | Backend source and tests compile. |
| SKIP | `python3.11 -m pytest -vv test/hummingbot/core/test_mm_liquidity.py test/hummingbot/cli/test_admin_quote_activity.py` | Python environment has no pytest module. |
| SKIP | `python3.11 -m hummingbot.cli.swagger --check` | Python environment has no pandas, required by the Hummingbot import path. Dashboard API check validates the manually updated checked-in artifacts, not server Swagger freshness. |

T-07 remains open until backend tests and Swagger source verification run in a provisioned Python environment. T-09 remains open because eligible and restriction intervals cannot be classified from current observations. T-19 is complete for this local/mock scope; hosted credential and exchange acceptance remain separate.
