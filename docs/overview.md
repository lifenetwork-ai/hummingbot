# LIFE Token Market Making

## Mục tiêu

Giúp Foundation thiết lập, vận hành và giám sát hoạt động tạo lập thị trường cho LIFE trên sàn giao dịch tập trung (CEX), đạt chất lượng thị trường mong muốn trong ngân sách và giới hạn rủi ro đã định.

Foundation cấp vốn, xác định mục tiêu và giới hạn. Bot duy trì lệnh mua/bán theo chính sách đó. Giao diện điều hành giúp Foundation biết chất lượng thị trường có đạt yêu cầu, vì sao chưa đạt và cần điều chỉnh gì.

Foundation là đơn vị sử dụng và ra quyết định điều hành. Chất lượng thanh khoản được đánh giá theo khả năng mua/bán LIFE ở quy mô mục tiêu với chi phí giao dịch chấp nhận được.

## Phạm vi

- Tạo lập thị trường cho duy nhất token LIFE trên CEX. Tài sản ghép cặp được xác định theo thị trường triển khai.
- Chất lượng thị trường được đánh giá qua chênh lệch giá mua–bán (spread), độ sâu sổ lệnh hai chiều, trượt giá theo quy mô giao dịch và thời gian duy trì lệnh đạt yêu cầu.
- Quản lý chính sách đặt lệnh, vốn bằng LIFE và tài sản ghép cặp, tồn kho, chi phí và rủi ro; theo dõi kết quả để quyết định tiếp tục, điều chỉnh hoặc dừng.
- Thiết kế công dụng token, kích cầu sử dụng và nhu cầu riêng của holder nằm ngoài phạm vi này.

## Năm giá trị cốt lõi

```mermaid
%%{init: {"theme":"base","flowchart":{"curve":"basis","nodeSpacing":20,"rankSpacing":45},"themeVariables":{"fontFamily":"Arial, sans-serif","lineColor":"#b5b2bd"}}}%%
flowchart LR
  root([TOKEN MARKET MAKING])
  root --> b1[1. Giao dịch thuận lợi]
  root --> b2[2. Duy trì thanh khoản đạt chuẩn]
  root --> b3[3. Vốn và chi phí hợp lý]
  root --> b4[4. Rủi ro trong giới hạn]
  root --> b5[5. Hiểu rõ hiệu quả hoạt động]

  b1 --> l1[Spread và độ sâu hai chiều<br/>Trượt giá theo quy mô mục tiêu]
  b2 --> l2[Thời gian đạt yêu cầu<br/>Phục hồi sau gián đoạn hoặc khớp lệnh]
  b3 --> l3[Vốn cần bố trí<br/>Chi phí để đạt chất lượng mục tiêu]
  b4 --> l4[Giới hạn tồn kho và tổn thất<br/>Giảm hoặc dừng an toàn]
  b5 --> l5[Đóng góp thực tế của bot<br/>Chi phí và nguyên nhân chưa đạt<br/>Tiếp tục, điều chỉnh hoặc dừng]

  %% Cấp 1–10: màu nhận diện nhánh giữ nguyên; cấp sâu giảm độ nổi bật.
  classDef level1 stroke-width:3px,font-size:22px,font-weight:bold;
  classDef level2 stroke-width:2px,font-size:18px,font-weight:bold;
  classDef level3 stroke-width:1.5px;
  classDef level4 stroke-width:1px,font-size:15px,font-weight:normal;
  classDef level5 stroke-width:1px,font-size:14px,font-weight:normal;
  classDef level6 stroke-width:1px,stroke-dasharray:5 2,font-size:14px,font-weight:normal;
  classDef level7 stroke-width:1px,stroke-dasharray:3 3,font-size:14px,font-weight:normal;
  classDef level8 stroke-width:1px,stroke-dasharray:2 4,font-size:14px,font-weight:normal;
  classDef level9 stroke-width:0px,font-size:14px,font-weight:normal;
  classDef level10 stroke-width:0px,font-size:14px,font-style:italic,font-weight:normal;
  classDef root fill:#eee8fb,stroke:#8b79b5,color:#403650;
  classDef branch1 fill:#ffe99b,stroke:#d5b958,stroke-width:2px,color:#51472c;
  classDef branch2 fill:#cfe5fa,stroke:#8eb4d7,stroke-width:2px,color:#344e66;
  classDef branch3 fill:#d9ebc4,stroke:#a3bf80,stroke-width:2px,color:#455735;
  classDef branch4 fill:#f6d4df,stroke:#d1a1b2,stroke-width:2px,color:#674452;
  classDef branch5 fill:#dcd5f3,stroke:#b0a2d2,stroke-width:2px,color:#51456c;
  classDef leaf fill:#faf9f6,color:#686572;
  class root root;
  class b1 branch1;
  class b2 branch2;
  class b3 branch3;
  class b4 branch4;
  class b5 branch5;
  class l1,l2,l3,l4,l5 leaf;
  class root level1;
  class b1,b2,b3,b4,b5 level2;
  class l1,l2,l3,l4,l5 level3;
  style l1 fill:#fffaf0,stroke:#d5b958
  style l2 fill:#f2f8fe,stroke:#8eb4d7
  style l3 fill:#f5f9ee,stroke:#a3bf80
  style l4 fill:#fdf3f6,stroke:#d1a1b2
  style l5 fill:#f6f3fc,stroke:#b0a2d2
```

## Các câu hỏi sản phẩm phải trả lời cho Foundation

Sản phẩm phải cung cấp thông tin để Foundation xác định mức độ đạt mục tiêu và lựa chọn hành động điều hành. Mỗi câu trả lời cần có mục tiêu đối chiếu, kết quả thực tế, thời điểm hoặc khoảng thời gian đo và giới hạn dữ liệu.

| Câu hỏi của Foundation | Thông tin cần có | Quyết định cần hỗ trợ |
|---|---|---|
| Bot được giao mục tiêu, vốn và giới hạn nào? | Sàn/cặp, mục tiêu thanh khoản, chính sách thực tế áp dụng, thời hạn, vốn phân bổ và giới hạn rủi ro. | Xác nhận nhiệm vụ trước khi chạy; thay đổi mục tiêu, chính sách hoặc vốn khi cần. |
| Yêu cầu vận hành đã được thực hiện đến đâu? | Yêu cầu chạy/dừng/thay đổi, kết quả thực hiện, phiên và trạng thái thực tế, lệnh đang hoạt động hoặc còn chưa xác định được trạng thái. | Chờ xử lý, xử lý lỗi hoặc can thiệp; xác nhận trạng thái thực tế trước khi thao tác tiếp. |
| Bot có cung cấp đúng mức thanh khoản đã giao không? | Spread, độ sâu riêng mỗi phía tại các khoảng giá và trượt giá ước tính theo quy mô đã thống nhất; phân biệt phần bot cung cấp với toàn thị trường. | Giữ nguyên hoặc xem xét điều chỉnh giá, lượng lệnh, vốn hay mức thanh khoản yêu cầu. |
| Trong thời gian được giao, bot duy trì yêu cầu đến mức nào; khi không đạt thì vì sao? | Tỷ lệ thời gian đồng thời đạt spread, độ sâu và hai chiều; các khoảng không đạt, thiếu dữ liệu, thời gian phục hồi sau khớp lệnh và nguyên nhân gián đoạn. | Cân bằng lại LIFE và tài sản ghép cặp, cải thiện bổ sung lệnh hoặc xử lý lỗi vận hành; điều chỉnh mục tiêu nếu nguồn lực không đủ. |
| Vốn đang được dùng thế nào, chi phí phát sinh ở đâu và rủi ro còn trong giới hạn không? | Vốn cấp và nạp/rút, tài sản còn lại và đang dành cho lệnh, tồn kho/vị thế, phí, kết quả giao dịch và mức sử dụng giới hạn. Phân biệt ảnh hưởng biến động giá của các tài sản với hiệu quả tạo lập. | Bổ sung hoặc thu hồi vốn, cân bằng tài sản, điều chỉnh chính sách; giảm hoặc dừng khi chạm giới hạn. |
| Cần tiếp tục, điều chỉnh hay dừng; nếu điều chỉnh thì điều chỉnh gì? | Tổng hợp mức đạt mục tiêu, chi phí và rủi ro theo phiên/chính sách, bằng chứng về nguyên nhân và phần chưa đủ dữ liệu để kết luận. | Chọn thay đổi vốn, chính sách hoặc hệ thống; kiểm chứng tác động của thay đổi trong giai đoạn tiếp theo. |

### Trách nhiệm chung của backend và frontend

- Backend cung cấp dữ liệu và cách tính nhất quán, phân biệt yêu cầu với trạng thái thực tế; lưu kết quả, cấu hình áp dụng và các thay đổi cần thiết để giải thích diễn biến. Nêu rõ dữ liệu thiếu, cũ hoặc chỉ được đo theo mẫu.
- Frontend trình bày mục tiêu cạnh kết quả thực tế, cho thấy phần chênh lệch, nguyên nhân đã biết và hành động được phép. Phân biệt rõ trạng thái bot, kết quả thanh khoản và rủi ro để Foundation ra quyết định.
- Backend và frontend sử dụng chung định nghĩa chỉ số, cửa sổ đo và tiêu chí đạt. Mỗi tính năng phải xác định câu hỏi sản phẩm và quyết định điều hành được hỗ trợ.
- Kết luận về nguyên nhân phải có bằng chứng; giả thuyết và giới hạn phân tích phải được thể hiện rõ. Foundation quyết định trong phạm vi quyền hạn và giới hạn đã đặt.

## Đánh giá năm mục tiêu

| Mục tiêu | Kết quả cần đánh giá |
|---|---|
| Giao dịch thuận lợi | Có thể mua và bán LIFE ở quy mô mục tiêu với spread và trượt giá chấp nhận được. Đo độ sâu riêng từng phía tại các khoảng cách giá đã định; trượt giá từ sổ lệnh là ước tính, không bảo đảm kết quả khớp thực tế. |
| Duy trì thanh khoản đạt chuẩn | Tỷ lệ thời gian đồng thời đạt yêu cầu về spread, độ sâu và hai chiều trong cửa sổ đo đã thống nhất. Theo dõi thêm thời gian bổ sung thanh khoản sau khớp lệnh, thời lượng gián đoạn và điều kiện phục hồi. |
| Vốn và chi phí hợp lý | Chất lượng thanh khoản đạt được tương xứng với vốn cần bố trí, mức sử dụng vốn, phí giao dịch và chi phí cân bằng tồn kho. Phân biệt vốn được cấp với chi phí hoặc tổn thất thực tế. |
| Rủi ro trong giới hạn | Tồn kho, vị thế và tổn thất nằm trong giới hạn. Có thể giảm hoặc dừng an toàn khi dữ liệu giá không đáng tin cậy, sàn gián đoạn hoặc trạng thái lệnh không rõ; chỉ phục hồi khi đủ điều kiện. |
| Hiểu rõ hiệu quả hoạt động | Nhìn riêng chất lượng thị trường tổng thể và phần thanh khoản do bot cung cấp trên từng sàn/cặp. Giải thích kết quả bằng lệnh, giao dịch, vốn và chi phí thực tế; so sánh hiệu quả với việc giữ nguyên tài sản ban đầu, có điều chỉnh nạp/rút vốn. |

## Nguyên tắc

- Giới hạn vốn và rủi ro được ưu tiên hơn mục tiêu thanh khoản; có thể phải giảm hoặc dừng một phía hay toàn bộ hoạt động.
- Thời gian dừng để bảo vệ vốn vẫn được ghi nhận trong báo cáo thanh khoản, kèm lý do; phân biệt với gián đoạn do lỗi vận hành. Dữ liệu thiếu không được mặc định là đạt yêu cầu.
- Volume lớn hay bot đang chạy chưa chứng minh thanh khoản tốt. Cần nhìn vào lệnh thực tế và chất lượng giao dịch.
- Sổ lệnh toàn thị trường tốt chưa chứng minh bot đóng góp hiệu quả. Biến động giá trị danh mục cũng chưa phản ánh riêng kết quả tạo lập vì còn chịu ảnh hưởng của biến động giá các tài sản.
- Tạo thanh khoản không bảo đảm lợi nhuận hoặc giữ giá LIFE.
- Các mục tiêu, ngưỡng và chính sách phải được Foundation xác định; yêu cầu thanh khoản của CEX là đầu vào nếu có.

## Nguyên tắc phát triển và nghiệm thu

[Yêu cầu frontend vận hành](frontend-requirements.md) xác định phạm vi phase hiện tại, ưu tiên chức năng, mức hỗ trợ backend và tiêu chí nghiệm thu phục vụ vận hành.

Các luồng nghiệp vụ được trình bày trong [Use case LIFE Token Market Making](usecase.md), gồm 10 nhóm use case cốt lõi và các tình huống chi tiết gắn với năm mục tiêu.

Yêu cầu và tính năng được tổ chức theo năm mục tiêu, với trình tự xác định:

**Muốn đạt gì → đo bằng gì → thế nào là đạt → cần làm gì.**

Mỗi tính năng cần gắn với một trong năm mục tiêu và chỉ rõ câu hỏi của Foundation, bằng chứng trả lời và quyết định được hỗ trợ. Nghiệm thu backend/frontend theo khả năng trả lời trọn vẹn các câu hỏi đó, với ngưỡng vận hành được xác định cho từng sàn/cặp và chính sách.
