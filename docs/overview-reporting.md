# Admin Overview reporting

Reporting supplement for `GET /api/v1/admin/status`. Collectors use existing owner
observations and recorder history. Fee catch-up writes separate reporting tables;
it does not rewrite the ledger or participate in financial commits, quoting,
sizing, execution or risk enforcement.

Reporting reads strip the private coverage/recovery histories inside PostgreSQL,
before network transfer and JSON decoding. The public observations and one-query
read contract stay unchanged. Full histories remain in the durable checkpoint
and in owner/restart reads; this projection does not shorten retention.
The publisher also reads only the previous guard observation for guard-history
comparison. Its row lock and owner/generation/control-version predicates remain;
it still writes the new full checkpoint and command outcome in the same transaction.
Large checkpoint JSON is encoded before opening that transaction. Only the small
guard/error metadata is merged under the lock; owner/generation/version are checked
after encoding, so a concurrent Stop rejects the stale prepared publication.

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
  Không dùng scientific notation, kể cả phí nhỏ/âm. Inventory `asset` phải khớp
  base asset của `pair`; đơn vị của risk inventory là asset đó, không gán cố định LIFE.
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
  `rolling_30m_fraction` vẫn là two-sided / toàn thời gian, không phải eligible SLO.
  Các field mới tách `eligible_seconds`, `two_sided_eligible_seconds`,
  `restricted_seconds`, `restriction_seconds` và `eligibility_unknown_seconds`.
  Ba nhóm eligible/restricted/eligibility-unknown cộng lại bằng elapsed; unknown
  quan sát ban đầu vẫn giữ nghĩa riêng. `conditional_eligible_fraction` chia cho
  eligible; `conservative_eligible_fraction` chia cho eligible + eligibility-unknown.
  Chỉ đánh giá target khi `measurement_ready=true`, đủ cửa sổ và có eligible time;
  luôn hiện unknown/restriction bên cạnh. Không có eligible time thì unqualified.
  Pending create/cancel/ACK thông thường không phải ngoại lệ guard. Readiness một
  mình không chứng minh funded: cần proposal/order evidence mới của cả hai phía.
  Bucket giây giữ kích thước hữu hạn; phần bucket bị cắt ở mép rolling được tính
  unknown thay vì đoán phân bố. Đây là sampled evidence, không phải continuous SLA.
  `measurement: sampled_interval_lower_bound` chỉ ghi nhận hai phía khi còn ID
  lệnh đã ACK ở cả hai đầu khoảng. Quote đổi ID giữa hai mẫu không chứng minh
  thời lượng hai phía, cũng không chứng minh toàn bộ khoảng đó thật sự mất quote.
  Không coi metric này là cùng một phép đo với point-sample qualification cũ;
  dưới target cần phân tích thêm, không tự kết luận engine chậm hay tự nới guard.
  Restriction breakdown dùng một nhóm chính cho mỗi khoảng, theo thứ tự
  balance → inventory → bootstrap → guard → policy, để không cộng trùng thời gian.
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
| #2 Phí phiên | Fill recorder và submission journal, lọc đúng bot/session/account/environment. Contribution riêng theo identity gốc của fill được cộng một lần bằng transaction. Không còn giới hạn tổng 10.000 fill; mỗi lượt đọc/xử lý có batch hữu hạn. `history_complete: false` vẫn có nghĩa tổng phí ghi nhận chưa chứng minh đầy đủ lịch sử tài chính. |
| #3 Portfolio | Equity tài khoản đã deduplicate spot/perp cùng UID; cộng phần LIFE quy đổi theo mid từ book spot đang có. USDT equity đã gồm unrealized PnL: không cộng PnL/margin lần hai. Thiếu giá, identity, balance hoặc có tài sản khác chưa định giá được thì không trả tổng một phần. |
| #4 Delta | Trả `session_baseline_and_transfers_unavailable`: chưa có snapshot tài sản đầu phiên và ledger nạp/rút đủ scope. |
| #5 Hold | Trả `initial_holdings_and_transfers_unavailable`: chưa có holdings ban đầu và nguồn loại trừ dòng tiền. |
| #6 Risk rules | Drawdown, outflow và quote deviation lấy limit từ policy hiện có; outflow tiêu thụ `max(0, net_outflow) + reserved`. Inventory trả limit, current chưa có projection đủ entry reservations nên unknown. Bootstrap nhiều giai đoạn giữ chi tiết trong status hiện có; summary không nhận là bao phủ đủ. |
| #7 Depth | Remaining notional của lệnh bot đã ACK trong ±25/50/100 bps quanh outside mid. Không tính pending create/cancel thành working orders. |
| #8 Rolling SLA | Checkpoint bucket giây có giới hạn trong JSONB status, window 1.800 giây và summary theo phút. Owner/generation/restart/clock gaps và eligibility transition thiếu bằng chứng là unknown; không dựng từ counter readiness tích lũy. `target_fraction` chưa cấu hình vẫn null. |
| #9 Slippage | Walk book cache có giới hạn; trừ toàn bộ known-own quantity theo bound bảo thủ trước khi tính. Notional 100/1.000/10.000 USDT quy ra base ở reference mid (`notional_basis: reference_mid`). Thiếu depth trong phần book quan sát được thì estimate null. Không gửi lệnh thử. |
| #10 Balances | `free` từ available cash. `reserved_for_open_orders` vẫn null vì chưa có field xác nhận chỉ phần giữ cho open orders; không suy từ equity trừ free. |
| #11 Inventory limits | Spot dùng `inventory_limit`; perp tách `long_limit`, `short_limit`, `gross_limit`. Không ép ba hạn mức directional/gross thành một số hoặc gọi helper có side effect. |
| #12 References | `last_price` là mid, có `price_basis: mid` và `price_observed_at`; anchor thuộc đúng session/generation. Random step sau bước đầu chưa có timestamp anchor được chứng minh nên unavailable. |
| #13 Recovery | P50/P95 là upper bound được sampling (`measurement: sampled_upper_bound`), từ fill có order ID được cached executor chứng minh thuộc session/generation tới lần quan sát quote hai phía đã ACK. History luôn incomplete; restart/gap không tự suy thời điểm phục hồi. |

### Scope, timestamp và dữ liệu một phần

- Phí có `processing_state: catching_up|ready`, `processed_fill_count` và
  `caught_up_at`. Ready nghĩa là sweep hiện tại đã kết thúc, không có nghĩa phí
  tài chính đầy đủ. Processed count gồm cả nguồn không đủ attribution/conversion;
  không dùng số đếm đó để suy rằng mọi phí đã vào tổng. `through_at` là upper bound
  trade-time của sweep hoàn tất gần
  nhất; không chứng minh mọi transaction cũ đã commit. Sweep kế tiếp tìm fill đến
  muộn hoặc attribution xuất hiện sau cursor; identity contribution chống cộng trùng.
  Khi đổi version projection, giữ aggregate đã công bố trong lúc rebuild;
  `reported_projection_version`, `processing_projection_version` và
  `backfill_processed_fill_count` phân biệt tổng đang hiển thị với tiến độ rebuild.
  Chuyển publication cùng transaction khi sweep kiểm chứng xong; worker cũ không
  được hạ version. Thiếu nguồn để khôi phục số fill đã công bố thì giữ tổng cũ,
  báo stale với `session_fee_rebuild_incomplete`, không tự công bố tổng thấp hơn.
- Worker reporting dùng lane DB nặng hiện có, nhường giữa các batch; tiến độ tiếp
  tục khi không có UI polling. Cache thường 15s, catch-up nhường ít nhất 50ms,
  lỗi retry sau 3s. Khi refresh lỗi, giữ tổng đã có với `stale=true` và lý do.
  Mỗi lần đọc kiểm tra lại timestamp theo clock DB: quá 15s hoặc timestamp vượt
  quá 1s về tương lai báo `observation_stale`, kể cả cache chưa hết TTL. Dung sai
  1s cho phép query lấy timestamp ngay sau row status, cùng quy tắc resident
  observation; không dùng để giấu clock lỗi lớn hơn. Khi refresh còn
  bận, giữ tổng cũ cùng timestamp với `stale=true`, `session_fees_refreshing`.
  Cold status chỉ đợi tối đa 200ms rồi trả pending/unavailable; không chờ full backfill.
- Migration `022_create_overview_fee_projection.sql` chỉ thêm projection/index.
  Schema này là optional đối với startup tài chính: khi owner cũ làm migration
  phải chờ, report có thể unavailable nhưng không được làm engine fail startup.
  Không tự sửa/reset ledger, risk checkpoint hoặc cờ HALT.

- `account_scope.identity_verified` xác nhận common UID/environment của các connector đang hoạt động.
  `account_scope.site` lấy từ cấu hình resident của các connector và phải thống
  nhất giữa Spot/perp. UID trong reporting vẫn là UID thô; fee query ghép namespace
  đúng `site:UID` để khớp binding credentials đã capture trong submission journal.
  Thiếu site thì phí unavailable; không bỏ prefix của binding hoặc suy site từ UID.
  Account cũ còn giữ trong reporting sau credential rotation không được cộng vào tài sản mới.
- `valuation.prices[].observed_at` là thời điểm book nguồn, `valued_at` dùng thời điểm giá đó.
  Thời điểm capture không thay cho thời điểm giá. Giá, reference và own-depth đều phải qua freshness check.
- Mỗi batch dùng deadline statement 1.500ms và lock 250ms, đọc tối đa 501 nguồn
  để xử lý tối đa 500 fill. Scope/order và order/market/trade index tránh quét
  lại toàn bộ lịch sử trong một batch; không giới hạn tổng fill của phiên.
- `history_complete: false`, dữ liệu stale, risk group có `unavailable_reason`, hoặc rolling window/target
  còn thiếu không đủ để kết luận verdict đạt. FE vẫn có thể hiển thị phần đã ghi nhận với nhãn incomplete.
- `pending_count` recovery gồm mẫu chưa chứng minh được thời điểm phục hồi; sau gap có thể còn mẫu
  không thể khôi phục timing, được giữ tới hết cửa sổ. P50/P95 chỉ lấy mẫu recoverable đã quan sát.
- Collector không gọi exchange API, tạo connector, đăng ký feed, ghi baseline giả hoặc đổi quote/sizing/risk enforcement.
  Lỗi một projection không được làm mất control status hay các nhóm dữ liệu độc lập.
