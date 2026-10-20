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
const element=selector=>{if(!elements.has(selector))elements.set(selector,{innerHTML:'',hidden:false,value:'',textContent:'',className:'',listeners:{},attributes:{},addEventListener(type,callback){this.listeners[type]=callback;},focus(){},setAttribute(name,value){this.attributes[name]=String(value);},getAttribute(name){return this.attributes[name]??null;},classList:{toggle(){}}});return elements.get(selector);};
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
assert.ok(markup.includes('<strong>浙大教务助手</strong>'));assert.ok(markup.includes('<strong>待澄清</strong>'));assert.ok(markup.includes('<strong>本轮未完成</strong>'));assert.match(markup,/本轮已完成的工具结果如下/);assert.match(markup,/模型后续调用失败/);
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
  evaluate("clearWorkspace();state.auth={authenticated:true};state.boot={demo:{courses:[]}};state.agentStatus={configured:true,remote:true};state.agentConsent=true;state.agentConversationId='current-conv';state.agentTurnId=1;state.agentTurns=[{message:'当前问题',status:'completed',reply:'当前答案'}];state.agentDraft='未发送追问';state.agentOptionReceipts={current:{saved_id:3}}");
  context.fetch=async()=>({ok:true,json:async()=>({conversation_id:'requested-conv',turn_id:2,turns:[],data_kind:'synthetic',option_receipts:{bad:{saved_id:99}},...malformed})});
  await evaluate("loadAgentConversation('requested-conv')");assert.equal(evaluate('state.agentConversationId'),'current-conv');assert.equal(evaluate('state.agentTurnId'),1);assert.equal(evaluate('state.agentTurns[0].reply'),'当前答案');assert.equal(evaluate('state.agentDraft'),'未发送追问');assert.equal(evaluate('state.agentOptionReceipts.current.saved_id'),3);assert.equal(evaluate('state.agentOptionReceipts.bad'),undefined);assert.equal(evaluate('state.agentConsent'),true);assert.equal(evaluate('state.agentHistoryLoadBusy'),false);assert.match(element('#global-status').textContent,/历史对话响应不完整/);
}
""")

    def test_failed_history_switch_keeps_current_conversation_draft_and_reading_state(self):
        self.run_js(r"""
for(const failure of ['network','service','401']){
 evaluate("clearWorkspace();state.auth={authenticated:true,identity:{student_id:'PERSONAL-A',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={};state.agentStatus={configured:true,available:true,remote:true,data_kind:'personal_source',personal_data_use:{version:'personal-retrieval-v1'}};state.agentConversationId='CURRENT';state.agentTurnId=2;state.agentTurns=[{request_id:'CURRENT-REQUEST',status:'completed',message:'当前问题',reply:'当前答案'}];state.agentDraft='尚未发送的追问';state.agentRetry={request_id:'RETRY-CURRENT'};state.agentProcessOpen={'CURRENT-REQUEST':true};state.agentEvidenceOpen={'CURRENT-REQUEST':true};state.agentReasoningScroll={'CURRENT-REQUEST':{top:80,nearBottom:false}};renderAssistant()");
 const scroll=element('#agent-dialogue-scroll');scroll.scrollHeight=1600;scroll.clientHeight=400;scroll.scrollTop=300;evaluate('captureAgentScroll()');
 let release;const paths=[];context.fetch=path=>{paths.push(path);return new Promise((resolve,reject)=>{release=failure==='network'?()=>reject(Error('offline')):()=>resolve({ok:false,status:failure==='401'?401:503,text:async()=>JSON.stringify({error:'暂未载入历史'})});});};
 const beforeEpoch=evaluate('state.agentEpoch'),pending=evaluate("loadAgentConversation('REQUESTED')");
 assert.equal(evaluate('state.agentHistoryLoadBusy'),true);assert.equal(evaluate('agentCanSend()'),false);assert.equal(evaluate('state.agentConversationId'),'CURRENT');assert.equal(evaluate('state.agentDraft'),'尚未发送的追问');assert.match(element('#panel-assistant').innerHTML,/当前答案/);
 release();await pending;assert.deepEqual(paths,['/api/chat/history']);
 if(failure==='401'){
  assert.equal(evaluate('state.auth.authenticated'),false);assert.equal(evaluate('state.agentConversationId'),null);assert.equal(evaluate('state.agentDraft'),'');assert.doesNotMatch(element('#panel-assistant').innerHTML,/当前答案/);continue;
 }
 assert.equal(evaluate('state.agentEpoch'),beforeEpoch);assert.equal(evaluate('state.agentHistoryLoadBusy'),false);assert.equal(evaluate('state.agentConversationId'),'CURRENT');assert.equal(evaluate('state.agentTurnId'),2);assert.equal(evaluate('state.agentTurns[0].reply'),'当前答案');assert.equal(evaluate('state.agentDraft'),'尚未发送的追问');assert.equal(evaluate('state.agentRetry.request_id'),'RETRY-CURRENT');assert.equal(evaluate("state.agentProcessOpen['CURRENT-REQUEST']"),true);assert.equal(evaluate("state.agentEvidenceOpen['CURRENT-REQUEST']"),true);assert.equal(evaluate("state.agentReasoningScroll['CURRENT-REQUEST'].top"),80);assert.equal(scroll.scrollTop,300);assert.equal(evaluate('agentCanSend()'),true);assert.match(element('#global-status').textContent,/无法连接本机服务|暂未载入历史/);
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
const paths=[];context.fetch=async(path)=>{paths.push(path);if(path==='/api/auth/session')return {ok:true,json:async()=>({authenticated:true,school_client_enabled:true,identity,csrf_token:'memory-only'})};if(path==='/api/bootstrap')return {ok:true,json:async()=>({identity,school:{status:'ready'},chat:{configured:false,reason:'real_recommendation_not_ready'}})};if(path==='/api/chat/status')return {ok:true,json:async()=>({configured:false,reason:'real_recommendation_not_ready'})};throw Error('unexpected '+path);};
await evaluate('bootstrap()');assert.deepEqual(paths,['/api/auth/session','/api/bootstrap']);assert.equal(evaluate('state.tab'),'assistant');assert.equal(element('#tab-school').hidden,false);assert.equal(element('#tab-saved').hidden,true);assert.equal(element('#conversation-sidebar').hidden,true);assert.equal(element('#mode-badge').textContent,'本人教务');assert.match(element('#panel-assistant').innerHTML,/本人资料对话待接入/);
evaluate("state.agentStatus={configured:true,remote:true};state.agentDraft='private fixture';state.agentConsent=true");assert.equal(evaluate('agentCanSend()'),false);await evaluate('sendAgentTurn()');await evaluate('refreshAgentStatus()');await evaluate('refreshAgentHistory()');await evaluate('refreshSaved()');assert.deepEqual(paths,['/api/auth/session','/api/bootstrap','/api/chat/status']);
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
await evaluate('schoolSync()');assert.deepEqual(sent,{operation:'academic',target_term:'2026-2027-AW',force_refresh:true});const markup=element('#panel-school').innerHTML;assert.match(markup,/&lt;unsafe&gt;/);assert.match(markup,/免修/);assert.ok(markup.includes('<th scope="col">学分</th>'));assert.match(markup,/部分读取/);assert.doesNotMatch(markup,/<unsafe>/);
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
const form=element('#panel-school').innerHTML;assert.match(form,/主修成绩不按学年或学期筛选，页面仅展示本次取得的记录/);assert.match(form,/<select id="school-year"[^>]*disabled/);assert.match(form,/<select id="school-season"[^>]*disabled/);
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

    def test_plan_keeps_one_applicability_boundary_without_empty_field_list(self):
        self.run_js("""
evaluate("state.auth={authenticated:true,identity:{student_id:'SCHOOL-FIXTURE',auth_mode:'school_client',security_realm:'personal_school_client'}};state.schoolOperation='profile';state.schoolPlan={status:'acquired_pending_review',source_alias:'PLAN-FIXTURE',missing_fields:['major_or_category_raw','program','study_path_raw','college_name','major_name']};renderSchool()");
const markup=element('#panel-school').innerHTML;assert.doesNotMatch(markup,/适用范围信息缺失：|待确认字段|major_or_category_raw|study_path_raw|college_name|major_name/);assert.match(markup,/PDF 获取成功后仍需核验适用范围及培养要求/);assert.match(markup,/当前不用于自动推荐/);
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
evaluate("state.auth={authenticated:true,identity:{student_id:'SCHOOL-FIXTURE',auth_mode:'school_client',security_realm:'personal_school_client'}};state.schoolOperation='timetable';state.schoolResult={operation:'timetable',status:'partial_acquisition',data_source:'live',coverage:{captured_rows:3,reported_total:null,complete_within_scope:false,has_more:null},records:[{course_name_raw:'FIXTURE-1'},{course_name_raw:'FIXTURE-2'},{course_name_raw:'FIXTURE-3'}]};state.schoolPlan={status:'ambiguous_plan',missing_fields:[]};renderSchool()");
const markup=element('#panel-school').innerHTML;assert.match(markup,/已读取，覆盖范围待确认/);assert.match(markup,/本次读取 3 条 · 完整性待确认/);assert.doesNotMatch(markup,/未完成或待确认|已完整读取当前查询范围/);evaluate("state.schoolOperation='profile';renderSchool()");assert.match(element('#panel-school').innerHTML,/方案匹配不唯一/);
""")

    def test_school_year_and_season_choices_follow_authenticated_context(self):
        self.run_js(r"""
const current=Number(evaluate('schoolCurrentYear().slice(0,4)'));context.cohort=current-3;
evaluate("state.auth={authenticated:true,identity:{student_id:'SCHOOL-FIXTURE',auth_mode:'school_client',security_realm:'personal_school_client'}};state.schoolStatus={query_context:{cohort,cohort_state:'known'}};state.schoolYear=schoolCurrentYear();renderSchool()");
const years=evaluate('schoolYearChoices()');assert.equal(years.length,4);assert.equal(years[0],`${current}-${current+1}`);assert.equal(years.at(-1),`${current-3}-${current-2}`);
assert.match(element('#panel-school').innerHTML,/<select id="school-year"/);assert.doesNotMatch(element('#panel-school').innerHTML,/<input id="school-year"/);
assert.deepEqual(JSON.parse(evaluate('JSON.stringify(schoolSeasonChoices("academic"))')),{AW:'秋冬',SS:'春夏'});assert.deepEqual(JSON.parse(evaluate('JSON.stringify(schoolSeasonChoices("exams"))')),{A:'秋',W:'冬',S:'春',SU:'夏'});
assert.equal(evaluate('schoolMatchSeason("exams","SS")'),'S');assert.equal(evaluate('schoolMatchSeason("academic","W")'),'AW');
evaluate("state.schoolStatus={query_context:{cohort:null,cohort_state:'unknown'}};state.schoolProfile=null;state.schoolYear=schoolCurrentYear()");assert.equal(evaluate('schoolYearChoices().length'),1);
""")

    def test_school_grade_pagination_filter_does_not_fetch_unread_source(self):
        self.run_js(r"""
context.records=Array.from({length:60},(_,index)=>({course_name_raw:`SYNTHETIC-${index}`,course_code_raw:index<2?'REPEATED-COURSE':`C-${index}`,grade_raw:'90',academic_year_raw:index<20?'2023-2024':'2024-2025',completion_status_raw:null}));
evaluate("state.auth={authenticated:true,identity:{student_id:'SCHOOL-FIXTURE',auth_mode:'school_client',security_realm:'personal_school_client'}};state.schoolOperation='major_academic';state.schoolResult={operation:'major_academic',status:'partial_acquisition',data_source:'live',coverage:{captured_rows:60,reported_total:75,has_more:true,complete_within_scope:false},records};renderSchool()");
let markup=element('#panel-school').innerHTML;assert.equal((markup.match(/<tbody>[\s\S]*?<\/tbody>/)[0].match(/<tr>/g)||[]).length,15);assert.match(markup,/显示 1–15 条 \/ 60 条/);assert.match(markup,/还有未读取内容/);assert.equal(evaluate('state.schoolResult.records.length'),60);
evaluate('state.schoolGradePage=4;renderSchool()');markup=element('#panel-school').innerHTML;assert.match(markup,/显示 46–60 条/);assert.match(markup,/SYNTHETIC-59/);assert.doesNotMatch(markup,/SYNTHETIC-0<\/td>/);
evaluate("state.schoolGradeSearch='REPEATED-COURSE';state.schoolGradePage=1;renderSchool()");markup=element('#panel-school').innerHTML;assert.match(markup,/显示 1–2 条 \/ 2 条（已读取 60 条）/);assert.match(markup,/SYNTHETIC-0/);assert.match(markup,/SYNTHETIC-1/);assert.match(markup,/还有未读取内容/);
evaluate("state.schoolGradeSearch='';state.schoolGradeYear='2023-2024';state.schoolGradePage=99;renderSchool()");assert.match(element('#panel-school').innerHTML,/显示 16–20 条 \/ 20 条/);assert.equal(evaluate('state.schoolGradePage'),2);
evaluate('clearWorkspace()');assert.equal(evaluate('state.schoolGradeSearch'),'');assert.equal(evaluate('state.schoolGradeYear'),'');assert.equal(evaluate('state.schoolProfile'),null);
""")

    def test_school_grade_filter_preserves_safe_text_and_unknown_status(self):
        self.run_js(r"""
const result={operation:'academic',records:[{course_name_raw:'<script>unsafe</script>',grade_raw:'90',nominal_credits:'2',completion_status_raw:null,earned_credits:null},{course_name_raw:'SYNTHETIC-PASSED',completion_status_raw:'passed',grade_raw:'90'}]};context.result=result;
const markup=evaluate('schoolRecordTable(result)');assert.match(markup,/&lt;script&gt;unsafe&lt;\/script&gt;/);assert.doesNotMatch(markup,/<script>|已获学分 2|待确认|来源未提供|含义待核验/);assert.match(markup,/muted">—/);assert.match(markup,/通过/);assert.doesNotMatch(markup,/passed<\/td>/);assert.equal(result.records[1].completion_status_raw,'passed');
""")

    def test_school_grade_columns_follow_all_acquired_rows_without_losing_repeats(self):
        self.run_js(r"""
context.result={operation:'major_academic',records:Array.from({length:16},(_,index)=>({course_name_raw:`SYNTHETIC-${index}`,course_code_raw:'REPEATED-COURSE',grade_raw:index===15?'90':null,nominal_credits:index===15?0:null,academic_year_raw:null,term_raw:'',completion_status_raw:'unknown'}))};
let markup=evaluate('schoolRecordTable(result)');const header=markup.match(/<thead>([\s\S]*?)<\/thead>/)[1];
assert.match(header,/课程/);assert.match(header,/成绩/);assert.match(header,/>学分<\/th>/);assert.doesNotMatch(header,/>学年<|>学期<|>修读状态<|来源|记录学年/);assert.doesNotMatch(markup,/school-grade-year|待确认|来源未提供/);
assert.equal((markup.match(/<tbody>[\s\S]*?<\/tbody>/)[0].match(/<tr>/g)||[]).length,15);assert.match(markup,/SYNTHETIC-0/);assert.match(markup,/SYNTHETIC-1/);
evaluate("state.schoolGradeSearch='SYNTHETIC-0'");markup=evaluate('schoolRecordTable(result)');assert.match(markup.match(/<thead>([\s\S]*?)<\/thead>/)[1],/成绩/);assert.match(markup,/muted">—/);
evaluate("state.schoolGradeSearch='';state.schoolGradePage=2");markup=evaluate('schoolRecordTable(result)');assert.match(markup,/SYNTHETIC-15/);assert.match(markup,/<td>0<\/td>/);assert.match(markup,/<td>90<\/td>/);
assert.equal(context.result.records.length,16);assert.equal(context.result.records[0].grade_raw,null);
""")

    def test_school_all_empty_grade_columns_are_removed_without_deleting_rows(self):
        self.run_js(r"""
context.result={operation:'academic',records:[{course_name_raw:'合成仅课程',course_code_raw:'SYNTHETIC-A',grade_raw:null,nominal_credits:'',academic_year_raw:'unknown',term_raw:null,completion_status_raw:null},{course_name_raw:'合成冲突课程',grade_raw:'不展示冲突成绩',field_states:{grade_raw:'conflicting'}}]};
const markup=evaluate('schoolRecordTable(result)'),header=markup.match(/<thead>([\s\S]*?)<\/thead>/)[1];assert.equal((header.match(/<th /g)||[]).length,1);
assert.match(markup,/合成仅课程/);assert.match(markup,/SYNTHETIC-A/);assert.match(markup,/合成冲突课程/);assert.doesNotMatch(markup,/不展示冲突成绩|待确认|来源未提供|来源值冲突/);
assert.equal((markup.match(/<tbody>[\s\S]*?<\/tbody>/)[0].match(/<tr>/g)||[]).length,2);
""")

    def test_school_exam_columns_only_show_available_independent_fields(self):
        self.run_js(r"""
context.result={operation:'exams',records:[{course_name_raw:'合成考试甲',final_exam_time_raw:'合成期末时间',final_exam_place_raw:null,midterm_exam_time_raw:'',midterm_exam_place_raw:null},{course_name_raw:'<img src=x onerror=bad()>',final_exam_time_raw:null,final_exam_place_raw:'合成期末地点',midterm_exam_time_raw:null,midterm_exam_place_raw:null}]};
const markup=evaluate('schoolRecordTable(result)'),header=markup.match(/<thead>([\s\S]*?)<\/thead>/)[1];assert.match(header,/期末考试时间/);assert.match(header,/期末考试地点/);assert.doesNotMatch(header,/期中/);assert.match(markup,/合成期末时间/);assert.match(markup,/合成期末地点/);assert.match(markup,/muted">—/);
assert.doesNotMatch(markup,/待确认|没有考试|无考试|<img/);assert.match(markup,/&lt;img/);
context.result={operation:'exams',records:[{course_name_raw:'合成未知考试',final_exam_time_raw:null,final_exam_place_raw:'',midterm_exam_time_raw:null,midterm_exam_place_raw:null}]};const empty=evaluate('schoolRecordTable(result)');assert.equal((empty.match(/<thead>[\s\S]*?<\/thead>/)[0].match(/<th /g)||[]).length,1);assert.match(empty,/合成未知考试/);assert.doesNotMatch(empty,/期末考试时间|期末考试地点|期中考试时间|期中考试地点|无考试|待确认/);
""")

    def test_school_profile_omits_empty_conflicting_and_unverified_fields(self):
        self.run_js(r"""
context.result={operation:'profile',profile:{name:'合成姓名',cohort:2023,college_name:'合成学院',major_name:'不展示冲突专业',program:null,study_path_raw:'',major_or_category_raw:'不展示未核验分类',field_states:{major_name:'conflicting',major_or_category_raw:'pending_verification'}}};
const markup=evaluate('schoolRecordTable(result)');assert.match(markup,/合成姓名|2023|合成学院/);assert.doesNotMatch(markup,/<dt>专业名称<\/dt>|培养项目|修读路径|类（专业）|待确认|不展示冲突专业|不展示未核验分类|<dt>学号<\/dt>/);
context.result={operation:'profile',profile:{name:null,cohort:null,program:null}};assert.match(evaluate('schoolRecordTable(result)'),/本次未返回可展示资料/);
""")

    def test_school_source_details_fold_diagnostics_without_missing_field_labels(self):
        self.run_js(r"""
context.result={operation:'academic',captured_at:null,source_field_diagnostics:[{source:{fields:{}},projection:{fields:{}}}],row_failures:[{code:'fixture'}]};
const markup=evaluate('schoolResultDetails(result)');assert.match(markup,/<summary>数据来源与读取说明<\/summary>/);assert.match(markup,/<details><summary>字段结构诊断<\/summary>/);assert.match(markup,/部分记录或字段未能读取/);assert.match(markup,/不会继续获取学校未读取的记录/);
assert.doesNotMatch(markup,/修读状态缺少明确来源时保持待确认|来源与待确认信息|读取时间：待确认|已保留待核验状态/);
""")

    def test_timetable_sync_fetches_two_seasons_in_order_and_keeps_primary_result(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'SCHOOL-FIXTURE',auth_mode:'school_client',security_realm:'personal_school_client'}};state.csrfToken='memory-only'");
for(const [season,expected] of [['AW',['A','W']],['SS',['S','SU']]]){
 element('#school-operation').value='timetable';element('#school-year').value='2025-2026';element('#school-season').value=season;const calls=[];
 context.fetch=async(path,options)=>{assert.equal(path,'/api/school/sync');const body=JSON.parse(options.body);calls.push(body);assert.equal(body.force_refresh,true);if(body.operation==='exams')assert.equal(evaluate('state.schoolResult.operation'),'timetable');return {ok:true,json:async()=>({operation:body.operation,target_term:body.target_term,status:'complete_within_scope',data_source:'live',records:[{course_name_raw:'合成课程',course_code_raw:'SYNTHETIC-C',source_selection_id_raw:'SYNTHETIC-X',time_raw:body.operation==='timetable'?'周一第1节':undefined,final_exam_time_raw:body.operation==='exams'?`合成考试-${body.target_term}`:undefined,field_states:{source_selection_id_raw:'known'}}]})};};
 await evaluate('schoolSync()');assert.deepEqual(calls.map(body=>[body.operation,body.target_term]),[['timetable',`2025-2026-${season}`],...expected.map(value=>['exams',`2025-2026-${value}`])]);assert.equal(evaluate('state.schoolResult.operation'),'timetable');assert.equal(evaluate('state.schoolResult.records[0].time_raw'),'周一第1节');assert.equal(evaluate('state.schoolResult.records[0].related_exams.length'),2);assert.equal(evaluate('state.schoolResult.related_exam_sources.length'),2);assert.equal(evaluate('state.schoolBusy'),false);assert.match(element('#panel-school').innerHTML,/school-meeting-exam/);
}
evaluate('clearSchoolWorkspace()');assert.equal(evaluate('state.schoolResult'),null);
""")

    def test_related_exam_sync_rejects_response_scope_and_operation_mismatches(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'SCHOOL-FIXTURE',auth_mode:'school_client',security_realm:'personal_school_client'}}");element('#school-operation').value='timetable';element('#school-year').value='2025-2026';element('#school-season').value='AW';let calls=0;
context.fetch=async(path,options)=>{const body=JSON.parse(options.body);calls++;return {ok:true,json:async()=>({operation:calls===3?'academic':body.operation,target_term:calls===2?'2025-2026-W':body.target_term,status:'complete',records:[{course_name_raw:'合成同名课',source_selection_id_raw:'SYNTHETIC-X',final_exam_time_raw:'不合并错误范围',field_states:{source_selection_id_raw:'known'}}]})};};
await evaluate('schoolSync()');assert.equal(calls,3);assert.equal(evaluate('state.schoolResult.records[0].related_exams.length'),0);assert.equal(evaluate('state.schoolResult.operation'),'timetable');assert.doesNotMatch(element('#panel-school').innerHTML,/不合并错误范围/);assert.match(element('#panel-school').innerHTML,/部分考试安排未同步/);
""")

    def test_related_exam_source_failure_and_captcha_keep_timetable_without_claiming_none(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'SCHOOL-FIXTURE',auth_mode:'school_client',security_realm:'personal_school_client'}}");
for(const captcha of [false,true]){
 element('#school-operation').value='timetable';element('#school-year').value='2025-2026';element('#school-season').value='AW';const calls=[];
 context.fetch=async(path,options)=>{assert.equal(path,'/api/school/sync');const body=JSON.parse(options.body);calls.push(body);if(calls.length===2)return captcha?{ok:true,json:async()=>({operation:'exams',target_term:body.target_term,status:'captcha_required'})}:{ok:false,status:502,text:async()=>JSON.stringify({code:'source_unavailable',error:'private error must stay out of UI'})};return {ok:true,json:async()=>({operation:body.operation,target_term:body.target_term,status:'complete',records:[{course_name_raw:'合成主课表',source_selection_id_raw:'SYNTHETIC-X',final_exam_time_raw:body.operation==='exams'?'合成冬考试':undefined,field_states:{source_selection_id_raw:'known'}}]})};};
 await evaluate('schoolSync()');assert.equal(calls.length,captcha?2:3);assert.equal(evaluate('state.schoolResult.operation'),'timetable');assert.equal(evaluate('state.schoolNeedsCaptcha'),false);assert.equal(evaluate('state.schoolPending'),null);assert.equal(evaluate('state.schoolResult.records[0].related_exams.length'),captcha?0:1);const markup=element('#panel-school').innerHTML;assert.match(markup,/合成主课表/);assert.equal((markup.match(/部分考试安排未同步/g)||[]).length,1);assert.doesNotMatch(markup,/无考试|没有考试|private error|验证码图片/);
}
""")

    def test_related_exam_auth_expiry_or_subject_change_clears_primary(self):
        self.run_js(r"""
for(const failure of ['source_session_expired','source_subject_changed']){
 evaluate("clearWorkspace();state.auth={authenticated:true,identity:{student_id:'SCHOOL-FIXTURE',auth_mode:'school_client',security_realm:'personal_school_client'}}");element('#school-operation').value='timetable';element('#school-year').value='2025-2026';element('#school-season').value='AW';let calls=0;
 context.fetch=async(path,options)=>{calls++;if(calls===2)return {ok:false,status:409,text:async()=>JSON.stringify({code:failure,error:'fixture'})};const body=JSON.parse(options.body);return {ok:true,json:async()=>({operation:'timetable',target_term:body.target_term,status:'complete',records:[{course_name_raw:'旧主体课表'}]})};};
 await evaluate('schoolSync()');assert.equal(calls,2);assert.equal(evaluate('state.schoolResult'),null);assert.equal(evaluate('state.schoolReauth'),failure==='source_session_expired');assert.doesNotMatch(element('#panel-school').innerHTML,/旧主体课表/);if(failure==='source_subject_changed')assert.match(element('#global-status').textContent,/请退出后重新登录/);
}
""")

    def test_related_exam_late_response_never_merges_across_owner_or_epoch(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'SCHOOL-A',auth_mode:'school_client',security_realm:'personal_school_client'}}");element('#school-operation').value='timetable';element('#school-year').value='2025-2026';element('#school-season').value='AW';let release,calls=0;
context.fetch=(path,options)=>{const body=JSON.parse(options.body);calls++;if(calls===2)return new Promise(resolve=>{release=resolve;});return Promise.resolve({ok:true,json:async()=>({operation:'timetable',target_term:body.target_term,status:'complete',records:[{course_name_raw:'A私有课表',source_selection_id_raw:'SYNTHETIC-X',field_states:{source_selection_id_raw:'known'}}]})});};
const pending=evaluate('schoolSync()');for(let index=0;index<10&&!release;index++)await Promise.resolve();assert.ok(release);assert.equal(evaluate('state.schoolResult.operation'),'timetable');evaluate("clearWorkspace();state.auth={authenticated:true,identity:{student_id:'SCHOOL-B',auth_mode:'school_client',security_realm:'personal_school_client'}};renderSchool()");release({ok:true,json:async()=>({operation:'exams',target_term:'2025-2026-A',status:'complete',records:[{source_selection_id_raw:'SYNTHETIC-X',final_exam_time_raw:'A私有考试',field_states:{source_selection_id_raw:'known'}}]})});await pending;assert.equal(calls,2);assert.equal(evaluate('state.schoolResult'),null);assert.doesNotMatch(element('#panel-school').innerHTML,/A私有课表|A私有考试/);
""")

    def test_school_section_navigation_preserves_query_and_separates_plan(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'SCHOOL-FIXTURE',auth_mode:'school_client',security_realm:'personal_school_client'}};state.schoolOperation='academic';state.schoolPlan={status:'acquired_pending_review',source_alias:'SYNTHETIC-PLAN'};renderSchool()");assert.doesNotMatch(element('#panel-school').innerHTML,/下载培养方案 PDF/);
element('#school-year').value='2024-2025';element('#school-season').value='SS';evaluate("selectSchoolSection('exams')");assert.equal(evaluate('state.schoolYear'),'2024-2025');assert.equal(evaluate('state.schoolSeason'),'S');assert.doesNotMatch(element('#panel-school').innerHTML,/<option value="AW"/);
element('#school-year').value='2024-2025';element('#school-season').value='SU';evaluate("selectSchoolSection('profile')");assert.match(element('#panel-school').innerHTML,/下载培养方案 PDF/);assert.match(element('#panel-school').innerHTML,/<select id="school-year"[^>]*disabled/);
element('#school-year').value='2024-2025';element('#school-season').value='SS';evaluate("selectSchoolSection('academic')");assert.equal(evaluate('state.schoolYear'),'2024-2025');assert.equal(evaluate('state.schoolSeason'),'SS');
evaluate('state.schoolReauth=true;renderSchool()');assert.match(element('#panel-school').innerHTML,/<summary>需要重新验证<\/summary>/);assert.doesNotMatch(element('#panel-school').innerHTML,/<summary>会话有效<\/summary>/);
""")

    def test_source_field_evidence_distinguishes_projection_loss_and_cache(self):
        self.run_js(r"""
context.result={operation:'timetable',data_source:'historical_cache',source_field_diagnostics:[{source:{fields:{skdd:{present:2,empty:0},sfqd:{present:2,empty:0}}},projection:{fields:{sfqd:{present:2,empty:0}}}}]};const markup=evaluate('schoolFieldEvidenceMarkup(result)');assert.match(markup,/地点候选字段，当前适配尚未保留全部字段/);assert.match(markup,/是否确定候选字段已保留，含义仍需核验/);assert.doesNotMatch(markup,/正式中选|skdd|sfqd|本次学校响应/);
context.result={operation:'major_academic',data_source:'historical_cache',source_field_diagnostics:[{source:{fields:{}},projection:{fields:{}}}]};assert.match(evaluate('schoolFieldEvidenceMarkup(result)'),/这批记录对应的学校响应未见已识别的修读状态字段/);assert.equal(evaluate('schoolFieldEvidenceMarkup({})'),'');
""")

    def test_profile_context_fallback_uses_own_request_and_discards_late_owner(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'SCHOOL-A',auth_mode:'school_client',security_realm:'personal_school_client'}};state.schoolProfile=null;state.schoolStatus={}");let release;context.fetch=path=>{assert.equal(path,'/api/school/profile');return new Promise(resolve=>{release=resolve;});};const pending=evaluate('ensureSchoolQueryContext()');evaluate("clearWorkspace();state.auth={authenticated:true,identity:{student_id:'SCHOOL-B',auth_mode:'school_client',security_realm:'personal_school_client'}}");release({ok:true,json:async()=>({profile:{cohort:2023,field_states:{cohort:'known'},name:'A-private'}})});await pending;assert.equal(evaluate('state.schoolProfile'),null);
evaluate("state.schoolStatus={query_context:{cohort:2023,cohort_state:'known'}}");context.fetch=async()=>{throw Error('context must not refetch profile');};await evaluate('ensureSchoolQueryContext()');
""")


    def test_personal_chat_defaults_to_sending_without_a_permission_checkbox(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-FIXTURE',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={};state.agentStatus={configured:true,available:true,remote:true,provider:'DeepSeek',model:'fixture',data_kind:'personal_source',personal_data_use:{version:'personal-retrieval-v1',summary:'本轮问题及检索到的相关教务记录、培养资料片段会发送给当前模型；不发送账号凭据。'}};state.agentDraft='我的成绩如何？';state.agentConsent=true;renderAssistant();renderAuth()");
let markup=element('#panel-assistant').innerHTML;assert.match(markup,/查询成绩、课表、考试和培养要求/);assert.doesNotMatch(markup,/id="agent-personal-consent"|对话设置|agent-chat-settings/);assert.doesNotMatch(markup,/本机资料查询|personal-operation|personal-observation|personal-prepare|准备本机培养资料|合成测试需求|agent-remote-consent/);assert.equal(element('#conversation-sidebar').hidden,false);assert.equal(evaluate('agentCanSend()'),true);
evaluate('state.agentConsent=false');assert.equal(evaluate('agentCanSend()'),true);
evaluate("state.agentStatus.personal_data_use=undefined;renderAssistant()");assert.equal(evaluate('agentCanSend()'),false);assert.match(element('#panel-assistant').innerHTML,/本人资料对话待接入/);assert.doesNotMatch(element('#panel-assistant').innerHTML,/personal-operation|personal-observation/);
""")

    def test_personal_turn_automatically_sends_required_scope_and_presents_tool_evidence(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-FIXTURE',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={};state.csrfToken='memory-only';state.agentStatus={configured:true,available:true,remote:true,provider:'DeepSeek',data_kind:'personal_source',personal_data_use:{version:'personal-retrieval-v1'}};state.agentConsent=false;state.agentDraft='专业选修要求是什么？'");
const calls=[];context.fetch=async(path,options)=>{const body=options.body?JSON.parse(options.body):null;calls.push({path,body});if(body)assert.equal(options.headers['X-CSRF-Token'],'memory-only');if(path==='/api/chat/progress')return {ok:true,json:async()=>({request_id:body.request_id,status:'not_started',events:[],next_sequence:0,terminal:false,active_reasoning:null,available:true})};if(path==='/api/chat/conversations?limit=100')return {ok:true,json:async()=>({conversations:[],has_more:false,data_kind:'personal_source'})};assert.equal(path,'/api/chat/turn');assert.equal(options.headers['X-CSRF-Token'],'memory-only');return {ok:true,json:async()=>({conversation_id:'personal-chat',turn_id:1,status:'completed',reply:'相关片段如下；本人适用仍待核验。',data_kind:'personal_source',tool_results:[{name:'query_my_records',result:{operation:'major_academic',target_term:'ALL-HISTORY',data_source:'historical_cache',coverage:{captured_rows:2,has_more:true},records:[{course_name_raw:'体育重复',grade_raw:'85'},{course_name_raw:'体育重复',grade_raw:'90'}]}},{name:'search_my_curriculum',result:{matches:[{reference:'opaque-reference',title:'要求<img src=x>',text:'至少 8 学分<script>bad()</script>',page:3,review_status:'unreviewed',locator:{path:'/private/never-show'},required_context:[{title:'必要前提',text:'条件 <img src=x>',page:2}]}]}}],citations:[]})};};
await evaluate('sendAgentTurn()');assert.deepEqual(Object.keys(calls[0].body).sort(),['consent_personal','conversation_id','data_use_version','expected_turn','message','request_id']);assert.equal(calls[0].body.consent_personal,true);assert.equal(calls[0].body.data_use_version,'personal-retrieval-v1');assert.deepEqual(calls.map(item=>item.path),['/api/chat/turn','/api/chat/progress','/api/chat/conversations?limit=100']);assert.equal(calls.filter(item=>item.path==='/api/chat/turn').length,1);assert.deepEqual(calls[1].body,{request_id:calls[0].body.request_id,after_sequence:0});let markup=element('#panel-assistant').innerHTML;assert.match(markup,/相关片段如下/);assert.match(markup,/PDF 第 3 页/);assert.match(markup,/原文待审核/);assert.match(markup,/必要前提/);assert.match(markup,/&lt;script&gt;/);assert.equal((markup.match(/体育重复/g)||[]).length,2);assert.match(markup,/还有未读取内容/);assert.doesNotMatch(markup,/<script>|<img|\/private\/never-show|opaque-reference|personal-operation|personal-observation|personal-prepare|data-action="save-agent-option"/);
""")

    def test_personal_default_send_still_requires_ready_source_and_idle_state(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-FIXTURE',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={};state.agentStatus={configured:true,available:true,remote:true,provider:'DeepSeek',data_kind:'personal_source',personal_data_use:{version:'personal-retrieval-v1'}};state.agentConsent=false;state.agentDraft='查询已读成绩'");
const calls=[];context.fetch=async path=>{calls.push(path);throw Error('blocked state must not send');};assert.equal(evaluate('agentCanSend()'),true);
for(const key of ['agentBusy','agentSettingsBusy','agentActionBusy','busy','agentHistoryLoadBusy','agentHistoryPending','schoolReauth']){
 evaluate(`state.${key}=true`);assert.equal(evaluate('agentCanSend()'),false,key);await evaluate('sendAgentTurn()');evaluate(`state.${key}=false`);
}
for(const available of [false,undefined,null]){context.available=available;evaluate('state.agentStatus.available=available');assert.equal(evaluate('agentCanSend()'),false);await evaluate('sendAgentTurn()');}
evaluate('state.agentStatus.available=true;state.agentStatus.configured=false');assert.equal(evaluate('agentCanSend()'),false);await evaluate('sendAgentTurn()');
evaluate('state.agentStatus.configured=true;state.agentStatus.personal_data_use=undefined');assert.equal(evaluate('agentCanSend()'),false);await evaluate('sendAgentTurn()');assert.deepEqual(calls,[]);
""")

    def test_personal_history_uses_personal_realm_and_never_replays_actions(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-FIXTURE',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={};state.agentStatus={configured:true,remote:true,data_kind:'personal_source',personal_data_use:{version:'personal-retrieval-v1'}}");
const calls=[];context.fetch=async(path,options)=>{calls.push(path);if(path==='/api/chat/conversations?limit=100')return {ok:true,json:async()=>({conversations:[{conversation_id:'personal-history',title:'培养要求问题'}],has_more:false,data_kind:'personal_source'})};assert.equal(path,'/api/chat/history');assert.deepEqual(JSON.parse(options.body),{conversation_id:'personal-history'});return {ok:true,json:async()=>({conversation_id:'personal-history',turn_id:1,turns:[{status:'completed',message:'历史问题',reply:'历史回答',tool_results:[],citations:[],reply_origin:'verified_explicit_action',download:{data_base64:'must-not-replay'}}],in_progress:false,action_pending:false,option_receipts:{},data_kind:'personal_source'})};};
await evaluate('refreshAgentHistory()');assert.match(element('#conversation-list').innerHTML,/培养要求问题/);await evaluate("loadAgentConversation('personal-history')");assert.equal(evaluate('state.agentDraft'),'');assert.equal(evaluate('state.agentConversationId'),'personal-history');assert.match(element('#panel-assistant').innerHTML,/历史问题/);assert.doesNotMatch(element('#panel-assistant').innerHTML,/agent-personal-consent/);assert.deepEqual(calls,['/api/chat/conversations?limit=100','/api/chat/history']);
context.fetch=async()=>({ok:true,json:async()=>({conversation_id:'wrong-realm',turn_id:1,turns:[{message:'合成内容不可串入'}],data_kind:'synthetic'})});await evaluate("loadAgentConversation('wrong-realm')");assert.equal(evaluate('state.agentConversationId'),'personal-history');assert.equal(evaluate('state.agentTurns.length'),1);assert.match(element('#panel-assistant').innerHTML,/历史问题/);assert.doesNotMatch(element('#panel-assistant').innerHTML,/合成内容不可串入/);
""")

    def test_personal_status_uses_current_scope_version_and_source_expiry_clears_data(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-FIXTURE',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={};state.agentStatus={configured:true,available:true,remote:true,provider:'Old',model:'m1',data_kind:'personal_source',personal_data_use:{version:'personal-retrieval-v1'}};state.agentDraft='问题'");
context.fetch=async()=>({ok:true,json:async()=>({configured:true,available:true,remote:true,provider:'New',model:'m2',data_kind:'personal_source',personal_data_use:{version:'personal-retrieval-v2'}})});await evaluate('refreshAgentStatus()');assert.equal(evaluate('state.agentStatus.personal_data_use.version'),'personal-retrieval-v2');assert.equal(evaluate('agentCanSend()'),true);
evaluate("state.agentTurns=[{status:'completed',message:'私人问题',reply:'私人回答',tool_results:[],citations:[]}];state.agentHistory=[{conversation_id:'p',title:'私人标题'}];renderAssistant();renderAgentHistory()");await evaluate("schoolFailure('source_session_expired',currentSchoolRequest(),null)");assert.equal(evaluate('agentCanSend()'),false);assert.equal(evaluate('state.agentTurns.length'),0);assert.equal(evaluate('state.agentHistory.length'),0);assert.doesNotMatch(element('#panel-assistant').innerHTML,/私人回答/);assert.doesNotMatch(element('#conversation-list').innerHTML,/私人标题/);
""")

    def test_personal_late_chat_history_and_401_cannot_cross_owner(self):
        self.run_js(r"""
const seed=owner=>evaluate(`clearWorkspace();state.auth={authenticated:true,identity:{student_id:'${owner}',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={};state.agentStatus={configured:true,available:true,remote:true,data_kind:'personal_source',personal_data_use:{version:'personal-retrieval-v1'}};state.agentDraft='条件';renderAssistant()`);
seed('PERSONAL-A');let release;context.fetch=()=>new Promise(resolve=>{release=resolve;});const pending=evaluate('sendAgentTurn()');seed('PERSONAL-B');release({ok:true,json:async()=>({conversation_id:'A-chat',turn_id:1,status:'completed',reply:'A 的私有原文',tool_results:[],citations:[],data_kind:'personal_source'})});await pending;assert.equal(evaluate('state.agentTurns.length'),0);assert.doesNotMatch(element('#panel-assistant').innerHTML,/A 的私有原文/);
const late401=evaluate('sendAgentTurn()');seed('PERSONAL-C');release({ok:false,status:401,text:async()=>JSON.stringify({error:'old session expired'})});await late401;assert.equal(evaluate('state.auth.authenticated'),true);assert.equal(evaluate('state.auth.identity.student_id'),'PERSONAL-C');assert.equal(evaluate('state.agentTurns.length'),0);
const history=evaluate("loadAgentConversation('A-history')");seed('PERSONAL-D');release({ok:true,json:async()=>({conversation_id:'A-history',turn_id:1,turns:[{message:'A的历史私密'}],data_kind:'personal_source'})});await history;assert.equal(evaluate('state.agentTurns.length'),0);assert.doesNotMatch(element('#panel-assistant').innerHTML,/A的历史私密/);
""")

    def test_personal_records_preserve_unknown_status_and_cannot_offer_save_or_export(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-FIXTURE',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={}");const markup=evaluate("personalRecordTable({operation:'major_academic',records:[{course_name_raw:'甲',completion_status_raw:'failed'},{course_name_raw:'乙',completion_status_raw:'in_progress'},{course_name_raw:'丙',completion_status_raw:'passed'},{course_name_raw:'丁',completion_status_raw:'pending_recognition'},{course_name_raw:'戊',completion_status_raw:'unknown'},{course_name_raw:'己',completion_status_raw:'passed',field_states:{completion_status_raw:'unknown'}}]})");assert.match(markup,/未通过/);assert.match(markup,/在修/);assert.match(markup,/通过/);assert.match(markup,/待认定/);assert.match(markup,/待确认/);assert.doesNotMatch(markup,/>failed<|>in_progress<|>passed<|>pending_recognition<|>unknown</);
const card=evaluate("agentToolCard({name:'recommend_my_courses',result:{plans:[{option_id:'must-not-save'}]}})");assert.match(card,/真实课表推荐待/);assert.doesNotMatch(card,/data-action="save-agent-option"|data-action="export-agent-option"/);const calls=[];context.fetch=async path=>{calls.push(path);throw Error('must not act');};await evaluate("saveAgentOption('must-not-save')");await evaluate("exportAgentOption('must-not-save','json')");await evaluate("applyAgentTurnActions({status:'completed',reply_origin:'verified_explicit_action',download:{data_base64:'must-not-download'}},state.agentEpoch)");assert.deepEqual(calls,[]);
""")

    def test_personal_citations_render_structured_records_context_and_numbered_curriculum(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-FIXTURE',auth_mode:'school_client',security_realm:'personal_school_client'}}");
const record=evaluate("agentCitation({kind:'record',citation_number:1,operation:'academic',text:'{\"internal_record\":\"never show\"}',evidence_id:'SECRET-RECORD-REF',record:{course_name_raw:'合成体育<img src=x>',grade_raw:'85',completion_status_raw:'passed',raw_row:{private:'never show'}}})");assert.match(record,/\[1\] 本人来源记录/);assert.match(record,/合成体育&lt;img/);assert.match(record,/85/);assert.match(record,/通过/);assert.doesNotMatch(record,/<img|internal_record|SECRET-RECORD-REF|raw_row|never show|原文待审核/);
const context=evaluate("agentCitation({kind:'context',citation_number:2,text:'{\"internal_context\":true}',context:{cohort:2023,major_name:'信息管理',owner:'never-show-owner',operations:[{operation:'major_academic',target_term:'ALL-HISTORY',coverage:{captured_rows:3,complete_within_scope:false,has_more:true}}],capabilities:{debug:true}}})");assert.match(context,/\[2\] 本轮已知范围/);assert.match(context,/入学年/);assert.match(context,/2023/);assert.match(context,/信息管理/);assert.match(context,/主修成绩 · 全部已读历史/);assert.match(context,/还有未读取内容/);assert.doesNotMatch(context,/internal_context|never-show-owner|capabilities|debug|PDF 第/);
const unknown=evaluate("agentCitation({kind:'context',context:{cohort:2023,major_name:'不可展示专业',field_states:{cohort:'unknown',major_name:'unknown'},operations:[]},text:'{\"must_not_parse\":1}'})");assert.match(unknown,/尚无可展示的已知值/);assert.doesNotMatch(unknown,/2023|不可展示专业|must_not_parse/);
const curriculum=evaluate("agentCitation({kind:'curriculum',citation_number:3,title:'培养要求',text:'至少 8 学分',physical_page:4,required_context:[{page:3,text:'完整前提'}],locator:{path:'/private/never-show'},evidence_id:'SECRET-CURRICULUM-REF'})");assert.match(curriculum,/\[3\] 培养要求/);assert.match(curriculum,/PDF 第 4 页/);assert.match(curriculum,/原文待审核/);assert.match(curriculum,/完整前提/);assert.doesNotMatch(curriculum,/\/private\/never-show|SECRET-CURRICULUM-REF/);
const omitted=evaluate("agentCitation({kind:'curriculum',citation_number:4,text:'范围片段',page:5,context_omitted:true,required_context:[]})");assert.match(omitted,/上下文因展示预算未载入/);assert.doesNotMatch(omitted,/local_context_budget|context_omitted/);
""")

    def test_model_directory_is_real_cached_and_remote_refresh_is_explicit(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-FIXTURE',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={};state.agentStatus={configured:true,remote:true,model:'configured-but-not-listed',data_kind:'personal_source',personal_data_use:{version:'personal-retrieval-v1'}}");
const calls=[];context.fetch=async(path,options)=>{calls.push({path,body:options.body?JSON.parse(options.body):null});return {ok:true,json:async()=>({models:[{id:'provider-real-a'},{id:'provider-real-b'}],source:'provider_cache',configured:true})};};
await evaluate('refreshAgentModels(false)');assert.deepEqual(calls,[{path:'/api/chat/models',body:null}]);let controls=evaluate('renderAgentModelControls()');assert.match(controls,/value="provider-real-a"/);assert.match(controls,/value="provider-real-b"/);assert.doesNotMatch(controls,/value="configured-but-not-listed"/);assert.match(controls,/configured-but-not-listed（当前配置）/);assert.match(controls,/<option[^>]*value="@refresh"[^>]*>刷新模型目录/);
element('#agent-model').value='@refresh';element('#agent-model').listeners.change({target:element('#agent-model')});for(let i=0;i<20&&evaluate('state.agentModelsBusy');i++)await Promise.resolve();assert.equal(evaluate('state.agentModelsBusy'),false);assert.deepEqual(calls[1],{path:'/api/chat/models/refresh',body:{}});assert.doesNotMatch(JSON.stringify(calls),/chat\/settings|chat\/turn/);evaluate('state.agentBusy=true');await evaluate('refreshAgentModels(true)');assert.equal(calls.length,2);assert.match(evaluate('renderAgentModelControls()'),/<select id="agent-model"[^>]*disabled/);
evaluate('state.agentBusy=false;state.agentModels=[]');controls=evaluate('renderAgentModelControls()');assert.doesNotMatch(controls,/<select id="agent-model"[^>]*disabled/);assert.match(controls,/<option[^>]*value="@refresh"/);
evaluate('state.agentBusy=false');context.fetch=async()=>({ok:false,status:404,text:async()=>JSON.stringify({error:'not found'})});await evaluate('refreshAgentModels(false)');assert.match(element('#panel-assistant').innerHTML,/当前服务暂不支持模型切换/);
""")

    def test_model_settings_keep_conversation_draft_and_reject_fake_ids(self):
        self.run_js(r"""
assert.notEqual(evaluate("agentPolicyKey({model:'real-a',thinking:false,settings_revision:0})"),evaluate("agentPolicyKey({model:'real-a',thinking:true,settings_revision:0})"));assert.notEqual(evaluate("agentPolicyKey({model:'real-a',thinking:false,settings_revision:0})"),evaluate("agentPolicyKey({model:'real-a',thinking:false,settings_revision:2})"));
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-FIXTURE',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={};state.agentStatus={configured:true,available:true,remote:true,model:'real-a',thinking:false,data_kind:'personal_source',personal_data_use:{version:'personal-retrieval-v1'}};state.agentModels=[{id:'real-a'},{id:'real-b'}];state.agentConversationId='same-conversation';state.agentTurns=[{message:'此前问题',status:'completed',reply:'此前回答',tool_results:[],citations:[]}];state.agentDraft='保留未发草稿'");
element('#agent-model').value='made-up-model';const calls=[];let release;context.fetch=(path,options)=>{calls.push({path,body:JSON.parse(options.body)});return new Promise(resolve=>{release=resolve;});};await evaluate('saveAgentSettings()');assert.equal(calls.length,0);
element('#agent-model').value='real-b';const pending=evaluate('saveAgentSettings()');assert.equal(evaluate('state.agentSettingsBusy'),true);assert.equal(evaluate('agentCanSend()'),false);assert.deepEqual(calls,[{path:'/api/chat/settings',body:{model:'real-b',thinking:false}}]);release({ok:true,json:async()=>({configured:true,available:true,remote:true,model:'real-b',thinking:false,data_kind:'personal_source',personal_data_use:{version:'personal-retrieval-v1'}})});await pending;assert.equal(evaluate('state.agentConversationId'),'same-conversation');assert.equal(evaluate('state.agentTurns.length'),1);assert.equal(evaluate('state.agentDraft'),'保留未发草稿');assert.equal(evaluate('state.agentStatus.model'),'real-b');assert.equal(evaluate('state.agentStatus.thinking'),false);assert.equal(evaluate('agentCanSend()'),true);
""")

    def test_thinking_button_toggles_via_settings_and_busy_states_prevent_duplicates(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-FIXTURE',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={};state.agentStatus={configured:true,available:true,remote:true,provider:'DeepSeek',model:'real-a',thinking:false,data_kind:'personal_source',personal_data_use:{version:'personal-retrieval-v1'}};state.agentModels=[{id:'real-a'}];state.agentDraft='保留未发送问题';renderAssistant()");
const buttonMarkup=()=>element('#panel-assistant').innerHTML.match(/<button\b[^>]*id="agent-thinking"[^>]*>/)?.[0];
assert.ok(buttonMarkup(),'thinking is an interactive button');assert.match(buttonMarkup(),/type="button"/);assert.match(buttonMarkup(),/aria-pressed="false"/);assert.doesNotMatch(element('#panel-assistant').innerHTML,/<input[^>]*id="agent-thinking"|对话设置|agent-personal-consent/);
element('#agent-model').value='real-a';const calls=[];let release;context.fetch=(path,options)=>{calls.push({path,body:JSON.parse(options.body)});return new Promise(resolve=>{release=resolve;});};
for(const key of ['agentBusy','agentSettingsBusy','agentActionBusy','busy','agentHistoryLoadBusy','agentModelsBusy']){
 evaluate(`state.${key}=true;renderAssistant()`);assert.match(buttonMarkup(),/disabled/,key);element('#agent-thinking').listeners.click({preventDefault(){}});await Promise.resolve();assert.equal(calls.length,0,key);evaluate(`state.${key}=false`);
}
evaluate('renderAssistant()');element('#agent-thinking').setAttribute('aria-pressed','false');const pending=element('#agent-thinking').listeners.click({preventDefault(){}});assert.equal(evaluate('state.agentSettingsBusy'),true);assert.equal(evaluate('agentCanSend()'),false);assert.deepEqual(calls,[{path:'/api/chat/settings',body:{model:'real-a',thinking:true}}]);element('#agent-thinking').listeners.click({preventDefault(){}});assert.equal(calls.length,1);
release({ok:true,json:async()=>({configured:true,available:true,remote:true,provider:'DeepSeek',model:'real-a',thinking:true,data_kind:'personal_source',personal_data_use:{version:'personal-retrieval-v1'}})});await pending;for(let i=0;i<20&&evaluate('state.agentSettingsBusy');i++)await Promise.resolve();assert.equal(evaluate('state.agentSettingsBusy'),false);assert.equal(evaluate('state.agentStatus.thinking'),true);assert.match(buttonMarkup(),/aria-pressed="true"/);assert.equal(evaluate('state.agentDraft'),'保留未发送问题');assert.equal(evaluate('agentCanSend()'),true);
element('#agent-thinking').setAttribute('aria-pressed','true');const failed=element('#agent-thinking').listeners.click({preventDefault(){}});assert.deepEqual(calls[1],{path:'/api/chat/settings',body:{model:'real-a',thinking:false}});release({ok:false,status:503,text:async()=>JSON.stringify({error:'synthetic settings failure'})});await failed;for(let i=0;i<20&&evaluate('state.agentSettingsBusy');i++)await Promise.resolve();assert.equal(evaluate('state.agentSettingsBusy'),false);assert.equal(evaluate('state.agentStatus.thinking'),true);assert.match(buttonMarkup(),/aria-pressed="true"/);assert.ok(evaluate('state.agentModelsError'));assert.equal(evaluate('state.agentDraft'),'保留未发送问题');
""")

    def test_run_details_show_actual_event_timeline_usage_and_visible_provider_reasoning(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-FIXTURE',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={};state.agentStatus={configured:true,data_kind:'personal_source',personal_data_use:{version:'personal-retrieval-v1'}}");
const calls=[];context.fetch=async(path,options)=>{calls.push({path,body:options.body?JSON.parse(options.body):null});if(path==='/api/chat/runs?limit=20')return {ok:true,json:async()=>({runs:[{run_id:'RUN-1',status:'failed',model:'real-a',started_at:1792022400}],has_more:false})};return {ok:true,json:async()=>({run:{run_id:'RUN-1',status:'failed',model:'real-a',thinking:true},reasoning_notice:'供应商实际返回文本，不代表完整内部思考。',events:[{kind:'model.start',at:1792022400,sequence:1,model:'real-a'},{kind:'tool.end',tool_name:'query_my_records',elapsed_ms:10,status:'completed'},{kind:'model.end',usage:{prompt_tokens:123,completion_tokens:45,total_tokens:168},reasoning_content:'供应商可见推理 <script>bad()</script>',prompt:{private:'DO-NOT-RENDER'}},{kind:'error',code:'model_timeout',error:{message:'稍后重试<img src=x>'}}]})};};
await evaluate('refreshAgentRuns()');assert.equal(evaluate('state.agentRuns.length'),1);await evaluate("toggleAgentRun('RUN-1')");const markup=evaluate("renderAgentRunContent('RUN-1')");assert.match(markup,/模型请求/);assert.match(markup,/调用返回/);assert.match(markup,/本人教务记录/);assert.match(markup,/输入 123 tokens/);assert.match(markup,/输出 45 tokens/);assert.match(markup,/合计 168 tokens/);assert.match(markup,/model_timeout/);assert.match(markup,/供应商实际返回文本，不代表完整内部思考/);assert.match(markup,/&lt;script&gt;/);assert.doesNotMatch(markup,/<script>|<img|DO-NOT-RENDER|"private"/);assert.deepEqual(calls,[{path:'/api/chat/runs?limit=20',body:null},{path:'/api/chat/run',body:{run_id:'RUN-1'}}]);
evaluate("state.agentRunDetails['RUN-EMPTY']={run:{run_id:'RUN-EMPTY'},events:[]}");assert.match(evaluate("renderAgentRunContent('RUN-EMPTY')"),/未提供供应商可见推理文本/);
""")

    def test_delayed_model_and_run_metadata_never_repopulate_another_owner(self):
        self.run_js(r"""
const seed=owner=>evaluate(`clearWorkspace();state.auth={authenticated:true,identity:{student_id:'${owner}',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={};state.agentStatus={configured:true,data_kind:'personal_source',personal_data_use:{version:'personal-retrieval-v1'}}`);seed('PERSONAL-A');let release;context.fetch=()=>new Promise(resolve=>{release=resolve;});const directory=evaluate('refreshAgentModels(false)');seed('PERSONAL-B');release({ok:true,json:async()=>({models:[{id:'A-private-model'}]})});await directory;assert.equal(evaluate('state.agentModels.length'),0);
const detail=evaluate("toggleAgentRun('A-run')");seed('PERSONAL-C');release({ok:true,json:async()=>({run:{run_id:'A-run'},events:[{kind:'model.end',reasoning_content:'A private reasoning'}]})});await detail;assert.equal(evaluate('Object.keys(state.agentRunDetails).length'),0);assert.doesNotMatch(element('#panel-assistant').innerHTML,/A private reasoning/);
evaluate("state.agentRunDetails.secret={events:[{reasoning_content:'secret'}]};state.agentModels=[{id:'secret-model'}]");evaluate('clearSchoolWorkspace()');assert.equal(evaluate('Object.keys(state.agentRunDetails).length'),0);assert.equal(evaluate('state.agentModels.length'),0);
""")

    def test_chat_scroll_preserves_reader_position_pages_and_follows_only_when_expected(self):
        self.run_js(r"""
const scroll=element('#agent-dialogue-scroll');let top=0;scroll.scrollHeight=2000;scroll.clientHeight=400;Object.defineProperty(scroll,'scrollTop',{get:()=>top,set:value=>{top=Math.max(0,Math.min(value,scroll.scrollHeight-scroll.clientHeight));},configurable:true});
evaluate("state.boot={};state.auth={authenticated:true};renderAssistant()");assert.equal(top,1600);scroll.scrollTop=300;scroll.listeners.scroll();assert.equal(evaluate('state.agentScroll.nearBottom'),false);
evaluate("state.agentTurns=[{status:'completed',message:'新问题',reply:'新回答',tool_results:[],citations:[]}]");scroll.scrollHeight=2500;evaluate('renderAssistant()');assert.equal(top,300);assert.equal(evaluate('state.agentScroll.newMessages'),true);assert.match(element('#agent-latest').textContent,/有新回复/);
evaluate('scrollAgentDialogue(1)');assert.equal(top,640);evaluate('scrollAgentDialogue(-1)');assert.equal(top,300);evaluate("scrollAgentDialogue('latest')");assert.equal(top,2100);assert.equal(evaluate('state.agentScroll.newMessages'),false);
scroll.scrollTop=150;scroll.listeners.scroll();evaluate('renderAssistant()');assert.equal(top,150);evaluate('state.agentScroll.forceLatest=true;renderAssistant()');assert.equal(top,2100);
scroll.scrollHeight=2900;evaluate("state.agentTurns.push({pending:true,message:'本人新发的问题'});state.agentScroll.forceLatest=true;renderAssistant()");assert.equal(top,2500);
const css=fs.readFileSync(process.argv[1].replace(/app\.js$/,'styles.css'),'utf8');assert.match(css,/body:has\(#panel-assistant:not\(\[hidden\]\)\)[^}]*height: 100svh[^}]*overflow: hidden/);assert.match(css,/\.agent-dialogue[^}]*min-height: 0[^}]*overflow: auto/);assert.match(css,/\.agent-run-content[^}]*max-height: 340px[^}]*overflow: auto/);assert.match(element('#panel-assistant').innerHTML,/对话上下翻页/);
""")

    def test_scripted_personal_fixture_uses_preflight_and_preserves_streaming_overrides(self):
        import json
        from unittest.mock import patch
        from validation.run_personal_ui_fixture import ScriptedPersonalModel

        marker = "服务器已完成本轮本机范围检查（service_preflight get_my_context）：\n"
        messages = [{"role": "system", "content": marker + json.dumps({"operations": [
            {"operation": "academic", "target_term": "2025-2026-AW"},
            {"operation": "major_academic", "target_term": "ALL-HISTORY"}]})},
            {"role": "user", "content": "我的成绩怎么样"}]
        model = ScriptedPersonalModel().with_options("synthetic-only", True)
        first = model.complete(messages, [])
        call = first["tool_calls"][0]["function"]
        self.assertEqual(call["name"], "query_my_records")
        self.assertEqual(json.loads(call["arguments"])["operation"], "major_academic")
        self.assertEqual(json.loads(call["arguments"])["target_term"], "ALL-HISTORY")
        self.assertEqual(json.loads(call["arguments"])["query"], "")
        exact = model.complete(messages[:-1] + [{"role": "user", "content": "查询 SYNTHETIC-PE-REPEAT"}], [])
        self.assertEqual(json.loads(exact["tool_calls"][0]["function"]["arguments"])["query"], "SYNTHETIC-PE-REPEAT")
        identity = model.complete([{"role": "user", "content": "你是谁？"}], [])
        self.assertEqual(json.loads(identity["content"]), {"kind": "general_chat", "intent": "identity"})
        summary_reply = model.complete(messages + [first, {"role": "tool", "tool_call_id": "synthetic-summary",
            "content": json.dumps({"source_grade_summary": {"evidence_id": "S-FIXTURE", "available_sections": ["counts"]}})}], [])
        self.assertEqual(json.loads(summary_reply["content"]), {"kind": "answer", "claims": [
            {"kind": "source_summary", "evidence_id": "S-FIXTURE", "sections": ["counts"]}]})
        deltas, observed = [], []
        model.stream_delay = 0.3
        with patch("validation.run_personal_ui_fixture.time.sleep") as delay:
            message, metadata = model.complete_stream_with_metadata(messages, [], observer=observed.append,
                                                                    on_reasoning=deltas.append)
        self.assertEqual("".join(deltas), message["reasoning_content"])
        self.assertEqual(delay.call_count, 4)
        self.assertIn("合成", message["reasoning_content"])
        self.assertEqual(observed, [metadata])

        class MetadataOverride(ScriptedPersonalModel):
            def complete_with_metadata(self, messages, tools, observer=None):
                result = {"role": "assistant", "content": "custom", "reasoning_content": "custom visible reasoning"}
                metadata = {"status": "completed", "provider_reasoning": "custom visible reasoning", "elapsed_ms": 47,
                            "usage": {"total_tokens": 17}}
                if observer:
                    observer(metadata)
                return result, metadata

        custom, chunks, receipts = MetadataOverride(), [], []
        custom.thinking = True
        _, receipt = custom.complete_stream_with_metadata(messages, [], observer=receipts.append,
                                                          on_reasoning=chunks.append)
        self.assertEqual("".join(chunks), "custom visible reasoning")
        self.assertEqual(receipt["elapsed_ms"], 47)
        self.assertEqual(receipts[0]["usage"], {"total_tokens": 17})

if __name__ == "__main__":
    unittest.main()
