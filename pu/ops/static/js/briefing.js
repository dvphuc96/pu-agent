// Business-facing entry: reports arrive here; people read and approve.
const BRIEF = {data:null, workspaces:[], loading:false, error:'', busy:false};
async function briefRefresh(){
  if(BRIEF.loading)return;
  BRIEF.loading=true;
  try{
    const [state,library]=await Promise.all([fetch('/api/briefing').then(r=>r.json()),fetch('/api/workspaces').then(r=>r.json())]);
    if(state.error||library.error)throw new Error(state.error||library.error);
    BRIEF.data=state;BRIEF.workspaces=library.items||[];BRIEF.error='';
    const [view,id]=(location.hash||'#today').slice(1).split('/');
    if(['today','review'].includes(view)||view==='reports'&&!id)briefPaint();
    if(['research','reports'].includes(view)&&id){
      const latest=state.jobs.find(j=>j.workspace_id===id);
      if(latest&&latest.status==='completed'&&latest.report_id&&NB.detail&&!NB.detail.report&&!NB.busy)await nbLoad(id);
      const banner=document.getElementById('nb-job-status');if(banner)banner.innerHTML=briefJobBanner(id);
    }
  }catch(e){BRIEF.error=e.message;briefPaint();}
  finally{BRIEF.loading=false;}
}
function briefPaint(){
  const [view,id]=(location.hash||'#today').slice(1).split('/');
  if(!['today','review','reports'].includes(view)||view==='reports'&&id)return;
  if(document.activeElement&&document.activeElement.closest&&document.activeElement.closest('#brief-settings'))return;
  document.getElementById('view').innerHTML=view==='review'?briefReview():view==='reports'?briefReports():briefToday();
}
function briefHead(eyebrow,title,description){
  return `<div class="brief-heading"><div><div class="nb-eyebrow">${esc(eyebrow)}</div><h1>${title}</h1><p>${description}</p></div><span class="brief-date">${new Date().toLocaleDateString('vi-VN',{weekday:'long',day:'numeric',month:'long'})}</span></div>`;
}
function briefActions(){return ((typeof D!=='undefined'&&D&&D.approvals||{}).items||[]).filter(a=>['save_report_to_knowledge','forward_report_email'].includes(a.tool_name));}
function briefToday(){
  if(!BRIEF.data&&!BRIEF.loading)setTimeout(briefRefresh,0);
  const x=BRIEF.data||{jobs:[]},pending=briefActions().filter(a=>['pending','approved'].includes(a.status));
  const running=x.jobs.filter(j=>['running','queued','retrying'].includes(j.status));
  const ready=x.jobs.filter(j=>j.status==='completed');
  return `<div class="nb-root brief-page">${briefHead('TRỢ LÝ ĐỐI TÁC','Báo cáo đã sẵn sàng.<br><span>Anh đọc, PU chuẩn bị.</span>','Hỏi PU trong Trò chuyện để nhận báo cáo ngay trong chat. Trang này theo dõi các tác vụ nền.')}
    ${BRIEF.error?`<div class="nb-alert">${esc(BRIEF.error)}</div>`:''}
    <p style="margin-bottom:20px"><a class="nb-btn primary" href="#chat">Hỏi PU về email hôm nay →</a></p><div class="brief-summary"><a href="#reports"><b>${ready.length}</b><span>Báo cáo đã chuẩn bị</span></a><a href="#review"><b>${pending.length}</b><span>Hành động chờ duyệt</span></a><div><b>${running.length}</b><span>Đang chuẩn bị</span></div></div>
    <section class="brief-automation"><div><span class="brief-indicator ${x.enabled&&x.worker_active?'on':''}"></span><b>${x.enabled?(!x.model_configured?'Chờ kết nối model':x.worker_active?'Tự động đang chạy':'Đang chờ worker chạy'):'Đã tạm dừng tự động'}</b><p>Kiểm tra inbox mỗi 15 phút khi dịch vụ đang chạy. Lần gần nhất: ${x.last_scan?nbDate(x.last_scan):'Chưa kiểm tra'}.</p><p>${esc(x.scan_message||'Kết nối Gmail một lần; PU sẽ tự lấy email mới, đọc nguồn và tạo báo cáo.')}</p>${x.meeting_scan_message?`<p>${esc(x.meeting_scan_message)}</p>`:''}</div><button class="nb-btn" onclick="briefCommand({op:'scan'})" ${BRIEF.busy?'disabled':''}>Kiểm tra ngay</button></section>
    ${!x.model_configured?'<div class="brief-connect"><div><h3>Kết nối model để PU nghiên cứu</h3><p>Thiết lập một lần trong Kết nối tài khoản. Không cần nhập lại yêu cầu cho mỗi đối tác.</p></div><a class="nb-btn primary" href="#connections">Thiết lập model</a></div>':''}
    ${!x.gmail_connected?'<div class="brief-connect"><div><h3>Kết nối Gmail để bắt đầu</h3><p>Chỉ cần thiết lập một lần. Quyền đọc email và quyền gửi email được kết nối riêng.</p></div><a class="nb-btn primary" href="#connections">Kết nối tài khoản</a></div>':''}
    <div class="brief-section-head"><h2>Cần anh xem</h2><a href="#reports">Tất cả báo cáo →</a></div>
    <div class="brief-report-list">${x.jobs.filter(j=>j.status!=='skipped').slice(0,12).map(briefJobCard).join('')||'<div class="brief-empty"><h3>PU đang chờ dữ liệu đầu tiên</h3><p>Email doanh nghiệp mới trong 7 ngày gần đây sẽ được gom theo domain và nghiên cứu tự động. Anh cũng có thể thêm đối tác một lần từ trang Đối tác.</p><a href="#research" class="nb-btn">Mở đối tác</a></div>'}</div>
    <details class="brief-settings"><summary>Thiết lập tự động & phạm vi dữ liệu</summary><form id="brief-settings" onsubmit="briefSettings(event)"><p>Nhận diện ban đầu theo domain email; báo cáo phải đối chiếu danh tính công ty. Email cá nhân và thông báo tự động được bỏ qua (${x.skipped_emails||0} email). Không tự gửi email hoặc lưu tri thức khi chưa duyệt.</p><label>Người nhận báo cáo mặc định (không bắt buộc)<input name="recipient" type="email" value="${esc(x.recipient||'')}" placeholder="Địa chỉ nội bộ để PU tự soạn đề xuất gửi"></label><label>Domain cần bỏ qua<input name="ignored_domains" value="${esc(x.ignored_domains||'')}" placeholder="yourcompany.com, notifications.example.com"></label><label class="brief-checkbox"><input name="enabled" type="checkbox" ${x.enabled?'checked':''}> Tự chuẩn bị báo cáo</label><button class="nb-btn primary" ${BRIEF.busy?'disabled':''}>Lưu thiết lập</button><p>Lịch trống chỉ phản ánh lịch của bạn. Báo cáo và phê duyệt có thể xem lại sau khi khởi động dịch vụ.</p></form></details></div>`;
}
function briefJobCard(job){
  const isReady=job.status==='completed',isFailed=job.status==='failed',skipped=job.status==='skipped';
  return `<article class="brief-report-card"><span class="nb-monogram tone-1">${esc(job.company.slice(0,2).toUpperCase())}</span><div class="brief-card-text"><span class="brief-status ${isFailed?'warning':''}">${esc(job.stage_label)}</span><h3><a href="#research/${esc(job.workspace_id)}">${esc(job.company)}</a></h3><p>${isReady?'Hồ sơ đối tác, nhu cầu, nguồn tham khảo và chuẩn bị cuộc gặp.':isFailed?esc(job.error):skipped?'Email không có nhu cầu hợp tác hoặc chuẩn bị cuộc gặp.':job.status==='retrying'?'Tự thử lại lúc '+nbDate(job.next_attempt):'PU đang tự thu thập và đối chiếu thông tin.'}</p><small>${nbDate(job.updated_at)}</small></div>${isReady?`<a class="nb-btn primary" href="#research/${esc(job.workspace_id)}">Đọc báo cáo →</a>`:isFailed?`<button class="nb-btn" onclick="briefCommand({op:'retry',id:'${esc(job.id)}'})">Thử lại</button>`:skipped?'':'<span class="brief-spinner" aria-label="Đang chuẩn bị"></span>'}</article>`;
}
function briefJobBanner(id){
  const job=BRIEF.data&&BRIEF.data.jobs.find(j=>j.workspace_id===id);
  if(!job)return '';
  if(job.status==='completed'){
    if(NB.detail&&NB.detail.report&&job.report_id&&job.report_id!==NB.detail.report.payload.report_id)return `<div class="nb-notice">Có bản báo cáo mới. <button class="nb-btn" onclick="nbVersion('${esc(job.report_id)}')">Đọc bản mới</button></div>`;
    return '';
  }
  return `<div class="${job.status==='failed'?'nb-alert':'nb-notice'}" role="status"><b>${esc(job.stage_label)}</b> · ${job.status==='failed'?esc(job.error):'Báo cáo sẽ xuất hiện khi hoàn tất. Anh có thể rời trang này.'}${job.status==='failed'?` <button class="nb-btn" onclick="briefCommand({op:'retry',id:'${esc(job.id)}'})">Thử lại</button>`:''}</div>`;
}
function briefReports(){
  if(!BRIEF.data&&!BRIEF.loading)setTimeout(briefRefresh,0);
  const items=BRIEF.workspaces.filter(w=>w.report_count);
  return `<div class="nb-root brief-page">${briefHead('THƯ VIỆN','Báo cáo đối tác','Mở báo cáo để đọc nhu cầu, kiểm tra dẫn chứng và duyệt bước tiếp theo.')}<div class="nb-library-grid">${items.map(w=>`<a class="nb-customer" href="#reports/${esc(w.id)}"><span class="nb-small-label">${w.report_count} phiên bản · ${w.source_count} nguồn</span><h3>${esc(w.company)}</h3><p>${esc(w.website||w.contact)}</p><span>Đọc báo cáo →</span></a>`).join('')||'<div class="brief-empty">Chưa có báo cáo. PU sẽ tự chuẩn bị từ inbox đã kết nối.</div>'}</div></div>`;
}
function briefReview(){
  const items=briefActions().filter(a=>['pending','approved'].includes(a.status));
  return `<div class="nb-root brief-page">${briefHead('QUYẾT ĐỊNH','Chờ anh phê duyệt','Đọc đúng báo cáo và nội dung hành động trước khi duyệt.')}<div class="brief-report-list">${items.map(a=>{const w=BRIEF.workspaces.find(w=>w.id===a.arguments.workspace_id);return `<article class="brief-report-card"><span class="nb-monogram">${nbIcon(a.tool_name==='forward_report_email'?'send':'book')}</span><div class="brief-card-text"><span class="brief-status">${a.tool_name==='forward_report_email'?'Gửi báo cáo qua email':'Lưu báo cáo vào tri thức'}</span><h3>${esc(w?w.company:a.arguments.report_id)}</h3><p>${a.arguments.recipient?'Người nhận: '+esc(a.arguments.recipient):'Giữ lại báo cáo có nguồn để dùng cho các lần gặp sau.'}</p><small>Hết hạn: ${nbDate(a.expires_at)}</small></div><button class="nb-btn primary" onclick="briefOpenApproval('${esc(a.arguments.workspace_id)}','${esc(a.arguments.report_id)}')">Đọc & duyệt →</button></article>`;}).join('')||'<div class="brief-empty"><h3>Không có hành động chờ duyệt</h3><p>PU chỉ chuẩn bị đề xuất khi báo cáo vượt qua kiểm tra nguồn. Nội dung thiếu bằng chứng vẫn được giữ để xem.</p></div>'}</div></div>`;
}
async function briefOpenApproval(id,report){location.hash='research/'+id;await nbLoad(id,report);}
async function briefCommand(payload){
  if(BRIEF.busy)return;
  BRIEF.busy=true;
  try{const r=await postJSON('/api/briefing',payload);if(r.error)throw new Error(r.error);BRIEF.data=r;BRIEF.error='';}
  catch(e){BRIEF.error=e.message;}
  finally{BRIEF.busy=false;briefPaint();}
}
async function briefSettings(event){event.preventDefault();const f=event.target;await briefCommand({op:'settings',recipient:f.elements.recipient.value,ignored_domains:f.elements.ignored_domains.value,enabled:f.elements.enabled.checked});}
async function nbAutoResearch(){
  await nbTask(async()=>{const r=await postJSON('/api/briefing',{op:'enqueue',workspace_id:NB.detail.workspace.id});if(r.error)throw new Error(r.error);BRIEF.data=r;NB.notice='PU đang tự chuẩn bị báo cáo. Anh có thể rời trang và quay lại sau.';nbPaint();});
}
function nbApprovalCards(){
  const stored=NB.detail.report;if(!stored)return '';
  const actions=briefActions().filter(a=>a.arguments.workspace_id===NB.detail.workspace.id&&a.arguments.report_id===stored.payload.report_id);
  return actions.map(a=>`<section class="brief-inline-approval"><span class="nb-small-label">${a.tool_name==='forward_report_email'?'GỬI EMAIL':'LƯU TRI THỨC'}</span><h3>${a.tool_name==='forward_report_email'?'Duyệt gửi báo cáo':'Duyệt lưu báo cáo'}</h3>${a.arguments.recipient?`<dl><dt>Người nhận</dt><dd>${esc(a.arguments.recipient)}</dd><dt>Tiêu đề</dt><dd>${esc(a.arguments.subject)}</dd><dt>Lời mở đầu</dt><dd>${esc(a.arguments.intro)}</dd></dl>`:'<p>Lưu toàn bộ báo cáo đang đọc, kèm nguồn tham khảo, vào tri thức nội bộ.</p>'}<p class="nb-muted">Bản: ${esc(a.arguments.report_id)}<br>Hết hạn: ${nbDate(a.expires_at)}</p><details><summary>Xem toàn bộ nội dung sẽ ${a.tool_name==='forward_report_email'?'gửi':'lưu'}</summary><pre>${esc(stored.payload.markdown||'')}</pre></details>${['pending','approved'].includes(a.status)?`<div class="brief-approve-buttons">${nbButton('Duyệt & thực hiện',`nbDecide('${esc(a.id)}','approve_execute')`,'primary','check')}${nbButton('Từ chối',`nbDecide('${esc(a.id)}','reject')`,'quiet')}</div>`:`<p>${esc(({executed:'Đã thực hiện',failed:'Thực hiện lỗi',expired:'Đã hết hạn',rejected:'Đã từ chối',executing:'Đang thực hiện'})[a.status]||a.status)}</p>${a.error?`<p class="nb-alert">${esc(a.error)}</p>`:''}`}</section>`).join('');
}
async function nbDecide(id,op){await nbTask(async()=>{const r=await postJSON('/api/approvals',{op,id});if(r.error)throw new Error(r.error);if(r.result&&!r.result.ok)throw new Error(r.result.output);NB.notice=op==='reject'?'Đã từ chối hành động.':'Đã thực hiện hành động đã duyệt.';await refresh();await nbLoad(NB.detail.workspace.id,NB.detail.report.payload.report_id);});}
VIEWS.today=briefToday;
VIEWS.review=briefReview;
VIEWS.reports=(d,id)=>id?notebookView(d,id):briefReports();
