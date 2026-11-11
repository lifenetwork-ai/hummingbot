# Bàn giao logic cho UI — LIFE MM Dashboard

## Phạm vi và trạng thái

Bản đề xuất đầu vào cho thiết kế cấu trúc UI, dựa trên adapter và DTO hiện có. Tên khối dưới đây thể hiện trách nhiệm dữ liệu, chưa chốt tên component, props hoặc layout. Hai bên đối chiếu với cây component UI trước khi triển khai interface TypeScript chi tiết.

Áp dụng [quy ước phối hợp](COORDINATION.md) và tiêu chí [TODO](../TODO.md). Nguồn ngữ nghĩa là [FE integration](../../docs/fe-integration.md); nguồn kiểu là [generated schema](../src/core/api/schema.d.ts). Không xem phần code dang dở là interface đã nghiệm thu.

## Đầu vào cho cấu trúc màn hình

| Trách nhiệm dữ liệu | Operation hiện có | UI cần biểu đạt |
|---|---|---|
| Người dùng và phiên | `adminLogin`, `getAdminIdentity`; `useAuth()` | Chưa đăng nhập, đang xác minh, danh tính/role, lỗi, hết phiên |
| Ngữ cảnh bot và môi trường | `getRuntimeStatus`, `getBalances` | Bot, thị trường, môi trường cấu hình, tài khoản đã xác minh; unknown/mismatch |
| Tình trạng vận hành | `getRuntimeStatus`, `getLiquidity` | Requested/observed session, operator summary, freshness, own quotes và coverage |
| Vốn và rủi ro | `getBalances`, `getPositions`, `getRuntimeStatus` | Shared balances, held exposure, policy blockers và timestamp độc lập |
| Review nhiệm vụ | `listProfiles`, `getSessionConfiguration` | Catalogue hiện tại và cấu hình ghim của phiên; Market/Profile/Duration |
| Điều khiển phiên | `submitCommand`, `getCommand` | Review, submission, receipt, kết quả chưa biết, conflict và trạng thái quan sát sau thao tác |
| Đối soát | `requestReconciliation`, `getReconciliationById` | Phạm vi/thời điểm check, tiến trình, mismatch/incomplete |
| Kết nối và cleanup | `listCredentialInputs`, `getExchangeCredentialStatus`, các credential mutations và `getCredentialOperation` | Form theo input names, trạng thái kết nối, disconnect/blocked/retry/repair |
| Recovery Admin | `getRecovery`, `checkRecovery`, `resetRecovery`, `getRecoveryOperation` | Điều kiện, check/review/reset, preview hết hạn, blocker và recovered proof |
| Orders/fills | `listOrders`, `getOrder`, `listFills` | Filters, cursor pages và detail theo nhu cầu |
| Activity | `listSessions`, `getSessionEvents`, `listCommands`, `getAdminAudit` | Diễn biến, actor và liên kết thao tác |
| Quản trị account | `listAdminAccounts`, `addAdminAccount`, `changeAdminAccount`, `getAdminBootstrap` | Quyền Admin, invitation/role/active, conflict và bảo vệ Admin cuối |
| Báo cáo | `getDailyStatistics`, `getPerformance` | Date/timezone, series có sẵn, gaps, stale/updating và phạm vi số liệu |

Các nguồn có vòng đời độc lập cần trạng thái riêng. Chẳng hạn báo cáo lỗi không được làm biến mất status hoặc Stop khi vẫn được phép.

## Đợt 1: nguồn field cho T-01–04

| Nội dung | Nguồn đã có | Quy tắc bàn giao |
|---|---|---|
| Danh tính | `useAuth().identity`, DTO `PrincipalResponseOutput` | `id`, email nullable, `role_label`, role và permissions; không coi email là ID ổn định |
| Phiên | `useAuth().status/error/login/logout` | Auth quản lý token trong memory/localStorage và khôi phục qua /access/me; View không nhận token hoặc API session |
| Bot và thị trường | Status `bot_id`, `markets` | Hiển thị market/connector/pair theo dữ liệu; thiếu dữ liệu phải biểu đạt rõ |
| Môi trường cấu hình | Status `process_identity`, `owner_identity` với `trading_environment` | `live/demo/unknown`; basis là process configuration, không phải bằng chứng xác minh tài khoản sàn |
| Tài khoản sàn | Balances `accounts` | Giữ `identity_verified`, environment và account ID; không tự chọn phần tử đầu khi có nhiều account |
| Khả năng thao tác | Status `actions`; DTO `ActionAvailabilityOutput` | Có `allowed`, `detail`, nullable `reason_code`; kết hợp permission tương ứng, không chỉ xét role |
| Freshness/ngữ cảnh | Status `observation`, `server_time`; timestamps/stale của balance observations | Giữ dấu thời gian và giới hạn bằng chứng; nhận HTTP thành công chưa đủ kết luận dữ liệu hiện tại |

Việc map từng action sang permission và quy tắc chặn theo observation cần được ghi cụ thể trong controller/test trước khi nối nút thao tác. Thiếu action/capability không được tự hiểu là được phép.

Route và navigation cần theo từng permission: `operate` cho điều khiển phiên, `manage_credentials` cho quản lý kết nối, `manage_accounts` cho account và `manage_recovery` cho recovery. Backend hiện cấp `manage_credentials` cho Operator và Admin; audit cho các role có quyền đọc. Không gom mọi chức năng quản trị vào một route chỉ cho Admin rồi chặn mất quyền đã được cấp. Stop luôn dễ truy cập nhưng chỉ bấm được khi permission và backend availability cho phép. Reporting timezone và hạn preview recovery theo contract/dữ liệu backend, không hard-code trong View.

## Provider và consumer hook

`src/features/operational-context/OperationalProvider.tsx` gọi `useOperationalController()` một lần, bọc protected workspace, rồi `useOperationalContext()` cho shell/route consumers đọc model từ provider. Provider không mount trên Login/UI Kit. `src/app/OperationalShell.tsx` bố trí `ContextBar` và protected route outlet. `HomePage` chỉ compose nội dung Overview.

`ContextBar` và page View không gọi `useAuth`, `useAdminQuery`, `getAppConfig` hoặc đọc URL để dựng context. Provider map auth identity và các query shared sang model; query cache TanStack vẫn là nguồn dữ liệu server. Không đồng bộ lại model đó vào Zustand hay store thứ hai.

## Hình thức interface cần thống nhất

### Interface logic ngữ cảnh được provider cung cấp

`src/features/operational-context/context.ts` xuất `OperationalContextInput`, `OperationalContextModel`, `ContextSource`, `SessionAction`, `ActionDecision` và hàm thuần `deriveOperationalContext`. Controller hook nối auth với các query status/balances; provider gọi hook này một lần. View có thể nhận model hoặc các field được chọn từ provider, tùy cây component đã thống nhất.

- `status` và `balances` giữ riêng `phase`, `data`, `error`, `refreshing`; nguồn lỗi vẫn có thể giữ last-good data.
- Model giữ danh sách `accounts`, không chọn account mặc định. Mỗi account có dữ liệu gốc, `verification`, `observation` và `environmentMismatch`. `fresh` yêu cầu balances query thành công, owner khả dụng và các entries thuộc snapshot mới nhất không bị stale; không dùng làm kết luận bot sẵn sàng. Account mismatch chỉ kết luận khi có account đã xác minh, balances fresh và status owner hiện khả dụng. Dữ liệu cũ vẫn giữ nguyên nguồn để tra cứu.
- `botId`, `controlVersion`, `processEnvironment`, `ownerEnvironment`, `markets`, `observation` và identity giữ nguồn backend. Unknown không được đổi thành Demo/Live.
- `observationState` là trạng thái trình bày do logic derive: `fresh | stale | unknown | session_mismatch`. UI dùng field này cho nhãn/tone freshness; không tự suy nhãn “Mới” từ raw `observation.stale`. Status query lỗi giữ timestamp cũ nhưng là stale; thiếu observation/timestamp hoặc owner không khả dụng là unknown; session mismatch có trạng thái riêng. Raw observation vẫn được giữ để tra cứu.
- `actions.start/switch/stop` trả `{allowed, reasonCode, detail}`. Chúng yêu cầu permission `operate`, status query thành công và availability của action từ backend. Mã `permission_required`, `status_unavailable`, `availability_unknown` là trạng thái frontend; reason backend được giữ nguyên khi đã có availability.
- Không thêm chặn Stop chỉ từ stale/session mismatch hoặc lỗi balances; backend kiểm tra lại mỗi POST. Khi status query lỗi, cần refresh status trước review thao tác.
- Hook trả `refreshStatus` và `refreshBalances` riêng; không submit mutation hoặc lưu secret.

Phần này định nghĩa interface logic; provider, consumer hook và View cần được nối trong authenticated workspace. Có interface chưa chứng minh workflow đã nghiệm thu.

T-01–04 đã hoàn tất local/mock; trạng thái và bằng chứng kiểm tra nằm tại [TODO](../TODO.md). Contract/runtime bên dưới tiếp tục dùng cho các đợt bàn giao; nghiệm thu môi trường thật thuộc T-37.

### Auth và receipt references

Auth giữ token đang dùng trong memory và lưu cùng token vào localStorage theo API/Google client ID. Reload/tab mới khôi phục qua GET `/access/me` trước khi cấp quyền truy cập workspace; không gọi lại login POST hoặc replay mutation. Logout/hết hạn/401 xóa token phù hợp, storage events đồng bộ các tab. Persistence không gia hạn Google token. Phiên API có `principalId` ổn định từ `/access/me`, độc lập với `cacheIdentity` đổi mỗi lần đăng nhập/quyền đổi. 403 dẫn đến xác minh lại identity; các 403 đồng thời chia sẻ request xác minh. Nếu xác minh thất bại, phiên kết thúc và cache bị thay thế theo lifecycle provider, tránh tiếp tục dùng quyền cũ.

`src/core/api/useTrackedReceipts.ts` cung cấp danh sách reference cho UI theo dõi sau đăng nhập lại. Nó chỉ giữ receipt kind/ID và thời điểm lưu nội bộ, theo API/principal/bot; không giữ request body, idempotency key, token hoặc credential secret. UI chọn reference rồi dùng receipt hook đúng loại để đọc kết quả; không tự replay mutation.

Đọc reference có thể dùng bot identity đã biết trong cache của đúng phiên khi status đang invalidate/refetch hoặc lỗi; trạng thái đó không cấp quyền thao tác mới. API/principal/session đổi sẽ thay cache. Ghi reference mới sau mutation vẫn yêu cầu `trustedBotId` theo status hợp lệ tại thời điểm gửi.

`useReceiptInspection(reference)` chỉ kích hoạt GET receipt được chọn, skip các loại còn lại và trả `phase`, processing `status`, `error` cùng typed `outcome` cho command/credential/reconciliation/recovery. `outcome` giữ stage, report state, reason/blocker và điều kiện recovery tương ứng; không trả raw DTO `data` hoặc confirmation token. View hiển thị tiến trình riêng với kết quả nghiệp vụ; `succeeded` chưa có nghĩa reconciliation matched, connection hiện connected hoặc recovery recovered.

Danh sách cục bộ có giới hạn 50 reference trong 7 ngày; storage có thể bị chặn/xóa và cập nhật đồng thời giữa tab có thể mất reference. Đây là tiện ích quay lại theo dõi; history backend vẫn là nguồn tra cứu. Timeout trước khi nhận ID, lưu body/key để retry cùng request và toàn bộ vòng đời operation thuộc T-13.

### Quy tắc cho các workflow tiếp theo

- **Dữ liệu:** field đã map kèm đơn vị, timestamp và ngữ cảnh; giữ decimal string và nullable semantics.
- **Trạng thái nguồn:** loading/empty/error, dữ liệu hiện có và freshness; error có thể cùng tồn tại với last-good data.
- **Khả năng thao tác:** được phép hay không cùng lý do; độc lập với trạng thái đang gửi/chờ receipt.
- **Callbacks:** ý định người dùng như login, logout, refresh, review, submit, theo dõi receipt; chỉ đưa callback cần cho workflow cụ thể.
- **Operation:** receipt kind/ID, phase và lỗi có thể trình bày; phân biệt HTTP acceptance, terminal receipt và kết quả quan sát.

UI không nhận token, secret persistence, confirmation token recovery để hiển thị hoặc tự tạo version/key. Secret người dùng nhập chỉ tồn tại trong form cần thiết và chuyển qua callback chuyên biệt; không đưa vào fixture/log/storage runtime.

## Bàn giao logic theo đợt triển khai

Phạm vi dưới đây bao phủ cả sáu đợt trong [UI plan](UI-STRUCTURE-PLAN.md#7-lộ-trình-triển-khai-tổng-thể). Đây là đầu việc cần thực hiện, không phải xác nhận controller hoặc workflow đã hoàn tất. Đợt 1 dùng model và các case ở trên; Đợt 2–6 cần bàn giao riêng theo bảng này. Props TypeScript được chốt cho từng workflow trước khi UI và logic triển khai song song.

### Đợt 2 — Overview, vốn và rủi ro (T-05–10, T-16–17)

- **Nguồn:** `getRuntimeStatus`, `getLiquidity`, `getBalances`, `getPositions`; `getSessionConfiguration` khi xem cấu hình ghim.
- **Đầu ra logic:** model nhiệm vụ/requested–observed session, own working orders từng market/side, pending/diagnosis, coverage và vốn/risk theo đúng phạm vi. Mỗi nguồn giữ freshness, timestamp, error và last-good data riêng; account/position coverage không bị gộp thành một cờ chung. Decimal giữ chuỗi, kèm đơn vị và basis.
- **Callbacks:** refresh nguồn tương ứng, mở chi tiết/cấu hình ghim và điều hướng tới workflow được phép. Không đặt lệnh, tự nạp vốn hoặc tự restart khi thiếu lệnh.
- **Fixture/kiểm tra:** running nhưng no_quotes; one-sided do guard/inventory; partial fill và pending replacement; unknown/stale owner; market không hoạt động; ví chung; LONG/SHORT; thiếu valuation. Coverage có đủ/chưa đủ cửa sổ, eligible bằng 0, unknown/restricted và warning deployment khác default.
- **Điều kiện bàn giao:** người dùng đọc được nhiệm vụ → thực tế → chênh lệch → nguyên nhân có bằng chứng → hành động; lỗi balances/liquidity/positions không làm mất status hoặc khả năng Stop hợp lệ. Không tự suy chất lượng toàn thị trường từ own quotes.

### Đợt 3 — Điều khiển phiên và đối soát (T-11–15, T-18)

- **Nguồn:** `listProfiles`, `getSessionConfiguration`, `getRuntimeStatus`, `submitCommand`, `getCommand`, `requestReconciliation`, `getReconciliationById`; đọc lại liquidity/balances/positions khi cần kiểm chứng tác động.
- **Đầu ra logic:** draft/review gắn version, session và catalogue đã review; validation Market/Profile/Duration; khả năng Start/Switch/Stop và lý do chặn. Controller giữ body/key, receipt, lỗi/retry và điều kiện hoàn tất riêng cho command và reconciliation; bao gồm `superseded` cho command.
- **Callbacks:** đổi lựa chọn, yêu cầu review, xác nhận gửi, Stop, theo dõi receipt, retry đúng request hoặc refresh/review quyết định mới, gửi/đọc reconciliation theo cooldown/availability. Confirmation chỉ dùng dữ liệu đã review; backend vẫn có thể từ chối khi state đổi.
- **Fixture/kiểm tra:** timed/until-stopped/Random, catalogue drift/409/422, hai operator, duplicate, timeout trước/sau receipt ID, reload/relogin, response sai thứ tự, lỗi kéo dài rồi phục hồi, Stop khi Start/Switch pending, cancelled orders còn chưa xác minh và residual exposure. Reconciliation tách receipt success khỏi matched/mismatch/incomplete/unavailable.
- **Điều kiện bàn giao:** retry giữ đúng body/key, quyết định mới dùng key mới; reload không tự replay mutation. Receipt succeeded không được dùng làm bằng chứng quotes hiện tại hoặc tài khoản đã flat. Stop target đúng phiên/version và giữ vị thế; giao diện vẫn cho theo dõi kết quả unknown.

### Đợt 4 — Connections, cleanup và recovery (T-19–25)

- **Nguồn:** credential catalogue/status/mutations và `getCredentialOperation`; `getRecovery`, `checkRecovery`, `resetRecovery`, `getRecoveryOperation`; status và các observation dùng xác minh kết quả.
- **Đầu ra logic Connections:** phân biệt environment/managed, permission và server actions; input names và complete bundle; Set chỉ sau Stop acknowledged, không auto-Start. Theo root cleanup operation, blocked stage, retry/repair đúng account và các miền version; chỉ Disconnected khi đủ final proofs. Credential secret không được lưu để retry.
- **Đầu ra logic Recovery:** Admin permission, HALT/preconditions và Stop acknowledgement; check có thể succeeded nhưng blocked. Chỉ review khi `review_required` + `can_recover`; token ở logic, hạn preview từ backend. Reset revalidate, drift/expiry cần check mới; chỉ `succeeded` + `recovered` hoàn tất, trading vẫn stopped.
- **Callbacks:** Set/Disconnect/retry/repair và check/review/confirm reset theo availability riêng; không có force-delete, financial override hoặc nút tự chạy lại.
- **Fixture/kiểm tra:** source environment chỉ đọc, permission đổi, Set sai bundle, stale owner/version, partial cleanup, timeout/reload/root receipt, repair sai account, secret được xóa khỏi form sau gửi. Recovery gồm không cần phục hồi, blocked check, preview hợp lệ/hết hạn/drift, reset thất bại và recovered nhưng không tự Start. Phân biệt Stop giữ exposure với Disconnect đóng managed perpetual exposure và giữ spot.
- **Điều kiện bàn giao:** không lẫn command/credential/recovery receipts; lỗi/unknown giữ nguyên khả năng theo dõi và hướng xử lý. Không báo Connected chỉ từ saved key hoặc báo cleanup xong chỉ từ một stage thành công.

### Đợt 5 — Tra cứu, account và audit (T-26–28)

- **Nguồn:** `listOrders`, `getOrder`, `listFills`, `listSessions`, `getSessionEvents`, `getSessionConfiguration`, `listCommands`, `getAdminAudit`, `listAdminAccounts`, `addAdminAccount`, `changeAdminAccount`, `getAdminBootstrap`.
- **Đầu ra logic:** filters/cursor gắn đúng bot/session và reset khi đổi filter; detail/linked fills đọc theo nhu cầu, không per-row polling. Giữ recorded progress/basis, fee currency, phạm vi và giới hạn history. Accounts dùng Admin permission/version, bảo vệ last bound active Admin; audit chỉ đọc theo quyền backend (`read`), không bị chặn cùng tab Accounts.
- **Callbacks:** đổi filter, trang tiếp, mở detail, chọn session/operation; invite, đổi role/active, refresh/review conflict. Bootstrap status là thông tin triển khai, không tạo mutation bật/tắt Basic auth.
- **Fixture/kiểm tra:** empty/partial history, cursor hết hiệu lực/sai filter, đổi filter khi request cũ pending, linked fills lỗi độc lập, operation pending/failed sau reload; account conflict/last-admin protection/403 và invitation chưa từng login. Audit thiếu actor phải hiện chưa biết, không tự gán.
- **Điều kiện bàn giao:** tìm và theo dõi được phiên/thao tác để bàn giao ca; không coi recorded open orders là exchange snapshot hoặc thiếu lịch sử là không có sự kiện. Accounts và audit được kiểm soát quyền riêng.

### Đợt 6 — Báo cáo ngày (T-29–31)

- **Nguồn:** `getDailyStatistics`, `getPerformance`; chỉ dùng series được contract cung cấp.
- **Đầu ra logic:** date range (from inclusive/to exclusive), reporting timezone và range validation; buy/sell volume, fills, paid fees/rebates, funding perpetual riêng, shared balance series một lần. Giữ original currency/conversion coverage và controller performance/basis riêng; không dựng benchmark hay OHLCV từ fills.
- **Callbacks:** chọn khoảng ngày/filter/timezone theo contract và refresh có retry hints; response cũ không ghi đè filter mới, không polling toàn bộ history.
- **Fixture/kiểm tra:** missing day khác zero; funding chưa quan sát là null; conversion coverage thiếu; số decimal rất nhỏ/lớn; HTTP 200 stale/updating, timeout, range invalid, đổi timezone/filter khi pending và retry phục hồi. Giữ timezone của bucket persisted, không đổi nhãn ngày tùy ý.
- **Điều kiện bàn giao:** chart gaps không nội suy; đơn vị, thời điểm và phạm vi luôn truy được. Reporting lỗi không chặn status/Stop; chart display number không thay authoritative decimal.

### Bằng chứng xuyên suốt (T-32–37)

Mỗi đợt cần interface/callback, fixture với expected output, test logic và UI, rồi browser workflow tích hợp. Đối chiếu success, validation/permission, transient/persistent failure, retry/recovery, timeout unknown, duplicate/idempotency, ordering, concurrency, reload/restart và side effects độc lập khi liên quan; ghi lý do nếu không áp dụng.

Người tích hợp ghi file sở hữu trước khi sửa page/shell/provider, command và kết quả PASS/FAIL/SKIP theo COORDINATION.md. Mock không chứng minh restart backend hoặc exchange effects thật. T-36–37 đóng bằng chứng hosted route/deep link, Google/gateway và đúng bot/account/Demo–Live trước khi gọi sẵn sàng vận hành; không tự thực hiện thao tác production chỉ để hoàn tất test.

## Bộ tình huống đầu tiên để hai bên đối chiếu

| Case | Kết quả UI cần thể hiện | Logic cần bảo đảm |
|---|---|---|
| Chưa đăng nhập / xác minh thất bại | Login và lỗi có thể xử lý | Không tạo API session trước khi xác minh |
| Viewer / Operator / Admin | Danh tính, quyền và khả năng tương ứng | Permissions kết hợp backend availability |
| Môi trường unknown hoặc account chưa xác minh | Chưa đủ bằng chứng xác nhận ngữ cảnh | Không suy từ URL/process config |
| Môi trường bot/account lệch nhau | Mismatch rõ ràng | Giữ nguồn và ngữ cảnh từng quan sát |
| Nhiều account / không có market | Danh sách hoặc trạng thái thiếu rõ ràng | Không tự chọn account, không tạo market giả |
| Status lỗi, balances còn dữ liệu hoặc ngược lại | Mỗi nguồn có trạng thái riêng | Không gộp thành kết luận chung “bot lỗi/chạy tốt” |
| Dữ liệu cũ hoặc lệch session | Last-good data có nhãn và timestamp | Không dùng response cũ để xác nhận ngữ cảnh mới |
| Quyền đổi / hết phiên | Thao tác và phiên được cập nhật | 403 refresh identity; 401/expiry/logout xóa phiên/cache |
| Đăng nhập lại khi có operation cũ | Truy cập theo dõi kết quả | Khôi phục metadata đúng API/bot/user; không tự gửi lại mutation |

Đây là danh sách case cần fixture và test, chưa phải bằng chứng đã triển khai hoặc đã kiểm tra đạt.

## Ma trận kiểm tra phần logic

| Phạm vi | Case và nơi kiểm tra |
|---|---|
| Mapping ngữ cảnh | `context.test.ts`: permissions theo backend, blocker detail, nhiều/thiếu account, unknown/mismatch, stale và số thập phân nguyên dạng |
| Controller | `useOperationalController.test.tsx`: HTTP status/balances độc lập, refresh khả năng thao tác, persistent error giữ last-good data và phục hồi |
| Provider/layout | `OperationalProvider.test.tsx`: shell/page dùng chung controller, refresh truyền đến mọi consumer, giữ provider khi chuyển route con; consumer ngoài provider báo lỗi rõ ràng |
| Phiên/quyền | `core/auth/AuthProvider.test.tsx`: login, 401/403, expiry/logout, failure khi xác minh lại quyền, concurrent login/403 và stale response sau phiên mới |
| Cache/ordering | `core/api/session.test.tsx`: cache replacement, hủy read cũ, token callback, pagination và theo dõi receipt sau remount |
| Retry và tác động độc lập | `core/api/hooks.test.tsx`, `queries.test.ts`, `client.test.ts`: transient/persistent failure, backoff, không replay mutation, same-key retry, duplicate in-flight và các query độc lập |
| Receipt persistence | `core/api/receipt-tracking.test.ts` và test hook liên quan: scope API/principal/bot, malformed/blocked storage, TTL/giới hạn, dedup và capture scope trước response |
| Browser | `e2e/login.spec.ts`: deep link, logout/reload, denied login/retry và storage. `e2e/workspace.spec.ts`: public routes không gọi API vận hành, rời workspace/logout dừng polling, login lại đọc context mới và không gửi command; kiểm tra overflow 320px |
| Ngoài phạm vi logic này | UI kết nối controller, history fallback trên browser, timeout chưa nhận receipt ID, concurrent operators tác động thật, restart backend/exchange và Google/gateway thật cần các đợt tích hợp T-13/T-32–37 |

Chạy `pnpm run api:check`, `pnpm run check` và `pnpm run test:e2e` từ `dashboard/` để lấy bằng chứng mới. Test HTTP mock không xác minh deployment, account sàn thật hoặc on-exchange effects.

## Trình tự bàn giao

1. UI đề xuất sơ đồ màn hình và cây khối chức năng từ các trách nhiệm dữ liệu trên.
2. Hai bên khớp khối UI với operation, field, trạng thái và callbacks; ghi khoảng thiếu contract.
3. Chốt interface TypeScript/fixtures và consumer hook cho T-01–04; không khóa trước props của toàn bộ sản phẩm.
4. UI triển khai ContextBar/Views; logic triển khai controller/provider theo file sở hữu; người tích hợp mount provider dưới auth guard, nối shell/outlet và nghiệm thu workflow.

UI có thể chọn bố cục hoặc ghép/tách khối theo nhu cầu người dùng. Mỗi cách ghép vẫn phải giữ các trạng thái và bằng chứng nghiệp vụ độc lập.
