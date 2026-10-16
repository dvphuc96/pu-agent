# Báo cáo đối tác: chuẩn chất lượng v2

Luồng chat và tác vụ nền dùng chung schema `report-quality-v2`, được đánh giá bằng rubric `report-quality-rubric-v3`. Người dùng không phải chọn nguồn hoặc điền form research: PU đọc email, thu thập thông tin công khai, kiểm tra lịch và viết báo cáo. Lưu tri thức hoặc gửi email vẫn cần phê duyệt đúng phiên bản báo cáo.

## Nội dung và bằng chứng

Báo cáo bổ sung quy mô/vận hành, bối cảnh kinh doanh/công nghệ và mục tiêu/câu hỏi chuẩn bị họp. Hồ sơ doanh nghiệp tách khỏi lời tự giới thiệu và yêu cầu trong email. Các đề xuất thử nghiệm phải được nhận diện là đề xuất, không phải cam kết đã được công ty xác nhận.

Checklist gồm website, lĩnh vực, sản phẩm, quy mô, thị trường, bối cảnh công nghệ, liên hệ, yêu cầu, chuẩn bị họp và lịch. Mỗi mục ghi nhận đã có nguồn, chưa tìm thấy, mâu thuẫn hoặc ngoài phạm vi. “Chưa tìm thấy” không tăng độ đầy đủ. Ngoài phạm vi không tự làm giảm mẫu số để tăng điểm.

Collector nghiên cứu theo nhóm thông tin, trích đoạn có liên quan và giữ ngày công bố. Mặc định tối đa 5 truy vấn, 12 lần đọc, 12 trang web và kiểm tra ngân sách thời gian 45 giây giữa các lần gọi. Request đang chạy vẫn chịu timeout riêng của web tool; đây không phải cam kết toàn bộ báo cáo hoàn thành trong 45 giây. Nội dung email không được chép vào truy vấn; chỉ những từ khóa chủ đề công khai nằm trong allowlist được sử dụng.

Nguồn được chuẩn hóa URL, gắn phạm vi công ty và mức bằng chứng full-page/snippet. Tên gần giống không đủ để gộp hai doanh nghiệp. Website công ty và miền email người gửi được lưu riêng; chưa xác minh quyền đại diện là cảnh báo, không phải lý do tự động loại nhu cầu hợp tác.

## Đánh giá và phát hành

- Checklist có trọng số thay việc đếm section có chữ. Không trình bày chỉ số này như phần trăm mọi dữ kiện đã đúng.
- Kiểm tra nguồn sơ cấp, ID không hợp lệ, nguồn chỉ có snippet, trùng claim, quyền đại diện và trạng thái lịch. Kiểm tra từ vựng là bước lọc, không thay xác minh ngữ nghĩa.
- Judge bắt buộc cho mọi report v2, kể cả research thủ công. Judge đọc cả summary, section prose và claims so với trích đoạn; chấm hỗ trợ dữ kiện, đầy đủ, liên quan, sẵn sàng họp và không lặp. Sai số liệu, ngày, đơn vị hoặc ID nguồn là lỗi cứng. Câu dịch/paraphrase không vượt bộ lọc từ vựng cần verdict theo claim cụ thể, không chỉ điểm tổng cao. Claim chưa được hỗ trợ vẫn chặn phát hành dù coverage vượt 80%.
- Semantic review phân biệt `unavailable`, `incomplete`, `failed`, `passed`. Thiếu judge/thiếu tiêu chí giữ nháp; điểm thấp hợp lệ có thể kích hoạt một lần tự sửa. Judge không thể ghi đè lỗi cứng. Ngưỡng mặc định: factual support ít nhất 7/10, các tiêu chí khác ít nhất 6/10, tổng ít nhất 7/10.
- Nguồn công khai, nguồn công khai được trích, tổng số nguồn và số publisher được đếm riêng. Email/lịch không làm tăng số nguồn web. Publisher/domain count là chỉ số đa dạng nguồn, không phải chứng nhận độc lập biên tập.
- Chỉ tin công khai liên quan có ngày công bố được xét độ mới. Email mới, ngày crawl, tin không ngày và tin tương lai không chứng minh công ty có tin mới trong 30 ngày.
- Có tối đa một lần tự sửa, dùng chung ngân sách với sửa JSON. Không thể tự sửa được thì trả nháp có giới hạn rõ; lỗi provider trong lần sửa chất lượng không xóa bản nháp đầu tiên.
- Cache báo cáo có phiên bản quality/evidence policy và rubric. Bằng chứng doanh nghiệp có TTL 6 giờ riêng; lịch có TTL 15 phút. Khi chỉ lịch cũ, PU dùng lại bằng chứng công ty, lấy snapshot lịch mới và viết/đối chiếu phiên bản report mới, không tải lại website. Report cũ và phê duyệt cũ không bị thay nội dung. Nguồn đã tắt không được tái sử dụng qua cache.
- Trong bounded workflow (đã thu thập nguồn workspace), năm role email/company/web/calendar/memory định tuyến nguồn có cấu trúc bằng xử lý local, có checkpoint và event `deterministic=true`; không gọi LLM để tạo các bản tổng hợp bị bỏ đi. Thông thường phần research chỉ cần một lần sinh report và một lần judge; classifier và web search là các bước riêng. Không tuyên bố đây là số call tối đa của toàn bộ chat khi có retry hoặc nhiều đối tác.

Nguồn đọc chính là các link đánh số, khoảng 5–8 link khi có đủ nguồn phù hợp. Chat, Markdown, HTML/PDF và DOCX dùng citation presentation chung: số theo URL public xuất hiện đầu tiên, alias dùng cùng số, hyperlink giữ URL thực đã đọc. Citation từ thông tin còn thiếu và mâu thuẫn cũng phải có đích. Citation quan trọng không bị mất khi vượt tám link; các nguồn bổ sung nằm ở phần mở rộng/đối chiếu. Không thêm link ít giá trị để đủ số.

Tin công khai dùng chung metadata `content_kind` và relevance. Chỉ bài thực có dấu hiệu bài viết/ngày/schema được xét là tin; snippet, email, lịch và chỉ tên topic `recent_relevant_news` chưa đủ. Keyword liên quan dùng whole word/phrase, ví dụ `AI` không khớp nhầm `chairman`. Collector resolve requested URL → final URL để merge kết quả redirect an toàn; dừng search khi hết năng lực đọc/sử dụng kết quả và chia ngân sách theo chủ đề.

## Lịch

Chat và research tự động mặc định xét từ hiện tại tới hết Chủ nhật theo giờ Việt Nam. Lịch của người dùng không xác nhận lịch của đối tác. Calendar lỗi không được coi là lịch trống.

Snapshot lịch có timestamp, múi giờ và nguồn local/Google. Trình đọc không đề xuất giờ đã qua hoặc snapshot quá 15 phút. Bản tải xuống giữ snapshot gốc và ghi rõ thời điểm kiểm tra. Khi gửi, nếu các giờ đề xuất đã hết hiệu lực, công cụ từ chối trước khi gọi Gmail; cần tạo báo cáo cập nhật và duyệt lại. Công cụ không âm thầm thay nội dung đã được phê duyệt.

## Kiểm thử offline

Từ thư mục repository:

```sh
rtk proxy .venv/bin/pytest -q evals/deterministic
rtk proxy node --test evals/deterministic/test_notebook_runtime.cjs evals/deterministic/test_report_reader_quality.cjs
```

Nếu Node không nằm trong PATH, dùng runtime Node đã cấu hình trên máy. Bộ fixture thực thi trong `evals/datasets/report_quality.jsonl` đi cùng runner `evals/deterministic/test_report_quality_v2.py`. Các test dùng nguồn giả lập, không gửi email/inbox thật tới model và không thực hiện gửi email.

## Thời gian và giới hạn kiểm chứng

`metadata.performance` đo workflow nghiên cứu: từng stage, checkpoint reuse, evaluation/repair và usage nếu được observer cung cấp. `metadata.collection_performance` đo riêng thu thập nguồn; tổng latency chat đã có trong metadata hội thoại. Các phạm vi này không được đánh đồng.

Tổng hợp các bản ghi performance đã loại bỏ nội dung nhạy cảm:

```sh
rtk proxy .venv/bin/python evals/benchmark_report_quality.py performance-records.json
```

CLI chỉ đọc JSON/JSONL được cung cấp, tách cold/warm và provider/model, tính p50/p95 bằng nearest-rank. Chỉ tổng hợp bản ghi có scope `research_workflow`; không trộn thời gian end-to-end vào mẫu workflow.

Chưa công bố SLA hoặc số đo chất lượng thực tế trên inbox/Vinamilk sau thay đổi. Kiểm thử offline chứng minh hành vi của pipeline và các trường hợp đối chứng, không chứng minh mọi câu trả lời model ngoài thực tế đều đúng. Cần đánh giá trên lượt sử dụng thực tế được phép trước khi hiệu chỉnh ngưỡng hoặc quyết định tối ưu concurrency.
