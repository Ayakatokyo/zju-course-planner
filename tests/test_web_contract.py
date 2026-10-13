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
const element=selector=>{if(!elements.has(selector))elements.set(selector,{innerHTML:'',hidden:false,value:'',textContent:'',className:'',addEventListener(){},focus(){},setAttribute(){},classList:{toggle(){}}});return elements.get(selector);};
const context=vm.createContext({document:{querySelector:element,querySelectorAll:()=>[],body:{append(){}},createElement:()=>({click(){},remove(){}})},fetch:async()=>{throw Error('unexpected fetch');},crypto:require('node:crypto'),URL:{createObjectURL:()=> 'blob:synthetic',revokeObjectURL(){}},setTimeout(){}});
let source=fs.readFileSync(process.argv[1],'utf8').replace(/\nbootstrap\(\);\s*$/,'');
vm.runInContext(source,context);
const evaluate=code=>vm.runInContext(code,context);
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
assert.equal(element('.sidebar').hidden,true);
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

    def test_agent_is_default_entry_and_examples_only_change_memory_draft(self):
        self.run_js("""
assert.equal(evaluate('state.tab'),'assistant');
const html=fs.readFileSync(process.argv[1].replace(/app\\.js$/,'index.html'),'utf8');
assert.match(html,/id="tab-assistant" class="nav-tab active"/);
assert.match(html,/条件与课表/);
evaluate("state.boot={demo:{courses:[],constraints:{revision:1,friday_free:'soft'}}};state.constraints={revision:1,friday_free:'soft'};renderAssistant()");
const markup=element('#panel-assistant').innerHTML;
assert.match(markup,/<button[^>]*type="submit"[^>]*disabled[^>]*>发送需求 · 待接入/);
assert.match(markup,/草稿仅保留在此页面，暂不发送/);
assert.match(markup,/澄清目标/);assert.match(markup,/计算工具/);assert.match(markup,/来源依据/);
evaluate('chooseAgentExample(1)');
assert.match(evaluate('state.agentDraft'),/保留已经确认的体育安排/);
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
assert.match(element('#panel-assistant').innerHTML,/data-action="preview-current-conditions" disabled/);
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
evaluate("state.boot={identity:{student_id:'DEMO-STUDENT-001'}};state.agentDraft='A 的可见草稿'");
element('#agent-draft').value='A 的可见草稿';
context.fetch=async()=>({ok:true,json:async()=>({identity:{student_id:'DEMO-STUDENT-002'},demo:{constraints:{revision:1}},saved_plans:[]})});
await evaluate('refreshWorkspace()');
assert.equal(evaluate('state.agentDraft'),'');
assert.equal(element('#agent-draft').value,'');
""")

    def test_chat_requires_model_and_remote_consent_and_sends_only_turn_contract(self):
        self.run_js("""
evaluate("state.auth={authenticated:true};state.boot={demo:{courses:[],constraints:{revision:3}}};state.constraints={revision:3};state.agentDraft='查询合成课程';state.csrfToken='memory-only'");
assert.equal(evaluate('agentCanSend()'),false);await evaluate('sendAgentTurn()');
evaluate("state.agentStatus={configured:true,provider:'DeepSeek',model:'test-model',remote:true,allow_reference_text:false}");
assert.equal(evaluate('agentCanSend()'),false);await evaluate('sendAgentTurn()');
const payloads=[];context.fetch=async(path,options)=>{assert.equal(path,'/api/chat/turn');assert.equal(options.headers['X-CSRF-Token'],'memory-only');const body=JSON.parse(options.body);payloads.push(body);return {ok:true,json:async()=>({conversation_id:'conv-A',turn_id:payloads.length,status:'completed',reply:'已查询',tool_results:[],citations:[],data_kind:'synthetic'})};};
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
assert.doesNotMatch(markup,/<script>|<img|href="javascript:/);assert.match(markup,/&lt;script&gt;/);assert.match(markup,/实际工具返回/);assert.match(markup,/只读预览，未改写本人工作区条件/);assert.match(markup,/周五必须空闲冲突/);assert.match(markup,/参考候选 · 待审核/);
assert.doesNotMatch(markup,/data-action="save-plan"|data-action="export"/);assert.equal(evaluate('state.constraints.revision'),5);assert.equal(evaluate('state.generated.existing'),'workspace');
""")

    def test_chat_plan_diagnostics_show_conflicts_and_unknown_before_collapsed_json(self):
        self.run_js("""
const markup=evaluate("agentToolCard({name:'generate_my_plan',arguments:{},result:{status:'scoped_infeasible',reason:'当前条件不兼容',plans:[],diagnostics:[{code:'friday_free',result:'fail',section_ids:['DEMO-ML-B'],reason:'锁定班与周五必须空闲冲突'},{code:'exam_schedule',result:'unknown',section_ids:['DEMO-UNKNOWN'],reason:'考试安排未提供'}]}})");
const visible=markup.split('<details>')[0];assert.match(visible,/锁定班与周五必须空闲冲突/);assert.match(visible,/DEMO-ML-B/);assert.match(visible,/检查代码：[\\s\\S]*friday_free/);assert.match(visible,/考试安排未提供/);assert.match(visible,/1 项不满足、1 项待确认/);
const validation=evaluate("agentToolCard({name:'validate_my_plan',arguments:{},result:{checks:[{code:'time_conflict',result:'fail',section_ids:['DEMO-A','DEMO-B'],reason:'时段冲突'}],plan:{validation:{checks:[{code:'time_conflict',result:'fail',section_ids:['DEMO-A','DEMO-B'],reason:'时段冲突'}]},sections:[]}}})");
assert.match(validation.split('<details>')[0],/时段冲突/);assert.match(validation,/1 项不满足、0 项待确认/);
const bounded=evaluate("agentPlanDiagnostics({diagnostics:[...Array.from({length:9},(_,i)=>({code:'fail-'+i,result:'fail',reason:'冲突'+i})),{code:'unknown-last',result:'unknown',reason:'最后的未知考试'}]})");
assert.equal((bounded.match(/<li>/g)||[]).length,8);assert.match(bounded,/最后的未知考试/);assert.match(bounded,/9 项不满足、1 项待确认/);assert.match(bounded,/此处展示 8 项，共 10 项/);
""")

    def test_chat_model_failure_keeps_tool_evidence_separate_from_model_reply(self):
        self.run_js("""
evaluate("state.boot={demo:{courses:[]}};state.agentTurns=[{message:'合成问题',status:'model_error',reply:'模型服务超时，已执行的工具结果如下。',tool_results:[{name:'get_my_context',arguments:{},result:{data_kind:'synthetic',unknown:['考试缺失']}}],citations:[]}];renderAssistant()");
const markup=element('#panel-assistant').innerHTML;assert.match(markup,/本轮状态/);assert.match(markup,/模型服务超时/);assert.match(markup,/实际工具返回/);assert.match(markup,/考试缺失/);assert.doesNotMatch(markup,/class="agent-message assistant"/);
""")

    def test_chat_reply_origin_labels_verified_results_clarification_and_partial_failure(self):
        self.run_js("""
evaluate("state.boot={demo:{courses:[]}};state.agentTurns=[{message:'查询',status:'completed',reply_origin:'verified_tool_presentation',reply:'状态待确认',tool_results:[],citations:[]},{message:'继续',status:'completed',reply_origin:'verified_clarification_prompt',reply:'请选择课程范围',tool_results:[],citations:[]},{message:'计算',status:'failed',partial_tools_completed:true,reply:'模型后续调用失败',tool_results:[{name:'get_my_context',arguments:{},result:{data_kind:'synthetic'}}],citations:[]}];renderAssistant()");
const markup=element('#panel-assistant').innerHTML;
assert.match(markup,/<strong>模型查询 · 程序核验结果<\\/strong>/);assert.match(markup,/<strong>待澄清<\\/strong>/);assert.match(markup,/工具已完成，本轮解释未完成/);assert.match(markup,/实际工具返回/);assert.doesNotMatch(markup,/模型解释与真实工具返回分别展示/);
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
evaluate("state.boot={identity:{student_id:'same-id',security_realm:'A'},demo:{}};state.agentTurns=[{message:'A question'}];state.agentConversationId='conv-A';state.agentConsent=true");
context.fetch=async()=>({ok:true,json:async()=>({identity:{student_id:'same-id',security_realm:'B'},demo:{constraints:{}}})});await evaluate('refreshWorkspace()');
assert.equal(evaluate('state.agentTurns.length'),0);assert.equal(evaluate('state.agentConversationId'),null);assert.equal(evaluate('state.agentConsent'),false);
evaluate("state.agentStatus={configured:true,provider:'DeepSeek',model:'old',remote:true};state.agentConsent=true");context.fetch=async()=>({ok:true,json:async()=>({configured:true,provider:'DeepSeek',model:'new',remote:true})});await evaluate('refreshAgentStatus()');assert.equal(evaluate('state.agentConsent'),false);
""")


if __name__ == "__main__":
    unittest.main()
