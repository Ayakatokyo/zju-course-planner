"""Execute session-draft restoration and ordering without model or school calls."""
import unittest
from tests import test_web_contract as web_contract


@unittest.skipUnless(web_contract.NODE, "Node is needed for draft UI contracts")
class PersonalDraftWebTests(unittest.TestCase):
    def run_js(self, script):
        setup = r"""
context.setTimeout=()=>1;context.clearTimeout=()=>{};
evaluate("state.auth={authenticated:true,identity:{student_id:'SYNTHETIC-OWNER',auth_mode:'school_client',security_realm:'personal_school_client'}};state.csrfToken='memory-only';state.boot={drafts:{available:true,storage:'server_session_memory'}};state.agentStatus={configured:true,available:true,remote:true,data_kind:'personal_source',personal_data_use:{version:'personal-retrieval-v1'}}");
const envelope=(text='',conversation_id=null,revision=0)=>({data_kind:'personal_source',draft:{text,conversation_id,revision,expires_at:text?'2099-01-01T00:00:00Z':null}});
const ok=value=>({ok:true,json:async()=>value});
const settle=async()=>{for(let n=0;n<30;n++)await Promise.resolve();};
"""
        web_contract.WebContractTests.run_js(self, setup+script)

    def test_restore_unsubmitted_text_without_sending_it_or_reading_school_sources(self):
        self.run_js(r"""
const calls=[];context.fetch=async(path,options)=>{calls.push(path);assert.equal(options.cache,'no-store');return ok(envelope('尚未发送的合成问题',null,2));};
await evaluate('restoreAgentDraft()');assert.deepEqual(calls,['/api/chat/draft']);assert.equal(evaluate('state.agentDraft'),'尚未发送的合成问题');assert.equal(element('#agent-draft').value,'尚未发送的合成问题');assert.equal(evaluate('state.agentTurns.length'),0);assert.equal(evaluate('state.agentDraftSync.savedText'),'尚未发送的合成问题');assert.doesNotMatch(element('#panel-assistant').innerHTML,/agent-draft-status|草稿已在本机暂存/);assert.equal(element('#agent-send').disabled,false);assert.equal(element('#agent-send').getAttribute('aria-disabled'),'false');
""")

    def test_restore_target_history_before_its_followup_and_keep_history_failure_safe(self):
        self.run_js(r"""
const id='12345678-1234-1234-1234-123456789abc',calls=[];
context.fetch=async(path)=>{calls.push(path);if(path==='/api/chat/draft')return ok(envelope('接着比较已有方案',id,4));assert.equal(path,'/api/chat/history');return ok({data_kind:'personal_source',conversation_id:id,turn_id:2,turns:[{status:'completed',reply:'历史回答',message:'历史问题',tool_results:[],citations:[]}]});};
await evaluate('restoreAgentDraft()');assert.deepEqual(calls,['/api/chat/draft','/api/chat/history']);assert.equal(evaluate('state.agentConversationId'),id);assert.equal(evaluate('state.agentDraft'),'接着比较已有方案');assert.equal(evaluate('state.agentTurnId'),2);
evaluate("resetAgentDraftSync();state.agentConversationId=null;state.agentTurns=[];state.agentDraft=''");
context.fetch=async(path)=>path==='/api/chat/draft'?ok(envelope('原草稿',id,4)):{ok:false,status:503,text:async()=>JSON.stringify({error:'history temporarily unavailable'})};
await evaluate('restoreAgentDraft()');assert.equal(evaluate('state.agentDraft'),'');assert.equal(evaluate('state.agentConversationId'),null);assert.match(evaluate('state.agentDraftSync.error'),/草稿暂未恢复/);
""")

    def test_late_restore_cannot_replace_new_input_and_only_new_input_is_saved(self):
        self.run_js(r"""
let release;const writes=[];context.fetch=async(path,options)=>{assert.equal(path,'/api/chat/draft');if(!options.method)return new Promise(resolve=>release=resolve);const body=JSON.parse(options.body);writes.push(body);assert.equal(options.keepalive,true);assert.equal(options.headers['X-CSRF-Token'],'memory-only');return ok(envelope(body.text,body.conversation_id,body.expected_revision+1));};
const restoring=evaluate('restoreAgentDraft()');evaluate("state.agentDraft='新输入';queueAgentDraftSave();updateAgentSend()");assert.equal(evaluate('agentCanSend()'),false);assert.equal(element('#agent-send').disabled,true);release(ok(envelope('旧草稿',null,5)));await restoring;await settle();assert.equal(evaluate('state.agentDraft'),'新输入');assert.deepEqual(writes,[{text:'新输入',conversation_id:null,expected_revision:5}]);assert.equal(evaluate('state.agentDraftSync.dirty'),false);assert.equal(evaluate('agentCanSend()'),true);assert.equal(element('#agent-send').disabled,false);assert.equal(element('#agent-send').getAttribute('aria-disabled'),'false');
""")

    def test_failed_initial_restore_enables_sending_existing_input(self):
        self.run_js(r"""
let release;const calls=[];context.fetch=async(path)=>{calls.push(path);return new Promise(resolve=>release=resolve);};
const restoring=evaluate('restoreAgentDraft()');evaluate("state.agentDraft='恢复期间输入的问题';queueAgentDraftSave();updateAgentSend()");assert.equal(element('#agent-send').disabled,true);
release({ok:false,status:503,text:async()=>JSON.stringify({error:'draft temporarily unavailable'})});await restoring;await settle();assert.equal(evaluate('state.agentDraft'),'恢复期间输入的问题');assert.equal(evaluate('agentCanSend()'),true);assert.equal(element('#agent-send').disabled,false);assert.equal(element('#agent-send').getAttribute('aria-disabled'),'false');assert.match(evaluate('state.agentDraftSync.error'),/草稿暂未恢复/);assert.deepEqual(calls,['/api/chat/draft']);
""")

    def test_serial_writes_keep_latest_text_without_overlapping_or_saving_on_render(self):
        self.run_js(r"""
const bodies=[],releases=[];context.fetch=async(path,options)=>{assert.equal(path,'/api/chat/draft');const body=JSON.parse(options.body);bodies.push(body);return new Promise(resolve=>releases.push(()=>resolve(ok(envelope(body.text,body.conversation_id,body.expected_revision+1)))));};
evaluate("state.agentDraftSync.enabled=true;state.agentDraftSync.revision=0;state.agentDraft='第一版';queueAgentDraftSave(true)");assert.equal(bodies.length,1);
evaluate("state.agentDraft='第二版';queueAgentDraftSave(true);state.agentDraft='最终版';queueAgentDraftSave(true);renderAssistant();renderAssistant()");assert.equal(bodies.length,1);
releases.shift()();await settle();assert.equal(bodies.length,2);assert.equal(bodies[1].text,'最终版');assert.equal(bodies[1].expected_revision,1);releases.shift()();await settle();assert.equal(evaluate('state.agentDraftSync.savedText'),'最终版');assert.equal(evaluate('state.agentDraftSync.dirty'),false);
""")

    def test_conflict_preserves_current_input_and_never_retries_it_without_new_edit(self):
        self.run_js(r"""
const calls=[];context.fetch=async(path,options)=>{calls.push(options.method||'GET');return options.method?{ok:false,status:409,text:async()=>JSON.stringify({code:'personal_draft_revision_conflict',error:'conflict'})}:ok(envelope('另一页面草稿',null,9));};
evaluate("state.agentDraftSync.enabled=true;state.agentDraftSync.revision=2;state.agentDraft='当前输入';queueAgentDraftSave(true)");await settle();assert.deepEqual(calls,['POST','GET']);assert.equal(evaluate('state.agentDraft'),'当前输入');assert.equal(evaluate('state.agentDraftSync.revision'),9);assert.match(evaluate('state.agentDraftSync.error'),/其他页面已更新/);
evaluate('renderAssistant()');assert.doesNotMatch(element('#panel-assistant').innerHTML,/agent-draft-status|其他页面已更新草稿|尚未暂存/);
await evaluate('saveAgentDraft()');await settle();assert.deepEqual(calls,['POST','GET']);
""")

    def test_identity_reset_discards_late_restore_and_late_write_acknowledgement(self):
        self.run_js(r"""
let release;context.fetch=async()=>new Promise(resolve=>release=resolve);
const pending=evaluate('restoreAgentDraft()');evaluate("clearWorkspace();state.auth={authenticated:true,identity:{student_id:'OTHER',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={drafts:{available:true,storage:'server_session_memory'}};state.agentDraft='另一会话的新输入'");release(ok(envelope('先前会话草稿',null,8)));await pending;assert.equal(evaluate('state.agentDraft'),'另一会话的新输入');assert.equal(evaluate('state.agentDraftSync.revision'),null);
evaluate("state.agentDraftSync.enabled=true;state.agentDraftSync.revision=0;queueAgentDraftSave(true)");evaluate("clearWorkspace();state.agentDraft='新页面';");release(ok(envelope('迟到保存',null,1)));await settle();assert.equal(evaluate('state.agentDraft'),'新页面');assert.equal(evaluate('state.agentDraftSync.revision'),null);
""")

    def test_source_reset_discards_late_restore_even_when_login_identity_is_unchanged(self):
        self.run_js(r"""
let release;context.fetch=async()=>new Promise(resolve=>release=resolve);const pending=evaluate('restoreAgentDraft()');evaluate('clearPersonalLocal()');release(ok(envelope('来源失效前的草稿',null,1)));await pending;assert.equal(evaluate('state.agentDraft'),'');assert.equal(evaluate('state.agentDraftSync.revision'),null);
""")

    def test_send_flushes_the_last_edit_before_dispatch_and_does_not_restore_sent_text(self):
        self.run_js(r"""
const calls=[];let stored=envelope('较早片段',null,1);
context.fetch=async(path,options)=>{calls.push(path);if(path==='/api/chat/draft'){if(options.method){const b=JSON.parse(options.body);assert.equal(b.text,'完整的最后编辑');stored=envelope(b.text,b.conversation_id,b.expected_revision+1);}return ok(stored);}if(path==='/api/chat/turn'){assert.equal(stored.draft.text,'完整的最后编辑');stored=envelope('',null,3);return ok({conversation_id:'12345678-1234-1234-1234-123456789abc',turn_id:1,status:'failed',code:'model_http_402',reply:'blocked fixture',tool_results:[],citations:[],data_kind:'personal_source'});}if(path==='/api/chat/progress')return ok({status:'completed',terminal:true,events:[],available:false});if(path.startsWith('/api/chat/conversations'))return ok({data_kind:'personal_source',conversations:[]});throw Error(path);};
evaluate("state.agentDraftSync.enabled=true;state.agentDraftSync.revision=1;state.agentDraftSync.savedText='较早片段';state.agentDraft='完整的最后编辑';queueAgentDraftSave()");await evaluate('sendAgentTurn()');assert.ok(calls.indexOf('/api/chat/draft')<calls.indexOf('/api/chat/turn'));assert.equal(evaluate('state.agentDraft'),'');assert.equal(evaluate('state.agentDraftSync.savedText'),'');assert.equal(evaluate('state.agentDraftSync.revision'),3);
""")

    def test_sending_suspends_pending_autosave_without_restoring_sent_text(self):
        self.run_js(r"""
let release;const writes=[];context.fetch=async(path,options)=>{const body=JSON.parse(options.body);writes.push(body);return new Promise(resolve=>release=resolve);};
evaluate("state.agentDraftSync.enabled=true;state.agentDraftSync.revision=0;state.agentDraft='准备发送';queueAgentDraftSave(true)");
evaluate("suspendAgentDraftForSend();state.agentDraft=''");release(ok(envelope('准备发送',null,1)));await settle();assert.equal(writes.length,1);assert.equal(evaluate('state.agentDraft'),'');assert.equal(evaluate('state.agentDraftSync.dirty'),false);assert.equal(evaluate('state.agentDraftSync.savedText'),'');
""")

    def test_regeneration_preserves_unflushed_followup_while_the_model_is_running(self):
        self.run_js(r"""
const nodes={};const node=key=>nodes[key]||(nodes[key]={value:'',textContent:'',disabled:false,onclick:null,addEventListener(){}});
const dialog={querySelector:node,setAttribute(){},addEventListener(){},showModal(){},close(){},remove(){}};
context.document.createElement=()=>dialog;
context.document.querySelector=selector=>selector==='.message-dialog'?null:element(selector);
context.button={dataset:{turnIndex:'0'},closest:()=>({querySelector:()=>null}),isConnected:false};
const id='12345678-1234-1234-1234-123456789abc';context.id=id;
evaluate("state.agentConversationId=id;state.agentTurnId=1;state.agentTurns=[{message:'原问题',status:'completed',reply:'原回答',tool_results:[],citations:[]}];state.agentDraftSync.enabled=true;state.agentDraftSync.revision=1;state.agentDraftSync.savedText='追问旧片段';state.agentDraft='完整的新追问';queueAgentDraftSave()");
let stored=envelope('追问旧片段',id,1),release;const writes=[],sent=[];
context.fetch=async(path,options)=>{
  if(path==='/api/chat/draft'){
    if(options.method){const body=JSON.parse(options.body);writes.push(body);assert.equal(body.expected_revision,stored.draft.revision);stored=envelope(body.text,body.conversation_id,body.expected_revision+1);}
    return ok(stored);
  }
  if(path==='/api/chat/turn'){
    const body=JSON.parse(options.body);sent.push(body);
    stored=stored.draft.text.trim()===body.message?envelope('',null,stored.draft.revision+1):envelope(stored.draft.text,stored.draft.conversation_id,stored.draft.revision+1);
    return new Promise(resolve=>release=()=>resolve(ok({conversation_id:id,turn_id:2,status:'completed',reply:'重新回答结果',tool_results:[],citations:[],data_kind:'personal_source'})));
  }
  if(path==='/api/chat/progress')return ok({status:'completed',terminal:true,events:[],available:false});
  if(path.startsWith('/api/chat/conversations'))return ok({data_kind:'personal_source',conversations:[]});
  throw Error(path);
};
evaluate("openMessageDialog(button,'regenerate')");const executing=node('.dialog-confirm').onclick();await settle();
assert.equal(sent.length,1);assert.equal(sent[0].message,'原问题');assert.equal(evaluate('state.agentBusy'),true);
assert.equal(stored.draft.text,'完整的新追问');assert.equal(stored.draft.conversation_id,id);
assert.deepEqual(writes,[{text:'完整的新追问',conversation_id:id,expected_revision:1}]);
release();await executing;assert.equal(evaluate('state.agentDraft'),'完整的新追问');
assert.equal(evaluate('state.agentTurns.length'),2);assert.equal(evaluate('state.agentTurns[1].reply'),'重新回答结果');
assert.equal(stored.draft.text,'完整的新追问');
""")

    def test_regeneration_waits_for_draft_and_stops_after_cancel_or_save_failure(self):
        self.run_js(r"""
for(const mode of ['cancel','identity','save-failed','save-conflict','double-click']){
  const nodes={};const node=key=>nodes[key]||(nodes[key]={value:'',textContent:'',disabled:false,onclick:null,addEventListener(){}});
  const dialog={querySelector:node,setAttribute(){},addEventListener(){},showModal(){},close(){},remove(){}};
  context.document.createElement=()=>dialog;
  context.document.querySelector=selector=>selector==='.message-dialog'?null:element(selector);
  context.button={dataset:{turnIndex:'0'},closest:()=>({querySelector:()=>null}),isConnected:false};
  evaluate("clearWorkspace();state.auth={authenticated:true,identity:{student_id:'SYNTHETIC-OWNER',auth_mode:'school_client',security_realm:'personal_school_client'}};state.boot={drafts:{available:true,storage:'server_session_memory'}};state.agentStatus={configured:true,available:true,remote:true,data_kind:'personal_source',personal_data_use:{version:'personal-retrieval-v1'}};state.agentDraftSync.enabled=true;state.agentDraftSync.revision=1;state.agentDraft='待保存追问';state.agentTurns=[{message:'原问题',status:'completed',reply:'原回答',tool_results:[],citations:[]}];queueAgentDraftSave()");
  let release;const writes=[],sent=[];
  context.fetch=async(path,options)=>{
    if(path==='/api/chat/draft'){
      if(!options.method)return ok(envelope('另一页面草稿',null,9));
      writes.push(JSON.parse(options.body));return new Promise(resolve=>release=resolve);
    }
    if(path==='/api/chat/turn'){sent.push(JSON.parse(options.body));return ok({conversation_id:'12345678-1234-1234-1234-123456789abc',turn_id:1,status:'completed',reply:'结果',tool_results:[],citations:[],data_kind:'personal_source'});}
    if(path==='/api/chat/progress')return ok({status:'completed',terminal:true,events:[],available:false});
    if(path.startsWith('/api/chat/conversations'))return ok({data_kind:'personal_source',conversations:[]});
    throw Error(path);
  };
  evaluate("openMessageDialog(button,'regenerate')");const executing=node('.dialog-confirm').onclick();await settle();
  assert.equal(node('.dialog-confirm').disabled,true,mode);assert.equal(sent.length,0,mode);
  assert.equal(writes.length,1);assert.equal(writes[0].text,'待保存追问');
  if(mode==='cancel')node('.dialog-cancel').onclick();
  if(mode==='identity')evaluate("clearWorkspace();state.auth={authenticated:true,identity:{student_id:'SYN-B',auth_mode:'school_client',security_realm:'personal_school_client'}};state.agentDraft='新账号草稿'");
  if(mode==='double-click')await node('.dialog-confirm').onclick();
  const failed=mode==='save-failed'||mode==='save-conflict';
  release(failed?{ok:false,status:mode==='save-conflict'?409:503,text:async()=>JSON.stringify({code:mode==='save-conflict'?'personal_draft_revision_conflict':'local_service_unavailable'})}:ok(envelope('待保存追问',null,2)));
  await executing;assert.equal(sent.length,mode==='double-click'?1:0,mode);
  assert.equal(evaluate('state.agentDraft'),mode==='identity'?'新账号草稿':'待保存追问',mode);
  if(failed)assert.match(node('[role="status"]').textContent,/当前输入尚未暂存/);
  if(mode==='save-conflict')assert.equal(evaluate('state.agentDraftSync.revision'),9);
  assert.equal(writes.length,1,mode);
}
""")


if __name__ == '__main__':
    unittest.main()
