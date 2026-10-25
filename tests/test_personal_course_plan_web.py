"""Executable UI contracts for server-verified conditional course-data results.

All plan values below are synthetic. Live browser and personal semantics remain
separate acceptance work; rendering never executes another model/tool request.
"""
import json
import tempfile
import unittest

from course_assistant.personal_course_plans import CURRENT_TERM, NAMES, PersonalCoursePlanService
from tests.test_current_term_recommendation import fixture, write_delivery
from tests.test_personal_course_plans import sources
from tests import test_web_contract as web_contract


@unittest.skipUnless(web_contract.NODE, "Node is needed for Web contracts")
class PersonalCoursePlanWebTests(unittest.TestCase):
    def test_unified_model_answer_stays_visible_once_above_canonical_plan(self):
        self.run_js(r"""
evaluate("state.agentTurns=[{status:'completed',message:'说明取舍',reply:'合成直接答案：**优先A**，周五更空闲。\\n仍需核对正式适用。<script>unsafe</script>',model_decision:{kind:'answer',answer_protocol:'text-v1'},recommendation_result:plan,tool_results:[],citations:[]}];renderAssistant()");
const html=element('#panel-assistant').innerHTML;
assert.equal((html.match(/合成直接答案/g)||[]).length,1);
assert.match(html,/<strong>优先A<\/strong>/);
assert.match(html,/周五更空闲。<\/p><p>仍需核对正式适用。/);
assert.ok(html.indexOf('合成直接答案')<html.indexOf('personal-plan-option'));
assert.match(html,/&lt;script&gt;unsafe&lt;\/script&gt;/);
assert.equal((html.match(/class="personal-plan-option"/g)||[]).length,2);
assert.doesNotMatch(html,/agent-plan-analysis|查看依据|处理过程|条件与核验范围|运行记录/);
""")

    def run_js(self, assertions):
        fixture = r"""
context.plan={schema_version:'personal-course-data-test/1',action:'generate',mode:'current_term_course_data_test',target_term:'2026-2027-AW',data_version:'SYNTHETIC-DATA-v1',reference_mode:'historical_course_observations',revision:0,plan_ref:'PLN-SYNTHETIC-NOT-USER-FACING',source_preparation:{major_academic:'available',curriculum:'available',timetable:'available',exams:'not_requested'},search:null,courses:[],options:[{label:'A',total_credits:'6',observed_slot_result:'pass',full_semantics:'unverified',classes:[{class_ref:'CLS-SYNTHETIC-LOCKED',course_name:'合成体育',course_code:'SYN-PE',credits:'3',weekday_period_text:['周一第1、2节'],locked:true},{class_ref:'CLS-SYNTHETIC-MATH',course_name:'合成数学',course_code:'SYN-MATH',credits:'3',weekday_period_text:['周二第3、4节'],locked:false}],compared_meeting_pair_count:1,friday_meeting_count:0},{label:'B',total_credits:'5',observed_slot_result:'unknown',full_semantics:'unverified',classes:[{class_ref:'CLS-SYNTHETIC-UNKNOWN',course_name:'合成数据分析',course_code:'SYN-DATA',credits:'2',weekday_period_text:[],locked:false}],compared_meeting_pair_count:0,friday_meeting_count:1}],locked_class_refs:['CLS-SYNTHETIC-LOCKED'],fixed_arrangement_count:2,constraints:{target_credits:'6',max_credits:'8',friday_free:'soft',required_course_codes:['SYN-PE']},validation_scope:'observed_weekday_periods_conservative_overlap_policy',limitations:[],actions:['compare','revise'],reason:null,search_status:{limit_reached:true,complete_within_observed_scope:false,school_offerings_coverage_complete:false},selection_hint:null};
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-SYNTHETIC',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={};state.agentStatus={configured:true,available:true,remote:true,data_kind:'personal_source',personal_data_use:{version:'personal-retrieval-v1'}}");
"""
        web_contract.WebContractTests.run_js(self, fixture + assertions)

    def test_main_options_are_visible_without_diagnostics_or_actions(self):
        self.run_js(r"""
let calls=0;context.fetch=async()=>{calls++;throw Error('render must not send');};
evaluate("state.agentTurns=[{status:'completed',task_status:'partial',message:'用已有课程数据测试',reply:'已有条件测试结果。',recommendation_result:plan,tool_results:[{name:'generate_current_term_plan',result:{status:'available',evidence_id:'T-1',result:plan}}],citations:[{kind:'course_data_plan',evidence_id:'T-1',citation_number:1,course_data_plan:plan}]}];renderAssistant()");
const html=element('#panel-assistant').innerHTML;
assert.match(html,/<strong>浙大教务助手<\/strong>/);assert.match(html,/2 套课程参考方案/);assert.match(html,/2026-2027 秋冬历史课程参考/);assert.match(html,/已保留 1 项锁定 · 保留 2 项本人固定安排/);
assert.equal((html.match(/class="personal-plan-option"/g)||[]).length,2);assert.equal((html.match(/合成体育/g)||[]).length,1);assert.match(html,/已锁定/);assert.match(html,/周一第1、2节/);assert.match(html,/周五未见占用/);assert.match(html,/观察节次待确认/);assert.match(html,/正式适用未核/);assert.match(html,/搜索已达本轮上限/);
assert.doesNotMatch(html,/查看依据|处理过程|条件与核验范围|运行记录|agent-evidence|agent-process|agent-runs|\[1\]/);assert.equal(evaluate('plan.locked_class_refs.length'),1);assert.equal(evaluate('plan.constraints.required_course_codes[0]'),'SYN-PE');
assert.doesNotMatch(html,/PLN-SYNTHETIC|CLS-SYNTHETIC|SYNTHETIC-DATA-v1|save-agent-option|export-agent-option|已获学分|全局最优/);assert.equal(calls,0);assert.equal(evaluate('agentCanSend()'),false);
// With an unsent follow-up, terminal task incompleteness does not block input.
evaluate("state.agentDraft='锁定 A 中的合成体育';renderAssistant()");assert.equal(evaluate('agentCanSend()'),true);assert.doesNotMatch(element('#panel-assistant').innerHTML.match(/<textarea[^>]*>/)[0],/disabled/);
""")

    def test_saved_computation_projection_retains_tradeoffs(self):
        self.run_js(r"""
context.summary={origin:'owned_last_computation',credit_target:{minimum:'6',maximum:null},options:[{label:'A',total_credits:'7',excess_above_minimum:'1',soft_meeting_count:0,friday_meeting_count:0,locked_class_count:1}],ranking:{version:'current-term-observed-ranking/1',criteria:['observed_slot_result','soft_meeting_count','known_credits','excess_above_minimum','course_count','stable_observation_order']},exact_target:{status:'unknown'},scope:{input_course_count:3,returned_search_count:3,omitted_count:0,complete_within_input_scope:true}};
context.plan={...context.plan,action:'inspect',computation_summary:context.summary};
const html=evaluate("personalCoursePlanEvidence(plan)");
assert.match(html,/已保存的计算记录/);assert.match(html,/上限 未设置/);assert.match(html,/软空闲时段占用 0 段/);assert.match(html,/学分更接近目标/);assert.match(html,/其他组合未记录/);assert.match(html,/输入池 3 门/);assert.match(html,/本次未重新读取/);
""")

    def test_headline_uses_actual_credits_and_occupancy_without_promoting_unknowns(self):
        self.run_js(r"""
const before=evaluate('JSON.stringify(plan)');
let html=evaluate('personalPlanHeadline(plan,plan.options)');
assert.doesNotMatch(html,/均满足|周五均未见占用/); // B has only 5 credits.
assert.equal(evaluate('JSON.stringify(plan)'),before);
evaluate("plan.options[1].total_credits='7.5';plan.options[0].friday_meeting_count=1");
html=evaluate('personalPlanHeadline(plan,plan.options)');assert.match(html,/均满足至少 6 学分、不超过 8 学分/);assert.match(html,/周五都有占用，尚未实现周五空闲/);
for(const value of [null,'unknown','',false,'9']){
 context.value=value;evaluate('plan.options[1].total_credits=value');
 assert.doesNotMatch(evaluate('personalPlanHeadline(plan,plan.options)'),/均满足/);
}
evaluate("plan.options[1].total_credits='7.5';delete plan.options[1].friday_meeting_count");
html=evaluate('personalPlanHeadline(plan,plan.options)');assert.match(html,/周五占用仍待核对/);assert.doesNotMatch(html,/周五都有占用|周五均未见占用/);
evaluate("plan.options.forEach(o=>o.friday_meeting_count=0)");assert.match(evaluate('personalPlanHeadline(plan,plan.options)'),/按已有节次，周五均未见占用/);
""")

    def test_generated_plan_leads_with_result_without_intermediate_search_narration(self):
        self.run_js(r"""
evaluate("state.agentTurns=[{status:'completed',task_status:'partial',message:'推荐课程',reply:'逐次课程检索的重复说明',recommendation_result:plan,tool_results:[],citations:[]}]");
const html=evaluate('renderAgentTurns()');
assert.match(html,/2 套课程参考方案/);assert.doesNotMatch(html,/逐次课程检索的重复说明|查看依据|处理过程|条件与核验范围/);assert.doesNotMatch(html,/查询学期以本轮来源范围为准/);assert.match(html,/正式适用未核/);assert.match(html,/搜索已达本轮上限/);
""")

    def test_evidence_analysis_remains_visible_with_plan_without_intermediate_narration(self):
        self.run_js(r"""
evaluate("state.agentTurns=[{status:'completed',task_status:'partial',message:'结合已修和培养推荐',reply:'逐次检索说明；合成培养条款支持这一方向。',model_decision:{kind:'answer',evidence_analysis:'合成培养条款支持这一方向。 [1] <script>unsafe</script>'},recommendation_result:plan,tool_results:[],citations:[]}]");
const html=evaluate('renderAgentTurns()'),main=html;
assert.match(main,/2 套课程参考方案/);assert.match(main,/合成培养条款支持这一方向/);
assert.doesNotMatch(main,/逐次检索说明|<script>/);assert.match(main,/&lt;script&gt;/);
assert.doesNotMatch(html,/查看依据|处理过程|条件与核验范围/);
""")

    def test_failed_or_unselected_tool_result_does_not_become_a_main_answer(self):
        self.run_js(r"""
evaluate("state.agentTurns=[{status:'failed',task_status:'partial',message:'计算',reply:'模型答复未完成。',partial_tools_completed:true,tool_results:[{name:'generate_current_term_plan',result:{status:'available',evidence_id:'T-1',result:plan}}],citations:[{kind:'course_data_plan',evidence_id:'T-1',citation_number:1,course_data_plan:plan}]}]");
const html=evaluate('renderAgentTurns()');assert.doesNotMatch(html,/class="personal-course-plans"|class="personal-plan-option"/);assert.match(html,/模型答复未完成/);assert.doesNotMatch(html,/本轮已完成的工具结果如下|已执行条件测试计算|返回 2 套条件测试方案/);assert.doesNotMatch(html,/查看依据|处理过程|条件与核验范围/);
""")

    def test_actual_owned_lock_survives_source_failure_without_ui_zero_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            service = PersonalCoursePlanService(write_delivery(directory, fixture()), CURRENT_TERM,
                lambda: {"scope_key": "SYNTHETIC-OWNER-GENERATION"})
            turn = []
            service.execute(NAMES[0], {"query": "Synthetic", "target_term": CURRENT_TERM}, turn_identity=turn)
            first = service.execute(NAMES[1], {"recommendation_kind": "timetable", "target_term": CURRENT_TERM,
                "course_codes": ["SYN-A", "SYN-B", "SYN-C"], "target_credits": 5, "friday_free": "soft"},
                sources=sources(timetable=True), turn_identity=turn)["result"]
            ref = next(row["class_ref"] for option in first["options"] for row in option["classes"]
                       if row["course_code"] == "SYN-A")
            policy = {"user_message": "必须保留Synthetic SYN-A", "active_plan_ref": first["plan_ref"],
                      "hard_preferences": [], "soft_preferences": []}
            service.execute(NAMES[2], {"plan_ref": first["plan_ref"], "action": "revise", "expected_revision": 0,
                "lock_class_refs": [ref]}, sources=sources(timetable=True), policy_context=policy)
            unavailable = sources(timetable=True)
            unavailable["curriculum"] = {"status": "failed", "verified": False}
            blocked = service.execute(NAMES[2], {"plan_ref": first["plan_ref"], "action": "revise",
                "expected_revision": 1}, sources=unavailable, policy_context={**policy, "user_message": "继续重算"})["result"]
        self.assertEqual(blocked["locked_class_refs"], [ref])
        self.run_js("context.blocked=" + json.dumps(blocked, ensure_ascii=False) + ";" + r"""
evaluate("state.agentTurns=[{status:'completed',task_status:'blocked',message:'继续重算',reply:'本轮所需来源未取得。',recommendation_result:blocked,tool_results:[],citations:[]}];renderAssistant()");
const html=element('#panel-assistant').innerHTML;assert.match(html,/本次调整尚未执行/);assert.match(html,/原锁定 1 项保留/);assert.match(html,/本人课表固定项与用户禁排时间尚未完成本轮核对/);assert.doesNotMatch(html,/保留锁定 0 项|本人课表固定 0 项|用户禁排时间 0 项|已按调整条件重新计算|class="personal-plan-option"|PLN-|CLS-/);
// A stored pre-fix blocked turn may have plan/ref but only placeholder locks.
evaluate("blocked.locked_class_refs=[]");const historical=evaluate('renderPersonalCoursePlans(blocked)');assert.match(historical,/锁定状态待确认/);assert.doesNotMatch(historical,/原锁定 0 项保留|保留锁定 0 项/);
evaluate("blocked.plan_ref=null;blocked.revision=null;blocked.locked_class_refs=[]");const initial=evaluate('renderPersonalCoursePlans(blocked)');assert.match(initial,/锁定状态待确认/);assert.doesNotMatch(initial,/原锁定 0 项保留|保留锁定 0 项/);
""")

    def test_computed_no_option_counts_and_unapplied_upgrade_attempt_are_distinct(self):
        self.run_js(r"""
evaluate("plan.action='revise';plan.options=[];plan.reason='no_observed_option_within_scope';plan.fixed_arrangement_count=1;plan.user_busy_arrangement_count=2");let html=evaluate('renderPersonalCoursePlans(plan)');assert.match(html,/保留锁定 1 项 · 本人课表固定 1 项 · 用户禁排时间 2 项/);assert.doesNotMatch(html,/尚未执行|尚未完成本轮核对/);assert.match(evaluate('personalCoursePlanEvidence(plan)'),/已按调整条件重新计算/);
// Failed courses→timetable upgrade computes but keeps the old plan revision.
evaluate("plan.action='generate';plan.recommendation_kind='courses';plan.constraints=null;plan.search_status=null;plan.fixed_arrangement_count=0;plan.user_busy_arrangement_count=0");html=evaluate('renderPersonalCoursePlans(plan)');assert.match(html,/本次测试已计算，未找到方案；原方案未更新/);assert.match(html,/原锁定 1 项保留/);assert.match(html,/本次计算未提供固定项与用户禁排时间计数/);assert.doesNotMatch(html,/尚未计算|固定 0 项|禁排时间 0 项/);
""")

    def test_soft_ranking_is_shown_only_for_consistent_actual_evaluation(self):
        self.run_js(r"""
assert.doesNotMatch(evaluate('renderPersonalCoursePlans(plan)'),/软空闲偏好：|偏好时段占用合计|先减少偏好时段占用/);
evaluate("plan.constraints.soft_free_slots=[{weekday:5,periods:null}];plan.options.forEach((o,i)=>o.soft_preference_evaluation={status:'evaluated',method:'observed_meeting_overlap_count',violation_count:i,scopes:[{weekday:5,periods:null,meeting_count:i}]})");assert.doesNotMatch(evaluate('renderPersonalCoursePlans(plan)'),/偏好时段占用/);
evaluate("plan.constraints.friday_free='hard';plan.constraints.soft_free_slots=[{weekday:4,periods:null}];plan.options.forEach((o,i)=>o.soft_preference_evaluation={status:'evaluated',method:'observed_meeting_overlap_count',violation_count:i,scopes:[{weekday:4,periods:null,meeting_count:i}]})");
let html=evaluate('renderPersonalCoursePlans(plan)');assert.match(html,/已保留 1 项锁定/);assert.match(html,/偏好时段占用：周四全天 0 段/);assert.match(html,/偏好时段占用：周四全天 1 段/);assert.doesNotMatch(html,/条件与核验范围|先减少偏好时段占用/);
evaluate("plan.action='compare'");html=evaluate('renderPersonalCoursePlans(plan)');assert.match(html,/偏好时段占用：周四全天 1 段/);assert.doesNotMatch(html,/先减少偏好时段占用/);
const saved=evaluate('JSON.stringify(plan.options[0].soft_preference_evaluation)');
for(const change of ["plan.options[0].soft_preference_evaluation.violation_count=8","plan.options[0].soft_preference_evaluation.status='pending'","plan.options[0].soft_preference_evaluation.scopes[0].periods=['<svg>']","delete plan.options[0].soft_preference_evaluation"]){
 evaluate('plan.options[0].soft_preference_evaluation=JSON.parse('+JSON.stringify(saved)+')');evaluate(change);
 assert.equal(evaluate('personalSoftPreferenceView(plan,plan.options[0])'),null);html=evaluate('renderPersonalCoursePlans(plan)');assert.doesNotMatch(html,/<svg>|周四全天 8 段/);
}
""")

    def test_partial_and_blocked_task_status_are_distinct_from_tool_completion(self):
        self.run_js(r"""
for(const [task,label] of [['partial','已有部分结果'],['blocked','任务受阻']]){
 context.task=task;evaluate("state.agentTurns=[{status:'completed',task_status:task,request_id:'REQUEST-SYN',run_id:'RUN-SYN',message:'测试',reply:'本轮说明',progress:{status:'running',confirmed:true,events:[{kind:'tool.end',tool_name:'generate_current_term_plan',status:'completed'}]},tool_results:[],citations:[]}]");
 let html=evaluate('renderAgentTurns()');assert.ok(html.includes('<strong>'+label+'</strong>'));assert.doesNotMatch(html,/处理过程|agent-process/);assert.doesNotMatch(html,/<details class="agent-process"[^>]*\sopen[\s>]|本轮处理完成/);
 evaluate('delete state.agentTurns[0].progress');html=evaluate('renderAgentTurns()');assert.doesNotMatch(html,/处理过程|agent-process/);
}
const running=evaluate("agentProcessStage({status:'running',events:[{kind:'tool.end',tool_name:'generate_current_term_plan',status:'completed'}]})");assert.match(running,/本次工具已返回，继续检查答复/);assert.doesNotMatch(running,/本轮处理完成|任务完成/);
""")

    def test_observed_colisted_code_is_visible_without_identity_or_private_promotion(self):
        self.run_js(r"""
evaluate("plan.action='search';plan.options=[];plan.courses=[{course_code:'SYN-A',course_name:'合成候选',credits:'3',observed_code_links:[{code:'99123450',relation_kind:'same_source_header',verification_status:'observed',source_field:'display_code'}]}]");
let html=evaluate('personalCoursePlanEvidence(plan)');assert.match(html,/同一来源标题另列代码 99123450/);assert.match(html,/课程等价与本人适用未核实/);assert.doesNotMatch(html,/已核实新旧|已核实等价/);
for(const mutation of ["plan.courses[0].observed_code_links[0].verification_status='verified'","plan.courses[0].observed_code_links[0].code='<svg>'","plan.courses[0].observed_code_links[0].code='12345678901'","delete plan.courses[0].observed_code_links"]){
 evaluate(mutation);html=evaluate('personalCoursePlanEvidence(plan)');assert.doesNotMatch(html,/同一来源标题另列代码|<svg>|12345678901/);
}
""")

    def test_selection_inspection_never_replays_old_course_facts_or_refs(self):
        self.run_js(r"""
evaluate("plan.action='inspect';plan.options=[];plan.selection_hint={plan_ref:plan.plan_ref,revision:0,options:[{label:'A',classes:[{class_ref:'CLS-PRIVATE-OLD',course_name:'PRIVATE-OLD-COURSE',course_code:'PRIVATE-OLD-CODE',locked:true}]}],selection_only:true,fresh_sources_required:true}");
const evidence=evaluate("agentCitation({kind:'course_data_plan',evidence_id:'T-2',citation_number:2,course_data_plan:plan})");
assert.match(evidence,/\[2\] 课程数据测试依据/);assert.match(evidence,/已定位上一轮方案的选择信息/);assert.match(evidence,/仍需本轮资料核查/);assert.doesNotMatch(evidence,/PRIVATE-|PLN-|CLS-|本轮可用|方案 A/);assert.equal(evaluate('renderPersonalCoursePlans(plan)'),'');
""")

    def test_search_evidence_is_a_candidate_result_and_unknown_reason_stays_safe(self):
        self.run_js(r"""
evaluate("plan.action='search';plan.options=[];plan.courses=[{course_name:'合成候选',course_code:'SYN-C',credits:'3'}]");
const evidence=evaluate('personalCoursePlanEvidence(plan)');assert.match(evidence,/返回 1 门课程观察候选，尚未生成方案/);assert.match(evidence,/合成候选 · SYN-C · 3 学分/);assert.doesNotMatch(evidence,/PLN-|CLS-|已推荐|已生成/);assert.equal(evaluate('renderPersonalCoursePlans(plan)'),'');
evaluate("plan.action='generate';plan.reason='PRIVATE-UNKNOWN-REASON';plan.options=[]");let html=evaluate('renderPersonalCoursePlans(plan)');assert.match(html,/本轮尚无可展示的测试方案/);assert.doesNotMatch(html,/PRIVATE-UNKNOWN-REASON|class="personal-plan-option"/);
evaluate("plan.reason='timetable_not_complete'");html=evaluate('renderPersonalCoursePlans(plan)');assert.match(html,/课表来源尚不完整/);assert.doesNotMatch(html,/已完成排课/);
""")

    def test_course_names_periods_and_constraints_are_escaped_without_json_dump(self):
        self.run_js(r"""
evaluate("plan.options[0].classes[0].course_name='<img src=x onerror=bad()>';plan.options[0].classes[0].course_code='<script>bad()</script>';plan.options[0].classes[0].weekday_period_text=['<svg onload=bad()>'];plan.constraints.required_course_codes=['<button onclick=bad()>'];plan.source_preparation.curriculum='PRIVATE-RAW-STATE'");
const html=evaluate('renderPersonalCoursePlans(plan)');assert.doesNotMatch(html,/<img|<script|<svg|<button/);assert.match(html,/&lt;img/);assert.match(html,/&lt;svg/);assert.doesNotMatch(html,/"schema_version"|"class_ref"|"plan_ref"/);
const evidence=evaluate('personalCoursePlanEvidence(plan)');assert.match(evidence,/培养资料：待确认/);assert.doesNotMatch(evidence,/PRIVATE-RAW-STATE/);
assert.equal(evaluate("renderPersonalCoursePlans({...plan,schema_version:'unrecognized'})"),'');
evaluate('plan.options=plan.options.slice(0,1)');assert.doesNotMatch(evaluate('renderPersonalCoursePlans(plan)'),/比较 A 和 B/);
""")

    def test_preparation_and_current_term_tools_have_real_process_names(self):
        self.run_js(r"""
const labels={prepare_recommendation_inputs:'准备推荐所需来源',search_current_term_courses:'本学期课程观察检索',generate_current_term_plan:'课程数据测试计算',update_current_term_plan:'测试方案复核与调整',get_current_term_plan:'定位当前方案选择'};
for(const [name,label] of Object.entries(labels)){
 context.tool=name;assert.equal(evaluate('agentToolName(tool)'),label);const markup=evaluate("agentRunEventMarkup({kind:'tool.end',tool_name:tool,status:'available'})");assert.ok(markup.includes(label));assert.match(markup,/已返回/);assert.doesNotMatch(markup,/受限资料工具|任务完成|脚本执行/);
 const stage=evaluate("agentProcessStage({status:'running',events:[{kind:'tool.start',tool_name:tool}]})");assert.doesNotMatch(stage,/正在检索当前本人资料/);
}
""")

    def test_user_busy_time_and_unresolved_hard_conditions_keep_distinct_provenance(self):
        self.run_js(r"""
evaluate("plan.options=[];plan.reason='hard_preferences_unresolved';plan.fixed_arrangement_count=1;plan.user_busy_arrangement_count=2;plan.policy_status={status:'needs_clarification',unresolved:[{text:'周二下午必须空',code:'clock_period_mapping_unverified'},{text:'<img src=x>原硬条件',code:'PRIVATE-UNKNOWN-CODE'}],enforced_hard_preferences:['最多8学分'],unhandled_soft_preferences:[]}");
const html=evaluate('renderPersonalCoursePlans(plan)');assert.match(html,/本次条件测试尚未计算/);assert.match(html,/原锁定 1 项保留/);assert.match(html,/尚未完成本轮核对/);assert.match(html,/条件测试状态/);assert.match(html,/硬条件尚未完整解析/);assert.match(html,/周二下午必须空/);assert.match(html,/钟点与学校节次的对应关系/);assert.doesNotMatch(html,/需求记录|最多8学分/);assert.match(html,/&lt;img src=x&gt;原硬条件/);assert.doesNotMatch(html,/已纳入任务的硬条件|<img|PRIVATE-UNKNOWN-CODE|clock_period_mapping_unverified|本人课表固定 1 项|用户禁排时间 2 项|固定 3 项|class="personal-plan-option"/);
const outsideRecords=html.replace(/<details\b[^>]*>[\s\S]*?<\/details>/g,'');assert.match(outsideRecords,/硬条件尚未完整解析/);assert.match(outsideRecords,/周二下午必须空/);assert.match(outsideRecords,/钟点与学校节次的对应关系/);assert.match(outsideRecords,/&lt;img src=x&gt;原硬条件/);
const evidence=evaluate('personalCoursePlanEvidence(plan)');assert.match(evidence,/条件测试尚未计算/);assert.doesNotMatch(evidence,/已执行条件测试计算|已按调整条件重新计算/);
evaluate("plan.action='inspect';plan.selection_hint={options:[{classes:[{course_name:'PRIVATE-OLD-COURSE',weekday_period_text:['PRIVATE-OLD-TIME']}]}]}");const inspected=evaluate('personalCoursePlanEvidence(plan)');assert.match(inspected,/周二下午必须空/);assert.doesNotMatch(inspected,/PRIVATE-OLD-/);
""")

    def test_unhandled_soft_preferences_are_visible_without_becoming_hard_or_blocking_results(self):
        self.run_js(r"""
evaluate("plan.user_busy_arrangement_count=0;plan.policy_status={status:'ready',unresolved:[],enforced_hard_preferences:[],unhandled_soft_preferences:['尽量集中在上午','<svg>偏好']}");const before=evaluate('JSON.stringify(plan.constraints)');
const html=evaluate('renderPersonalCoursePlans(plan)');assert.match(html,/以下软偏好尚未参与排序：尽量集中在上午；&lt;svg&gt;偏好/);assert.equal((html.match(/class="personal-plan-option"/g)||[]).length,2);assert.doesNotMatch(html,/用户禁排时间 0 项/);assert.doesNotMatch(html,/硬条件尚未完整解析|已纳入任务的硬条件|<svg>/);assert.equal(evaluate('JSON.stringify(plan.constraints)'),before);
""")

    def test_historical_lock_request_is_absent_after_unlock_without_rewriting_conditions(self):
        self.run_js(r"""
evaluate("plan.action='revise';plan.locked_class_refs=[];plan.options.forEach(option=>option.classes.forEach(row=>row.locked=false));plan.constraints.max_credits='7';plan.user_busy_arrangement_count=1;plan.policy_status={status:'ready',unresolved:[],enforced_hard_preferences:['之前的锁定必须保留','<img src=x onerror=bad()>历史需求'],unhandled_soft_preferences:['尽量集中在上午']}");
const before=evaluate('JSON.stringify(plan)'),html=evaluate('renderPersonalCoursePlans(plan)');
assert.doesNotMatch(html,/需求记录|之前的锁定|历史需求|已纳入任务的硬条件|硬条件尚未完整解析|<img|onerror="|已锁定|保留锁定 0 项/);
assert.match(html,/以下软偏好尚未参与排序：尽量集中在上午/);assert.match(html,/保留 2 项本人固定安排 · 保留 1 项禁排时间/);assert.equal(evaluate('JSON.stringify(plan)'),before);
""")

    def test_policy_blocked_reasons_are_readable_without_claiming_a_computation(self):
        self.run_js(r"""
const reasons={hard_preferences_unresolved:'硬条件待明确',change_evidence_required:'本轮要修改哪项条件',policy_context_required:'当前任务条件尚未建立',locked_scope_requires_revision:'涉及已锁定课程'};
for(const [reason,text] of Object.entries(reasons)){context.reason=reason;evaluate("plan.reason=reason;plan.options=[]");const html=evaluate('renderPersonalCoursePlans(plan)');assert.ok(html.includes(text));assert.ok(!html.includes(reason));assert.doesNotMatch(evaluate('personalCoursePlanEvidence(plan)'),/已执行条件测试计算/);}
""")

    def test_course_combination_and_timetable_scope_are_not_conflated(self):
        self.run_js(r"""
evaluate("plan.recommendation_kind='courses';plan.fixed_arrangement_count=0");let html=evaluate('renderPersonalCoursePlans(plan)');assert.match(html,/<h3>2 套课程组合参考方案<\/h3>/);assert.match(html,/未核本人课表及考试冲突/);assert.doesNotMatch(html,/本人课表固定 0 项|本人无课|已避开本人/);assert.equal((html.match(/class="personal-plan-option"/g)||[]).length,2);
evaluate("plan.action='compare'");assert.match(evaluate('renderPersonalCoursePlans(plan)'),/<h3>2 套课程组合参考方案<\/h3>/);
evaluate("plan.recommendation_kind='timetable';plan.fixed_arrangement_count=1");html=evaluate('renderPersonalCoursePlans(plan)');assert.match(html,/<h3>2 套课表参考方案<\/h3>/);assert.match(html,/保留 1 项本人固定安排/);assert.doesNotMatch(html,/未核本人课表/);
evaluate('delete plan.recommendation_kind');assert.match(evaluate('renderPersonalCoursePlans(plan)'),/<h3>2 套课程参考方案<\/h3>/);
evaluate("plan.recommendation_kind='courses';plan.options=[]");assert.match(evaluate('renderPersonalCoursePlans(plan)'),/<h3>课程组合条件测试状态<\/h3>/);
""")

    def test_history_restores_main_plan_without_recomputation_or_save_export(self):
        self.run_js(r"""
const paths=[];context.fetch=async(path)=>{paths.push(path);assert.equal(path,'/api/chat/history');return {ok:true,json:async()=>({conversation_id:'CHAT-SYN',turn_id:3,data_kind:'personal_source',turns:[{turn_id:3,status:'completed',task_status:'partial',is_stale:true,message:'上次测试',reply:'历史条件测试结果',recommendation_result:context.plan,tool_results:[],citations:[]}]})};};
await evaluate("loadAgentConversation('CHAT-SYN')");const html=element('#panel-assistant').innerHTML;assert.doesNotMatch(html,/历史条件测试结果|查看依据|处理过程|运行记录/);assert.match(html,/历史测试结果，来源版本已变化/);assert.equal((html.match(/class="personal-plan-option"/g)||[]).length,2);assert.match(html,/正式适用未核/);assert.deepEqual(paths,['/api/chat/history']);assert.doesNotMatch(html,/save-agent-option|export-agent-option|PLN-SYNTHETIC|CLS-SYNTHETIC/);
""")


if __name__ == "__main__":
    unittest.main()
