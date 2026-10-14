"""Execute Web contract helpers in Node; browser acceptance remains separate."""
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "Node is needed only for Web contract verification")
class WebContractTests(unittest.TestCase):
    def run_js(self, assertions):
        setup = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const elements=new Map();
const element=selector=>{if(!elements.has(selector))elements.set(selector,{innerHTML:'',hidden:false,value:'',textContent:'',className:'',listeners:{},addEventListener(type,callback){this.listeners[type]=callback;},focus(){},setAttribute(){},classList:{toggle(){}}});return elements.get(selector);};
const context=vm.createContext({document:{querySelector:element,querySelectorAll:()=>[],body:{append(){}},createElement:()=>({click(){},remove(){}})},fetch:async()=>{throw Error('unexpected fetch');},crypto:require('node:crypto'),Blob,atob,URL:{createObjectURL:()=> 'blob:synthetic',revokeObjectURL(){}},setTimeout(){}});
let source=fs.readFileSync(process.argv[1],'utf8').replace(/\nbootstrap\(\);\s*$/,'');
vm.runInContext(source,context);
const evaluate=code=>vm.runInContext(code,context);
const historyListResponse=(conversations=[],has_more=false)=>({ok:true,json:async()=>({conversations,has_more,data_kind:'synthetic'})});
"""
        script = setup + "\n(async()=>{\n" + assertions + "\n})().catch(error=>{console.error(error);process.exitCode=1;});"
        result = subprocess.run([NODE, "-e", script, str(ROOT / "web" / "app.js")],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_authoritative_status_and_unknown_credit_fields(self):
        self.run_js("""
assert.match(evaluate("statusBadge('pass')"),/满足/);
assert.match(evaluate("statusBadge('fail')"),/不满足/);
assert.match(evaluate("statusBadge('unknown')"),/待确认/);
assert.equal(evaluate("planCredits({validation:{total_credits:null},sections:[{credits:3}]})"),null);
assert.equal(evaluate("planCredits({validation:{planned_credits:0},sections:[{credits:3}]})"),0);
assert.equal(evaluate("academicStatus({completion_status:'in_progress',status:'passed'})"),'in_progress');
assert.match(evaluate("academicMeasurements({earned_credits:null,credits:5}).join('、')"),/已获学分 待确认/);
assert.doesNotMatch(evaluate("academicMeasurements({earned_credits:null,credits:5}).join('、')"),/5/);
""")

    def test_anonymous_bootstrap_never_requests_personal_data(self):
        self.run_js("""
const paths=[];context.fetch=async(path)=>{paths.push(path);return {ok:true,json:async()=>({authenticated:false,mock_enabled:false,mock_profiles:[]})};};
await evaluate('bootstrap()');
assert.deepEqual(paths,['/api/auth/session']);
assert.equal(element('#auth-panel').hidden,false);
assert.match(element('#auth-panel').innerHTML,/学校认证和个人学业数据源尚未接入/);
assert.doesNotMatch(element('#auth-panel').innerHTML,/type="password"/);
assert.equal(element('.nav-tabs').hidden,true);
""")

    def test_writes_use_csrf_and_401_erases_personal_workspace(self):
        self.run_js("""
evaluate("state.csrfToken='memory-only';state.auth={authenticated:true,mock_enabled:false};state.boot={private_record:'synthetic'};state.saved=[{saved_id:5}];state.agentDraft='草稿仅页面内存'");
let options;context.fetch=async(path,value)=>{options=value;return {ok:false,status:401,text:async()=>JSON.stringify({error:'请先登录'})};};
await assert.rejects(evaluate("api('/api/save',{plan:{}})"),/请先登录/);
assert.equal(options.credentials,'same-origin');
assert.equal(options.headers['X-CSRF-Token'],'memory-only');
assert.equal(evaluate('state.boot'),null);
assert.equal(evaluate('state.saved.length'),0);
assert.equal(evaluate('state.csrfToken'),null);
assert.equal(evaluate('state.agentDraft'),'');
assert.equal(element('#auth-panel').hidden,false);
""")

    def test_historical_export_uses_saved_id_and_frozen_input(self):
        self.run_js("""
evaluate("state.generated={plans:[{section_ids:['DEMO-OLD']} ]};state.loadedSaved={saved_id:19,frozen_dataset:{academic_calendar:{calendar_id:'DEMO-FROZEN'}}};state.csrfToken='memory-only'");
let body;context.fetch=async(path,options)=>{assert.equal(path,'/api/export');body=JSON.parse(options.body);return {ok:true,blob:async()=>({}),headers:{get:()=> 'attachment; filename="synthetic-plan.json"'}};};
await evaluate("exportPlan('json')");
assert.deepEqual(body,{saved_id:19,format:'json'});
assert.equal(evaluate('planDataset().academic_calendar.calendar_id'),'DEMO-FROZEN');
assert.match(element('#global-status').textContent,/保存时的冻结/);
""")

    def test_frozen_comparison_discloses_different_versions_without_ranking(self):
        self.run_js("""
const markup=evaluate("renderComparison({mode:'frozen',different_input_versions:true,ranking:[0,1],summaries:[{saved_id:1,plan:{section_ids:['DEMO-A'],validation:{result:'pass',total_credits:3}},versions:{offering_snapshot:'DEMO-OLD'},constraints:{}},{saved_id:2,plan:{section_ids:['DEMO-B'],validation:{result:'unknown',total_credits:null}},versions:{offering_snapshot:'DEMO-NEW'},constraints:{}}]})");
assert.match(markup,/输入版本不同/);
assert.match(markup,/DEMO-OLD/);assert.match(markup,/DEMO-NEW/);
assert.doesNotMatch(markup,/按校验结果及周五软偏好排序/);
""")

    def test_no_solution_exposes_specific_conflicts_and_actions(self):
        self.run_js("""
evaluate("state.generated={status:'scoped_infeasible',reason:'当前范围内条件不兼容',diagnostics:[{code:'friday_free',result:'fail',section_ids:['DEMO-ML-B'],reason:'周五必须空闲条件冲突'},{code:'friday_free',result:'fail',section_ids:['DEMO-ML-B'],reason:'周五必须空闲条件冲突'}],suggestions:[{code:'friday_free',section_ids:['DEMO-ML-B'],reason:'周五必须空闲条件冲突',action:'审查个人偏好，锁定项保留'}]};renderPlanResults()");
const markup=element('#plans-results').innerHTML;
assert.match(markup,/当前范围内条件不兼容/);assert.match(markup,/周五空闲条件/);
assert.match(markup,/DEMO-ML-B/);assert.match(markup,/审查个人偏好，锁定项保留/);
assert.doesNotMatch(markup,/scoped_infeasible/);
assert.equal(evaluate('uniqueChecks(state.generated.diagnostics).length'),1);
""")

    def test_source_audit_summary_remains_unknown_and_synthetic_totals_remain_visible(self):
        self.run_js("""
evaluate("state.mode='source';state.sourceAudit={ledger:{base:0,extra_classrooms:{total:0},in_progress:{credits:0},unknown:{record_count:0}},rules:[]};renderAudit()");
const source=element('#panel-audit').innerHTML;
const sourceSummary=source.match(/<dl class="audit-summary">[\\s\\S]*?<\\/dl>/)[0];
assert.equal((sourceSummary.match(/待确认<span>/g)||[]).length,4);
assert.doesNotMatch(sourceSummary,/>0<span>/);
assert.match(source,/本人学业来源未接入/);
evaluate("state.mode='demo';state.boot={demo:{student:{data_kind:'synthetic'},courses:[],rules:[],academic_snapshot:{records:[]}}};state.audit={ledger:{base:8,extra_classrooms:{total:4},in_progress:{credits:3},unknown:{record_count:1}},rules:[]};renderAudit()");
const demoSummary=element('#panel-audit').innerHTML.match(/<dl class="audit-summary">[\\s\\S]*?<\\/dl>/)[0];
assert.deepEqual([...demoSummary.matchAll(/<dd class="numeric">(.*?)<span>/g)].map(match=>match[1]),['8','4','3','1']);
""")

    def test_agent_is_default_entry_with_core_composer_and_unsent_memory_draft(self):
        self.run_js("""
assert.equal(evaluate('state.tab'),'assistant');
const html=fs.readFileSync(process.argv[1].replace(/app\\.js$/,'index.html'),'utf8');
assert.match(html,/id="tab-assistant" class="nav-tab active"/);
assert.deepEqual([...html.matchAll(/data-tab="([^"]+)"/g)].map(match=>match[1]),['assistant','saved','school']);
assert.match(html,/id="tab-school"[^>]*hidden/);
evaluate("state.boot={demo:{courses:[],constraints:{revision:1,friday_free:'soft'}}};state.constraints={revision:1,friday_free:'soft'};renderAssistant()");
const markup=element('#panel-assistant').innerHTML;
assert.match(markup,/<button[^>]*type="submit"[^>]*disabled[^>]*>发送/);
assert.match(markup,/模型未配置/);assert.match(markup,/aria-label="对话历史"/);
assert.match(markup,/<label[^>]*for="agent-draft"[^>]*>选课需求/);
evaluate("state.agentDraft='保留体育，周五必须空闲';renderAssistant()");
assert.equal(element('#agent-draft').value,evaluate('state.agentDraft'));
assert.equal(evaluate('state.constraints.friday_free'),'soft');
evaluate('clearAgentDraft()');assert.equal(evaluate('state.agentDraft'),'');
""")

    def test_agent_preview_uses_server_conditions_without_sending_or_parsing_draft(self):
        self.run_js("""
evaluate("state.boot={identity:{student_id:'DEMO-STUDENT-001'},demo:{courses:[],constraints:{revision:1,friday_free:'soft'}}};state.constraints={revision:1,friday_free:'soft'};state.agentDraft='DEMO-DRAFT-MARKER: 周五必须空闲';state.loadedSaved={constraints:{friday_free:'off'}}");
const requests=[];
context.fetch=async(path,options)=>{
  const body=options.body?JSON.parse(options.body):undefined;requests.push({path,body});
  if(path==='/api/bootstrap')return {ok:true,json:async()=>({identity:{student_id:'DEMO-STUDENT-001'},demo:{courses:[],constraints:{revision:7,friday_free:'off',locked_section_ids:['DEMO-PE-A']}}})};
  if(path==='/api/generate')return {ok:true,json:async()=>({plans:[],pending_plans:[],constraints:{revision:8,friday_free:'off',locked_section_ids:['DEMO-PE-A']},reason:'当前范围待确认'})};
  throw Error('unexpected endpoint '+path);
};
await evaluate('previewCurrentConditions()');
assert.deepEqual(requests.map(item=>item.path),['/api/bootstrap','/api/generate']);
assert.equal(requests[1].body.constraints.friday_free,'off');
assert.equal(requests[1].body.constraints.expected_revision,7);
assert.deepEqual(requests[1].body.constraints.locked_section_ids,['DEMO-PE-A']);
assert.doesNotMatch(JSON.stringify(requests),/DEMO-DRAFT-MARKER/);
assert.equal(evaluate('state.agentDraft'),'DEMO-DRAFT-MARKER: 周五必须空闲');
assert.equal(evaluate('state.tab'),'planning');
assert.match(element('#global-status').textContent,/草稿未发送，也未参与计算/);
evaluate("state.mode='source';renderAssistant()");
assert.doesNotMatch(element('#panel-assistant').innerHTML,/data-action="preview-current-conditions"/);
""")

    def test_agent_draft_cleared_on_logout_and_authenticated_subject_change(self):
        self.run_js("""
evaluate("state.auth={authenticated:true,identity:{student_id:'DEMO-STUDENT-001'}};state.agentDraft='A 的未发送草稿';state.csrfToken='memory-only'");
context.fetch=async()=>({ok:true,json:async()=>({authenticated:false})});
await evaluate('logout()');assert.equal(evaluate('state.agentDraft'),'');
evaluate("state.auth={authenticated:true,identity:{student_id:'DEMO-STUDENT-001'}};state.agentDraft='A 的另一个草稿'");
context.fetch=async(path)=>({ok:true,json:async()=>path==='/api/auth/session'?{authenticated:true,identity:{student_id:'DEMO-STUDENT-002'},csrf_token:'new-memory-only'}:{identity:{student_id:'DEMO-STUDENT-002'},demo:{constraints:{revision:1},courses:[],rules:[]},source:{},saved_plans:[]}});
await evaluate('bootstrap()');
assert.equal(evaluate('state.agentDraft'),'');assert.equal(element('#agent-draft').value,'');
assert.equal(evaluate('state.auth.identity.student_id'),'DEMO-STUDENT-002');
assert.equal(evaluate('state.tab'),'assistant');
""")

    def test_agent_draft_cleared_immediately_on_workspace_subject_change(self):
        self.run_js("""
evaluate("state.boot={identity:{student_id:'DEMO-STUDENT-001'}};state.agentDraft='A 的可见草稿';state.agentHistory=[{conversation_id:'A-conv',title:'A 的历史'}];renderAgentHistory()");
element('#agent-draft').value='A 的可见草稿';
context.fetch=async()=>({ok:true,json:async()=>({identity:{student_id:'DEMO-STUDENT-002'},demo:{constraints:{revision:1}},saved_plans:[]})});
await evaluate('refreshWorkspace()');
assert.equal(evaluate('state.agentDraft'),'');
assert.equal(element('#agent-draft').value,'');assert.equal(evaluate('state.agentHistory.length'),0);assert.doesNotMatch(element('#conversation-list').innerHTML,/A 的历史/);
""")

    def test_chat_requires_model_and_remote_consent_and_sends_only_turn_contract(self):
        self.run_js("""
evaluate("state.auth={authenticated:true};state.boot={demo:{courses:[],constraints:{revision:3}}};state.constraints={revision:3};state.agentDraft='查询合成课程';state.csrfToken='memory-only'");
assert.equal(evaluate('agentCanSend()'),false);await evaluate('sendAgentTurn()');
evaluate("state.agentStatus={configured:true,provider:'DeepSeek',model:'test-model',remote:true,allow_reference_text:false}");
assert.equal(evaluate('agentCanSend()'),false);await evaluate('sendAgentTurn()');
const payloads=[];context.fetch=async(path,options)=>{if(path==='/api/chat/conversations?limit=100')return historyListResponse([{conversation_id:'conv-A',title:'合成课程查询',turn_id:payloads.length}]);assert.equal(path,'/api/chat/turn');assert.equal(options.headers['X-CSRF-Token'],'memory-only');const body=JSON.parse(options.body);payloads.push(body);return {ok:true,json:async()=>({conversation_id:'conv-A',turn_id:payloads.length,status:'completed',reply:'已查询',tool_results:[],citations:[],data_kind:'synthetic'})};};
evaluate('state.agentConsent=true');assert.equal(evaluate('agentCanSend()'),true);await evaluate('sendAgentTurn()');
assert.deepEqual(Object.keys(payloads[0]).sort(),['consent_remote','conversation_id','expected_turn','message','request_id']);
assert.equal(payloads[0].conversation_id,null);assert.equal(payloads[0].expected_turn,0);assert.equal(payloads[0].consent_remote,true);assert.match(payloads[0].request_id,/^[0-9a-f-]{36}$/);
evaluate("state.agentDraft='保留体育后再查询'");await evaluate('sendAgentTurn()');
assert.equal(payloads[1].conversation_id,'conv-A');assert.equal(payloads[1].expected_turn,1);
assert.equal(evaluate('state.agentTurnId'),2);assert.equal(evaluate('state.agentTurns.length'),2);
assert.equal(evaluate('state.constraints.revision'),3);assert.equal(evaluate('state.generated'),null);
""")

    def test_chat_delayed_success_and_401_cannot_repopulate_or_log_out_new_subject(self):
        self.run_js("""
evaluate("state.auth={authenticated:true,identity:{student_id:'A'}};state.boot={demo:{courses:[]}};state.agentStatus={configured:true,remote:false};state.agentDraft='A的合成问题'");
let release;context.fetch=()=>new Promise(resolve=>{release=resolve;});
const pending=evaluate('sendAgentTurn()');
evaluate("clearWorkspace();state.auth={authenticated:true,identity:{student_id:'B'}}");
release({ok:true,json:async()=>({conversation_id:'A-conversation',turn_id:1,status:'completed',reply:'A的结果',tool_results:[],citations:[],data_kind:'synthetic'})});await pending;
assert.equal(evaluate('state.agentTurns.length'),0);assert.equal(evaluate('state.agentConversationId'),null);assert.equal(evaluate('state.auth.identity.student_id'),'B');
evaluate("state.boot={demo:{courses:[]}};state.agentStatus={configured:true,remote:false};state.agentDraft='B的合成问题'");
const late401=evaluate('sendAgentTurn()');evaluate("clearWorkspace();state.auth={authenticated:true,identity:{student_id:'C'}}");
release({ok:false,status:401,text:async()=>JSON.stringify({error:'old session expired'})});await late401;
assert.equal(evaluate('state.auth.authenticated'),true);assert.equal(evaluate('state.auth.identity.student_id'),'C');assert.equal(evaluate('state.agentTurns.length'),0);
""")

    def test_chat_evidence_is_escaped_and_plan_preview_cannot_be_saved_as_workspace(self):
        self.run_js("""
evaluate("state.auth={authenticated:true};state.boot={demo:{courses:[]}};state.agentStatus={configured:true,remote:false};state.agentDraft='生成合成课表';state.constraints={revision:5};state.generated={existing:'workspace'}");
context.fetch=async()=>({ok:true,json:async()=>({conversation_id:'conv',turn_id:1,status:'completed',reply:'<script>bad()</script>',data_kind:'synthetic',tool_results:[{name:'generate_my_plan',arguments:{},result:{preview_only:true,constraints:{revision:6,locked_section_ids:['DEMO-PE-A'],friday_free:'hard'},plans:[{section_ids:['DEMO-PE-A'],validation:{result:'fail',total_credits:null,checks:[{result:'fail',code:'friday_free',reason:'周五必须空闲冲突'}]},sections:[{section_id:'DEMO-PE-A',course_name:'<img src=x onerror=bad()>',meetings:[]}]}]}}],citations:[{title:'参考<script>',text:'<img src=x>',locator:{json_pointer:'/candidate/0'},record_id:'R1',url:'javascript:bad()'}]})});
await evaluate('sendAgentTurn()');const markup=element('#panel-assistant').innerHTML;
assert.doesNotMatch(markup,/<script>|<img|href="javascript:/);assert.match(markup,/&lt;script&gt;/);assert.match(markup,/badge danger">不满足/);assert.match(markup,/周五必须空闲冲突/);assert.match(markup,/待审核/);
assert.doesNotMatch(markup,/data-action="save-plan"|data-action="export"/);assert.equal(evaluate('state.constraints.revision'),5);assert.equal(evaluate('state.generated.existing'),'workspace');
""")

    def test_chat_plan_diagnostics_keep_conflicts_and_unknown_without_debug_json(self):
        self.run_js("""
const markup=evaluate("agentToolCard({name:'generate_my_plan',arguments:{},result:{status:'scoped_infeasible',reason:'当前条件不兼容',plans:[],diagnostics:[{code:'friday_free',result:'fail',section_ids:['DEMO-ML-B'],reason:'锁定班与周五必须空闲冲突'},{code:'exam_schedule',result:'unknown',section_ids:['DEMO-UNKNOWN'],reason:'考试安排未提供'}]}})");
assert.match(markup,/锁定班与周五必须空闲冲突/);assert.match(markup,/考试安排未提供/);assert.match(markup,/badge danger">不满足/);assert.match(markup,/badge warning">待确认/);
const validation=evaluate("agentToolCard({name:'validate_my_plan',arguments:{},result:{checks:[{code:'time_conflict',result:'fail',section_ids:['DEMO-A','DEMO-B'],reason:'时段冲突'}],plan:{validation:{checks:[{code:'time_conflict',result:'fail',section_ids:['DEMO-A','DEMO-B'],reason:'时段冲突'}]},sections:[]}}})");
assert.match(validation,/时段冲突/);assert.equal((validation.match(/时段冲突/g)||[]).length,1);
const diagnostics=evaluate("agentPlanDiagnostics({diagnostics:[...Array.from({length:9},(_,i)=>({code:'fail-'+i,result:'fail',reason:'冲突'+i})),{code:'unknown-last',result:'unknown',reason:'最后的未知考试'},{code:'known-pass',result:'pass',reason:'已确认通过'}]})");
assert.match(diagnostics,/最后的未知考试/);assert.doesNotMatch(diagnostics,/已确认通过/);
""")

    def test_chat_model_failure_keeps_tool_evidence_separate_from_model_reply(self):
        self.run_js("""
evaluate("state.boot={demo:{courses:[]}};state.agentTurns=[{message:'合成问题',status:'model_error',reply:'模型服务超时，已执行的工具结果如下。',tool_results:[{name:'get_my_context',arguments:{},result:{data_kind:'synthetic',unknown:['考试缺失']}}],citations:[]}];renderAssistant()");
const markup=element('#panel-assistant').innerHTML;assert.match(markup,/本轮未完成/);assert.match(markup,/模型服务超时/);assert.match(markup,/考试缺失/);assert.match(markup,/badge warning">待确认/);assert.doesNotMatch(markup,/class="agent-message assistant"/);
""")

    def test_chat_reply_origin_labels_verified_results_clarification_and_partial_failure(self):
        self.run_js("""
evaluate("state.boot={demo:{courses:[]}};state.agentTurns=[{message:'查询',status:'completed',reply_origin:'verified_tool_presentation',reply:'状态待确认',tool_results:[],citations:[]},{message:'继续',status:'completed',reply_origin:'verified_clarification_prompt',reply:'请选择课程范围',tool_results:[],citations:[]},{message:'计算',status:'failed',partial_tools_completed:true,reply:'模型后续调用失败',tool_results:[{name:'get_my_context',arguments:{},result:{data_kind:'synthetic'}}],citations:[]}];renderAssistant()");
const markup=element('#panel-assistant').innerHTML;
assert.ok(markup.includes('<strong>选课助手</strong>'));assert.ok(markup.includes('<strong>待澄清</strong>'));assert.ok(markup.includes('<strong>本轮未完成</strong>'));assert.match(markup,/已完成的查询结果如下/);assert.match(markup,/模型后续调用失败/);
""")

    def test_chat_transport_retry_reuses_request_id_and_malformed_result_is_rejected(self):
        self.run_js("""
evaluate("state.auth={authenticated:true};state.boot={demo:{courses:[]}};state.agentStatus={configured:true,remote:false};state.agentDraft='查询合成课程'");
const payloads=[];context.fetch=async(path,options)=>{payloads.push(JSON.parse(options.body));if(payloads.length===1)throw Error('connection dropped');return {ok:true,json:async()=>({conversation_id:'c',turn_id:1,status:'completed',reply:'结果',tool_results:[],citations:[],data_kind:'synthetic'})};};
await evaluate('sendAgentTurn()');assert.equal(evaluate('state.agentDraft'),'查询合成课程');await evaluate('sendAgentTurn()');assert.equal(payloads[0].request_id,payloads[1].request_id);
evaluate("state.agentDraft='继续'");context.fetch=async()=>({ok:true,json:async()=>({conversation_id:'c',turn_id:2,status:'completed',reply:'untrusted incomplete response'})});await evaluate('sendAgentTurn()');
assert.equal(evaluate('state.agentTurnId'),1);assert.match(element('#panel-assistant').innerHTML,/对话响应契约不完整/);
""")

    def test_chat_realm_change_erases_conversation_and_model_change_revokes_consent(self):
        self.run_js("""
evaluate("state.boot={identity:{student_id:'same-id',security_realm:'A'},demo:{}};state.agentTurns=[{message:'A question'}];state.agentConversationId='conv-A';state.agentConsent=true;state.agentHistory=[{conversation_id:'conv-A',title:'A 域历史'}];renderAgentHistory()");
context.fetch=async()=>({ok:true,json:async()=>({identity:{student_id:'same-id',security_realm:'B'},demo:{constraints:{}}})});await evaluate('refreshWorkspace()');
assert.equal(evaluate('state.agentTurns.length'),0);assert.equal(evaluate('state.agentConversationId'),null);assert.equal(evaluate('state.agentConsent'),false);assert.equal(evaluate('state.agentHistory.length'),0);assert.doesNotMatch(element('#conversation-list').innerHTML,/A 域历史/);
evaluate("state.agentStatus={configured:true,provider:'DeepSeek',model:'old',remote:true};state.agentConsent=true");context.fetch=async()=>({ok:true,json:async()=>({configured:true,provider:'DeepSeek',model:'new',remote:true})});await evaluate('refreshAgentStatus()');assert.equal(evaluate('state.agentConsent'),false);
""")

    def test_recommendation_card_keeps_course_times_unknown_and_reference_actions(self):
        self.run_js("""
evaluate("state.auth={authenticated:true};state.agentConversationId='conv';state.agentTurnId=1;state.boot={versions:{dataset:'v1'},demo:{courses:[]}}");
const result={run_id:'run-1',workspace_versions:{dataset:'v1'},intent:{summary:'AI 方向专业选修合成目标'},constraints:{revision:2,friday_free:'soft',locked_section_ids:['DEMO-PE-A']},candidates:[{course_id:'DEMO-AI',course_name:'合成 AI',reason:'目标模块候选'}],plans:[{option_id:'option-A',course_ids:['DEMO-AI'],section_ids:['DEMO-AI-A'],metrics:{module_credits:3,friday_meeting_count:1},evidence:['合成课程说明支持 AI 主题'],differences:{reason:'相对基准换入 AI 课程'},validation:{result:'pass',planned_credits:3,checks:[]},sections:[{section_id:'DEMO-AI-A',course_id:'DEMO-AI',course_name:'<img src=x>',meetings:[{day_of_week:5,week_numbers:[1,2],period_indices:[1,2],season_codes:['秋']}]}]}],pending_plans:[{option_id:'option-U',course_ids:['DEMO-U'],metrics:{module_credits:null},validation:{result:'unknown',planned_credits:null},sections:[]}]};
context.result=result;evaluate("state.agentTurns=[{tool_results:[{name:'recommend_my_courses',result}]}]");
const markup=evaluate("agentToolCard({name:'recommend_my_courses',result})");
assert.match(markup,/3 学分/);assert.match(markup,/相对基准换入 AI 课程/);assert.match(markup,/周五有 1 个上课时段/);assert.match(markup,/秋 · 1、2周 · 周五 · 1、2节/);
assert.match(markup,/badge warning">待确认/);assert.match(markup,/待确认 学分/);assert.match(markup,/保存这套/);assert.match(markup,/data-action="save-agent-option" data-option-id="option-A" >/);for(const format of ['json','csv','ics'])assert.match(markup,new RegExp('data-action="export-agent-option" data-option-id="option-A" data-format="'+format+'"'));assert.doesNotMatch(markup,/<img/);assert.match(markup,/&lt;img/);
assert.equal(evaluate("planName({label:'U1'})"),'待确认方案 U1');
assert.match(evaluate("agentMetricRows({contribution_state:'unknown',goal_contribution:0})"),/目标预计贡献（学分）<\\/dt><dd>待确认/);
assert.match(evaluate("agentRecommendationScope({search:{course_pool_size:4,limit_reached:true,candidate_retention_clipped:true}})"),/搜索尚未穷尽/);

assert.equal(evaluate("agentEvidenceTexts([{snapshot_id:'DEMO-E'},{snapshot_id:'DEMO-E'}]).length"),1);
""")

    def test_old_runs_and_changed_workspace_disable_reference_actions_but_comparison_keeps_current(self):
        self.run_js("""
evaluate("state.auth={authenticated:true};state.agentConversationId='conv';state.boot={versions:{dataset:'v1',revision:2},demo:{}};state.agentTurns=[{tool_results:[{name:'recommend_my_courses',result:{run_id:'old'}}]},{tool_results:[{name:'revise_my_recommendation',result:{run_id:'new'}},{name:'compare_my_options',result:{run_id:'old'}}]}]");
assert.equal(evaluate('latestAgentRunId()'),'new');
assert.match(evaluate("agentRecommendationPlan({option_id:'old-A',run_id:'old',validation:{result:'pass'}},{workspace_versions:{dataset:'v1',revision:2}},0)"),/上一轮推荐/);
assert.match(evaluate("agentRecommendationPlan({option_id:'new-A',run_id:'new',validation:{result:'pass'}},{workspace_versions:{dataset:'v2',revision:2}},0)"),/输入版本已变化/);
assert.equal(evaluate("agentOptionState({option_id:'new-A',run_id:'new',versions:{revision:99},validation:{result:'pass'}},{workspace_versions:{revision:2,dataset:'v1'}}).enabled"),true);
assert.match(evaluate("agentRecommendationPlan({validation:{result:'pass'}},{},0)"),/服务端引用/);
assert.match(evaluate("agentRecommendationPlan({option_id:'new-A',run_id:'new',validation:{result:'fail'}},{},0)"),/已知不满足项/);
assert.match(evaluate("agentToolCard({name:'compare_my_options',result:{summary:'A 多一门课程，B 周五空闲',differences:[{title:'课程差异',reason:'A 含 DEMO-A，B 含 DEMO-B'}]}})"),/A 含 DEMO-A，B 含 DEMO-B/);
evaluate("state.agentTurns.push({status:'model_error',tool_results:[{name:'revise_my_recommendation',result:{run_id:'failed-run',workspace_versions:{dataset:'v1',revision:2},plans:[{option_id:'failed-A',validation:{result:'pass'}}]}}]})");
assert.equal(evaluate('latestAgentRunId()'),'new');
assert.equal(evaluate("agentOptionState({option_id:'new-A',run_id:'new',validation:{result:'pass'}},{workspace_versions:{dataset:'v1',revision:2}}).enabled"),true);
assert.equal(evaluate("agentOptionState(findAgentOption('failed-A').plan,findAgentOption('failed-A').result).enabled"),false);
assert.match(evaluate("agentRecommendationPlan(findAgentOption('failed-A').plan,findAgentOption('failed-A').result,0)"),/本轮未完成/);
let writes=0;context.fetch=async()=>{writes+=1;throw Error('a failed turn cannot save');};await evaluate("saveAgentOption('failed-A')");assert.equal(writes,0);
""")

    def test_save_reference_refreshes_workspace_without_using_workspace_generated_plan(self):
        self.run_js("""
evaluate("state.auth={authenticated:true};state.csrfToken='memory-only';state.agentConversationId='conv';state.agentTurnId=3;state.boot={versions:{revision:1},demo:{courses:[]}};state.generated={plans:[{section_ids:['WRONG-WORKSPACE-PLAN']}]};state.agentTurns=[{tool_results:[{name:'recommend_my_courses',result:{run_id:'run-1',workspace_versions:{revision:1},plans:[{option_id:'option-A',run_id:'run-1',validation:{result:'pass'}}]}}]}]");
const requests=[];
context.fetch=async(path,options)=>{const body=options.body?JSON.parse(options.body):undefined;requests.push({path,body});if(path==='/api/chat/save-option')return {ok:true,json:async()=>({saved_id:7,conversation_id:'conv',turn_id:3,action_id:body.action_id,constraints:{revision:2,requested_course_ids:['DEMO-AI']},versions:{revision:2},preview_valid:true})};if(path==='/api/bootstrap')return {ok:true,json:async()=>({versions:{revision:2},demo:{courses:[],rules:[],constraints:{revision:2,requested_course_ids:['DEMO-AI']}},saved_plans:[{saved_id:7,plan:{},versions:{revision:2}}]})};throw Error('unexpected '+path);};
await evaluate("saveAgentOption('option-A')");
assert.deepEqual(requests.map(row=>row.path),['/api/chat/save-option','/api/bootstrap']);assert.deepEqual(Object.keys(requests[0].body).sort(),['action_id','conversation_id','expected_turn','option_id','run_id']);assert.equal(requests[0].body.expected_turn,3);assert.equal(requests[0].body.run_id,'run-1');assert.match(requests[0].body.action_id,/^[0-9a-f-]{36}$/);assert.doesNotMatch(JSON.stringify(requests),/WRONG-WORKSPACE-PLAN/);
assert.equal(evaluate('state.agentConversationId'),'conv');assert.equal(evaluate('state.agentTurnId'),3);assert.equal(evaluate('state.constraints.revision'),2);assert.equal(evaluate('state.generated'),null);assert.equal(evaluate("agentOptionState(findAgentOption('option-A').plan,findAgentOption('option-A').result).enabled"),true);assert.match(element('#global-status').textContent,/草案 #7/);
await evaluate("saveAgentOption('option-A')");assert.equal(requests.length,2);
""")

    def test_reference_save_transport_retry_reuses_action_id_and_version_rejection_invalidates_card(self):
        self.run_js("""
evaluate("state.auth={authenticated:true};state.agentConversationId='conv';state.agentTurnId=1;state.boot={versions:{revision:1},demo:{courses:[]}};state.agentTurns=[{tool_results:[{name:'recommend_my_courses',result:{run_id:'run-1',workspace_versions:{revision:1},plans:[{option_id:'option-A',validation:{result:'pass'}}]}}]}]");
const payloads=[];context.fetch=async(path,options)=>{payloads.push(JSON.parse(options.body));if(payloads.length===1)throw Error('connection dropped');return {ok:false,status:409,text:async()=>JSON.stringify({error:'版本变化，请重新推荐',code:'workspace_changed'})};};
await evaluate("saveAgentOption('option-A')");await evaluate("saveAgentOption('option-A')");assert.equal(payloads[0].action_id,payloads[1].action_id);assert.equal(evaluate('state.agentActionBusy'),false);assert.equal(evaluate("findAgentOption('option-A').plan.preview_valid"),false);assert.match(element('#panel-assistant').innerHTML,/版本变化，请重新推荐/);
await evaluate("saveAgentOption('option-A')");assert.equal(payloads.length,2);
""")

    def test_reference_export_download_uses_only_server_option_and_retains_unknown(self):
        self.run_js("""
evaluate("state.auth={authenticated:true};state.csrfToken='memory-only';state.agentConversationId='conv';state.agentTurnId=2;state.boot={demo:{courses:[]}};state.generated={plans:[{section_ids:['WRONG-WORKSPACE-PLAN']}]};state.agentTurns=[{tool_results:[{name:'recommend_my_courses',result:{run_id:'run-1',pending_plans:[{option_id:'option-U',validation:{result:'unknown'}}]}}]}]");
let body;context.fetch=async(path,options)=>{assert.equal(path,'/api/chat/export-option');assert.equal(options.headers['X-CSRF-Token'],'memory-only');body=JSON.parse(options.body);return {ok:true,blob:async()=>({}),headers:{get:()=> 'attachment; filename="synthetic-unknown.csv"'}};};
await evaluate("exportAgentOption('option-U','csv')");assert.deepEqual(body,{conversation_id:'conv',expected_turn:2,run_id:'run-1',option_id:'option-U',format:'csv'});assert.match(element('#global-status').textContent,/待确认草案/);assert.equal(evaluate('state.agentActionBusy'),false);assert.equal(evaluate('state.generated.plans[0].section_ids[0]'),'WRONG-WORKSPACE-PLAN');
""")

    def test_delayed_reference_actions_cannot_cross_subject_or_new_conversation(self):
        self.run_js("""
evaluate("state.auth={authenticated:true,identity:{student_id:'A'}};state.agentConversationId='conv-A';state.agentTurnId=1;state.boot={demo:{courses:[]}};state.agentTurns=[{tool_results:[{name:'recommend_my_courses',result:{run_id:'run-A',plans:[{option_id:'option-A',validation:{result:'pass'}}]}}]}]");
let release;context.fetch=()=>new Promise(resolve=>{release=resolve;});const pending=evaluate("saveAgentOption('option-A')");evaluate("clearWorkspace();state.auth={authenticated:true,identity:{student_id:'B'}}");release({ok:true,json:async()=>({saved_id:8,conversation_id:'conv-A',turn_id:1,constraints:{revision:2},versions:{revision:2}})});await pending;assert.equal(evaluate('Object.keys(state.agentOptionReceipts).length'),0);assert.equal(evaluate('state.auth.identity.student_id'),'B');assert.equal(evaluate('state.agentConversationId'),null);
evaluate("state.agentConversationId='conv-B';state.agentTurnId=1;state.boot={demo:{courses:[]}};state.agentTurns=[{tool_results:[{name:'recommend_my_courses',result:{run_id:'run-B',plans:[{option_id:'option-B',validation:{result:'pass'}}]}}]}]");let downloaded=false;context.URL.createObjectURL=()=>{downloaded=true;return 'blob:test';};const exportPending=evaluate("exportAgentOption('option-B','json')");evaluate('clearAgentConversation()');release({ok:true,blob:async()=>({}),headers:{get:()=> null}});await exportPending;assert.equal(downloaded,false);assert.equal(evaluate('state.agentTurns.length'),0);
""")

    def test_explicit_chat_save_receipt_refreshes_workspace_and_preserves_new_turn(self):
        self.run_js("""
evaluate("state.auth={authenticated:true};state.agentStatus={configured:true,remote:false};state.agentConversationId='conv';state.agentTurnId=1;state.agentDraft='保存A';state.boot={versions:{revision:1},demo:{courses:[]}};state.agentTurns=[{tool_results:[{name:'recommend_my_courses',result:{run_id:'run',workspace_versions:{revision:1},plans:[{option_id:'option-A',validation:{result:'pass'}}]}}]}]");
const requests=[];context.fetch=async(path,options)=>{requests.push(path);if(path==='/api/chat/turn')return {ok:true,json:async()=>({conversation_id:'conv',turn_id:2,status:'completed',reply_origin:'verified_explicit_action',reply:'已保存',tool_results:[{name:'act_on_my_option',result:{status:'action_ready',action:'save',label:'A',option_id:'option-A'}}],citations:[],data_kind:'synthetic',saved_action:{saved_id:9,option_id:'option-A',run_id:'run',versions:{revision:2},constraints:{revision:2,requested_course_ids:['DEMO-AI']}}})};if(path==='/api/bootstrap')return {ok:true,json:async()=>({versions:{revision:2},demo:{courses:[],rules:[],constraints:{revision:2,requested_course_ids:['DEMO-AI']}},saved_plans:[{saved_id:9,plan:{}}]})};if(path==='/api/chat/conversations?limit=100')return historyListResponse([{conversation_id:'conv',title:'保存A',turn_id:2}]);throw Error('unexpected '+path);};
await evaluate('sendAgentTurn()');assert.deepEqual(requests,['/api/chat/turn','/api/bootstrap','/api/chat/conversations?limit=100']);assert.equal(evaluate('state.agentConversationId'),'conv');assert.equal(evaluate('state.agentTurnId'),2);assert.equal(evaluate('state.agentOptionReceipts["option-A"].saved_id'),9);assert.equal(evaluate('state.constraints.revision'),2);assert.match(element('#panel-assistant').innerHTML,/已保存为课表 #9/);
const prepared=evaluate("agentToolCard({name:'act_on_my_option',result:{status:'action_ready',action:'save',label:'A',option_id:'option-A'}})");assert.match(prepared,/操作已准备/);assert.match(prepared,/执行尚未完成/);assert.doesNotMatch(prepared,/已保存为课表/);
""")

    def test_explicit_chat_download_decodes_once_without_another_export_request(self):
        self.run_js("""
evaluate("state.auth={authenticated:true};state.agentStatus={configured:true,remote:false};state.agentConversationId='conv';state.agentTurnId=1;state.agentDraft='导出A为JSON';state.boot={demo:{courses:[]}}");
const content=JSON.stringify({data_kind:'synthetic',status:'unknown',note:'合成草案'});const data=Buffer.from(content);let exportedBlob;context.URL.createObjectURL=blob=>{exportedBlob=blob;return 'blob:synthetic';};const requests=[];
context.fetch=async(path)=>{requests.push(path);if(path==='/api/chat/conversations?limit=100')return historyListResponse([{conversation_id:'conv',title:'导出A',turn_id:2}]);assert.equal(path,'/api/chat/turn');return {ok:true,json:async()=>({conversation_id:'conv',turn_id:2,status:'completed',reply_origin:'verified_explicit_action',reply:'已导出',tool_results:[{name:'act_on_my_option',result:{status:'action_ready',action:'export',label:'A',option_id:'option-A'}}],citations:[],data_kind:'synthetic',pending_action:{option_id:'option-A'},download:{data_base64:data.toString('base64'),content_type:'application/json; charset=utf-8',filename:'synthetic-plan.json',bytes:data.length}})};};
await evaluate('sendAgentTurn()');assert.deepEqual(requests,['/api/chat/turn','/api/chat/conversations?limit=100']);assert.equal(await exportedBlob.text(),content);assert.equal(exportedBlob.type,'application/json; charset=utf-8');assert.match(element('#panel-assistant').innerHTML,/已导出 synthetic-plan.json/);assert.match(element('#global-status').textContent,/已下载 synthetic-plan.json/);
""")

    def test_explicit_chat_download_and_saved_receipt_reject_late_or_wrong_conversation_response(self):
        self.run_js("""
evaluate("state.auth={authenticated:true,identity:{student_id:'A'}};state.agentStatus={configured:true,remote:false};state.agentConversationId='conv-A';state.agentTurnId=1;state.agentDraft='保存A';state.boot={demo:{courses:[]}}");
let release;context.fetch=()=>new Promise(resolve=>{release=resolve;});let downloads=0;context.URL.createObjectURL=()=>{downloads+=1;return 'blob:test';};const pending=evaluate('sendAgentTurn()');evaluate("clearWorkspace();state.auth={authenticated:true,identity:{student_id:'B'}}");release({ok:true,json:async()=>({conversation_id:'conv-A',turn_id:2,status:'completed',reply_origin:'verified_explicit_action',reply:'已保存',tool_results:[],citations:[],data_kind:'synthetic',saved_action:{saved_id:12,option_id:'option-A',versions:{revision:2},constraints:{revision:2}},download:{data_base64:'e30=',content_type:'application/json',filename:'test.json',bytes:2}})});await pending;assert.equal(downloads,0);assert.equal(evaluate('Object.keys(state.agentOptionReceipts).length'),0);
evaluate("state.agentStatus={configured:true,remote:false};state.agentConversationId='conv-B';state.agentTurnId=1;state.agentDraft='导出B为JSON';state.boot={demo:{courses:[]}}");context.fetch=async()=>({ok:true,json:async()=>({conversation_id:'another-conversation',turn_id:2,status:'completed',reply_origin:'verified_explicit_action',reply:'已导出',tool_results:[],citations:[],data_kind:'synthetic',download:{data_base64:'e30=',content_type:'application/json',filename:'test.json',bytes:2}})});await evaluate('sendAgentTurn()');assert.equal(downloads,0);assert.equal(evaluate('state.agentConversationId'),'conv-B');assert.equal(evaluate('state.agentTurnId'),1);assert.match(element('#panel-assistant').innerHTML,/对话响应契约不完整/);
""")


    def test_merged_tool_results_use_verified_source_and_keep_secondary_failures_and_unknown(self):
        self.run_js("""
evaluate("state.auth={authenticated:true};state.boot={demo:{courses:[]}};state.agentConversationId='conv'");
context.mergedTurn={message:'推荐课程',status:'completed',reply_origin:'verified_tool_presentation',reply:'主推荐已复核',model_decision:{source_tool_result_index:1},tool_results:[
  {name:'get_my_context',result:{courses:[{course_name:'UNRELATED_CONTEXT',credits:3}]}},
  {name:'recommend_my_courses',result:{run_id:'run-main',plans:[{option_id:'option-main',validation:{result:'pass'},sections:[{course_name:'SELECTED_COURSE',meetings:[]}]}]}},
  {name:'generate_my_plan',result:{plans:[{validation:{result:'pass'},sections:[{course_name:'UNRELATED_LATER_PLAN',meetings:[]}]}]}},
  {name:'validate_my_plan',result:{result:'fail',plan:{validation:{result:'fail',checks:[{result:'fail',reason:'考试与锁定班冲突'}]},sections:[]}}},
  {name:'get_my_context',result:{unknown:['课程资格尚未确认']}},
  {name:'find_my_sections',error:true,result:{message:'查询服务暂时不可用'}},
  {name:'validate_my_plan',result:{result:'pass',plan:{validation:{result:'pass'},sections:[{course_name:'UNRELATED_VALIDATED_PLAN',meetings:[]}]}}}
]};
evaluate('state.agentTurns=[mergedTurn];renderAssistant()');
const markup=element('#panel-assistant').innerHTML;
assert.match(markup,/SELECTED_COURSE/);assert.match(markup,/考试与锁定班冲突/);assert.match(markup,/课程资格尚未确认/);assert.match(markup,/查询服务暂时不可用/);
assert.match(markup,/badge danger">不满足/);assert.match(markup,/badge warning">待确认/);
assert.doesNotMatch(markup,/UNRELATED_CONTEXT|UNRELATED_LATER_PLAN|UNRELATED_VALIDATED_PLAN/);
evaluate("mergedTurn.status='model_error';mergedTurn.reply='模型调用未完成';renderAssistant()");
const partial=element('#panel-assistant').innerHTML;assert.match(partial,/本轮未完成/);assert.match(partial,/UNRELATED_CONTEXT/);assert.match(partial,/UNRELATED_LATER_PLAN/);assert.match(partial,/课程资格尚未确认/);
""")

    def test_saved_list_exports_by_frozen_id_and_late_download_cannot_cross_identity(self):
        self.run_js("""
evaluate("state.auth={authenticated:true,identity:{student_id:'A',security_realm:'synthetic'}};state.csrfToken='memory-only';state.boot={demo:{}};state.saved=[{saved_id:19,plan:{validation:{result:'unknown'},sections:[{course_name:'FROZEN_COURSE',meetings:[]}]}}];state.generated={plans:[{section_ids:['WRONG-CURRENT-PLAN']}]};state.loadedSaved={saved_id:999};renderSaved()");
const markup=element('#panel-saved').innerHTML;assert.match(markup,/FROZEN_COURSE/);assert.match(markup,/badge warning">待确认/);assert.match(markup,/data-action="export-saved" data-saved-id="19"/);
const payloads=[];const content=JSON.stringify({data_kind:'synthetic',result:'unknown',saved_id:19});let downloaded;
context.URL.createObjectURL=blob=>{downloaded=blob;return 'blob:frozen';};
context.fetch=async(path,options)=>{assert.equal(path,'/api/export');assert.equal(options.headers['X-CSRF-Token'],'memory-only');assert.equal(options.credentials,'same-origin');payloads.push(JSON.parse(options.body));return {ok:true,blob:async()=>new Blob([content],{type:'application/json'}),headers:{get:()=> 'attachment; filename="synthetic-frozen-unknown.json"'}};};
await evaluate("exportSaved('19','json')");assert.deepEqual(payloads,[{saved_id:19,format:'json'}]);assert.equal(await downloaded.text(),content);assert.equal(evaluate('state.agentActionBusy'),false);assert.equal(evaluate('state.loadedSaved.saved_id'),999);
await evaluate("exportSaved(20,'json')");assert.equal(payloads.length,1);
let release;let extraDownloads=0;context.fetch=()=>new Promise(resolve=>{release=resolve;});context.URL.createObjectURL=()=>{extraDownloads+=1;return 'blob:late';};
const pending=evaluate("exportSaved(19,'csv')");evaluate("clearWorkspace();state.auth={authenticated:true,identity:{student_id:'B',security_realm:'synthetic'}}");release({ok:true,blob:async()=>new Blob(['late A data']),headers:{get:()=>null}});await pending;
assert.equal(extraDownloads,0);assert.equal(evaluate('state.auth.identity.student_id'),'B');assert.equal(evaluate('state.saved.length'),0);assert.equal(evaluate('state.agentActionBusy'),false);
evaluate("state.saved=[{saved_id:23,plan:{validation:{result:'pass'}}}]");const late401=evaluate("exportSaved(23,'json')");evaluate("clearWorkspace();state.auth={authenticated:true,identity:{student_id:'C',security_realm:'synthetic'}}");release({ok:false,status:401,text:async()=>JSON.stringify({error:'old session expired'})});await late401;
assert.equal(evaluate('state.auth.authenticated'),true);assert.equal(evaluate('state.auth.identity.student_id'),'C');assert.equal(extraDownloads,0);
""")


    def test_history_restores_receipts_without_replaying_actions_and_continues_at_saved_turn(self):
        self.run_js("""
evaluate("state.auth={authenticated:true,identity:{student_id:'A'}};state.csrfToken='memory-only';state.boot={versions:{revision:2},demo:{courses:[]}};state.saved=[{saved_id:7}];state.agentStatus={configured:true,provider:'DeepSeek',remote:true};state.agentConsent=true;state.agentHistory=[{conversation_id:'history-conv',title:'历史课程推荐'}]");
const turns=[
  {message:'推荐课程',status:'completed',tool_results:[{name:'recommend_my_courses',result:{run_id:'history-run',workspace_versions:{revision:1},plans:[{option_id:'option-A',validation:{result:'pass'}},{option_id:'option-M',validation:{result:'pass'}}]}}],citations:[]},
  {message:'保存 A',status:'completed',reply_origin:'verified_explicit_action',tool_results:[{name:'act_on_my_option',result:{status:'action_ready',action:'save',option_id:'option-A'}}],citations:[],saved_action:{saved_id:9,option_id:'option-A',versions:{revision:2},constraints:{revision:2}}},
  {message:'导出 A',status:'completed',reply_origin:'verified_explicit_action',tool_results:[{name:'act_on_my_option',result:{status:'action_ready',action:'export',option_id:'option-A'}}],citations:[],pending_action:{option_id:'option-A'},download:{data_base64:'e30=',content_type:'application/json',filename:'past-plan.json',bytes:2}}
];
const requests=[];let downloads=0;context.URL.createObjectURL=()=>{downloads+=1;return 'blob:unexpected';};
context.fetch=async(path,options)=>{const body=options.body?JSON.parse(options.body):undefined;requests.push({path,body});if(path==='/api/chat/history'){assert.equal(options.headers['X-CSRF-Token'],'memory-only');assert.deepEqual(body,{conversation_id:'history-conv'});return {ok:true,json:async()=>({conversation_id:'history-conv',turn_id:3,turns,data_kind:'synthetic',in_progress:false,option_receipts:{'option-M':{saved_id:7,option_id:'option-M',versions:{revision:2},constraints:{revision:2}}}})};}if(path==='/api/chat/turn'){assert.equal(body.conversation_id,'history-conv');assert.equal(body.expected_turn,3);assert.equal(body.consent_remote,true);return {ok:true,json:async()=>({conversation_id:'history-conv',turn_id:4,status:'completed',reply:'比较已完成',tool_results:[],citations:[],data_kind:'synthetic'})};}if(path==='/api/chat/conversations?limit=100')return historyListResponse([{conversation_id:'history-conv',title:'历史课程推荐',turn_id:4}]);throw Error('history must not replay '+path);};
await evaluate("loadAgentConversation('history-conv')");
assert.deepEqual(requests.map(item=>item.path),['/api/chat/history']);assert.equal(downloads,0);assert.equal(evaluate('state.agentConversationId'),'history-conv');assert.equal(evaluate('state.agentTurnId'),3);assert.equal(evaluate('state.agentTurns.length'),3);assert.equal(evaluate('state.agentConsent'),false);assert.equal(evaluate('state.saved.length'),1);
assert.equal(evaluate("state.agentOptionReceipts['option-A'].saved_id"),9);assert.equal(evaluate("state.agentOptionReceipts['option-M'].saved_id"),7);assert.equal(evaluate("agentOptionState(findAgentOption('option-M').plan,findAgentOption('option-M').result).enabled"),true);
await evaluate("saveAgentOption('option-M')");assert.equal(requests.length,1);assert.match(element('#panel-assistant').innerHTML,/已保存/);
evaluate("state.agentDraft='比较 A 和 B'");assert.equal(evaluate('agentCanSend()'),false);await evaluate('sendAgentTurn()');assert.equal(requests.length,1);
evaluate('state.agentConsent=true');await evaluate('sendAgentTurn()');assert.deepEqual(requests.map(item=>item.path),['/api/chat/history','/api/chat/turn','/api/chat/conversations?limit=100']);assert.equal(evaluate('state.agentTurnId'),4);assert.equal(downloads,0);
""")

    def test_new_conversation_keeps_history_but_clears_turns_receipts_and_remote_consent(self):
        self.run_js("""
evaluate("state.auth={authenticated:true};state.boot={demo:{courses:[]}};state.agentStatus={configured:true,remote:true};state.agentHistory=[{conversation_id:'prior-conv',title:'以前的选课需求'}];state.agentTurns=[{message:'原问题'}];state.agentConversationId='prior-conv';state.agentTurnId=5;state.agentDraft='未发送草稿';state.agentConsent=true;state.agentRetry={request_id:'old-request'};state.agentActionRetries={old:'old-action'};state.agentOptionReceipts={old:{saved_id:3}};renderAgentHistory()");
const previousEpoch=evaluate('state.agentEpoch');const button={disabled:false,dataset:{action:'new-agent-conversation'}};
await element('.app-shell').listeners.click({target:{closest:()=>button}});
assert.equal(evaluate('state.agentEpoch'),previousEpoch+1);assert.equal(evaluate('state.agentTurns.length'),0);assert.equal(evaluate('state.agentConversationId'),null);assert.equal(evaluate('state.agentTurnId'),0);assert.equal(evaluate('state.agentDraft'),'');assert.equal(evaluate('state.agentConsent'),false);assert.equal(evaluate('state.agentRetry'),null);assert.equal(evaluate('Object.keys(state.agentActionRetries).length'),0);assert.equal(evaluate('Object.keys(state.agentOptionReceipts).length'),0);
assert.equal(evaluate('state.agentHistory.length'),1);assert.match(element('#conversation-list').innerHTML,/以前的选课需求/);assert.doesNotMatch(element('#conversation-list').innerHTML,/aria-current="true"/);
evaluate("state.agentDraft='新的合成需求'");assert.equal(evaluate('agentCanSend()'),false);
""")

    def test_late_history_load_and_list_success_or_401_do_not_cross_subject(self):
        self.run_js("""
for(const operation of ['load','list'])for(const responseKind of ['success','401']){
  evaluate("clearWorkspace();state.auth={authenticated:true,identity:{student_id:'A',security_realm:'A'}};state.boot={identity:{student_id:'A',security_realm:'A'},demo:{courses:[]}};state.agentHistory=[{conversation_id:'conv-A',title:'A private title'}]");
  let release;context.fetch=()=>new Promise(resolve=>{release=resolve;});
  const pending=evaluate(operation==='load'?"loadAgentConversation('conv-A')":'refreshAgentHistory()');
  evaluate("clearWorkspace();state.auth={authenticated:true,identity:{student_id:'B',security_realm:'B'}};state.boot={identity:{student_id:'B',security_realm:'B'},demo:{courses:[]}};state.agentHistory=[{conversation_id:'conv-B',title:'B current title'}];state.agentTurns=[{message:'B current message',status:'completed'}];state.agentConversationId='conv-B';state.agentTurnId=2;state.agentOptionReceipts={'B-option':{saved_id:11}};renderAuth();renderAgentHistory();message('B current status')");
  if(responseKind==='401')release({ok:false,status:401,text:async()=>JSON.stringify({error:'A old session expired'})});
  else if(operation==='load')release({ok:true,json:async()=>({conversation_id:'conv-A',turn_id:6,turns:[{message:'A private message'}],option_receipts:{'A-option':{saved_id:9}},data_kind:'synthetic'})});
  else release(historyListResponse([{conversation_id:'conv-A',title:'A private title',turn_id:6}]));
  await pending;
  assert.equal(evaluate('state.auth.authenticated'),true);assert.equal(evaluate('state.auth.identity.student_id'),'B');assert.equal(evaluate('state.auth.identity.security_realm'),'B');assert.equal(evaluate('state.agentConversationId'),'conv-B');assert.equal(evaluate('state.agentTurnId'),2);assert.equal(evaluate('state.agentTurns[0].message'),'B current message');assert.equal(evaluate('state.agentHistory[0].conversation_id'),'conv-B');assert.equal(evaluate('state.agentOptionReceipts["B-option"].saved_id'),11);assert.equal(evaluate('state.agentHistoryLoadBusy'),false);assert.equal(evaluate('state.agentHistoryLoading'),false);
  assert.match(element('#conversation-list').innerHTML,/B current title/);assert.doesNotMatch(element('#conversation-list').innerHTML,/A private/);assert.equal(element('#global-status').textContent,'B current status');assert.equal(element('#conversation-sidebar').hidden,false);
}
""")

    def test_history_in_progress_or_pending_action_blocks_send_until_reloaded(self):
        self.run_js("""
for(const pendingState of [{in_progress:true},{action_pending:true}]){
  evaluate("clearWorkspace();state.auth={authenticated:true};state.boot={demo:{courses:[]}};state.agentStatus={configured:true,remote:false}");
  const paths=[];let completed=false;
  context.fetch=async(path,options)=>{paths.push(path);assert.equal(path,'/api/chat/history');assert.deepEqual(JSON.parse(options.body),{conversation_id:'processing-conv'});return {ok:true,json:async()=>({conversation_id:'processing-conv',turn_id:completed?7:6,turns:[{message:'正在处理的需求',status:completed?'completed':'failed',tool_results:[],citations:[]}],data_kind:'synthetic',...(!completed?pendingState:{in_progress:false,action_pending:false})})};};
  await evaluate("loadAgentConversation('processing-conv')");evaluate("state.agentDraft='继续询问'");assert.equal(evaluate('state.agentHistoryPending'),true);assert.equal(evaluate('agentCanSend()'),false);assert.match(element('#panel-assistant').innerHTML,/重新加载/);await evaluate('sendAgentTurn()');assert.deepEqual(paths,['/api/chat/history']);
  completed=true;await evaluate("loadAgentConversation('processing-conv')");assert.equal(evaluate('state.agentHistoryPending'),false);assert.equal(evaluate('state.agentTurnId'),7);evaluate("state.agentDraft='继续询问'");assert.equal(evaluate('agentCanSend()'),true);assert.deepEqual(paths,['/api/chat/history','/api/chat/history']);
}
""")

    def test_history_load_rejects_mismatched_or_malformed_response_contract(self):
        self.run_js("""
for(const malformed of [{conversation_id:'wrong-conv'},{turn_id:'2'},{turn_id:-1},{turns:{}},{data_kind:'real'}]){
  evaluate("clearWorkspace();state.auth={authenticated:true};state.boot={demo:{courses:[]}};state.agentStatus={configured:true,remote:true};state.agentConsent=true");
  context.fetch=async()=>({ok:true,json:async()=>({conversation_id:'requested-conv',turn_id:2,turns:[],data_kind:'synthetic',option_receipts:{bad:{saved_id:99}},...malformed})});
  await evaluate("loadAgentConversation('requested-conv')");assert.equal(evaluate('state.agentConversationId'),null);assert.equal(evaluate('state.agentTurnId'),0);assert.equal(evaluate('state.agentTurns.length'),0);assert.equal(evaluate('Object.keys(state.agentOptionReceipts).length'),0);assert.equal(evaluate('state.agentConsent'),false);assert.equal(evaluate('state.agentHistoryLoadBusy'),false);assert.match(element('#global-status').textContent,/历史对话响应不完整/);
}
""")


    def test_school_login_is_explicitly_enabled_and_password_never_persists(self):
        self.run_js("""
evaluate("state.auth={authenticated:false,school_client_enabled:true,mock_enabled:false};renderAuth()");
assert.match(element('#auth-panel').innerHTML,/type="password"/);assert.match(element('#auth-panel').innerHTML,/autocomplete="off"/);
element('#school-login-username').value='fixture-user';element('#school-login-password').value='fixture-secret';
let release,body;context.fetch=(path,options)=>{assert.equal(path,'/api/auth/school-login');body=JSON.parse(options.body);assert.equal(element('#school-login-password').value,'');return new Promise(resolve=>{release=resolve;});};
const pending=evaluate('schoolLogin()');assert.deepEqual(body,{username:'fixture-user',password:'fixture-secret'});
release({ok:false,status:401,text:async()=>JSON.stringify({code:'login_failed',error:'fixture-secret must not be echoed'})});await pending;
assert.equal(element('#school-login-password').value,'');assert.doesNotMatch(element('#global-status').textContent,/fixture-secret/);assert.doesNotMatch(evaluate('JSON.stringify(state)'),/fixture-secret/);
assert.doesNotMatch(fs.readFileSync(process.argv[1],'utf8'),/localStorage|sessionStorage/);
""")

    def test_school_bootstrap_preserves_primary_entry_without_synthetic_or_model_calls(self):
        self.run_js("""
const identity={student_id:'SCHOOL-FIXTURE',auth_mode:'school_client',security_realm:'personal_school_client',data_kind:'personal_source'};
const paths=[];context.fetch=async(path)=>{paths.push(path);if(path==='/api/auth/session')return {ok:true,json:async()=>({authenticated:true,school_client_enabled:true,identity,csrf_token:'memory-only'})};if(path==='/api/bootstrap')return {ok:true,json:async()=>({identity,school:{status:'ready'},chat:{configured:false,reason:'real_recommendation_not_ready'}})};throw Error('unexpected '+path);};
await evaluate('bootstrap()');assert.deepEqual(paths,['/api/auth/session','/api/bootstrap']);assert.equal(evaluate('state.tab'),'assistant');assert.equal(element('#tab-school').hidden,false);assert.equal(element('#tab-saved').hidden,true);assert.equal(element('#conversation-sidebar').hidden,true);assert.equal(element('#mode-badge').textContent,'本人教务');assert.match(element('#panel-assistant').innerHTML,/真实推荐待接入/);
evaluate("state.agentStatus={configured:true,remote:true};state.agentDraft='private fixture';state.agentConsent=true");assert.equal(evaluate('agentCanSend()'),false);await evaluate('sendAgentTurn()');await evaluate('refreshAgentStatus()');await evaluate('refreshAgentHistory()');await evaluate('refreshSaved()');assert.equal(paths.length,2);
evaluate("switchTab('saved')");assert.equal(evaluate('state.tab'),'assistant');
""")

    def test_school_login_captcha_retry_requires_fresh_credentials_and_clears_image(self):
        self.run_js("""
evaluate("state.auth={authenticated:false,school_client_enabled:true};renderAuth()");let created=0;const revoked=[];context.URL.createObjectURL=()=>{created++;return 'blob:login-'+created;};context.URL.revokeObjectURL=url=>revoked.push(url);
const bodies=[];context.fetch=async(path,options)=>{bodies.push(JSON.parse(options.body));return {ok:true,json:async()=>({authenticated:false,state:'captcha_required',challenge_id:'challenge-fixture',captcha:{content_type:'image/png',data_base64:Buffer.from('image fixture').toString('base64')}})};};context.Buffer=Buffer;
element('#school-login-username').value='fixture-user';element('#school-login-password').value='fixture-password';await evaluate('schoolLogin()');assert.equal(evaluate('state.schoolChallengeId'),'challenge-fixture');assert.match(element('#auth-panel').innerHTML,/school-login-captcha/);assert.equal(element('#school-login-password').value,'');assert.equal(created,1);assert.doesNotMatch(evaluate('JSON.stringify(state)'),/fixture-password/);
element('#school-login-password').value='fresh-password';element('#school-login-captcha').value='A123';context.fetch=async(path,options)=>{bodies.push(JSON.parse(options.body));return {ok:false,status:403,text:async()=>JSON.stringify({code:'login_failed',error:'secret diagnostic should not display'})};};await evaluate('schoolLogin()');assert.deepEqual(bodies[1],{username:'fixture-user',password:'fresh-password',challenge_id:'challenge-fixture',captcha_text:'A123'});assert.equal(element('#school-login-captcha').value,'');assert.equal(evaluate('state.schoolChallengeId'),null);assert.deepEqual(revoked,['blob:login-1']);assert.doesNotMatch(element('#global-status').textContent,/secret diagnostic/);
""")

    def test_school_query_preserves_source_values_and_rejects_ambiguous_exam_term(self):
        self.run_js("""
evaluate("state.auth={authenticated:true,identity:{student_id:'SCHOOL-FIXTURE',auth_mode:'school_client',security_realm:'personal_school_client'}};state.csrfToken='memory-only';state.schoolOperation='academic'");
element('#school-operation').value='academic';element('#school-year').value='2026-2027';element('#school-season').value='AW';
let sent;context.fetch=async(path,options)=>{assert.equal(path,'/api/school/sync');assert.equal(options.headers['X-CSRF-Token'],'memory-only');sent=JSON.parse(options.body);return {ok:true,json:async()=>({operation:'academic',status:'partial',data_source:'live',coverage:'bounded',records:[{course_name_raw:'<unsafe>',grade_raw:'免修',nominal_credits:'2.5',earned_credits:null,completion_status:'unknown'}],issues:[{code:'unknown'}]})};};
await evaluate('schoolSync()');assert.deepEqual(sent,{operation:'academic',target_term:'2026-2027-AW',force_refresh:true});const markup=element('#panel-school').innerHTML;assert.match(markup,/&lt;unsafe&gt;/);assert.match(markup,/免修/);assert.match(markup,/来源学分/);assert.match(markup,/部分读取/);assert.doesNotMatch(markup,/<unsafe>/);
element('#school-operation').value='exams';element('#school-year').value='2026-2027';element('#school-season').value='AW';sent=null;await evaluate('schoolSync()');assert.equal(sent,null);assert.match(element('#global-status').textContent,/请选择单独分学期/);
""")

    def test_late_school_query_or_401_cannot_publish_or_logout_another_owner(self):
        self.run_js("""
evaluate("state.auth={authenticated:true,identity:{student_id:'SCHOOL-A',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={};state.csrfToken='A-memory'");
element('#school-operation').value='academic';element('#school-year').value='2026-2027';element('#school-season').value='AW';let release;context.fetch=()=>new Promise(resolve=>{release=resolve;});
const pending=evaluate('schoolSync()');evaluate("clearWorkspace();state.auth={authenticated:true,identity:{student_id:'SCHOOL-B',auth_mode:'school_client',security_realm:'personal_school_client'}}");release({ok:true,json:async()=>({status:'ok',records:[{course_name_raw:'A-private-data'}]})});await pending;assert.equal(evaluate('state.schoolResult'),null);assert.doesNotMatch(element('#panel-school').innerHTML,/A-private-data/);
element('#school-operation').value='academic';element('#school-year').value='2026-2027';element('#school-season').value='AW';const expired=evaluate('schoolSync()');evaluate("clearWorkspace();state.auth={authenticated:true,identity:{student_id:'SCHOOL-C',auth_mode:'school_client',security_realm:'personal_school_client'}}");release({ok:false,status:401,text:async()=>JSON.stringify({error:'old app session'})});await expired;assert.equal(evaluate('state.auth.authenticated'),true);assert.equal(evaluate('state.auth.identity.student_id'),'SCHOOL-C');
""")

    def test_major_grade_query_uses_all_history_and_disables_term_filters(self):
        self.run_js("""
evaluate("state.auth={authenticated:true,identity:{student_id:'SCHOOL-FIXTURE',auth_mode:'school_client',security_realm:'personal_school_client'}};state.csrfToken='memory-only';state.schoolOperation='major_academic';state.schoolYear='2026-2027';state.schoolSeason='AW';renderSchool()");
const form=element('#panel-school').innerHTML;assert.match(form,/主修成绩查询全部修读记录，不按学期筛选/);assert.match(form,/<input id="school-year"[^>]*disabled/);assert.match(form,/<select id="school-season"[^>]*disabled/);
element('#school-operation').value='major_academic';element('#school-year').value='2026-2027';element('#school-season').value='AW';let sent;
context.fetch=async(path,options)=>{assert.equal(path,'/api/school/sync');sent=JSON.parse(options.body);return {ok:true,json:async()=>({operation:'major_academic',target_term:'ALL-HISTORY',status:'complete_within_scope',data_source:'live',coverage:'all_history',records:[{course_name_raw:'HISTORY-FIXTURE',academic_year_raw:'2023-2024',grade_raw:'通过'}]})};};
await evaluate('schoolSync()');assert.deepEqual(sent,{operation:'major_academic',target_term:'ALL-HISTORY',force_refresh:true});assert.match(element('#panel-school').innerHTML,/HISTORY-FIXTURE/);assert.equal(evaluate('state.schoolResult.records[0].academic_year_raw'),'2023-2024');assert.equal(evaluate('state.schoolYear'),'2026-2027');
element('#school-operation').value='major_academic';element('#school-year').value='';element('#school-season').value='AW';sent=null;await evaluate('schoolSync()');assert.equal(sent.target_term,'ALL-HISTORY');
""")

    def test_school_expiry_keeps_app_identity_and_prompts_same_owner_reauthentication(self):
        self.run_js("""
evaluate("state.auth={authenticated:true,identity:{student_id:'SCHOOL-A',auth_mode:'school_client',security_realm:'personal_school_client'}};state.csrfToken='memory-only'");element('#school-operation').value='academic';element('#school-year').value='2026-2027';element('#school-season').value='AW';
context.fetch=async()=>({ok:false,status:409,text:async()=>JSON.stringify({code:'source_session_expired',error:'source expired'})});await evaluate('schoolSync()');assert.equal(evaluate('state.auth.authenticated'),true);assert.equal(evaluate('state.schoolReauth'),true);assert.match(element('#panel-school').innerHTML,/school-reauth-password/);assert.equal(evaluate('state.schoolResult'),null);
""")

    def test_school_captcha_blob_is_revoked_and_cannot_cross_owner(self):
        self.run_js("""
evaluate("state.auth={authenticated:true,identity:{student_id:'SCHOOL-A',auth_mode:'school_client',security_realm:'personal_school_client'}}");const revoked=[];context.URL.revokeObjectURL=url=>revoked.push(url);let created=0;context.URL.createObjectURL=()=>{created++;return 'blob:school-'+created;};
context.fetch=async()=>({ok:true,blob:async()=>new Blob(['fixture'],{type:'image/png'})});await evaluate('loadSchoolCaptcha()');await evaluate('loadSchoolCaptcha()');assert.deepEqual(revoked,['blob:school-1']);assert.equal(evaluate('state.schoolCaptchaUrl'),'blob:school-2');evaluate('clearWorkspace()');assert.deepEqual(revoked,['blob:school-1','blob:school-2']);assert.equal(evaluate('state.schoolCaptchaUrl'),null);
evaluate("state.auth={authenticated:true,identity:{student_id:'SCHOOL-B',auth_mode:'school_client',security_realm:'personal_school_client'}}");let release;context.fetch=()=>new Promise(resolve=>{release=resolve;});const late=evaluate('loadSchoolCaptcha()');evaluate("clearWorkspace();state.auth={authenticated:true,identity:{student_id:'SCHOOL-C',auth_mode:'school_client',security_realm:'personal_school_client'}}");release({ok:true,blob:async()=>new Blob(['old image'],{type:'image/png'})});await late;assert.equal(created,2);assert.equal(evaluate('state.schoolCaptchaUrl'),null);
""")

    def test_school_plan_download_uses_owner_alias_and_late_response_is_discarded(self):
        self.run_js("""
evaluate("state.auth={authenticated:true,identity:{student_id:'SCHOOL-A',auth_mode:'school_client',security_realm:'personal_school_client'}};state.csrfToken='memory-only';state.schoolPlan={status:'acquired_pending_review',source_alias:'PLAN-FIXTURE'}");let release,body,downloads=0;context.URL.createObjectURL=()=>{downloads++;return 'blob:plan';};context.fetch=(path,options)=>{assert.equal(path,'/api/school/plan-document');assert.equal(options.headers['X-CSRF-Token'],'memory-only');body=JSON.parse(options.body);return new Promise(resolve=>{release=resolve;});};
await evaluate("downloadSchoolPlan('OTHER-ALIAS')");assert.equal(body,undefined);const pending=evaluate("downloadSchoolPlan('PLAN-FIXTURE')");assert.deepEqual(body,{source_alias:'PLAN-FIXTURE'});evaluate("clearWorkspace();state.auth={authenticated:true,identity:{student_id:'SCHOOL-B',auth_mode:'school_client',security_realm:'personal_school_client'}}");release({ok:true,blob:async()=>new Blob(['fixture pdf']),headers:{get:()=>null}});await pending;assert.equal(downloads,0);assert.equal(evaluate('state.schoolPlan'),null);
""")

    def test_school_captcha_accepts_gif_for_login_and_queries_but_rejects_svg(self):
        self.run_js("""
const gif=Buffer.from('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7','base64');const blobs=[];const revoked=[];context.URL.createObjectURL=blob=>{blobs.push(blob);return 'blob:captcha-'+blobs.length;};context.URL.revokeObjectURL=url=>revoked.push(url);context.loginChallenge={challenge_id:'gif-login-fixture',captcha:{content_type:'image/gif',data_base64:gif.toString('base64')}};
evaluate('acceptSchoolLoginChallenge(loginChallenge)');assert.equal(evaluate('state.schoolChallengeId'),'gif-login-fixture');assert.equal(blobs[0].type,'image/gif');evaluate('clearWorkspace()');assert.deepEqual(revoked,['blob:captcha-1']);
evaluate("state.auth={authenticated:true,identity:{student_id:'SCHOOL-FIXTURE',auth_mode:'school_client',security_realm:'personal_school_client'}}");context.fetch=async()=>({ok:true,blob:async()=>new Blob([gif],{type:'image/gif'})});await evaluate('loadSchoolCaptcha()');assert.equal(blobs[1].type,'image/gif');assert.equal(evaluate('state.schoolNeedsCaptcha'),true);
context.fetch=async()=>({ok:true,blob:async()=>new Blob(['<svg/>'],{type:'image/svg+xml'})});await evaluate('loadSchoolCaptcha()');assert.equal(blobs.length,2);assert.match(element('#global-status').textContent,/验证码图片读取失败/);
context.loginChallenge={challenge_id:'svg-fixture',captcha:{content_type:'image/svg+xml',data_base64:Buffer.from('<svg/>').toString('base64')}};evaluate('acceptSchoolLoginChallenge(loginChallenge)');assert.equal(evaluate('state.schoolChallengeId'),null);assert.equal(evaluate('state.schoolCaptchaUrl'),null);assert.equal(blobs.length,2);
""")

    def test_school_result_provenance_uses_scoped_counts_readable_time_and_safe_unknowns(self):
        self.run_js("""
assert.equal(evaluate('schoolCoverageText({coverage:{captured_rows:1,reported_total:1,complete_within_scope:true,has_more:false}})'), '本次读取 1 条 · 已完整读取当前查询范围');
assert.equal(evaluate('schoolCoverageText({coverage:{captured_rows:20,reported_total:35,complete_within_scope:false,has_more:true}})'), '本次读取 20 条 · 还有未读取内容');
assert.equal(evaluate('schoolCoverageText({coverage:{captured_rows:0,reported_total:null,complete_within_scope:false,has_more:null}})'), '本次读取 0 条 · 完整性待确认');
assert.equal(evaluate('schoolCoverageText({coverage:{captured_rows:1,reported_total:2,complete_within_scope:true,has_more:false}})'), '本次读取 1 条 · 完整性待确认');
assert.equal(evaluate('schoolCoverageText({coverage:"unexpected-source-value"})'),'读取条数待确认 · 完整性待确认');
assert.equal(evaluate('schoolCaptureTime(1791952661)'),evaluate("schoolCaptureTime('2026-10-14T04:37:41Z')"));assert.match(evaluate('schoolCaptureTime(1791952661)'), /2026.*10.*14.*12:37:41/);
for(const value of [null,undefined,'not-a-date','',Infinity,{}]){context.captureValue=value;assert.equal(evaluate('schoolCaptureTime(captureValue)'),'待确认');}
evaluate("state.auth={authenticated:true,identity:{student_id:'SCHOOL-FIXTURE',auth_mode:'school_client',security_realm:'personal_school_client'}};state.schoolResult={operation:'academic',status:'complete_within_scope',data_source:'live',captured_at:1791952661,coverage:{captured_rows:1,reported_total:1,pages_captured:1,complete_within_scope:true,has_more:false},records:[]};renderSchool()");const markup=element('#panel-school').innerHTML;assert.match(markup,/已完整读取当前查询范围/);assert.match(markup,/读取时间：2026/);assert.doesNotMatch(markup,/captured_rows|reported_total|1791952661|全校|所有课程已读取/);
""")

    def test_prelogin_badge_matches_enabled_school_and_mock_entrypoints(self):
        self.run_js("""
evaluate("state.auth={authenticated:false,school_client_enabled:true,mock_enabled:false};clearWorkspace();renderAuth()");assert.equal(element('#mode-badge').textContent,'本人教务');assert.equal(element('#mode-badge').className,'badge');assert.match(element('#auth-panel').innerHTML,/登录教务账号/);
evaluate("state.auth={authenticated:false,school_client_enabled:false,mock_enabled:true,mock_profiles:[]};clearWorkspace();renderAuth()");assert.equal(element('#mode-badge').textContent,'合成演示');assert.equal(element('#mode-badge').className,'badge synthetic');
evaluate("state.auth={authenticated:false,school_client_enabled:true,mock_enabled:true,mock_profiles:[]};renderAuth()");assert.equal(element('#mode-badge').textContent,'登录入口');assert.equal(element('#mode-badge').className,'badge');
""")

    def test_plan_missing_profile_fields_have_specific_product_labels(self):
        self.run_js("""
evaluate("state.auth={authenticated:true,identity:{student_id:'SCHOOL-FIXTURE',auth_mode:'school_client',security_realm:'personal_school_client'}};state.schoolPlan={status:'acquired_pending_review',source_alias:'PLAN-FIXTURE',missing_fields:['major_or_category_raw','program','study_path_raw','college_name','major_name']};renderSchool()");
const markup=element('#panel-school').innerHTML;assert.match(markup,/适用范围信息缺失：类（专业）、培养项目、修读路径、学院、专业/);assert.doesNotMatch(markup,/待确认字段|major_or_category_raw|study_path_raw|college_name|major_name/);assert.match(markup,/PDF 获取成功后仍需核验适用范围及培养要求/);
""")

    def test_school_login_protocol_failures_show_specific_safe_messages(self):
        self.run_js("""
evaluate("state.auth={authenticated:false,school_client_enabled:true};renderAuth()");
const labels={invalid_credentials:'学校未接受登录凭据',schema_changed:'学校响应格式尚未适配',identity_not_observed:'学校已返回资料但未确认本人身份',route_not_confirmed:'学校接口尚未确认',source_unavailable:'学校连接暂不可用',permission_denied:'学校拒绝本次访问',source_subject_changed:'学校返回的本人身份已变化',source_epoch_changed:'学校会话已变化',source_generation_changed:'学校会话已变化'};
for(const [code,label] of Object.entries(labels)){element('#school-login-username').value='fixture-user';element('#school-login-password').value='fixture-secret';context.fetch=async()=>({ok:true,json:async()=>({authenticated:false,state:code,message:'fixture-secret private diagnostic'})});await evaluate('schoolLogin()');assert.ok(element('#global-status').textContent.startsWith(label));assert.doesNotMatch(element('#global-status').textContent,/fixture-secret|private diagnostic|未完成或待确认/);assert.equal(element('#school-login-password').value,'');}
element('#school-login-password').value='fixture-secret';context.fetch=async()=>({ok:false,status:502,text:async()=>JSON.stringify({code:'schema_changed',error:'fixture-secret raw response'})});await evaluate('schoolLogin()');assert.match(element('#global-status').textContent,/学校响应格式尚未适配/);assert.doesNotMatch(element('#global-status').textContent,/fixture-secret|raw response/);assert.equal(evaluate("schoolStatusText('__proto__')"),'未完成或待确认');
""")

    def test_partial_timetable_and_ambiguous_plan_have_truthful_status_labels(self):
        self.run_js("""
evaluate("state.auth={authenticated:true,identity:{student_id:'SCHOOL-FIXTURE',auth_mode:'school_client',security_realm:'personal_school_client'}};state.schoolResult={operation:'timetable',status:'partial_acquisition',data_source:'live',coverage:{captured_rows:3,reported_total:null,complete_within_scope:false,has_more:null},records:[{course_name_raw:'FIXTURE-1'},{course_name_raw:'FIXTURE-2'},{course_name_raw:'FIXTURE-3'}]};state.schoolPlan={status:'ambiguous_plan',missing_fields:[]};renderSchool()");
const markup=element('#panel-school').innerHTML;assert.match(markup,/我的课表 · 已读取，覆盖范围待确认/);assert.match(markup,/本次读取 3 条 · 完整性待确认/);assert.match(markup,/方案匹配不唯一/);assert.doesNotMatch(markup,/未完成或待确认|已完整读取当前查询范围/);
""")


if __name__ == "__main__":
    unittest.main()
