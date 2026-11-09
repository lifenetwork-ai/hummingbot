# Use case LIFE Token Market Making

Tài liệu triển khai năm mục tiêu trong [Tổng quan sản phẩm](overview.md) thành các use case nghiệp vụ cho Foundation.

## Cấu trúc

**Năm mục tiêu → nhóm use case cốt lõi → use case chi tiết và tình huống xử lý.**

Mười nhóm dưới đây là cách tổ chức nghiệp vụ ban đầu, có thể mở rộng hoặc tách/gộp khi đặc tả. Danh sách mô tả nhu cầu sản phẩm; mức độ hỗ trợ của hệ thống hiện tại cần được đối chiếu riêng.

Foundation giao mục tiêu, cấp vốn và quyết định điều hành. Bot thực hiện chính sách trong giới hạn được giao. CEX tiếp nhận lệnh, khớp lệnh và cung cấp trạng thái thị trường, tài sản.

## Liên hệ với năm mục tiêu

| Mã | Nhóm use case cốt lõi | Mục tiêu phục vụ |
|---|---|---|
| UC-01 | Thiết lập và bắt đầu nhiệm vụ tạo thanh khoản | Cả năm mục tiêu |
| UC-02 | Duy trì khả năng mua/bán LIFE | Giao dịch thuận lợi; duy trì thanh khoản đạt chuẩn |
| UC-03 | Bổ sung thanh khoản sau khi lệnh khớp | Giao dịch thuận lợi; duy trì thanh khoản đạt chuẩn; rủi ro trong giới hạn |
| UC-04 | Phát hiện và xử lý thanh khoản không đạt | Giao dịch thuận lợi; duy trì thanh khoản đạt chuẩn; hiểu rõ hiệu quả hoạt động |
| UC-05 | Cân bằng lại LIFE và tài sản ghép cặp | Duy trì thanh khoản đạt chuẩn; vốn và chi phí hợp lý; rủi ro trong giới hạn |
| UC-06 | Điều chỉnh vốn phân bổ | Giao dịch thuận lợi; vốn và chi phí hợp lý; rủi ro trong giới hạn |
| UC-07 | Giảm hoặc dừng khi chạm giới hạn | Rủi ro trong giới hạn |
| UC-08 | Xử lý gián đoạn và phục hồi vận hành | Duy trì thanh khoản đạt chuẩn; rủi ro trong giới hạn |
| UC-09 | Điều chỉnh chính sách và kiểm chứng tác động | Cả năm mục tiêu |
| UC-10 | Đánh giá để tiếp tục, điều chỉnh hoặc kết thúc | Vốn và chi phí hợp lý; rủi ro trong giới hạn; hiểu rõ hiệu quả hoạt động |

## UC-01 — Thiết lập và bắt đầu nhiệm vụ tạo thanh khoản

**Tình huống:** Foundation bắt đầu tạo lập thị trường trên một sàn/cặp LIFE.

**Các use case chi tiết:**

- Chọn sàn/cặp, tài khoản vận hành và thời hạn nhiệm vụ.
- Xác định spread, độ sâu riêng từng phía tại các khoảng giá, quy mô giao dịch để đánh giá trượt giá và tỷ lệ thời gian đạt yêu cầu.
- Phân bổ LIFE và tài sản ghép cặp; xác định ngân sách chi phí, giới hạn tồn kho, tổn thất và điều kiện giảm/dừng.
- Kiểm tra quyền truy cập sàn, quy tắc đặt lệnh, số dư khả dụng, dữ liệu giá và lệnh đang tồn tại.
- Xử lý mục tiêu không khả thi với nguồn vốn hoặc điều kiện sàn; điều chỉnh trước khi chạy.
- Yêu cầu khởi chạy và xác nhận chính sách áp dụng, trạng thái bot cùng lệnh thực tế trên sàn.

**Kết quả cần đạt:** Nhiệm vụ có mục tiêu đo được, đủ điều kiện vận hành và xác định rõ phần đã chạy, đang chờ hoặc thất bại. Yêu cầu khởi chạy được tiếp nhận chưa đồng nghĩa đã có thanh khoản.

## UC-02 — Duy trì khả năng mua/bán LIFE

**Tình huống:** Bot vận hành bình thường và thị trường liên tục thay đổi.

**Các use case chi tiết:**

- Duy trì lệnh mua và bán theo mức giá, lượng lệnh và giới hạn đã giao.
- Cập nhật lệnh khi giá tham chiếu thay đổi hoặc lệnh không còn phù hợp với chính sách.
- Theo dõi spread, độ sâu mỗi phía và trượt giá ước tính ở quy mô mục tiêu.
- Phân biệt thanh khoản toàn thị trường với phần lệnh thực tế do bot cung cấp.
- Ghi nhận thời gian đồng thời đạt yêu cầu và các khoảng không đạt hoặc thiếu dữ liệu.

**Kết quả cần đạt:** Khả năng mua/bán LIFE đáp ứng yêu cầu đã thống nhất trong cửa sổ đo, với bằng chứng từ sổ lệnh và lệnh của bot. Trượt giá ước tính không bảo đảm giá khớp thực tế.

## UC-03 — Bổ sung thanh khoản sau khi lệnh khớp

**Tình huống:** Lệnh khớp một phần hoặc toàn bộ làm giảm độ sâu một phía.

**Các use case chi tiết:**

- Cập nhật lượng lệnh còn lại, số dư, tồn kho và phí sau giao dịch.
- Bổ sung lệnh theo chính sách khi vẫn đủ vốn và còn trong giới hạn rủi ro.
- Xử lý nhiều lệnh khớp liên tiếp hoặc hai phía cùng khớp.
- Đối soát khi thông tin khớp đến chậm, trùng hoặc khác thứ tự để tránh bổ sung sai lượng.
- Ghi nhận thời gian phục hồi; chỉ rõ phần không thể bổ sung do thiếu vốn, giới hạn rủi ro hoặc lỗi sàn.

**Kết quả cần đạt:** Khôi phục mức thanh khoản yêu cầu trong thời gian đã giao hoặc giải thích được nguyên nhân chưa khôi phục; lệnh bổ sung phù hợp với tài sản thực tế.

## UC-04 — Phát hiện và xử lý thanh khoản không đạt

**Tình huống:** Spread quá rộng, thiếu độ sâu hoặc mất một phía trong thời gian cần xử lý.

**Các use case chi tiết:**

- Xác định chỉ tiêu chưa đạt, phía bị ảnh hưởng, mức thiếu hụt và thời lượng.
- Phân biệt không đạt thực tế với không đủ dữ liệu để đánh giá.
- Kiểm tra nguyên nhân: chính sách lệnh, thiếu vốn, lệch tồn kho, giới hạn rủi ro hoặc sự cố vận hành.
- Chọn điều chỉnh chính sách, cân bằng tài sản, bổ sung vốn hoặc xử lý sự cố theo nguyên nhân có bằng chứng.
- Theo dõi kết quả xử lý và xác nhận thời điểm đạt lại yêu cầu.

**Kết quả cần đạt:** Foundation biết vấn đề nằm ở đâu, bằng chứng nào hỗ trợ kết luận và hành động nào giúp phục hồi. Trường hợp chưa xác định được nguyên nhân phải được thể hiện rõ.

## UC-05 — Cân bằng lại LIFE và tài sản ghép cặp

**Tình huống:** Giao dịch lệch một chiều khiến bot thiếu LIFE để bán hoặc thiếu tài sản ghép cặp để mua.

**Các use case chi tiết:**

- Đánh giá cơ cấu tài sản hiện tại so với vùng tồn kho được phép.
- Điều chỉnh cách đặt lệnh để đưa tồn kho về vùng mục tiêu theo chính sách.
- Foundation quyết định cân bằng tài sản qua giao dịch hoặc điều chuyển vốn khi cần; giao dịch cân bằng phải nằm trong phạm vi được giao.
- Đánh giá phí, trượt giá và ảnh hưởng thanh khoản trước khi cân bằng.
- Xác nhận tài sản thực nhận, chi phí phát sinh và điều kiện phục hồi lệnh hai chiều.

**Kết quả cần đạt:** Cơ cấu tài sản trở lại vùng cho phép với chi phí được ghi nhận. Nếu chưa cân bằng được, hoạt động tiếp tục ở mức được phép hoặc giảm/dừng theo giới hạn.

## UC-06 — Điều chỉnh vốn phân bổ

**Tình huống:** Foundation cần tăng khả năng cung cấp thanh khoản, bổ sung vốn thiếu hoặc thu hồi vốn dư.

**Các use case chi tiết:**

- Xem vốn phân bổ, số dư khả dụng và phần đang dành cho lệnh theo từng tài sản.
- Đánh giá lượng vốn cần thêm để đạt mục tiêu thanh khoản.
- Ghi nhận bổ sung vốn và xác nhận tài sản đã sẵn sàng sử dụng.
- Giảm/hủy lệnh cần thiết trước khi thu hồi phần vốn đang được sử dụng.
- Xử lý nạp/rút đang chờ, thất bại hoặc chưa xác định kết quả.
- Ghi nhận dòng vốn để báo cáo hiệu quả không nhầm nạp/rút với lãi/lỗ.

**Kết quả cần đạt:** Vốn sử dụng khớp với phân bổ thực tế; Foundation biết mức thanh khoản có thể duy trì sau thay đổi. Các thao tác chuyển tài sản tuân theo quyền hạn được cấp.

## UC-07 — Giảm hoặc dừng khi chạm giới hạn

**Tình huống:** Tồn kho, tổn thất hoặc biến động chạm ngưỡng bảo vệ vốn.

**Các use case chi tiết:**

- Phát hiện mức sử dụng giới hạn và điều kiện kích hoạt bảo vệ.
- Giảm lượng lệnh, dừng một phía hoặc dừng toàn bộ theo chính sách.
- Foundation chủ động yêu cầu dừng khẩn cấp khi cần.
- Hủy các lệnh thuộc phạm vi dừng và xác minh lệnh còn lại trên sàn.
- Xử lý lệnh khớp trong lúc dừng, hủy thất bại hoặc trạng thái chưa rõ.
- Đánh giá tồn kho còn chịu rủi ro và điều kiện cho phép vận hành trở lại.

**Kết quả cần đạt:** Hành động bảo vệ được thực hiện và xác minh. Dừng bot không đồng nghĩa đã hủy hết lệnh hoặc đã loại bỏ rủi ro nắm giữ tài sản; thời gian dừng vẫn được ghi nhận trong báo cáo thanh khoản cùng lý do.

## UC-08 — Xử lý gián đoạn và phục hồi vận hành

**Tình huống:** Hệ thống không còn đủ thông tin đáng tin cậy để tiếp tục vận hành bình thường.

**Các use case chi tiết:**

- Xử lý mất kết nối rồi kết nối lại với sàn.
- Xử lý dữ liệu giá cũ, thiếu hoặc bất thường.
- Khôi phục bot sau khởi động lại khi vẫn còn lệnh trên sàn.
- Đối soát yêu cầu đặt/hủy lệnh chưa biết kết quả trước khi thử lại.
- Xử lý sàn giới hạn tần suất, bảo trì hoặc từ chối yêu cầu kéo dài.
- Đối soát lệnh, giao dịch và số dư; tránh đặt trùng hoặc để sót lệnh ngoài kiểm soát.
- Giữ trạng thái bảo vệ nếu chưa đủ điều kiện phục hồi; chạy lại khi đáp ứng chính sách.

**Kết quả cần đạt:** Trạng thái vận hành được xác lập từ dữ liệu đã đối soát; việc phục hồi không tạo thêm lệnh ngoài ý muốn. Thời gian gián đoạn, thiếu dữ liệu và phục hồi được ghi nhận riêng.

## UC-09 — Điều chỉnh chính sách và kiểm chứng tác động

**Tình huống:** Foundation muốn cải thiện thanh khoản, giảm chi phí hoặc kiểm soát rủi ro tốt hơn.

**Các use case chi tiết:**

- Chọn thay đổi khoảng giá, lượng lệnh, cách bổ sung hoặc các giới hạn vận hành trong phạm vi cho phép.
- Kiểm tra chính sách mới với vốn và điều kiện sàn trước khi áp dụng.
- Xác nhận chính sách thực tế đã áp dụng và các lệnh cũ đã được xử lý phù hợp.
- Xử lý thay đổi thất bại, áp dụng chưa đầy đủ hoặc chưa xác định kết quả.
- So sánh chất lượng thanh khoản, vốn, chi phí và rủi ro trước/sau; nêu ảnh hưởng của điều kiện thị trường khác nhau.
- Giữ chính sách mới, điều chỉnh tiếp hoặc quay lại chính sách trước nếu còn phù hợp với điều kiện hiện tại.

**Kết quả cần đạt:** Truy được chính sách áp dụng theo thời gian và có bằng chứng đánh giá thay đổi. Các giai đoạn thiếu dữ liệu hoặc không đủ tương đồng phải được nêu rõ khi so sánh.

## UC-10 — Đánh giá để tiếp tục, điều chỉnh hoặc kết thúc

**Tình huống:** Đến kỳ đánh giá hoặc Foundation cần quyết định có tiếp tục nhiệm vụ hay không.

**Các use case chi tiết:**

- Tổng hợp mức đạt từng mục tiêu theo sàn/cặp, phiên và chính sách.
- Đánh giá thanh khoản toàn thị trường và đóng góp riêng của bot.
- Tổng hợp vốn, nạp/rút, phí, chi phí cân bằng, kết quả giao dịch và mức sử dụng giới hạn.
- So sánh với việc giữ nguyên tài sản ban đầu, có điều chỉnh dòng vốn; tách ảnh hưởng biến động giá tài sản khỏi hiệu quả tạo lập.
- Giải thích các giai đoạn chưa đạt và phần chưa đủ bằng chứng để kết luận.
- Quyết định tiếp tục, thay đổi mục tiêu/vốn/chính sách hoặc kết thúc nhiệm vụ.
- Khi kết thúc: dừng, xác minh lệnh đã hủy, đối soát tài sản và chi phí còn lại; ghi rõ khoản chưa hoàn tất.

**Kết quả cần đạt:** Quyết định có căn cứ từ chất lượng thanh khoản, chi phí và rủi ro. Khi kết thúc, Foundation nắm rõ tài sản còn lại, lệnh còn tồn tại nếu có và việc cần xử lý tiếp.

## Luồng nghiệp vụ xuyên suốt

Foundation giao mục tiêu và vốn (UC-01) → bot duy trì lệnh hai chiều (UC-02) → lệnh khớp và được bổ sung (UC-03) → phát hiện thanh khoản chưa đạt (UC-04) → cân bằng tài sản hoặc điều chỉnh vốn (UC-05, UC-06) → kiểm chứng chính sách điều chỉnh (UC-09) → đánh giá để tiếp tục hoặc kết thúc (UC-10).

Trong suốt luồng này, giới hạn rủi ro có thể yêu cầu giảm/dừng (UC-07); sự cố vận hành đi qua đối soát và phục hồi (UC-08) trước khi quay lại hoạt động được phép.

## Nguyên tắc đặc tả tiếp

[Yêu cầu frontend vận hành](frontend-requirements.md) xác định phạm vi phase hiện tại, ưu tiên chức năng, mức hỗ trợ backend và tiêu chí nghiệm thu phục vụ vận hành.

- Mỗi use case chi tiết cần xác định tác nhân, điều kiện bắt đầu, hành động, kết quả cần xác minh và cách xử lý khi chưa hoàn tất.
- Mục tiêu, ngưỡng và quyền tự động thực hiện phải được xác định cho từng sàn/cặp và chính sách; giới hạn vốn và rủi ro được ưu tiên.
- Phân biệt yêu cầu đã gửi, kết quả đã xác nhận và trạng thái chưa xác định; thử lại không được tạo tác động trùng.
- Đánh giá theo mục tiêu đối chiếu, thời điểm/cửa sổ đo và chất lượng dữ liệu; dữ liệu thiếu không mặc định là đạt.
- Dùng các nhóm này làm cơ sở tách yêu cầu và nghiệm thu backend/frontend theo kết quả nghiệp vụ.
