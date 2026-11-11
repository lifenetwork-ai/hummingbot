# Yêu cầu frontend vận hành LIFE Token Market Making

## 1. Mục đích và phạm vi

Giúp Foundation trả lời theo đúng thứ tự:

**Bot đang làm gì? Có làm đúng nhiệm vụ không? Nếu không, cần làm gì?**

Phase hiện tại tập trung vào khả năng bắt đầu, quan sát, nhận biết vấn đề, can thiệp và dừng/phục hồi an toàn. Năm mục tiêu dài hạn trong [Tổng quan sản phẩm](overview.md) và các nhóm trong [Use case](usecase.md) vẫn là định hướng; phase này chưa yêu cầu đo lường đầy đủ hiệu quả kinh doanh.

Tài liệu này xác định nhu cầu, ưu tiên và kết quả nghiệm thu; chưa quy định màn hình, component hay bố cục. [FE integration](fe-integration.md) quy định tích hợp và ngữ nghĩa dữ liệu; [OpenAPI](api/openapi.json) là nguồn kiểu request/response. Tên field và trạng thái bên dưới chỉ dùng để đối chiếu triển khai.

Chấp nhận trong phase này:

- Chọn/chuyển profile đã triển khai; cấu hình chi tiết qua YAML/deploy bởi kỹ thuật.
- Nạp/rút và cân bằng tài sản thủ công ngoài bot; frontend quan sát dữ liệu cập nhật.
- Một số tình huống phục hồi cần kỹ thuật, với nguyên nhân và thông tin tra cứu rõ ràng.
- Báo cáo vận hành cơ bản, dữ liệu có giới hạn và lịch sử chưa bao phủ mọi khoảng gián đoạn.

Chưa yêu cầu tự động rebalance/chuyển vốn, trình chỉnh sửa mọi tham số, bộ đo spread–depth–slippage toàn thị trường, SLA bổ sung lệnh, báo cáo tác động trước/sau chính sách hoặc benchmark giữ nguyên tài sản có điều chỉnh dòng vốn. Đây không phải điều kiện chặn bản frontend vận hành đầu tiên.

## 2. Thứ tự ưu tiên chức năng

| Mức | Thành phần | Người vận hành cần nhìn/đọc | Quyết định hoặc thao tác |
|---|---|---|---|
| Cốt lõi | Nhận diện ngữ cảnh | Bot, sàn/cặp, tài khoản đã xác minh nếu có, demo/live và người dùng hiện tại | Xác nhận đúng nơi trước khi thao tác; không suy đoán tài khoản khi chưa xác minh |
| Cốt lõi | Nhiệm vụ và hoạt động thực tế | Profile, thị trường, thời hạn; yêu cầu đang chờ; trạng thái dịch vụ, phiên và lệnh thực tế | Biết bot đang làm gì và có khớp với nhiệm vụ được giao không |
| Cốt lõi | Lệnh mua/bán | Có/thiếu từng phía, giá, lượng còn lại, giá trị và thời điểm quan sát; đặt/hủy đang chờ | Kiểm tra khả năng cung cấp lệnh hai chiều |
| Cốt lõi | Vấn đề và hành động | Phía bị ảnh hưởng, nguyên nhân có bằng chứng, thời gian chờ và hướng xử lý | Chờ, cân bằng/bổ sung tài sản thủ công, đổi profile hoặc liên hệ kỹ thuật |
| Cốt lõi | Điều khiển phiên | Điều kiện được phép thao tác, thay đổi dự kiến và tiến trình yêu cầu | Chạy/chuyển/dừng và theo dõi đến kết quả xác nhận |
| Cốt lõi | Vốn và rủi ro | Số dư khả dụng, allocation, tồn kho/vị thế, mức sử dụng giới hạn và dữ liệu còn thiếu | Tiếp tục, xử lý tài sản hoặc giảm/dừng |
| Cốt lõi, luôn sẵn sàng khi được phép | Dừng và xác minh | Kết quả dừng, lệnh chưa rõ/chưa hủy, tài sản/vị thế còn chịu rủi ro | Dừng, kiểm tra và yêu cầu đối soát khi cần |
| Hỗ trợ thường xuyên | Hoạt động gần đây | Lệnh, khớp và thay đổi gần đây | Tra cứu để giải thích diễn biến và kiểm tra bot tiếp tục đặt lệnh |
| Hỗ trợ định kỳ | Lịch sử và chi phí | Phiên, người thao tác, phí, volume, kết quả ghi nhận, sự cố | Kiểm tra cuối ca và xem lại quyết định |
| Chi tiết tra cứu | Cấu hình và chẩn đoán | Cấu hình áp dụng, stage, mã lỗi, request/operation ID và đối soát | Đào sâu hoặc gửi thông tin cho kỹ thuật |
| Thiết lập, ít dùng | Quyền và kết nối sàn | Quyền hiện tại, nguồn quản lý khóa, trạng thái kết nối và hành động được phép | Đăng nhập, quản lý quyền/kết nối theo khả năng backend |

Ít sử dụng không làm giảm mức quan trọng của dừng an toàn hoặc nhận diện demo/live. Chi tiết chẩn đoán cần dễ truy cập khi có sự cố. PnL, volume và biểu đồ lịch sử là thông tin hỗ trợ trong phase này.

## 3. Câu hỏi nghiệp vụ và mức hỗ trợ backend

**Có**: contract hiện có cung cấp dữ liệu/thao tác trong phạm vi nêu. **Tổng hợp**: frontend cần kết hợp các nguồn hiện có. **Một phần**: chỉ trả lời trong phạm vi dữ liệu hỗ trợ. **Chưa hỗ trợ**: không đưa thành thao tác hoặc cam kết của phase này.

Bảng là cơ sở đặc tả từ contract và đối chiếu backend; không xác nhận mức sẵn sàng production hay tỷ lệ hoàn thành. Không dùng ước lượng phần trăm làm tiêu chí nghiệm thu.

| Nhóm / use case | Câu hỏi cần trả lời | Hỗ trợ và giới hạn |
|---|---|---|
| Ngữ cảnh, quyền và kết nối | Tôi đang vận hành bot, tài khoản và môi trường nào? Tôi được làm gì? Kết nối đã sẵn sàng chưa? | **Có/Tổng hợp**: identity, quyền, credential status, readiness và actions. Tài khoản có thể chưa xác minh hoặc dữ liệu cũ. Connected không đồng nghĩa MM đang chạy. Demo/live thuộc triển khai, không phải nút chuyển phiên. |
| Nhiệm vụ — UC-01, UC-09 | Profile, thị trường, budget, nguyên tắc đặt lệnh, giới hạn và thời hạn thực tế là gì? Được thay đổi gì? | **Có**: catalogue và cấu hình ghim theo phiên. Chỉ sửa lựa chọn thị trường/profile/thời hạn được cho phép; các tham số quản lý còn lại chỉ đọc. Phân biệt cấu hình mới triển khai với cấu hình phiên hiện hành. |
| Điều kiện bắt đầu — UC-01, UC-08 | Có phiên/yêu cầu đang xử lý? Start/Switch bị chặn vì sao? Có tồn đọng lần trước không? | **Có/Một phần**: actions, admission/readiness, recovery, pending mutations và incidents. Kiểm tra trước chưa bảo đảm điều kiện tại lúc thực hiện; dữ liệu thiếu không chứng minh tài khoản sạch. |
| Kết quả điều khiển — UC-01, UC-07, UC-09 | Yêu cầu đã nhận, đang chờ gì, đã xong hay thất bại? Timeout, người khác thao tác hoặc mở lại ứng dụng thì sao? | **Có/Tổng hợp**: receipt, history, version và session identity. Frontend khôi phục theo dõi thao tác chưa hoàn tất; không gửi một thao tác mới chỉ vì mất response. |
| Lệnh thực tế — UC-02 | Bot có lệnh mua/bán không? Giá, lượng còn lại và giá trị mỗi phía? Có đặt/hủy đang chờ? | **Có trong phạm vi bot**: quote activity và quan sát lệnh đã xác nhận. Pending chưa phải lệnh sàn đã nhận. Không coi recorder history hoặc lệnh riêng bot là toàn bộ sổ lệnh sàn. |
| Sau giao dịch — UC-03 | Vừa khớp gì? Bot đã tiếp tục đặt lệnh chưa? Nếu chưa, đang chờ hay bị chặn? | **Có/Một phần**: fills và hoạt động/lệnh hiện tại. Chưa có quan hệ đầy đủ “fill này → lệnh bổ sung này” hoặc phép đo đầy đủ thời gian phục hồi; không tự tạo kết luận đó. |
| Thiếu lệnh — UC-04 | Thiếu phía nào và vì sao? Chờ hay cần can thiệp? Sau xử lý đã phục hồi chưa? | **Có/Tổng hợp**: lý do từng phía, readiness, policy, incidents và next action. Giữ trạng thái chưa xác định khi thiếu bằng chứng; xác nhận phục hồi bằng dữ liệu mới. |
| Vốn và tài sản — UC-05, UC-06 | Có bao nhiêu tài sản khả dụng? Allocation khác số dư thế nào? Thiếu gì để tiếp tục? | **Có/Một phần**: balances, allocation, orders và policy. Chưa có đối chiếu vốn toàn diện hoặc khuyến nghị tổng quát cần nạp/cân bằng bao nhiêu. Số dư mới không xác nhận riêng từng giao dịch nạp/rút. |
| Rủi ro và dừng — UC-07 | Giới hạn nào gần/chạm ngưỡng? Dừng đã hoàn tất chưa? Còn lệnh/vị thế gì? | **Có/Một phần**: policy observations, receipt, status, positions và reconciliation. Mỗi giới hạn có phạm vi riêng. Stop giữ tài sản/vị thế đã khớp; thiếu connector/dữ liệu có thể khiến xác minh chưa đầy đủ. |
| Sự cố và phục hồi — UC-08 | Hệ thống đang bảo vệ thế nào? Còn trạng thái chưa rõ nào? Có thể kiểm tra với sàn không? | **Có/Một phần**: HALT, incidents, pending mutations, đối soát và luồng Admin recovery có kiểm chứng. Admin có thể check exchange/ledger, review preview và xác nhận reset kỹ thuật khi `can_recover=true`; blocker hoặc bằng chứng thiếu cần chuyển kỹ thuật. Không có thao tác resume tổng quát, không tự chạy lại trading; reconciliation chỉ quan sát và không sửa sai lệch. |
| Lịch sử và kết thúc — UC-09, UC-10 | Phiên/profile nào đã chạy, ai thao tác, khớp/chi phí gì, còn việc nào chưa xong? | **Có/Tổng hợp/Một phần**: sessions, commands, events, audit, fills, statistics và performance. Chưa có lịch sử mọi khoảng thiếu lệnh, ghi chú bàn giao riêng hoặc tổng hợp hiệu quả năm mục tiêu. Basic bootstrap chỉ nhận diện tài khoản dùng chung. |

## 4. Yêu cầu hành vi cốt lõi

### FE-01 — Ngữ cảnh và độ tin cậy dữ liệu

- Nhận diện bot, sàn/cặp và demo/live rõ ràng trước thao tác; không cung cấp chuyển môi trường nếu backend không hỗ trợ.
- Mỗi thông tin vận hành cần thời điểm quan sát và trạng thái dữ liệu phù hợp: mới, cũ, thiếu hoặc không khả dụng. Response vừa nhận không chứng minh observation mới.
- Phân biệt dịch vụ khả dụng, kết nối đã tải khóa, phiên sẵn sàng và có lệnh thực tế. Không gộp thành một nhãn “đang chạy tốt”.
- Không dùng `running`, volume, PnL hoặc việc không có fill để kết luận bot làm đúng/sai. Thanh khoản riêng bot đo theo mẫu phải được diễn đạt đúng phạm vi.

### FE-02 — Nhiệm vụ, lệnh và vấn đề

- Cho phép đọc nhiệm vụ hiện hành cùng kết quả quan sát, đủ để đối chiếu bot đang làm gì với nhiệm vụ được giao.
- Operator summary dùng `status.operator.state`, `label`, `detail`, `next_action` cùng `observation`, `actions` và `recovery` để nêu tình trạng, ảnh hưởng và bước tiếp theo. `actions` là khả năng hiện tại có lý do chặn; quyết định cuối vẫn được backend kiểm tra khi nhận POST.
- Thể hiện hai phía mua/bán độc lập; phân biệt lệnh xác nhận, chờ đặt, chờ hủy, bị chặn và chưa xác định.
- Mỗi vấn đề cần nêu ảnh hưởng, lý do đã biết và hành động được phép. Đưa chi tiết kỹ thuật vào phần tra cứu; không trình bày giả thuyết thành nguyên nhân đã xác nhận.
- Với thiếu lệnh bất thường, cảnh báo sau hơn 20 giây và yêu cầu điều tra sau hơn 60 giây; tuổi lệnh không tự chứng minh engine chậm. Mục tiêu ban đầu là ít nhất 99% sampled two-sided availability trong 30 phút, tính riêng theo thị trường và eligible conditions. Hiện total session coverage cùng unknown/restricted time; không tính unknown là thành công, không che thời gian bị hạn chế và không tuyên bố đạt mục tiêu từ mẫu ngắn. Deployment phải chủ động đặt `MM_NO_QUOTE_WARNING_SECONDS=20` vì default backend là 120 giây.
- Sau khớp hoặc can thiệp, dùng lệnh/readiness mới để xác minh tiếp tục hoạt động; không suy diễn chỉ từ thao tác của người dùng.

### FE-03 — Chạy, chuyển và theo dõi yêu cầu

- Chỉ cung cấp thị trường, profile/Random và chế độ thời hạn backend quảng bá. Giải thích Random theo pool/chu kỳ cấu hình; không hứa đường giá thị trường.
- Trước gửi, cho phép xem lại nhiệm vụ dự kiến, phiên bị ảnh hưởng và phần cấu hình chỉ đọc. Switch tạo phiên thay thế; không mặc định đóng vị thế.
- Dùng quyền người dùng kết hợp actions hiện tại của backend; không chỉ kiểm tra tên role. Giữ Stop có thể truy cập khi Start/Switch đang chờ nếu backend cho phép.
- Gửi version, session identity và catalogue precondition tương ứng. Theo dõi receipt đến `succeeded`, `failed` hoặc `superseded`; `202`, `accepted` và `applying` chưa hoàn tất.
- Timeout là kết quả chưa rõ. Theo dõi receipt đã biết hoặc thử lại đúng body/key cũ; không tự đổi version hoặc tạo key mới. Conflict cần tải trạng thái mới và người dùng xem lại quyết định.
- Sau reload, khôi phục theo dõi từ định danh không chứa bí mật hoặc lịch sử backend trước thao tác mới. Dữ liệu giữ lại phải gắn đúng API/bot; bỏ response thuộc ngữ cảnh cũ.

### FE-04 — Vốn, rủi ro và dừng

- Phân biệt số dư tài khoản, vốn khả dụng và allocation; không cộng trùng ví dùng chung giữa spot/perpetual. Nêu đơn vị, phạm vi và thời điểm của từng số liệu.
- Hiển thị tồn kho/vị thế và mức sử dụng giới hạn theo đúng định nghĩa policy; không gọi controller drawdown là tổn thất toàn bộ vốn Foundation.
- Stop có xác nhận ngắn, nêu phiên/thị trường và việc vị thế vẫn mở. Không báo thành công khi kết quả chưa rõ; tiếp tục cho xem lệnh/tài sản/vị thế sau dừng.
- Chỉ kết luận lệnh đã được xử lý trong phạm vi xác minh. Zero row, thiếu dữ liệu hay dừng process không đủ chứng minh hết lệnh/rủi ro.
- Đối soát cần hiện phạm vi, thời điểm, chênh lệch và phần không kiểm tra được. Không có nút reset HALT tổng quát hoặc sửa sai lệch giả định; chỉ Admin được dùng recovery check/review/reset theo preview và bằng chứng của contract.

### FE-05 — Thiết lập và kết nối

- Hỗ trợ Google sign-in theo contract; Google ID token được lưu ở memory và localStorage theo API/Google client ID; reload/tab mới khôi phục qua `/access/me` khi token còn hạn, chờ xác minh trước khi vào workspace. Logout đồng bộ giữa các tab. Persistence không gia hạn token. Hết hạn hoặc 401 xóa phiên và API cache; 403 làm mới identity/quyền qua `/access/me` rồi cập nhật khả năng thao tác. Sau đăng nhập lại, khôi phục safe local deep link và tiếp tục theo dõi operation cũ bằng receipt/history; không tự gửi lại mutation. Quản lý tài khoản chỉ dành cho quyền được cấp.
- Chỉ cung cấp cấu hình khóa khi nguồn/capability cho phép. Nguồn environment thể hiện do triển khai quản lý; không cung cấp form sửa YAML hoặc hạ tầng.
- Với kết nối managed, phân biệt Set, Start, Stop và Disconnect. Set thành công không tự chạy. Disconnect dừng bot-wide, xử lý lệnh và đóng vị thế perpetual được quản lý, rồi gỡ slot được chọn; tài sản spot còn lại. Cần xác nhận tác động này trước gửi.
- Theo dõi credential receipt riêng với command receipt; theo root operation khi retry/repair cleanup. Chỉ kết luận Disconnected khi có trạng thái cuối và các proof theo integration contract; không có force-delete để bỏ qua cleanup.
- Khóa và token không xuất hiện trong URL hoặc log. Chỉ Google ID token được phép lưu localStorage theo luồng auth đã chốt; không lưu khóa sàn, recovery confirmation token hoặc secret form vào browser storage; xóa secret input khỏi trạng thái form sau gửi. Chỉ giữ metadata không chứa bí mật cần thiết để theo dõi.

### FE-06 — Lịch sử, lỗi và khả năng tiếp tục vận hành

- Có tra cứu lệnh/khớp gần đây, phiên, command và audit; giữ đúng bộ lọc, phân trang và phạm vi dữ liệu. Dùng audit cho người thao tác khi receipt không có identity.
- Phí, volume, funding và performance phải có đơn vị/cửa sổ/phạm vi. Giữ Decimal string và cách tính chính xác; không ép số tiền sang JavaScript Number để cộng hoặc so sánh.
- Lỗi lịch sử/biểu đồ không chặn hiển thị status và Stop. Giữ dữ liệu tốt gần nhất cùng thời điểm gốc, đánh dấu cũ; không thay lỗi tải thành số 0.
- Polling chia sẻ theo query, tránh request chồng nhau; backoff khi lỗi, tôn trọng Retry-After, giảm polling không cần thiết khi ẩn tab. Phản hồi đến muộn không ghi đè ngữ cảnh mới.
- Mất mạng/CORS không được diễn giải thành bot đã dừng hoặc mật khẩu sai. Cung cấp hướng xử lý và request/operation ID cho kỹ thuật khi cần.

## 5. Tiêu chí nghiệm thu phase vận hành

| Tình huống | Kết quả cần quan sát |
|---|---|
| Mở ứng dụng hoặc đổi bot | Nhận diện đúng ngữ cảnh/quyền; dữ liệu cũ của bot trước không lẫn sang bot mới |
| Start hợp lệ | Xem lại nhiệm vụ → gửi một thao tác → theo dõi receipt → đọc trạng thái/lệnh thực tế; không báo có thanh khoản chỉ từ receipt thành công |
| Không đủ điều kiện hoặc cấu hình thay đổi | Nêu lý do/field bị chặn; refresh và xem lại khi version/catalogue conflict; không retry mù |
| Mất response, reload hoặc hai người cùng thao tác | Khôi phục receipt hoặc retry cùng request/key; không nhân đôi ý định; xung đột được hiển thị rõ |
| Có/thiếu lệnh hai phía, khớp một phần/toàn bộ | Hiện lượng còn lại và hoạt động mới; nêu pending/cooldown/blocker khi chưa bổ sung; không tự gắn fill với lệnh thay thế |
| Thiếu tài sản hoặc chạm giới hạn | Hiện phía ảnh hưởng, tài sản/giới hạn và hành động; sau can thiệp kiểm tra dữ liệu mới trước kết luận phục hồi |
| Stop khi đang chạy hoặc Start/Switch đang chờ | Theo dõi đúng thao tác/phiên; timeout/lỗi chưa báo dừng xong; sau dừng còn xem được lệnh và vị thế/rủi ro |
| Sàn/dịch vụ gián đoạn, dữ liệu stale hoặc khởi động lại | Nêu phần chưa rõ; không hiển thị trạng thái tốt từ dữ liệu cũ; đối soát/phục hồi theo quyền và contract |
| Đối soát thiếu dữ liệu hoặc có chênh lệch | Hiện phạm vi chưa xác minh; không tuyên bố sạch lệnh hoặc đã sửa; có hướng chuyển kỹ thuật |
| Lỗi tạm thời/kéo dài, rate limit, lỗi báo cáo | Backoff đúng hướng dẫn; status/điều khiển vẫn dùng được khi backend khả dụng; giữ timestamp cũ và không tạo dữ liệu 0 giả |
| Hết xác thực, reload hoặc quyền thay đổi | Yêu cầu đăng nhập lại khi cần; khôi phục safe local deep link và theo dõi receipt/history cũ, tải identity/trạng thái mới; không tự gửi lại mutation |
| Thiếu lệnh bất thường hoặc đo availability | Cảnh báo sau 20 giây, điều tra sau 60 giây; xem availability theo từng market, eligible/total coverage, unknown và restricted time; không tuyên bố đạt mục tiêu 99%/30 phút từ mẫu ngắn |
| Admin recovery | Chỉ Admin có `manage_recovery`; dừng session và đợi version được xác nhận trước check; check thành công vẫn có thể bị blocked; review preview chỉ khi `can_recover=true`; chỉ `succeeded` + `stage=recovered` mới hoàn tất, trading vẫn stopped và không tự Start |
| Set/Disconnect managed và cleanup bị chặn | Set không tự Start; Stop không bị hiểu thành Disconnect; theo đúng root receipt, slot version và proof; retry/repair theo actions |
| Tra cứu cuối ca | Xem được nhiệm vụ, thao tác, fills/chi phí và việc còn tồn đọng trong phạm vi dữ liệu; phần thiếu được ghi rõ |

Nghiệm thu cần luồng browser thực tế trên môi trường triển khai đã thống nhất, với auth/network path phù hợp. Đối chiếu mã nguồn, schema hoặc test backend không thay thế bằng chứng frontend xuyên suốt. Mỗi kịch bản phải ghi PASS/FAIL/SKIP cùng bằng chứng và giới hạn môi trường; chưa chạy không được tính là PASS.

## 6. Điều kiện bắt đầu và hoàn tất

Có thể bắt đầu frontend từ contract hiện có với các giới hạn đã nêu. Trước tích hợp browser cần thống nhất API origin, đường gateway/ingress, xác thực và quyền; không suy ra service nội bộ truy cập được chỉ vì CORS đã bật.

Bản đầu hoàn tất khi người vận hành nhận diện đúng ngữ cảnh, hiểu nhiệm vụ và lệnh thực tế, biết vấn đề/hành động, điều khiển đến kết quả, quan sát vốn/rủi ro sau dừng và tra cứu diễn biến gần đây. Các kịch bản cốt lõi ở trên cần bằng chứng nghiệm thu; blocker backend phát hiện trong quá trình tích hợp được ghi riêng theo luồng ảnh hưởng.
