# Phối hợp UI và logic/hệ thống — LIFE MM Dashboard

## Mục tiêu và nguồn yêu cầu

Dashboard giúp người vận hành biết bot đang làm gì, nhận ra chênh lệch so với nhiệm vụ và thực hiện can thiệp đúng quyền. Hai người triển khai theo cùng workflow và nghiệm thu trên cùng bản tích hợp.

[TODO](../TODO.md) giữ danh sách công việc, mức ưu tiên và tiêu chí nghiệp vụ T-01–37. Tài liệu này quy định trách nhiệm, interface bàn giao và trình tự phối hợp; không thay thế các yêu cầu đó.

[Bàn giao logic cho UI](LOGIC-HANDOFF.md) cung cấp bản đồ nguồn dữ liệu, trạng thái và các tình huống đầu tiên để đối chiếu với cấu trúc component UI.

Nguồn quyết định:

- [Frontend requirements](../../docs/frontend-requirements.md): nhu cầu và kết quả nghiệp vụ.
- [FE integration](../../docs/fe-integration.md): ngữ nghĩa dữ liệu, điều kiện thao tác và vòng đời operation.
- [OpenAPI](../../docs/api/openapi.json): endpoint, payload và DTO; [API foundation](API.md): adapter/hooks hiện có.
- [Architecture](ARCHITECTURE.md), [Design system](DESIGN_SYSTEM.md), [Feedback](FEEDBACK.md): cấu trúc và quy tắc giao diện.

Nếu các nguồn mâu thuẫn, ghi rõ điểm mâu thuẫn và xử lý trước phần triển khai bị ảnh hưởng. Không tự tạo endpoint, trạng thái hoặc dữ liệu để lấp khoảng thiếu.

## Hai phần trách nhiệm

| Phạm vi | Người làm UI | Người làm logic/hệ thống |
|---|---|---|
| Kết quả | Màn hình dễ hiểu, thao tác được, đầy đủ trạng thái và phản hồi | Dữ liệu đúng ngữ cảnh, quyền đúng, operation và tác động được theo dõi đúng contract |
| Công việc | Bố cục, component trình bày, copy, form input, xác nhận, điều hướng tương tác, responsive, theme, keyboard/focus | API/hooks, mapping DTO, auth/cache, freshness, quyền/capabilities, validation nghiệp vụ, polling, receipt, retry/idempotency, persistence an toàn |
| Test | Component và tương tác với fixture; keyboard, responsive, light/dark, feedback | Adapter/controller/hooks; lỗi dependency, stale/ordering, session isolation, timeout/retry, duplicates và recovery |
| Bàn giao | Component nhận props/callback đã thống nhất và bộ trạng thái có thể render | Controller/hook cung cấp dữ liệu, khả năng thao tác, lỗi và callback theo interface đã thống nhất |

Logic/hệ thống trong phân công này gồm lớp tích hợp frontend và cấu hình kết nối cần thiết. Thay đổi backend, schema hoặc deployment phải được ghi thành đầu việc có phạm vi và người phụ trách riêng trước khi thực hiện.

Người làm UI giữ state tương tác như mở dialog, tab và input chưa gửi. Người làm logic giữ state nghiệp vụ như operation đang chờ, version đã review và khả năng gửi lệnh. UI không suy quyền từ role, tự lên lịch retry hoặc tự kết luận bot đã dừng.

## Sở hữu file và điểm tích hợp

| Khu vực | Người sửa chính | Quy ước |
|---|---|---|
| `src/features/<feature>/*View.tsx`, component trình bày và test | UI | Nhận props rõ ràng; không gọi HTTP, đọc browser storage, auth hay env trực tiếp |
| `src/features/<feature>/use*.ts`, mapping, validation và test | Logic | Dùng adapter/hooks có sẵn; không phụ thuộc page hoặc component trình bày |
| `src/features/<feature>/types.ts`, fixture test của feature | Logic soạn, UI cùng review | Chốt trước khi hai bên triển khai song song; fixture bám DTO/interface |
| `src/core/api/`, `src/core/auth/`, `src/core/config/` | Logic | Tái sử dụng nền hiện có; generated DTO chỉ cập nhật qua generator |
| `src/shared/components/`, theme và motion | UI | Giữ visual system; chỉ thêm shared abstraction khi có nhu cầu thực |
| `src/pages/`, `src/app/`, route và provider | Người tích hợp được ghi trên đầu việc | Root shell giữ khung public; auth guard mở OperationalProvider/Shell một lần cho protected outlet; page compose nội dung của route |
| E2E workflow, mock HTTP dùng chung | Người tích hợp | Hai bên cùng xác nhận assertion nghiệp vụ và hành vi hiển thị |

Tên file trên là quy ước cho phần mới, không yêu cầu đổi tên hoặc di chuyển code hiện có. Hai người thống nhất file sở hữu trước mỗi đợt; thay đổi file của người kia phải phối hợp trước. Không hoàn tác thay đổi ngoài phạm vi được giao.

## Interface bàn giao theo từng workflow

Mỗi workflow cần một bản bàn giao ngắn trong PR hoặc tài liệu feature trước khi dựng UI và logic song song. Interface là props/types TypeScript cụ thể của feature; không cần tạo framework state chung cho toàn dashboard. ContextBar và route views nhận context từ provider hoặc props; không tự fetch shared status/balances.

| Nội dung phải chốt | Yêu cầu |
|---|---|
| Entry và kết quả | Route/điểm vào, người dùng thực hiện việc gì, bằng chứng nào xác nhận hoàn tất |
| Nguồn dữ liệu | Operation/DTO hiện có, field nguồn, phạm vi bot/account/market/session; ghi rõ dữ liệu chưa có API |
| Dữ liệu hiển thị | Tên field, kiểu, đơn vị, timestamp; giữ decimal string, phân biệt `null`, zero và missing |
| Trạng thái | Loading, empty, ready, error và stale khi liên quan; last-good data vẫn kèm timestamp và cảnh báo |
| Thao tác | Callback, input, validation; khả năng thao tác và lý do bị chặn từ permissions + actions/capabilities backend |
| Vòng đời operation | Chưa gửi, đang gửi, accepted/applying và terminal theo đúng loại receipt; timeout là kết quả chưa biết |
| Đổi ngữ cảnh | Logout/401/403, đổi quyền, reload, response cũ và bot/session mismatch được xử lý ra sao |
| Fixture và test | Case ID, input/DTO, output mong đợi và assertion; fixture chỉ dùng trong test/development |

UI gọi callback thể hiện ý định người dùng. Logic kiểm tra điều kiện, tạo request và theo dõi kết quả; backend vẫn có thể từ chối khi trạng thái thay đổi. Không dùng một boolean `isLoading` cho cả tải dữ liệu, gửi lệnh và chờ receipt.

Giữ độc lập trạng thái các nguồn: lỗi báo cáo không làm mất status hoặc Stop khi vẫn được phép. Receipt thành công cần đọc lại trạng thái quan sát; reconciliation thành công chưa chứng minh khớp dữ liệu, recovery check thành công chưa chứng minh đã phục hồi.

### Mẫu đầu việc bàn giao

```md
Workflow / TODO IDs:
Giai đoạn hiện tại: phân tích | chờ chốt | logic | UI và nối | logic verify | hoàn tất
Phạm vi/contract đã chốt với người dùng:
Người UI:
Người logic:
Người tích hợp:
File mỗi bên sở hữu:
Nguồn contract / điểm còn thiếu:
Interface TypeScript / props / callbacks:
Fixture cases và expected output:
Điều kiện được thao tác / lý do bị chặn:
Điều kiện hoàn tất quan sát được:
Test mỗi bên / test tích hợp:
Prompt bàn giao UI / commit logic đối chiếu:
Trạng thái UI: chưa làm | đang làm | sẵn sàng tích hợp
Trạng thái logic: chưa làm | đang làm | sẵn sàng tích hợp
Trạng thái tích hợp: chưa nối | đang kiểm tra | đạt | bị chặn
Bằng chứng: commit/PR, command, PASS/FAIL/SKIP, phạm vi chưa xác minh
Kết luận logic verify / việc cần trả lại UI:
```

Đợt 1 (T-01–04) đã hoàn tất local/mock; trạng thái và bằng chứng nằm tại [TODO](../TODO.md). Mẫu bàn giao ở trên dùng cho workflow đang thực hiện, không tích lũy nhật ký các đợt đã đóng trong tài liệu này.

## Phân chia backlog

Mỗi hàng giữ tiêu chí chi tiết tại [TODO](../TODO.md). Người tích hợp được chọn từ hai người và ghi rõ trước khi bắt đầu hàng đó.

| TODO | UI bàn giao | Logic/hệ thống bàn giao |
|---|---|---|
| T-01 | Review thuật ngữ, hành vi và trạng thái hiển thị | Đồng bộ requirement với integration/OpenAPI; ghi nhận contract còn thiếu |
| T-02 | Khối bot/sàn/cặp/account/Demo–Live/người dùng; unknown/mismatch rõ ràng | Cấu hình public, nguồn identity và account verification; không suy môi trường từ URL |
| T-03 | Login, hết phiên, trở về deep link và truy cập thao tác cũ | Auth/cache/401/403, safe redirect, lưu và phục hồi metadata operation an toàn; không replay mutation |
| T-04 | Hiện/khóa thao tác và giải thích lý do | Kết hợp permissions với actions/capabilities; cập nhật khi quyền hoặc trạng thái đổi |
| T-05–06 | Overview, operator summary và hành động tiếp theo | Mapping requested/observed state, operator/observation/actions/recovery |
| T-07–08 | Lệnh từng market/side, pending và nguyên nhân thiếu lệnh | Own-order semantics, nguồn xác nhận, fill/refresh và dữ liệu bằng chứng |
| T-09–10 | Coverage, warning, timestamp, stale/unknown; giữ status/Stop | Eligible samples, ngưỡng deployment, polling/backoff, request sharing và ordering |
| T-11 | Form Market/Profile/Duration và review cấu hình | Catalogue, pinned configuration, validation và payload; until-stopped theo capability |
| T-12–13 | Tiến trình, kết quả chưa biết, conflict/review và thao tác đang theo dõi | Version/hash/key, receipt, persistence theo API/bot/user, retry/reload/duplicates |
| T-14–15 | Stop dễ truy cập, xác nhận, residual exposure và feedback | Target đúng phiên/version, completion proofs, lỗi có reason/request ID/retry |
| T-16–17 | Vốn, allocation, exposure và risk blocker đúng đơn vị | Decimal semantics, shared-wallet dedup, policy scope và observation |
| T-18 | Check đối soát, thời điểm/phạm vi/mismatch/incomplete | Availability/cooldown, receipt và kết quả đối soát chỉ đọc |
| T-19 | Form connection theo input names; trạng thái và xóa secret input | Environment/managed source, Stop acknowledgement, credential receipt và fresh owner/version |
| T-20–21 | Xác nhận disconnect, blocked stage và repair/retry | Root operation, final proofs, versions, same-account repair và secret lifecycle |
| T-22–25 | Recovery entry/check/review/confirm/blocker đúng quyền | Preconditions, preview/token/version, drift/expiry, recovered proof; giữ trading stopped |
| T-26–27 | Filters, pagination/detail, activity và operation links | Cursor binding/reset, on-demand reads, recorded-history semantics và actor/audit mapping |
| T-28 | Form quản lý account, conflict và audit chỉ đọc | Admin permissions, version, last-bound-Admin protection và endpoint/filter contract |
| T-29–31 | Daily stats/chart, gaps, đơn vị và trạng thái updating | Date/timezone, series scope, decimals, missing/stale và retry hints |
| T-32–33 | Test hành vi người dùng và kết quả hiển thị | Ma trận failure/recovery/concurrency và assertion operation/tác động; cùng tích hợp E2E |
| T-34–35 | Responsive/theme/keyboard/focus, feedback, a11y và visual review | API drift, typecheck/lint/unit/build; người tích hợp thu bằng chứng toàn bộ gates |
| T-36–37 | Deep-link/reload/assets và nghiệm thu browser trên hosted URL | CI/deploy/gateway/Google/CORS/IAM và đúng identity/account/environment; cùng nghiệm thu thật |

## Trình tự phối hợp

Mỗi vòng chỉ xử lý một workflow hoặc nhóm TODO được chọn. Luồng mặc định là **phân tích → chốt với người dùng → logic → UI làm component và nối → logic verify**.

1. **Phân tích:** xác định mục tiêu, TODO IDs, hành vi/non-goals, nguồn contract, trạng thái/lỗi và tiêu chí nghiệm thu. Đối chiếu source; ghi rõ API hoặc thông tin còn thiếu.
2. **Chốt với người dùng:** thống nhất phạm vi, contract/interface UI–logic, file sở hữu và người tích hợp trước khi implement logic. Ghi quyết định vào đầu việc hoặc tài liệu tương ứng. Phạm vi đã chốt không cần xin xác nhận lại cho các bước local có thể đảo ngược; thay đổi phạm vi/hành vi cần được chốt trước phần bị ảnh hưởng.
3. **Làm logic:** người làm logic triển khai controller/hooks, mapping, validation, quyền, callbacks và fixtures có kiểu; thêm/chạy test rồi ghi rõ phần sẵn sàng bàn giao và giới hạn kiểm chứng. Có adapter/DTO chưa có nghĩa workflow đã hoàn tất.
4. **UI làm component và nối:** người làm logic gửi prompt giới hạn đúng nhóm TODO đã chốt, kèm interface/source, fixture cases, tiêu chí nghiệm thu và file sở hữu. UI kiểm kê component tái sử dụng/mở rộng/tạo mới, triển khai View và nối với logic đã bàn giao. Provider dùng chung nằm trong authenticated workspace; không đặt shared fetching trong page. UI tự kiểm tra component/browser và trả bằng chứng cùng phần còn thiếu. Không đưa fixture vào runtime hoặc tự mở rộng sang các đợt sau.
5. **Quay lại logic verify:** người làm logic đối chiếu UI với contract và implementation thực tế, kiểm tra mapping, callbacks, quyền, operation lifecycle, tích hợp và failure/recovery cases. Chạy các check liên quan, ghi PASS/FAIL/SKIP và giới hạn. Sửa phần logic thuộc trách nhiệm của mình; trả lỗi UI bằng case tái hiện và expected result cụ thể. Lặp lại trong cùng phạm vi cho đến khi đạt nghiệm thu, rồi mới đóng TODO và chọn workflow kế tiếp.

Interface đổi trong quá trình nối phải cập nhật đồng thời types, fixtures, hai phía và test liên quan. Bàn giao UI xong chưa thay cho kết luận logic verify. Thiếu callback/API phải được ghi thành đầu việc cụ thể; hoàn thành các phần độc lập nhưng không gọi workflow là hoàn tất khi chưa tích hợp.

Thứ tự workflow:

| Đợt | Phạm vi | Điều kiện chuyển tiếp |
|---|---|---|
| 1 | T-01–04: contract, identity/context, auth và quyền | Hai bên dùng cùng interface; phiên/quyền/ngữ cảnh thiếu được hiển thị đúng |
| 2 | T-05–10, T-16–17: Overview và vốn/rủi ro | Dữ liệu đọc được với freshness và semantics đúng trước khi nối điều khiển |
| 3 | T-11–15, T-18: Start/Switch/Stop, receipt và đối soát | Theo dõi đến kết quả, timeout/reload/duplicates đúng; Stop vẫn truy cập được khi được phép |
| 4 | T-19–25: connection/cleanup và recovery | Phân biệt từng operation, kiểm chứng kết quả và quyền; không tự Start |
| 5 | T-26–28: tra cứu và quản trị | Filters/history/audit và account workflow được tích hợp |
| 6 | T-29–31: báo cáo hỗ trợ | Series/decimal/timezone/gaps đúng contract |
| Xuyên suốt | T-32–37: tests và release | Đóng bằng chứng trước khi gọi bản triển khai sẵn sàng vận hành |

Chỉ dựng View hoặc làm song song ở đợt khác khi phạm vi đó đã được người dùng chốt riêng; không tự biến prompt cho một nhóm TODO thành yêu cầu triển khai cả sáu đợt. Việc tích hợp điều khiển vẫn phụ thuộc ngữ cảnh/quyền/dữ liệu quan sát của các đợt trước. Account/connection có thể ưu tiên sớm nếu onboarding cần và đã thống nhất.

## Nghiệm thu chung

Trước khi thêm test, đối chiếu test hiện có theo từng workflow. Ma trận gồm success, empty, validation/permission, transient/persistent failure, retry/recovery, timeout unknown, duplicates/idempotency, response ordering, concurrent operators, reload/restart và các tác động độc lập khi liên quan. Case không áp dụng cần ghi lý do.

Từ `dashboard/`, chạy các gate liên quan: `pnpm run api:check`, `pnpm run check`, `pnpm run test:e2e`, `pnpm run test:a11y`. Visual chỉ chạy sau khi baseline được tạo, review và commit. Không tự cập nhật baseline để che sai khác. Không tính SKIP là PASS; khi dependency chặn kiểm tra, ghi command chạy lại nguyên selector, flags, environment setup và timeout.

UI đạt với fixture chứng minh khả năng hiển thị/tương tác. Logic đạt với mocked HTTP chứng minh xử lý contract trong phạm vi test. Bản tích hợp cần browser E2E; môi trường thật cần thêm bằng chứng Google/gateway/backend/exchange tương ứng. Ghi branch/commit, runtime, môi trường Demo/Live và phần chưa xác minh cùng kết quả.

Một TODO chỉ hoàn tất khi có interface thống nhất, hai phần đã nối, test liên quan đạt và giới hạn nghiệm thu được ghi rõ. Build thành công hoặc một bên bàn giao xong chưa đủ để đóng workflow.
