// Customer research notebooks. Local state is separate from the general chat dock.
const NB = {items:null, detail:null, requested:null, tab:"report", source:null, excerpt:"",
  busy:false, error:"", notice:"", availability:null, selectedSlot:null, calendar:{days:7,duration:45,timezone:"Asia/Ho_Chi_Minh",start_date:""}};
const NB_SECTIONS = {company:"Giới thiệu doanh nghiệp",contacts:"Người liên hệ",email_need:"Nhu cầu từ email",
  meeting_context:"Bối cảnh cuộc họp",public_research:"Lĩnh vực & sản phẩm",recent_updates:"Tin tức gần đây",risks_opportunities:"Điểm cần lưu ý"};
const NB_TYPES = {email:"Email",web:"Website",news:"Tin tức",document:"Tài liệu",internal_knowledge:"Knowledge",memory:"Memory",calendar:"Lịch",tool:"Nguồn"};
function nbIcon(name){
  const paths={book:'<path d="M4 4h6a3 3 0 0 1 2 2 3 3 0 0 1 2-2h6v15h-6a3 3 0 0 0-2 2 3 3 0 0 0-2-2H4z"/><path d="M12 6v15"/>',
    plus:'<path d="M12 5v14M5 12h14"/>',arrow:'<path d="M5 12h14m-6-6 6 6-6 6"/>',
    search:'<circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/>',
    file:'<path d="M14 3H5v18h14V8zM14 3v5h5M8 12h8M8 16h6"/>',
    web:'<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a18 18 0 0 1 0 18 18 18 0 0 1 0-18z"/>',
    calendar:'<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M7 3v4m10-4v4M3 11h18m-14 4h3m4 0h3"/>',
    chat:'<path d="M21 12a9 9 0 0 1-9 9H3l2-5a9 9 0 1 1 16-4z"/><path d="M8 10h8m-8 4h5"/>',
    check:'<path d="m5 12 4 4L19 6"/>',download:'<path d="M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4"/>',
    send:'<path d="m3 3 18 9-18 9 4-9-4-9zm4 9h14"/>',close:'<path d="m6 6 12 12M6 18 18 6"/>'};
  return `<svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name]||paths.file}</svg>`;
}
function nbButton(label,handler,kind="",icon=""){
  return `<button class="nb-btn ${kind}" onclick="${handler}" ${NB.busy?"disabled":""}>${icon?nbIcon(icon):""}${label}</button>`;
}
function nbDate(value){
  if (!value) return "Chưa xác định";
  const dt=new Date(value);
  return Number.isNaN(dt.getTime())?esc(value):esc(dt.toLocaleString("vi-VN",{day:"2-digit",month:"2-digit",year:"numeric",hour:"2-digit",minute:"2-digit"}));
}
async function nbAPI(payload){
  if(NB.detail&&payload.workspace_id===NB.detail.workspace.id&&NB.detail.report&&payload.report_id===undefined){payload={report_id:NB.detail.report.payload.report_id,...payload};}
  const response=await fetch("/api/workspace",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
  const result=await response.json();
  if(result.error) throw new Error(result.error);
  return result;
}
function nbPaint(){
  const [view,id]=(location.hash||"").slice(1).split("/");
  if(!["research","reports"].includes(view)) return;
  const scrolls={};
  document.querySelectorAll(".nb-scroll").forEach(el=>scrolls[el.id]=el.scrollTop);
  document.getElementById("view").innerHTML=notebookView(D,id);
  document.querySelectorAll(".nb-scroll").forEach(el=>{el.scrollTop=scrolls[el.id]||0;});
}
async function nbLoad(id="",reportId=""){
  const request=Symbol("notebook"); NB.requested=request; NB.error="";
  try{
    const response=await fetch(id?`/api/workspaces?id=${encodeURIComponent(id)}${reportId?"&report="+encodeURIComponent(reportId):""}`:"/api/workspaces");
    const result=await response.json();
    if(request!==NB.requested) return;
    if(result.error) throw new Error(result.error);
    if(id) {NB.detail=result; NB.loadedId=id;if(!NB.availability&&result.report)NB.availability=(result.report.payload.metadata||{}).availability||null;} else {NB.items=result.items; NB.loadedId=""; NB.detail=null;}
  }catch(e){if(request===NB.requested) NB.error=e.message;}
  if(request===NB.requested){NB.loading=false;nbPaint();
    if(id&&NB.detail&&NB.detail.report&&(NB.detail.report.payload.metadata||{}).require_semantic_review&&NB.detail.report.payload.evaluation.release_allowed){
      postJSON('/api/briefing',{op:'prepare_review',workspace_id:id,report_id:NB.detail.report.payload.report_id}).then(r=>{if(r.error){NB.error=r.error;nbPaint();}else if(typeof refresh==='function')refresh();}).catch(()=>{});
    }
  }
}
function notebookView(d,id){
  id=id||"";
  if(NB.loadedId!==id&&!NB.loading){NB.loading=true;setTimeout(()=>nbLoad(id),0);}
  if(!id) return nbLibrary();
  if(!NB.detail||NB.detail.workspace.id!==id) return `<div class="nb-root"><div class="nb-loading">${nbIcon("book")}<h3>${NB.error?"Chưa mở được workspace":"Đang mở workspace…"}</h3><p>${esc(NB.error||"Đọc báo cáo, nguồn và lịch sử nghiên cứu.")}</p>${NB.error?nbButton("Thử lại",`nbLoad('${esc(id)}')`):""}</div></div>`;
  return nbWorkspace();
}
function nbLibrary(){
  const items=NB.items||[];
  return `<div class="nb-root nb-library"><div class="nb-library-head"><div><div class="nb-eyebrow">ĐỐI TÁC CỦA ANH</div><h1>Mỗi đối tác.<br><span>Một hồ sơ có dẫn chứng.</span></h1><p>PU tự chuẩn bị từ email mới. Chỉ thêm thủ công khi anh cần nghiên cứu đối tác khác.</p></div>${nbButton("Thêm đối tác","nbOpenDialog('create')","primary","plus")}</div>
    ${NB.error?`<div class="nb-alert">${esc(NB.error)}</div>`:""}
    <div class="nb-library-caption"><span>${items.length} đối tác</span>${nbButton("Làm mới","nbLoad()","quiet")}</div>
    <div class="nb-library-grid">${items.map((w,i)=>`<a class="nb-customer" href="#research/${esc(w.id)}"><div class="nb-customer-top"><span class="nb-monogram tone-${i%4}">${esc(w.company.slice(0,2).toUpperCase())}</span><span class="nb-small-label">${w.report_count?"Có báo cáo":"Chờ nghiên cứu"}</span></div><h3>${esc(w.company)}</h3><p>${esc(w.website||w.contact||"Workspace nghiên cứu khách hàng")}</p><div class="nb-customer-footer"><span>${w.source_count} nguồn · ${w.report_count} báo cáo</span>${nbIcon("arrow")}</div></a>`).join("")}
    <button class="nb-customer nb-create-card" onclick="nbOpenDialog('create')">${nbIcon("plus")}<h3>Thêm khách hàng</h3><p>Bắt đầu từ email, website hoặc tài liệu.</p></button></div>
    ${!items.length?`<div class="nb-onboarding"><span>01 · Thêm khách hàng và nguồn</span><span>02 · Nghiên cứu có dẫn chứng</span><span>03 · Kiểm tra lịch và phê duyệt</span></div>`:""}</div>`;
}
function nbWorkspace(){
  const x=NB.detail,w=x.workspace,stored=x.report,r=stored&&stored.payload;
  const selected=x.sources.filter(s=>s.enabled).length;
  return `<div class="nb-root"><div class="nb-workspace-top"><div class="nb-workspace-title"><a href="#research" class="nb-back" aria-label="Về danh sách workspace">←</a><span class="nb-monogram tone-0">${esc(w.company.slice(0,2).toUpperCase())}</span><div><div class="nb-eyebrow">HỒ SƠ ĐỐI TÁC</div><h1>${esc(w.company)}</h1><p>${esc(w.contact||w.website||"Nghiên cứu trước cuộc họp")}</p></div></div><div class="nb-top-actions">${NB.busy?'<span class="nb-working"><i></i> Đang xử lý…</span>':""}${nbButton("Làm mới",`nbLoad('${esc(w.id)}')`,"quiet")}${nbButton("Cập nhật tự động", "nbAutoResearch()","primary","search")}</div></div>
    ${NB.error?`<div class="nb-alert" role="alert">${esc(NB.error)}</div>`:""}${NB.notice?`<div class="nb-notice" role="status">${esc(NB.notice)}</div>`:""}
    <div id="nb-job-status">${typeof briefJobBanner==='function'?briefJobBanner(w.id):''}</div><div class="nb-grid"><section class="nb-pane nb-sources"><div class="nb-pane-head"><h2>${nbIcon("file")} Nguồn <span>${x.sources.length}</span></h2>${nbButton("+","nbOpenDialog('source')","icon")}</div><div class="nb-source-toolbar"><span>${selected} nguồn đang chọn</span>${nbButton("Thêm nguồn","nbOpenDialog('source')","quiet")}</div>
    <div class="nb-scroll" id="nb-source-scroll">${NB.source?nbSourceViewer():nbSourceList()}</div><div class="nb-pane-foot">Hỏi đáp chỉ sử dụng nguồn đã chọn.</div></section>
    <section class="nb-pane nb-center"><div class="nb-tabs" role="tablist"><button role="tab" aria-selected="${NB.tab==='report'}" class="${NB.tab==='report'?'on':''}" onclick="nbTab('report')">${nbIcon("book")} Báo cáo</button><button role="tab" aria-selected="${NB.tab==='chat'}" class="${NB.tab==='chat'?'on':''}" onclick="nbTab('chat')">${nbIcon("chat")} Hỏi đáp</button>${r?nbButton('Sửa',"nbOpenDialog('edit')",'quiet'):''}${x.versions.length?`<select class="nb-version" aria-label="Phiên bản báo cáo" onchange="nbVersion(this.value)">${x.versions.map((v,i)=>`<option value="${esc(v.report_id)}" ${r&&r.report_id===v.report_id?'selected':''}>${i===0?'Mới nhất':'Bản '+(x.versions.length-i)} · ${nbDate(v.created_at)}</option>`).join("")}</select>`:""}</div>
    <div class="nb-scroll nb-reading" id="nb-reading-scroll">${NB.tab==='chat'?nbChat():nbReport()}</div>${nbComposer()}</section>
    <section class="nb-pane nb-studio"><div class="nb-pane-head"><h2>${nbIcon("calendar")} Chuẩn bị cuộc họp</h2><span class="nb-small-label">STUDIO</span></div><div class="nb-scroll" id="nb-studio-scroll">${nbCalendar()}${nbActions()}${nbDeliveries()}</div></section></div></div>`;
}
function nbSourceList(){
  const sources=NB.detail.sources;
  if(!sources.length) return `<div class="nb-empty">${nbIcon("file")}<h3>Đưa nguồn vào nghiên cứu</h3><p>Thêm website, email đã đồng bộ, knowledge hoặc tài liệu của khách hàng.</p>${nbButton("Thêm nguồn đầu tiên","nbOpenDialog('source')","soft","plus")}</div>`;
  return sources.map(s=>`<div class="nb-source-row ${s.enabled?'selected':''}"><input type="checkbox" ${s.enabled?'checked':''} ${NB.busy?'disabled':''} aria-label="Chọn nguồn ${esc(s.title)}" onchange="nbSelect('${esc(s.id)}',this.checked)"><button class="nb-source-open" onclick="nbOpenSource('${esc(s.id)}')"><span class="nb-source-symbol">${nbIcon(s.source_type==='web'?'web':'file')}</span><span><b>${esc(s.title)}</b><small>${esc(NB_TYPES[s.source_type]||'Nguồn')} · ${s.published_at?nbDate(s.published_at):'Ngày xuất bản chưa xác định'}</small></span></button></div>`).join("");
}
function nbSourceViewer(){
  const s=NB.detail.sources.find(s=>s.id===NB.source);
  if(!s){NB.source=null;return nbSourceList();}
  let content=esc(s.content);
  if(NB.excerpt&&s.content.includes(NB.excerpt)) content=content.replace(esc(NB.excerpt),`<mark>${esc(NB.excerpt)}</mark>`);
  return `<div class="nb-source-viewer">${nbButton("← Tất cả nguồn","nbCloseSource()","quiet")}<span class="nb-small-label">${esc(NB_TYPES[s.source_type]||'Nguồn')}</span><h3>${esc(s.title)}</h3>${s.url?`<a class="nb-source-url" href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">Mở nguồn gốc ↗</a>`:""}<p class="nb-muted">Xuất bản: ${nbDate(s.published_at)}<br>Thu thập: ${nbDate(s.created_at)}</p><pre>${content}</pre></div>`;
}
function nbCite(id,label=""){
  const stored=NB.detail.report;
  const sourceId=stored&&stored.source_map[id]||id;
  if(!NB.detail.sources.some(s=>s.id===sourceId)) return `<span class="nb-citation missing" title="Nguồn chưa có nội dung truy xuất">${esc(label||id)}</span>`;
  return `<button class="nb-citation" onclick="nbCitation(${esc(JSON.stringify(sourceId))},${esc(JSON.stringify(id))})" title="Xem bằng chứng">${esc(label||id)}</button>`;
}
function nbReportText(text){
  return renderMarkdown(text||"").replace(/\[(S\d+|W\d+)\]/g,(_,id)=>nbCite(id));
}
function nbReport(){
  const stored=NB.detail.report;
  if(!stored) return `<div class="nb-report-empty">${nbIcon("book")}<div class="nb-eyebrow">TỪ NGUỒN ĐẾN HIỂU BIẾT</div><h2>Bản nghiên cứu tiếp theo<br>bắt đầu ở đây.</h2><p>PU tự đọc email, website, nguồn công khai và lịch để chuẩn bị báo cáo có dẫn chứng. Anh có thể rời trang trong lúc chờ.</p>${nbButton("Tự chuẩn bị báo cáo", "nbAutoResearch()","primary","search")}</div>`;
  const r=stored.payload,e=r.evaluation||{},m=e.metrics||{};
  return `<article class="nb-report"><div class="nb-report-meta"><span class="nb-gate ${e.release_allowed?'pass':'warning'}">${nbIcon(e.release_allowed?'check':'file')} ${e.release_allowed?'Đã kiểm tra cấu trúc & citation':'Cần bổ sung bằng chứng'}</span><span>${nbDate(r.generated_at)}</span></div><h1>${esc(r.title)}</h1><div class="nb-summary">${nbReportText(r.executive_summary)}</div><div class="nb-report-stats"><span><b>${(r.sources||[]).filter(s=>!s.derived).length}</b> nguồn tham khảo</span><span><b>${m.source_coverage!=null?Math.round(m.source_coverage*100)+'%':'—'}</b> kết luận có dẫn nguồn</span><span><b>${(r.claims||[]).length}</b> kết luận</span></div>
    ${Object.entries(NB_SECTIONS).filter(([key])=>(r.sections||{})[key]).map(([key,label])=>`<section class="nb-report-section"><h2>${label}</h2><div>${nbReportText(r.sections[key])}</div><details class="nb-claim-details"><summary>Đối chiếu kết luận và nguồn</summary>${(r.claims||[]).filter(c=>c.section===key).map(c=>`<div class="nb-claim"><span>${esc(c.text)}</span><span>${(c.source_ids||[]).map(id=>nbCite(id)).join(' ')}</span></div>`).join('')}</details></section>`).join('')}
    ${(r.missing_information||[]).length?`<section class="nb-callout"><h3>Thông tin còn thiếu</h3><ul>${r.missing_information.map(v=>`<li>${esc(v)}</li>`).join('')}</ul></section>`:''}
    ${(r.contradictions||[]).length?`<section class="nb-callout"><h3>Nguồn cần đối chiếu</h3>${r.contradictions.map(v=>`<p>${esc(v.description)} ${(v.source_ids||[]).map(id=>nbCite(id)).join(' ')}</p>`).join('')}</section>`:''}
    ${(e.findings||[]).length?`<section class="nb-callout"><h3>Đánh giá chất lượng</h3><ul>${e.findings.map(v=>`<li>${esc(v)}</li>`).join('')}</ul></section>`:''}
    ${(e.warnings||[]).length?`<details class="nb-quality-notes"><summary>Lưu ý về chất lượng nguồn</summary>${e.warnings.map(v=>`<p>${esc(v)}</p>`).join('')}</details>`:''}
    <section class="nb-report-section"><h2>Nguồn tham khảo</h2>${(r.sources||[]).filter(s=>!s.derived).map(s=>`<div class="nb-report-source">${nbCite(s.id)}<span>${esc(s.title)}<small>${esc(s.url||NB_TYPES[s.source_type]||s.source_type)}</small></span></div>`).join('')}</section></article>`;
}
function nbChat(){
  const messages=NB.detail.messages||[];
  return `<div class="nb-conversation">${!messages.length?`<div class="nb-chat-welcome">${nbIcon("chat")}<h2>Hỏi từ những gì đã biết.</h2><p>Câu trả lời dựa trên các nguồn anh chọn, có citation để kiểm tra.</p><div class="nb-suggestions">${["Khách hàng đang có nhu cầu gì?","Những điểm cần hỏi trong cuộc họp?","Có thông tin nào mâu thuẫn giữa các nguồn?"].map(q=>`<button onclick="nbSuggestion(this.textContent)">${esc(q)} ${nbIcon('arrow')}</button>`).join('')}</div></div>`:''}${messages.map(m=>`<div class="nb-question">${esc(m.question)}</div><div class="nb-answer"><div class="nb-answer-label">${nbIcon('book')} PU Research</div><div>${renderMarkdown(m.answer)}</div><div class="nb-answer-citations">${(m.source_ids||[]).map((id,i)=>nbCite(id,String(i+1))).join(' ')}<span>${(m.source_ids||[]).length?'Nguồn hỗ trợ câu trả lời':'Chưa đủ bằng chứng'}</span></div></div>`).join('')}</div>`;
}
function nbComposer(){
  return `<form class="nb-composer" onsubmit="nbAsk(event)"><div class="nb-composer-field"><input id="nb-question" aria-label="Câu hỏi về khách hàng" placeholder="Hỏi về khách hàng từ nguồn đã chọn…" maxlength="4000" ${NB.busy?'disabled':''}><button type="submit" aria-label="Gửi câu hỏi" ${NB.busy?'disabled':''}>${nbIcon('arrow')}</button></div><small>${NB.detail.sources.filter(s=>s.enabled).length} nguồn được chọn · AI có thể sai, hãy kiểm tra dẫn chứng.</small></form>`;
}
function nbCalendar(){
  const a=NB.availability,c=NB.calendar;
  return `<section class="nb-studio-section"><div class="nb-section-title"><h3>Lịch trống</h3>${nbIcon('calendar')}</div><p class="nb-muted">Giờ đề xuất đủ thời lượng, 09:00–18:00 ngày làm việc. Chỉ phản ánh lịch của bạn; chưa biết lịch đối tác.</p><details class="nb-calendar-advanced"><summary>Tùy chọn lịch</summary><div class="nb-calendar-controls"><label>Từ ngày<input type="date" value="${esc(c.start_date)}" onchange="nbCalendarOption('start_date',this.value)"></label><label>Khoảng ngày<select onchange="nbCalendarOption('days',Number(this.value))">${[7,14,30].map(n=>`<option ${c.days===n?'selected':''} value="${n}">${n} ngày</option>`).join('')}</select></label><label>Thời lượng<select onchange="nbCalendarOption('duration',Number(this.value))">${[30,45,60,90].map(n=>`<option ${c.duration===n?'selected':''} value="${n}">${n} phút</option>`).join('')}</select></label><label>Múi giờ<select onchange="nbCalendarOption('timezone',this.value)">${['Asia/Ho_Chi_Minh','Asia/Tokyo','UTC'].map(z=>`<option ${c.timezone===z?'selected':''} value="${z}">${z==='Asia/Ho_Chi_Minh'?'Việt Nam · UTC+7':z}</option>`).join('')}</select></label></div>${nbButton(a?'Kiểm tra lại':'Kiểm tra lịch trống','nbAvailability()','soft','calendar')}</details>
    ${!a?'<div class="nb-calendar-placeholder">Chưa kiểm tra lịch. Chọn khoảng ngày để lấy availability thực tế.</div>':`<div class="nb-calendar-info"><b>${a.status==='unknown'?'Chưa xác định lịch trống':'Đã kiểm tra'}</b><small>${nbDate(a.checked_at)} · ${esc(a.timezone)}</small><small>${esc(a.checked_sources.join(' + '))}</small></div>${(a.warnings||[]).map(v=>`<p class="nb-calendar-warning">${esc(v)}</p>`).join('')}<div class="nb-slots">${a.slots.slice(0,12).map((slot,i)=>`<button class="nb-slot ${NB.selectedSlot===i?'on':''}" onclick="nbChooseSlot(${i})"><span>${esc(slot.label)}</span><b>${esc(slot.window)}</b>${nbIcon(NB.selectedSlot===i?'check':'plus')}</button>`).join('')}</div>${a.status==='available'&&!a.slots.length?'<p class="nb-muted">Không có khoảng trống đủ thời lượng trong giờ làm việc đã chọn.</p>':''}`}
    ${nbButton('Kết nối Google Calendar',"nbConnect('connect_calendar')",'quiet')}
    ${a&&a.events.length?`<details class="nb-upcoming"><summary>${a.events.length} cuộc hẹn liên quan đến đối tác</summary>${a.events.slice(0,30).map(ev=>`<div><b>${esc(ev.title)}</b><small>${nbDate(ev.start)} → ${nbDate(ev.end)}</small><small>${esc(ev.source)}${ev.attendees?' · '+esc(ev.attendees):''}</small></div>`).join('')}</details>`:''}</section>`;
}
function nbActions(){
  const stored=NB.detail.report;
  if(!stored) return `<section class="nb-studio-section">${typeof nbApprovalCards==='function'?nbApprovalCards():''}<h3>Báo cáo & phê duyệt</h3><p class="nb-muted">Chạy nghiên cứu để tạo bản báo cáo đầu tiên.</p></section>`;
  const e=stored.payload.evaluation||{};
  return `<section class="nb-studio-section">${typeof nbApprovalCards==='function'?nbApprovalCards():''}<h3>Báo cáo & phê duyệt</h3><details class="nb-extra-actions"><summary>Xuất báo cáo & tùy chọn khác</summary><p class="nb-muted">Xem đúng phiên bản trước khi gửi hoặc lưu.</p><div class="nb-export-links">${(NB.detail.export_formats||['md','json']).map(ext=>`<a href="/api/workspace/export?id=${encodeURIComponent(NB.detail.workspace.id)}&report=${encodeURIComponent(stored.payload.report_id)}&format=${ext}">${nbIcon('download')} ${ext.toUpperCase()}${ext==='md'?'':' '}</a>`).join('')}</div><div class="nb-action-stack">${nbButton('Đề xuất gửi email',"nbOpenDialog('send')",'soft','send')}${nbButton('Đề xuất lưu knowledge',"nbPropose('save_report_to_knowledge')",'soft','book')}</div>${!e.release_allowed?'<p class="nb-calendar-warning">Quality gate chưa đạt. Bổ sung evidence và nghiên cứu lại để phân phối.</p>':''}${nbButton('Kết nối Gmail gửi email',"nbConnect('connect_send')",'quiet')}</details><a href="#review" class="nb-queue-link">Tất cả hành động chờ duyệt →</a></section>`;
}
function nbDeliveries(){
  const xs=NB.detail.deliveries||[];
  if(!xs.length)return '';
  return `<section class="nb-studio-section"><h3>Lịch sử phân phối</h3>${xs.map(d=>`<div class="nb-delivery"><b>${d.result.status==='saved'?'Đã lưu knowledge':d.result.status==='sent'?'Đã gửi email':'Cần kiểm tra kết quả'}</b><small>${esc(d.destination)} · ${nbDate(d.created_at)}</small>${d.result.message_id?`<small>Gmail ID: ${esc(d.result.message_id)}</small>`:''}</div>`).join('')}</section>`;
}
function nbTab(tab){NB.tab=tab;nbPaint();}
function nbOpenSource(id){NB.source=id;NB.excerpt='';nbPaint();}
function nbCloseSource(){NB.source=null;NB.excerpt='';nbPaint();}
function nbCitation(sourceId,citationId){
  NB.source=sourceId;
  const r=NB.detail.report;
  const source=r&&(r.payload.sources||[]).find(s=>s.id===citationId);
  NB.excerpt=source?source.excerpt:''; nbPaint();
  const mark=document.querySelector('.nb-source-viewer mark'); if(mark)mark.scrollIntoView({block:'center',behavior:'smooth'});
}
function nbCalendarOption(key,value){NB.calendar[key]=value;NB.availability=null;NB.selectedSlot=null;}
function nbChooseSlot(index){NB.selectedSlot=index;NB.notice='Đã chọn khung giờ. Phần gửi email sẽ đưa khung giờ này vào nội dung để anh xem trước.';nbPaint();}
async function nbVersion(id){NB.source=null;NB.availability=null;NB.selectedSlot=null;await nbLoad(NB.detail.workspace.id,id);}
async function nbTask(fn){
  if(NB.busy)return;
  NB.busy=true;NB.error='';NB.notice='';nbPaint();
  try{await fn();}catch(e){NB.error=e.message;}finally{NB.busy=false;nbPaint();}
}
async function nbSelect(id,enabled){
  await nbTask(async()=>{NB.detail=await nbAPI({op:'select_source',workspace_id:NB.detail.workspace.id,source_id:id,enabled});});
}
async function nbAvailability(){await nbTask(async()=>{NB.availability=await nbAPI({op:'availability',workspace_id:NB.detail.workspace.id,...NB.calendar});NB.selectedSlot=null;});}
async function nbAsk(event){
  event.preventDefault();const el=document.getElementById('nb-question');const question=el.value.trim();if(!question)return;
  await nbTask(async()=>{await nbAPI({op:'ask',workspace_id:NB.detail.workspace.id,question});NB.tab='chat';await nbLoad(NB.detail.workspace.id,NB.detail.report&&NB.detail.report.payload.report_id);});
  const log=document.getElementById('nb-reading-scroll');if(log)log.scrollTop=log.scrollHeight;
}
function nbSuggestion(text){const el=document.getElementById('nb-question');if(el){el.value=text;el.focus();}}
async function nbConnect(op){await nbTask(async()=>{const r=await nbAPI({op});NB.notice=r.message;});}
async function nbPropose(tool,fields={}){
  await nbTask(async()=>{const stored=NB.detail.report;if(!stored)throw new Error('Chưa có báo cáo.');const r=await nbAPI({op:'propose',workspace_id:NB.detail.workspace.id,report_id:stored.payload.report_id,content_hash:stored.content_hash,tool,...fields});NB.notice='Đã chuẩn bị hành động. Anh có thể duyệt ngay bên cạnh báo cáo.';await refresh();nbPaint();});
}
function nbCloseDialog(){const el=document.getElementById('nb-dialog');if(el)el.remove();}
function nbOpenDialog(kind){
  if(NB.busy)return;
  nbCloseDialog();const dialog=document.createElement('dialog');dialog.id='nb-dialog';dialog.className='nb-dialog';
  const w=NB.detail&&NB.detail.workspace;
  const headers={create:'Workspace khách hàng mới',source:'Thêm nguồn nghiên cứu',research:'Nghiên cứu khách hàng',send:'Đề xuất gửi báo cáo',edit:'Chỉnh sửa phiên bản báo cáo'};
  let body='';
  if(kind==='create')body=`<label>Tên doanh nghiệp<input name="company" required maxlength="200" placeholder="VD: FPT Software"></label><label>Website<input name="website" type="url" placeholder="https://…"></label><label>Người liên hệ<input name="contact" maxlength="300" placeholder="Tên hoặc email liên hệ"></label>`;
  if(kind==='research')body=`<p>Nghiên cứu <b>${esc(w.company)}</b> từ nguồn đã chọn và tìm thêm thông tin công khai. Các agent email, company, web, calendar và memory sẽ tổng hợp một báo cáo mới.</p><label>Điều cần tập trung<textarea name="intent" maxlength="4000" placeholder="VD: nhu cầu AI, sản phẩm và những điểm cần hỏi trước cuộc gặp"></textarea></label><p class="nb-muted">Bản trước được giữ lại. Tác vụ có thể mất vài phút.</p>`;
  if(kind==='source')body=`<div class="nb-source-modes"><button type="button" class="on" onclick="nbSourceMode('text',this)">Dán / File</button><button type="button" onclick="nbSourceMode('web',this)">Website</button><button type="button" onclick="nbSourceMode('import',this)">Email / Knowledge</button></div><input name="mode" type="hidden" value="text"><div id="nb-source-fields">${nbSourceFields('text')}</div>`;
  if(kind==='edit'){
    const report=NB.detail.report.payload;
    body=`<p>Thay đổi tạo phiên bản mới và hủy các proposal chưa thực thi của bản cũ. Giữ citation như [S1] hoặc [W1] cho nội dung có dữ kiện.</p><label>Tiêu đề<input name="title" required maxlength="300" value="${esc(report.title)}"></label><label>Tóm tắt<textarea name="executive_summary" maxlength="15000">${esc(report.executive_summary)}</textarea></label>${Object.entries(report.sections||{}).map(([key,value])=>`<label>${esc(NB_SECTIONS[key]||key)}<textarea name="section-${esc(key)}" maxlength="25000">${esc(value)}</textarea></label>`).join('')}`;
  }
  if(kind==='send'){
    const slot=NB.availability&&NB.selectedSlot!=null&&NB.availability.slots[NB.selectedSlot];
    const intro=slot?`Khung giờ đề xuất: ${slot.label}, ${slot.window} (${NB.availability.timezone}), thời lượng ${slot.duration_minutes} phút. Đây là giờ trống trên lịch của tôi; vui lòng xác nhận lịch của đối tác.`:'';
    body=`<p>Tạo yêu cầu phê duyệt cho phiên bản đang xem. Email chứa nội dung báo cáo đầy đủ và citations.</p><label>Người nhận<input name="recipient" type="email" required placeholder="contact@company.com"></label><label>Tiêu đề<input name="subject" required maxlength="200" value="${esc('Báo cáo nghiên cứu — '+w.company)}"></label><label>Lời mở đầu / lịch đề xuất<textarea name="intro" maxlength="3000">${esc(intro)}</textarea></label><div class="nb-email-preview"><b>Nội dung báo cáo đi kèm</b><pre>${esc(NB.detail.report.payload.markdown||'')}</pre></div><p class="nb-muted">Bước này chỉ tạo proposal. Chỉ gửi sau khi anh xem payload và phê duyệt.</p>`;
  }
  dialog.innerHTML=`<form onsubmit="nbSubmitDialog(event,'${kind}')"><div class="nb-dialog-head"><h2>${headers[kind]}</h2><button type="button" aria-label="Đóng" onclick="nbCloseDialog()">${nbIcon('close')}</button></div><div class="nb-dialog-body">${body}<p id="nb-dialog-error" class="nb-dialog-error" role="alert"></p></div><div class="nb-dialog-foot"><button type="button" class="nb-btn" onclick="nbCloseDialog()">Hủy</button><button class="nb-btn primary" type="submit">${kind==='research'?'Bắt đầu nghiên cứu':kind==='send'?'Tạo yêu cầu phê duyệt':kind==='create'?'Tạo workspace':kind==='edit'?'Lưu phiên bản mới':'Thêm nguồn'}</button></div></form>`;
  document.body.appendChild(dialog);dialog.showModal();
  dialog.addEventListener('click',e=>{if(e.target===dialog)nbCloseDialog();});
}
function nbSourceFields(mode){
  if(mode==='web')return '<label>URL website / bài viết<input name="url" type="url" required placeholder="https://…"></label><p class="nb-muted">Đọc nội dung trang công khai; trang yêu cầu đăng nhập có thể cần dán nội dung.</p>';
  if(mode==='import')return '<label>Nguồn đã đồng bộ<select name="import_id" id="nb-import-list" required><option value="">Đang đọc danh sách…</option></select></label><p class="nb-muted">Email lấy body đã đồng bộ; knowledge lấy nội dung tài liệu. Không tải inbox mới ở bước này.</p>';
  return '<label>Tiêu đề<input name="title" required maxlength="300" placeholder="VD: Email trao đổi nhu cầu AI"></label><label>Tài liệu (PDF, DOCX, TXT, Markdown · tối đa 1 MB)<input type="file" accept=".pdf,.docx,.txt,.md,.markdown" onchange="nbReadFile(this)"></label><input name="filename" type="hidden"><input name="filedata" type="hidden"><label>Nội dung<textarea name="content" required maxlength="100000" placeholder="Dán nội dung email, tài liệu hoặc ghi chú…"></textarea></label><label>URL nguồn (nếu có)<input name="url" type="url" placeholder="https://…"></label><label>Ngày xuất bản (nếu có)<input name="published_at" type="date"></label>';
}
async function nbSourceMode(mode,button){
  const dialog=document.getElementById('nb-dialog');dialog.querySelector('[name="mode"]').value=mode;
  dialog.querySelectorAll('.nb-source-modes button').forEach(el=>el.classList.toggle('on',el===button));
  dialog.querySelector('#nb-source-fields').innerHTML=nbSourceFields(mode);
  if(mode==='import'){
    try{const r=await nbAPI({op:'imports',workspace_id:NB.detail.workspace.id});const select=document.getElementById('nb-import-list');if(!select)return;
      select.innerHTML='<option value="">Chọn nguồn…</option>'+r.emails.map(e=>`<option value="email:${esc(e.gmail_id)}">Email · ${esc(e.subject)} — ${esc(e.sender_email)}</option>`).join('')+r.knowledge.map(e=>`<option value="knowledge:${e.id}">Knowledge · ${esc(e.title)}</option>`).join('');
    }catch(e){dialog.querySelector('#nb-dialog-error').textContent=e.message;}
  }
}
async function nbReadFile(input){
  const file=input.files[0];if(!file)return;const form=input.closest('form');
  if(file.size>1000000||!(/\.(pdf|docx|txt|md|markdown)$/i.test(file.name))){document.getElementById('nb-dialog-error').textContent='Chọn PDF/DOCX/TXT/Markdown tối đa 1 MB.';input.value='';return;}
  if(/\.(pdf|docx)$/i.test(file.name)){
    const reader=new FileReader();reader.onload=()=>{form.elements.filename.value=file.name;form.elements.filedata.value=String(reader.result).split(',')[1];form.elements.content.required=false;form.elements.content.value='';form.elements.content.placeholder='Nội dung sẽ được trích xuất từ '+file.name;};reader.readAsDataURL(file);
  }else{form.elements.filedata.value='';form.elements.filename.value='';form.elements.content.required=true;form.elements.content.value=await file.text();}
  if(!form.elements.title.value)form.elements.title.value=file.name;
}
async function nbSubmitDialog(event,kind){
  event.preventDefault();const form=event.target,fields=Object.fromEntries(new FormData(form));
  const submit=form.querySelector('[type="submit"]');submit.disabled=true;const error=form.querySelector('#nb-dialog-error');error.textContent='';
  try{
    if(kind==='create'){const r=await nbAPI({op:'create',...fields});NB.items=null;NB.source=null;NB.tab='report';NB.availability=null;location.hash='research/'+r.workspace.id;nbCloseDialog();await postJSON('/api/briefing',{op:'enqueue',workspace_id:r.workspace.id});if(typeof briefRefresh==='function')briefRefresh();return;}
    const workspace_id=NB.detail.workspace.id;
    if(kind==='source'){
      let payload={op:'add_source',workspace_id,...fields};
      if(fields.filedata)payload={op:'upload_source',workspace_id,filename:fields.filename,data:fields.filedata};
      if(fields.mode==='web')payload={op:'fetch_source',workspace_id,url:fields.url};
      if(fields.mode==='import'){const [type,...id]=fields.import_id.split(':');payload={op:'import_source',workspace_id,kind:type,source_id:id.join(':')};}
      NB.detail=await nbAPI(payload);nbCloseDialog();nbPaint();return;
    }
    if(kind==='research'){nbCloseDialog();await nbTask(async()=>{NB.detail=await nbAPI({op:'research',workspace_id,intent:fields.intent||''});NB.tab='report';NB.notice='Đã tạo phiên bản nghiên cứu mới.';});return;}
    if(kind==='edit'){
      const stored=NB.detail.report;
      const sections=Object.fromEntries(Object.entries(fields).filter(([k])=>k.startsWith('section-')).map(([k,v])=>[k.slice(8),v]));
      NB.detail=await nbAPI({op:'revise',workspace_id,report_id:stored.payload.report_id,content_hash:stored.content_hash,changes:{title:fields.title,executive_summary:fields.executive_summary,sections}});
      nbCloseDialog();NB.notice='Đã lưu phiên bản mới và đánh giá lại chất lượng. Các proposal chưa thực thi của bản cũ đã hủy.';nbPaint();return;
    }
    if(kind==='send'){nbCloseDialog();await nbPropose('forward_report_email',fields);return;}
  }catch(e){error.textContent=e.message;submit.disabled=false;}
}
