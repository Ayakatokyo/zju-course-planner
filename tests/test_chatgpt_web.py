import unittest
from tests import test_web_contract as helper


@unittest.skipUnless(helper.NODE, "Node is needed for UI contracts")
class ChatGPTWebTests(unittest.TestCase):
    run_js = helper.WebContractTests.run_js

    def test_controls_show_real_levels_and_gpt_connection(self):
        self.run_js(r'''
evaluate("state.agentStatus={model:'deepseek-flash',thinking:true,reasoning_effort:'high'};state.agentModels=[{id:'deepseek-flash',name:'DeepSeek',effort:{supported_levels:['low','high','max'],default_level:'high'}}];state.agentChatGPT={connected:false,pending:false}");
assert.match(evaluate('renderAgentModelControls()'),/Continue with ChatGPT/);
assert.match(evaluate('renderAgentModelControls()'),/<details id="agent-model-menu"[\s\S]*推理等级[\s\S]*agent-chatgpt-connect/);
assert.doesNotMatch(evaluate('renderAgentThinking()'),/agent-reasoning-effort/);
let html=evaluate('renderAgentEffort()');assert.match(html,/value="high" selected/);assert.match(html,/value="max"/);assert.doesNotMatch(html,/value="medium"/);assert.match(html,/high（高）/);assert.match(html,/low（低）/);assert.match(html,/max（最高）/);assert.match(evaluate('renderAgentModelControls()'),/high（高）/);
evaluate('state.agentStatus.thinking=false');assert.match(evaluate('renderAgentEffort()'),/disabled/);
evaluate("state.agentStatus={model:'chatgpt:test',thinking:true,reasoning_effort:'xhigh',thinking_toggle:false};state.agentModels=[{id:'chatgpt:test',name:'GPT Test',effort:{supported_levels:['low','medium','high','xhigh'],default_level:'high'}}]");
html=evaluate('renderAgentEffort()');assert.match(html,/value="xhigh" selected/);assert.doesNotMatch(html,/value="max"/);assert.match(evaluate('renderAgentThinking()'),/disabled/);assert.match(evaluate('renderAgentModelControls()'),/GPT Test/);
evaluate("state.agentModels[0].effort={supported_levels:[],default_level:'default'}");html=evaluate('renderAgentEffort()');assert.match(html,/default（默认）/);assert.match(html,/disabled/);
''')

    def test_effort_is_sent_and_model_change_uses_new_capabilities(self):
        self.run_js(r'''
evaluate("personalChatReady=()=>true;renderAssistant=()=>{};renderAgentHistory=()=>{};sameAgentDiagnostics=()=>true;agentDataKind=()=> 'personal_source';state.agentStatus={model:'deepseek-flash',thinking:true,reasoning_effort:'max'};state.agentModels=[{id:'deepseek-flash',effort:{supported_levels:['low','high','max'],default_level:'low'}},{id:'chatgpt:test',thinking_toggle:false,effort:{supported_levels:['low','high'],default_level:'high'}}]");
const calls=[];context.api=async(path,body)=>{calls.push({path,body});return {...body,data_kind:'personal_source'};};
element('#agent-model').value='deepseek-flash';await evaluate("saveAgentSettings({reasoning_effort:'high'})");assert.equal(calls[0].body.reasoning_effort,'high');
await evaluate("saveAgentSettings({model:'chatgpt:test'})");assert.equal(calls[1].body.reasoning_effort,'high');assert.equal(calls[1].body.thinking,true);assert.equal(calls[1].body.model,'chatgpt:test');assert.equal(calls.length,2);
''')

    def test_connection_opens_only_official_auth_url_no_model_call(self):
        self.run_js(r'''
evaluate("renderAssistant=()=>{};sameAgentDiagnostics=()=>true;pollAgentChatGPT=()=>{};state.agentChatGPT={connected:false,pending:false}");
const popup={opener:{},location:{},close(){this.closed=true;}};context.window={open:()=>popup};context.URL=URL;
const calls=[];context.api=async(path,body)=>{calls.push(path);return {authorization_url:'https://auth.openai.com/api/accounts/authorize?state=synthetic'};};
await evaluate('connectAgentChatGPT()');assert.equal(popup.opener,null);assert.match(popup.location.href,/^https:\/\/auth.openai.com\//);assert.deepEqual(calls,['/api/chat/chatgpt/connect']);
evaluate('state.agentChatGPT.pending=false');popup.location.href='';context.api=async()=>({authorization_url:'https://evil.invalid/'});await evaluate('connectAgentChatGPT()');assert.equal(popup.location.href,'');assert.equal(popup.closed,true);
''')
