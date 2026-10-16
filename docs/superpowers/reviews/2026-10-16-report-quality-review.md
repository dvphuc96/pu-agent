# Review trực tiếp phần report quality sau tích hợp subagent

Ngày: 16/10/2026. Phạm vi: collector/planner, nguồn và schema, evaluator, workflow/retry/cache, lịch, reader và export. Đây là review trực tiếp source hiện tại; thư mục không có metadata Git để xác định diff/base commit. Không gọi model thật, không dùng computer use, không sửa dữ liệu ứng dụng trong lượt review. Các reproduction tạo database riêng trong thư mục tạm và dùng fake provider/runtime.

## Kết luận

Chưa thể kết luận code sạch hoặc hiệu năng tốt. Lượt trước đã chạy kiểm thử tích hợp (634 Python đạt, 13 bỏ qua, 27 JavaScript đạt và Ruff trên file thay đổi đạt), nhưng bộ test còn bỏ sót lỗi hợp đồng dữ liệu, điều kiện retry và hiệu quả sử dụng các nhánh agent. Không đánh đồng test pass với đã chứng minh độ chính xác hoặc tốc độ thực tế.

## Các phát hiện

### R1 — P1: Kiểm tra hỗ trợ dữ kiện có false positive; chưa bắt buộc judge trên mọi đường research

Vị trí: `pu/evaluation/report.py:74`, `pu/evaluation/report.py:257`, `pu/evaluation/report.py:295`, `pu/ops/workspace_api.py:245`.

`_text_supported` chỉ yêu cầu giao tập từ có ít nhất một từ không thuộc danh sách generic. Tên công ty như Acme đủ làm cho câu mới có số liệu bịa được coi là có nguồn hỗ trợ. Reproduction dùng fixture có “Acme has 120 employees”, thêm claim “Acme has 999999 employees and revenue of 987654 million dollars” trỏ cùng nguồn. Evaluator trả `source_coverage=1.0`, claim supported và scale được cộng điểm.

Luồng inbox tự động có `require_semantic_review=True`, nên còn lớp judge để có thể chặn lỗi này; không kết luận false positive trên đã vượt qua judge thật. Tuy nhiên, nhánh notebook `op=research` không truyền cờ bắt buộc, mặc định không yêu cầu semantic review. Với report tương ứng, deterministic gate có thể cho phép phát hành dù chưa đối chiếu ngữ nghĩa.

Hướng sửa: bắt buộc đối chiếu ngữ nghĩa cho mọi report v2 được phép phân phối; xem kiểm tra từ vựng là heuristic, không gọi đó là bằng chứng xác thực. Kiểm tra số liệu/ngày/đơn vị quan trọng và trả kết quả hỗ trợ theo từng claim/field. Bổ sung counterexample có cùng tên công ty nhưng số liệu khác.

### R2 — P1: URL redirect kết hợp kết quả search trùng có thể làm collector crash

Vị trí: `pu/reporting/collection.py:287`, `pu/reporting/collection.py:419`.

`accepted_urls` lưu URL yêu cầu ban đầu, còn `collected` lưu URL cuối sau redirect. Khi bài báo ngoài domain xuất hiện lần nữa và snippet không đủ chứng minh quan hệ, nhánh `if item_key in accepted_urls` truy cập `collected[item_key]`. Hai khóa có thể khác nhau.

Reproduction: search trả `https://news.example/story`; fetch trả `https://news.example/story/amp` có liên kết tới `acme.example`; truy vấn tiếp theo trả URL ban đầu. Collector ném `KeyError: 'https://news.example/story'`, thay vì tiếp tục với nguồn đã đọc.

Hướng sửa: lưu ánh xạ requested URL → canonical/final URL và hợp nhất metadata qua khóa đã resolve. Lỗi xử lý một kết quả không nên hủy toàn bộ research.

### R3 — P1: Báo cáo bị judge chấm thấp lại không chạy quality repair

Vị trí: `pu/evaluation/report.py:558`, `pu/orchestration/research.py:443`.

Judge bị thiếu/lỗi và judge trả điểm thấp hợp lệ cùng được ghi `semantic_review='unavailable_or_incomplete'`. Orchestrator bỏ qua quality repair với trạng thái này. Vì vậy các lỗi relevance, completeness hoặc nondup mà judge phát hiện không được tự sửa như thiết kế.

Reproduction dùng `DefaultEvaluationHarness` thật với fake judge trả score 4 và đủ năm tiêu chí. Workflow chỉ sinh một lần, `quality_repair_attempts=0`, dù có response sửa hợp lệ sẵn trong fake agent.

Hướng sửa: tách `unavailable`, `incomplete`, `failed`, `passed`; chỉ bỏ qua retry khi không có đánh giá sử dụng được. Test phải đi qua harness thật, không chỉ fake evaluator tự gán trạng thái.

### R4 — P2: Bốn nhánh LLM chạy tuần tự nhưng kết quả không được dùng trong bounded report generation

Vị trí: `pu/orchestration/research.py:236`, `pu/orchestration/research.py:266`, `pu/orchestration/research.py:282`.

Nhánh company/web/calendar/memory nhận lại toàn bộ nguồn và chạy tuần tự. Sau đó bounded workflow thay evidence sinh từ stages bằng bundle rỗng, rồi chỉ nhập nguồn gốc. Generation prompt không nhận các kết quả phân tích của bốn nhánh này.

Reproduction ghi nhận đủ sáu lần gọi agent runtime theo thứ tự email/company/web/calendar/memory/report. Sentinel chỉ xuất hiện trong kết quả Web Agent không xuất hiện trong prompt viết report. Bên cạnh đó còn classifier và judge. Đây là đếm lời gọi ở fixture, không phải benchmark độ trễ provider thật.

Hướng sửa: dùng các agent để tạo phân tích có cấu trúc gắn nguồn, truyền phân tích như ngữ cảnh không được coi là nguồn sơ cấp; hoặc thay nhánh không cần suy luận bằng xử lý xác định. Sau khi mỗi nhánh có đầu ra hữu ích mới cân nhắc concurrency có giới hạn. Không chỉ chạy song song các lời gọi dư.

### R5 — P2: Lịch hết hạn làm mất cache cả phần nghiên cứu doanh nghiệp

Vị trí: `pu/orchestration/inbox_chat.py:497`, `pu/orchestration/inbox_chat.py:543`.

Cache toàn bộ report phụ thuộc snapshot lịch còn hiệu lực 15 phút. Lịch cũ khiến collector, các agent và judge chạy lại, dù email và dữ kiện công ty không đổi.

Reproduction trên database tạm: hỏi inbox lần đầu; đổi riêng timestamp snapshot thành 16 phút trước; hỏi lại. Có hai lần collector, hai workflow và hai report khác nhau.

Hướng sửa: tách cache bằng chứng doanh nghiệp, phân tích và availability theo TTL riêng. Refresh lịch và tạo phiên bản snapshot/phê duyệt mới khi cần, giữ tính bất biến của report đã được duyệt.

### R6 — P2: Collector vẫn tìm kiếm khi không còn ngân sách đọc trang

Vị trí: `pu/reporting/collection.py:290`, `pu/reporting/collection.py:381`.

`read_public_page` dừng khi hết `max_read_attempts`, nhưng vòng search chỉ kiểm tra deadline. Các URL mới trả về sau đó không được đọc hoặc thêm như nguồn fallback.

Reproduction cấu hình ngân sách một lần đọc: homepage dùng hết; collector vẫn gọi đủ năm truy vấn search, cuối cùng chỉ lưu một nguồn public. Trong cấu hình mặc định điều này cũng xảy ra khi các nhóm đầu dùng hết 12 lần đọc trước nhóm tin gần đây.

Hướng sửa: phân bổ budget theo chủ đề, dành khả năng đọc cho tin/công nghệ và dừng search không còn khả năng sử dụng kết quả.

### R7 — P2: Dữ liệu news thực tế của collector không khớp cách evaluator nhận diện tin

Vị trí: `pu/tools/web_source.py:121`, `pu/reporting/collection.py:276`, `pu/evaluation/report.py:336`.

Web reader trả `source_type='web'`. Collector gắn `research_topics`, kể cả `recent_relevant_news`, nhưng không gắn `is_news`/`content_kind`. Evaluator nhận tin qua type news/article/press_release hoặc hai cờ này, không qua topic. Tin công khai có ngày trong pipeline thực tế có thể bị tính là không có tin xác định ngày.

Reproduction chuyển source tin có ngày ở fixture sang đúng dạng collector trả: type web, topic recent_relevant_news. `public_news_known_sources=0`, freshness=0. Không nên sửa bằng cách coi mọi trang xuất hiện ở truy vấn news là tin liên quan.

Hướng sửa: định nghĩa chung loại tài liệu và trạng thái liên quan, phân loại bằng chứng bài viết từ metadata/nội dung, rồi test nối collector → normalizer → evaluator bằng cùng fixture.

### R8 — P2: Citation/export chưa có một hợp đồng trình bày chung

Vị trí: `pu/reporting/render.py:186`, `pu/reporting/render.py:231`, `pu/reporting/docx.py:48`, `pu/reporting/docx.py:62`.

HTML/PDF đánh số theo URL được sort; chat/Markdown theo thứ tự nguồn xuất hiện và cách dedup khác. DOCX vẫn đưa `[W1]`/`[S1]` nguyên văn vào summary/section nhưng danh sách nguồn chỉ có title/hyperlink, không có ID/số tương ứng để lần theo. Thêm nữa, `_cited_source_ids` không tính citation trong missing_information, nên nguồn ngoài tám link đầu chỉ được tham chiếu ở mục này có thể bị bỏ khỏi source panel HTML, để lại anchor không có đích.

Hướng sửa: một citation presentation model dùng chung cho chat, Markdown, HTML/PDF, DOCX; tập hợp citation từ summary, sections, claims, missing information và contradictions. Test phải kiểm tra mọi citation có đích, không chỉ có hyperlink bất kỳ trong file.

## Nhận xét về code clean

- Tốt: có schema, ranh giới nguồn công ty, hạn mức collector, checkpoint, fail-closed khi judge lỗi ở luồng tự động và giữ phê duyệt trước hành động ghi/gửi.
- Còn yếu: enum/field/policy version được lặp giữa nhiều module; xử lý citation và freshness lặp giữa các renderer; collector và orchestrator là hàm lớn, nhiều closure và nhánh trạng thái. Các lỗi R2/R3/R7 là hậu quả cụ thể của hợp đồng liên module chưa chặt.
- Tests tập trung từng module và fake judge thường trả điểm cao cố định. Chưa đủ kiểm thử nối evaluator thật với orchestrator, collector thật với evaluator hoặc so sánh các export.

## Performance đã và chưa kiểm chứng

Đã xác nhận các chi phí dư tại R4–R6 bằng fake runtime/tool và số lần gọi. Chưa đo latency hoặc chi phí model thật. Đọc thống kê local ở thời điểm review có ba báo cáo đã lưu, không có báo cáo v2 và không có duration workflow v2. Chưa có cơ sở công bố p50/p95 live hoặc khẳng định nhanh hơn trước.

Thứ tự xử lý đề xuất: R1/R2/R3 trước; sau đó hợp đồng nguồn/citation (R7/R8); rồi giảm call dư, tách cache và phân bổ ngân sách (R4/R5/R6). Sau khi có baseline được phép mới tối ưu concurrency và xác nhận cải thiện bằng cùng tập dữ liệu, provider và model.

Tại thời điểm viết review ban đầu chỉ tái hiện offline, chưa áp dụng bản sửa.

## Cập nhật sau khi anh yêu cầu sửa

Đã bổ sung bản sửa cho R1–R8 cùng regression tests: grounding số/ngày/đơn vị + semantic bắt buộc trên v2; trạng thái judge và quality repair đúng; alias redirect; bỏ preprocessing LLM dư ở bounded workflow; TTL bằng chứng công ty riêng lịch; ngân sách đọc/search theo topic; hợp đồng news/relevance; citation chung cho các định dạng. Policy schema vẫn v2, rubric được tăng lên `report-quality-rubric-v3` để kết quả đánh giá cũ không được dùng như đã qua gate mới.

Việc giảm số lời gọi và tái sử dụng bằng chứng đã được kiểm tra bằng runtime/tool giả lập, không phải benchmark p50/p95 của provider thật. Chi tiết vận hành tại `docs/report-quality.md`.

Xác nhận sau tích hợp: 662 deterministic Python tests đạt, 13 bỏ qua theo điều kiện môi trường; 28 JavaScript runtime tests đạt; Ruff trên các file thay đổi đạt. Các regression đi qua collector thật với tool giả lập, evaluator thật với judge giả lập và toàn bộ luồng inbox → workflow → approval để kiểm tra các điểm nối từng bị bỏ sót.
