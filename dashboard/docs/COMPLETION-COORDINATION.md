# Phối hợp hoàn thiện frontend sau audit

## Phạm vi

Đợt này đã hoàn thiện các khoảng PARTIAL trong T-05–06, T-08, T-11, T-15, T-17, T-22–23, T-25, T-27–29 và thu bằng chứng T-32–35 sau khi ghép. Follow-up bổ sung T-07 live order projection nhưng backend test/Swagger verification còn chờ Python dependencies. T-09 vẫn thiếu rolling eligibility/restriction provenance; T-19 được chứng minh bằng server-connected evidence và frontend freshness. Hosting và môi trường thật không thuộc backlog.

Người logic và tích hợp sở hữu checkout chính `dev`; UI làm component ở checkout snapshot độc lập. Không commit ở cả hai phía. UI bàn giao xong thì dừng sửa; integrator mới áp patch và nối pages/providers. Không sửa chồng file trình bày trong lúc UI đang làm.

## Nguồn bàn giao

- [Prompt đầy đủ cho UI](UI-COMPLETION-PROMPT.md).
- `src/features/overview/model.ts`: Random cycle/runtime/policy/side evidence.
- `src/features/session-control/configuration-review.ts`: hai nguồn current/pinned read-only, references/configuration/risk limits.
- `src/core/api/operation-feedback.ts`: form field mapping và safe diagnostics.
- `src/features/recovery/comparison.ts`: đầy đủ bảy giá trị before/after, basis và explanation.
- `src/features/history/filters.ts`, `useHistoryController.ts`: filters/cursors/validation, historical pinned config, command inspect và audit lookup.
- `src/features/reporting/performance.ts`: model scope/coverage/basis/lifecycle của controller performance, độc lập daily statistics.
- `src/features/operational-context/recovery-fence.ts`: fence theo scoped receipt, giữ Stop; Start/Switch/connection bị chặn khi recovery chưa xác minh, pending hoặc còn preview review hợp lệ.
- `src/test/mocks/completionFixtures.ts`: fixtures test-only cho component UI.

Recovery fence theo API/principal/bot và quyền manage_recovery; không đọc recovery endpoint cho người không có quyền, không suy kind từ ID và không thay kiểm tra concurrency/availability của backend. Các actor khác vẫn chịu backend capabilities/version checks. Callback refresh fence trả Promise<void>, không đưa confirmation token tới consumer. Không persist token/body recovery.

## Ownership

| Lane | Sửa | Không sửa |
|---|---|---|
| UI | Component trình bày và `.test.tsx` trong overview/session/admin/activity/statistics; primitive UI cần thiết; UI completion result | Model/types/controller/core/generated schema, logic fixtures, pages/app, package/lockfile, E2E/CI |
| Logic | Các module/callback/validation ở trên, test logic, diagnostics và shared provider fence | File View/tsx của UI trong giai đoạn song song |
| Integrator | Page/provider callback binding, mock/component/flow verification và tài liệu nghiệm thu sau khi nhận patch | Không áp toàn bộ dirty snapshot hoặc tự overwrite thay đổi UI khác |

Các enhancement props mới cho View hiện có được để optional trong nhánh UI để caller cũ vẫn compile; integrator nối model đã chốt sau. Logic source và fixtures là readonly đối với UI. Nếu cần đổi type/callback, báo exact interface/field lên logic owner trước phần bị ảnh hưởng.

## Nghiệm thu trước và sau ghép

Logic kiểm tra exact decimal/null/unknown, source/basis, invalid filter, permission, retry/ref equality, cursor reset, late/scope responses, recovery fence và independent query effects. UI kiểm tra presentation/loading/empty/error/stale, callbacks, keyboard/focus và responsive/themes bằng typed fixtures. Không đưa fixture vào runtime.

Sau UI bàn giao, integrator kiểm tra ownership/patch baseline, nối từng workflow rồi chạy API check, lint/typecheck, full unit/coverage/build và existing browser/a11y gates. Không claim integrated acceptance từ test model/component riêng. Visual/contrast và các browser workflow còn thiếu giữ trạng thái riêng theo audit; policy hiện tại vẫn hoãn viết mới E2E khi UI còn biến động.

## Bằng chứng logic trước khi UI ghép

Branch `dev`, HEAD nền `4b4188e1d` cộng thay đổi chưa commit; Node 26.8.2/pnpm 12.4.1.

| Kết quả | Command | Bằng chứng |
|---|---|---|
| PASS | `pnpm run api:check` | Generated schema không drift. |
| PASS | `pnpm run lint` | Không lỗi hoặc warning. |
| PASS | `pnpm run typecheck` | Không diagnostic. |
| PASS | `node node_modules/vitest/vitest.mjs run --coverage --coverage.reportsDirectory=/private/tmp/hummingbot-completion-logic-coverage` | 92 files / 420 tests; statements/lines 91.15%, branches 82.58%, functions 95.25%; threshold giữ nguyên. |
| PASS | `node node_modules/vitest/vitest.mjs run src/features/reporting/performance.test.ts` | 2 tests xác nhận report exact values, source isolation và percentage points/volume denominator. |
| PASS | `python3 /private/tmp/export-hummingbot-ui-completion.py` | Baseline/exporter hoạt động, 0 UI changes ở thời điểm khởi tạo. |
| SKIP | Build/browser/a11y/visual của các component UI bổ sung | UI chưa bàn giao/ghép. Không dùng gate cũ để gọi đợt này đã nghiệm thu. |

Các type/domain contract trong snapshot UI khớp với bản logic bàn giao. UI không sửa model/controller để làm test pass; integrator revalidate source/contract và chạy gates sau khi áp patch.
