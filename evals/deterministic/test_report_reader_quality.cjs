const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const node = '/Users/dvphuc/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node';

function harness(){
  const context = vm.createContext({
    URL,
    Intl,
    Date,
    console,
    setTimeout,
    document:{getElementById:()=>null,querySelectorAll:()=>[],querySelector:()=>null},
    location:{hash:'#chat'},
    VIEWS:{},
    D:{approvals:{items:[]}},
    CHAT:[],
    fetch:async()=>({json:async()=>({})}),
  });
  for(const file of ['util.js','notebook.js','briefing.js','render.js','inbox-chat.js']){
    vm.runInContext(fs.readFileSync(path.join(__dirname,'../../pu/ops/static/js',file),'utf8'),context,{filename:file});
  }
  return context;
}

function sources(count=10){
  return Array.from({length:count},(_,index)=>({
    id:`W${index+1}`, source_type:'web', derived:false,
    title:`Nguồn công khai ${index+1}`, url:`https://partner.example/source-${index+1}`,
    published_at:'2026-10-01', excerpt:'Dữ kiện đã đọc',
  }));
}

test('reader maps citations to numbered links and folds extra public sources',()=>{
  const context=harness();
  const report={
    title:'Báo cáo Acme', executive_summary:'Acme hoạt động trong AI [W1][W2] và có quy mô [W10].',
    sections:{company:'Thông tin công ty [W1]',company_scale:'Có mạng lưới vận hành [W9].',meeting_context:'Cuộc họp đã được đề cập [W2].'},
    missing_information:[{text:'Chưa tìm thấy doanh thu',source_ids:['W10']}], sources:sources(10), claims:[{text:'Claim [W1]',source_ids:['W1','W2']}],
    metadata:{quality_policy_version:'report-quality-v2',evidence_policy_version:'company-boundary-v1'},
    evaluation:{release_allowed:true,metrics:{completeness:.8}},
  };
  const html=context.chatReadableReport(report);
  assert.ok(html.includes('href="https://partner.example/source-1"'));
  assert.ok(html.includes('[10]') || html.includes('source-10'));
  assert.ok(html.includes('Xem thêm 2 nguồn đã được trích dẫn'));
  assert.ok(!html.includes('[W1]')&&!html.includes('[W10]'));
  assert.match(html,/<a[^>]+>\[1\]<\/a> <a[^>]+>\[2\]<\/a>/);
  assert.ok(html.includes('Cuộc họp đã được đề cập'));
  assert.ok(!html.includes('](https://'));
});

test('reader honors server citation presentation and keeps canonical duplicate URLs first-seen',()=>{
  const context=harness();
  const report={title:'Acme',executive_summary:'Fact [S1][S2]',sections:{},sources:[
    {id:'S1',source_type:'web',derived:false,title:'Tracked page',url:'https://www.acme.example/about?utm_source=news#team'},
    {id:'S2',source_type:'web',derived:false,title:'Canonical page',url:'https://acme.example/about'},
    {id:'E1',source_type:'email',derived:false,title:'Email'},
  ],claims:[{text:'Claim [S1]',source_ids:['S1']}],contradictions:[{description:'Nguồn khác nhau [S2]',source_ids:['S2']}],missing_information:[{text:'Thiếu dữ liệu',source_ids:['E1']}],metadata:{
    citation_presentation:{S1:{number:2,url:'https://acme.example/about',public:true},S2:{number:1,url:'https://acme.example/about',public:true},E1:{number:null,public:false}},
  }};
  const html=context.chatReadableReport(report);
  assert.ok(html.includes('href="https://www.acme.example/about?utm_source=news#team"'));
  assert.ok(html.includes('>Nguồn khác nhau'));
  assert.match(html,/<a[^>]+>\[1\]<\/a>/);
  assert.ok(html.includes('Mâu thuẫn cần lưu ý')&&html.includes('Email nội bộ'));
});

test('reader renders the new research sections and one consolidated identity warning',()=>{
  const context=harness();
  const report={
    title:'Vinamilk', executive_summary:'Tóm tắt [S1]', sections:{
      company:'Vinamilk', company_scale:'Quy mô [S1]', business_context:'Bối cảnh [S1]',
      meeting_preparation:'Câu hỏi chuẩn bị [S1]', meeting_context:'Nội dung cũ không nên lặp',
    }, sources:[{id:'S1',source_type:'web',derived:false,title:'Vinamilk',url:'https://vinamilk.example'}], claims:[],
    metadata:{quality_policy_version:'report-quality-v2',evidence_policy_version:'company-boundary-v1',partner_identity:{conflicts:['Tên công ty trong chữ ký khác nhau'],affiliation_warnings:['Chưa xác minh quyền đại diện']}},
  };
  const html=context.chatReadableReport(report);
  for(const label of ['Quy mô và vận hành','Bối cảnh liên quan','Mục tiêu và câu hỏi chuẩn bị'])assert.ok(html.includes(label));
  assert.equal((html.match(/class="chat-reader-warning"/g)||[]).length,1);
  assert.ok(html.includes('Nội dung cũ không nên lặp'));
});

test('reader only displays fresh future structured availability slots',()=>{
  const context=harness();
  const checked=new Date(Date.now()-60*1000).toISOString();
  const future=new Date(Date.now()+60*60*1000).toISOString();
  const futureEnd=new Date(Date.now()+105*60*1000).toISOString();
  const past=new Date(Date.now()-60*60*1000).toISOString();
  const report={title:'Acme',executive_summary:'Summary',sections:{},sources:[],claims:[],metadata:{
    availability:{status:'available',checked_at:checked,timezone:'Asia/Ho_Chi_Minh',checked_sources:['Lịch local'],slots:[
      {label:'Hôm nay',window:'09:00–09:45',start:future,end:futureEnd},
      {label:'Đã qua',window:'08:00–08:45',start:past,end:future},
    ]},quality_policy_version:'report-quality-v2',
  }};
  const html=context.chatReadableReport(report);
  assert.ok(html.includes('Lịch trống trong tuần'));
  assert.ok(html.includes('09:00–09:45'));
  assert.ok(!html.includes('08:00–08:45'));
  assert.ok(html.includes('Asia/Ho_Chi_Minh')&&html.includes('Lịch local'));
});

test('reader rejects future checks and non-available calendar status',()=>{
  const context=harness();
  const future=new Date(Date.now()+60*60*1000).toISOString();
  const end=new Date(Date.now()+105*60*1000).toISOString();
  const report={title:'Acme',executive_summary:'Summary',sections:{},sources:[],claims:[],metadata:{availability:{
    status:'unknown',checked_at:new Date(Date.now()+1000).toISOString(),timezone:'Asia/Ho_Chi_Minh',checked_sources:['Lịch local'],slots:[{label:'Không được đề xuất',window:'10:00–10:45',start:future,end}],
  }}};
  const html=context.chatReadableReport(report);
  assert.ok(html.includes('Chưa có khung giờ còn hiệu lực để đề xuất.'));
  assert.ok(!html.includes('10:00–10:45'));
});

test('quality disclosure uses real metrics and marks legacy evaluations as unknown',()=>{
  const context=harness();
  const base={title:'Acme',executive_summary:'Summary',sections:{},sources:[],claims:[],metadata:{performance:{scope:'research_workflow',duration_seconds:42,cache_hit:true,stages:{research:{duration_seconds:31.1234,reused:false}}}}};
  const current=context.chatReadableReport({...base,metadata:{...base.metadata,quality_policy_version:'report-quality-v2'},evaluation:{quality_policy_version:'report-quality-v2',rubric_version:'report-quality-rubric-v3',schema_version:2,gate_status:'pass',release_allowed:true,metrics:{completeness:.98,source_count:12,unsupported_claims:0}}});
  assert.ok(current.includes('Chất lượng báo cáo')&&current.includes('98%')&&current.includes('42.00 giây'));
  assert.ok(current.includes('Tổng số nguồn (mọi loại)')&&current.includes('Thời gian workflow nghiên cứu')&&current.includes('31.12 giây'));
  assert.ok(current.includes('Đánh giá theo chuẩn hiện tại')&&!current.includes('Độ chính xác'));
  const mismatched=context.chatReadableReport({...base,metadata:{...base.metadata,quality_policy_version:'report-quality-v2'},evaluation:{quality_policy_version:'legacy',schema_version:2,metrics:{completeness:.98}}});
  assert.ok(mismatched.includes('Chưa đánh giá theo chuẩn mới'));
  const legacy=context.chatReadableReport({...base,evaluation:{release_allowed:true,metrics:{completeness:.98}}});
  assert.ok(legacy.includes('Chưa đánh giá theo chuẩn mới'));
  const unknown=context.chatReadableReport({...base,metadata:{quality_policy_version:'report-quality-v2'},evaluation:{quality_policy_version:'report-quality-v2',rubric_version:'report-quality-rubric-v3',schema_version:2,metrics:{freshness:.5,freshness_known_sources:0}}});
  assert.ok(unknown.includes('Độ mới</span><b>Chưa xác định'));
  assert.ok(!unknown.includes('Độ mới</span><b>50%'));
  const semantic=context.chatReadableReport({...base,metadata:{...base.metadata,quality_policy_version:'report-quality-v2'},evaluation:{quality_policy_version:'report-quality-v2',rubric_version:'report-quality-rubric-v3',schema_version:2,status_details:{semantic_review:'unavailable_or_incomplete'},metrics:{completeness:.8}}});
  assert.ok(semantic.includes('chưa đủ hoặc chưa khả dụng'));
});

test('reader blocks action controls until the release reference has the v2 quality policy',()=>{
  const context=harness();
  vm.runInContext("D={approvals:{items:[{id:'a',tool_name:'save_report_to_knowledge',status:'pending',arguments:{workspace_id:'ws-a',report_id:'r'}}]}}",context);
  const turn={reports:[{workspace_id:'ws-a',report_id:'r',title:'Draft',release_allowed:true,evidence_policy_version:'company-boundary-v1',quality_policy_version:'report-quality-v1',rubric_version:'report-quality-rubric-v3'}]};
  assert.ok(!context.chatReportActions(turn).includes('Import vào sổ tay'));
  turn.reports[0].quality_policy_version='report-quality-v2';
  turn.reports[0].rubric_version='report-quality-rubric-v3';
  assert.ok(context.chatReportActions(turn).includes('Import vào sổ tay'));
});

test('reader css keeps the wide modal and quality surfaces readable',()=>{
  const css=fs.readFileSync(path.join(__dirname,'../../pu/ops/static/notebook.css'),'utf8');
  assert.match(css,/chat-report-dialog[^}]+1320px/);
  for(const token of ['chat-reader-quality','chat-reader-slots','chat-reader-extra-sources'])assert.ok(css.includes(token));
});

