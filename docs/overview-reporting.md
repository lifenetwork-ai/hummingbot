# Admin Overview reporting

Read-only reporting supplement for `GET /api/v1/admin/status`. Collectors use existing owner observations and recorder history. This contract does not change quoting, sizing, execution or risk enforcement.

## Contract

Nguồn contract: [`OverviewSupplement`](../hummingbot/cli/admin_overview_models.py),
được gắn vào `StatusResponse.overview` của `GET /api/v1/admin/status`.
Owner publish các quan sát vào snapshot hiện có; `/status` đọc snapshot và trả từng nhóm có provenance.
`overview` vẫn có thể null nếu projection không dùng được; từng metric thiếu nguồn trả null kèm lý do.

Pipeline bắt buộc: sửa model Python → `make swagger` → `docs/api/openapi.json` →
`src/core/api/schema.d.ts`. FE lấy type từ DTO generated, không tạo interface thay thế (ví dụ import từ `src/features/overview/`):

```ts
import type { ResponseBody } from '../../core/api/types'

type OverviewData = NonNullable<ResponseBody<'getRuntimeStatus'>['overview']>
```

Đoạn import trên chỉ dùng được sau khi artifacts được generate bằng môi trường backend
đã provision đầy đủ. Không sửa tay schema/type generated và không nối mock thành dữ liệu thật.

Các metric chỉ phục vụ quan sát/báo cáo admin. Verdict hỗ trợ operator, không tự thay đổi
quote, sizing, hạn mức hay kích hoạt dừng bot.

| Mục yêu cầu | Vị trí trong `status.overview` |
| --- | --- |
| #1 | `session.capital_allocated` |
| #2 | `session.fees` |
| #3 | `portfolio` |
| #4 | `session.delta` |
| #5 | `session.benchmark_hold` |
| #6 | `risk_rules` |
| #7 | `markets[].depth` |
| #8 | `markets[].coverage` |
| #9 | `markets[].slippage_estimate` |
| #10 | `balances` |
| #11 | `inventory_limits` |
| #12 | `references` |
| #13 | `markets[].post_fill_recovery` |

Quy ước để FE follow:

- Mỗi nhóm dữ liệu dùng `{ value, observed_at, stale, unavailable_reason }`.
  `value: null` là chưa có dữ liệu; số 0 là giá trị đo thật. Dữ liệu stale/partial/unknown
  không đủ để kết luận verdict đạt. Các field bên trong cũng có thể null khi thiếu một phần.
- Tiền, giá, số lượng và tỷ lệ tài chính dùng chuỗi decimal thuần (`"120000.00"`),
  không có dấu phân nhóm, đơn vị hay `%`. Type alias không thay thế runtime validation.
  `pct: "0.82"` nghĩa là 0.82%; coverage fraction dùng number trong [0,1].
- `account_scope` phân biệt tài sản toàn tài khoản với vốn mandate của phiên. Session metrics
  phải cùng `session_id` và baseline; không thay bằng PnL lifetime của controller.
- Delta chỉ có giá trị khi xác định đủ nạp/rút: equity cuối − equity đầu − net inflows.
  `%` dùng equity đầu làm mẫu số, không phải lợi suất có điều chỉnh thời điểm dòng tiền.
- Hold benchmark đề xuất giữ LIFE+USDT spot ban đầu, cùng basis giá với portfolio;
  chưa hỗ trợ account scope có tài sản khác hoặc perp exposure ở baseline: trả unavailable
  đến khi backend có định nghĩa benchmark tương ứng. Không suy từ PerformanceReport hiện tại.
- Depth là lệnh bot, cộng dồn trong từng khoảng quanh mid; slippage là toàn order book,
  riêng BUY/SELL. Thiếu depth để khớp đủ notional thì estimate null.
- Rolling coverage giữ unknown trong mẫu số; cửa sổ chưa đủ 30 phút có `full_window: false`.
  Lịch sử từng phút dùng số giây thay boolean để không mất thông tin trong một phút.
- Phí đề xuất quy đổi tại fill, số dương là chi phí, số âm là rebate. Reservation chỉ dùng
  nguồn xác nhận phần giữ cho open orders; không lấy equity trừ available để đoán.
- Recovery đề xuất từ fill đến quote hai phía được xác nhận; percentile chỉ tính mẫu đã
  phục hồi, luôn kèm số mẫu pending và độ đầy đủ lịch sử.

Các metric chưa đủ nguồn vẫn dùng unavailable theo bảng dưới đây. Các định nghĩa delta/hold
là contract mục tiêu; không thay bằng phép tính từ PnL lifetime hoặc balance hiện tại.

## Sources and limits

| Mục | Nguồn / hành vi |
| --- | --- |
| #1 Vốn phân bổ | Chưa có cấu hình phân bổ USDT+LIFE; trả `capital_mandate_not_configured`. `total_amount_quote` là ngân sách đặt lệnh, không phải vốn phân bổ tài khoản. |
| #2 Phí phiên | Fill recorder join submission journal, lọc đúng bot/session/account/environment. Tổng chỉ gồm phí USDT hoặc base asset có conversion tại fill được chứng minh. `history_complete: false`: đây là phần phí đã ghi nhận, chưa phải tổng chi phí đầy đủ. |
| #3 Portfolio | Equity tài khoản đã deduplicate spot/perp cùng UID; cộng phần LIFE quy đổi theo mid từ book spot đang có. USDT equity đã gồm unrealized PnL: không cộng PnL/margin lần hai. Thiếu giá, identity, balance hoặc có tài sản khác chưa định giá được thì không trả tổng một phần. |
| #4 Delta | Trả `session_baseline_and_transfers_unavailable`: chưa có snapshot tài sản đầu phiên và ledger nạp/rút đủ scope. |
| #5 Hold | Trả `initial_holdings_and_transfers_unavailable`: chưa có holdings ban đầu và nguồn loại trừ dòng tiền. |
| #6 Risk rules | Drawdown, outflow và quote deviation lấy limit từ policy hiện có; outflow tiêu thụ `max(0, net_outflow) + reserved`. Inventory trả limit, current chưa có projection đủ entry reservations nên unknown. Bootstrap nhiều giai đoạn giữ chi tiết trong status hiện có; summary không nhận là bao phủ đủ. |
| #7 Depth | Remaining notional của lệnh bot đã ACK trong ±25/50/100 bps quanh outside mid. Không tính pending create/cancel thành working orders. |
| #8 Rolling SLA | Checkpoint interval có giới hạn trong JSONB status, window 1.800 giây và bucket phút. Owner/generation/restart/clock gaps là unknown; không dựng ngược từ counter tích lũy. Chưa có SLA target trong config nên `target_fraction: null`. |
| #9 Slippage | Walk book cache có giới hạn; trừ toàn bộ known-own quantity theo bound bảo thủ trước khi tính. Notional 100/1.000/10.000 USDT quy ra base ở reference mid (`notional_basis: reference_mid`). Thiếu depth trong phần book quan sát được thì estimate null. Không gửi lệnh thử. |
| #10 Balances | `free` từ available cash. `reserved_for_open_orders` vẫn null vì chưa có field xác nhận chỉ phần giữ cho open orders; không suy từ equity trừ free. |
| #11 Inventory limits | Spot dùng `inventory_limit`; perp tách `long_limit`, `short_limit`, `gross_limit`. Không ép ba hạn mức directional/gross thành một số hoặc gọi helper có side effect. |
| #12 References | `last_price` là mid, có `price_basis: mid` và `price_observed_at`; anchor thuộc đúng session/generation. Random step sau bước đầu chưa có timestamp anchor được chứng minh nên unavailable. |
| #13 Recovery | P50/P95 là upper bound được sampling (`measurement: sampled_upper_bound`), từ fill có order ID được cached executor chứng minh thuộc session/generation tới lần quan sát quote hai phía đã ACK. History luôn incomplete; restart/gap không tự suy thời điểm phục hồi. |

### Scope, timestamp và dữ liệu một phần

- `account_scope.identity_verified` xác nhận common UID/environment của các connector đang hoạt động.
  Account cũ còn giữ trong reporting sau credential rotation không được cộng vào tài sản mới.
- `valuation.prices[].observed_at` là thời điểm book nguồn, `valued_at` dùng thời điểm giá đó.
  Thời điểm capture không thay cho thời điểm giá. Giá, reference và own-depth đều phải qua freshness check.
- Phí được đọc ở heavy DB lane, query read-only, deadline 1.500 ms, tối đa 10.000 fill.
  Cache theo scope 15 giây, lỗi/unavailable retry sau 3 giây; concurrent requests dùng chung một refresh.
  `/status` chờ tối đa 200 ms cho phần phí rồi trả `session_fees_refreshing`; refresh tiếp tục độc lập.
- `history_complete: false`, dữ liệu stale, risk group có `unavailable_reason`, hoặc rolling window/target
  còn thiếu không đủ để kết luận verdict đạt. FE vẫn có thể hiển thị phần đã ghi nhận với nhãn incomplete.
- `pending_count` recovery gồm mẫu chưa chứng minh được thời điểm phục hồi; sau gap có thể còn mẫu
  không thể khôi phục timing, được giữ tới hết cửa sổ. P50/P95 chỉ lấy mẫu recoverable đã quan sát.
- Collector không gọi exchange API, tạo connector, đăng ký feed, ghi baseline giả hoặc đổi quote/sizing/risk enforcement.
  Lỗi một projection không được làm mất control status hay các nhóm dữ liệu độc lập.
