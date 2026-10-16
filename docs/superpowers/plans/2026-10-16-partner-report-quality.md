# Kế hoạch bổ sung chất lượng báo cáo nghiên cứu đối tác

Ngày: 16/10/2026. Trạng thái: anh đã đồng ý triển khai; chức năng đã được bổ sung và kiểm tra offline, còn đánh giá trên dữ liệu sử dụng thực tế.

Cập nhật sau khi anh đồng ý triển khai: đã bổ sung collector theo chủ đề, schema/generation v2, quality gate và fixture offline, reader/export, lịch snapshot và công cụ đo/benchmark. Chi tiết vận hành và giới hạn kiểm chứng tại `docs/report-quality.md`. Số liệu live, hiệu chỉnh trên báo cáo thực tế và tối ưu concurrency sau baseline vẫn cần dữ liệu đo; không coi các số mẫu trong ảnh là kết quả đã đạt.

## 1. Mục tiêu và phạm vi

Người dùng hỏi về email mới trong chat. PU tự đồng bộ, nhận diện yêu cầu kinh doanh, nghiên cứu đúng doanh nghiệp, kiểm tra lịch và tạo báo cáo. Người dùng đọc, phê duyệt lưu/gửi khi phù hợp. Không thêm bước nhập thông tin nghiên cứu thủ công vào luồng chính.

Bổ sung báo cáo theo sáu tiêu chí trong requirement.md: đầy đủ, chính xác, thời gian tạo, nguồn tham khảo, độ mới và mức sẵn sàng cho cuộc họp. Các số 98%, 42 giây, 12 nguồn trong ảnh là kết quả mẫu, không phải kết quả hiện tại hay cam kết hiệu năng.

Giữ nguyên kiểm soát danh tính doanh nghiệp, Tool Harness, chống prompt injection và phê duyệt trước hành động ghi/gửi. Không đổi dữ liệu thật hoặc chạy lại inbox trong bước lập kế hoạch này. Không dùng computer use theo yêu cầu trước đó của anh.

## 2. Khoảng trống đã kiểm tra trong code

- `pu/reporting/collection.py`: đọc trang gốc, tối đa ba trang giới thiệu/sản phẩm và một truy vấn `company news` với tối đa bốn kết quả. Chưa có kế hoạch tìm kiếm riêng cho quy mô, vận hành, chuyển đổi số và cơ hội được nêu trong email.
- `pu/reporting/evidence.py`: nguồn được giới hạn theo số lượng, mỗi excerpt trong generation prompt chỉ có 1.800 ký tự; dễ bỏ mất đoạn có giá trị cho câu hỏi cụ thể.
- `pu/evaluation/report.py`: completeness đếm sáu section có chữ; source coverage chủ yếu kiểm tra có ID nguồn sơ cấp hợp lệ. Semantic judge có kiểm tra nội dung nhưng chưa chấm riêng mức liên quan, trùng lặp và khả năng chuẩn bị cuộc họp.
- Freshness hiện tính trên các nguồn có ngày, chưa tách rõ tin công ty với email/lịch và chưa yêu cầu tin liên quan đến cơ hội.
- `pu/orchestration/research.py`: đã có checkpoint, một lần sửa JSON/claim thiếu nguồn và quality gate. Chưa có vòng tìm bổ sung theo lỗ hổng chất lượng. Các nhánh company/web/calendar/memory hiện chạy bằng vòng lặp tuần tự dù event mang tên fanout.
- `pu/ops/static/js/inbox-chat.js`: đã có trình đọc rộng và link nguồn; cảnh báo còn có thể lặp trong văn bản, danh sách missing_information chưa xử lý citation giống phần nội dung, số trích dẫn liền nhau khó đọc.
- `evals/datasets/report_quality.jsonl`: bốn mô tả tình huống; bài judge hiện chỉ kiểm tra một fixture nhỏ. Chưa đủ bộ đối chứng cho chất lượng bản Vinamilk anh gửi.

## 3. Kết quả người dùng sẽ nhận

Trong chat: đoạn giới thiệu ngắn, 3–5 điểm đáng chú ý liên quan đến cơ hội, đề xuất bước tiếp theo, lịch trống trong phần còn lại của tuần và thẻ mở báo cáo đầy đủ.

Báo cáo đầy đủ có các phần sau, tránh lặp giữa chúng:

1. Tóm tắt: doanh nghiệp, cơ hội và điểm cần quyết định.
2. Hồ sơ doanh nghiệp: website, tên pháp nhân nếu xác minh được, lĩnh vực, sản phẩm/dịch vụ, khách hàng/thị trường.
3. Quy mô và vận hành: chỉ số có nguồn và kỳ báo cáo; chuỗi vận hành, cơ sở, thị trường hoặc mạng lưới phân phối khi có dữ liệu.
4. Nhu cầu từ email và người liên hệ: giữ nguyên mức độ xác minh, không biến lời tự giới thiệu thành xác nhận của doanh nghiệp.
5. Bối cảnh liên quan đến cơ hội: hoạt động công nghệ/chuyển đổi số đã công bố và những đặc điểm vận hành có ích cho cuộc trao đổi.
6. Cập nhật gần đây có liên quan: ngày xuất bản và ý nghĩa với cơ hội; không đưa tin ngoài chủ đề chỉ để đủ mục.
7. Chuẩn bị họp: mục tiêu, khoảng năm câu hỏi ưu tiên, đề xuất pilot dưới dạng giả thuyết cần xác nhận, các cuộc họp liên quan và lịch trống.
8. Khoảng trống thông tin, một cảnh báo danh tính tập trung và khoảng 5–8 link nguồn được sử dụng.

Doanh nghiệp ít dữ liệu công khai vẫn nhận được bản nháp hữu ích, có giới hạn rõ; không bịa thông tin hoặc nguồn để đạt chỉ tiêu.

## 4. Quy ước dữ liệu và cách chấm đề xuất

### Bằng chứng và trạng thái thông tin

- Mỗi nhóm thông tin có trạng thái `supported`, `not_found`, `conflicting`, `not_applicable`; trạng thái và lý do do hệ thống lưu, không bắt người dùng tự điền.
- Mỗi nhận định quan trọng lưu nguồn, đoạn trích hỗ trợ, loại nhận định (sự thật công khai/lời người gửi/đề xuất), ngày xuất bản, ngày quan sát và kỳ số liệu nếu có.
- `not_found` không cộng điểm đầy đủ; vẫn được ghi nhận là đã nghiên cứu có giới hạn. `not_applicable` phải có lý do theo chính sách, không do model tự loại mục để tăng điểm.
- Chỉ tìm kiếm web bằng dữ kiện công khai của doanh nghiệp và chủ đề tổng quát; không đưa nguyên email, địa chỉ cá nhân hoặc dữ liệu nội bộ vào search query.

### Bảng đo

| Tiêu chí | Cách đo và điều kiện sử dụng |
| --- | --- |
| Đầy đủ | Tỷ lệ trường có bằng chứng theo checklist có trọng số; tách hồ sơ doanh nghiệp khỏi chuẩn bị cơ hội. Mục có chữ hoặc chỉ ghi “chưa biết” không được tính là đủ. |
| Chính xác | Kiểm tra tất cả nhận định quan trọng, kể cả summary và section prose, với đoạn trích hỗ trợ. ID hợp lệ chưa đủ chứng minh đúng. Không hiển thị phần trăm “chính xác” chỉ từ điểm LLM. |
| Thời gian | Đo từ lúc nhận yêu cầu đến khi trả kết quả; tách sync, research, generation, judge, repair; ghi cache hit và retry. Tổng hợp p50/p95 theo tập chạy và cấu hình model. |
| Nguồn | Đếm riêng URL công khai chuẩn hóa đã đọc, nguồn thực sự được trích và số publisher độc lập. Email/lịch/agent synthesis không được tính vào số link web. Trang trùng hoặc bài đăng lại không được coi là nhiều nguồn độc lập. |
| Độ mới | Chỉ xét tin công khai đúng công ty và liên quan cơ hội trong 30 ngày; dùng ngày xuất bản, không dùng ngày crawl/email. Không có ngày thì ghi chưa xác định. Hồ sơ và số liệu thường niên hiển thị kỳ tương ứng. |
| Sẵn sàng họp | Kiểm tra mục tiêu, nhu cầu có dẫn chứng, thông tin người liên hệ, câu hỏi ưu tiên, bước tiếp theo và trạng thái lịch. Không hạ điểm chỉ vì email đầu tiên chưa nêu ngân sách; phải chỉ ra cần hỏi gì. |

Ngưỡng chấm số cụ thể sẽ hiệu chỉnh trên tập báo cáo tốt/xấu ở giai đoạn 5 trước khi bật chặn diện rộng. Không dùng một điểm tổng để che lỗi nghiêm trọng.

Phân biệt ba kết quả: “Đủ để chuẩn bị trao đổi”, “Bản nháp còn thiếu dữ liệu” và “Có lỗi cần xử lý”. Mâu thuẫn danh tính công ty, nguồn bịa, nhận định quan trọng không được hỗ trợ hoặc lịch được khẳng định sai phải chặn phát hành. Lệch miền người gửi đơn thuần là cảnh báo quyền đại diện, không tự động chặn nghiên cứu đúng website.

## 5. Các giai đoạn triển khai

### Giai đoạn 1 — Chuẩn hóa schema và tiêu chuẩn nghiệm thu (P0)

- Chốt checklist thông tin, loại nhận định và trạng thái thiếu dữ kiện theo mục 4.
- Mở rộng `pu/reporting/models.py`, `pu/evaluation/models.py`, generation schema trong `pu/reporting/evidence.py`.
- Version schema/policy, đọc tương thích báo cáo cũ; bản cũ chưa có đo lường mới hiển thị “chưa đánh giá”, không được tự gán kết quả đạt.
- Lưu rubric version cùng báo cáo và kết quả đánh giá để tái hiện được lý do chấm.
- Nghiệm thu: fixture có đủ tiêu đề nhưng không có dữ kiện không còn được chấm đầy đủ; thông tin không tìm thấy không biến thành sự thật.

### Giai đoạn 2 — Research theo nhóm thông tin (P0)

- Bổ sung research planner vào `pu/reporting/collection.py`: hồ sơ, sản phẩm/thị trường, quy mô/vận hành, công nghệ và tin gần đây liên quan nhu cầu email.
- Ưu tiên trang chính thức, tài liệu doanh nghiệp và báo cáo thường niên; bổ sung báo chí có liên hệ thực thể được kiểm chứng. LinkedIn chỉ khi công khai và truy cập hợp lệ, không là điều kiện bắt buộc.
- Chọn liên kết theo mục tiêu còn thiếu thay vì chỉ dò about/product. Lưu đoạn trích theo trường để thông tin quan trọng không bị mất do cắt đầu trang.
- Duy trì ranh giới doanh nghiệp trong `pu/reporting/identity.py`; chỉ nới sang nguồn ngoài/miền liên quan nếu có bằng chứng quan hệ, không nới theo tên giống nhau.
- Chuẩn hóa URL, xử lý redirect, loại trang/bài trùng, gắn chất lượng full-page/snippet và trạng thái lỗi truy cập. Snippet không làm bằng chứng duy nhất cho nhận định quan trọng.
- Giới hạn số truy vấn, trang, timeout và token qua cấu hình; dừng khi đủ bằng chứng cho mục tiêu thay vì chạy hết hạn mức.
- Nghiệm thu: có thông tin vận hành liên quan khi fixture cung cấp; không lấy trang sản phẩm ngẫu nhiên hoặc tin ngoài chủ đề để lấp số nguồn.

### Giai đoạn 3 — Viết báo cáo và bổ sung vòng tự sửa có giới hạn (P0)

- Cập nhật `pu/agents/report.py`, `pu/reporting/evidence.py`, `pu/orchestration/research.py`: viết theo bố cục mục 3 và giới hạn độ dài từng phần.
- Tách dữ kiện công khai, lời người gửi và đề xuất. Tập trung cảnh báo quyền đại diện một chỗ; không lặp cảnh báo trong mọi đoạn.
- Kiểm tra lặp ý giữa sections và mức liên quan với nhu cầu kinh doanh. Pilot là gợi ý, không gán thành nhu cầu đã được công ty xác nhận.
- Chọn 5–8 nguồn sử dụng để trình bày; mọi citation công khai trong nội dung phải có link đối chiếu. Nếu nguồn quan trọng vượt giới hạn, rút gọn nội dung hoặc có danh sách đầy đủ trong phần đối chiếu, không cắt mất nguồn đang trích.
- Sau đánh giá, tối đa một vòng sửa theo lỗi cụ thể: tìm bổ sung bằng chứng còn thiếu hoặc viết lại phần không đạt. Dùng checkpoint để không làm lại toàn bộ, áp dụng tổng ngân sách cho cả lần sửa.
- Nếu hết ngân sách hoặc nguồn không khả dụng: trả bản nháp có ích và giới hạn rõ. Chặn nút gửi/lưu như báo cáo hoàn tất theo quality policy.
- Nghiệm thu: bản đối chứng Vinamilk không còn lặp danh mục sản phẩm, không dùng tin ít liên quan để đủ mục, không gọi lời người gửi là cam kết của Vinamilk.

### Giai đoạn 4 — Lịch và trải nghiệm đọc nhất quán (P1)

- Đồng nhất chat/report/background với phạm vi từ hiện tại đến hết Chủ nhật theo giờ Việt Nam trong `pu/reporting/availability.py` và các nơi gọi.
- Render lịch từ dữ liệu có cấu trúc, không để model tự viết giờ mới. Hiển thị thời điểm kiểm tra, múi giờ, nguồn lịch và giới hạn chưa biết lịch đối tác.
- Khi mở lại báo cáo: giữ snapshot gốc, đánh dấu giờ đã qua hoặc dữ liệu cũ; trước đề xuất lịch/gửi, đọc lại availability và hiển thị thay đổi để người dùng duyệt. Chốt tuổi dữ liệu tối đa trong policy.
- Sửa citation `[1], [2]`, xử lý nguồn trong missing_information, thống nhất link chat/trình đọc/bản tải xuống.
- Giữ trình đọc rộng hiện có; nguồn là danh sách link đơn giản. Thêm phần thu gọn “Chất lượng báo cáo” với số đo thật, khoảng trống và thời gian tạo; không đưa mã kỹ thuật vào phần đọc chính.
- Nghiệm thu: không còn `[W1]` ở phần đọc chính, không có lịch đã qua được trình bày là giờ đề xuất còn dùng được, citation dẫn đúng trang trên mọi định dạng.

### Giai đoạn 5 — Bộ đối chứng và quality gate thực chất (P0, xây song song về thiết kế với giai đoạn 1)

- Mở rộng `pu/evaluation/report.py` với kiểm tra cấu trúc và kiểm tra ngữ nghĩa riêng: hỗ trợ nhận định, độ đầy đủ hữu ích, mức liên quan, rõ ràng/không lặp, sẵn sàng họp.
- Kiểm tra độ bao phủ cả summary/sections, tránh việc model chỉ khai báo một ít claims dễ chấm trong khi văn xuôi chứa nhiều khẳng định khác.
- Judge nhận đoạn trích theo nhận định và trả lý do cùng ID/đoạn có vấn đề; không được ghi đè lỗi cứng. Lỗi/timeout judge phải hiển thị chưa kiểm tra, không tự đạt.
- Tập đầu tối thiểu 15 fixture có nguồn chụp sẵn và nhãn mong đợi: bản Vinamilk hiện tại, bản cải thiện, công ty ít dữ liệu, hai công ty trùng tên, lệch miền người gửi, nguồn có link nhưng không hỗ trợ, nguồn chỉ snippet, tin mới không liên quan, ngày không rõ/tương lai, số liệu cũ, cảnh báo lặp, thiếu ngân sách, lịch cũ, Google Calendar lỗi, prompt injection.
- Bổ sung runner thực thi dataset thay vì chỉ lưu mô tả kỳ vọng; tách offline tests với judge/live tests có phát sinh chi phí.
- Hiệu chỉnh rubric/threshold theo nhãn và xem false pass/false fail. Phải có test điểm tổng cao vẫn không vượt qua lỗi nghiêm trọng.
- Nghiệm thu: các fixture sai đều bị đánh dấu đúng; fixture thiếu thông tin công khai được trả nháp trung thực; fixture tốt đạt mà không cần ép đủ 12 nguồn hoặc tin 30 ngày không liên quan.

### Giai đoạn 6 — Đo hiệu năng và triển khai có kiểm soát (P1)

- Gắn timing/usage vào workflow và lần thử: end-to-end, từng stage, thời gian sửa, cache, số tool call, token và chi phí khi provider cung cấp. Xác định rõ thời gian chờ queue.
- Chạy benchmark trên fixture công khai, tách cold/warm cache, ghi model/provider và số mẫu; công bố p50/p95 thực đo, chưa cam kết mốc 42 giây.
- Sau khi có baseline, tối ưu bằng cache đúng hạn và concurrency có giới hạn cho các lần đọc độc lập; giữ rate limit, checkpoint, cancel, ngân sách và không ghi SQLite đồng thời thiếu kiểm soát.
- Version cache theo input, evidence và quality policy; không tái dùng báo cáo cũ như thể đã qua gate mới.
- Áp dụng gate mới theo giai đoạn sau khi fixture đạt, kiểm tra tương thích dữ liệu/báo cáo cũ và giữ đường lui cấu hình. Không sửa nội dung báo cáo đã phát hành một cách âm thầm.
- Chạy lại deterministic suite, JS runtime tests và regression approval/security. Live test chỉ dùng fixture công khai hoặc dữ liệu thật được anh cho phép rõ ràng; không tự gửi inbox thật tới model để benchmark.

## 6. Thứ tự thực hiện và mốc xem kết quả

1. Schema/rubric và bộ fixture đối chứng nền: giai đoạn 1 + phần đầu giai đoạn 5.
2. Thu thập theo mục tiêu và bản báo cáo mới: giai đoạn 2 → 3.
3. Hoàn thiện evaluator, hiệu chỉnh gate: phần còn lại giai đoạn 5; hard gate cũ vẫn giữ trong quá trình làm.
4. Đồng nhất lịch, citation và phần chất lượng trong giao diện: giai đoạn 4.
5. Benchmark, tối ưu và rollout: giai đoạn 6.

Mốc đầu để anh xem: so sánh bản Vinamilk trước/sau trên cùng bộ nguồn đã chụp, kèm chỉ ra phần đã biết, chưa biết và nguồn hỗ trợ. Không hứa nghiên cứu được những thông tin không có trong bộ nguồn. Mốc cuối: chạy đầy đủ luồng trên dữ liệu kiểm thử, hiển thị report và kết quả đánh giá/thời gian thực; người dùng chỉ đọc và duyệt.

## 7. Điều kiện hoàn thành

- Nghiên cứu đúng doanh nghiệp, không trộn hồ sơ hoặc tự xác nhận quyền đại diện.
- Hồ sơ doanh nghiệp có chiều sâu tương ứng bằng chứng thu thập được, nội dung gắn với cơ hội và không lặp để đủ mục.
- Nhận định quan trọng có nguồn hỗ trợ; nguồn dễ mở và kiểm tra; thiếu dữ kiện được nói rõ.
- Có đề xuất chuẩn bị cuộc họp cụ thể, lịch đúng phạm vi và trạng thái cập nhật minh bạch.
- Sáu tiêu chí được đo/đánh giá theo cách có thể giải thích; không trình bày số mẫu hoặc tự chấm như kết quả thực nghiệm.
- Tự động nghiên cứu và sửa có giới hạn; phê duyệt của người dùng vẫn bắt buộc trước gửi email/lưu tri thức.

Các quyết định mặc định để tránh làm chậm triển khai: giữ 5–8 link hiển thị, ưu tiên chất lượng hơn số lượng; 30 ngày là cửa sổ ưu tiên cho tin liên quan; chưa đặt SLA thời gian cho tới khi có benchmark. Ngưỡng và ngân sách cụ thể nằm trong cấu hình và được chốt bằng dữ liệu kiểm thử.
