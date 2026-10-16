const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function harness(withBriefing=false){
  const view = {innerHTML:''};
  const pending = [];
  const context = vm.createContext({
    document:{getElementById:id=>id==='view'?view:null,querySelectorAll:()=>[],querySelector:()=>null},
    URL,
    location:{hash:'#research/ws-a'},
    setTimeout(){},
    VIEWS:{},
    fetch:(url,options)=>new Promise((resolve,reject)=>pending.push({url,options,resolve,reject})),
  });
  for(const file of ['util.js','notebook.js']){
    vm.runInContext(fs.readFileSync(path.join(__dirname,'../../pu/ops/static/js',file),'utf8'),context);
  }
  if(withBriefing)for(const file of ['briefing.js','render.js','inbox-chat.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../../pu/ops/static/js',file),'utf8'),context);
  context.fixture={workspace:{id:'ws-a',company:'Acme',contact:'Alice'},
    sources:[{id:'src-a',title:'Email <img src=x onerror=alert(1)>',source_type:'email',enabled:true,content:'Need AI <script>alert(1)</script>',url:''}],
    versions:[],messages:[],deliveries:[],export_formats:['md','json'],
    report:{content_hash:'hash',source_map:{S1:'src-a'},payload:{report_id:'report-a',title:'Acme',executive_summary:'Need AI [S1]',sources:[{id:'S1',title:'Email',excerpt:'Need AI',source_type:'email'}],sections:{company:'Acme [S1]'},claims:[],evaluation:{release_allowed:true,metrics:{}}}}};
  vm.runInContext('NB.detail=fixture; NB.loadedId="ws-a"',context);
  return {context,view,pending};
}

test('workspace renders three panes with functional citations and escaped sources',()=>{
  const h=harness();
  const html=h.context.notebookView({},'ws-a');
  for(const text of ['Nguồn','Báo cáo','Hỏi đáp','Chuẩn bị cuộc họp','Kiểm tra lịch trống'])assert.ok(html.includes(text));
  assert.match(html,/onclick="nbCitation\(&quot;src-a&quot;,&quot;S1&quot;\)"/);
  assert.ok(html.includes('&lt;img'));
  assert.ok(!html.includes('<img src=x'));
});

test('citation opens evidence viewer and highlights the cited excerpt',()=>{
  const h=harness();
  h.context.nbCitation('src-a','S1');
  assert.match(h.view.innerHTML,/<mark>Need AI<\/mark>/);
  assert.ok(!h.view.innerHTML.includes('<script>alert'));
  assert.ok(h.view.innerHTML.includes('&lt;script&gt;'));
});

test('missing evidence cannot masquerade as a clickable citation',()=>{
  const h=harness();
  assert.ok(!h.context.nbCite('S999').includes('onclick'));
});

test('late workspace response cannot replace the most recently opened customer',async()=>{
  const h=harness();
  const old=h.context.nbLoad('ws-a');
  const current=h.context.nbLoad('ws-b');
  const b={...h.context.fixture,workspace:{id:'ws-b',company:'B'}};
  h.pending[1].resolve({json:async()=>b});await current;
  h.pending[0].resolve({json:async()=>h.context.fixture});await old;
  assert.equal(vm.runInContext('NB.detail.workspace.id',h.context),'ws-b');
});

test('unavailable calendar has no fake free slots',()=>{
  const h=harness();
  vm.runInContext('NB.availability={status:"unknown",checked_at:"2026-10-14",timezone:"Asia/Ho_Chi_Minh",checked_sources:[],warnings:["Google unavailable"],slots:[],events:[]}',h.context);
  const html=h.context.nbCalendar();
  assert.ok(html.includes('Chưa xác định lịch trống'));
  assert.ok(!html.includes('<button class="nb-slot'));
});

test('only available export formats are offered',()=>{
  const h=harness();
  const html=h.context.nbActions();
  assert.ok(html.includes('format=md'));
  assert.ok(!html.includes('format=pdf'));
});

test('source changes preserve the report version being reviewed',async()=>{
  const h=harness();
  let body;
  h.context.fetch=async(url,options)=>{body=JSON.parse(options.body);return {json:async()=>({})};};
  await h.context.nbAPI({op:'select_source',workspace_id:'ws-a',source_id:'src-a',enabled:false});
  assert.equal(body.report_id,'report-a');
});


test('today makes reading and approval primary, with one-time connection state',()=>{
  const h=harness(true);
  vm.runInContext('BRIEF.data={jobs:[],enabled:true,worker_active:true,gmail_connected:false};location.hash="#today"',h.context);
  const html=h.context.briefToday();
  for(const text of ['Anh đọc, PU chuẩn bị','Kết nối Gmail để bắt đầu','15 phút','Hành động chờ duyệt'])assert.ok(html.includes(text));
  assert.ok(!html.includes('Exact payload'));
});

test('reports library is distinct from partner creation',()=>{
  const h=harness(true);
  vm.runInContext('BRIEF.data={jobs:[]};BRIEF.workspaces=[{id:"ws-a",company:"Acme",report_count:2,source_count:4}]',h.context);
  const html=h.context.VIEWS.reports({},null);
  assert.ok(html.includes('Báo cáo đối tác'));
  assert.ok(html.includes('#reports/ws-a'));
  assert.ok(!html.includes('Thêm khách hàng'));
});

test('approval shows actual recipient, subject and exact saved report without raw JSON editor',()=>{
  const h=harness(true);
  vm.runInContext('D={approvals:{items:[{id:"action-a",tool_name:"forward_report_email",status:"pending",arguments:{workspace_id:"ws-a",report_id:"report-a",recipient:"review@example.com",subject:"<script>x</script>",intro:"Hello"}}]}};NB.detail.report.payload.markdown="# Exact report"',h.context);
  const html=h.context.nbApprovalCards();
  for(const text of ['review@example.com','&lt;script&gt;','Exact report','Duyệt & thực hiện'])assert.ok(html.includes(text));
  assert.ok(!html.includes('<script>'));
  assert.ok(!html.includes('<textarea'));
  assert.ok(html.includes("nbDecide('action-a','approve_execute')"));
});

test('automatic research enqueues durable work and returns without a synchronous workflow request',async()=>{
  const h=harness(true);
  const task=h.context.nbAutoResearch();
  assert.equal(h.pending.length,1);
  assert.equal(h.pending[0].url,'/api/briefing');
  assert.deepEqual(JSON.parse(h.pending[0].options.body),{op:'enqueue',workspace_id:'ws-a'});
  h.pending[0].resolve({json:async()=>({jobs:[]})});
  await task;
  assert.ok(vm.runInContext('NB.notice',h.context).includes('rời trang'));
});

test('a newer completed report offers a link instead of replacing the reviewed version',()=>{
  const h=harness(true);
  vm.runInContext('BRIEF.data={jobs:[{workspace_id:"ws-a",status:"completed",report_id:"report-new"}]}',h.context);
  const html=h.context.briefJobBanner('ws-a');
  assert.ok(html.includes('Đọc bản mới'));
  assert.equal(vm.runInContext('NB.detail.report.payload.report_id',h.context),'report-a');
});


test('chat is a full reading surface with the inbox question and report composer',()=>{
  const h=harness(true);
  const html=h.context.VIEWS.chat();
  for(const text of ['main-chatlog','id="msg"','Hôm nay có email gì mới?','báo cáo ngay tại đây'])assert.ok(html.includes(text));
  assert.ok(!html.includes('href="#research'));
});

test('inbox progress events are visible while a long report is generated',()=>{
  const h=harness(true),pending={};
  h.context.applyStreamEvent(pending,{kind:'inbox_progress',stage:'website',company:'Acme'});
  assert.equal(pending.inboxProgress,'Đang đọc website và sản phẩm · Acme');
  h.context.applyStreamEvent(pending,{kind:'text',delta:'Full report'});
  h.context.pendingFixture=pending;
  const html=vm.runInContext('streamingCard(pendingFixture)',h.context);
  assert.ok(html.includes('Full report')&&html.includes('Đang đọc website'));
});

test('stored report references survive chat history reload',()=>{
  const h=harness(true);
  const row=h.context.histItem({role:'assistant',content:'Full report',meta:{reports:[{report_id:'report-a',workspace_id:'ws-a'}]}});
  assert.equal(row.reports[0].report_id,'report-a');
});

test('chat keeps complete report text and approval controls in the same conversation',()=>{
  const h=harness(true);
  vm.runInContext('D={approvals:{items:[{id:"a",tool_name:"save_report_to_knowledge",status:"pending",arguments:{workspace_id:"ws-a",report_id:"report-a"}}]}}',h.context);
  const html=vm.runInContext("chatTurnCard({reply:'Full company report and weekly slots',reports:[{workspace_id:'ws-a',report_id:'report-a',release_allowed:true,evidence_policy_version:'company-boundary-v1',quality_policy_version:'report-quality-v2',rubric_version:'report-quality-rubric-v3',title:'Acme'}]})",h.context);
  assert.ok(html.includes('Full company report and weekly slots'));
  assert.ok(html.includes('Import vào sổ tay')&&html.includes('chatReviewAction'));
  assert.ok(html.includes('Đọc báo cáo')&&html.includes('chatOpenReport'));
  assert.ok(!html.includes("location.hash='approvals"));
});


test('nested research tool results do not erase already streamed report content',()=>{
  const h=harness(true),pending={};
  h.context.applyStreamEvent(pending,{kind:'inbox_progress',stage:'inbox'});
  h.context.applyStreamEvent(pending,{kind:'text',delta:'Report for first partner'});
  h.context.applyStreamEvent(pending,{kind:'tool',tool:'search_web',output:'Second partner sources'});
  assert.equal(pending.stream,'Report for first partner');
});



test('classification details stay collapsed and escape email content',()=>{
  const h=harness(true);
  const html=h.context.chatValidationDetails({inbox_validation:[{subject:'<script>alert(1)</script>',status:'excluded',reason:'Bulk newsletter',quote:'<img src=x onerror=alert(1)>'}]});
  assert.ok(html.includes('<details class="chat-validation">'));
  assert.ok(!html.includes('<details open'));
  assert.ok(html.includes('&lt;script&gt;')&&!html.includes('<script>'));
  assert.ok(!html.includes('<img src=x'));
});

test('reading an attached draft does not require passing the distribution gate',()=>{
  const h=harness(true);
  vm.runInContext('D={approvals:{items:[]}}',h.context);
  const html=h.context.chatReportActions({reports:[{workspace_id:'ws-a',report_id:'r',title:'Draft',release_allowed:false}]});
  assert.ok(html.includes('Đọc báo cáo'));
  assert.ok(!html.includes('Import vào sổ tay'));
  assert.ok(!html.includes('checkbox')&&!html.includes('Chọn tất cả'));
});



test('report reader uses wide reading layout and hides internal agent syntheses',()=>{
  const h=harness(true);
  const report={title:'Alpha',executive_summary:'Summary',sections:{company:'Company facts'},sources:[{id:'W1',source_type:'web',title:'Official site',url:'https://alpha.example',derived:false},{id:'S99',source_type:'agent_output',title:'web agent synthesis',derived:true}],claims:[{text:'Claim',source_ids:['W1']}],metadata:{evidence_policy_version:'company-boundary-v1',partner_identity:{status:'conflicting',conflicts:['Sender Alpha; signature Beta']}}};
  const html=h.context.chatReadableReport(report);
  assert.ok(html.includes('chat-reader-article')&&html.includes('Company facts'));
  assert.ok(html.includes('Danh tính công ty chưa thống nhất'));
  assert.ok(!html.includes('web agent synthesis'));
  assert.ok(html.includes('<details class="chat-validation">'));
  const css=fs.readFileSync(path.join(__dirname,'../../pu/ops/static/notebook.css'),'utf8');
  assert.match(css,/chat-report-dialog[^}]+1320px/);
});
