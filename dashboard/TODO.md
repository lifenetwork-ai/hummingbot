# TODO — LIFE MM Dashboard

## Mục tiêu và cách sử dụng

**Bot đang làm gì? Có làm đúng nhiệm vụ không? Nếu không, cần làm gì?**

Danh sách phục vụ phase vận hành: chạy, quan sát, nhận biết vấn đề, can thiệp và dừng/phục hồi an toàn. Giữ cấu hình chi tiết qua YAML/deploy và cân bằng/nạp/rút tài sản thủ công.

## Cách chia việc UI và logic/hệ thống

Thực hiện theo [quy ước phối hợp](docs/COORDINATION.md): hai người cùng một backlog T-01–35, chia phần triển khai và tích hợp theo từng workflow.

- **UI:** component trình bày, form/tương tác, copy, responsive/theme, keyboard/focus và test hiển thị với fixture.
- **Logic/hệ thống:** API/hooks, mapping dữ liệu, auth/cache, ngữ cảnh/quyền, validation nghiệp vụ, receipt/retry/persistence và test xử lý.
- **Tích hợp:** chọn một người trong hai bên làm người tích hợp cho mỗi workflow; người đó nối controller với View, route/provider và thu bằng chứng E2E.

Trước khi làm song song, chốt interface TypeScript, nguồn field/đơn vị, trạng thái, callbacks và fixture cases. [Bảng phân chia backlog](docs/COORDINATION.md#phân-chia-backlog) chỉ rõ đầu ra của từng bên; [mẫu đầu việc](docs/COORDINATION.md#mẫu-đầu-việc-bàn-giao) ghi người phụ trách, file sở hữu và trạng thái UI/logic/tích hợp riêng.

Checkbox dưới đây là **hoàn tất workflow đã tích hợp**. Một bên hoàn thành hoặc UI chạy bằng fixture chưa đủ để tick. Giữ nguyên ID và tiêu chí nghiệp vụ khi chia đầu việc.

### Bàn giao phần logic T-05–31

Các module logic, kiểu dữ liệu và callback nằm tại [Logic interfaces](docs/LOGIC-INTERFACES.md). [Logic verification](docs/LOGIC-VERIFICATION.md) ghi ma trận test, bằng chứng local/mock và các gate còn mở. Phần này tách trạng thái logic khỏi checkbox nghiệm thu workflow bên dưới; UI, route và shell do lane UI/tích hợp sở hữu.

| Phạm vi logic | Nơi triển khai |
|---|---|
| T-05–10, T-16–17 | `src/features/overview/` |
| T-11–15, T-18 | `src/features/session-control/`, `src/features/reconciliation/`, `src/core/api/operation-journal.ts`, `operation-feedback.ts` |
| T-19–21 | `src/features/connections/` |
| T-22–25 | `src/features/recovery/` |
| T-26–28 | `src/features/history/`, `src/features/accounts/` |
| T-29–31 | `src/features/reporting/` |

Phạm vi nghiệm thu là T-01–35 trên frontend local/mock. Hosting và nghiệm thu môi trường thật không thuộc backlog này. [Ma trận nghiệm thu từng tiêu chí](docs/ACCEPTANCE-AUDIT.md) là nguồn trạng thái chi tiết; có module hoặc gate PASS chưa đủ để đóng task còn thiếu hành vi.

Nguồn đối chiếu:

- [Yêu cầu frontend](../docs/frontend-requirements.md): nhu cầu, ưu tiên và tiêu chí nghiệp vụ.
- [FE integration](../docs/fe-integration.md): contract và ngữ nghĩa vận hành chi tiết.
- [OpenAPI](../docs/api/openapi.json) và [API foundation](docs/API.md): kiểu dữ liệu, adapter và hooks.
- [Use case](../docs/usecase.md): UC-01 đến UC-10.
- [Architecture](docs/ARCHITECTURE.md), [System feedback](docs/FEEDBACK.md), [Testing](docs/TESTING.md), [Deployment](docs/DEPLOYMENT.md).

**Phạm vi recovery:** backend có recovery dành cho Admin theo luồng check → review → reset có kiểm chứng. Requirement dùng phạm vi này; không có reset tùy ý hoặc tự chạy lại. Theo [Admin recovery](../docs/fe-integration.md#admin-recovery) khi triển khai.

Checkbox hoàn thành chỉ đánh dấu khi có code và bằng chứng kiểm tra tương ứng. Bảng hiện trạng dưới đây ghi nhận thành phần có trong source, không thay cho nghiệm thu môi trường thật. Mỗi mục còn mở cần có loading/empty/error/stale, quyền thao tác, test hành vi và tiêu chí kết thúc. Không hard-code tài khoản, profile, ngưỡng triển khai hoặc dữ liệu giả vào runtime.

## 0. Nền tảng đã có — tái sử dụng

| Thành phần trong source | Phạm vi đã có | Việc còn phải xác minh/tích hợp |
|---|---|---|
| `src/core/api/` | Generated DTO, operation adapter, query/hooks, receipt polling, invalidation, phân trang và cache theo session | Kết nối vào tính năng vận hành; giữ receipt qua reload; kiểm tra hợp đồng bằng `api:check` |
| `src/core/auth/`, `src/features/auth/`, `LoginPage` | Google sign-in, identity, token memory/localStorage, khôi phục và bảo vệ route và quản lý phiên frontend | Nghiệm thu Google thật, quyền thay đổi, API origin và gateway |
| Shared components/theme/feedback | LifeAI light/dark, dialog, banner, inline feedback, toast adapter | Áp dụng vào workflow; lỗi polling không tạo toast liên tục |
| CI và Cloudflare Pages | Kiểm tra/build và luồng tự deploy độc lập backend | Điền account/project/branch/token sau; chưa coi hosting là nghiệm thu vận hành |

Các route Overview, Session, Orders, Activity, Statistics và Admin đã được nối controller. Trạng thái nghiệm thu của từng tiêu chí nằm tại [ACCEPTANCE-AUDIT.md](docs/ACCEPTANCE-AUDIT.md); không suy hoàn tất toàn bộ backlog từ số lượng test PASS.

## 1. P0 — Chốt contract và ngữ cảnh làm việc

**Hoàn tất triển khai và nghiệm thu local/mock T-01–04**, gồm lưu Google ID token trong localStorage và khôi phục qua `/access/me` khi còn hạn. Không thêm backend session hoặc gia hạn token.

Bằng chứng tích hợp trước đợt audit: Node 26.8.2/pnpm 12.4.1; API/schema, lint/types/coverage/build (396 tests), 30 browser tests và 4 a11y checks PASS. Kết quả này không thay nghiệm thu từng tiêu chí. Bằng chứng mới và giới hạn nằm tại [ACCEPTANCE-AUDIT.md](docs/ACCEPTANCE-AUDIT.md).

- [x] **T-01 — Đồng bộ requirement.** [Requirement](../docs/frontend-requirements.md) đã đồng bộ phạm vi Admin recovery, operator summary, ngưỡng cảnh báo thanh khoản và hành vi đăng nhập lại theo integration/OpenAPI. Bằng chứng tài liệu: FE-02, FE-05–06 và bảng nghiệm thu; không thay nghiệm thu runtime của các tính năng tương ứng.
- [x] **T-02 — Kết nối đúng môi trường.** Xác định public Google client ID, API base URL và đường truy cập backend; nhận diện bot, sàn/cặp, account đã xác minh, Demo/Live và người dùng. Không suy môi trường từ URL hoặc cung cấp nút chuyển Demo/Live. Thiếu/mismatch phải hiện rõ.
- [x] **T-03 — Hoàn thiện phiên người dùng.** Gắn mọi route vận hành với auth; 401/expiry/logout xóa phiên/cache, 403 cập nhật identity/quyền. Đăng nhập lại khôi phục deep link an toàn và theo dõi thao tác cũ, không tự gửi lại mutation. Google ID token được lưu localStorage theo API/Google client ID để khôi phục qua `/access/me` khi còn hạn; đồng bộ logout giữa tab. Không persist exchange secrets, recovery tokens hoặc mutation body chứa bí mật; body không bí mật cho same-key retry theo T-13 được scope và kiểm tra riêng.
- [x] **T-04 — Quyền và khả năng thao tác.** Dùng permissions kết hợp actions/capabilities backend. Viewer chỉ quan sát; Operator điều khiển trong quyền; recovery và quản lý tài khoản đúng quyền Admin. Có giải thích khi thao tác bị chặn; POST vẫn có thể từ chối nếu trạng thái đổi.

Nguồn: [Connection](../docs/fe-integration.md#connection-and-openapi), [Accounts](../docs/fe-integration.md#accounts-roles-and-audit). UC-01, UC-08.

## 2. P0 — Overview: biết bot đang làm gì

- [x] **T-05 — Thay starter home bằng Overview vận hành.** Hiện nhiệm vụ/profile thực tế, thị trường, trạng thái yêu cầu và trạng thái quan sát, thời hạn/chu kỳ Random. Phân biệt service, connection, session readiness và working orders; không gộp thành một trạng thái “chạy tốt”.
- [x] **T-06 — Operator summary.** Dùng `operator.state/label/detail/next_action`, `observation`, `actions` và `recovery`; hiện vấn đề, phía/thị trường ảnh hưởng và hành động tiếp theo. Dữ liệu cũ hoặc lệch phiên không dùng để kết luận tình trạng hiện tại.
- [ ] **T-07 — Lệnh thực tế từng thị trường.** Spot/perpetual tách riêng; BUY/SELL riêng; hiện giá, lượng còn lại, own notional, own spread và pending create/cancel. Phân biệt xác nhận từ sàn với tracker/recorded history; không gọi lệnh bot là toàn bộ order book.
- [x] **T-08 — Thiếu lệnh và refresh.** Hiện pending/cooldown/guard/inventory/balance và hướng xử lý có bằng chứng. Phân biệt refresh/bổ sung bình thường với chờ kéo dài; không dùng tuổi lệnh để kết luận engine chậm. Sau partial/full fill, xác minh lệnh hiện tại; không tự gán quan hệ fill → replacement khi contract chưa có.
- [ ] **T-09 — Ngưỡng vận hành.** Hiện cảnh báo thiếu lệnh bất thường trên 20 giây, điều tra trên 60 giây; mục tiêu ít nhất 99% sampled two-sided availability/30 phút theo từng thị trường và điều kiện eligible. Hiện coverage toàn phiên, unknown và thời gian observed nhưng không eligible; lịch sử guard-restricted và eligible two-sided rolling chưa có trong contract. Không hứa đạt mục tiêu từ mẫu ngắn; không tự nới guard/restart. Kiểm tra deployment đã chọn warning threshold phù hợp, không coi default backend 120 giây là 20 giây.
- [x] **T-10 — Freshness và polling.** Timestamp gốc, stale/unknown/session mismatch; giữ last-good data có nhãn. Tái sử dụng lịch polling/hook hiện có, pause tab ẩn, chia sẻ request và backoff theo server. Lỗi report không chặn status/Stop; response cũ không ghi đè ngữ cảnh mới.

Nguồn: [Health](../docs/fe-integration.md#service-health-and-working-orders), [Quotes](../docs/fe-integration.md#reviewing-quote-availability), [Operating targets](../docs/fe-integration.md#initial-operating-targets-approved-2026-11-09), [Polling](../docs/fe-integration.md#polling-and-availability). UC-02, UC-03, UC-04, UC-08.

## 3. P0 — Chạy/chuyển/dừng và theo dõi đến kết quả

- [x] **T-11 — Form và review nhiệm vụ.** Chỉ Market, Fixed profile/Random và Duration theo catalogue; references/weights/budget/spread/guards chỉ đọc. Until stopped chỉ khi được quảng bá, không gửi duration cũ. Hiện cấu hình ghim của phiên riêng với catalogue hiện tại; Switch tạo phiên thay thế, không mặc định đóng exposure.
- [x] **T-12 — Gửi lệnh và receipt.** Dùng current control version, session ID và catalogue hash đã review. Theo dõi accepted/applying/succeeded/failed/superseded; 202 chưa hoàn tất. Sau receipt thành công vẫn đọc status và lệnh thực tế.
- [x] **T-13 — Timeout, conflict và reload.** Giữ metadata không bí mật của thao tác theo API/bot/người dùng; lưu body không chứa secret, key và receipt ID phù hợp loại thao tác. Timeout theo dõi receipt hoặc retry cùng body/key. Conflict refresh/review; quyết định mới mới dùng key mới. Không coi guard HTTP in-flight là đã chặn trùng toàn bộ vòng đời operation.
- [x] **T-14 — Stop luôn có thể truy cập khi được phép.** Kể cả Start/Switch đang chờ; refresh để tác động đúng phiên/version. Xác nhận ngắn “Stop MM; positions remain open”. Sau dừng giữ lệnh, vị thế và timestamp; lỗi/unknown không báo dừng xong. Không suy hết lệnh từ process stopped hoặc history rỗng.
- [x] **T-15 — Phản hồi thao tác.** Field validation dùng inline, tình trạng kéo dài dùng banner, phản hồi chủ động dùng toast phù hợp. Một thao tác không thông báo trùng; giữ progress/receipt truy cập được sau toast. Có reason/request ID và hướng xử lý đúng `retry`, không retry mọi 409.

Nguồn: [Session commands](../docs/fe-integration.md#session-command-workflow), [Stop](../docs/fe-integration.md#stop-and-held-exposure), [Feedback](docs/FEEDBACK.md). UC-01, UC-07, UC-09.

## 4. P0 — Vốn, rủi ro và đối soát

- [x] **T-16 — Số dư và allocation.** Hiện cash/available cash/equity/available equity đúng đơn vị và observation; ví chung chỉ tính một lần. Phân biệt allocation với tài sản thực, notional/exposure với tài sản spot sở hữu; LONG/SHORT tách riêng.
- [x] **T-17 — Giới hạn và hành động.** Inventory, exposure, drawdown, outflow, collateral và guards theo phạm vi policy. Hiện tài sản/phía thiếu và vùng hạn chế; hướng dẫn bổ sung/cân bằng thủ công. Không tự tính khuyến nghị nạp vốn tổng quát hoặc gọi controller drawdown là lỗ toàn bộ Foundation.
- [x] **T-18 — Đối soát chỉ đọc.** Gửi check theo availability/cooldown; theo receipt, hiện thời điểm/phạm vi/mismatch/incomplete. Receipt succeeded chưa có nghĩa dữ liệu khớp. Không tự sửa/hủy/đóng vị thế; thiếu connector phải hiện chưa xác minh được.

Nguồn: [Policy](../docs/fe-integration.md#policy-observations), [Balances](../docs/fe-integration.md#shared-balances-positions-and-performance), [Reconciliation](../docs/fe-integration.md#observational-reconciliation). UC-05, UC-06, UC-07, UC-08.

## 5. P0 — Thiết lập kết nối và xử lý cleanup

- [x] **T-19 — Trạng thái/cấu hình kết nối.** Theo nguồn environment/managed, input names và server actions. Managed Set yêu cầu Stop đã acknowledged; nhận complete bundle, xóa secret input sau gửi, poll credential receipt riêng. Connected dựa trên trạng thái server chỉ trả về khi owner/version khớp và còn mới; Set không tự Start.
- [x] **T-20 — Disconnect riêng với Stop.** Xác nhận bot-wide stop/cancel/đóng managed perpetual exposure ở market rồi gỡ slot được chọn; phí/slippage, spot holdings còn lại, key tại OKX chưa bị revoke. Theo root receipt và bốn final proofs; slot khác không tự bị gọi Disconnected.
- [x] **T-21 — Cleanup bị chặn và cấu hình lại.** Hiện blocked stage, reason, next retry và actions. Retry/repair theo root operation/current versions, đúng account; không force-delete. Tách slot credential version, control version và operation version; slot đã revoke vẫn giữ version khi Set lại. Không lưu secret để retry tự động.

Nguồn: [Managed connection](../docs/fe-integration.md#managed-connection-integration), [Disconnect](../docs/fe-integration.md#disconnect-ux). UC-01, UC-07, UC-08.

## 6. P0 — Recovery có kiểm chứng cho Admin

- [x] **T-22 — Entry và điều kiện recovery.** Dùng `manage_recovery` và availability thực tế; non-Admin hướng liên hệ Admin. Không HALT thì “No recovery needed”. Stop và chờ acknowledgement trước check; khóa Start/Switch và đổi connection khi recovery đang chạy.
- [x] **T-23 — Check và review.** Gửi check với version/key, poll exact operation. Check succeeded nhưng blocked/can_recover=false vẫn chưa phục hồi. Khi review_required, hiện proposed before/after, verification, blocker và hạn preview; chỉ bật xác nhận khi can_recover.
- [x] **T-24 — Xác nhận và kết thúc.** Reset dùng check ID, confirmation token, version và key mới; không log token, không gửi force/financial overrides. Preview hết hạn/drift/owner change cần check mới. Chỉ succeeded + recovered mới báo hoàn tất; refresh status/positions và giữ trading stopped, không tự Start.
- [x] **T-25 — Giới hạn phục hồi.** Hiện blocker/next action, không biến risk stop thành lỗi kỹ thuật recoverable. Recovery không cancel orders/close exposure/nới risk. Giá trị PnL tái dựng bằng 0 không có nghĩa hoàn lỗ hoặc live PnL bằng 0; giữ valuation basis và dữ liệu live riêng.

Nguồn: [Admin recovery](../docs/fe-integration.md#admin-recovery). UC-08, UC-07.

## 7. P1 — Tra cứu diễn biến và quản trị

P1 làm sau vòng điều khiển cốt lõi nhưng vẫn cần trước nghiệm thu phạm vi tra cứu của bản đầu. Quản lý account/kết nối có thể dựng sớm nếu cần onboarding môi trường.

- [x] **T-26 — Orders/fills và order detail.** Bộ lọc thị trường/trạng thái, cursor pages và drawer tải theo nhu cầu; recorded fills/progress basis, fee currency, partial fill và unknown/zero đúng nghĩa. Không poll mọi trang hoặc gọi per-row; không coi `/orders?state=open` là exchange-wide snapshot.
- [x] **T-27 — Activity/history.** Sessions, pinned configuration, events, commands và operation links; truy actor qua audit. Filter đổi thì reset cursor; tìm được thao tác pending/failed và vị thế còn tồn đọng để bàn giao. Không hứa lịch sử mọi khoảng thiếu lệnh hoặc chức năng ghi chú chưa có API.
- [x] **T-28 — Quản lý account và audit.** Admin invite/đổi role/enable với version và xử lý bảo vệ last bound Admin; invitation khác đăng nhập thành công. Audit chỉ đọc, đúng endpoint/filters/cursor. Bootstrap disable thuộc triển khai, không tạo nút API bật/tắt Basic.

Nguồn: [Orders/fills](../docs/fe-integration.md#orders-and-fills), [History](../docs/fe-integration.md#session-and-command-history), [Accounts](../docs/fe-integration.md#accounts-roles-and-audit). UC-03, UC-08, UC-09, UC-10.

## 8. P2 — Báo cáo vận hành hỗ trợ

- [x] **T-29 — Daily statistics.** Buy/sell volume, fills, paid fees/rebates và funding riêng; from inclusive/to exclusive, reporting timezone, giới hạn range. Shared balance series chỉ một lần; controller performance ở phần phụ, có phạm vi.
- [x] **T-30 — Dữ liệu tài chính và chart.** Decimal string cho payload/tính toán, round khi hiển thị; chart number chỉ là biểu diễn. Missing days thành gaps, không bù zero/interpolate. HTTP 200 stale vẫn “Statistics updating”, giữ timestamp và retry delay; thiếu observation thì unavailable, không báo bot dừng.
- [x] **T-31 — Giới hạn biểu đồ.** Chỉ dựng chart từ series được contract cung cấp. Không tạo OHLCV/reference price history hoặc benchmark đầu tư từ fills/daily stats; dữ liệu client tự thu phải ghi rõ không có lịch sử qua reload.

Nguồn: [Daily statistics](../docs/fe-integration.md#daily-statistics-fees-and-funding), [Financial semantics](../docs/fe-integration.md#financial-and-chart-semantics). UC-10. Chưa cần báo cáo tổng hợp năm mục tiêu để chạy luồng vận hành cốt lõi.

## 9. Nghiệm thu và phát hành

- [ ] **T-32 — Ma trận test theo workflow.** Với từng mục liên quan, đối chiếu test hiện có trước khi thêm: success, validation/permission, transient/persistent dependency failure, retry, timeout unknown, duplicate/idempotency, concurrent operators, response ordering, reload/restart và tác động độc lập. Mock không thay bằng chứng exchange/Google thật.
- [ ] **T-33 — Browser hành vi vận hành.** Login → đúng identity/context → review Start → receipt → observed quotes → fill/thiếu lệnh → can thiệp → Stop → kiểm tra residual exposure. Thêm Switch bị supersede, cleanup blocked/repair và recovery check/review/reset; xác minh không tự Start hoặc mutation replay.
- [ ] **T-34 — Khả dụng và UX.** Light/dark/mobile, keyboard/focus, reduced motion và feedback không lặp. Giữ Stop/status khi report lỗi. Kiểm tra contrast riêng; axe suite hiện có ngoại lệ không đủ kết luận toàn bộ WCAG.
- [ ] **T-35 — Quality gates.** Chạy từ `dashboard/`: `pnpm run api:check`, `pnpm run check`, `pnpm run test:e2e`, `pnpm run test:a11y`; visual chỉ sau khi tạo/review/commit baseline. Ghi PASS/FAIL/SKIP cùng command/evidence; check chưa chạy không được tick hoàn thành.

Nguồn: [Browser acceptance](../docs/fe-integration.md#browser-acceptance-checklist), [Deployment](docs/DEPLOYMENT.md).

## Trình tự thực hiện và điều kiện hoàn tất

Phạm vi, UI/logic bàn giao, phụ thuộc và tiêu chí hoàn tất của cả sáu đợt nằm tại [UI plan — Lộ trình triển khai](docs/UI-STRUCTURE-PLAN.md#7-lộ-trình-triển-khai-tổng-thể). [Logic handoff — Bàn giao theo đợt](docs/LOGIC-HANDOFF.md#bàn-giao-logic-theo-đợt-triển-khai) cung cấp nguồn dữ liệu, callbacks và fixture cần chuẩn bị. Các tài liệu này cụ thể hóa T-01–35, không tự đánh dấu task hoàn tất.

### Checklist bàn giao logic cho UI

Các mục sau cụ thể hóa task hiện có; không thay tiêu chí nghiệm thu hoặc đánh dấu workflow đã hoàn tất. Logic sở hữu controller, validation, quyền, receipt, retry và persistence; UI sở hữu trình bày và state tương tác. Người tích hợp nối hai phần theo COORDINATION.md.

| Task hiện có | Đầu việc logic cần bàn giao | Điều kiện bàn giao |
|---|---|---|
| T-02–04, T-10 | Chốt props/callback Đợt 1 từ OperationalContextModel; giữ danh sách accounts, process/owner environment và trạng thái/freshness riêng của status/balances | UI render được nhiều account, unknown/mismatch và lỗi/stale từng nguồn; action có allowed/reason/detail; không suy quyền từ role |
| T-04, T-19–25, T-28 | Bảng quyền và availability cho từng workflow, gồm Connections cho Operator/Admin và Recovery/Accounts cho Admin | UI có quyết định truy cập/thao tác và lý do bị chặn; backend vẫn kiểm tra từng request |
| T-12–15, T-18, T-20–24 | Controller và điều kiện hoàn tất riêng cho command, reconciliation, credential cleanup và recovery | Phân biệt receipt terminal với kết quả nghiệp vụ; recovery check giữ bước review/blocker; reconciliation giữ matched/mismatch/incomplete; refresh đúng nguồn và không chuyển trang tự động theo một quy tắc chung |
| T-13 | Tách phục hồi receipt reference đã có khỏi same-body/key retry chưa nhận receipt ID; scope theo API/principal/bot | Reload/relogin không replay mutation; không lấy thao tác của người khác; chỉ giữ metadata/body không bí mật phù hợp từng loại operation |
| T-09–10 | Model coverage theo từng market: cửa sổ thực đo, eligible/total, unknown/restricted và trạng thái chưa đủ 30 phút | Không kết luận đạt mục tiêu từ mẫu ngắn hoặc eligible denominator bằng 0; giữ timestamp và ngưỡng deployment |
| T-16, T-30 | Bàn giao decimal string, đơn vị, nguồn và nullable semantics cho từng metric | UI format theo loại số liệu, giữ khả năng xem giá trị chính xác; số nhỏ không bị diễn giải thành zero do làm tròn |

### Tích hợp UI và logic

Các route vận hành dùng controller và API adapter thay cho việc tự dựng mutation trong component trình bày. [Bằng chứng tích hợp](docs/INTEGRATION-VERIFICATION.md) ghi command, ma trận case, runtime và giới hạn kiểm chứng. Checkbox workflow vẫn yêu cầu nghiệm thu theo tiêu chí tương ứng; mock không đóng các gate triển khai và môi trường thật.

| Route | Điểm nối logic | Các ngữ nghĩa được giữ |
|---|---|---|
| `/` | `useOverviewController`, operational provider và asset ribbon | Requested/observed, freshness riêng từng nguồn, coverage từng market, unknown không thành zero/Flat hoặc “An toàn” |
| `/session`, Stop toàn cục | `useSessionControl`, pinned session configuration và command receipts | Review version/catalogue/session; Start khác Switch; retry cùng body/key; Stop refresh target và giữ exposure |
| `/admin` | Connections, Recovery, Accounts và audit controllers | Permission không fallback theo role; credential/control/root versions riêng; token recovery ở logic; last-bound-Admin theo backend |
| `/orders`, `/activity` | Cursor history/detail hooks và reconciliation controller | Recorded history khác live orders; detail tải theo chọn; receipt success khác matched; filters reset cursor |
| `/statistics` | Reporting controller, exact decimal summary và chart series | Server timezone, from inclusive/to exclusive, gaps/null, fee currency/paid/rebate riêng và shared balance một lần |

T-07 đã bổ sung per-order live rows và pending create/cancel trong backend/API source; test backend và Swagger regeneration đang chờ Python dependencies. Contract còn thiếu cho T-09 là interval eligibility/restriction provenance để tính rolling two-sided coverage 30 phút. T-19 dùng server `connected` làm bằng chứng owner/version; không cần lộ credential version riêng.

1. T-01–04: chốt contract, context và quyền trên nền auth/API có sẵn.
2. T-05–10 và T-16–17: Overview đọc được tình trạng thật trước khi nối điều khiển.
3. T-11–15 và T-18: vòng Start/Switch/Stop, receipt và đối soát.
4. T-19–25: connection/cleanup và recovery; không lẫn ba loại operation.
5. T-26–28: tra cứu và quản trị cần cho vận hành/bàn giao.
6. T-29–31: báo cáo hỗ trợ, triển khai sau phần cốt lõi.
7. T-32–35: thực hiện xuyên suốt, đóng bằng chứng nghiệm thu frontend trong phạm vi local/mock.

Mỗi checklist cần ghi PR/commit hoặc test evidence khi đóng. Bản vận hành đầu hoàn tất khi người dùng hiểu đúng nhiệm vụ/lệnh, biết vì sao bị chặn, thực hiện và theo dõi điều khiển, thấy vốn/rủi ro sau dừng, xử lý cleanup/recovery theo quyền và tra cứu được diễn biến. Chưa đưa auto-rebalance, chuyển vốn trong app, sửa YAML qua form, full-market analytics hoặc tối ưu đầu tư vào phạm vi này.
