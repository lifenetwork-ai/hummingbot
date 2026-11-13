# Review thiết kế trang Overview — đối chiếu docs/overview.md

> Tổng hợp buổi review ngày 2026-11-13. Phạm vi: thiết kế thông tin (information architecture) của trang tổng quan (`src/pages/HomePage.tsx` + `src/features/overview/`), đối chiếu với sáu câu hỏi sản phẩm trong [docs/overview.md](../../docs/overview.md). Không bàn code logic.

## 1. Kết luận chính

Cảm giác "chưa có trọng tâm, chưa rạch ròi, còn trùng lặp" là chính xác. Nguyên nhân gốc:

1. **Trang được tổ chức theo nguồn dữ liệu** (liquidity / balances / positions / status), **không theo câu hỏi** Foundation cần trả lời. Người dùng phải tự lắp ghép các ô dữ liệu rải rác để ra kết luận "đạt hay chưa đạt, vì sao, làm gì tiếp".
2. **Thiếu block tổng hợp đánh giá** — câu hỏi số 6 (tiếp tục / điều chỉnh / dừng) không có bất kỳ khối màn hình nào trả lời.
3. **Chỉ số hiển thị thiếu cặp đối chiếu target / actual / gap** — có kết quả thực tế nhưng mục tiêu của phiên không hiện cạnh nhau, nên không tự trả lời "đạt chưa".

## 2. Đối chiếu sáu câu hỏi của Foundation với trang hiện tại

| # | Câu hỏi (overview.md) | Trang hiện có | Đánh giá |
|---|---|---|---|
| 1 | Bot được giao mục tiêu, vốn và giới hạn nào? | ControlBar (profile, pair, session duration); Risk Guards **ẩn trong dialog** | Nửa chừng. Không hiển thị vốn phân bổ; mục tiêu thanh khoản (spread/depth target) không hiện; giới hạn rủi ro phải bấm mới thấy. |
| 2 | Yêu cầu vận hành thực hiện đến đâu? | Status badge, OperatorAttentionBanner, pending states | Tạm ổn. |
| 3 | Có cung cấp đúng mức thanh khoản đã giao không? | WorkingOrdersSection (own spread, notional, two-sided) | Thiếu target-vs-actual. Spread hiện nhưng không có ngưỡng mục tiêu cạnh nó (`classifySpreadTone` là ngưỡng cứng trong `spread-thresholds.ts`, không phải target của phiên). Không có trượt giá, không có depth theo khoảng giá (xem mục 5). |
| 4 | Duy trì yêu cầu đến mức nào; vì sao chưa đạt? | Coverage KPI + OperatingTargetsCard | Trùng lặp nặng nhất (mục 3). Nguyên nhân chưa đạt có (diagnosis, readiness ineligible) nhưng rải rác nhiều nơi. |
| 5 | Vốn dùng thế nào, chi phí ở đâu, rủi ro trong giới hạn không? | USDT equity KPI, SharedBalances, HeldPositions, RiskGuards dialog | Thiếu phí giao dịch ngay trên trang; thiếu so sánh giữ nguyên tài sản ban đầu (overview.md yêu cầu rõ). "2/8 violations" là ngôn ngữ kỹ thuật, chưa phải câu trả lời vận hành. |
| 6 | Tiếp tục, điều chỉnh hay dừng? | **Không có block nào** | Đây là lý do trang không có trọng tâm. |

## 3. Trùng lặp cụ thể

- **Coverage % xuất hiện 2 lần**: tile "Two-Sided Coverage" trong `OverviewKpiStrip` và headline "two-sided of elapsed" trong `OperatingTargetsCard` — cùng một con số; card còn tự ghi chú "The headline percentage is on the KPI strip above".
- **Quoting absent (noQuoteSeconds) xuất hiện 3 lần**: legend `OperatingTargetsCard`, accordion "Technical parameters", và diagnosis trong `WorkingOrdersSection`.
- **Diagnosis side kể 2 lần trong cùng card** WorkingOrders: badge tooltip + drawer chi tiết.
- **Net Position** ở KPI strip và `HeldPositionsCard` dùng cùng dữ liệu positions, chỉ khác mức tổng hợp; chưa có luồng "tổng → chi tiết" rõ ràng.
- **Badge inflation**: OBSERVED / NO DATA / SESSION MISMATCH / SYNCING lặp ở hầu hết khối. Nguyên tắc "dữ liệu thiếu phải rõ" đang được thực hiện cơ học từng ô thay vì một cơ chế freshness thống nhất.

## 4. Đề xuất thiết kế lại

Nguyên tắc: **mỗi vùng màn hình = một câu hỏi; mỗi chỉ số hiển thị theo cặp target / actual / gap.**

```
┌─ Q1. Nhiệm vụ được giao ───────────────────────────────────────┐
│ Profile · Markets · Thời hạn · Vốn phân bổ · Giới hạn rủi ro   │  ← gộp ControlBar + RiskGuards summary
└────────────────────────────────────────────────────────────────┘
┌─ Q3+Q4. Thanh khoản: ĐANG ĐẠT / CHƯA ĐẠT — vì sao ────────────┐
│ Verdict theo market: spread (vs target) · depth 2 phía ·       │  ← WorkingOrders giữ, thêm cột target
│ two-sided · coverage % (vs 99%) — một chỗ duy nhất             │  ← xóa headline trùng ở OperatingTargets
│ └ Chi tiết "vì sao chưa đạt": composition + diagnosis +        │  ← OperatingTargets trở thành sub-panel
│    quoting absent                                               │
└────────────────────────────────────────────────────────────────┘
┌─ Q5. Vốn & rủi ro ─────────────────────────────────────────────┐
│ Tồn kho/vị thế · vốn còn/dành cho lệnh · phí · guards status   │
└────────────────────────────────────────────────────────────────┘
┌─ Q6. Đánh giá & hành động (mới) ───────────────────────────────┐
│ Tổng hợp phiên: đạt % · chi phí · rủi ro → Continue/Adjust/Stop│  ← link sang Daily Statistics / Session
└────────────────────────────────────────────────────────────────┘
```

Điểm khác biệt then chốt so với layout hiện tại:

1. **Một verdict duy nhất cho coverage** — KPI strip giữ con số tổng; `OperatingTargetsCard` bỏ headline, chỉ còn composition "vì sao" (nó làm vai trò này tốt).
2. **Target lấy từ cấu hình phiên** — `spread_policy` đã có trong `model.ts` (`risk.marketPolicies.spreadPolicy`) nhưng UI chưa dùng; không hard-code ngưỡng màu.
3. **Risk Guards cần bản tóm tắt điều hành ngay trên trang** (Đang ổn / Cần can thiệp / Dừng an toàn) thay vì chỉ `violationCount/totalCount` + dialog kỹ thuật.
4. **Freshness xử lý tập trung một chỗ** — một trạng thái quan sát chung đầu trang, dữ liệu cấp dưới chỉ biến thành `—` có nhãn, thay vì mỗi card tự treo badge riêng.

## 5. Khoảng trống dữ liệu backend

Đối chiếu trực tiếp `src/core/api/schema.d.ts` (`LiquidityMarketOutput`, `PerformanceResponseOutput`).

### 5.1. Backend chưa cung cấp (UI chỉ có thể hiển thị "chưa có dữ liệu")

| Mục | Bằng chứng |
|---|---|
| **Trượt giá theo quy mô giao dịch** | Không có field slippage hay order book toàn thị trường. Liquidity DTO chỉ có `best_bid`/`best_ask` — giá tốt nhất **của lệnh bot**, không có sổ lệnh để ước tính trượt giá. |
| **Độ sâu theo khoảng giá (depth ladder)** | Chỉ có `bid_notional`/`ask_notional` gộp + số lệnh mỗi phía; không chia theo khoảng cách giá (±0.5%, ±1%...). overview.md yêu cầu "độ sâu riêng mỗi phía tại các khoảng giá". |
| **Benchmark giữ nguyên tài sản ban đầu** | `getPerformance` chỉ có scope `controller_lifetime_and_active_exposure`; không có series hold/benchmark. LOGIC-HANDOFF đợt 6 cũng ghi "không dựng benchmark hay OHLCV từ fills". |
| **Rolling 30 phút SLA + lịch sử từng phút** | Chỉ có counter tích lũy cả phiên (`elapsed_seconds`, `eligible_seconds`, `unknown_seconds`, `two_sided_fraction_of_elapsed`). UI đã ghi đúng "rolling SLA unavailable". |

### 5.2. Có ở nguồn khác, không nằm trên endpoint Overview

| Mục | Nguồn thực tế | Hướng xử lý |
|---|---|---|
| **Phí giao dịch trong phiên** | `listFills` (`RecordedFeeOutput`, `fee_in_quote`) và Daily Statistics (`fee_in_usdt`, `DailyFeeOutput`) | Tổng hợp frontend từ fills, hoặc đề nghị backend thêm aggregate cho phiên. |
| **Thời gian phục hồi sau khớp lệnh** | Không có metric trực tiếp; chỉ có tín hiệu gián tiếp: `working_orders_with_fills`, `no_quote_seconds`, `diagnosis.observed_seconds` | Ước tính gián tiếp từ counter, hoặc đề nghị backend thêm metric trực tiếp. |

Nguyên tắc áp dụng: theo overview.md, các mục thiếu phải hiển thị tường minh "chưa có dữ liệu" thay vì im lặng — trang đã làm đúng với "Rolling SLA unavailable", cần áp dụng nhất quán cho các mục còn lại.

## 6. Bước tiếp theo (chưa quyết)

- Chọn hướng: (a) chi tiết hóa mục 4 thành wireframe/cây component để đối chiếu UI-STRUCTURE-PLAN, hay (b) chỉnh dần trực tiếp vào code.
- Bảng mục 5 là đầu vào bàn với backend nếu muốn đóng các khoảng trống đen đặc.
