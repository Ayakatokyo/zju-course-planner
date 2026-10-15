"""Browser-independent contracts for personal in-message progress only."""
import unittest

from tests import test_web_contract as web_contract


@unittest.skipUnless(web_contract.NODE, "Node is needed only for Web progress contracts")
class PersonalProgressWebTests(unittest.TestCase):
    def run_js(self, assertions):
        web_contract.WebContractTests.run_js(self, assertions)

    def test_progress_is_parallel_and_final_run_reasoning_precedes_answer(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-A',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={};state.agentStatus={configured:true,available:true,remote:true,model:'real-model',thinking:true,data_kind:'personal_source',personal_data_use:{version:'personal-retrieval-v1'}};state.agentDraft='查询培养要求'");
let releaseTurn,requestId;const calls=[];context.fetch=(path,options)=>{const body=options.body?JSON.parse(options.body):null;calls.push({path,body});if(path==='/api/chat/turn'){requestId=body.request_id;return new Promise(resolve=>{releaseTurn=resolve;});}if(path==='/api/chat/progress')return Promise.resolve({ok:true,json:async()=>({request_id:requestId,run_id:'RUN-A',status:'running',model:'real-model',thinking:true,events:[{kind:'model.start'}],next_sequence:1,terminal:false,available:true,active_reasoning:{call:1,text:'实际流式片段<script>bad()</script>',truncated:false,redacted:false}})});if(path==='/api/chat/run')return Promise.resolve({ok:true,json:async()=>({run:{run_id:'RUN-A',status:'completed',model:'real-model',thinking:true},events:[{kind:'model.end',reasoning_content:'最终供应商可见推理'}],reasoning_notice:'实际返回片段，不代表完整内部思考。'})});if(path==='/api/chat/conversations?limit=100')return Promise.resolve({ok:true,json:async()=>({conversations:[],data_kind:'personal_source'})});throw Error(path);};
const pending=evaluate('sendAgentTurn()');for(let index=0;index<20;index++)await Promise.resolve();let html=element('#panel-assistant').innerHTML;assert.match(html,/模型正在深度思考/);assert.match(html,/实际流式片段&lt;script&gt;/);assert.doesNotMatch(html,/<script>|最终答案正文/);assert.equal(calls[0].path,'/api/chat/turn');assert.deepEqual(calls[1],{path:'/api/chat/progress',body:{request_id:requestId,after_sequence:0}});
releaseTurn({ok:true,json:async()=>({conversation_id:'CHAT-A',turn_id:1,run_id:'RUN-A',model:'real-model',thinking:true,status:'completed',reply:'最终答案正文',tool_results:[],citations:[],data_kind:'personal_source'})});await pending;html=element('#panel-assistant').innerHTML;assert.match(html,/最终供应商可见推理/);assert.ok(html.indexOf('最终供应商可见推理')<html.indexOf('最终答案正文'));assert.doesNotMatch(html,/实际流式片段/);assert.equal(calls.filter(item=>item.path==='/api/chat/turn').length,1);assert.equal(evaluate('state.agentProgressPoll'),null);
""")

    def test_full_progress_snapshots_replace_and_use_revision_without_replaying_turn(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-A',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={};state.agentTurns=[{message:'问题',pending:true,request_id:'REQUEST-A'}]");
const timers=[],bodies=[];context.setTimeout=(callback,ms)=>{assert.equal(ms,800);timers.push(callback);return timers.length;};context.clearTimeout=()=>{};context.fetch=async(path,options)=>{assert.equal(path,'/api/chat/progress');const body=JSON.parse(options.body);bodies.push(body);return {ok:true,json:async()=>({request_id:'REQUEST-A',run_id:'RUN-A',status:'running',thinking:false,model:'real-model',events:[{kind:'tool.start',tool_name:'query_my_records'},{kind:'tool.end',tool_name:'query_my_records'}],next_sequence:bodies.length,terminal:false,available:true,active_reasoning:null})};};
evaluate("startAgentProgress(state.agentTurns[0],'REQUEST-A')");for(let index=0;index<20;index++)await Promise.resolve();assert.equal(timers.length,1);await timers.shift()();assert.deepEqual(bodies,[{request_id:'REQUEST-A',after_sequence:0},{request_id:'REQUEST-A',after_sequence:1}]);assert.equal(evaluate('state.agentTurns[0].progress.events.length'),2);assert.match(element('#panel-assistant').innerHTML,/本轮普通模式，未开启深度思考/);assert.match(element('#panel-assistant').innerHTML,/尚未收到供应商可见推理文本/);assert.doesNotMatch(element('#panel-assistant').innerHTML,/模型正在深度思考/);evaluate('stopAgentProgress()');
""")

    def test_progress_failure_does_not_resend_or_hide_the_pending_turn(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-A',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={};state.agentStatus={configured:true,available:true,remote:true,data_kind:'personal_source',personal_data_use:{version:'personal-retrieval-v1'}};state.agentDraft='问题'");
let releaseTurn;const calls=[];context.fetch=async(path)=>{calls.push(path);if(path==='/api/chat/turn')return new Promise(resolve=>{releaseTurn=resolve;});if(path==='/api/chat/progress')return {ok:false,status:404,text:async()=>JSON.stringify({error:'unsupported'})};if(path==='/api/chat/conversations?limit=100')return {ok:true,json:async()=>({conversations:[],data_kind:'personal_source'})};throw Error(path);};
const pending=evaluate('sendAgentTurn()');for(let index=0;index<20;index++)await Promise.resolve();assert.match(element('#panel-assistant').innerHTML,/当前服务尚不支持实时进度/);assert.equal(evaluate('state.agentBusy'),true);assert.equal(evaluate('state.agentProgressPoll'),null);releaseTurn({ok:true,json:async()=>({conversation_id:'CHAT-A',turn_id:1,status:'completed',reply:'正常答案',tool_results:[],citations:[],data_kind:'personal_source'})});await pending;assert.match(element('#panel-assistant').innerHTML,/正常答案/);assert.equal(calls.filter(path=>path==='/api/chat/turn').length,1);
""")

    def test_late_progress_and_401_cannot_publish_or_logout_another_owner(self):
        self.run_js(r"""
const seed=owner=>evaluate(`clearWorkspace();state.auth={authenticated:true,identity:{student_id:'${owner}',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={};state.agentTurns=[{message:'问题',pending:true}];renderAssistant()`);seed('PERSONAL-A');let release;context.fetch=()=>new Promise(resolve=>{release=resolve;});evaluate("startAgentProgress(state.agentTurns[0],'REQUEST-A')");seed('PERSONAL-B');release({ok:true,json:async()=>({request_id:'REQUEST-A',run_id:'PRIVATE-A',status:'running',events:[],next_sequence:1,terminal:false,available:true,active_reasoning:{call:1,text:'A私有推理'}})});for(let index=0;index<20;index++)await Promise.resolve();assert.doesNotMatch(element('#panel-assistant').innerHTML,/A私有推理|PRIVATE-A/);assert.equal(evaluate('state.agentProgressPoll'),null);
evaluate("startAgentProgress(state.agentTurns[0],'REQUEST-B')");seed('PERSONAL-C');release({ok:false,status:401,text:async()=>JSON.stringify({error:'old session'})});for(let index=0;index<20;index++)await Promise.resolve();assert.equal(evaluate('state.auth.authenticated'),true);assert.equal(evaluate('state.auth.identity.student_id'),'PERSONAL-C');assert.equal(evaluate('state.agentProgressPoll'),null);
""")

    def test_history_load_is_lazy_and_scrolling_tracks_progress_revisions(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-A',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={};state.agentStatus={data_kind:'personal_source',personal_data_use:{version:'personal-retrieval-v1'}};state.agentTurns=[{run_id:'HISTORY-RUN',status:'completed',message:'旧问题',reply:'旧回答',tool_results:[],citations:[]}];renderAssistant()");assert.match(element('#panel-assistant').innerHTML,/处理过程 · 点击载入/);context.fetch=async(path,options)=>{assert.equal(path,'/api/chat/run');assert.deepEqual(JSON.parse(options.body),{run_id:'HISTORY-RUN'});return {ok:true,json:async()=>({run:{run_id:'HISTORY-RUN',status:'completed',thinking:false},events:[]})};};await evaluate("loadAgentProcess('HISTORY-RUN')");assert.match(element('#panel-assistant').innerHTML,/本轮普通模式，未开启深度思考/);assert.match(element('#panel-assistant').innerHTML,/本轮未返回供应商可见推理文本/);
const scroll=element('#agent-dialogue-scroll');let top=0;scroll.scrollHeight=2000;scroll.clientHeight=400;Object.defineProperty(scroll,'scrollTop',{get:()=>top,set:value=>{top=Math.max(0,Math.min(value,scroll.scrollHeight-scroll.clientHeight));}});evaluate("state.agentScroll={initialized:false,forceLatest:true,nearBottom:true,version:''};state.agentTurns=[{pending:true,progress:{status:'running',thinking:true,confirmed:true,events:[],next_sequence:1,active_reasoning:{call:1,text:'片段'}}}];renderAssistant()");assert.equal(top,1600);scroll.scrollTop=300;scroll.listeners.scroll();const before=evaluate('agentScrollVersion()');evaluate("state.agentTurns[0].progress.next_sequence=2;state.agentTurns[0].progress.active_reasoning.text='新的实际片段'");scroll.scrollHeight=2300;evaluate('renderAssistant()');assert.equal(top,300);assert.notEqual(evaluate('agentScrollVersion()'),before);assert.equal(evaluate('state.agentScroll.newMessages'),true);assert.doesNotMatch(element('#panel-assistant').innerHTML,/agent-personal-consent|personal-operation|personal-observation/);
""")


    def test_grade_summary_uses_all_matching_source_rows_and_distinguishes_missing_from_zero(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'PERSONAL-A',auth_mode:'school_client',security_realm:'personal_school_client'}}");
const summary={matched_records:60,numeric_grade_count:57,non_numeric_grade_count:2,unknown_grade_count:1,numeric_min:'45',numeric_max:'98',numeric_mean_unweighted:'86.74',coverage:{captured_rows:60,reported_total:75,has_more:true,complete_within_scope:false}};context.summary=summary;
const citation=evaluate("agentCitation({kind:'summary',citation_number:1,title:'来源成绩概况',source_grade_summary:summary,target_term:'ALL-HISTORY',data_source:'live'})");assert.match(citation,/\[1\] 来源成绩概况/);assert.match(citation,/匹配来源记录<\/dt><dd>60/);assert.match(citation,/86.74/);assert.match(citation,/全部匹配行，不受展示条数限制/);assert.match(citation,/不是 GPA、绩点、已获学分或修读认定/);assert.match(citation,/还有未读取内容/);assert.doesNotMatch(citation,/培养资料片段|PDF 第|numeric_mean_unweighted/);
const absent=evaluate("agentToolCard({name:'query_my_records',result:{operation:'major_academic',status:'not_ready',records:[],matched_records:null,returned_records:0,query_executed:false}})");assert.match(absent,/尚未取得成绩来源/);assert.doesNotMatch(absent,/0 条|0条|来源成绩概况/);
const zero=evaluate("agentToolCard({name:'query_my_records',result:{operation:'academic',status:'ok',records:[],matched_records:0,coverage:{captured_rows:0,complete_within_scope:true}}})");assert.match(zero,/当前已读范围 0 条/);assert.doesNotMatch(zero,/尚未取得成绩来源/);
const unknown=evaluate("agentToolCard({name:'query_my_records',result:{operation:'academic',status:'unknown',records:[],matched_records:null,coverage:{}}})");assert.match(unknown,/条数待确认/);assert.doesNotMatch(unknown,/0 条|0条/);
assert.match(evaluate("agentProcessStage({status:'running',thinking:true,events:[{kind:'tool.start',tool_name:'read_my_major_academic'}]})"),/正在读取主修成绩/);assert.match(evaluate("agentRunEventMarkup({kind:'tool.end',tool_name:'read_my_curriculum'})"),/读取培养资料/);
""")

    def test_streamed_reasoning_scroll_follows_latest_but_preserves_reader_position(self):
        self.run_js(r"""
const reasoning=element('#reasoning-fixture');reasoning.dataset={reasoningKey:'REQUEST-A'};reasoning.clientHeight=120;reasoning.scrollHeight=800;let top=0;Object.defineProperty(reasoning,'scrollTop',{get:()=>top,set:value=>{top=Math.max(0,Math.min(value,reasoning.scrollHeight-reasoning.clientHeight));}});context.document.querySelectorAll=selector=>selector==='.agent-process-reasoning'?[reasoning]:[];
evaluate('restoreAgentReasoningScroll()');assert.equal(top,680);reasoning.scrollTop=120;reasoning.listeners.scroll();reasoning.scrollHeight=1200;evaluate('restoreAgentReasoningScroll()');assert.equal(top,120);reasoning.scrollTop=1080;reasoning.listeners.scroll();reasoning.scrollHeight=1500;evaluate('restoreAgentReasoningScroll()');assert.equal(top,1380);evaluate('clearAgentConversation()');assert.equal(evaluate('Object.keys(state.agentReasoningScroll).length'),0);
""")

if __name__ == '__main__':
    unittest.main()
