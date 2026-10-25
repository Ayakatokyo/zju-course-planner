"""Browser-independent contracts for personal in-message progress only."""
import unittest

from tests import test_web_contract as web_contract


@unittest.skipUnless(web_contract.NODE, "Node is needed only for Web progress contracts")
class PersonalProgressWebTests(unittest.TestCase):
    def test_grade_table_merges_overlapping_source_rows_but_keeps_distinct_attempts(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={}");
const record={course_name_raw:'合成数据课',course_code_raw:'SYN-A',grade_raw:'88',academic_year_raw:'2099-2100',term_raw:'1'};
const citation={kind:'record',operation:'major_academic',observation_id:'OBS-synthetic',source_record_index:3,record};
context.turn={status:'completed',message:'列出成绩明细',reply:'见来源表。',model_decision:{answer_protocol:'text-v1'},citations:[citation,{...citation,evidence_id:'second-query'},{...citation,source_record_index:8}]};
const original=JSON.stringify(context.turn);evaluate('state.agentTurns=[turn]');let html=evaluate('renderAgentTurns()');
assert.equal((html.match(/SYN-A/g)||[]).length,2);assert.match(html,/本答复展示 2 条/);assert.match(html,/2099-2100 · 学期 1/);assert.equal(JSON.stringify(context.turn),original);
// Historical entries without stable positions and different snapshots stay separate.
delete context.turn.citations[1].source_record_index;
assert.equal((evaluate('renderAgentTurns()').match(/SYN-A/g)||[]).length,3);
context.turn.citations[1]={...citation,observation_id:'OBS-another'};
assert.equal((evaluate('renderAgentTurns()').match(/SYN-A/g)||[]).length,3);
""")

    def run_js(self, assertions):
        web_contract.WebContractTests.run_js(self, assertions)

    def test_repeated_answer_citation_is_displayed_once_without_rewriting_evidence(self):
        self.run_js(r"""
const turn={status:'completed',reply:'未知不代表未通过。[1] [1]\n\n成绩值另行核对。[1] [2] [2]\n原文编号[8] [8]',citations:[{citation_number:1},{citation_number:2}]};
context.citationTurn=turn;const original=JSON.stringify(turn);
assert.equal(evaluate('agentDisplayReply(citationTurn)'),'未知不代表未通过。[1]\n\n成绩值另行核对。[1] [2]\n原文编号[8] [8]');
assert.equal(JSON.stringify(turn),original);
""")

    def test_unknown_thinking_state_cannot_be_saved_as_false(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-A',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={};state.agentStatus={configured:true,available:true,model:'deepseek-flash',data_kind:'personal_source',personal_data_use:{version:'personal-retrieval-v1'}};state.agentModels=[{id:'deepseek-flash'}]");
element('#agent-model').value='deepseek-flash';let calls=0;context.fetch=async()=>{calls++;throw Error('unknown mode must not update settings');};
await evaluate('saveAgentSettings()');assert.equal(calls,0);assert.match(evaluate('renderAgentThinking()'),/disabled/);
""")

    def test_grade_detail_is_a_table_without_duplicate_prose_or_citation_markers(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-A',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={}");
const row={course_name_raw:'合成课<script>x</script>',course_code_raw:'SAME',grade_raw:'90',grade_points_raw:'unknown',nominal_credits:'2',term_raw:'unknown'};
context.turn={status:'completed',message:'把春夏学期的课程成绩明细列出来',reply:'原答复逐项长说明',citations:[{kind:'record',operation:'academic',target_term:'2025-2026-SS',data_source:'live',record:row,citation_number:1},{kind:'record',operation:'academic',target_term:'2025-2026-SS',data_source:'live',record:{...row,grade_raw:'80'},citation_number:2}]};
let calls=0;context.fetch=async()=>{calls++;throw Error('render must not query');};
evaluate('state.agentTurns=[turn]');const html=evaluate('renderAgentTurns()');
assert.match(html,/agent-grade-answer/);assert.doesNotMatch(html,/原答复逐项长说明|查看依据|处理过程|\[1\]|\[2\]/);
assert.match(html,/2025-2026 春夏 · 成绩明细/);assert.match(html,/本答复展示 2 条/);assert.match(html,/来源绩点/);assert.match(html,/名义学分/);assert.match(html,/待确认/);assert.doesNotMatch(html,/<script>/);
assert.equal((html.match(/SAME/g)||[]).length,2);assert.equal(context.turn.citations.length,2);assert.equal(calls,0);
context.turn.status='failed';assert.equal(evaluate('personalGradeAnswer(turn)'),null);
context.turn.status='completed';context.turn.message='为什么这门课有两条成绩';assert.equal(evaluate('personalGradeAnswer(turn)'),null);
""")

    def test_direct_grade_prose_replaces_only_bound_duplicate_table_and_keeps_explanation(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-A',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={}");
const row={course_name_raw:'合成数据课',course_code_raw:'SYN-A',grade_raw:'88',grade_points_raw:null,nominal_credits:'2'};
context.turn={status:'completed',message:'把相关课程成绩列出来',model_decision:{answer_protocol:'text-v1'},
reply:'这些是按课程名筛选的记录。\n| 课程 | 成绩 | 绩点 | 学分 |\n|---|---|---|---|\n| **合成数据课** | 99 | 4.8 | 2 |\n\n未知不等于未通过。<script>unsafe</script>',
citations:[{kind:'record',operation:'major_academic',record:row},{kind:'record',operation:'major_academic',record:{...row,grade_raw:null}}]};
const before=JSON.stringify(context.turn);evaluate('state.agentTurns=[turn]');let html=evaluate('renderAgentTurns()');
assert.equal((html.match(/<table/g)||[]).length,1);assert.doesNotMatch(html,/\|---|\| 99|4\.8|<script>/);
assert.match(html,/>88</);assert.match(html,/未知不等于未通过/);assert.match(html,/这些是按课程名筛选/);assert.match(html,/待确认/);
assert.equal((html.match(/SYN-A/g)||[]).length,2);assert.equal(JSON.stringify(context.turn),before);
// A supporting catalog/context citation must not suppress the source table.
context.turn.citations.push({kind:'course_reference',course:{course_name:'合成数据课'}},{kind:'context'});
html=evaluate('renderAgentTurns()');assert.equal((html.match(/<table/g)||[]).length,1);
assert.match(html,/>88</);assert.doesNotMatch(html,/\| 99/);
// Other tables, uncited courses, and questions with no source table are kept.
context.turn.reply='| 课程 | 成绩 |\n|---|---|\n| 未引用课程 | 99 |';
assert.match(evaluate('renderAgentTurns()'),/未引用课程/);
context.turn.reply='| 方案 | 学分 |\n|---|---|\n| A | 6 |';assert.match(evaluate('renderAgentTurns()'),/<th scope="col">方案<\/th>/);assert.match(evaluate('renderAgentTurns()'),/<tr><td>A<\/td><td>6<\/td><\/tr>/);
context.turn.message='解释成绩';assert.equal(evaluate('personalGradeAnswer(turn)'),null);
""")

    def test_enabled_thinking_without_text_is_distinct_from_normal_mode(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-A',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={}");
const html=evaluate("renderAgentProcess({run_id:'SYNTHETIC-RUN',status:'completed',progress:{status:'completed',confirmed:true,thinking:true,events:[]}})");
assert.match(html,/已开启深度思考，但本轮未收到/);assert.match(html,/class="agent-process-provider"/);assert.doesNotMatch(html,/普通模式/);
""")

    def test_folded_process_keeps_a_read_only_retry_when_history_load_failed(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-A',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={}");
const html=evaluate("renderAgentProcess({run_id:'RUN-FAILED-LOAD',status:'completed',progress_error:'本轮运行过程暂未载入',progress:{status:'completed',confirmed:true,thinking:false,events:[]}})");
assert.match(html,/data-action="agent-process-load" data-run-id="RUN-FAILED-LOAD">重新读取处理记录/);
assert.doesNotMatch(html,/agent-run-details|agent-process-provider/);
""")

    def test_provider_402_remains_actionable_after_history_load_without_claiming_data_queries(self):
        self.run_js(r"""
let requests=0;context.fetch=async()=>{requests++;throw Error('render cannot retry');};
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-A',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={};state.agentTurns=[{status:'failed',code:'model_http_402',message:'课程组合',reply:'可查看已完成的本机查询结果后重试。',partial_tools_completed:true,tool_results:[{name:'get_my_context',result:{status:'available'}}],citations:[]}];renderAssistant()");
let html=element('#panel-assistant').innerHTML;assert.match(html,/模型服务返回 HTTP 402/);assert.match(html,/核对模型账户计费状态后再试/);assert.doesNotMatch(html,/本轮已完成的工具结果如下|查看依据|处理过程|运行记录/);assert.doesNotMatch(html,/已完成的查询结果|已完成的本机查询结果|学校没有数据|已生成|class="personal-plan-option"/);
evaluate("state.agentTurns[0].progress={status:'failed',events:[{kind:'error',error_code:'model_http_402'}]};renderAssistant()");
html=element('#panel-assistant').innerHTML;assert.match(html,/模型服务返回 HTTP 402/);assert.equal(requests,0);
""")

    def test_progress_has_one_status_line_and_final_answer_has_no_reasoning_panel(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-A',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={};state.agentStatus={configured:true,available:true,remote:true,model:'real-model',thinking:true,data_kind:'personal_source',personal_data_use:{version:'personal-retrieval-v1'}};state.agentDraft='查询培养要求'");
let releaseTurn,requestId;const calls=[];context.fetch=(path,options)=>{const body=options.body?JSON.parse(options.body):null;calls.push({path,body});if(path==='/api/chat/turn'){requestId=body.request_id;return new Promise(resolve=>{releaseTurn=resolve;});}if(path==='/api/chat/progress')return Promise.resolve({ok:true,json:async()=>({request_id:requestId,run_id:'RUN-A',status:'running',model:'real-model',thinking:true,events:[{kind:'model.start'}],next_sequence:1,terminal:false,available:true,active_reasoning:{call:1,text:'实际流式片段<script>bad()</script>',truncated:false,redacted:false}})});if(path==='/api/chat/run')return Promise.resolve({ok:true,json:async()=>({run:{run_id:'RUN-A',status:'completed',model:'real-model',thinking:true},events:[{kind:'model.end',reasoning_content:'最终供应商可见推理'}],reasoning_notice:'实际返回片段，不代表完整内部思考。'})});if(path==='/api/chat/conversations?limit=100')return Promise.resolve({ok:true,json:async()=>({conversations:[],data_kind:'personal_source'})});throw Error(path);};
const pending=evaluate('sendAgentTurn()');for(let index=0;index<20;index++)await Promise.resolve();let html=element('#panel-assistant').innerHTML;assert.match(html,/模型正在深度思考/);assert.match(html,/class="agent-pending" role="status"/);assert.doesNotMatch(html,/实际流式片段|agent-process|运行记录/);assert.doesNotMatch(html,/<script>|最终答案正文/);assert.equal(calls[0].path,'/api/chat/turn');assert.deepEqual(calls[1],{path:'/api/chat/progress',body:{request_id:requestId,after_sequence:0}});
releaseTurn({ok:true,json:async()=>({conversation_id:'CHAT-A',turn_id:1,run_id:'RUN-A',model:'real-model',thinking:true,status:'completed',reply:'最终答案正文',tool_results:[],citations:[],data_kind:'personal_source'})});await pending;html=element('#panel-assistant').innerHTML;assert.match(html,/最终答案正文/);assert.doesNotMatch(html,/最终供应商可见推理|实际流式片段|agent-pending|处理过程|运行记录/);assert.equal(evaluate("state.agentRunDetails['RUN-A'].events[0].reasoning_content"),'最终供应商可见推理');assert.equal(calls.filter(item=>item.path==='/api/chat/turn').length,1);assert.equal(evaluate('state.agentProgressPoll'),null);
""")

    def test_full_progress_snapshots_replace_and_use_revision_without_replaying_turn(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-A',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={};state.agentTurns=[{message:'问题',pending:true,request_id:'REQUEST-A'}]");
const timers=[],bodies=[];context.setTimeout=(callback,ms)=>{assert.equal(ms,800);timers.push(callback);return timers.length;};context.clearTimeout=()=>{};context.fetch=async(path,options)=>{assert.equal(path,'/api/chat/progress');const body=JSON.parse(options.body);bodies.push(body);return {ok:true,json:async()=>({request_id:'REQUEST-A',run_id:'RUN-A',status:'running',thinking:false,model:'real-model',events:[{kind:'tool.start',tool_name:'query_my_records'},{kind:'tool.end',tool_name:'query_my_records'}],next_sequence:bodies.length,terminal:false,available:true,active_reasoning:null})};};
evaluate("startAgentProgress(state.agentTurns[0],'REQUEST-A')");for(let index=0;index<20;index++)await Promise.resolve();assert.equal(timers.length,1);await timers.shift()();assert.deepEqual(bodies,[{request_id:'REQUEST-A',after_sequence:0},{request_id:'REQUEST-A',after_sequence:1}]);assert.equal(evaluate('state.agentTurns[0].progress.events.length'),2);assert.match(element('#panel-assistant').innerHTML,/role="status"/);assert.doesNotMatch(element('#panel-assistant').innerHTML,/本轮普通模式|处理过程|运行记录/);assert.doesNotMatch(element('#panel-assistant').innerHTML,/class="agent-process-provider"/);assert.doesNotMatch(element('#panel-assistant').innerHTML,/模型正在深度思考/);evaluate('stopAgentProgress()');
""")

    def test_progress_failure_does_not_resend_or_hide_the_pending_turn(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-A',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={};state.agentStatus={configured:true,available:true,remote:true,data_kind:'personal_source',personal_data_use:{version:'personal-retrieval-v1'}};state.agentDraft='问题'");
let releaseTurn;const calls=[];context.fetch=async(path)=>{calls.push(path);if(path==='/api/chat/turn')return new Promise(resolve=>{releaseTurn=resolve;});if(path==='/api/chat/progress')return {ok:false,status:404,text:async()=>JSON.stringify({error:'unsupported'})};if(path==='/api/chat/conversations?limit=100')return {ok:true,json:async()=>({conversations:[],data_kind:'personal_source'})};throw Error(path);};
const pending=evaluate('sendAgentTurn()');for(let index=0;index<20;index++)await Promise.resolve();assert.match(element('#panel-assistant').innerHTML,/请求已发送，等待运行状态/);assert.doesNotMatch(element('#panel-assistant').innerHTML,/当前服务尚不支持实时进度|处理过程/);assert.equal(evaluate('state.agentBusy'),true);assert.equal(evaluate('state.agentProgressPoll'),null);releaseTurn({ok:true,json:async()=>({conversation_id:'CHAT-A',turn_id:1,status:'completed',reply:'正常答案',tool_results:[],citations:[],data_kind:'personal_source'})});await pending;assert.match(element('#panel-assistant').innerHTML,/正常答案/);assert.equal(calls.filter(path=>path==='/api/chat/turn').length,1);
""")

    def test_late_progress_and_401_cannot_publish_or_logout_another_owner(self):
        self.run_js(r"""
const seed=owner=>evaluate(`clearWorkspace();state.auth={authenticated:true,identity:{student_id:'${owner}',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={};state.agentTurns=[{message:'问题',pending:true}];renderAssistant()`);seed('PERSONAL-A');let release;context.fetch=()=>new Promise(resolve=>{release=resolve;});evaluate("startAgentProgress(state.agentTurns[0],'REQUEST-A')");seed('PERSONAL-B');release({ok:true,json:async()=>({request_id:'REQUEST-A',run_id:'PRIVATE-A',status:'running',events:[],next_sequence:1,terminal:false,available:true,active_reasoning:{call:1,text:'A私有推理'}})});for(let index=0;index<20;index++)await Promise.resolve();assert.doesNotMatch(element('#panel-assistant').innerHTML,/A私有推理|PRIVATE-A/);assert.equal(evaluate('state.agentProgressPoll'),null);
evaluate("startAgentProgress(state.agentTurns[0],'REQUEST-B')");seed('PERSONAL-C');release({ok:false,status:401,text:async()=>JSON.stringify({error:'old session'})});for(let index=0;index<20;index++)await Promise.resolve();assert.equal(evaluate('state.auth.authenticated'),true);assert.equal(evaluate('state.auth.identity.student_id'),'PERSONAL-C');assert.equal(evaluate('state.agentProgressPoll'),null);
""")

    def test_history_load_is_lazy_and_scrolling_tracks_progress_revisions(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-A',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={};state.agentStatus={data_kind:'personal_source',personal_data_use:{version:'personal-retrieval-v1'}};state.agentTurns=[{run_id:'HISTORY-RUN',status:'completed',message:'旧问题',reply:'旧回答',tool_results:[],citations:[]}];renderAssistant()");assert.match(element('#panel-assistant').innerHTML,/旧回答/);assert.doesNotMatch(element('#panel-assistant').innerHTML,/处理过程|agent-process-load|运行记录/);context.fetch=async(path,options)=>{assert.equal(path,'/api/chat/run');assert.deepEqual(JSON.parse(options.body),{run_id:'HISTORY-RUN'});return {ok:true,json:async()=>({run:{run_id:'HISTORY-RUN',status:'completed',thinking:false},events:[]})};};await evaluate("loadAgentProcess('HISTORY-RUN')");assert.match(element('#panel-assistant').innerHTML,/旧回答/);assert.doesNotMatch(element('#panel-assistant').innerHTML,/本轮普通模式|处理过程|运行记录/);assert.doesNotMatch(element('#panel-assistant').innerHTML,/class="agent-process-provider"/);
const scroll=element('#agent-dialogue-scroll');let top=0;scroll.scrollHeight=2000;scroll.clientHeight=400;Object.defineProperty(scroll,'scrollTop',{get:()=>top,set:value=>{top=Math.max(0,Math.min(value,scroll.scrollHeight-scroll.clientHeight));}});evaluate("state.agentScroll={initialized:false,forceLatest:true,nearBottom:true,version:''};state.agentTurns=[{pending:true,progress:{status:'running',thinking:true,confirmed:true,events:[],next_sequence:1,active_reasoning:{call:1,text:'片段'}}}];renderAssistant()");assert.equal(top,1600);scroll.scrollTop=300;scroll.listeners.scroll();const before=evaluate('agentScrollVersion()');evaluate("state.agentTurns[0].progress.next_sequence=2;state.agentTurns[0].progress.active_reasoning.text='新的实际片段'");scroll.scrollHeight=2300;evaluate('renderAssistant()');assert.equal(top,300);assert.notEqual(evaluate('agentScrollVersion()'),before);assert.equal(evaluate('state.agentScroll.newMessages'),true);assert.doesNotMatch(element('#panel-assistant').innerHTML,/agent-personal-consent|personal-operation|personal-observation/);
""")


    def test_grade_summary_uses_all_matching_source_rows_and_distinguishes_missing_from_zero(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-A',auth_mode:'school_client',security_realm:'personal_school_client'}}");
const summary={matched_records:60,numeric_grade_count:57,non_numeric_grade_count:2,unknown_grade_count:1,numeric_min:'45',numeric_max:'98',numeric_mean_unweighted:'86.74',coverage:{captured_rows:60,reported_total:75,has_more:true,complete_within_scope:false}};context.summary=summary;
const citation=evaluate("agentCitation({kind:'summary',citation_number:1,title:'来源成绩概况',source_grade_summary:summary,target_term:'ALL-HISTORY',data_source:'live'})");assert.match(citation,/\[1\] 成绩概况/);assert.match(citation,/匹配来源记录<\/dt><dd>60/);assert.match(citation,/86.74/);assert.match(citation,/全部匹配行，不受展示条数限制/);assert.match(citation,/不是 GPA、绩点、已获学分或修读认定/);assert.match(citation,/还有未读取内容/);assert.doesNotMatch(citation,/培养资料片段|PDF 第|numeric_mean_unweighted/);
const absent=evaluate("agentToolCard({name:'query_my_records',result:{operation:'major_academic',status:'not_ready',records:[],matched_records:null,returned_records:0,query_executed:false}})");assert.match(absent,/尚未取得成绩来源/);assert.doesNotMatch(absent,/0 条|0条|成绩概况/);
const zero=evaluate("agentToolCard({name:'query_my_records',result:{operation:'academic',status:'ok',records:[],matched_records:0,coverage:{captured_rows:0,complete_within_scope:true}}})");assert.match(zero,/当前已读范围 0 条/);assert.doesNotMatch(zero,/尚未取得成绩来源/);
const unknown=evaluate("agentToolCard({name:'query_my_records',result:{operation:'academic',status:'unknown',records:[],matched_records:null,coverage:{}}})");assert.match(unknown,/条数待确认/);assert.doesNotMatch(unknown,/0 条|0条/);
assert.match(evaluate("agentProcessStage({status:'running',thinking:true,events:[{kind:'tool.start',tool_name:'read_my_major_academic'}]})"),/正在读取主修成绩/);assert.match(evaluate("agentRunEventMarkup({kind:'tool.end',tool_name:'read_my_curriculum'})"),/读取培养资料/);
""")

    def test_streamed_reasoning_scroll_follows_latest_but_preserves_reader_position(self):
        self.run_js(r"""
const reasoning=element('#reasoning-fixture');reasoning.dataset={reasoningKey:'REQUEST-A'};reasoning.clientHeight=120;reasoning.scrollHeight=800;let top=0;Object.defineProperty(reasoning,'scrollTop',{get:()=>top,set:value=>{top=Math.max(0,Math.min(value,reasoning.scrollHeight-reasoning.clientHeight));}});context.document.querySelectorAll=selector=>selector==='.agent-process-reasoning'?[reasoning]:[];
evaluate('restoreAgentReasoningScroll()');assert.equal(top,680);reasoning.scrollTop=120;reasoning.listeners.scroll();reasoning.scrollHeight=1200;evaluate('restoreAgentReasoningScroll()');assert.equal(top,120);reasoning.scrollTop=1080;reasoning.listeners.scroll();reasoning.scrollHeight=1500;evaluate('restoreAgentReasoningScroll()');assert.equal(top,1380);evaluate('clearAgentConversation()');assert.equal(evaluate('Object.keys(state.agentReasoningScroll).length'),0);
""")

    def test_full_request_trace_preserves_source_preparation_and_controlled_failure_details(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-A',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={}");
const events=[
 {kind:'tool.start',sequence:1,at:1792112400,tool_name:'read_my_major_academic',initiated_by:'service_acquisition',operation:'major_academic',target_term:'ALL-HISTORY'},
 {kind:'tool.end',sequence:2,at:1792112401,tool_name:'read_my_major_academic',initiated_by:'service_acquisition',status:'completed',operation:'major_academic',target_term:'ALL-HISTORY',elapsed_ms:1100,captured_rows:60,reported_total:104,has_more:true,complete_within_scope:false},
 {kind:'tool.start',sequence:3,tool_name:'get_my_context',initiated_by:'service_preflight'},
 {kind:'tool.end',sequence:4,tool_name:'get_my_context',status:'completed'},
 {kind:'model.start',sequence:5,model:'provider-real',step:1},
 {kind:'model.end',sequence:6,model:'provider-real',step:1,elapsed_ms:1200,reasoning_content:'第一段实际返回的供应商可见推理'},
 {kind:'tool.start',sequence:7,tool_name:'query_my_records',initiated_by:'model',operation:'major_academic',target_term:'ALL-HISTORY',query_mode:'overview',arguments:{query:'PRIVATE-QUERY',path:'/Users/PRIVATE-FILE'}},
 {kind:'tool.end',sequence:8,tool_name:'query_my_records',status:'completed',matched_records:60,returned_records:15,elapsed_ms:6},
 {kind:'model.start',sequence:9,step:2},
 {kind:'model.end',sequence:10,step:2,reasoning_content:'第二段实际返回的供应商可见推理'},
 {kind:'answer.rejected',sequence:11,error_code:'invalid_final_answer',failure_detail:'claim_count_out_of_bounds',claim_count:9,attempt:1},
 {kind:'answer.repair',sequence:12,attempt:1},
 {kind:'error',sequence:13,error_code:'invalid_final_answer',stage:'answer'}];
context.detail={run:{run_id:'RUN-12345678abcdef',request_id:'12345678-abcd-1234-abcd-123456789012',status:'failed',model:'provider-real',thinking:true,duration_ms:12600},events};
evaluate("state.agentRunDetails[detail.run.run_id]=detail;state.agentTurns=[{request_id:detail.run.request_id,run_id:detail.run.run_id,status:'failed',message:'查看成绩',reply:'答复尚未完成',tool_results:[],citations:[]}];renderAssistant()");
const page=element('#panel-assistant').innerHTML;assert.match(page,/答复尚未完成/);assert.doesNotMatch(page,/处理过程|实际处理记录|第一段实际返回|运行记录/);
const html=evaluate('renderAgentProcess(state.agentTurns[0])');
assert.match(html,/服务读取来源/);assert.match(html,/read_my_major_academic/);assert.match(html,/已读取 60 \/ 来源报告 104 条/);assert.match(html,/还有未读取内容/);assert.match(html,/匹配 60 条/);assert.match(html,/返回 15 条/);assert.match(html,/本轮已开启深度思考/);assert.match(html,/第 2 次模型调用/);assert.match(html,/修正答复格式与引用/);assert.match(html,/结论条数超出允许范围/);assert.match(html,/9 条结论/);assert.match(html,/invalid_final_answer/);assert.match(html,/运行 12345678 · 请求 12345678 · 12.6 秒/);assert.match(html,/实际处理记录/);assert.match(html,/供应商可见推理 · 2 段/);assert.match(html,/第一段实际返回/);assert.match(html,/第二段实际返回/);assert.doesNotMatch(html,/PRIVATE-QUERY|PRIVATE-FILE|内部思维已展示|运行Python脚本/);
assert.equal(evaluate('state.agentRunDetails[detail.run.run_id].events.length'),13);
// The HTTP service returns flat fields. Also accept the durable store envelope
// without confusing it with proof that this is the actual HTTP response shape.
const stored=evaluate("agentRunEventMarkup({kind:'tool.end',created_at:1792112401,fields:{tool_name:'query_my_records',operation:'major_academic',target_term:'ALL-HISTORY',matched_records:60,returned_records:15,elapsed_ms:8}})");
assert.match(stored,/query_my_records/);assert.match(stored,/8 ms/);assert.match(stored,/2026\/10\/16/);assert.match(stored,/匹配 60 条/);
const legacy=evaluate("agentRunEventMarkup({kind:'tool.end',tool_name:'query_my_records',record_count:15,match_count:0})");assert.match(legacy,/本次返回 15 条来源行/);assert.doesNotMatch(legacy,/匹配 0 条|来源记录 15 条/);
assert.match(evaluate("agentRunEventMarkup({kind:'tool.end',tool_name:'search_my_curriculum',match_count:3})"),/命中 3 个片段/);
""")

    def test_prior_run_diagnostic_cards_and_citations_show_only_actual_safe_fields(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-A',auth_mode:'school_client',security_realm:'personal_school_client'}}");
context.diagnostic={run_id:'RUN-abcdef01abcdef',status:'failed',model:'provider-real',thinking:false,events:[{kind:'tool.end',fields:{tool_name:'get_my_context',elapsed_ms:5,status:'completed'}},{kind:'answer.rejected',fields:{error_code:'invalid_final_answer',failure_detail:'invalid_json'}}],private_prompt:'DO-NOT-RENDER',reasoning_content:'NOT-PROVIDER-REASONING'};
const card=evaluate("agentToolCard({name:'get_my_run_diagnostics',result:{status:'ok',diagnostic}})");
assert.match(card,/上一轮实际运行记录/);assert.match(card,/运行 abcdef01/);assert.match(card,/普通模式/);assert.match(card,/get_my_context/);assert.match(card,/5 ms/);assert.match(card,/未返回有效 JSON/);assert.match(card,/没有通用终端脚本执行能力/);assert.doesNotMatch(card,/DO-NOT-RENDER|NOT-PROVIDER-REASONING/);
const citation=evaluate("agentCitation({kind:'diagnostic',citation_number:1,diagnostic})");assert.match(citation,/\[1\] 上一轮实际运行记录/);assert.match(citation,/get_my_context/);assert.doesNotMatch(citation,/来源类型待确认/);
assert.match(evaluate("agentToolCard({name:'get_my_run_diagnostics',result:{status:'not_ready'}})"),/还没有可核对的上一轮运行记录/);
""")

    def test_answer_completion_reveals_answer_start_and_preserves_an_earlier_reader_anchor(self):
        self.run_js(r"""
const scroll=element('#agent-dialogue-scroll');let top=0;scroll.scrollHeight=2200;scroll.clientHeight=400;Object.defineProperty(scroll,'scrollTop',{get:()=>top,set:value=>{top=Math.max(0,Math.min(value,scroll.scrollHeight-scroll.clientHeight));}});scroll.getBoundingClientRect=()=>({top:100,bottom:500});
let anchorY=1200,answerY=1700;const earlier={dataset:{scrollAnchor:'earlier:answer'},getBoundingClientRect:()=>({top:100+anchorY-top,bottom:100+anchorY+300-top})};const answer={dataset:{answerKey:'REQUEST-A',scrollAnchor:'REQUEST-A:answer'},getBoundingClientRect:()=>({top:100+answerY-top,bottom:100+answerY+800-top})};scroll.querySelectorAll=()=>[earlier,answer];
evaluate("state.agentTurns=[{request_id:'REQUEST-A',pending:true}];state.agentScroll={initialized:false,forceLatest:true,nearBottom:true,version:''};restoreAgentScroll()");assert.equal(top,1800);
evaluate("state.agentTurns[0].pending=false;state.agentTurns[0].reply='最终答复';restoreAgentScroll()");assert.equal(top,1692);assert.equal(evaluate('state.agentScroll.newMessages'),false);
scroll.scrollTop=1250;evaluate('captureAgentScroll()');assert.equal(evaluate('state.agentScroll.anchor.key'),'earlier:answer');
anchorY+=200;answerY+=200;scroll.scrollHeight+=200;evaluate("state.agentTurns[0].progress={next_sequence:3};restoreAgentScroll()");assert.equal(top,1450);assert.equal(earlier.getBoundingClientRect().top,50);assert.equal(evaluate('state.agentScroll.newMessages'),true);
// Opening a completed historical conversation starts at its answer, not its logs.
evaluate("state.agentScroll={initialized:false,forceLatest:true,nearBottom:true,version:''};restoreAgentScroll()");assert.equal(top,1892);
""")

    def test_execution_trace_scroll_preserves_manual_reading_across_progress_renders(self):
        self.run_js(r"""
const trace=element('#trace-fixture');trace.dataset={traceKey:'REQUEST-A'};trace.clientHeight=128;trace.scrollHeight=800;let top=0;Object.defineProperty(trace,'scrollTop',{get:()=>top,set:value=>{top=Math.max(0,Math.min(value,trace.scrollHeight-trace.clientHeight));}});context.document.querySelectorAll=selector=>selector==='.agent-process-trace'?[trace]:[];
evaluate('restoreAgentReasoningScroll()');assert.equal(top,672);trace.scrollTop=100;trace.listeners.scroll();trace.scrollHeight=1000;evaluate('captureAgentReasoningScroll();restoreAgentReasoningScroll()');assert.equal(top,100);evaluate('clearAgentConversation()');assert.equal(evaluate('Object.keys(state.agentTraceScroll).length'),0);
""")

    def test_processing_descriptions_distinguish_returned_failed_and_unready_calls(self):
        self.run_js(r"""
const description=evaluate("agentProcessDescription({status:'running',events:[{kind:'tool.end',tool_name:'get_my_context',status:'completed'},{kind:'tool.end',tool_name:'query_my_records',status:'failed'},{kind:'tool.end',tool_name:'search_my_curriculum',status:'not_ready'},{kind:'model.end',status:'timeout'}]})");
assert.match(description,/收到 3 次本机工具返回/);assert.match(description,/1 次资料未就绪/);assert.match(description,/1 次未完成/);assert.match(description,/模型调用已结束 1 次（1 次未完成）/);assert.doesNotMatch(description,/已完成 3 次/);
assert.match(evaluate("agentProcessStage({status:'running',thinking:true,events:[{kind:'model.end',status:'completed'}]})"),/模型已返回/);
assert.doesNotMatch(evaluate("agentProcessStage({status:'running',thinking:true,events:[{kind:'model.end',status:'completed'}]})"),/正在深度思考/);
assert.match(evaluate("agentProcessStage({status:'running',thinking:true,events:[{kind:'model.end',status:'timeout'}]})"),/模型服务返回超时/);
assert.match(evaluate("agentProcessStage({status:'running',thinking:true,events:[{kind:'tool.end',tool_name:'query_my_records',status:'not_ready'}]})"),/资料尚未就绪/);
assert.match(evaluate("agentProcessStage({status:'running',events:[{kind:'tool.end',tool_name:'read_my_major_academic',status:'failed'}]})"),/主修成绩读取未完成/);
assert.deepEqual(Array.from(evaluate("['not_ready','accepted','rejected','skipped','timeout'].map(agentRunStatus)")),['资料未就绪','已通过','未通过','未执行','超时']);
""")

    def test_course_not_ready_is_not_counted_as_missing_source_in_progress_or_run_details(self):
        self.run_js(r"""
const courseTools=['search_current_term_courses','generate_current_term_plan','update_current_term_plan','get_current_term_plan'];
context.mixedEvents=[{kind:'tool.end',tool_name:'read_my_curriculum',status:'completed',captured_rows:9},{kind:'tool.end',tool_name:'get_my_context',status:'completed'},{kind:'tool.end',tool_name:'query_my_records',status:'not_ready'},...courseTools.map(tool_name=>({kind:'tool.end',tool_name,status:'not_ready',reason:'PRIVATE-UNKNOWN-REASON'})),{kind:'tool.end',tool_name:'future_unknown_tool',status:'not_ready',reason:'PRIVATE-UNKNOWN-REASON'},{kind:'model.end',status:'completed'},{kind:'model.end',status:'failed'}];
const before=JSON.stringify(context.mixedEvents),description=evaluate("agentProcessDescription({status:'running',events:mixedEvents})");
assert.match(description,/收到 7 次本机工具返回/);assert.match(description,/2 次资料未就绪/);assert.match(description,/4 次课程条件测试受阻/);assert.match(description,/模型调用已结束 2 次（1 次未完成）/);assert.match(description,/读取培养资料已返回，已读取 9 条/);assert.doesNotMatch(description,/6 次资料未就绪|无解|方案已生成|PRIVATE-UNKNOWN/);
for(const tool of courseTools){
 context.courseEvent={kind:'tool.end',tool_name:tool,status:'not_ready',reason:'PRIVATE-UNKNOWN-REASON'};
 assert.match(evaluate("agentProcessStage({status:'running',events:[courseEvent]})"),/课程条件测试受阻，待核对/);
 for(const html of [evaluate('agentRunEventMarkup(courseEvent)'),evaluate('agentRunEventMarkup(courseEvent,true)'),evaluate("agentRunEventMarkup({kind:'tool.end',fields:courseEvent})")]){assert.match(html,/课程条件测试受阻，待核对/);assert.doesNotMatch(html,/资料未就绪|所需资料|无解|方案已生成|PRIVATE-UNKNOWN/);}
}
for(const tool of ['query_my_records','search_my_curriculum','prepare_recommendation_inputs','read_my_timetable','future_unknown_tool']){
 context.sourceEvent={kind:'tool.end',tool_name:tool,status:'not_ready'};assert.match(evaluate('agentRunEventMarkup(sourceEvent)'),/资料未就绪/);assert.doesNotMatch(evaluate('agentRunEventMarkup(sourceEvent)'),/课程条件测试受阻/);
}
assert.match(evaluate("agentProcessStage({status:'running',events:[{kind:'tool.end',tool_name:'query_my_records',status:'not_ready'}]})"),/资料尚未就绪/);
context.onlyCourses=context.mixedEvents.filter(event=>courseTools.includes(event.tool_name));const only=evaluate("agentProcessDescription({status:'running',events:onlyCourses})");assert.match(only,/收到 4 次本机工具返回（4 次课程条件测试受阻）/);assert.doesNotMatch(only,/资料未就绪/);
evaluate("state.agentRunDetails['RUN-SYN']={run:{run_id:'RUN-SYN',status:'completed'},events:mixedEvents}");
for(const html of [evaluate("agentDiagnosticMarkup({status:'completed',events:mixedEvents})")]){assert.equal((html.match(/课程条件测试受阻，待核对/g)||[]).length,4);assert.equal((html.match(/资料未就绪/g)||[]).length,2);assert.match(html,/受限资料工具/);assert.doesNotMatch(html,/PRIVATE-UNKNOWN|无解|方案已生成/);}
assert.equal(JSON.stringify(context.mixedEvents),before);
""")

    def test_rejected_tool_arguments_show_check_and_repair_without_claiming_execution(self):
        self.run_js(r"""
context.rejection={kind:'tool.rejected',sequence:3,tool_name:'query_my_records',error_code:'invalid_tool_arguments',failure_detail:'argument_schema_mismatch',status:'retrying',attempt:1,count:1,arguments:{query:'PRIVATE-QUERY'}};
const markup=evaluate('agentRunEventMarkup(rejection)');
assert.match(markup,/本人教务记录 · 工具参数检查/);assert.match(markup,/参数未通过检查，未执行；正在修正/);assert.doesNotMatch(markup,/开始调用|调用返回|PRIVATE-QUERY|运行Python|脚本执行/);
assert.match(evaluate("agentProcessStage({status:'running',events:[rejection]})"),/参数检查未通过，正在修正/);
const failed=evaluate("agentRunEventMarkup({...rejection,status:'failed',attempt:2})");assert.match(failed,/参数未通过检查，未执行；本轮未完成/);assert.doesNotMatch(failed,/正在修正/);
const description=evaluate("agentProcessDescription({status:'running',events:[{kind:'tool.end',tool_name:'get_my_context',status:'completed'},rejection]})");assert.match(description,/收到 1 次本机工具返回/);assert.doesNotMatch(description,/收到 2 次/);
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-A',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={};state.agentTurns=[{pending:true,request_id:'REQUEST-A',progress:{status:'running',confirmed:true,events:[rejection]}}];renderAssistant()");
const process=element('#panel-assistant').innerHTML;assert.match(process,/工具参数检查未通过，正在修正/);assert.match(process,/role="status"/);assert.doesNotMatch(process,/实际处理记录|实际调用记录|PRIVATE-QUERY|处理过程|运行记录/);
const stored=evaluate("agentRunEventMarkup({kind:'tool.rejected',fields:{tool_name:'query_my_records',error_code:'invalid_tool_arguments',failure_detail:'argument_schema_mismatch',status:'retrying'}})");assert.match(stored,/工具参数检查/);assert.match(stored,/未执行；正在修正/);
""")

    def test_process_defaults_close_on_completion_and_preserve_only_manual_choices(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-A',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={};state.agentTurns=[{pending:true,request_id:'REQUEST-A',run_id:'RUN-A',progress:{status:'running',confirmed:true,thinking:true,events:[]}}]");
const opening=()=>evaluate('renderAgentProcess(state.agentTurns[0])').split('>')[0];
assert.match(opening(),/\sopen\s*$/);assert.equal(evaluate('Object.keys(state.agentProcessOpen).length'),0);
evaluate("state.agentTurns[0].pending=false;state.agentTurns[0].status='completed';state.agentTurns[0].progress.status='completed'");assert.doesNotMatch(opening(),/\sopen\s*$/);
// A summary activation is the manual choice. Rendering's native toggle event
// must not accidentally preserve the initial running default as an override.
const summary={dataset:{},listeners:{},addEventListener(type,callback){this.listeners[type]=callback;}},details={open:false,querySelector:()=>summary};context.fixtureDetails=details;
evaluate("bindAgentDisclosureChoice(fixtureDetails,state.agentProcessOpen,'REQUEST-A')");assert.equal(summary.listeners.toggle,undefined);summary.listeners.click();assert.match(opening(),/\sopen\s*$/);
details.open=true;summary.listeners.click();assert.doesNotMatch(opening(),/\sopen\s*$/);
evaluate("state.agentTurns[0].pending=true;state.agentTurns[0].progress.status='running'");assert.doesNotMatch(opening(),/\sopen\s*$/);
details.open=false;summary.listeners.click();evaluate("state.agentTurns[0].pending=false;state.agentTurns[0].progress.status='completed'");assert.match(opening(),/\sopen\s*$/);
evaluate('clearAgentConversation()');assert.equal(evaluate('Object.keys(state.agentProcessOpen).length'),0);
""")

    def test_personal_clarification_waits_for_input_without_blocking_the_composer(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-A',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={};state.agentStatus={configured:true,available:true,remote:true,data_kind:'personal_source',personal_data_use:{version:'personal-retrieval-v1'}};state.agentTurns=[{request_id:'REQUEST-A',run_id:'RUN-A',status:'completed',task_status:'awaiting_user',reply_origin:'personal_model_clarification',message:'查一下课表',reply:'需要查询哪个学年和学期？',tool_results:[],citations:[],progress:{status:'completed',confirmed:true,events:[]}}];state.agentDraft='2024-2025秋冬';renderAssistant()");
let html=element('#panel-assistant').innerHTML;assert.match(html,/<strong>待补充信息<\/strong>/);assert.match(html,/需要查询哪个学年和学期/);assert.doesNotMatch(html,/处理过程|agent-pending|运行记录/);assert.doesNotMatch(html,/本轮处理完成|这段对话仍在处理/);assert.equal(evaluate('state.agentHistoryPending'),false);assert.equal(evaluate('agentCanSend()'),true);assert.doesNotMatch(html.match(/<textarea[^>]*>/)[0],/disabled/);
// Completed run details cannot overwrite the server's task state.
evaluate("state.agentRunDetails['RUN-A']={run:{run_id:'RUN-A',status:'completed'},events:[]};renderAssistant()");assert.match(element('#panel-assistant').innerHTML,/<strong>待补充信息<\/strong>/);
evaluate("state.agentTurns[0].task_status='needs_clarification';renderAssistant()");assert.match(element('#panel-assistant').innerHTML,/<strong>待补充信息<\/strong>/);
// Lazy history preserves the load action and communicates the same task state.
evaluate("delete state.agentRunDetails['RUN-A'];delete state.agentTurns[0].progress;renderAssistant()");html=element('#panel-assistant').innerHTML;assert.match(html,/<strong>待补充信息<\/strong>/);assert.doesNotMatch(html,/agent-process-load/);
// Old responses still label the clarification, but do not invent a task status.
evaluate("delete state.agentTurns[0].task_status;state.agentTurns[0].progress={status:'completed',confirmed:true,events:[]};renderAssistant()");html=element('#panel-assistant').innerHTML;assert.match(html,/<strong>待补充信息<\/strong>/);assert.doesNotMatch(html,/处理过程|本轮处理完成/);
evaluate("state.agentTurns[0].task_status='completed';state.agentTurns[0].reply_origin='personal_model_evidence';renderAssistant()");assert.doesNotMatch(element('#panel-assistant').innerHTML,/待补充信息/);
assert.doesNotMatch(evaluate("agentProcessStage({status:'running',events:[{kind:'model.start'}]},'awaiting_user')"),/本轮已结束/);
""")

    def test_terminal_turn_overrides_stale_running_progress_when_final_details_fail(self):
        self.run_js(r"""
for(const choice of ['untouched','open','closed']){
 evaluate("clearWorkspace();state.auth={authenticated:true,identity:{student_id:'PERSONAL-A',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={};state.agentStatus={configured:true,available:true,remote:true,thinking:true,data_kind:'personal_source',personal_data_use:{version:'personal-retrieval-v1'}};state.agentDraft='查课表'");
 let requestId,release;context.fetch=(path,options)=>{
  if(path==='/api/chat/turn'){requestId=JSON.parse(options.body).request_id;return new Promise(resolve=>{release=resolve;});}
  if(path==='/api/chat/progress')return Promise.resolve({ok:true,json:async()=>({request_id:requestId,run_id:'RUN-A',status:'running',thinking:true,events:[{kind:'model.start'}],next_sequence:1,available:true,active_reasoning:{call:1,text:'实际收到但最终详情尚未载入的片段'}})});
  if(path==='/api/chat/run')return Promise.resolve({ok:false,status:503,text:async()=>JSON.stringify({error:'合成详情不可用'})});
  if(path==='/api/chat/conversations?limit=100')return Promise.resolve({ok:true,json:async()=>({conversations:[],data_kind:'personal_source'})});
  throw Error(path);
 };
 const pending=evaluate('sendAgentTurn()');for(let index=0;index<20;index++)await Promise.resolve();
 assert.match(element('#panel-assistant').innerHTML,/role="status"/);assert.doesNotMatch(element('#panel-assistant').innerHTML,/agent-process/);
 if(choice!=='untouched'){context.manualOpen=choice==='open';evaluate('state.agentProcessOpen[state.agentTurns[0].request_id]=manualOpen');}
 release({ok:true,json:async()=>({conversation_id:'CHAT-A',turn_id:1,run_id:'RUN-A',status:'completed',task_status:'awaiting_user',reply_origin:'personal_model_clarification',reply:'需要哪个学年？',tool_results:[],citations:[],data_kind:'personal_source'})});await pending;
 let html=element('#panel-assistant').innerHTML;
 assert.match(html,/需要哪个学年/);assert.doesNotMatch(html,/agent-process|agent-pending|最后收到的推理片段|实际收到但最终详情尚未载入的片段|本轮运行过程暂未载入|正在返回的推理|模型正在深度思考/);
 assert.equal(evaluate('state.agentTurns[0].progress.status'),'running','the raw progress snapshot remains unchanged');assert.equal(evaluate('agentTurnProgress(state.agentTurns[0]).status'),'completed');
 if(choice==='untouched')assert.equal(evaluate('Object.keys(state.agentProcessOpen).length'),0);
 // A delayed running run cache must also yield to the confirmed turn result.
 evaluate("state.agentRunDetails['RUN-A']={run:{run_id:'RUN-A',status:'running'},events:[]};renderAssistant()");html=element('#panel-assistant').innerHTML;assert.match(html,/<strong>待补充信息<\/strong>/);assert.doesNotMatch(html,/agent-process|agent-pending/);
 evaluate("state.agentTurns[0].status='failed'");assert.equal(evaluate('agentTurnProgress(state.agentTurns[0]).status'),'failed');assert.match(evaluate('renderAgentProcess(state.agentTurns[0])'),/本轮处理未完成/);
}
""")

    def test_personal_answer_omits_evidence_panels_without_rewriting_source_records(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-A',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={}");
const records=[{evidence_id:'RECORD-1',course_name_raw:'重复课程甲',course_code_raw:'REPEAT',grade_raw:'70'},{evidence_id:'RECORD-2',course_name_raw:'重复课程乙',course_code_raw:'REPEAT',grade_raw:'90'}],summary={evidence_id:'SUMMARY-1',matched_records:2,numeric_grade_count:2,non_numeric_grade_count:0,unknown_grade_count:0,numeric_min:'70',numeric_max:'90',numeric_mean_unweighted:'80',coverage:{captured_rows:2,reported_total:2,complete_within_scope:true}};
context.turn={request_id:'REQUEST-A',run_id:'RUN-A',status:'completed',message:'成绩怎么样',reply:'答案正文 [1] [2] [3]',tool_results:[{name:'query_my_records',result:{status:'ok',operation:'major_academic',target_term:'ALL-HISTORY',data_source:'live',coverage:summary.coverage,records,source_grade_summary:summary}}],citations:[{kind:'summary',evidence_id:'SUMMARY-1',citation_number:1,operation:'major_academic',source_grade_summary:summary},{kind:'record',evidence_id:'RECORD-1',citation_number:2,operation:'major_academic',record:records[0]},{kind:'context',evidence_id:'CONTEXT-1',citation_number:3,context:{major_name:'合成专业'}}]};
evaluate("state.agentTurns=[turn];state.agentRunDetails['RUN-A']={run:{run_id:'RUN-A',status:'completed',thinking:false,model:'provider-real'},events:[{kind:'model.end',usage:{total_tokens:100}}]}");
const before=JSON.stringify(context.turn),html=evaluate('renderAgentTurns()'),answer=html.slice(html.indexOf('class="agent-answer"'));
assert.match(answer,/答案正文/);assert.doesNotMatch(answer,/查看依据|处理过程|条件与核验范围|运行记录|agent-evidence|agent-process|重复课程|成绩概况|personal-record-table|\[1\]|\[2\]|\[3\]/);assert.equal(JSON.stringify(context.turn),before);
evaluate("state.agentEvidenceOpen['REQUEST-A']=true");assert.match(evaluate("renderPersonalEvidence(turn,'REQUEST-A')").match(/<details[^>]*>/)[0],/\sopen[\s>]/);
const unknown=evaluate("renderPersonalEvidence({tool_results:[{name:'query_my_records',result:{status:'unknown',operation:'academic',records:[],matched_records:null,coverage:{}}}]},'UNKNOWN')");assert.match(unknown,/条数待确认/);assert.doesNotMatch(unknown,/没有匹配记录/);
""")

    def test_curriculum_evidence_merges_citations_and_keeps_full_context_collapsed(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-A',auth_mode:'school_client',security_realm:'personal_school_client'}}");
context.item={evidence_id:'E-1',title:'培养段落甲',text:'只显示一次的培养原文',page:4,review_status:'unreviewed',required_context:[{title:'表格上下文',text:'只显示一次的上下文',page:4}]};
const html=evaluate("renderPersonalEvidence({tool_results:[{name:'search_my_curriculum',result:{matches:[item]}}],citations:[{...item,kind:'curriculum',citation_number:1}]},'REQUEST-A')");
assert.equal((html.match(/<details/g)||[]).length,3);assert.equal((html.match(/只显示一次的培养原文/g)||[]).length,1);assert.equal((html.match(/只显示一次的上下文/g)||[]).length,1);assert.match(html,/\[1\] 培养段落甲/);assert.match(html,/PDF 第 4 页/);assert.match(html,/原文待审核/);assert.match(html,/<details><summary>查看原文上下文/);assert.match(html,/<summary>培养资料依据/);assert.doesNotMatch(html,/其他检索片段/);
// The original synthetic recommendation interface keeps its own citations.
evaluate("state.auth={authenticated:true,identity:{student_id:'DEMO-A'}};state.agentTurns=[{status:'completed',message:'合成问题',reply:'合成答案',tool_results:[],citations:[{title:'合成参考',text:'合成材料'}]}]");const synthetic=evaluate('renderAgentTurns()');assert.match(synthetic,/<summary>参考依据/);assert.doesNotMatch(synthetic,/查看依据|agent-process/);
""")

    def test_cited_curriculum_precedes_uncited_hits_and_secondary_hits_remain_available(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-A',auth_mode:'school_client',security_realm:'personal_school_client'}}");
context.noise={evidence_id:'E-NOISE',title:'未引用水印',text:'未引用的首个噪声命中',page:1,required_context:[{text:'噪声完整上下文',page:1}]};context.cited={evidence_id:'E-CITED',title:'实际引用的专业选修',text:'实际引用条款',page:3,required_context:[{text:'引用条款完整上下文',page:3}]};context.extra={evidence_id:'E-EXTRA',title:'其他未引用命中',text:'另一个可核对片段',page:4};
const html=evaluate("renderPersonalEvidence({tool_results:[{name:'search_my_curriculum',result:{matches:[noise,cited,extra]}}],citations:[{...cited,kind:'curriculum',citation_number:1}]},'REQUEST-A')");
assert.equal((html.match(/<summary>查看依据/g)||[]).length,1);assert.equal((html.match(/实际引用条款/g)||[]).length,1);assert.ok(html.indexOf('[1] 实际引用的专业选修')<html.indexOf('其他检索片段（2）'));assert.ok(html.indexOf('其他检索片段（2）')<html.indexOf('未引用的首个噪声命中'));
assert.match(html,/<details class="agent-other-evidence"><summary>其他检索片段（2）/);assert.match(html,/<details><summary>查看原文上下文/);assert.match(html,/引用条款完整上下文/);assert.match(html,/噪声完整上下文/);assert.match(html,/另一个可核对片段/);assert.doesNotMatch(html,/<details[^>]*\sopen[\s>]/);
const remaining=evaluate("renderPersonalEvidence({tool_results:[],citations:[{...cited,kind:'curriculum',citation_number:1}]},'REQUEST-B')");assert.match(remaining,/<details><summary>查看原文上下文/);assert.match(remaining,/引用条款完整上下文/);assert.doesNotMatch(remaining,/personal-evidence-context/);
""")

    def test_recommendation_readiness_stays_in_one_evidence_entry_and_never_claims_a_plan(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-A',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={}");
context.readiness={status:'not_ready',can_generate:false,can_save_or_export:false,sources:[{id:'course_reference',status:'available'},{id:'academic_records',status:'partial'},{id:'curriculum',status:'needs_clarification'},{id:'existing_timetable',status:'failed'},{id:'exams',status:'missing'}],dependencies:[{id:'identity_and_study_facts',status:'required_dependency_unverified',missing:['stable_course_and_attempt_identity','completion_recognition_and_repeat_policy']},{id:'current_candidate_pool',status:'required_dependency_unverified',missing:['bounded_current_offerings','personal_eligibility_and_fresh_capacity']}],delivery_dependency:'real_draft_freeze_and_export_contract_not_connected',next_step:'PRIVATE-UNEXPECTED-TEXT'};
context.readiness.recommendation_kind='timetable';context.readiness.target_term='2024-2025-SS';
context.readinessResult={evidence_id:'P-SYNTHETIC-1',check_executed:true,readiness:context.readiness};
context.readinessTurn={tool_results:[{name:'check_recommendation_inputs',result:context.readinessResult}],citations:[{kind:'recommendation_readiness',evidence_id:'P-SYNTHETIC-1',citation_number:2,readiness:context.readiness}]};
const html=evaluate("renderPersonalEvidence(readinessTurn,'REQUEST-A')");assert.equal((html.match(/<summary>查看依据/g)||[]).length,1);assert.equal((html.match(/推荐准备检查/g)||[]).length,1);assert.match(html,/\[2\] 推荐准备检查/);assert.match(html,/检查已完成，正式推荐尚未就绪/);assert.match(html,/课程名称与代码参考：可用/);assert.match(html,/成绩记录：部分取得/);assert.match(html,/培养资料：需补充查询范围/);assert.match(html,/已有课表：读取未完成/);assert.match(html,/已有考试：尚未具备/);assert.match(html,/稳定课程与修读实例身份/);assert.match(html,/本人资格与最新余量/);assert.match(html,/保存与导出契约仍待接通/);assert.doesNotMatch(html,/<details[^>]*\sopen[\s>]/);assert.doesNotMatch(html,/PRIVATE-UNEXPECTED-TEXT|stable_course_and_attempt_identity|real_recommendation_not_ready|data-action="save-agent-option"|已推荐/);
const citation=evaluate("agentCitation(readinessTurn.citations[0])");assert.match(citation,/\[2\] 推荐准备检查/);assert.doesNotMatch(citation,/来源类型待确认/);
assert.match(html,/课表推荐 · 2024-2025学年春夏/);
assert.match(evaluate("agentRunEventMarkup({kind:'tool.end',tool_name:'check_recommendation_inputs',status:'completed'})"),/推荐输入检查/);
const unexecuted=evaluate('personalRecommendationReadiness(readiness,false)');assert.match(unexecuted,/状态待确认/);assert.doesNotMatch(unexecuted,/检查已完成|课程名称与代码参考：可用/);
const unknown=evaluate("personalRecommendationReadiness({...readiness,target_term:'/private/never-show',recommendation_kind:'PRIVATE-KIND',sources:[{id:'/private/never-show',status:'PRIVATE-STATUS'}],dependencies:[{id:'PRIVATE-DEPENDENCY',status:'required_dependency_unverified',missing:['PRIVATE-DETAIL']}]},true)");assert.match(unknown,/其他来源：待确认/);assert.match(unknown,/其他依赖：其他项目待确认/);assert.doesNotMatch(unknown,/\/private|PRIVATE-/);
""")

if __name__ == '__main__':
    unittest.main()
