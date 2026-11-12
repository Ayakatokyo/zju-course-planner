'use strict';
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
const clone = value => JSON.parse(JSON.stringify(value));
const rows = value => Array.isArray(value) ? value : (value?.records || []);
const showValue = value => value === null || value === undefined ? '待确认' : (typeof value === 'object' ? JSON.stringify(value) : String(value));
const state = {agentHistoryQuery:'',agentHistoryTimer:null,agentHistoryRequest:0,agentHistorySearch:'',schoolGradeSort:'source',auth:null, csrfToken:null, loadedSaved:null, comparison:null, savedComparison:null, compareSavedIds:[], agentDraft:'', agentStatus:null, agentTurns:[], agentConversationId:null, agentTurnId:0, agentConsent:false,  agentBusy:false, agentActionBusy:false, agentActionRetries:{}, agentOptionReceipts:{}, agentEpoch:0, agentHistory:[],agentHistoryLoading:false,agentHistoryError:'',agentHistoryHasMore:false,agentHistoryLoadBusy:false,agentHistoryPending:false, previewBusy:false, boot:null, mode:'demo', tab:'assistant', constraints:{}, lastConstraints:null, generated:null, selected:0, audit:null, sourceAudit:null, saved:[], week:1, season:'冬', dirty:false, busy:false};
state.agentProgressPoll=null;
state.agentHistoryPoll=null;
state.agentDraftComposing=false;
state.agentDraftSync=newAgentDraftSync();
Object.assign(state,{agentModels:[],agentModelsStatus:'idle',agentModelsBusy:false,agentSettingsBusy:false,agentModelsError:'',agentChatGPT:null,agentAuthPolling:false,agentRunDetails:{},agentScroll:{initialized:false,top:0,nearBottom:true,forceLatest:true,newMessages:false,version:''}});
Object.assign(state,{schoolEpoch:0,schoolBusy:false,schoolEnrollment:null,schoolResult:null,schoolProfile:null,schoolStatus:null,schoolOperation:'academic',schoolYear:'2026-2027',schoolSeason:'AW',schoolGradePage:1,schoolGradeSearch:'',schoolGradeYear:'',schoolChallengeId:null,schoolReauth:false,schoolCaptchaUrl:null,schoolNeedsCaptcha:false,schoolPending:null,schoolQueryChallengeId:null});
const statusNames = {pass:['满足','success'],fail:['不满足','danger'],satisfied:['满足','success'],unsatisfied:['不满足','danger'],pending:['待确认','warning'],unknown:['待确认','warning'],valid:['可行','success'],invalid:['不满足','danger'],passed:['满足','success'],failed:['不满足','danger'],needs_confirmation:['待确认','warning'],not_applicable:['不适用',''],candidate_pending_human_review:['待审核','warning']};
function statusBadge(result) {const [name, cls] = statusNames[result] || [showValue(result),'']; return `<span class="badge ${cls}">${esc(name)}</span>`;}
function notice(text, kind = 'warning'){return `<div class="notice ${kind}"><span class="notice-icon" aria-hidden="true">${kind === 'success' ? '✓' : 'ⓘ'}</span><p>${esc(text)}</p></div>`;}
function message(text, error = false) {const element = $('#global-status'); element.textContent=text; element.className=`status-message${error?' error':''}`; element.hidden=false;}
function clearMessage() {$('#global-status').hidden=true;}
function identityKey(identity){return identity?JSON.stringify([identity.student_id,identity.security_realm,identity.auth_mode]):null;}
function isAcademicIdentity(){const identity=state.auth?.identity;return state.auth?.authenticated===true&&((identity?.auth_mode==='school_client'&&identity?.security_realm==='personal_school_client')||(identity?.auth_mode==='synthetic_account'&&identity?.security_realm==='curriculum_synthetic_accounts'));}
function clearPersonalLocal(){resetSchoolPresentation();if(isAcademicIdentity()){resetAgentHistorySearch();resetAgentDraftSync();clearAgentConversation();state.agentHistory=[];state.agentHistoryError='';state.agentHistoryHasMore=false;state.agentStatus=null;renderAgentHistory();renderAssistant();}}
function clearSchoolWorkspace(){resetSchoolPresentation();resetAgentDraftSync();state.schoolEpoch+=1;clearAgentDiagnostics();clearPersonalLocal();if(state.schoolCaptchaUrl)URL.revokeObjectURL(state.schoolCaptchaUrl);Object.assign(state,{schoolBusy:false,schoolEnrollment:null,schoolResult:null,schoolProfile:null,schoolStatus:null,schoolOperation:'academic',schoolYear:'2026-2027',schoolSeason:'AW',schoolGradePage:1,schoolGradeSearch:'',schoolGradeYear:'',schoolChallengeId:null,schoolReauth:false,schoolCaptchaUrl:null,schoolNeedsCaptcha:false,schoolPending:null,schoolQueryChallengeId:null});}
function clearAgentDiagnostics(){Object.assign(state,{agentModels:[],agentModelsStatus:'idle',agentModelsBusy:false,agentSettingsBusy:false,agentModelsError:'',agentChatGPT:null,agentAuthPolling:false,agentRunDetails:{}});}
function clearAgentConversation(){closeCitationPreview();document.querySelector('.message-dialog')?.remove?.();stopAgentProgress();stopAgentHistoryPoll();state.agentDraftComposing=false;state.agentEpoch+=1;if(state.agentModelsBusy)state.agentModelsStatus='idle';state.agentModelsBusy=false;state.agentSettingsBusy=false;state.agentScroll={initialized:false,top:0,nearBottom:true,forceLatest:true,newMessages:false,version:''};Object.assign(state,{agentRunDetails:{}});Object.assign(state,{agentDraft:'',agentTurns:[],agentConversationId:null,agentTurnId:0,agentConsent:false,agentBusy:false,agentActionBusy:false,agentActionRetries:{},agentOptionReceipts:{},agentRetry:null,agentHistoryLoadBusy:false,agentHistoryPending:false,agentHistoryLoading:false});const draft=$('#agent-draft');if(draft)draft.value='';}
function clearWorkspace() {
  resetAgentHistorySearch();resetAgentDraftSync();clearAgentConversation();clearSchoolWorkspace();state.agentStatus=null;
  Object.assign(state,{agentHistorySearch:'',schoolGradeSort:'source',mode:'demo',tab:'assistant',agentDraft:'',previewBusy:false,week:1,season:'冬',boot:null,csrfToken:null,loadedSaved:null,comparison:null,savedComparison:null,compareSavedIds:[],constraints:{},lastConstraints:null,generated:null,audit:null,sourceAudit:null,saved:[],agentHistory:[],agentHistoryError:'',agentHistoryHasMore:false,selected:0,dirty:false,busy:false});
  $$('.tab-panel').forEach(panel=>{panel.innerHTML='';panel.hidden=true;});
  $('#mode-badge').textContent='合成演示';$('#mode-badge').className='badge synthetic';$('#session-control').hidden=true;
  $('.nav-tabs').hidden=true;$('#conversation-sidebar').hidden=true;$('#conversation-list').innerHTML='';if($('#agent-history-search'))$('#agent-history-search').value='';
}
function renderAuth() {
  const auth=state.auth || {};const authenticated=auth.authenticated===true;
  $('.nav-tabs').hidden=!authenticated;$('#session-control').hidden=!authenticated;$('#conversation-sidebar').hidden=!authenticated;$('#tab-saved').hidden=isAcademicIdentity();
  $('#auth-panel').hidden=authenticated;
  $('#workspace-navigation').hidden=!authenticated||!isAcademicIdentity();
  if(authenticated){$('#logout').setAttribute('aria-label',`退出 ${auth.identity?.name || '当前账号'}`);return;}
  const schoolEnabled=auth.academic_login_enabled===true||auth.school_client_enabled===true;const badge=$('#mode-badge');
  badge.textContent=schoolEnabled?(auth.mock_enabled||auth.scenario_accounts_enabled?'登录入口':'本人教务'):(auth.mock_enabled?'合成演示':'未登录');badge.className=auth.mock_enabled&&!schoolEnabled?'badge synthetic':'badge';
  $$('.tab-panel').forEach(panel=>panel.hidden=true);
  $('#auth-panel').innerHTML=`<div class="auth-card"><h1>${schoolEnabled?'登录教务助手':auth.mock_enabled?'进入合成演示':'学校登录未接入'}</h1>${schoolEnabled?`${notice(auth.scenario_accounts_enabled?'使用学校账号或已配置的模拟账号登录。密码不会保存。':'通过学校统一认证登录，仅在本机读取本人教务资料。密码不会保存。','neutral')}${schoolCredentialForm(false)}`:''}${auth.mock_enabled?`<div class="auth-profile-list">${(auth.mock_profiles||[]).map(profile=>`<button class="button primary" data-action="mock-login" data-profile-id="${esc(profile.profile_id)}" ${state.busy?'disabled':''}>${esc(profile.name)}</button>`).join('')}</div>`:!schoolEnabled?notice('学校认证和个人学业数据源尚未接入。'):''}</div>`;
  bindSchoolCredentialForm(false);
}
async function request(path, body, expectedEpoch, keepalive=false) {
  const session={owner:identityKey(state.auth?.identity),epoch:state.schoolEpoch,csrfToken:state.csrfToken};
  const options={cache:'no-store',credentials:'same-origin'};
  if(keepalive)options.keepalive=true;
  if(body!==undefined){options.method='POST';options.headers={'Content-Type':'application/json'};if(state.csrfToken)options.headers['X-CSRF-Token']=state.csrfToken;options.body=JSON.stringify(body);}
  let response;
  try{response=await fetch(path,options);}
  catch{throw Object.assign(new Error('暂时无法连接本机服务，请确认服务正在运行后重试。'),{code:'local_service_unavailable'});}
  if(!response.ok){
    let detail={};
    try{
      const parsed=JSON.parse(await response.text());
      if(parsed&&typeof parsed==='object'&&!Array.isArray(parsed))detail=parsed;
    }catch{}
    const sameSession=session.owner===identityKey(state.auth?.identity)&&session.epoch===state.schoolEpoch&&session.csrfToken===state.csrfToken;
    if(response.status===401&&!['/api/auth/school-login','/api/auth/login'].includes(path)&&sameSession&&(expectedEpoch===undefined||expectedEpoch===state.agentEpoch)){clearWorkspace();state.auth={...(state.auth||{}),authenticated:false};renderAuth();message('本机会话已失效，请重新登录。',true);}
    const explanation=[detail.error?.message,detail.error,detail.message].find(value=>typeof value==='string'&&value);
    const error=new Error(explanation||`请求失败（${response.status}），请重试。`);
    error.code=detail.code||detail.status;error.status=response.status;error.challengeId=detail.challenge_id;throw error;
  }
  return response;
}
async function api(path,body,expectedEpoch,keepalive=false){
  const response=await request(path,body,expectedEpoch,keepalive);
  try{return await response.json();}
  catch{throw Object.assign(new Error('本机服务返回的数据不完整或格式无效，请重试。'),{code:'local_response_invalid'});}
}

function newAgentDraftSync(){return {enabled:false,loading:false,busy:false,pending:null,revision:null,edit:0,dirty:false,savedText:'',timer:null,error:''};}
function resetAgentDraftSync(){if(state.agentDraftSync?.timer!=null)clearTimeout(state.agentDraftSync.timer);state.agentDraftSync=newAgentDraftSync();}
function agentDraftSupported(){return isAcademicIdentity()&&state.boot?.drafts?.available===true&&state.boot.drafts.storage==='server_session_memory';}
function draftRequestCapture(){return {sync:state.agentDraftSync,owner:identityKey(state.auth?.identity),schoolEpoch:state.schoolEpoch,epoch:state.agentEpoch};}
function sameDraftRequest(c){return c.sync===state.agentDraftSync&&isAcademicIdentity()&&c.owner===identityKey(state.auth?.identity)&&c.schoolEpoch===state.schoolEpoch;}
function validAgentDraftEnvelope(value){
  const d=value?.draft;
  return value?.data_kind===agentDataKind()&&Object.keys(value).length===2&&d&&Object.keys(d).length===4&&typeof d.text==='string'&&d.text.length<=2000&&Number.isSafeInteger(d.revision)&&d.revision>=0&&(d.conversation_id===null||typeof d.conversation_id==='string'&&/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(d.conversation_id))&&(d.expires_at===null||typeof d.expires_at==='string'&&Number.isFinite(Date.parse(d.expires_at)))&&(!d.text?(d.conversation_id===null&&d.expires_at===null):d.expires_at!==null);
}
function queueAgentDraftSave(immediate=false){
  if(!agentDraftSupported())return;
  const s=state.agentDraftSync;s.edit+=1;s.dirty=true;s.error='';
  if(s.timer!=null)clearTimeout(s.timer);s.timer=null;
  if(s.enabled&&!s.loading&&!s.busy){if(immediate)void saveAgentDraft();else s.timer=setTimeout(()=>{s.timer=null;void saveAgentDraft();},350);}
}
function saveAgentDraft(){
  const s=state.agentDraftSync;if(s.pending)return s.pending;
  if(!agentDraftSupported()||!s.enabled||s.loading||s.busy||!s.dirty||s.error)return Promise.resolve();
  s.pending=performAgentDraftSave().finally(()=>{s.pending=null;if(s===state.agentDraftSync&&s.dirty&&!s.error)void saveAgentDraft();});return s.pending;
}
async function performAgentDraftSave(){
  const c=draftRequestCapture(),s=c.sync;
  if(state.agentDraft.length>2000){s.error='草稿超过暂存长度，当前文字仍保留。';return;}
  s.busy=true;s.error='';
  try{
    if(s.revision===null){const read=await api('/api/chat/draft',undefined,c.epoch);if(!sameDraftRequest(c))return;if(!validAgentDraftEnvelope(read))throw Error('invalid_draft_response');s.revision=read.draft.revision;}
    const edit=s.edit,snapshot={text:state.agentDraft,conversation_id:state.agentDraft?state.agentConversationId:null,expected_revision:s.revision};
    const value=await api('/api/chat/draft',snapshot,c.epoch,true);
    if(!sameDraftRequest(c))return;if(!validAgentDraftEnvelope(value))throw Error('invalid_draft_response');
    s.revision=Math.max(s.revision,value.draft.revision);
    if(edit===s.edit){s.dirty=false;s.savedText=snapshot.text;}
  }catch(error){
    if(!sameDraftRequest(c))return;
    s.error=error.code==='personal_draft_revision_conflict'?'其他页面已更新草稿，当前输入保留，尚未暂存。':'草稿暂未保存，当前输入仍保留。';
    if(error.code==='personal_draft_revision_conflict'){try{const latest=await api('/api/chat/draft',undefined,c.epoch);if(sameDraftRequest(c)&&validAgentDraftEnvelope(latest))s.revision=Math.max(s.revision,latest.draft.revision);}catch{}}
  }finally{if(sameDraftRequest(c)){s.busy=false;}}
}
async function restoreAgentDraft(){
  if(!agentDraftSupported())return;
  const c=draftRequestCapture(),s=c.sync;if(s.loading||s.enabled)return;
  const edit=s.edit;s.loading=true;
  try{
    const value=await api('/api/chat/draft',undefined,c.epoch);
    if(!sameDraftRequest(c))return;if(!validAgentDraftEnvelope(value))throw Error('invalid_draft_response');
    s.revision=value.draft.revision;s.enabled=true;
    if(edit===s.edit&&value.draft.text){
      if(value.draft.conversation_id){await loadAgentConversation(value.draft.conversation_id,{fromDraft:true});if(!sameDraftRequest(c)||edit!==s.edit)return;if(state.agentConversationId!==value.draft.conversation_id)throw Error('draft_conversation_unavailable');}
      state.agentDraft=value.draft.text;s.savedText=value.draft.text;renderAssistant();
    }
  }catch{if(sameDraftRequest(c)){s.enabled=true;s.error='草稿暂未恢复，当前输入仍保留。';}}
  finally{if(sameDraftRequest(c)){s.loading=false;updateAgentSend();if(s.dirty)void saveAgentDraft();}}
}
async function flushAgentDraftBeforeSend(){const s=state.agentDraftSync;if(!agentDraftSupported()||!s.enabled)return;if(s.timer!=null)clearTimeout(s.timer);s.timer=null;if(s.pending)await s.pending;if(s===state.agentDraftSync&&s.dirty&&!s.error)await saveAgentDraft();if(s===state.agentDraftSync&&s.pending)await s.pending;}
function suspendAgentDraftForSend(){const s=state.agentDraftSync;if(s.timer!=null)clearTimeout(s.timer);s.timer=null;s.edit+=1;s.dirty=false;s.savedText='';s.error='';}
async function refreshAgentDraftRevision(){
  if(!agentDraftSupported()||!state.agentDraftSync.enabled)return;
  const c=draftRequestCapture();try{const value=await api('/api/chat/draft',undefined,c.epoch);if(sameDraftRequest(c)&&validAgentDraftEnvelope(value))c.sync.revision=Math.max(c.sync.revision??0,value.draft.revision);}catch{}
}
if(typeof window!=='undefined'&&window.addEventListener)window.addEventListener('pagehide',()=>{const s=state.agentDraftSync;if(s.timer!=null)clearTimeout(s.timer);s.timer=null;if(s.dirty&&!s.busy&&!s.error)void saveAgentDraft();});
function demo(){return state.boot?.demo || {};}
function planDataset(){return state.loadedSaved?.frozen_dataset || demo();}
function planCredits(plan){
  const validation=plan?.validation || {};
  if(Object.hasOwn(validation,'planned_credits'))return validation.planned_credits;
  if(Object.hasOwn(validation,'total_credits'))return validation.total_credits;
  if(Object.hasOwn(plan||{},'total_credits'))return plan.total_credits;
  const values=planSections(plan).map(section=>section.credits);
  return values.length && values.every(value=>value!==null&&value!==undefined&&Number.isFinite(Number(value)))?values.reduce((sum,value)=>sum+Number(value),0):null;
}
function academicStatus(record){return record.completion_status ?? record.normalized_status ?? record.status ?? 'unknown';}
function academicMeasurements(record){
  const values=[];
  for(const [field,label] of [['earned_credits','已获学分'],['planned_credits','计划学分'],['recognized_hours','认定学时'],['classroom_points','课堂记点']]){
    if(Object.hasOwn(record,field))values.push(`${label} ${showValue(record[field])}`);
  }
  if(!values.length)values.push('获分与认定数值待确认');
  return values;
}
function source(){return state.boot?.source || {};}
function sections(){return rows(planDataset().offering_snapshot);}
function courseMap(){return new Map(rows(demo().courses).map(item=>[item.course_id,item]));}
function sectionMap(){return new Map(sections().map(item=>[item.section_id,item]));}
function currentPlan(){return state.generated?.plans?.[state.selected] || state.generated?.pending_plans?.[state.selected] || null;}
function planSections(plan){const map=sectionMap();return plan?.sections || (plan?.section_ids || []).map(id=>map.get(id)).filter(Boolean);}
function planName(plan,index=0){return plan?.title || `${/^U\d+$/.test(plan?.label||'')?'待确认方案':'方案'} ${plan?.label || String.fromCharCode(65+index)}`;}
const dayNames = ['','周一','周二','周三','周四','周五','周六','周日'];
function meetingText(meeting) {
  const weeks=meeting.week_numbers || []; const periods=meeting.period_indices || []; const seasons=meeting.season_codes || [];
  const weekLabel=weeks.length ? (weeks.length>2 && weeks.every((week,index)=>!index||week===weeks[index-1]+1) ? `${weeks[0]}–${weeks.at(-1)}周` : `${weeks.join('、')}周`) : '周次待确认';
  return `${seasons.join('/') || '分学期待确认'} · ${weekLabel} · ${dayNames[meeting.day_of_week] || '星期待确认'} · ${periods.length?`${periods.join('、')}节`:'节次待确认'}`;
}
function sourceLinks(refs) {
  const aliases={'document (1).pdf':'business_analytics_2026.pdf','document-2.pdf':'materials_2026.pdf','document-3.pdf':'journalism_2026.pdf','course_slides.pdf':'course_slides.pdf'};
  return `<div class="source-links">${(refs || []).filter(ref=>ref&&typeof ref==='object').flatMap(ref=>{const filename=aliases[ref.file_name] || (Object.values(aliases).includes(ref.file_name)?ref.file_name:null); return filename?(ref.pdf_pages || [1]).map(page=>`<a href="/sources/${encodeURIComponent(filename)}#page=${Number(page)||1}" target="_blank" rel="noopener">${esc(ref.file_name)} · 第 ${esc(page)} 页 ↗</a>`):[`<span class="badge">${esc(ref.snapshot_id || ref.file_name || '演示依据')}</span>`];}).join('')}</div>`;
}
function dataNotice() {return state.mode==='demo' ? notice('当前为 synthetic 合成演示。学生、规则、教师、教学班、余量和校历仅用于计算验证，不能作为本人学业或学校当期开课事实。') : notice('本页只读取原交接包：3 份 2026 级培养方案、19 条课程定义、23 条待审核规则，真实教学班模板为空。Excel 与截图已用于字段设计，尚未接入本应用；学校认证和本人学业数据源均待确认。');}
function pageHeading(title,description,actions=''){return `<div class="page-heading"><div><h1>${esc(title)}</h1><p>${esc(description)}</p></div>${actions?`<div class="page-heading-actions">${actions}</div>`:''}</div>`;}
function snapshotLine(){const snapshot=planDataset().offering_snapshot || {};return `<div class="snapshot-line"><span class="tiny-dot" aria-hidden="true"></span><span>演示快照 ${esc(snapshot.snapshot_id || '待导入')}</span><span>· ${esc(snapshot.captured_at || '时间未提供')}</span></div>`;}
const agentExamples=[
  {label:'推荐合成选修课表',draft:'在这个合成场景中，我想补齐专业选修，优先考虑 AI 和计算材料方向。请核对模块要求，从可选课程中推荐两到三套有实际差异的秋冬课表，周五尽量空闲。'},
  {label:'保留体育，周五必须空闲',draft:'保留已经确认的体育安排，将周五尽量空闲改为周五必须空闲。请重新计算并说明还剩哪些合成课表。'},
  {label:'比较方案，再换课',draft:'请比较本轮方案 A 和 B 的课程、模块贡献、兴趣依据和周五安排。随后我想在方案 A 中替换一门可选课程，请先说明可替换对象。'}
];
function personalChatReady(){return isAcademicIdentity()&&state.agentStatus?.data_kind===agentDataKind()&&typeof state.agentStatus?.personal_data_use?.version==='string'&&state.agentStatus.personal_data_use.version.length>0;}
function agentDataKind(){return isAcademicIdentity()&&state.auth.identity.auth_mode==='school_client'?'personal_source':'synthetic';}
function agentPolicyKey(status){return JSON.stringify([status?.provider,status?.model,status?.thinking,status?.settings_revision,status?.remote,status?.personal_data_use?.version]);}
function agentCanSend(){return state.auth?.authenticated===true&&state.agentStatus?.configured===true&&(!isAcademicIdentity()||personalChatReady()&&state.agentStatus.available===true&&!state.schoolReauth)&&!state.agentBusy&&!state.agentSettingsBusy&&!state.agentActionBusy&&!state.busy&&!state.agentHistoryLoadBusy&&!state.agentHistoryPending&&(!agentDraftSupported()||state.agentDraftSync.enabled&&!state.agentDraftSync.loading)&&(!state.agentStatus.remote||(isAcademicIdentity()||state.agentConsent))&&state.agentDraft.trim().length>0&&state.agentDraft.trim().length<=2000;}
const recommendationTools=['recommend_my_courses','revise_my_recommendation'];
function agentPlans(result){return [...(result.plans||[]),...(result.pending_plans||[])];}
function agentRunId(result){return result.run_id||agentPlans(result).find(plan=>plan.run_id)?.run_id;}
function sameVersions(left,right){
  const canonical=value=>Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value;
  return JSON.stringify(canonical(left))===JSON.stringify(canonical(right));
}
function latestAgentRunId(){
  for(const turn of [...state.agentTurns].reverse())for(const tool of [...(turn.tool_results||[])].reverse()){
    if(turn.status&&!['completed','ok'].includes(turn.status))continue;
    if(recommendationTools.includes(tool.name)&&!tool.error&&agentRunId(tool.result||{}))return agentRunId(tool.result);
  }
  return null;
}
function findAgentOption(optionId){
  for(const turn of [...state.agentTurns].reverse())for(const tool of [...(turn.tool_results||[])].reverse()){
    if(!recommendationTools.includes(tool.name)||tool.error)continue;
    const plan=agentPlans(tool.result||{}).find(item=>item.option_id===optionId);
    if(plan)return {plan,result:tool.result,turn,runId:plan.run_id||agentRunId(tool.result)};
  }
  return null;
}
function agentOptionState(plan,result){
  const runId=plan.run_id||agentRunId(result);const receipt=state.agentOptionReceipts[plan.option_id];
  if(!plan.option_id||!runId)return {enabled:false,reason:'结果未提供可保存的服务端引用。'};
  if(state.agentHistoryPending||state.agentHistoryLoadBusy)return {enabled:false,reason:'这段对话仍在处理，完成后请重新加载。'};
  const turn=findAgentOption(plan.option_id)?.turn;
  if(turn?.status&&!['completed','ok'].includes(turn.status))return {enabled:false,reason:'本轮未完成，请重新推荐。'};
  if(result.preview_valid===false||plan.preview_valid===false||['stale','invalidated','expired'].includes(result.status))return {enabled:false,reason:plan.stale_reason||result.stale_reason||'本轮预览已失效，请重新推荐。'};
  const latest=latestAgentRunId();
  if(latest&&runId!==latest)return {enabled:false,reason:'这是上一轮推荐，已被后续结果替换；请按当前条件重新推荐。'};
  const baseline=receipt?.versions||result.workspace_versions;
  if(baseline&&state.boot?.versions&&!sameVersions(baseline,state.boot.versions))return {enabled:false,reason:'工作区输入版本已变化，这套预览已失效；请重新推荐。'};
  if((plan.validation?.result||plan.result)==='fail')return {enabled:false,reason:'这套组合包含已知不满足项，不能作为当前推荐保存。'};
  return {enabled:state.auth?.authenticated===true&&Boolean(state.agentConversationId)&&!state.agentBusy&&!state.agentSettingsBusy&&!state.agentActionBusy&&!state.busy,reason:receipt?`已保存为草案 #${receipt.saved_id}`:'尚未保存 · 规划草案',receipt};
}
function agentEvidenceText(item){
  if(typeof item==='string')return item;
  if(!item||typeof item!=='object')return showValue(item);
  const text=item.text||item.reason||item.summary||item.excerpt||item.title||item.course_name;
  const ref=item.evidence_id||item.reference_id||item.source_id||item.record_id||item.snapshot_id||item.chunk_id||item.file_name;
  return text?`${text}${ref?' · '+ref:''}`:ref?`合成来源引用 ${ref}`:showValue(item);
}
function agentDifferenceText(differences){
  if(!differences||typeof differences!=='object')return agentEvidenceText(differences);
  const labels={added_course_ids:'增加课程',removed_course_ids:'移除课程',added_section_ids:'换入班次',removed_section_ids:'换出班次'};
  const known=Object.keys(labels).filter(key=>Object.hasOwn(differences,key));
  if(!known.length)return agentEvidenceText(differences);
  const names=courseMap();const sectionNames=new Map(state.agentTurns.flatMap(turn=>(turn.tool_results||[]).flatMap(tool=>agentPlans(tool.result||{}).flatMap(plan=>plan.sections||[]))).map(section=>[section.section_id,section.course_name||section.section_id]));
  return known.filter(key=>differences[key]?.length).map(key=>`${labels[key]} ${differences[key].map(id=>names.get(id)?.course_name||sectionNames.get(id)||id).join('、')}`).join('；');
}
function agentExportMenu(action,identifier,enabled=true){
  const attribute=action==='export-agent-option'?'option-id':'saved-id';
  return `<details class="export-menu"><summary>导出</summary><div class="export-options">${['json','csv','ics'].map(format=>`<button class="button small" data-action="${action}" data-${attribute}="${esc(identifier)}" data-format="${format}" ${enabled?'':'disabled'}>${format.toUpperCase()}</button>`).join('')}</div></details>`;
}
function agentCheckList(checks){
  const items=uniqueChecks(checks).filter(check=>['fail','unknown'].includes(check.result));
  return items.length?`<ul class="results-list">${items.map(check=>`<li>${statusBadge(check.result)}<span>${esc(check.reason||check.message||check.title||'信息待确认')}</span></li>`).join('')}</ul>`:'';
}
function agentRecommendationPlan(plan,result,index){
  const constraints=plan.constraints||result.constraints||{};
  const validation=plan.validation||{};const option=agentOptionState(plan,result);
  const friday=plan.metrics?.friday_meeting_count??validation.preference_scores?.friday_meeting_count;
  const differences=plan.differences||plan.difference_from_baseline||plan.difference;
  const difference= differences?agentDifferenceText(differences):'';
  return `<article class="agent-plan agent-recommendation-plan"><div class="agent-card-heading"><h3>${esc(planName(plan,index))}</h3>${statusBadge(validation.result||plan.result||'unknown')}</div><p class="agent-plan-summary">${esc(showValue(planCredits(plan)))} 学分 · ${esc(Number.isFinite(friday)?friday===0?'周五空闲':`周五有 ${friday} 个上课时段`:'周五安排待确认')}</p>${difference?`<p class="agent-tradeoff">${esc(difference)}</p>`:''}${agentPlanTable(plan,constraints)}${agentCheckList(validation.checks)}${!option.enabled||option.receipt?`<p class="agent-option-state" role="status">${esc(option.reason)}</p>`:''}<div class="button-group agent-option-actions"><button class="button small primary" data-action="save-agent-option" data-option-id="${esc(plan.option_id)}" ${option.enabled&&!option.receipt?'':'disabled'}>${option.receipt?'已保存':'保存这套'}</button>${agentExportMenu('export-agent-option',plan.option_id,option.enabled)}</div></article>`;
}
function agentRecommendationScope(result){
  const search=result.search||{};const limits=[];
  if(search.complete_within_scope===false)limits.push('资料覆盖不足。');
  if(search.limit_reached===true)limits.push('搜索尚未穷尽。');
  if(search.candidate_scope_clipped===true||search.candidate_retention_clipped===true)limits.push('仅展示本轮搜索范围内的结果。');
  return limits.length?notice(limits.join(' ')):'';
}
function agentRecommendationCard(result){
  const plans=agentPlans(result);
  return `${agentRecommendationScope(result)}${plans.length?plans.map((plan,index)=>agentRecommendationPlan(plan,result,index)).join(''):`${notice(result.reason||'当前条件下没有可确认通过的课表。')}${agentPlanDiagnostics(result)}`}`;
}
function agentOptionComparison(result){
  const comparisons=result.comparisons||result.differences||result.summaries||result.plans||[];
  return `${result.different_input_intents?notice('方案目标不同，不能直接排名。'):''}${result.preview_valid===false?notice(result.stale_reason||'推荐已失效，请重新推荐。'):''}${Array.isArray(comparisons)?comparisons.map((item,index)=>{const plan=(result.plans||[]).find(option=>option.option_id===item.option_id)||item;return `<article class="agent-comparison-item"><h3>${esc(item.title||planName(plan,index))}</h3><p>${esc(agentDifferenceText(item))}</p>${plan.sections?agentPlanTable(plan,plan.constraints||{}):''}${agentCheckList(plan.validation?.checks)}</article>`;}).join(''):notice(agentEvidenceText(comparisons))}`;
}
function agentExplicitActionSummary(result,turn={}){
  if(turn.status==='completed'&&turn.reply_origin==='verified_explicit_action'){
    if(turn.saved_action?.option_id===result.option_id)return notice(`已保存为课表 #${turn.saved_action.saved_id}`,'success');
    if(turn.download&&turn.pending_action?.option_id===result.option_id)return notice(`已导出 ${turn.download.filename}`,'success');
  }
  return notice(result.status==='action_ready'?'操作已准备，执行尚未完成。':result.reason||result.message||'操作未完成。');
}
function agentToolCard(tool,turn={}){
  const result=tool.result||{};
  if(isAcademicIdentity()){
    if(tool.error)return notice('本次资料检索未完成，详情见本轮说明。');
    if(tool.name==='query_my_records'){
      if(result.status==='not_ready'||result.query_executed===false||result.reason==='no_observation_in_scope')return notice(['academic','major_academic'].includes(result.operation)?'尚未取得成绩来源，当前无法查询或评价成绩。':'尚未取得本次查询所需的本人来源，不能把未知当作零条记录。','neutral');
      const records=Array.isArray(result.records)?result.records:[],emptyKnown=result.matched_records===0||result.coverage?.captured_rows===0&&result.coverage?.complete_within_scope===true;
      const count=records.length?`展示 ${records.length} 条`:emptyKnown?'当前已读范围 0 条':'来源记录条数待确认';
      return `<details class="agent-personal-records"><summary>本人来源记录 · ${count}</summary><p class="field-help">${esc(personalSourceKind(result))} · ${esc(personalTermLabel(result.target_term))} · ${esc(schoolCoverageText(result))}</p><p class="field-help">仅限本人已读范围，未读取的内容不在本次结果中；同码多条记录分别保留，来源学分不等于已获学分。</p>${result.source_grade_summary?personalGradeSummary(result.source_grade_summary,result):''}${records.length||emptyKnown?personalRecordTable(result):'<p class="field-help">本次未取得可展示记录，不能据此判断来源为零条。</p>'}</details>`;
    }
    if(tool.name==='search_my_curriculum')return Array.isArray(result.matches)&&result.matches.length?`<details class="agent-personal-evidence"><summary>培养资料依据 · ${result.matches.length} 个片段</summary>${result.matches.map(personalChatEvidence).join('')}</details>`:'';
    if(tool.name==='resolve_my_evidence')return `<details class="agent-personal-evidence"><summary>回查的培养资料依据</summary>${personalChatEvidence(result.evidence||result.match||result)}</details>`;
    if(tool.name==='get_my_run_diagnostics')return result.status==='not_ready'?'<p class="field-help">当前对话还没有可核对的上一轮运行记录。</p>':`<details class="agent-personal-evidence"><summary>上一轮实际运行记录</summary>${agentDiagnosticMarkup(result.diagnostic)}</details>`;
    if(recommendationTools.includes(tool.name)||['generate_my_plan','validate_my_plan'].includes(tool.name))return notice('真实课表推荐待输入与规则核验，当前未生成真实推荐。');
    return '';
  }
  if(tool.error)return notice(result.message||result.error||'请求未完成，请重试。','danger');
  if(tool.name==='act_on_my_option')return agentExplicitActionSummary(result,turn);
  if(recommendationTools.includes(tool.name))return agentRecommendationCard(result);
  if(tool.name==='compare_my_options')return agentOptionComparison(result);
  if(['generate_my_plan','validate_my_plan'].includes(tool.name)){
    const plans=agentPlans(result).concat(result.plan?[result.plan]:[]);
    return plans.length?plans.map((plan,index)=>`<article class="agent-plan"><div class="agent-card-heading"><h3>${esc(planName(plan,index))}</h3>${statusBadge(plan.validation?.result||result.result||'unknown')}</div>${agentPlanTable(plan,result.constraints)}${agentCheckList(plan.validation?.checks||result.checks)}</article>`).join(''):`${notice(result.reason||'当前没有可展示的课表。')}${agentPlanDiagnostics(result)}`;
  }
  return agentQuerySummary(tool.name,result);
}
function agentPlanDiagnostics(result){
  const plans=agentPlans(result).concat(result.plan?[result.plan]:[]);
  return agentCheckList([...(result.diagnostics||[]),...(result.checks||[]),...plans.flatMap(plan=>plan.validation?.checks||[])]);
}
function agentQuerySummary(name,result){
  const unknowns=(result.unknown||[]).map(item=>`<li>${statusBadge('unknown')}<span>${esc(agentEvidenceText(item))}</span></li>`).join('');
  const unknown=unknowns?`<ul class="results-list">${unknowns}</ul>`:'';
  if(name==='get_my_context')return `<ul class="results-list">${(result.courses||[]).map(course=>`<li>${esc(course.course_name||course.course_id)} · ${esc(showValue(course.credits))} 学分</li>`).join('')}</ul>${unknown}`;
  if(name==='audit_my_progress')return `<ul class="results-list">${(result.rules||[]).map(rule=>`<li>${statusBadge(rule.result||'unknown')}<span>${esc(rule.title||rule.rule_id)}：${esc(rule.reason||'信息待确认')}</span></li>`).join('')}</ul>${unknown}`;
  if(name==='find_my_sections')return `${agentPlanTable({sections:result.sections})}${result.missing_course_ids?.length?notice('未找到教学班：'+result.missing_course_ids.join('、')):''}${unknown}`;
  if(name==='search_plan_references')return (result.warnings||[]).map(warning=>notice(agentEvidenceText(warning))).join('')+unknown;
  return unknown;
}
function agentPlanTable(plan,constraints={}){
  const sections=plan.sections||[];
  const examText=section=>section.exam_state==='none'?'无考试':section.exam_state==='scheduled'&&section.exams?.length?section.exams.map(exam=>`${exam.exam_date||'日期待确认'} ${exam.start_time_local||'时间待确认'}${exam.end_time_local?'–'+exam.end_time_local:''}`).join('、'):section.exam_state==='scheduled'?'已安排，时间待确认':'待确认';
  return `<div class="table-wrap"><table class="data-table agent-plan-table"><thead><tr><th>课程</th><th>上课时间</th><th>考试</th></tr></thead><tbody>${sections.map(section=>`<tr><td>${esc(section.course_name||section.course_id||'课程待确认')}</td><td>${section.meetings?.length?section.meetings.map(meeting=>esc(meetingText(meeting))).join('<br>'):'时段待确认'}</td><td>${esc(examText(section))}</td></tr>`).join('')}${(constraints.fixed_meetings||[]).map(meeting=>`<tr><td>${esc(meeting.title||'固定安排')}</td><td>${esc(meetingText(meeting))}</td><td>—</td></tr>`).join('')}</tbody></table></div>${!sections.length?notice('课程时段待确认。'):''}`;
}
function agentCitation(citation,options={}){
  const item=citation.chunk||citation;
  if(isAcademicIdentity()){
    const number=personalCitationNumber(item);
    if(item.kind==='summary')return `<li class="agent-citation"><strong>${number}成绩概况</strong>${personalGradeSummary(item.source_grade_summary,item)}</li>`;
    if(item.kind==='record')return `<li class="agent-citation"><strong>${number}本人来源记录</strong>${item.record&&typeof item.record==='object'?personalRecordTable({operation:item.operation,records:[item.record]}):'<p class="field-help">本条记录尚无可展示字段，请重新提问以核对来源。</p>'}</li>`;
    if(item.kind==='context')return `<li class="agent-citation"><strong>${number}本轮已知范围</strong>${personalCitationContext(item.context)}</li>`;
    if(item.kind==='diagnostic')return `<li class="agent-citation"><strong>${number}上一轮实际运行记录</strong>${agentDiagnosticMarkup(item.diagnostic)}</li>`;
    if(item.kind==='recommendation_readiness')return `<li class="agent-citation"><strong>${number}推荐准备检查</strong>${personalRecommendationReadiness(item.readiness,true)}</li>`;
    if(item.kind==='course_data_plan')return `<li class="agent-citation"><strong>${number}课程数据测试依据</strong>${personalCoursePlanEvidence(item.course_data_plan)}</li>`;
    if(item.kind==='academic_term')return `<li class="agent-citation"><strong>${number}${esc(item.title||'教务字段说明')}</strong><p>${esc(item.text||'')}</p><p class="field-help">本系统字段解释，不是个人成绩或学校政策认定。</p></li>`;
    if(item.kind==='course_reference')return `<li class="agent-citation"><strong>${number}${esc(item.course?.course_name||item.title||'本地课程参考')}</strong><p>${esc(item.course?.course_code||'')}</p><p class="field-help">${esc(item.text||'本地课程名称与代码参考；当前开课与本人可选资格待核实。')}</p></li>`;
    if(item.kind==='curriculum'||!item.kind&&Number.isSafeInteger(item.page))return `<li class="agent-citation">${personalChatEvidence(item,options)}</li>`;
    return '<li class="agent-citation"><p class="field-help">来源类型待确认，暂不展示原文。</p></li>';
  }
  const sourceFiles={S1:'course_slides.pdf',S2:'journalism_2026.pdf',S3:'materials_2026.pdf',S4:'business_analytics_2026.pdf'};
  const refs=(item.pdf_evidence||item.source_refs||[]).map(ref=>({...ref,file_name:sourceFiles[ref.source_id]||ref.file_name||ref.source_file||'',pdf_pages:ref.pdf_pages||ref.pages||[]}));
  return `<li class="agent-citation"><strong>${esc(item.title||'参考依据')}</strong><span class="badge warning">待审核</span><p>${esc(item.text||item.excerpt||'')}</p>${sourceLinks(refs)}</li>`;
}
function agentVisibleTools(turn){
  const tools=turn.tool_results||[];
  if(isAcademicIdentity())return tools;
  if(!['completed','ok'].includes(turn.status))return tools;
  const index=turn.model_decision?.source_tool_result_index;
  const selected=Number.isInteger(index)&&tools[index]?tools[index]:[...tools].reverse().find(tool=>['act_on_my_option',...recommendationTools,'compare_my_options','generate_my_plan','validate_my_plan'].includes(tool.name))||tools.at(-1);
  // A secondary failed validation must remain visible even when the main answer recommends a plan.
  return tools.filter(tool=>tool===selected||tool.error||tool.name==='validate_my_plan'&&tool.result?.result!=='pass'||tool.result?.unknown?.length);
}
function agentDisplayReply(turn){
  if(!['completed','ok'].includes(turn.status))return turn.code==='model_http_402'?'模型服务尚未完成本轮回复。':turn.reply||'';
  if(turn.reply_origin==='verified_explicit_action'&&(turn.saved_action||turn.download))return '';
  if(turn.reply_origin==='verified_tool_presentation'){
    const index=turn.model_decision?.source_tool_result_index;
    const tool=Number.isInteger(index)?turn.tool_results?.[index]:agentVisibleTools(turn)[0];
    if(recommendationTools.includes(tool?.name)&&tool.result?.status==='feasible')return `已推荐 ${tool.result.plans?.length||0} 套课表。`;
  }
  // The model can echo an already-numbered citation before the server appends it.
  // Collapse only adjacent duplicates bound to this answer's actual citations.
  const citationNumbers=new Set((turn.citations||[]).map(item=>item.chunk||item).map(item=>item.citation_number).filter(Number.isSafeInteger));
  return (turn.reply||'').replace(/\[(\d+)\](?:[ \t]*\[\1\])+/g,(match,number)=>citationNumbers.has(Number(number))?`[${number}]`:match);
}
function personalAnswerText(turn,text){
  const numbers=new Set((turn.citations||[]).map(item=>(item.chunk||item).citation_number).filter(Number.isSafeInteger));
  return String(text||'').replace(/[ \t]*\[(\d+)\]/g,(match,number)=>numbers.has(Number(number))?'':match).trim();
}
function personalGradeAnswer(turn){
  if(!['completed','ok'].includes(turn.status)||turn.error||turn.task_status==='blocked')return null;
  const citations=(turn.citations||[]).map(item=>item.chunk||item);
  const records=citations.flatMap(item=>item.kind==='summary'&&Array.isArray(item.record_table)?item.record_table.map(row=>({...item,kind:'record',record:row.record,source_record_index:row.source_record_index})):item).filter(item=>item.kind==='record'&&['academic','major_academic'].includes(item.operation)&&item.record);
  if(!records.length)return null;
  const groups=new Map();
  for(const item of records){
    const scope=JSON.stringify([item.operation,item.target_term,item.observation_id,item.historical_only,item.data_source]);
    if(!groups.has(scope))groups.set(scope,{...item,records:[],citation_numbers:[],seen:new Set()});
    const group=groups.get(scope);
    // Deduplicate only the same immutable source row returned by overlapping
    // queries. Equal course codes or even equal grades are not row identity.
    const rowKey=typeof item.observation_id==='string'&&Number.isSafeInteger(item.source_record_index)&&item.source_record_index>=0?JSON.stringify([item.source_record_index,item.record]):null;
    if(rowKey!==null&&group.seen.has(rowKey))continue;
    if(rowKey!==null)group.seen.add(rowKey);
    group.records.push(item.record);group.citation_numbers.push(item.citation_number);
  }
  // The answer contract delegates cited grade rows to this source table.
  // A wording heuristic must not hide them for paraphrases or mixed questions.
  const listing=turn.model_decision?.answer_protocol==='text-v1'||(/(明细|列出|列一下|课程成绩|每门.*成绩|成绩.*表格)/.test(turn.message||'')&&!/(为什么|解释|建议|比较|怎么算)/.test(turn.message||'')&&citations.every(item=>['context','course_reference'].includes(item.kind)||['record','summary'].includes(item.kind)&&['academic','major_academic'].includes(item.operation)));
  if(!listing)return null;
  const html=[...groups.values()].map(group=>`<section class="agent-grade-answer"><h4>${esc(personalTermLabel(group.target_term))} · 成绩明细</h4><p class="field-help">${esc(personalSourceKind(group))} · ${esc(schoolCoverageText(group))} · 本答复展示 ${group.records.length} 条</p>${personalRecordTable({...group,citation_numbers:[],compact:true})}<p class="field-help">表内绩点与学分均为来源字段，未作 GPA 或已获学分认定。</p></section>`).join('');
  return {html,listing,records:records.map(item=>item.record)};
}
function personalGradeProse(text,grades){
  if(!grades?.listing)return text;
  const plain=value=>value.replace(/[*_`]/g,'').trim();
  const names=new Set(grades.records.map(row=>plain(String(row.course_name_raw||''))).filter(Boolean));
  const cells=line=>line.trim().replace(/^\||\|$/g,'').split('|').map(plain);
  // Only replace an echoed grade table whose courses are present in the
  // authoritative table below. Keep surrounding explanations and other tables.
  return String(text).replace(/^[ \t]*\|[^\n]*\|[ \t]*\n[ \t]*\|(?:[ \t]*:?-{3,}:?[ \t]*\|)+[ \t]*\n(?:[ \t]*\|[^\n]*\|[ \t]*(?:\n|$))+/gm,table=>{
    const rows=table.trim().split('\n').map(cells),header=rows[0];
    const courseIndex=header.findIndex(value=>['课程','课程名称','课程名'].includes(value));
    if(courseIndex<0||!header.some(value=>/成绩|绩点|学分/.test(value))||
      !rows.slice(2).every(row=>row.length===header.length&&names.has(row[courseIndex])))return table;
    return '具体成绩、绩点和学分见下方来源表。\n';
  }).trim();
}
function curriculumCitationLinks(turn,index){
  if(!isAcademicIdentity()||!['completed','ok'].includes(turn.status)||turn.error)return '';
  const seen=new Set(),links=[];
  (turn.citations||[]).forEach((citation,citationIndex)=>{
    const item=citation.chunk||citation;
    if(item.kind!=='curriculum'||!/^PERSONAL-REF-[A-Za-z0-9_-]{43}$/.test(item.reference||'')||!Number.isSafeInteger(item.page)||item.page<1)return;
    const key=JSON.stringify([item.source_alias,item.page]);if(seen.has(key))return;seen.add(key);
    links.push(`<button type="button" class="citation-page-link" data-action="open-citation-pdf" data-turn-index="${index}" data-citation-index="${citationIndex}">培养方案 · 第 ${item.page} 页 ↗</button>`);
  });
  return links.length?`<nav class="citation-page-links" aria-label="回答引用的原文">${links.join('')}</nav>`:'';
}
let pdfLibraryPromise=null;
function loadPdfLibrary(){
  if(globalThis.PDFLib)return Promise.resolve(globalThis.PDFLib);
  if(!pdfLibraryPromise){
    const script=document.createElement('script');
    script.src='/pdf-lib.min.js';script.async=true;
    pdfLibraryPromise=new Promise((resolve,reject)=>{
      script.onload=()=>{
        script.remove();
        if(globalThis.PDFLib)resolve(globalThis.PDFLib);
        else reject(new Error('PDF library unavailable'));
      };
      script.onerror=()=>reject(new Error('PDF library unavailable'));
      document.head.append(script);
    }).catch(error=>{script.remove();pdfLibraryPromise=null;throw error;});
  }
  return pdfLibraryPromise;
}
async function highlightCitationPdf(blob,evidence){
  // Derived viewing copy only. Never change the cached source PDF or its hash.
  const boxes=evidence.source_boxes;
  if(!Array.isArray(boxes)||!boxes.length)return {blob,highlighted:false};
  try{
    const library=await loadPdfLibrary();
    const doc=await library.PDFDocument.load(new Uint8Array(await blob.arrayBuffer()),{updateMetadata:false});
    const number=evidence.locator?.physical_page||evidence.page;
    if(!Number.isSafeInteger(number)||number<1||number>doc.getPageCount())return {blob,highlighted:false};
    const page=doc.getPage(number-1),media=page.getMediaBox(),crop=page.getCropBox();
    // Extractor coordinates are top-left PDF points. Reject geometries that
    // would need an unverified rotation/crop transform instead of mis-highlighting.
    if(page.getRotation().angle!==0||media.x!==0||media.y!==0||Object.keys(media).some(k=>Math.abs(media[k]-crop[k])>.01))return {blob,highlighted:false};
    const valid=boxes.length<=4000&&boxes.every(w=>Array.isArray(w.bbox)&&w.bbox.length===4&&w.bbox.every(Number.isFinite)&&w.bbox[0]>=0&&w.bbox[1]>=0&&w.bbox[2]>w.bbox[0]&&w.bbox[3]>w.bbox[1]&&w.bbox[2]<=media.width&&w.bbox[3]<=media.height);
    if(!valid)return {blob,highlighted:false};
    for(const word of boxes){const [x,top,right,bottom]=word.bbox;page.drawRectangle({x,y:media.height-bottom,width:right-x,height:bottom-top,color:library.rgb(1,.83,0),opacity:.32,blendMode:library.BlendMode.Multiply});}
    // Chrome's view=FitH offset uses unzoomed CSS pixels from page top,
    // while source boxes use PDF points (72/in). Leave 36pt of context above.
    const scrollTop=Math.round(Math.max(0,Math.min(...boxes.map(word=>word.bbox[1]))-36)*96/72);
    return {blob:new Blob([await doc.save()],{type:'application/pdf'}),highlighted:true,scrollTop};
  }catch{return {blob,highlighted:false};}
}
const citationPreviewUrls=new Set();
if(typeof window!=='undefined')window.addEventListener?.('pagehide',()=>{for(const url of citationPreviewUrls)URL.revokeObjectURL(url);citationPreviewUrls.clear();});
let activeCitationPreview=null;
function closeCitationPreview(){activeCitationPreview?.();activeCitationPreview=null;}
async function openCitationPdf(button){
  if(!isAcademicIdentity())return;
  const raw=state.agentTurns[Number(button.dataset.turnIndex)]?.citations?.[Number(button.dataset.citationIndex)],item=raw?.chunk||raw;
  if(item?.kind!=='curriculum'||!/^PERSONAL-REF-[A-Za-z0-9_-]{43}$/.test(item.reference||''))return;
  closeCitationPreview();
  const viewer=window.open('about:blank','_blank');
  if(!viewer){message('请允许本站打开新标签页后重试。',true);return;}
  viewer.opener=null;viewer.document.title='培养方案原文';viewer.document.body.textContent='正在核对引用并读取 PDF…';
  const capture=currentSchoolRequest(),dialog=document.createElement('dialog');let url=null,closed=false;
  dialog.className='citation-pdf-dialog';dialog.setAttribute('aria-label','培养方案原文');
  dialog.innerHTML='<header><strong>培养方案原文</strong><button type="button" class="button small">关闭</button></header><p class="citation-pdf-status" role="status">正在核对引用…</p>';
  const finish=()=>{if(closed)return;closed=true;dialog.remove();if(url)citationPreviewUrls.add(url);else viewer.close();button.focus?.();};
  activeCitationPreview=finish;dialog.querySelector('button').onclick=()=>{finish();if(activeCitationPreview===finish)activeCitationPreview=null;};
  dialog.addEventListener('cancel',event=>{event.preventDefault();finish();});
  document.body.append(dialog);dialog.showModal();
  const current=()=>!closed&&sameSchoolRequest(capture)&&capture.agentEpoch===state.agentEpoch;
  try{
    const receipt=await api('/api/evidence/preview',{reference:item.reference,source_alias:item.source_alias},capture.agentEpoch);
    if(!current())return finish();
    const evidence=receipt.evidence,page=evidence?.locator?.physical_page||evidence?.page;
    if(!Number.isSafeInteger(page)||page<1||evidence.source_alias!==item.source_alias)throw Error('missing_location');
    const bytes=Uint8Array.from(atob(receipt.pdf_base64),char=>char.charCodeAt(0));
    const blob=new Blob([bytes],{type:'application/pdf'});
    const preview=await highlightCitationPdf(blob,evidence);
    if(!current()||viewer.closed)return finish();
    message((receipt.historical?'已打开该回答当时的历史 PDF。':'')+(preview.highlighted?'已打开原文，引用句段已标黄。':'已打开原文；该片段暂无可靠坐标，仅定位到引用页。'));
    url=URL.createObjectURL(preview.blob);viewer.location.replace(`${url}#page=${page}&view=FitH${preview.highlighted&&Number.isSafeInteger(preview.scrollTop)?','+preview.scrollTop:''}`);finish();
  }catch(error){viewer.close();if(current())dialog.querySelector('.citation-pdf-status').textContent='暂时无法打开该引用。它可能已过期或文档已更新，请重新提问后再试。';else finish();}
}
function agentAnswerMarkup(text){
  const inline=value=>esc(value).replace(/\*\*([^*\n]+)\*\*/g,'<strong>$1</strong>');
  const lines=String(text).replace(/\r\n?/g,'\n').split('\n'),blocks=[];
  const cells=line=>{
    const value=line.trim();if(!value.startsWith('|')||!value.endsWith('|'))return null;
    return value.slice(1,-1).split(/(?<!\\)\|/).map(cell=>cell.trim().replace(/\\\|/g,'|'));
  };
  for(let index=0;index<lines.length;){
    const header=cells(lines[index]),separator=index+1<lines.length?cells(lines[index+1]):null;
    if(header&&header.length>=2&&header.length<=12&&separator?.length===header.length&&separator.every(cell=>/^:?-{3,}:?$/.test(cell))){
      const rows=[];let end=index+2;
      while(end<lines.length){const row=cells(lines[end]);if(!row||row.length!==header.length)break;rows.push(row);end++;}
      if(rows.length){
        blocks.push(`<div class="agent-answer-table" role="region" aria-label="答复表格，可横向滚动" tabindex="0"><table><thead><tr>${header.map(cell=>`<th scope="col">${inline(cell)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(cell=>`<td>${inline(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`);
        index=end;continue;
      }
    }
    const heading=lines[index].match(/^#{1,6}\s+(.+)$/);
    const list=lines[index].match(/^\s*(?:([-*])|\d+[.)])\s+(.+)$/);
    if(heading){blocks.push(`<h3>${inline(heading[1])}</h3>`);index++;continue;}
    if(list){
      const ordered=!list[1],items=[];let end=index;
      while(end<lines.length){const item=lines[end].match(/^\s*(?:([-*])|\d+[.)])\s+(.+)$/);if(!item||(!item[1])!==ordered)break;items.push(`<li>${inline(item[2])}</li>`);end++;}
      blocks.push(`<${ordered?'ol':'ul'}>${items.join('')}</${ordered?'ol':'ul'}>`);index=end;continue;
    }
    if(lines[index].trim())blocks.push(`<p>${inline(lines[index])}</p>`);
    index++;
  }
  return blocks.join('');
}
function renderAgentTurns(){
  if(!state.agentTurns.length&&state.agentHistoryPending)return '';
  if(!state.agentTurns.length)return isAcademicIdentity()?'<div class="agent-welcome"><h1>有什么想了解的？</h1><p>查询成绩、课表、考试和培养要求，或提出选课与排课需求。</p><div class="agent-starters" aria-label="示例问题"><button type="button" class="button" data-action="agent-starter" data-example="0">看看我的成绩概况</button><button type="button" class="button" data-action="agent-starter" data-example="1">读懂培养要求</button><button type="button" class="button" data-action="agent-starter" data-example="2">规划课程组合</button></div></div>':'<div class="agent-welcome"><span class="welcome-symbol" aria-hidden="true">课</span><h1>想怎么安排这学期的课？</h1><p>从修读目标和时间偏好开始，逐步比较、调整你的课表。</p><p class="agent-empty">当前为合成演示，推荐结果用于验证。</p></div>';
  return state.agentTurns.map((turn,index)=>{
    const completed=['completed','ok'].includes(turn.status);
    const label=completed&&turn.reply_origin==='personal_model_clarification'?'待补充信息':turn.reply_origin==='verified_clarification_prompt'?'待澄清':turn.task_status==='partial'?'已有部分结果':turn.task_status==='blocked'?'任务受阻':completed?'浙大教务助手':'本轮未完成';
    const originalReply=turn.error||(isAcademicIdentity()?personalAnswerText(turn,agentDisplayReply(turn)):agentDisplayReply(turn));
    const grades=isAcademicIdentity()?personalGradeAnswer(turn):null;
    const verifiedPlan=completed&&!turn.error&&isAcademicIdentity()?personalCoursePlanValue(turn.recommendation_result):null;
    const planBrief=verifiedPlan&&!verifiedPlan.reason&&['generate','revise','compare'].includes(verifiedPlan.action)&&verifiedPlan.options?.length>0?`已${verifiedPlan.action==='revise'?'调整并重新计算':verifiedPlan.action==='compare'?'比较':'生成'} ${Math.min(3,verifiedPlan.options.length)} 套条件参考方案，课程与保留条件见下方。`:'';
    const directAnswer=turn.model_decision?.answer_protocol==='text-v1';
    const planAnalysis=!directAnswer&&planBrief&&typeof turn.model_decision?.evidence_analysis==='string'?personalAnswerText(turn,turn.model_decision.evidence_analysis):'';
    const reply=directAnswer?personalGradeProse(originalReply,grades):grades?.listing?'成绩明细见下表，按本轮引用的来源记录展示。':planBrief?'':originalReply;
    const key=turn.request_id||turn.run_id||`turn-${turn.turn_id??index}`;
    const failedEvents=[...(agentTurnProgress(turn)?.events||[])].map(agentEventData).reverse();
    const failure=!completed&&isAcademicIdentity()?agentFailureText(agentTerminalFailure(turn,failedEvents)):'';
    const evidence=isAcademicIdentity()?'':`${agentVisibleTools(turn).map(tool=>agentToolCard(tool,turn)).join('')}${turn.citations?.length?`<details class="agent-citations"><summary>参考依据</summary><ul>${turn.citations.map(agentCitation).join('')}</ul></details>`:''}`;
    const plans=isAcademicIdentity()?renderPersonalCoursePlans(turn.recommendation_result,turn.is_stale===true):'';
    const analysis=planAnalysis?`<section class="agent-plan-analysis"><h3>选择依据</h3>${planAnalysis.split(/\n\s*\n/).filter(text=>text.trim()).map(text=>`<p>${esc(text)}</p>`).join('')}</section>`:'';
    const body=directAnswer?agentAnswerMarkup(reply):(reply?`<p>${esc(reply)}</p>`:'');
    const answer=turn.pending?'':`<div class="agent-answer" data-answer-key="${esc(key)}" data-scroll-anchor="${esc(key)}:answer"><strong>${turn.error?'请求未完成':planBrief?'浙大教务助手':label}</strong>${body}${curriculumCitationLinks(turn,index)}${grades?.html||''}${failure?`<p class="agent-run-error">${esc(failure)}</p>`:''}${!isAcademicIdentity()&&turn.partial_tools_completed?'<p>本轮已完成的工具结果如下。</p>':''}${plans}${analysis}${evidence}</div>`;
    const progress=turn.pending?agentTurnProgress(turn):null;
    const pending=turn.pending?`<p class="agent-pending" role="status">${esc(progress?agentProcessStage(progress):'正在回复…')}</p>`:'';
    return `<article class="agent-turn" data-turn-key="${esc(key)}" data-turn-index="${index}"><div class="agent-user-row"><div class="agent-message user" data-scroll-anchor="${esc(key)}:question"><p>${esc(turn.message)}</p></div><div class="agent-message-actions user-actions">${agentMessageButton('copy-question','复制问题','copy',index)}<span class="copy-status" role="status"></span></div></div><div class="agent-message ${completed&&!turn.error||turn.pending?'assistant':'system'}">${pending}${answer}${!turn.pending?`<div class="agent-answer-actions agent-message-actions">${completed&&!turn.error?agentMessageButton('copy-answer','复制回答','copy',index)+(index===state.agentTurns.length-1?agentMessageButton('regenerate-answer','重新回答','retry',index,true):'')+agentMessageButton('share-answer','分享问答','share',index):(index===state.agentTurns.length-1?agentMessageButton('regenerate-answer','重新回答','retry',index,true):'')}<span class="copy-status" role="status" aria-live="polite"></span></div>`:''}</div></article>`;
  }).join('');
}
function personalText(value,missing='待确认'){return value!=='unknown'&&['string','number','boolean'].includes(typeof value)&&String(value).trim()?String(value):missing;}
function personalTermLabel(value){return value==='ALL-HISTORY'?'全部已读历史':personalText(value,'范围待确认').replace(/-(AW|SS|SU|A|W|S)$/,(_,season)=>' '+({AW:'秋冬',SS:'春夏',A:'秋',W:'冬',S:'春',SU:'夏'}[season]));}
function personalSourceKind(value){const name=value.operation==='major_academic'?'主修成绩 · ':value.operation==='academic'?'学期成绩 · ':'';return name+(value.historical_only===true?'历史恢复记录':value.data_source==='historical_cache'?'历史缓存':value.data_source==='live'?'学校读取快照':value.data_source==='synthetic_fixture'?'模拟数据':'读取来源待确认');}
function personalReviewLabel(value){return ({unreviewed:'原文待审核',assistant_reviewed_pending_human:'助手整理，待人工审核',reviewed_for_scope:'指定范围已审核',not_reviewed:'原文待审核'})[value]||'审核状态待确认';}
function personalCitationNumber(item){return Number.isSafeInteger(item?.citation_number)&&item.citation_number>0?`[${item.citation_number}] `:'';}
function personalRecommendationReadiness(value,checked=false){
  const readiness=value&&typeof value==='object'?value:{};
  if(!checked||readiness.status!=='not_ready'||readiness.can_generate!==false||readiness.can_save_or_export!==false)return '<p class="field-help">推荐准备检查状态待确认，当前不能据此确认正式推荐就绪。</p>';
  const sourceNames={course_reference:'课程名称与代码参考',academic_records:'成绩记录',curriculum:'培养资料',existing_timetable:'已有课表',exams:'已有考试'};
  const sourceStates={available:'可用',missing:'尚未具备',partial:'部分取得',failed:'读取未完成',needs_clarification:'需补充查询范围'};
  const dependencyNames={identity_and_study_facts:'课程与修读身份',curriculum_applicability_and_rules:'培养适用与规则',current_candidate_pool:'当期候选课程',section_time_and_calendar:'班次、时段与校历',protected_arrangements:'需保留的安排'};
  const missingNames={stable_course_and_attempt_identity:'稳定课程与修读实例身份',completion_recognition_and_repeat_policy:'完成状态、认定与重复修读规则',personal_plan_applicability:'本人培养方案适用',reviewed_versioned_rules_and_course_mapping:'已审核规则与课程映射',bounded_current_offerings:'限定范围的当期开课',personal_eligibility_and_fresh_capacity:'本人资格与最新余量',stable_section_identity:'稳定教学班身份',full_meetings_and_week_sets:'完整时段与周次',exam_intervals:'考试时段',calendar_period_mapping:'校历与节次日期映射',verified_formal_enrollment_mapping:'正式中选对应关系',explicit_locks_and_fixed_arrangements:'明确的锁定与固定安排'};
  const lookup=(mapping,key,fallback)=>typeof key==='string'&&Object.hasOwn(mapping,key)?mapping[key]:fallback;
  const term=typeof readiness.target_term==='string'?readiness.target_term.match(/^((?:19|20|21)\d{2})-((?:19|20|21)\d{2})-(AW|SS)$/):null;
  const scope=lookup({courses:'课程推荐',timetable:'课表推荐'},readiness.recommendation_kind,'推荐')+' · '+(term&&Number(term[2])===Number(term[1])+1?`${term[1]}-${term[2]}学年${term[3]==='AW'?'秋冬':'春夏'}`:'目标学期待明确');
  const sources=(Array.isArray(readiness.sources)?readiness.sources:[]).slice(0,8).map(item=>{const row=item&&typeof item==='object'?item:{};return `${lookup(sourceNames,row.id,'其他来源')}：${lookup(sourceStates,row.status,'待确认')}`;});
  const dependencies=(Array.isArray(readiness.dependencies)?readiness.dependencies:[]).slice(0,8).map(item=>{
    const row=item&&typeof item==='object'?item:{},name=lookup(dependencyNames,row.id,'其他依赖');
    const missing=[...new Set((Array.isArray(row.missing)?row.missing:[]).slice(0,8).map(key=>lookup(missingNames,key,'其他项目待确认')))];
    return `<li>${esc(name)}：${esc(row.status==='required_dependency_unverified'&&missing.length?missing.join('、'):'具体项目待确认')}</li>`;
  });
  return `<p>检查已完成，正式推荐尚未就绪；本人适用与正式可选仍待核实。</p><p class="field-help">${esc(scope)}</p>${sources.length?`<p class="field-help">来源准备：${esc(sources.join('；'))}。</p>`:''}${dependencies.length?`<p class="field-help">尚待核实：</p><ul class="results-list">${dependencies.join('')}</ul>`:''}<p class="field-help">已有来源不表示业务依赖已核实；课程参考仅含本地名称与代码。</p>${readiness.delivery_dependency==='real_draft_freeze_and_export_contract_not_connected'?'<p class="field-help">本人方案保存与导出契约仍待接通。</p>':''}`;
}
function personalCoursePlanValue(value){return value&&typeof value==='object'&&value.schema_version==='personal-course-data-test/1'&&value.mode==='current_term_course_data_test'&&value.reference_mode==='historical_course_observations'&&value.validation_scope==='observed_weekday_periods_conservative_overlap_policy'?value:null;}
function personalCoursePlanReason(reason){const names={course_data_test_not_configured:'本学期课程数据测试尚未配置。',source_preparation_required:'本轮所需本人来源尚未齐备。',timetable_not_complete:'本轮课表来源尚不完整，暂不能完成排课检查。',timetable_unparseable:'本轮课表缺少可核对的星期或节次。',personal_timetable_changed_requires_regeneration:'本人课表已变化，需要按本轮资料重新生成。',course_data_unavailable:'获准课程观察暂不可用。',course_search_required:'本轮课程候选尚未检索，需要先按指定范围检索。',course_data_version_changed:'课程观察版本已变化，需要重新检索。',hard_preferences_unresolved:'仍有硬条件待明确，请补充后再计算。',change_evidence_required:'请明确本轮要修改哪项条件，再按新条件调整。',policy_context_required:'当前任务条件尚未建立，请先说明选课目标和必须保留的要求。',locked_scope_requires_revision:'范围调整涉及已锁定课程，需要明确如何保留或调整锁定。',no_observed_option_within_scope:'在本轮已检查范围内尚未找到符合条件的测试方案。',stale_plan_reference:'上一轮方案已不可用，需要重新生成。',stale_plan_revision:'方案版本已变化，请按当前方案继续调整。'};return Object.hasOwn(names,reason)?names[reason]:'本轮尚无可展示的测试方案。';}
function personalCoursePlanPolicy(value,showRecords=true){
  if(!value||typeof value!=='object'||!['ready','needs_clarification'].includes(value.status))return '';
  const reasons={multiple_time_scopes_require_review:'涉及多个时间范围，请分别说明星期和节次。',clock_period_mapping_unverified:'需要核对钟点与学校节次的对应关系。',invalid_period_scope:'请补充有效的星期和节次范围。',unsupported_hard_condition:'这项硬条件暂不能可靠计算，需要进一步明确。',unsupported_positive_schedule_requirement:'当前尚不能可靠落实该上课时段要求。',hard_condition_evidence_required:'缺少支持这项硬条件的来源或具体范围。',conflicting_credit_bounds:'学分目标和上限有冲突，请明确保留哪一项。',hard_condition_budget_exceeded:'硬条件数量超出本轮处理范围，请先保留关键条件。',current_user_change_evidence_required:'还需说明本轮允许修改哪些条件。',non_asserted_hard_condition:'这句话未明确提出必须满足的条件，请说明实际要求。'};
  const unresolved=(Array.isArray(value.unresolved)?value.unresolved:[]).slice(0,6).map(item=>`<li><span><strong>${esc(personalText(item?.text,'待补充的硬条件'))}</strong>：${esc(Object.hasOwn(reasons,item?.code)?reasons[item.code]:'还需补充具体条件后核对。')}</span></li>`);
  const records=(showRecords&&Array.isArray(value.enforced_hard_preferences)?value.enforced_hard_preferences:[]).filter(text=>typeof text==='string'&&text.trim()).slice(0,6);
  const soft=(Array.isArray(value.unhandled_soft_preferences)?value.unhandled_soft_preferences:[]).filter(text=>typeof text==='string'&&text.trim()).slice(0,6);
  return `${value.status==='needs_clarification'?'<p>硬条件尚未完整解析，补充后再计算方案。</p>':''}${unresolved.length?`<ul class="results-list">${unresolved.join('')}</ul>`:''}${records.length?`<details class="personal-plan-request-records"><summary>需求记录</summary><ul class="results-list">${records.map(text=>`<li><span>${esc(text)}</span></li>`).join('')}</ul></details>`:''}${soft.length?`<p class="field-help">以下软偏好尚未参与排序：${esc(soft.join('；'))}。</p>`:''}`;
}
function personalCoursePlanSources(value){
  const names={major_academic:'主修成绩',curriculum:'培养资料',timetable:'本人课表',exams:'本人考试'},states={available:'本轮可用',partial:'部分取得',missing:'尚未取得',failed:'读取未完成',not_required:'本次不需要',not_requested:'本轮未请求',unparseable:'字段待核对',changed:'来源已变化'},sources=value.source_preparation||{};
  return Object.entries(names).map(([key,name])=>`${name}：${Object.hasOwn(states,sources[key])?states[sources[key]]:'待确认'}`).join('；');
}
function personalSoftScopeLabel(scope){
  if(!scope||typeof scope!=='object'||!Number.isInteger(scope.weekday)||scope.weekday<1||scope.weekday>7)return null;
  const day='周'+'一二三四五六日'[scope.weekday-1];
  if(scope.periods===null)return day+'全天';
  const periods=scope.periods;if(!Array.isArray(periods)||!periods.length||periods.length>60||periods.some((n,i)=>!Number.isInteger(n)||n<1||n>60||(i>0&&n<=periods[i-1])))return null;
  return day+'第'+periods.join('、')+'节';
}
function personalSoftPreferenceView(result,option){
  const scopes=result?.constraints?.soft_free_slots;
  if(!Array.isArray(scopes)||!scopes.length||scopes.length>7||scopes.some((s,i)=>!personalSoftScopeLabel(s)||(i>0&&s.weekday<=scopes[i-1].weekday)))return null;
  const evaluation=option?.soft_preference_evaluation,rows=evaluation?.scopes;
  if(evaluation?.status!=='evaluated'||evaluation.method!=='observed_meeting_overlap_count'||!Number.isSafeInteger(evaluation.violation_count)||evaluation.violation_count<0||evaluation.violation_count>100000||!Array.isArray(rows)||rows.length!==scopes.length)return null;
  if(rows.some((s,i)=>!personalSoftScopeLabel(s)||s.weekday!==scopes[i].weekday||JSON.stringify(s.periods)!==JSON.stringify(scopes[i].periods)||!Number.isSafeInteger(s.meeting_count)||s.meeting_count<0||s.meeting_count>100000)||rows.reduce((sum,s)=>sum+s.meeting_count,0)!==evaluation.violation_count)return null;
  return {total:evaluation.violation_count,labels:scopes.map(personalSoftScopeLabel),details:rows.map(s=>`${personalSoftScopeLabel(s)} ${s.meeting_count} 段`)};
}
function personalObservedCodeLink(course){
  const links=course?.observed_code_links;
  if(!Array.isArray(links)||links.length!==1)return '';
  const link=links[0],code=link?.code;
  if(!link||Object.keys(link).length!==4||link.relation_kind!=='same_source_header'||link.verification_status!=='observed'||link.source_field!=='display_code'||typeof code!=='string'||!(/^[A-Za-z0-9][A-Za-z0-9_.-]{0,59}$/).test(code)||(/\d{11,}|password|cookie|token|authorization|session/i).test(code)||(/^\d+$/).test(code)&&!(code.length>=6&&code.length<=10)||code.toLowerCase()===course?.course_code?.toLowerCase())return '';
  return `<br><span class="field-help">同一来源标题另列代码 ${esc(code)}；课程等价与本人适用未核实。</span>`;
}
function personalComputationDetails(summary){
  if(!summary||summary.origin!=='owned_last_computation')return '';
  const amount=value=>typeof value==='string'&&/^[0-9]+(?:\.[0-9]+)?$/.test(value)&&value.length<=64?value:'未记录';
  const count=value=>Number.isSafeInteger(value)&&value>=0?String(value):'未记录';
  const credit=summary.credit_target||{},scope=summary.scope||{},options=(Array.isArray(summary.options)?summary.options:[]).filter(item=>['A','B','C'].includes(item?.label)).slice(0,3);
  const criteria=['observed_slot_result','soft_meeting_count','known_credits','excess_above_minimum','course_count','stable_observation_order'];
  const ranking=summary.ranking?.version==='current-term-observed-ranking/1'&&JSON.stringify(summary.ranking.criteria)===JSON.stringify(criteria)?'排序依次考虑：观察节次检查、软空闲时段占用、已知学分、超出下限的学分、课程数量和固定观察顺序。空闲偏好更好的组合可能排在学分更接近目标的组合之前。':'原计算未记录完整排序依据，不能补推具体取舍。';
  const exact={seen_feasible:'原搜索记录了恰好达到下限的组合。',not_found_in_complete_examined_scope:'在原计算池内未找到恰好达到下限的组合。',search_incomplete:'原搜索未完成，不能排除恰好达到下限的组合。'}[summary.exact_target?.status]||'是否存在恰好达到下限的其他组合未记录。';
  return `<div class="personal-computation-details"><h4>已保存的计算记录</h4><p>学分下限 ${esc(amount(credit.minimum))} · 上限 ${credit.maximum===null?'未设置':esc(amount(credit.maximum))}</p><ul>${options.map(option=>`<li>方案 ${option.label}：${esc(amount(option.total_credits))} 学分，比下限多 ${esc(amount(option.excess_above_minimum))}；软空闲时段占用 ${count(option.soft_meeting_count)} 段，周五占用 ${count(option.friday_meeting_count)} 段，锁定 ${count(option.locked_class_count)} 项。</li>`).join('')}</ul><p>${esc(ranking)}</p><p>${esc(exact)}</p><p>输入池 ${count(scope.input_course_count)} 门，检索返回 ${count(scope.returned_search_count)} 门，未进入计算池 ${count(scope.omitted_count)} 门；未进入不表示不适用。${scope.limit_reached===true?'已达搜索上限，仅覆盖已检查部分。':scope.complete_within_input_scope===true?'输入池内有界搜索已完成。':'输入池内搜索完整性未确认。'}输入池不代表学校全部开课。</p><p class="field-help">本次未重新读取教务资料或求解。历史课程观察的当前开课、可选资格、培养适用、完整周次、校历和考试仍待核实。</p></div>`;
}
function personalCoursePlanEvidence(value){
  const result=personalCoursePlanValue(value);if(!result)return '<p class="field-help">本次课程数据测试结果结构待确认。</p>';
  if(result.action==='inspect')return `<p>已定位上一轮方案的选择信息；这次回查不证明旧课程事实仍有效，比较或调整仍需本轮资料核查。</p>${personalComputationDetails(result.computation_summary)}${personalCoursePlanPolicy(result.policy_status)}`;
  const options=Array.isArray(result.options)?result.options.slice(0,3):[],courses=Array.isArray(result.courses)?result.courses.slice(0,10):[];
  const computed=options.length>0||result.reason==='no_observed_option_within_scope';
  const actions={search:result.reason?'课程观察检索尚未完成':'已检索获准课程观察',generate:computed?'已执行条件测试计算':'条件测试尚未计算',revise:computed?'已按调整条件重新计算':'本次调整尚未执行',compare:computed?'已按本轮资料比较方案':'本次比较尚未执行'},action=Object.hasOwn(actions,result.action)?actions[result.action]:'本次处理状态待确认';
  const summary=result.reason?personalCoursePlanReason(result.reason):result.action==='search'?`返回 ${courses.length} 门课程观察候选，尚未生成方案。`:`返回 ${options.length} 套条件测试方案。`;
  const candidates=result.action==='search'&&courses.length?`<ul class="results-list">${courses.map(course=>`<li><span>${esc(personalText(course?.course_name,'课程名称待确认'))} · ${esc(personalText(course?.course_code,'课程代码待确认'))} · ${esc(personalText(course?.credits))} 学分${personalObservedCodeLink(course)}</span></li>`).join('')}</ul>${result.search?.truncated===true?'<p class="field-help">本次仅展示部分候选，可继续缩小课程范围。</p>':''}`:'';
  const scope=result.action==='search'?'':`<p class="field-help">来源准备：${esc(personalCoursePlanSources(result))}。</p>`;
  const search=result.search_status?.limit_reached===true?'<p class="field-help">搜索已达本轮上限，结果仅限已检查范围。</p>':'';
  return `<p>${esc(action)} · ${esc(personalTermLabel(result.target_term))}</p><p>${esc(summary)}</p>${candidates}${scope}${personalCoursePlanPolicy(result.policy_status)}${search}<p class="field-help">历史课程观察不证明当前开课、可选或有余量；正式适用、完整周次、校历和考试仍待核验。</p>`;
}
function personalPlanHeadline(result,options){
  const amount=value=>typeof value==='string'&&/^\d+(?:\.\d+)?$/.test(value)&&Number.isFinite(Number(value))?Number(value):typeof value==='number'&&Number.isFinite(value)&&value>=0?value:null;
  const minimum=amount(result.constraints?.target_credits),maximum=amount(result.constraints?.max_credits);
  const known=options.map(option=>amount(option.total_credits));
  const meets=minimum!==null&&known.every(value=>value!==null&&value>=minimum&&(maximum===null||value<=maximum));
  const credit=meets?`均满足至少 ${personalText(result.constraints.target_credits)} 学分${maximum===null?'':'、不超过 '+personalText(result.constraints.max_credits)+' 学分'}的要求。`:'学分与条件见各方案。';
  const requiredCount=result.constraints?.course_count,countText=Number.isSafeInteger(requiredCount)&&requiredCount>0&&options.every(option=>option.classes?.length===requiredCount)?`每套 ${requiredCount} 门。`:'';
  const friday=options.map(option=>option.friday_meeting_count),valid=friday.every(value=>Number.isSafeInteger(value)&&value>=0);
  const preference=['soft','hard'].includes(result.constraints?.friday_free);
  const time=preference&&valid?(friday.every(value=>value>0)?'本次方案周五都有占用，尚未实现周五空闲。':friday.every(value=>value===0)?'按已有节次，周五均未见占用。':'各方案的周五占用不同，可按下方时间比较。'):preference?'周五占用仍待核对。':'';
  return `<p class="personal-plan-conclusion">${esc(countText+credit)}${time?` <strong>${esc(time)}</strong>`:''}</p>`;
}
function renderPersonalCoursePlans(value,stale=false,view='cards'){
  const result=personalCoursePlanValue(value);if(!result||!['generate','revise','compare'].includes(result.action))return '';
  const options=(Array.isArray(result.options)?result.options:[]).slice(0,3),count=value=>Number.isSafeInteger(value)&&value>=0?String(value):'待确认';
  const coursesOnly=result.recommendation_kind==='courses',kindTitle=coursesOnly?'课程组合条件测试':result.recommendation_kind==='timetable'?'课表条件测试':null;
  const title=kindTitle?kindTitle+(options.length?result.action==='compare'?'比较':'':'状态'):options.length?result.action==='compare'?'条件测试方案比较':'条件测试方案':'条件测试状态';
  const locked=Array.isArray(result.locked_class_refs)?new Set(result.locked_class_refs.filter(item=>typeof item==='string')).size:null;
  const constraints=result.constraints&&typeof result.constraints==='object'?result.constraints:{},preferences=[];
  if(constraints.target_credits!=null)preferences.push(`至少 ${personalText(constraints.target_credits)} 学分`);
  if(constraints.max_credits!=null)preferences.push(`上限 ${personalText(constraints.max_credits)} 学分`);
  if(constraints.friday_free==='soft')preferences.push('周五尽量空闲');else if(constraints.friday_free==='hard')preferences.push('周五必须空闲');
  const required=(Array.isArray(constraints.required_course_codes)?constraints.required_course_codes:[]).filter(code=>typeof code==='string').slice(0,12);if(required.length)preferences.push('必含课程 '+required.join('、'));
  const cards=options.map(option=>{
    const label=['A','B','C'].includes(option?.label)?option.label:'待确认',slot={pass:'观察节次检查通过',unknown:'观察节次待确认',fail:'观察节次存在冲突'},check=Object.hasOwn(slot,option?.observed_slot_result)?slot[option.observed_slot_result]:'观察检查状态待确认';
    const classes=(Array.isArray(option?.classes)?option.classes:[]).slice(0,12).map(row=>{
      const periods=(Array.isArray(row?.weekday_period_text)?row.weekday_period_text:[]).filter(text=>typeof text==='string'&&text.trim()).slice(0,60);
      return `<li><div class="personal-plan-course-heading"><strong>${esc(personalText(row?.course_name,'课程待确认'))}</strong><span>${esc(personalText(row?.credits))} 学分</span>${row?.locked===true?'<span class="badge neutral">已锁定</span>':''}</div><p>${periods.length?periods.map(text=>esc(text)).join('；'):'观察节次待确认'}</p></li>`;
    }).join('');
    const friday=Number.isSafeInteger(option?.friday_meeting_count)&&option.friday_meeting_count>=0?(option.friday_meeting_count===0?'周五未见占用':`周五占用 ${option.friday_meeting_count} 段`):'周五占用待确认';
    const soft=personalSoftPreferenceView(result,option);
    const softDetails=soft?soft.details.filter((_,index)=>{const scope=option.soft_preference_evaluation.scopes[index];return scope.weekday!==5||scope.periods!==null||scope.meeting_count!==option.friday_meeting_count;}):[];
    return `<article class="personal-plan-option"><div class="personal-plan-option-heading"><h4>方案 ${esc(label)}</h4><strong>${esc(personalText(option?.total_credits))}<span> 学分</span></strong></div><p class="personal-plan-timing">${esc(friday)}${option?.observed_slot_result==='pass'?'':` · ${esc(check)}`}</p>${softDetails.length?`<p class="personal-plan-timing">偏好时段占用：${esc(softDetails.join('；'))}</p>`:''}<ul class="personal-plan-classes">${classes||'<li>本轮未取得可展示的班次。</li>'}</ul></article>`;
  }).join('');
  const search=result.search_status?.limit_reached===true?'<p class="field-help">搜索已达本轮上限，可能还有其他组合。</p>':result.search_status?.complete_within_observed_scope===false?'<p class="field-help">结果仅限本轮已检查范围，可能还有其他组合。</p>':'';
  const labels=options.map(option=>option?.label).filter(label=>['A','B','C'].includes(label));
  const next=labels.length>1?`可继续说“比较 ${labels[0]} 和 ${labels[1]}”，或说明要锁定、替换的课程与时间偏好。`:options.length?'可继续说明要锁定、替换的课程与时间偏好。':'可继续调整课程范围、学分目标或时间偏好。';
  const computed=options.length>0||result.reason==='no_observed_option_within_scope',countsChecked=computed&&result.constraints!=null&&result.search_status!=null;
  // Older blocked turns also used an empty array as a placeholder. Do not
  // promote that default to a verified zero when restoring their history.
  const knownSelection=typeof result.plan_ref==='string'&&Number.isSafeInteger(result.revision)&&result.revision>=0&&locked>0;
  const blockedAction=result.action==='revise'?'本次调整尚未执行。':result.action==='compare'?'本次比较尚未执行。':'本次条件测试尚未计算。';
  const protection=countsChecked?`<p>保留锁定 ${count(locked)} 项${coursesOnly?'':' · 本人课表固定 '+count(result.fixed_arrangement_count)+' 项'}${Number.isSafeInteger(result.user_busy_arrangement_count)&&result.user_busy_arrangement_count>=0&&result.user_busy_arrangement_count<=20?' · 用户禁排时间 '+count(result.user_busy_arrangement_count)+' 项':''}</p>`:`<p>${computed?'本次测试已计算，未找到方案；原方案未更新。':blockedAction}${knownSelection?`原锁定 ${count(locked)} 项保留。`:'锁定状态待确认。'}</p><p class="field-help">${computed?'本次计算未提供固定项与用户禁排时间计数。':'本人课表固定项与用户禁排时间尚未完成本轮核对。'}</p>`;
  const policy=personalCoursePlanPolicy(result.policy_status,false),scope=`<p class="field-help">测试数据 · ${esc(personalTermLabel(result.target_term))} · 历史课程观察</p>${coursesOnly?'<p class="field-help">课程组合参考，未执行与本人课表的时段核对。</p>':''}`;
  const limits='<p class="field-help">仅核对已观察的星期与节次；当前开课、可选资格、余量、完整周次、校历、考试及培养适用仍未验证。</p>';
  const staleNote=stale?'<p class="personal-plan-notice">历史测试结果，来源版本已变化；继续比较或调整需按新一轮资料复核。</p>':'';
  if(!options.length)return `<section class="personal-course-plans" aria-label="课程数据条件测试方案"><h3>${title}</h3>${scope}${staleNote}${preferences.length?`<p>${esc(preferences.join(' · '))}</p>`:''}${protection}${policy}<p>${esc(personalCoursePlanReason(result.reason))}</p>${search}${limits}<p class="personal-plan-next">${esc(next)}</p></section>`;
  const activeProtection=countsChecked?[locked>0?`已保留 ${count(locked)} 项锁定`:'',!coursesOnly&&result.fixed_arrangement_count>0?`保留 ${count(result.fixed_arrangement_count)} 项本人固定安排`:'',result.user_busy_arrangement_count>0?`保留 ${count(result.user_busy_arrangement_count)} 项禁排时间`:''].filter(Boolean).join(' · '):'';
  return `<section class="personal-course-plans" aria-label="课程数据条件测试方案"><h3>${options.length} 套${coursesOnly?'课程组合':result.recommendation_kind==='timetable'?'课表':'课程'}参考方案</h3>${staleNote}${personalPlanHeadline(result,options)}<p class="personal-plan-scope">${esc(personalTermLabel(result.target_term))}历史课程参考，正式适用未核。${coursesOnly?'未核本人课表及考试冲突。':'完整周次与考试冲突待核验。'}</p>${activeProtection?`<p class="personal-plan-protection">${esc(activeProtection)}</p>`:''}${view==='table'?renderPlanComparison(result,options):`<div class="personal-plan-options">${cards}</div>`}${search}${policy}</section>`;
}
function personalCitationContext(value){
  const context=value&&typeof value==='object'?value:{},states=context.field_states||{},profile=[];
  if((!states.cohort||states.cohort==='known')&&Number.isSafeInteger(context.cohort)&&context.cohort>=1900&&context.cohort<=2100)profile.push(['入学年份',context.cohort]);
  if((!states.major_name||states.major_name==='known')&&typeof context.major_name==='string'&&personalText(context.major_name,'')!=='')profile.push(['专业名称',context.major_name]);
  const names={academic:'学期成绩',major_academic:'主修成绩',timetable:'课表',exams:'考试'},operations=Array.isArray(context.operations)?context.operations.filter(item=>item&&Object.hasOwn(names,item.operation)):[];
  return `${profile.length?`<dl class="personal-citation-context">${profile.map(([label,text])=>`<div><dt>${label}</dt><dd>${esc(text)}</dd></div>`).join('')}</dl>`:'<p class="field-help">入学年份、专业名称尚无可展示的已知值。</p>'}${operations.length?`<p class="field-help">当前已读范围：</p><ul class="results-list">${operations.map(item=>`<li>${names[item.operation]} · ${esc(personalTermLabel(item.target_term))} · ${esc(schoolCoverageText(item))}</li>`).join('')}</ul>`:'<p class="field-help">当前尚无可展示的已读范围，不据此推断没有学校记录。</p>'}`;
}
function personalPageLabel(item){const locator=item?.locator&&typeof item.locator==='object'?item.locator:{};const value=item?.page??item?.physical_page??locator.page??locator.pdf_page??locator.physical_page;const pages=Array.isArray(value)?value:[value];const valid=pages.filter(page=>Number.isSafeInteger(page)&&page>0);return valid.length?`PDF 第 ${valid.join('、')} 页`:'页码待确认';}
function personalContextMarkup(items){return Array.isArray(items)&&items.length?items.map(item=>{const value=typeof item==='string'?{text:item}:item||{};return `<div class="personal-context"><strong>${esc(personalText(value.title,'必要上下文'))}</strong><p>${esc(personalText(value.text??value.excerpt,'上下文原文未提供'))}</p>${typeof item==='object'?`<span class="field-help">${esc(personalPageLabel(value))}</span>`:''}</div>`;}).join(''):'<p class="field-help">必要上下文未提供，不能据此补推完整要求。</p>';}
function personalChatEvidence(item,options={}){
  const evidence=item&&typeof item==='object'?item:{},context=`${evidence.context_omitted?'<p class="field-help">本机上下文因展示预算未载入，请缩小问题后重新检索。</p>':personalContextMarkup(evidence.required_context)}<p class="field-help">本人适用、文本版面及跨页关系仍需核验。</p>`;
  return `<article class="personal-evidence"><div class="personal-evidence-heading"><strong>${personalCitationNumber(evidence)}${esc(personalText(evidence.title,'培养资料片段'))}</strong><span class="badge">${esc(personalPageLabel(evidence))}</span><span class="badge warning">${esc(personalReviewLabel(evidence.review_status||(evidence.kind==='curriculum'?'unreviewed':null)))}</span></div><p class="personal-excerpt">${esc(personalText(evidence.text??evidence.excerpt,'原文未提供'))}</p>${options.inlineContext?`<div class="personal-evidence-context"><h5>原文上下文</h5>${context}</div>`:`<details><summary>查看原文上下文</summary>${context}</details>`}</article>`;
}
function personalGradeSummary(value,source={}){
  if(!value||typeof value!=='object')return '<p class="field-help">来源统计尚未取得，不能据此评价完整成绩。</p>';
  const counts=[['matched_records','匹配来源记录'],['numeric_grade_count','数字成绩'],['non_numeric_grade_count','非数字成绩'],['unknown_grade_count','未知成绩']];
  const count=value=>Number.isSafeInteger(value)&&value>=0?String(value):'待确认',decimal=value=>typeof value==='string'&&/^(?:100(?:\.0+)?|(?:[0-9]|[1-9][0-9])(?:\.[0-9]+)?)$/.test(value)?value:'待确认';
  const coverage=value.coverage&&Object.keys(value.coverage).length?value.coverage:source.coverage;
  const numbers=value.numeric_grade_count===0?'<p class="field-help">当前匹配范围没有可用于数字统计的来源成绩。</p>':`<dl class="personal-citation-context"><div><dt>最低数字成绩</dt><dd>${decimal(value.numeric_min)}</dd></div><div><dt>最高数字成绩</dt><dd>${decimal(value.numeric_max)}</dd></div><div><dt>简单不加权平均</dt><dd>${decimal(value.numeric_mean_unweighted)}</dd></div></dl>`;
  return `<section class="agent-source-summary"><p class="field-help">${esc(personalSourceKind(source))} · ${esc(personalTermLabel(source.target_term))} · ${esc(schoolCoverageText({coverage}))}</p><dl class="personal-citation-context">${counts.map(([key,label])=>`<div><dt>${label}</dt><dd>${count(value[key])}</dd></div>`).join('')}</dl>${numbers}<p class="field-help">统计基于当前快照的全部匹配行，不受展示条数限制；数字来源成绩仅作简单不加权平均，不是 GPA、绩点、已获学分或修读认定。</p></section>`;
}
function personalRecordTermMarkup(record){
  const parts=[];
  if(schoolKnownField(record,'academic_year_raw'))parts.push(personalText(record.academic_year_raw,''));
  if(schoolKnownField(record,'term_raw'))parts.push('学期 '+personalText(record.term_raw,''));
  return parts.length?`<small>${esc(parts.join(' · '))}</small>`:'';
}
function personalRecordTable(result){
  const operation=result.operation;
  const records=Array.isArray(result.records)?result.records:[];if(!records.length)return '<p class="inline-empty">本次已读范围中没有匹配记录。可修改查询词，或前往“我的教务”正常读取所需范围。</p>';
  const academic=['academic','major_academic'].includes(operation);
  const fields=academic?[['grade_raw','成绩'],['grade_points_raw','绩点'],['nominal_credits','学分'],['academic_year_raw','学年'],['term_raw','学期'],['completion_status_raw','修读状态']]:operation==='exams'?[['final_exam_time_raw','期末考试时间'],['final_exam_place_raw','期末考试地点'],['midterm_exam_time_raw','期中考试时间'],['midterm_exam_place_raw','期中考试地点'],['exam_time_raw','考试时间'],['exam_date','考试日期'],['start_time','开始时间'],['end_time','结束时间'],['place_raw','考试地点']]:[['time_raw','上课时间'],['place_raw','上课地点'],['academic_year_raw','学年'],['term_raw','学期']];
  const available=result.compact&&academic?fields.slice(0,3).map(([key,label])=>[key,key==='grade_points_raw'?'来源绩点':key==='nominal_credits'?'名义学分':label]):fields.filter(([key])=>records.some(record=>record&&schoolKnownField(record,key)&&personalText(record[key],'')!==''));
  return `<div class="table-wrap"><table class="data-table personal-record-table"><caption class="sr-only">本机已读记录，保留相同课程的多条来源记录</caption><thead><tr><th scope="col">课程名称</th>${available.map(([,label])=>`<th scope="col">${label}</th>`).join('')}</tr></thead><tbody>${records.map((record,index)=>{const item=record&&typeof record==='object'?record:{};const name=schoolKnownField(item,'course_name_raw')?personalText(item.course_name_raw,'未命名课程'):'课程名称待确认';const code=schoolKnownField(item,'course_code_raw')?personalText(item.course_code_raw,''):'';return `<tr><td>${personalCitationNumber({citation_number:result.citation_numbers?.[index]})}${esc(name)}${code?`<small>${esc(code)}</small>`:''}${result.compact&&academic?personalRecordTermMarkup(item):''}</td>${available.map(([key])=>`<td>${schoolKnownField(item,key)?esc(personalText(key==='completion_status_raw'?schoolCompletionStatusText(item[key]):item[key],'—')).replace(/\n/g,'<br>'):'<span class="muted">待确认</span>'}</td>`).join('')}</tr>`;}).join('')}</tbody></table></div>`;
}
function stopAgentProgress(session=state.agentProgressPoll){
  if(!session)return;session.stopped=true;if(session.timer!==undefined&&typeof clearTimeout==='function')clearTimeout(session.timer);if(state.agentProgressPoll===session)state.agentProgressPoll=null;
}
function activeAgentProgress(session){return !session.stopped&&state.agentProgressPoll===session&&sameAgentDiagnostics(session.capture)&&state.agentTurns.includes(session.turn);}
function startAgentProgress(turn,requestId){
  stopAgentProgress();const session={turn,requestId,capture:agentDiagnosticsCapture(),stopped:false,timer:undefined,sequence:0};state.agentProgressPoll=session;
  turn.progress={request_id:requestId,status:'not_started',events:[],next_sequence:0,active_reasoning:null,available:true,confirmed:false};
  pollAgentProgress(session);return session;
}
async function pollAgentProgress(session){
  if(!activeAgentProgress(session))return;
  try{
    const result=await api('/api/chat/progress',{request_id:session.requestId,after_sequence:session.sequence},session.capture.agentEpoch);
    if(!activeAgentProgress(session))return;
    if(result.request_id!==session.requestId||!Array.isArray(result.events)||!Number.isSafeInteger(result.next_sequence)||result.next_sequence<session.sequence)throw Error('invalid progress');
    session.sequence=result.next_sequence;session.turn.progress={...result,confirmed:result.status!=='not_started'};session.turn.progress_error='';if(typeof result.run_id==='string')session.turn.run_id=result.run_id;
    renderAssistant();
    if(result.terminal===true||result.available===false){stopAgentProgress(session);return;}
  }catch(error){
    if(!activeAgentProgress(session))return;
    session.turn.progress_error=error.status===404||error.status===405?'当前服务尚不支持实时进度；完成后读取本机运行详情。':'实时进度暂不可用；本轮请求仍在处理中，不会重复发送。';renderAssistant();stopAgentProgress(session);return;
  }
  if(activeAgentProgress(session))session.timer=setTimeout(()=>pollAgentProgress(session),800);
}
function agentTurnProgress(turn){
  const terminal=!turn.pending&&['completed','ok','failed'].includes(turn.status)?turn.status:null;
  const detail=turn.run_id?state.agentRunDetails[turn.run_id]:null;if(!detail)return turn.progress?(terminal?{...turn.progress,status:terminal}:turn.progress):null;
  return {request_id:detail.run?.request_id||turn.request_id||turn.progress?.request_id,run_id:turn.run_id,status:terminal||detail.run?.status||turn.status,model:detail.run?.model??turn.model,thinking:detail.run?.thinking??turn.thinking,duration_ms:detail.run?.duration_ms,error_code:detail.run?.error_code,events:detail.events||[],active_reasoning:null,reasoning_notice:detail.reasoning_notice,available:true,confirmed:true,next_sequence:turn.progress?.next_sequence||0};
}
function agentProcessStage(progress,taskStatus){
  if(['completed','ok','success'].includes(progress.status))return ['awaiting_user','needs_clarification'].includes(taskStatus)?'本轮已结束，待补充信息':taskStatus==='partial'?'本轮已结束，已有部分结果':taskStatus==='blocked'?'本轮已结束，任务受阻':'本轮处理完成';
  if(['failed','error','revoked','cancelled'].includes(progress.status))return '本轮处理未完成';
  if(progress.status==='not_started'||progress.confirmed===false)return '请求已发送，等待运行状态';
  const event=agentEventData((progress.events||[]).at(-1)),kind=event.kind||event.type;
  const sourceNames={read_my_major_academic:'主修成绩',read_my_academic:'学期成绩',read_my_curriculum:'培养资料'};
  if(kind==='tool.start'&&sourceNames[event.tool_name])return '正在读取'+sourceNames[event.tool_name];
  if(kind==='tool.end'&&sourceNames[event.tool_name])return sourceNames[event.tool_name]+(event.status==='not_ready'?'资料尚未就绪':['failed','error','timeout'].includes(event.status)?'读取未完成':'读取已返回');
  const courseStages={prepare_recommendation_inputs:'正在准备推荐所需本人来源',search_current_term_courses:'正在检索本学期课程观察',generate_current_term_plan:'正在计算课程数据测试方案',update_current_term_plan:'正在复核或调整测试方案',get_current_term_plan:'正在定位上一轮方案选择'};
  if(kind==='tool.start'&&Object.hasOwn(courseStages,event.tool_name))return courseStages[event.tool_name];
  if(kind==='tool.end'&&Object.hasOwn(courseStages,event.tool_name))return event.status==='not_ready'?(agentCourseTestTool(event.tool_name)?'课程条件测试受阻，待核对':'课程数据测试所需资料尚未齐备'):['failed','error','timeout'].includes(event.status)?'课程数据测试调用未完成':event.tool_name==='get_current_term_plan'?'方案选择信息已返回，继续核查':event.tool_name==='search_current_term_courses'?'课程观察已返回，继续处理':'本次工具已返回，继续检查答复';
  if(kind==='tool.start')return '正在检索当前本人资料';
  if(kind==='tool.rejected')return event.status==='retrying'?'工具参数检查未通过，正在修正':'工具参数检查未通过，未执行本次调用';
  if(kind==='tool.end')return event.status==='not_ready'?'查询资料尚未就绪':['failed','error','timeout'].includes(event.status)?'资料查询未完成':'资料检索已返回，继续处理';
  if(kind==='model.end')return event.status==='timeout'?'模型服务返回超时':['failed','error','cancelled','aborted'].includes(event.status)?'模型调用未完成':'模型已返回，正在检查答复或继续处理';
  if(kind==='answer.validated')return '格式与引用检查完成';if(kind==='answer.rejected')return '答复检查未通过';if(kind==='answer.repair')return '正在修正答复格式与引用';if(kind==='error')return '运行出现错误';
  if(kind==='model.start')return progress.thinking===true?'模型正在深度思考':'模型正在处理';
  return '正在准备本轮处理';
}
function agentShortId(value){return typeof value==='string'&&/^[A-Za-z0-9_-]{1,128}$/.test(value)?value.replace(/^RUN-/,'').slice(0,8):'';}
function agentDuration(value){return Number.isFinite(value)&&value>=0?(value<1000?`${Math.round(value)} ms`:`${(value/1000).toFixed(1)} 秒`):'';}
function agentTerminalFailure(turn,events){
  const terminal=events.find(event=>event.kind==='error');
  const code=turn.code||terminal?.error_code||terminal?.code;
  const rejection=events.find(event=>event.kind==='answer.rejected'&&(!code||event.error_code===code));
  return {...rejection,...terminal,...(code?{error_code:code}:{})};
}
function agentFailureText(event){
  const code=event.error_code||event.code||event.error?.code,detail=event.failure_detail;
  if((event.kind||event.type)==='tool.rejected')return '参数未通过检查，未执行；'+(event.status==='retrying'?'正在修正':'本轮未完成');
  if(code==='model_http_402')return '模型服务返回 HTTP 402，请核对模型账户计费状态后再试（model_http_402）';
  const reasons={invalid_final_answer:'模型答复格式未通过检查',unverified_personal_answer:'答复引用未通过本轮证据检查',unsupported_personal_claim:'答复包含当前证据不能确认的结论',model_timeout:'模型服务返回超时',model_service_unavailable:'模型服务暂不可用',model_step_limit:'本轮达到模型调用上限',chatgpt_reconnect_required:'ChatGPT 授权已失效，请重新连接',subscription_sharing_usage_limit_exceeded:'ChatGPT 订阅共享额度已用完',subscription_sharing_usage_unavailable:'ChatGPT 订阅共享额度暂不可用',model_stream_incomplete:'模型连接中断，本轮未完成',personal_model_changed:'模型设置已变化',personal_scope_changed:'本人授权范围已变化',source_session_expired:'学校会话已失效',source_unavailable:'学校来源暂不可用',invalid_tool_arguments:'工具参数未通过检查',tool_unavailable:'资料工具暂不可用'};
  const details={invalid_json:'未返回有效 JSON',answer_not_object:'答复结构不是对象',clarification_text_invalid:'澄清文本格式无效',answer_shape_invalid:'答复结构不符合约定',claims_not_array:'结论列表格式无效',claim_count_out_of_bounds:'结论条数超出允许范围',claim_shape_invalid:'结论字段格式无效',claim_text_invalid:'结论文字格式无效',evidence_list_invalid:'引用列表格式无效',evidence_not_current:'引用不属于本轮有效证据',repair_evidence_omitted:'修正后遗漏了原有有效依据'};
  const safeCode=/^[a-z][a-z0-9_]{0,79}$/.test(code||'');
  return [reasons[code]||(safeCode?'运行错误':''),details[detail]||''].filter(Boolean).join('：')+(safeCode?`（${code}）`:'');
}
async function hydrateAgentProcess(turn,capture=agentDiagnosticsCapture()){
  if(!isAcademicIdentity()||typeof turn.run_id!=='string')return;
  try{const result=await api('/api/chat/run',{run_id:turn.run_id},capture.agentEpoch);if(!sameAgentDiagnostics(capture)||!state.agentTurns.includes(turn))return;if(result.run?.run_id!==turn.run_id||!Array.isArray(result.events))throw Error('invalid final progress');state.agentRunDetails[turn.run_id]=result;turn.progress_error='';}
  catch(error){if(sameAgentDiagnostics(capture)&&state.agentTurns.includes(turn))turn.progress_error='本轮运行过程暂未载入，可重新读取处理记录。';}
}

function agentDiagnosticsCapture(){return {...currentSchoolRequest(),agentEpoch:state.agentEpoch};}
function sameAgentDiagnostics(capture){return sameSchoolRequest(capture)&&capture.agentEpoch===state.agentEpoch;}
function agentControlsBusy(){return state.agentBusy||state.agentActionBusy||state.agentHistoryLoadBusy||state.agentSettingsBusy||state.busy;}
function agentEffortLabel(value){
  const translations={none:'关闭',minimal:'最低',low:'低',medium:'中',high:'高',xhigh:'更高',max:'最高',ultra:'极致',default:'默认'};
  return translations[value]?`${value}（${translations[value]}）`:value;
}
function renderAgentModelControls(){
  const busy=agentControlsBusy()||state.agentModelsBusy,models=state.agentModels,known=models.some(item=>item.id===state.agentStatus?.model);
  const current=models.find(item=>item.id===state.agentStatus?.model),name=current?.name||state.agentStatus?.model||'选择模型';
  const effort=state.agentStatus?.reasoning_effort||current?.effort?.default_level||'default';
  const level=agentEffortLabel(state.agentStatus?.thinking?effort:'none');
  return `<details id="agent-model-menu" class="agent-model-menu"><summary id="agent-model-trigger" title="选择模型与推理等级" aria-disabled="${busy}"><span class="agent-model-name">${esc(name)}</span><span class="agent-model-level">${esc(level)}</span><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 10 4-4 4 4"/></svg></summary><div class="agent-model-popover"><label class="agent-model-setting" for="agent-model"><span>模型</span><select id="agent-model" title="选择对话模型" ${busy?'disabled':''}>${known?'':`<option value="" selected disabled>${esc(personalText(state.agentStatus?.model,'选择模型'))}（当前配置）</option>`}${models.map(item=>`<option value="${esc(item.id)}" ${item.id===state.agentStatus?.model?'selected':''}>${esc(item.name||item.id)}</option>`).join('')}<option value="@refresh">${state.agentModelsBusy?'读取目录中…':'刷新模型目录…'}</option></select></label>${renderAgentEffort()}${state.agentChatGPT?`<div class="agent-model-connection"><button id="agent-chatgpt-connect" type="button" ${busy||state.agentChatGPT.pending?'disabled':''}>${state.agentChatGPT.pending?'等待 ChatGPT 授权…':state.agentChatGPT.connected?'重新连接 ChatGPT':'Continue with ChatGPT'}</button>${state.agentChatGPT.connected||state.agentChatGPT.pending?`<button id="agent-chatgpt-disconnect" type="button" ${busy?'disabled':''}>断开连接</button>`:''}</div>`:''}</div></details>`;
}
function renderAgentThinking(){
  const active=state.agentStatus?.thinking===true,busy=agentControlsBusy()||state.agentModelsBusy,known=state.agentModels.some(item=>item.id===state.agentStatus?.model);
  return `<button id="agent-thinking" class="agent-thinking-toggle" type="button" aria-pressed="${active}" title="${state.agentStatus?.thinking_toggle===false?'当前模型不支持关闭思考，可在模型菜单中调整强度':(active?'关闭':'开启')+'深度思考'}" ${busy||!known||state.agentStatus?.thinking_toggle===false||typeof state.agentStatus?.thinking!=='boolean'?'disabled':''}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 4a3 3 0 0 0-5.6-1.5A3.8 3.8 0 0 0 3 8.7a4 4 0 0 0 .7 7.5A4 4 0 0 0 12 19M12 4a3 3 0 0 1 5.6-1.5A3.8 3.8 0 0 1 21 8.7a4 4 0 0 1-.7 7.5A4 4 0 0 1 12 19M12 4v15M7 8c0 2 1 3 3 3M17 8c0 2-1 3-3 3M7 16c1-2 2-2 3-2M17 16c-1-2-2-2-3-2"/></svg><span>深度思考</span></button>`;
}
function renderAgentEffort(){
  const status=state.agentStatus||{},row=state.agentModels.find(item=>item.id===status.model),cap=row?.effort||status.effort;
  if(!cap)return '';
  const levels=(cap.supported_levels||[]).filter(value=>value!=='none'),selected=status.reasoning_effort||cap.default_level||'default';
  return `<label class="agent-model-setting agent-effort-picker"><span>推理等级</span><select id="agent-reasoning-effort" aria-label="思考强度" title="思考强度" ${agentControlsBusy()||state.agentModelsBusy||!status.thinking||!levels.length?'disabled':''}>${(levels.length?levels:['default']).map(value=>`<option value="${esc(value)}" ${value===selected?'selected':''}>${esc(agentEffortLabel(value))}</option>`).join('')}</select></label>`;
}
async function connectAgentChatGPT(){
  if(agentControlsBusy()||state.agentChatGPT?.pending)return;
  const capture=agentDiagnosticsCapture(),popup=window.open('','_blank');
  if(!popup){state.agentModelsError='请允许本机页面打开登录窗口后重试。';renderAssistant();return;}
  popup.opener=null;state.agentSettingsBusy=true;state.agentModelsError='';renderAssistant();
  try{
    const result=await api('/api/chat/chatgpt/connect',{},capture.agentEpoch);
    if(!sameAgentDiagnostics(capture)){popup.close();return;}
    const url=new URL(result.authorization_url);
    if(url.origin!=='https://auth.openai.com'||url.pathname!=='/api/accounts/authorize')throw Error('授权地址校验失败。');
    popup.location.href=url.href;state.agentChatGPT={...state.agentChatGPT,pending:true};
    state.agentModelsError='请在 OpenAI 官方页面完成登录与授权，然后返回此页。';pollAgentChatGPT(capture);
  }catch(error){popup.close();if(sameAgentDiagnostics(capture))state.agentModelsError=error.message||'ChatGPT 连接未启动，请重试。';}
  finally{if(sameAgentDiagnostics(capture)){state.agentSettingsBusy=false;renderAssistant();}}
}
async function pollAgentChatGPT(capture=agentDiagnosticsCapture(),attempt=0){
  if(!sameAgentDiagnostics(capture)||attempt>=155){state.agentAuthPolling=false;return;}
  state.agentAuthPolling=true;
  try{
    const result=await api('/api/chat/chatgpt/status',undefined,capture.agentEpoch);
    if(!sameAgentDiagnostics(capture))return;
    state.agentChatGPT=result;
    if(!result.pending){
      state.agentAuthPolling=false;state.agentModelsError=result.error||'';
      if(result.connected){await refreshAgentModels(true);const preferred=state.agentModels.find(item=>item.id==='chatgpt:gpt-6-luna');if(preferred)await saveAgentSettings({model:preferred.id});else state.agentModelsError='ChatGPT 已授权，但目录尚未提供 GPT-6-Luna；可刷新目录或手动选择模型。';}
      renderAssistant();return;
    }
  }catch(error){if(sameAgentDiagnostics(capture))state.agentModelsError=error.message;}
  if(sameAgentDiagnostics(capture)){renderAssistant();setTimeout(()=>pollAgentChatGPT(capture,attempt+1),4000);}
}
async function disconnectAgentChatGPT(){
  if(agentControlsBusy())return;
  const capture=agentDiagnosticsCapture();state.agentSettingsBusy=true;
  try{const result=await api('/api/chat/chatgpt/disconnect',{},capture.agentEpoch);if(!sameAgentDiagnostics(capture))return;state.agentChatGPT=result;state.agentModelsError=result.error||'';state.agentModels=state.agentModels.filter(item=>!item.id.startsWith('chatgpt:'));await refreshAgentStatus();}
  catch(error){if(sameAgentDiagnostics(capture))state.agentModelsError=error.message;}
  finally{if(sameAgentDiagnostics(capture)){state.agentSettingsBusy=false;renderAssistant();}}
}
async function refreshAgentModels(remote=false){
  if(!personalChatReady()||state.agentModelsBusy||agentControlsBusy())return;
  const capture=agentDiagnosticsCapture();state.agentModelsBusy=true;state.agentModelsError='';renderAssistant();
  try{const result=await api(remote?'/api/chat/models/refresh':'/api/chat/models',remote?{}:undefined,capture.agentEpoch);if(!sameAgentDiagnostics(capture))return;if(!Array.isArray(result.models))throw Error('invalid directory');state.agentModels=result.models.filter(item=>item&&typeof item.id==='string'&&item.id.length>0&&item.id.length<=200);state.agentModelsStatus='ready';state.agentChatGPT=result.chatgpt||null;if(state.agentChatGPT?.error)state.agentModelsError=state.agentChatGPT.error;else if(result.warnings?.length)state.agentModelsError=result.warnings.join(' ');if(state.agentChatGPT?.pending&&!state.agentAuthPolling)pollAgentChatGPT(capture);if(!state.agentModels.length)state.agentModelsError='当前缓存没有可用目录；可点击刷新模型目录。';}
  catch(error){if(sameAgentDiagnostics(capture)){state.agentModelsStatus=error.status===404||error.status===405?'unsupported':'error';state.agentModelsError=state.agentModelsStatus==='unsupported'?'当前服务暂不支持模型切换，可继续使用已配置模型。':'暂时无法读取模型目录，请稍后重试。';}}
  finally{if(sameAgentDiagnostics(capture)){state.agentModelsBusy=false;renderAssistant();}}
}
async function saveAgentSettings(options={}){
  if(!personalChatReady()||agentControlsBusy()||state.agentModelsBusy)return;
  const model=options.model||$('#agent-model')?.value,row=state.agentModels.find(item=>item.id===model);if(!row)return;
  const changed=model!==state.agentStatus?.model,cap=row.effort,levels=cap?.supported_levels||[];
  let thinking=typeof options.thinking==='boolean'?options.thinking:changed&&row.thinking_toggle===false?true:state.agentStatus?.thinking;
  if(typeof thinking!=='boolean')return;
  const body={model,thinking};
  if(cap){const value=options.reasoning_effort||(changed?cap.default_level:state.agentStatus?.reasoning_effort)||cap.default_level||'default';body.reasoning_effort=value==='none'&&thinking?(levels.find(level=>level!=='none')||'default'):value;}
  const capture=agentDiagnosticsCapture();state.agentSettingsBusy=true;state.agentModelsError='';renderAssistant();renderAgentHistory();
  try{const result=await api('/api/chat/settings',body,capture.agentEpoch);if(!sameAgentDiagnostics(capture))return;const status=result.chat||result;if(status.data_kind!==agentDataKind()||typeof status.model!=='string')throw Error('invalid settings');state.agentStatus=status;state.agentRetry=null;}
  catch(error){if(sameAgentDiagnostics(capture))state.agentModelsError=error.status===404||error.status===405?'当前服务暂不支持模型切换，原配置继续有效。':'模型设置未更新，请稍后重试。';}
  finally{if(sameAgentDiagnostics(capture)){state.agentSettingsBusy=false;renderAssistant();renderAgentHistory();}}
}
function agentCourseTestTool(name){return ['search_current_term_courses','generate_current_term_plan','update_current_term_plan','get_current_term_plan'].includes(name);}
function agentRunStatus(value,toolName){if(value==='not_ready'&&agentCourseTestTool(toolName))return '课程条件测试受阻，待核对';return ({running:'处理中',processing:'处理中',started:'处理中',retrying:'正在修正',completed:'已完成',ok:'已完成',success:'已完成',available:'已返回',partial:'部分结果',blocked:'受阻',accepted:'已通过',rejected:'未通过',failed:'未完成',error:'出错',not_ready:'资料未就绪',timeout:'超时',skipped:'未执行',cancelled:'已取消',aborted:'已中止',revoked:'已撤销'})[value]||'状态待确认';}
function agentUsageText(value){if(!value||typeof value!=='object')return '';const fields=[['prompt_tokens','输入'],['completion_tokens','输出'],['total_tokens','合计']].filter(([key])=>Number.isSafeInteger(value[key])&&value[key]>=0);return fields.map(([key,label])=>`${label} ${value[key]} tokens`).join(' · ');}
function agentEventData(event){return event&&typeof event==='object'?{...(event.fields&&typeof event.fields==='object'?event.fields:{}),...(event.data&&typeof event.data==='object'?event.data:{}),...(event.payload&&typeof event.payload==='object'?event.payload:{}),...event}:{};}
function agentToolName(name){return ({get_my_context:'本人已知范围',lookup_academic_terms:'教务字段说明',query_my_records:'本人教务记录',search_course_catalog:'本地课程参考',read_my_timetable:'读取本人课表',read_my_exams:'读取本人考试',search_my_curriculum:'培养资料检索',resolve_my_evidence:'来源回查',get_my_run_diagnostics:'上一轮运行诊断',check_recommendation_inputs:'推荐输入检查',prepare_recommendation_inputs:'准备推荐所需来源',search_current_term_courses:'本学期课程观察检索',generate_current_term_plan:'课程数据测试计算',update_current_term_plan:'测试方案复核与调整',get_current_term_plan:'定位当前方案选择',read_my_major_academic:'读取主修成绩',read_my_academic:'读取学期成绩',read_my_curriculum:'读取培养资料'})[name]||'受限资料工具';}
function agentEventScope(event){
  const values=[],operations={academic:'学期成绩',major_academic:'主修成绩',curriculum:'培养资料',timetable:'课表',exams:'考试'},operation=operations[event.operation];
  if(operation)values.push(operation);
  if(typeof event.target_term==='string'&&/^(?:ALL-HISTORY|(?:19|20|21)\d{2}-(?:19|20|21)\d{2}-(?:AW|SS|A|W|S|SU))$/.test(event.target_term))values.push(personalTermLabel(event.target_term));
  const modes={overview:'范围总览',all_records:'范围总览',all:'范围总览',filtered:'按条件筛选',course_keyword:'按课程关键词筛选',curriculum_keyword:'按培养关键词检索',course_search:'按课程筛选',course:'按课程筛选',exact_code:'精确课程码',lexical:'词法检索',empty:'范围总览',search:'按条件检索'};if(modes[event.query_mode])values.push(modes[event.query_mode]);
  const count=value=>Number.isSafeInteger(value)&&value>=0;
  if(count(event.captured_rows))values.push(`已读取 ${event.captured_rows}${count(event.reported_total)?' / 来源报告 '+event.reported_total:''} 条`);
  else if(count(event.record_count)&&!count(event.returned_records))values.push(`本次返回 ${event.record_count} 条来源行`);
  if(count(event.matched_records))values.push(`匹配 ${event.matched_records} 条`);
  else if(event.tool_name==='search_my_curriculum'&&count(event.match_count))values.push(`命中 ${event.match_count} 个片段`);
  if(count(event.returned_records))values.push(`返回 ${event.returned_records} 条`);
  if(event.has_more===true)values.push('还有未读取内容');else if(event.complete_within_scope===true)values.push('当前范围已完整读取');else if(event.complete_within_scope===false)values.push('覆盖尚不完整');
  return values.join(' · ');
}
function agentRunEventMarkup(raw,compact=false){
  const event=agentEventData(raw),kind=event.kind||event.type,names={'model.start':'模型请求','model.end':'模型返回','tool.start':'开始调用','tool.end':'调用返回','tool.rejected':'工具参数检查','answer.validated':'格式与引用检查','answer.rejected':'答复检查未通过','answer.repair':'修正答复格式与引用',error:'运行错误',run_started:'开始运行',run_completed:'运行完成',model_request:'模型请求',model_response:'模型返回',tool_start:'开始调用',tool_result:'调用返回'};
  const elapsed=agentDuration(event.elapsed_ms??event.duration_ms),tool=event.tool_name||event.tool,usage=agentUsageText(event.usage),failure=agentFailureText(event),scope=agentEventScope(event);
  const at=event.at??event.created_at,clock=at===undefined?'':schoolCaptureTime(at),step=Number.isSafeInteger(event.step)&&event.step>0?`第 ${event.step} 次模型调用`:Number.isSafeInteger(event.call)&&event.call>0?`第 ${event.call} 次模型调用`:'';
  const initiators={source_preparation:'来源准备',service_acquisition:'服务读取来源',service_preflight:'服务范围预检',model:'模型选择',model_selected:'模型选择',service:'服务准备',service_preparation:'来源准备',answer_repair:'答复修复'},actor=initiators[event.initiated_by]||(String(tool||'').startsWith('read_my_')?'来源准备':'');
  const countText=[['claim_count','条结论'],['evidence_count','条证据'],['citation_count','条引用']].filter(([key])=>Number.isSafeInteger(event[key])&&event[key]>=0).map(([key,label])=>`${event[key]} ${label}`).join(' · ');
  const meta=[actor,step,event.model||'',event.status?agentRunStatus(event.status,tool):'',elapsed,compact?'':clock].filter(Boolean).join(' · '),title=tool?`${agentToolName(tool)} · ${names[kind]||'调用记录'}`:names[kind]||'运行事件';
  return `<li class="agent-run-event${compact?' compact':''}"><div><strong>${esc(title)}</strong>${event.sequence?`<span class="field-help">#${esc(event.sequence)}</span>`:''}</div>${meta?`<p class="field-help">${esc(meta)}</p>`:''}${tool&&/^[a-z][a-z0-9_]{0,79}$/.test(tool)?`<code>${esc(tool)}</code>`:''}${scope?`<p>${esc(scope)}</p>`:''}${countText?`<p class="field-help">${esc(countText)}</p>`:''}${!compact&&usage?`<p class="field-help">${esc(usage)}</p>`:''}${failure?`<p class="agent-run-error">${esc(failure)}</p>`:''}</li>`;
}
function agentDiagnosticMarkup(value){
  if(!value||typeof value!=='object')return '<p class="field-help">本轮没有取得可展示的运行诊断。</p>';
  const events=Array.isArray(value.events)?value.events:[],id=agentShortId(value.run_id);
  return `<p class="field-help">${esc(agentRunStatus(value.status))}${value.model?' · '+esc(value.model):''} · ${value.thinking===true?'已开启深度思考':value.thinking===false?'普通模式':'模式待确认'}${id?' · 运行 '+esc(id):''}</p><p class="field-help">这是受控服务与工具调用记录；本轮没有通用终端脚本执行能力。</p><ol class="agent-run-timeline">${events.map(event=>agentRunEventMarkup(event)).join('')}</ol>`;
}
function captureAgentScroll(){
  const element=$('#agent-dialogue-scroll');if(!state.agentScroll.initialized||!element||!Number.isFinite(element.scrollTop))return;
  const scroll=state.agentScroll;scroll.top=element.scrollTop;scroll.nearBottom=element.scrollHeight-element.clientHeight-element.scrollTop<=64;
  if(typeof element.getBoundingClientRect==='function'&&typeof element.querySelectorAll==='function'){
    const viewport=element.getBoundingClientRect(),anchor=[...element.querySelectorAll('[data-scroll-anchor]')].find(item=>{const box=item.getBoundingClientRect();return box.bottom>viewport.top&&box.top<viewport.bottom;});
    scroll.anchor=anchor?{key:anchor.dataset.scrollAnchor,offset:anchor.getBoundingClientRect().top-viewport.top}:null;
  }
}
function agentScrollVersion(){return JSON.stringify(state.agentTurns.map(turn=>[turn.turn_id,turn.pending,turn.reply?.length,turn.error,turn.tool_results?.length,turn.citations?.length,turn.progress?.next_sequence,turn.progress?.active_reasoning?.text?.length,turn.progress_error,turn.run_id?state.agentRunDetails[turn.run_id]?.events?.length:0]));}
function updateAgentScroll(){captureAgentScroll();if(state.agentScroll.nearBottom)state.agentScroll.newMessages=false;}
function restoreAgentScroll(){
  const element=$('#agent-dialogue-scroll');if(!element)return;const scroll=state.agentScroll,version=agentScrollVersion(),changed=scroll.version!==version;
  const follow=scroll.forceLatest||!scroll.initialized||changed&&scroll.nearBottom;
  const last=state.agentTurns.at(-1),lastKey=last?.request_id||last?.run_id,finished=lastKey&&scroll.pendingKey===lastKey&&!last.pending;
  const anchors=typeof element.querySelectorAll==='function'?[...element.querySelectorAll('[data-scroll-anchor]')]:[];
  if(follow){
    const revealAnswer=finished||!last?.pending&&(scroll.forceLatest||!scroll.initialized);
    const answer=revealAnswer&&anchors.find(item=>item.dataset.answerKey===lastKey);
    if(answer&&typeof answer.getBoundingClientRect==='function'&&typeof element.getBoundingClientRect==='function')element.scrollTop=Math.max(0,element.scrollTop+answer.getBoundingClientRect().top-element.getBoundingClientRect().top-8);
    else element.scrollTop=element.scrollHeight||0;
    scroll.newMessages=false;
  }else{
    const anchor=scroll.anchor&&anchors.find(item=>item.dataset.scrollAnchor===scroll.anchor.key);
    element.scrollTop=anchor&&typeof element.getBoundingClientRect==='function'?element.scrollTop+anchor.getBoundingClientRect().top-element.getBoundingClientRect().top-scroll.anchor.offset:scroll.top;
    if(changed)scroll.newMessages=true;
  }
  scroll.pendingKey=last?.pending?lastKey:null;
  scroll.initialized=true;scroll.forceLatest=false;scroll.version=version;element.addEventListener('scroll',updateAgentScroll);element.addEventListener('keydown',event=>{if(event.target!==element||!['PageUp','PageDown','End'].includes(event.key))return;event.preventDefault();scrollAgentDialogue(event.key==='End'?'latest':event.key==='PageUp'?-1:1);});updateAgentScroll();
}
function scrollAgentDialogue(direction){const element=$('#agent-dialogue-scroll');if(!element)return;const maximum=Math.max(0,(element.scrollHeight||0)-(element.clientHeight||0));element.scrollTop=direction==='latest'?maximum:Math.min(maximum,Math.max(0,(element.scrollTop||0)+direction*(element.clientHeight||0)*0.85));if(direction==='latest')state.agentScroll.newMessages=false;updateAgentScroll();}

function renderAssistant() {
  const panel=$('#panel-assistant');if(!state.boot){panel.innerHTML='';return;}
  captureAgentScroll();
  const status=state.agentStatus,school=isAcademicIdentity(),configured=status?.configured===true&&(!school||personalChatReady()&&status.available!==false&&!state.schoolReauth);
  const busy=state.agentBusy||state.agentSettingsBusy||state.agentActionBusy||state.agentHistoryLoadBusy;
  const personal=school&&personalChatReady();
  const consent=configured&&status.remote&&!school?`<label class="checkbox-line agent-consent"><input type="checkbox" id="agent-remote-consent" ${state.agentConsent?'checked':''} ${busy?'disabled':''}><span>允许向 ${esc(status.provider||'远程模型')} 发送合成测试需求${status.allow_reference_text?'和参考片段':''}</span></label>`:configured?'':`<span class="badge warning">${school&&!personalChatReady()?'本人资料对话待接入':school&&status?.configured?'本人资料暂不可用':'模型未配置'}</span>`;
  const modelError=personal&&state.agentModelsError?`<p class="agent-settings-error" role="status">${esc(state.agentModelsError)}</p>`:'';
  panel.innerHTML=`<div class="assistant-workspace${!state.agentTurns.length&&!state.agentHistoryPending?' is-empty':''}"><section id="agent-dialogue-scroll" class="agent-dialogue" tabindex="0" aria-label="对话历史" aria-live="polite" aria-busy="${state.agentBusy}">${renderAgentTurns()}${state.agentHistoryPending?notice('这段对话仍在处理。')+'<button class="button small" data-action="load-agent-conversation" data-conversation-id="'+esc(state.agentConversationId)+'">重新加载</button>':''}</section><section class="dialogue-composer"><form id="agent-draft-form" autocomplete="off"><label for="agent-draft" class="sr-only">${school?'向浙大教务助手提问':'选课需求'}</label><textarea id="agent-draft" rows="2" maxlength="2000" placeholder="${school?'询问教务信息，或提出选课与排课需求…':'告诉我你的修读目标和时间偏好…'}" ${busy?'disabled':''}></textarea>${modelError}<div class="agent-compose-footer"><div class="agent-compose-context">${personal?renderAgentThinking():''}${consent}</div><div class="agent-compose-actions">${personal?renderAgentModelControls():''}<button id="agent-send" type="submit" class="button primary" ${agentCanSend()?'':'disabled'} aria-disabled="${!agentCanSend()}">${state.agentBusy?'发送中…':'发送'}</button></div></div></form></section></div>`;
  $('#agent-draft').value=state.agentDraft;
  $('#agent-draft').setAttribute('title','Enter 发送，Shift+Enter 换行');
  $('#agent-draft').setAttribute('aria-description','Enter 发送，Shift+Enter 换行；输入法选字时不会发送。');
  $('#agent-draft').addEventListener('input',event=>{state.agentDraft=event.target.value;queueAgentDraftSave();updateAgentSend();});
  let composing=false;
  $('#agent-draft').addEventListener('compositionstart',()=>{composing=true;state.agentDraftComposing=true;});
  $('#agent-draft').addEventListener('compositionend',()=>{composing=false;state.agentDraftComposing=false;});
  $('#agent-draft').addEventListener('keydown',event=>{
    // Enter confirms an IME candidate while composing; it must never send it.
    if(event.key!=='Enter'||event.shiftKey||composing||event.isComposing||event.keyCode===229)return;
    event.preventDefault();
    if(!event.repeat&&agentCanSend())void sendAgentTurn();
  });
  $('#agent-remote-consent')?.addEventListener('change',event=>{state.agentConsent=event.target.checked;updateAgentSend();});
  $('#agent-draft-form').addEventListener('submit',event=>{event.preventDefault();sendAgentTurn();});
  $('#agent-chatgpt-connect')?.addEventListener('click',connectAgentChatGPT);
  $('#agent-chatgpt-disconnect')?.addEventListener('click',disconnectAgentChatGPT);
  $('#agent-model-trigger')?.addEventListener('click',event=>{if(agentControlsBusy()||state.agentModelsBusy)event.preventDefault();});
  $('#agent-model-menu')?.addEventListener('keydown',event=>{if(event.key==='Escape'){$('#agent-model-menu').open=false;$('#agent-model-trigger')?.focus();event.stopPropagation();}});
  $('#agent-model')?.addEventListener('change',event=>{if(event.target.value==='@refresh')refreshAgentModels(true);else if(event.target.value==='@connect')connectAgentChatGPT();else if(event.target.value==='@disconnect')disconnectAgentChatGPT();else saveAgentSettings();});
  $('#agent-reasoning-effort')?.addEventListener('change',event=>saveAgentSettings({reasoning_effort:event.target.value}));
  $('#agent-thinking')?.addEventListener('click',()=>saveAgentSettings({thinking:state.agentStatus?.thinking!==true}));
  restoreAgentScroll();
}
function historyDay(value){
  if(value==null||value==='')return null;
  const date=new Date(typeof value==='number'?value*1000:value);
  return Number.isFinite(date.getTime())?date.toLocaleDateString('en-CA',{timeZone:'Asia/Shanghai'}):null;
}
function historyGroup(value){
  const day=historyDay(value);if(!day)return '时间未知';
  const today=historyDay(Date.now()/1000),age=Math.round((Date.parse(today)-Date.parse(day))/86400000);
  return age===0?'今天':age===1?'昨天':age>1&&age<7?'近7天':'更早';
}
function renderAgentHistory(){
  const disabled=state.agentBusy||state.agentSettingsBusy||state.agentActionBusy||state.agentHistoryLoadBusy;
  const query=state.agentHistorySearch.trim(),serverMatched=state.agentHistoryQuery===query,groups=new Map();
  for(const item of state.agentHistory){if(query&&!serverMatched&&!String(item.title||'新对话').toLocaleLowerCase().includes(query.toLocaleLowerCase()))continue;const key=historyGroup(item.updated_at);if(!groups.has(key))groups.set(key,[]);groups.get(key).push(item);}
  $('#conversation-list').innerHTML=[...groups].map(([group,items])=>`<section class="history-group"><h3>${group}</h3>${items.map(item=>`<button class="conversation-history-item ${item.conversation_id===state.agentConversationId?'active':''}" data-action="load-agent-conversation" data-conversation-id="${esc(item.conversation_id)}" title="${esc(item.title)}" aria-current="${item.conversation_id===state.agentConversationId?'true':'false'}" ${disabled?'disabled':''}><span class="conversation-title">${esc(item.title||'新对话')}</span></button>`).join('')}</section>`).join('')+(!groups.size?`<p class="history-empty">${state.agentHistoryLoading?'正在读取…':query?(serverMatched?'没有匹配标题':'已加载对话中没有匹配标题'):'暂无历史对话'}</p>`:'')+(state.agentHistoryError?`<p class="history-error">${esc(state.agentHistoryError)}</p><button class="button small" data-action="refresh-agent-history">重试</button>`:'')+(serverMatched&&state.agentHistoryHasMore?`<p class="history-empty">${query?'仅显示最近100段匹配对话':'仅显示最近100段对话'}</p>`:'');
  $('#new-agent-conversation').disabled=disabled;
}
const agentStarterQuestions=['帮我看看我的主修成绩概况。','请解释培养方案中的专业必修要求。','我想规划课程组合，请先帮我明确需要提供哪些课程范围和时间偏好。'];
function fillAgentDraft(text){
  if(!text||state.agentBusy||state.agentHistoryLoadBusy)return;
  state.agentDraft=state.agentDraft.trim()&&state.agentDraft!==text?state.agentDraft+'\n'+text:text;
  state.agentDraft=state.agentDraft.slice(0,2000);queueAgentDraftSave();renderAssistant();$('#agent-draft')?.focus();
}
function agentMessageButton(action,label,icon,index,changes=false){
  const paths={copy:'M8 8h12v13H8zM16 8V3H3v13h5',retry:'M20 7v5h-5M20 12a8 8 0 1 0-2 6',share:'M14 4l7 6-7 6v-4C8 12 5 15 3 20c0-9 4-12 11-12z'};
  const disabled=changes&&(state.agentBusy||state.agentHistoryPending||state.agentHistoryLoadBusy||state.agentSettingsBusy||state.agentActionBusy);
  return `<button type="button" class="button message-action" data-action="${action}" data-turn-index="${index}" aria-label="${label}" ${disabled?'disabled':''}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[icon]}"/></svg><span class="message-tooltip" aria-hidden="true">${label}</span></button>`;
}
async function copyMessageText(text,feedback){
  try{await navigator.clipboard.writeText(text);feedback.textContent='已复制';}
  catch{feedback.textContent='复制未完成，请选中文字后复制。';}
}
function openMessageDialog(button,mode){
  if(!['share','regenerate'].includes(mode))return;
  const index=Number(button.dataset.turnIndex),turn=state.agentTurns[index];
  if(!turn||turn.pending)return;
  if(mode!=='share'&&(state.agentBusy||state.agentHistoryPending||state.agentHistoryLoadBusy||state.agentSettingsBusy||state.agentActionBusy))return;
  if(mode==='regenerate'&&index!==state.agentTurns.length-1)return;
  document.querySelector('.message-dialog')?.remove?.();
  const epoch=state.agentEpoch,dialog=document.createElement('dialog');dialog.className='message-dialog';
  const retry=mode==='regenerate'&&Boolean(state.agentRetry)&&state.agentRetry.request_id===turn.request_id;
  const title=retry?'重试本轮请求':{regenerate:'重新回答',share:'分享本轮问答'}[mode];
  const description=retry?'将沿用原请求编号重试。已完成时返回原结果，仍在处理时提示等待。':{regenerate:'将按当前对话上下文再次发送这个问题，可能重新读取相关资料。原回答保留，新回答追加在下方。',share:'仅包含下方可见的本轮问答。可删去不想分享的内容，再复制或下载。不会创建公开链接。'}[mode];
  const answer=button.closest('.agent-turn')?.querySelector('.agent-answer')?.innerText||'';
  dialog.setAttribute('aria-labelledby','message-dialog-title');
  dialog.innerHTML=`<header><h2 id="message-dialog-title">${title}</h2><button type="button" class="button dialog-close" aria-label="关闭">×</button></header><p>${description}</p><label class="sr-only" for="message-dialog-text">${mode==='share'?'分享内容':'问题内容'}</label><textarea id="message-dialog-text" ${mode==='regenerate'?'readonly':''}></textarea><footer><span role="status" class="copy-status"></span><button type="button" class="button dialog-cancel">取消</button>${mode==='share'?'<button type="button" class="button dialog-download">下载文本</button>':''}<button type="button" class="button primary dialog-confirm">${mode==='share'?'复制分享内容':retry?'确认重试':'确认重新回答'}</button></footer>`;
  const text=dialog.querySelector('textarea');text.value=mode==='share'?`问题：${turn.message}\n\n回答：\n${answer}`:turn.message;
  let closed=false;
  const close=()=>{closed=true;dialog.close();};dialog.querySelector('.dialog-close').onclick=close;dialog.querySelector('.dialog-cancel').onclick=close;
  dialog.addEventListener('close',()=>{closed=true;dialog.remove();if(button.isConnected)button.focus();});
  dialog.querySelector('.dialog-download')?.addEventListener('click',()=>{
    const url=URL.createObjectURL(new Blob([text.value],{type:'text/plain;charset=utf-8'}));const link=document.createElement('a');link.href=url;link.download='浙大教务助手-本轮问答.txt';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
  const confirm=dialog.querySelector('.dialog-confirm');
  confirm.onclick=async()=>{
    if(confirm.disabled)return;
    if(epoch!==state.agentEpoch){close();return;}
    if(mode==='share'){await copyMessageText(text.value,dialog.querySelector('[role="status"]'));return;}
    if(!text.value.trim())return;
    const draft=state.agentDraft;
    if(draft&&draft.trim()!==turn.message&&agentDraftSupported()){
      confirm.disabled=true;
      await flushAgentDraftBeforeSend();
      confirm.disabled=false;
      if(closed||epoch!==state.agentEpoch)return;
      if(state.agentDraft!==draft||state.agentDraftSync.dirty||state.agentDraftSync.error){
        dialog.querySelector('[role="status"]').textContent='当前输入尚未暂存，重新回答尚未发起，请稍后重试。';return;
      }
    }
    state.agentDraft=turn.message;
    if(!agentCanSend()){state.agentDraft=draft;dialog.querySelector('[role="status"]').textContent='当前暂不能发送，请检查模型或登录状态。';return;}
    close();await sendAgentTurn();
    if(epoch===state.agentEpoch&&draft&&(!retry||draft.trim()!==turn.message)){state.agentDraft=draft;queueAgentDraftSave();renderAssistant();}
  };
  document.body.append(dialog);dialog.showModal();
}
async function copyAgentAnswer(button){
  const text=button.closest('.agent-message')?.querySelector('.agent-answer')?.innerText;
  const feedback=button.parentElement?.querySelector('.copy-status');if(!text||!feedback)return;
  try{await navigator.clipboard.writeText(text);feedback.textContent='已复制';}
  catch{feedback.textContent='复制未完成，请选中文字后复制。';}
}
function resetAgentHistorySearch(){
  if(state.agentHistoryTimer!=null)clearTimeout(state.agentHistoryTimer);
  state.agentHistoryTimer=null;state.agentHistoryRequest+=1;state.agentHistoryQuery='';
}
function searchAgentHistory(event){
  state.agentHistorySearch=event.target.value.slice(0,100);state.agentHistoryRequest+=1;
  if(state.agentHistoryTimer!=null)clearTimeout(state.agentHistoryTimer);
  state.agentHistoryTimer=null;state.agentHistoryLoading=false;state.agentHistoryError='';renderAgentHistory();
  if(event.isComposing)return;
  state.agentHistoryTimer=setTimeout(()=>{state.agentHistoryTimer=null;return refreshAgentHistory();},250);
}
async function refreshAgentHistory(){
  if(!state.auth?.authenticated||isAcademicIdentity()&&!personalChatReady()){
    state.agentHistoryLoading=false;renderAgentHistory();return;
  }
  if(state.agentHistoryTimer!=null)clearTimeout(state.agentHistoryTimer);state.agentHistoryTimer=null;
  const epoch=state.agentEpoch,query=state.agentHistorySearch.trim(),requestId=++state.agentHistoryRequest;
  const current=()=>epoch===state.agentEpoch&&requestId===state.agentHistoryRequest&&query===state.agentHistorySearch.trim();
  state.agentHistoryLoading=true;state.agentHistoryError='';renderAgentHistory();
  try{
    const result=await api('/api/chat/conversations?limit=100'+(query?'&query='+encodeURIComponent(query):''),undefined,epoch);
    if(!current())return;
    if(!Array.isArray(result.conversations)||result.data_kind!==agentDataKind())throw new Error('历史列表无法读取');
    state.agentHistory=result.conversations;state.agentHistoryQuery=query;state.agentHistoryHasMore=result.has_more===true;
  }catch(error){if(current())state.agentHistoryError=error.message;}
  finally{if(current()){state.agentHistoryLoading=false;renderAgentHistory();}}
}
function stopAgentHistoryPoll(session=state.agentHistoryPoll){
  if(!session)return;
  if(session.timer!==undefined&&typeof clearTimeout==='function')clearTimeout(session.timer);
  if(state.agentHistoryPoll===session)state.agentHistoryPoll=null;
}
function startAgentHistoryPoll(){
  stopAgentHistoryPoll();
  if(!state.agentHistoryPending||!state.agentConversationId||!state.auth?.authenticated)return;
  const session={identifier:state.agentConversationId,epoch:state.agentEpoch,
    owner:identityKey(state.auth.identity),schoolEpoch:state.schoolEpoch,timer:undefined};
  state.agentHistoryPoll=session;
  const current=()=>state.agentHistoryPoll===session&&state.auth?.authenticated===true&&
    session.identifier===state.agentConversationId&&session.epoch===state.agentEpoch&&
    session.owner===identityKey(state.auth.identity)&&session.schoolEpoch===state.schoolEpoch;
  const poll=async()=>{
    if(!current()){stopAgentHistoryPoll(session);return;}
    const keepPolling=await loadAgentConversation(session.identifier,{background:true});
    if(current()&&keepPolling&&state.agentHistoryPending)session.timer=setTimeout(poll,3000);
    else stopAgentHistoryPoll(session);
  };
  session.timer=setTimeout(poll,3000);
}
async function loadAgentConversation(identifier,{fromDraft=false,background=false}={}){
  if(isAcademicIdentity()&&!personalChatReady())return;
  if(state.agentBusy||state.agentSettingsBusy||state.agentActionBusy||state.agentHistoryLoadBusy||background&&state.agentDraftComposing)return background;
  let epoch=state.agentEpoch,applied=false,selection=null;
  const owner=identityKey(state.auth?.identity),schoolEpoch=state.schoolEpoch;
  const current=()=>epoch===state.agentEpoch&&owner===identityKey(state.auth?.identity)&&schoolEpoch===state.schoolEpoch;
  state.agentHistoryLoadBusy=true;
  if(!background){stopAgentHistoryPoll();renderAssistant();renderAgentHistory();clearMessage();}
  try{
    const result=await api('/api/chat/history',{conversation_id:identifier},epoch);
    if(!current())return;
    if(result.conversation_id!==identifier||!Number.isInteger(result.turn_id)||result.turn_id<0||!Array.isArray(result.turns)||result.data_kind!==agentDataKind())throw new Error('历史对话响应不完整');
    // Polls only observe completion. Pending reads leave the composer and its
    // IME session intact; a completed receipt never repeats model/tools/actions.
    if(background&&(result.in_progress===true||result.action_pending===true||state.agentDraftComposing))return true;
    const draft=$('#agent-draft');
    if(background&&document.activeElement===draft)selection=[draft.selectionStart,draft.selectionEnd,draft.selectionDirection];
    // Keep the current conversation and unsent follow-up until the replacement
    // is valid. A failed history read must not erase the user's working state.
    const working=identifier===state.agentConversationId?{agentDraft:state.agentDraft,agentConsent:state.agentConsent,agentScroll:state.agentScroll}:null;
    clearAgentConversation();epoch=state.agentEpoch;state.agentHistoryLoadBusy=true;
    state.agentConversationId=identifier;state.agentTurnId=result.turn_id;state.agentTurns=result.turns;state.agentScroll.forceLatest=true;
    if(working)Object.assign(state,working);
    state.agentHistoryPending=result.in_progress===true||result.action_pending===true;state.agentOptionReceipts=result.option_receipts||{};
    for(const turn of result.turns){if(turn.status==='completed'&&turn.reply_origin==='verified_explicit_action'&&turn.saved_action?.option_id)state.agentOptionReceipts[turn.saved_action.option_id]=turn.saved_action;}
    // Loading history displays receipts. It never repeats a save or download.
    if(!fromDraft&&!background)queueAgentDraftSave(true);
    if(!background)switchTab('assistant');
    state.agentHistory=state.agentHistory.map(row=>row.conversation_id===identifier?{...row,turn_id:result.turn_id,in_progress:state.agentHistoryPending}:row);
    applied=true;
  }catch(error){if(current())message(error.message,true);}
  finally{
    if(current()){
      state.agentHistoryLoadBusy=false;
      if(!background){startAgentHistoryPoll();}
      if(!background||applied){
        renderAssistant();renderAgentHistory();
        if(selection){const draft=$('#agent-draft');draft.focus({preventScroll:true});draft.setSelectionRange(...selection);}
      }
      if(background&&applied)await refreshAgentHistory();
    }
  }
}

function updateAgentSend(){const button=$('#agent-send');if(button){button.disabled=!agentCanSend();button.setAttribute('aria-disabled',String(!agentCanSend()));}}
async function refreshAgentStatus(){
  const epoch=state.agentEpoch,owner=identityKey(state.auth?.identity),schoolEpoch=state.schoolEpoch;
  try{const status=await api('/api/chat/status',undefined,epoch);if(epoch!==state.agentEpoch||owner!==identityKey(state.auth?.identity)||schoolEpoch!==state.schoolEpoch)return;
    if(agentPolicyKey(state.agentStatus)!==agentPolicyKey(status)||status.available===false||status.configured!==true)state.agentRetry=null;
    if(JSON.stringify([state.agentStatus?.provider,state.agentStatus?.model,state.agentStatus?.remote,state.agentStatus?.allow_reference_text])!==JSON.stringify([status.provider,status.model,status.remote,status.allow_reference_text]))state.agentConsent=false;
    state.agentStatus=status;if(isAcademicIdentity()){renderAuth();if(state.tab==='assistant')renderAssistant();}
  }catch(error){if(epoch===state.agentEpoch&&owner===identityKey(state.auth?.identity)){state.agentStatus={configured:false,reason:error.message};if(isAcademicIdentity())renderAssistant();}}
}
async function sendAgentTurn(){
  if(!agentCanSend())return;
  const epoch=state.agentEpoch;const turn={message:state.agentDraft.trim(),pending:true};
  const use=isAcademicIdentity()?{consent_personal:true,data_use_version:state.agentStatus.personal_data_use.version}:{consent_remote:state.agentConsent};
  const payload=state.agentRetry?.message===turn.message&&state.agentRetry.expected_turn===state.agentTurnId&&(!isAcademicIdentity()||state.agentRetry.data_use_version===use.data_use_version)?{...state.agentRetry,...use}:{conversation_id:state.agentConversationId,message:turn.message,request_id:crypto.randomUUID(),expected_turn:state.agentTurnId,...use};
  if(agentDraftSupported()&&state.agentDraftSync.enabled){state.agentBusy=true;renderAssistant();await flushAgentDraftBeforeSend();if(epoch!==state.agentEpoch||!state.auth?.authenticated)return;}
  suspendAgentDraftForSend();turn.request_id=payload.request_id;state.agentBusy=true;state.agentScroll.forceLatest=true;state.agentDraft='';
  if(state.agentTurns.at(-1)?.request_id===payload.request_id)state.agentTurns[state.agentTurns.length-1]=turn;
  else state.agentTurns.push(turn);
  renderAssistant();renderAgentHistory();
  let progressSession;const capture=agentDiagnosticsCapture();
  try{
    const response=api('/api/chat/turn',payload,epoch);if(isAcademicIdentity())progressSession=startAgentProgress(turn,payload.request_id);
    const result=await response;
    if(epoch!==state.agentEpoch||!state.auth?.authenticated)return;
    if(typeof result.conversation_id!=='string'||payload.conversation_id&&result.conversation_id!==payload.conversation_id||!Number.isInteger(result.turn_id)||result.turn_id<=state.agentTurnId||!Array.isArray(result.tool_results)||!Array.isArray(result.citations)||result.data_kind!==agentDataKind())throw new Error('对话响应契约不完整，请重新开始对话后重试。');
    state.agentConversationId=result.conversation_id;state.agentTurnId=result.turn_id;state.agentRetry=null;
    if(progressSession)stopAgentProgress(progressSession);Object.assign(turn,result,{message:turn.message,pending:false});
    if(isAcademicIdentity())await hydrateAgentProcess(turn,capture);
    await applyAgentTurnActions(turn,epoch);
  }catch(error){if(epoch!==state.agentEpoch)return;if(progressSession)stopAgentProgress(progressSession);Object.assign(turn,{pending:false,error:error.message});if(turn.progress)turn.progress={...turn.progress,status:'failed',active_reasoning:null};state.agentDraft=turn.message;state.agentRetry=payload;if(isAcademicIdentity())await hydrateAgentProcess(turn,capture);}
  finally{if(progressSession)stopAgentProgress(progressSession);if(epoch===state.agentEpoch){await refreshAgentDraftRevision();if(epoch!==state.agentEpoch)return;state.agentBusy=false;renderAssistant();await refreshAgentHistory();}}
}
async function applyAgentTurnActions(turn,epoch){
  if(isAcademicIdentity())return;
  if(epoch!==state.agentEpoch||turn.conversation_id!==state.agentConversationId||turn.turn_id!==state.agentTurnId||turn.status!=='completed'||turn.reply_origin!=='verified_explicit_action')return;
  if(turn.saved_action){
    const saved=turn.saved_action;
    if(!Number.isInteger(saved.saved_id)||saved.saved_id<=0||typeof saved.option_id!=='string'||!saved.constraints||!saved.versions)throw new Error('明确保存的执行回执不完整，请核对已保存列表。');
    state.agentOptionReceipts[saved.option_id]=saved;resetCurrentPlan();
    try{
      await refreshWorkspace(epoch);
      if(epoch!==state.agentEpoch||turn.conversation_id!==state.agentConversationId)return;
      state.lastConstraints=clone(saved.constraints);renderPlanning();renderAudit();renderSaved();
      message(`已保存为草案 #${saved.saved_id}`);
    }catch(error){if(epoch===state.agentEpoch)message(`合成草案 #${saved.saved_id} 已保存，工作区刷新失败：${error.message}`,true);}
  }
  if(turn.download&&epoch===state.agentEpoch&&turn.conversation_id===state.agentConversationId){
    const download=turn.download;const media=String(download.content_type||'').split(';')[0];
    const formats={'application/json':'json','text/csv':'csv','text/calendar':'ics'};
    if(typeof download.data_base64!=='string'||typeof download.filename!=='string'||!download.filename||!Number.isInteger(download.bytes)||download.bytes<0||!formats[media])throw new Error('明确导出的下载回执不完整，文件未下载。');
    const decoded=atob(download.data_base64);const bytes=Uint8Array.from(decoded,character=>character.charCodeAt(0));
    if(bytes.length!==download.bytes)throw new Error('导出文件长度与执行回执不一致，文件未下载。');
    const blob=new Blob([bytes],{type:download.content_type});
    downloadResponse({headers:{get:()=>`attachment; filename="${download.filename.replace(/["\\/\0]/g,'_')}"`}},blob,formats[media]);
    message(`已下载 ${download.filename}`);
  }
}
function rejectAgentOption(error,reference){
  if(error.status===409||['workspace_changed','stale_option','preview_stale','stale_recommendation'].includes(error.code)){
    reference.plan.preview_valid=false;reference.plan.stale_reason=error.message;
    delete state.agentOptionReceipts[reference.plan.option_id];
  }
  message(error.message,true);
}
async function saveAgentOption(optionId){
  if(isAcademicIdentity())return;
  const reference=findAgentOption(optionId);if(!reference)return;
  const option=agentOptionState(reference.plan,reference.result);
  if(!option.enabled){message(option.reason,true);return;}
  if(option.receipt){message(`已保存为草案 #${option.receipt.saved_id}`);return;}
  const epoch=state.agentEpoch;const key=JSON.stringify([state.agentConversationId,state.agentTurnId,optionId]);
  const actionId=state.agentActionRetries[key]||crypto.randomUUID();state.agentActionRetries[key]=actionId;
  const payload={conversation_id:state.agentConversationId,expected_turn:state.agentTurnId,run_id:reference.runId,option_id:optionId,action_id:actionId};
  state.agentActionBusy=true;renderAssistant();renderAgentHistory();
  try{
    const saved=await api('/api/chat/save-option',payload,epoch);
    if(epoch!==state.agentEpoch||!state.auth?.authenticated)return;
    if(!Number.isInteger(saved.saved_id)||saved.saved_id<=0||!saved.constraints||!saved.versions||saved.action_id!==actionId||saved.conversation_id!==state.agentConversationId||saved.turn_id!==state.agentTurnId)throw new Error('保存回执不完整，请核对已保存列表后重试。');
    state.agentOptionReceipts[optionId]=saved;
    resetCurrentPlan();await refreshWorkspace(epoch);
    if(epoch!==state.agentEpoch)return;
    state.lastConstraints=clone(saved.constraints);renderPlanning();renderAudit();renderSaved();
    message(`已保存为草案 #${saved.saved_id}`);
  }catch(error){if(epoch===state.agentEpoch)rejectAgentOption(error,reference);}
  finally{if(epoch===state.agentEpoch){state.agentActionBusy=false;renderAssistant();renderAgentHistory();}}
}
function downloadResponse(response,blob,format){
  const filename=response.headers.get('Content-Disposition')?.match(/filename="?([^";]+)"?/)?.[1]||`synthetic-course-plan.${format}`;
  const url=URL.createObjectURL(blob);const anchor=document.createElement('a');anchor.href=url;anchor.download=filename;document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
async function exportAgentOption(optionId,format){
  if(isAcademicIdentity())return;
  const reference=findAgentOption(optionId);if(!reference||!['json','csv','ics'].includes(format))return;
  const option=agentOptionState(reference.plan,reference.result);
  if(!option.enabled){message(option.reason,true);return;}
  const epoch=state.agentEpoch;
  const payload={conversation_id:state.agentConversationId,expected_turn:state.agentTurnId,run_id:reference.runId,option_id:optionId,format};
  state.agentActionBusy=true;renderAssistant();renderAgentHistory();
  try{
    const response=await request('/api/chat/export-option',payload,epoch);const blob=await response.blob();
    if(epoch!==state.agentEpoch||!state.auth?.authenticated)return;
    downloadResponse(response,blob,format);message(`已导出 ${format.toUpperCase()}${reference.plan.validation?.result==='unknown'?'（待确认草案）':''}`);
  }catch(error){if(epoch===state.agentEpoch)rejectAgentOption(error,reference);}
  finally{if(epoch===state.agentEpoch){state.agentActionBusy=false;renderAssistant();renderAgentHistory();}}
}
function chooseAgentExample(index){const example=agentExamples[index];if(!example)return;state.agentDraft=example.draft;queueAgentDraftSave();$('#agent-draft').value=state.agentDraft;$('#agent-draft').focus();updateAgentSend();}
function clearAgentDraft(){state.agentDraft='';queueAgentDraftSave(true);$('#agent-draft').value='';$('#agent-draft').focus();updateAgentSend();}
async function previewCurrentConditions() {
  if(state.busy||state.agentBusy||state.agentActionBusy||state.previewBusy||!state.boot||state.mode!=='demo')return;
  state.previewBusy=true;renderAssistant();
  try{
    resetCurrentPlan();await refreshWorkspace();
    await generate(clone(state.constraints));
    if(state.generated){switchTab('planning');message('已用当前合成条件计算课表预览；自然语言草稿未发送，也未参与计算。');}
  }catch(error){message(error.message,true);}finally{state.previewBusy=false;renderAssistant();}
}
function renderPlanning() {
  const panel=$('#panel-planning');
  if(!state.boot)return;
  if(isAcademicIdentity()){panel.innerHTML=notice('真实课表规划待培养要求与当期教学班数据核验。');return;}
  if(state.mode==='source') {panel.innerHTML=pageHeading('选课规划','先确认适用资料，再规划当期教学班。')+dataNotice()+`<div class="empty-state"><div class="empty-glyph" aria-hidden="true">▦</div><h2>真实规划待本人数据源接入</h2><p>本应用原交接包的真实教学班模板为 0 条，不能据此推断学校没有开课。真实规划还需要本人适用方案、学业及认定记录、目标学期和完整教学班快照。</p><button class="button primary" data-action="switch-demo">体验合成演示</button><button class="button" data-action="show-sources">查看原始资料</button></div>`;return;}
  const constraints=state.constraints; const locked=new Set(constraints.locked_section_ids || []); const required=new Set(constraints.requested_course_ids || []); const preloaded=new Set(constraints.preloaded_section_ids || []);
  const frozen=Boolean(state.loadedSaved);
  const availableCourses=[...new Map(sections().map(section=>[section.course_id,section])).values()];
  panel.innerHTML=pageHeading(frozen?'查看冻结方案':'规划本学期，比较每一种选择',frozen?'课表、条件与校验结果取自保存时的冻结输入。':'调整修读目标和时间条件，查看确定性计算生成的方案。')+dataNotice()+(frozen?notice(state.loadedSaved.is_stale?'保存输入与当前数据或偏好已不同。此处保留历史计算；点击按当前数据重新校验后再调整。':'此处是冻结保存版本。继续调整前仍须明确按当前数据重新校验。')+`<div class="button-group frozen-actions"><button class="button primary" data-action="revalidate-saved">按当前数据重新校验</button><button class="button" data-action="use-current">返回当前工作区</button></div>`:'')+`<div class="planning-layout"><aside class="conditions-panel"><div class="panel-top"><h2>我的规划条件</h2><p>${esc(planDataset().student?.target_term || '目标学期未确认')} · 版本 ${esc(constraints.revision || 1)}</p></div><form id="conditions-form" class="conditions-form"><fieldset class="conditions-fields" ${frozen?'disabled':''}><div class="field"><label for="friday-free">周五空闲</label><select id="friday-free" name="friday_free"><option value="off" ${constraints.friday_free==='off'?'selected':''}>无此偏好</option><option value="soft" ${constraints.friday_free==='soft'?'selected':''}>尽量空闲 · 可权衡</option><option value="hard" ${constraints.friday_free==='hard'?'selected':''}>必须空闲 · 不可放宽</option></select><p class="field-help">修改偏好后会保留锁定班、预置项与固定安排，并重新校验。</p></div><div class="field"><span class="field-label">计划修读课程</span><div class="course-choices">${availableCourses.map(course=>`<label class="checkbox-line"><input type="checkbox" name="requested_course_ids" value="${esc(course.course_id)}" ${required.has(course.course_id)?'checked':''} ${preloaded.has(course.section_id)?'disabled':''}><span>${esc(course.course_name)}<small>${esc(course.credits)} 学分 · ${esc(course.course_id)}</small></span></label>`).join('')}</div></div><div class="divider"></div><div class="field"><span class="field-label">锁定教学班</span><p class="field-help">明确勾选表示必须保留。只查看方案不会锁定教学班。</p>${sections().map(section=>`<label class="checkbox-line locked-section"><input type="checkbox" name="locked_section_ids" value="${esc(section.section_id)}" ${locked.has(section.section_id)||preloaded.has(section.section_id)?'checked':''} ${preloaded.has(section.section_id)||locked.has(section.section_id)?'disabled':''}><span>${esc(section.section_id)}${locked.has(section.section_id)&&!preloaded.has(section.section_id)?'<span class="lock-tag">继承锁定</span>':''}${preloaded.has(section.section_id)?'<span class="lock-tag">预置保留</span>':''}<small>${esc(section.course_name)}<br>${(section.meetings||[]).map(meeting=>esc(meetingText(meeting))).join('<br>')}</small></span></label>`).join('')}</div><div class="field"><span class="field-label">固定安排</span>${(constraints.fixed_meetings||[]).length?(constraints.fixed_meetings||[]).map(meeting=>`<div class="fixed-item">${esc(meeting.title||'固定安排')}<br>${esc(meetingText(meeting))}</div>`).join(''):'<p class="field-help">当前无额外固定安排。预置班按当前条件保留。</p>'}</div><button type="submit" class="button primary full" ${state.busy?'disabled':''}>${frozen?'冻结条件 · 只读':state.busy?'正在生成…':'生成并校验方案'}</button><p class="field-help">基于当前演示范围搜索，最多比较 3 个方案。</p><details class="disclosure-block"><summary>编辑完整条件 JSON</summary><p>保留现有锁定、预置和固定安排；可添加新条件。额外锁定冲突会被明确报告。</p><textarea id="constraints-editor" aria-label="完整规划条件 JSON" rows="9"></textarea><button type="button" class="button small" data-action="apply-constraints">应用条件并重新生成</button></details></fieldset></form></aside><div class="plan-content"><div class="toolbar"><div><h2>候选方案</h2><p>每个方案包含一个具体教学班组合</p></div><span class="badge primary">${esc(planDataset().metadata?.version || 'DEMO')}</span></div><div id="plans-results"></div>${snapshotLine()}</div></div>`;
  $('#constraints-editor').value=JSON.stringify(constraints,null,2);
  $('#conditions-form').addEventListener('submit',event=>{event.preventDefault();generateFromForm();});
  $('#conditions-form').addEventListener('change',event=>{if(event.target.name) {state.dirty=true;markDirty();}});
  renderPlanResults();
}
function markDirty() {const results=$('#plans-results');if(!results)return;let note=$('#dirty-notice');if(!note){note=document.createElement('div');note.id='dirty-notice';note.className='notice warning';note.textContent='条件已修改。当前显示的是上次计算结果，请重新生成后再保存或导出。';results.prepend(note);}$$('[data-action="save-plan"], [data-action="export"]',results).forEach(button=>button.disabled=true);}
function readConstraints() {
  const form=$('#conditions-form'); const updated=clone(state.constraints);
  updated.friday_free=$('#friday-free').value;
  updated.requested_course_ids=$$('input[name="requested_course_ids"]:checked',form).map(input=>input.value);
  updated.locked_section_ids=[...new Set([...(updated.locked_section_ids||[]),...$$('input[name="locked_section_ids"]:checked',form).map(input=>input.value),...(updated.preloaded_section_ids || [])])];
  updated.revision=Number(updated.revision||0)+1;return updated;
}
async function generateFromForm(){await generate(readConstraints());}
async function generate(constraints) {
  if(state.busy||state.agentBusy||state.agentActionBusy||state.loadedSaved)return;
  const previous=state.lastConstraints || state.constraints;state.busy=true;state.constraints=constraints;renderPlanning();clearMessage();
  try {
    const result=await api('/api/generate',{constraints:{...constraints,expected_revision:previous.revision}});
    state.generated=result;state.lastConstraints=clone(result.constraints || constraints);state.constraints=clone(result.constraints || constraints);state.selected=0;state.dirty=false;state.comparison=null;
    if(result.versions&&state.boot){state.boot.versions=clone(result.versions);if(state.boot.demo)state.boot.demo.constraints=clone(state.constraints);}
    const choices=result.plans?.length?result.plans:(result.pending_plans||[]);
    if(choices.length>1){try{state.comparison=await api('/api/compare',{plans:choices});}catch(error){message(`方案已生成，比较暂不可用：${error.message}`,true);}}
    const plans=result.plans?.length || result.pending_plans?.length || 0;message(plans?`已计算 ${plans} 个方案，锁定和固定条件已重新校验。`:'当前条件没有形成可推荐方案，请查看计算说明。');
  }catch(error){message(error.message,true);}finally{state.busy=false;renderPlanning();}
}
const checkNames={friday_free:'周五空闲条件',time_conflict:'上课时间冲突',exam_conflict:'考试时间冲突',exam_class_conflict:'考试与上课冲突',exam_schedule:'考试安排',schedule:'上课时段',fixed_schedule:'固定安排',protected_sections:'锁定与预置保留',missing_protected_section:'保留班次缺失',missing_course_offerings:'课程开课数据',missing_section:'教学班数据',same_course_multiple_sections:'同课多班',required_course:'计划修读课程',availability:'余量快照',eligibility:'选课资格',prerequisite:'先修条件',prerequisites:'先修条件',plan_binding:'培养方案适用性',section_plan:'教学班适用方案',section_term:'教学班学期',section_academic_year:'教学班学年',credit_range:'计划学分范围',planned_credits:'计划学分',preference:'个人偏好',excluded_sections:'排除班次',duplicate_selection:'重复班次',snapshot_identity:'开课快照身份',snapshot_structure:'开课数据结构',snapshot_term:'目标学期'};
function uniqueChecks(items) {
  const seen=new Set();return (items||[]).filter(item=>{const key=typeof item==='string'?item:JSON.stringify([item.reason||item.message||'', [...(item.section_ids||[])].sort()]);if(seen.has(key))return false;seen.add(key);return true;});
}
function checkDescription(item){
  if(typeof item==='string')return esc(item);
  const label=item.title || checkNames[item.code || item.check] || '条件核对';
  return `<strong>${esc(label)}</strong>${item.section_ids?.length?` · ${item.section_ids.map(id=>`<span class="mono">${esc(id)}</span>`).join('、')}`:''}<br>${esc(item.reason || item.message || '待确认')}`;
}
function renderSuggestions(items) {
  const suggestions=uniqueChecks(items);return suggestions.length?`<div class="search-detail"><strong>可审查的修改建议</strong><ul class="results-list">${suggestions.map(item=>`<li><span>${checkDescription(item)}${typeof item==='object'&&item.action?`<br>建议：${esc(item.action)}`:''}</span></li>`).join('')}</ul></div>`:'';
}
function renderPlanResults() {
  const container=$('#plans-results'); if(!container)return;
  const result=state.generated;
  if(!result){container.innerHTML=`<div class="empty-state"><div class="empty-glyph" aria-hidden="true">▦</div><h2>从一组可解释的方案开始</h2><p>演示已准备两门专业课程和一个保留的体育班。点击「生成并校验方案」，比较教学班组合与周五空闲的取舍。</p></div>`;return;}
  const plans=result.plans?.length?result.plans:(result.pending_plans||[]);
  const diagnostics=uniqueChecks(result.diagnostics);
  container.innerHTML=(diagnostics.length?`<div class="search-detail"><strong>计算说明</strong><ul class="results-list">${diagnostics.map(item=>`<li>${typeof item==='object'?statusBadge(item.result || 'unknown'):''}<span>${checkDescription(item)}</span></li>`).join('')}</ul></div>`:'')+(plans.length?`<div class="plan-options" role="group" aria-label="比较生成方案">${plans.map((plan,index)=>{const validation=plan.validation || {};const friday=validation.preference_scores?.friday_meeting_count;return `<button class="plan-option ${index===state.selected?'selected':''}" data-action="select-plan" data-index="${index}" aria-pressed="${index===state.selected}"><span class="option-title">${esc(planName(plan,index))}<span class="selection-circle" aria-hidden="true"></span></span><div class="option-metrics"><strong class="numeric">${esc(showValue(planCredits(plan)))}</strong><span>预计学分 · ${esc(plan.section_ids?.length || planSections(plan).length)} 个教学班</span></div><p class="option-description">${esc(typeof friday==='number' ? (friday===0?'周五没有课程安排':`周五安排 ${friday} 个上课时段`) : `锁定 ${state.constraints.locked_section_ids?.length||0} 个教学班`)}<br>${esc(planSections(plan).map(section=>section.section_id.replace('DEMO-','')).join(' · '))}</p>${statusBadge(validation.result || 'pending')}</button>`;}).join('')}</div>${state.comparison?renderComparison(state.comparison):''}<div id="selected-plan-detail"></div>`:`<div class="empty-state"><h2>当前范围未找到可行组合</h2><p>${esc(result.reason || '请核对锁定项与硬约束，并查看计算说明。')}</p><p>搜索未找到方案不等于学校全局无解；数据覆盖范围和搜索限制影响结论。</p></div>`)+renderSuggestions(result.suggestions)+`<details class="details-list"><summary>查看搜索范围与结果版本</summary><pre id="search-json"></pre></details>`;
  $('#search-json').textContent=JSON.stringify({status:result.status,reason:result.reason,search:result.search,versions:result.versions,provenance:result.provenance},null,2);
  if(plans.length)renderSelectedPlan();if(state.dirty)markDirty();
}
function renderSelectedPlan() {
  const plan=currentPlan();if(!plan)return; const selectedSections=planSections(plan);const validation=plan.validation||{};const locks=new Set(state.constraints.locked_section_ids || []);const preloaded=new Set(state.constraints.preloaded_section_ids || []);
  const valid=['pass','satisfied','valid'].includes(validation.result);const frozen=Boolean(state.loadedSaved);
  $('#selected-plan-detail').innerHTML=`<article class="plan-detail"><div class="plan-detail-header"><div><h2>${esc(planName(plan,state.selected))} · 模拟课表</h2><p>规划草案，仅表示计算组合；保存不表示正式中选。</p></div><div class="button-group">${frozen?'<span class="badge warning">冻结保存版</span>':`<button class="button small" data-action="validate-plan">重新校验</button><button class="button small" data-action="lock-plan">锁定本方案班次</button><button class="button small primary" data-action="save-plan" ${state.dirty?'disabled':''}>保存方案</button>`}</div></div><div class="detail-body"><div class="summary-strip"><dl class="summary-item"><dt>校验结果</dt><dd>${statusBadge(validation.result || 'pending')}</dd></dl><dl class="summary-item"><dt>预计修读学分</dt><dd class="numeric">${esc(showValue(planCredits(plan)))} 学分</dd></dl><dl class="summary-item"><dt>周五条件</dt><dd>${esc({soft:'尽量空闲',hard:'必须空闲',off:'无此偏好'}[state.constraints.friday_free]||'未设置')}</dd></dl><dl class="summary-item"><dt>保留项</dt><dd>${esc(locks.size)} 个锁定</dd></dl></div>${!valid?notice('该方案有不满足或待确认项，请展开校验明细核对。保存时仍会保留这个状态。',['fail','unsatisfied'].includes(validation.result)?'danger':'warning'):''}<div class="timetable-toolbar"><h3>每周安排</h3><div class="timetable-filters"><label for="season-filter">分学期</label><select id="season-filter"></select><label for="week-filter">周次</label><select id="week-filter"></select></div></div><div id="timetable-view"></div><div class="current-plan-table table-wrap"><table class="data-table"><thead><tr><th>课程 / 教学班</th><th>全部上课时段</th><th>余量快照</th></tr></thead><tbody>${selectedSections.map(section=>`<tr><td><strong>${esc(section.course_name)}</strong>${locks.has(section.section_id)?'<span class="lock-tag">锁定</span>':''}<small>${esc(section.section_id)} · ${esc(section.credits)} 学分${preloaded.has(section.section_id)?' · 模拟预置':''}<br>${esc((section.instructors||[]).join('、')||'教师待确认')}</small></td><td>${(section.meetings||[]).length?(section.meetings||[]).map(meeting=>`${esc(meetingText(meeting))}<small>${esc(meeting.campus||'校区待确认')} · ${esc(meeting.building_room||'地点待确认')}</small>`).join('<br>'):'时段待确认'}</td><td class="nowrap">${esc(showValue(section.available_seats))}<small>${section.available_seats==null?'未知，不按 0 处理':'旧快照不保证当前名额'}</small></td></tr>`).join('')}</tbody></table></div><details class="details-list"><summary>校验明细与修改建议</summary><div id="validation-checks"></div><pre id="validation-json"></pre></details><details><summary>预计培养要求贡献</summary><p>预计贡献用于比较修读安排，不能计作已经获得的学分或认定。</p><pre id="contribution-json"></pre></details><div class="toolbar export-toolbar"><span class="version-note">导出含 synthetic 标记与版本信息</span><div class="button-group"><button class="button small" data-action="export" data-format="json" ${state.dirty?'disabled':''}>导出 JSON</button><button class="button small" data-action="export" data-format="csv" ${state.dirty?'disabled':''}>导出 CSV</button><button class="button small" data-action="export" data-format="ics" ${state.dirty?'disabled':''}>导出日历 ICS</button></div></div></div></article>`;
  $('#validation-json').textContent=JSON.stringify(validation,null,2);$('#contribution-json').textContent=JSON.stringify(plan.expected_rule_contributions||[],null,2);
  const checks=validation.checks || [];$('#validation-checks').innerHTML=`<ul class="results-list">${checks.map(check=>`<li>${statusBadge(check.result || check.status || 'pending')}<span>${checkDescription(check)}</span></li>`).join('')}${(validation.conflicts||[]).map(conflict=>`<li>${statusBadge('unsatisfied')}<span>${esc(typeof conflict==='string'?conflict:JSON.stringify(conflict))}</span></li>`).join('')}</ul>${renderSuggestions(validation.suggestions)}`;
  const meetings=[...selectedSections.flatMap(section=>section.meetings||[]),...(state.constraints.fixed_meetings||[])];
  const seasons=[...new Set(meetings.flatMap(meeting=>meeting.season_codes||[]))];if(!seasons.includes(state.season))state.season=seasons[0]||'冬';
  $('#season-filter').innerHTML=seasons.map(season=>`<option value="${esc(season)}" ${season===state.season?'selected':''}>${esc(season)}季</option>`).join('');
  const maxWeek=Math.max(1,...meetings.flatMap(meeting=>meeting.week_numbers||[]));if(state.week>maxWeek)state.week=1;
  $('#week-filter').innerHTML=Array.from({length:Math.min(maxWeek,60)},(_,index)=>`<option value="${index+1}" ${index+1===state.week?'selected':''}>第 ${index+1} 周</option>`).join('');
  $('#season-filter').addEventListener('change',event=>{state.season=event.target.value;renderTimetable();});$('#week-filter').addEventListener('change',event=>{state.week=Number(event.target.value);renderTimetable();});renderTimetable();
}
function renderTimetable() {
  const plan=currentPlan();const selectedSections=planSections(plan);const activeMeetings=[];
  selectedSections.forEach(section=>(section.meetings||[]).forEach(meeting=>{if((meeting.season_codes||[]).includes(state.season)&&(meeting.week_numbers||[]).includes(state.week))activeMeetings.push({...meeting,course_name:section.course_name,section_id:section.section_id});}));
  (state.constraints.fixed_meetings||[]).forEach(meeting=>{if((meeting.season_codes||[]).includes(state.season)&&(meeting.week_numbers||[]).includes(state.week))activeMeetings.push({...meeting,course_name:meeting.title||'固定安排',fixed:true});});
  const periodCount=Math.max(8,...activeMeetings.flatMap(meeting=>meeting.period_indices || []));
  $('#timetable-view').innerHTML=`<div class="timetable-scroll"><table class="timetable" aria-label="${esc(state.season)}季第${state.week}周模拟课表"><thead><tr><th scope="col">节次</th>${dayNames.slice(1).map(day=>`<th scope="col">${day}</th>`).join('')}</tr></thead><tbody>${Array.from({length:Math.min(periodCount,20)},(_,index)=>{const period=index+1;return `<tr><td class="period">${period}</td>${dayNames.slice(1).map((_,dayIndex)=>{const matches=activeMeetings.filter(meeting=>meeting.day_of_week===dayIndex+1&&(meeting.period_indices||[]).includes(period));return `<td>${matches.map(meeting=>`<div class="meeting-block ${meeting.fixed?'fixed':''}" title="${esc(meetingText(meeting))}">${esc(meeting.course_name)}${period===meeting.period_indices[0]?`<small>${esc(meeting.section_id||'固定')} · ${esc(meeting.building_room||'地点待确认')}</small>`:''}</div>`).join('')}</td>`;}).join('')}</tr>`;}).join('')}</tbody></table></div><p class="table-caption">当前仅显示 ${esc(state.season)}季第 ${state.week} 周。单双周与分学期以时段记录为准，全部时段见下表。校历 ${esc(planDataset().academic_calendar?.calendar_id || '待确认')} 来自合成演示；缺失周次或节次的安排不会被当作空闲，仍须核对校验明细。</p>`;
}
function renderAudit() {
  const panel=$('#panel-audit');const audit=state.mode==='demo'?state.audit:state.sourceAudit;
  const heading=pageHeading('学业账本','已获学分、在修、认定和记点分别核算。','<button class="button" data-action="refresh-audit">重新计算账本</button>');
  if(!audit){panel.innerHTML=heading+dataNotice()+`<div class="empty-state"><h2>账本待计算</h2><p>点击「重新计算账本」读取当前数据范围。</p></div>`;return;}
  const ledger=state.mode==='demo'?(audit.ledger || {}):{};const student=state.mode==='demo'?demo().student:{};const courses=courseMap();const records=state.mode==='demo'?rows(demo().academic_snapshot):[];
  const ruleRows=audit.rules || [];const units={credits:'学分',course_count:'门',hours:'学时',points:'记点'};const ruleMap=new Map(rows(demo().rules).map(rule=>[rule.rule_id,rule]));
  panel.innerHTML=heading+dataNotice()+`<div class="section-container"><div class="student-summary"><div><span>适用专业</span><strong>${esc(student?.major_or_program || '未确认')}</strong></div><div><span>入学年</span><strong>${esc(showValue(student?.cohort))}</strong></div><div><span>方案版本</span><strong>${esc(student?.plan_version || '未确认')}</strong></div><div><span>外语层次</span><strong>${esc(student?.language_level || '待确认')}</strong></div></div></div><dl class="audit-summary"><div><dt>已获基础学分</dt><dd class="numeric">${esc(showValue(typeof ledger.base==='object'?(ledger.base?.credits??ledger.base?.total):ledger.base))}<span>学分</span></dd></div><div><dt>二 / 三 / 四课堂</dt><dd class="numeric">${esc(showValue(ledger.extra_classrooms?.total))}<span>学分 · 独立账本</span></dd></div><div><dt>在修学分</dt><dd class="numeric">${esc(showValue(ledger.in_progress?.credits))}<span>尚未计入已获</span></dd></div><div><dt>待确认记录</dt><dd class="numeric">${esc(showValue(ledger.unknown?.record_count))}<span>项</span></dd></div></dl><div class="section-container"><div class="section-title-bar"><div><h2>${state.mode==='demo'?'演示要求核算':'原始候选状态'}<span class="page-count">${ruleRows.length} 条</span></h2><p>${state.mode==='demo'?'三态结果保留未知信息；标签重叠不重复增加实际学分。':'本人学业来源未接入，摘要均待确认；空输入小计不代表本人总账。原始候选全部保持待审核，不输出正式毕业结论。'}</p></div><span class="badge ${state.mode==='demo'?'synthetic':'warning'}">${state.mode==='demo'?'DEMO-RULES':'候选未启用'}</span></div><div class="table-wrap"><table class="data-table"><thead><tr><th>要求与依据</th><th>实际 / 目标</th><th>结果</th><th>说明</th></tr></thead><tbody>${ruleRows.map(rule=>{const definition=ruleMap.get(rule.rule_id)||{};const threshold=rule.threshold ?? definition.threshold;const unit=units[definition.metric] || '';return `<tr><td><strong>${esc(rule.title || rule.rule_id)}</strong><small>${esc(rule.rule_id)}</small>${sourceLinks(rule.source_refs||rule.sources)}</td><td class="nowrap numeric">${esc(showValue(rule.actual))} / ${esc(showValue(threshold))}<small>${esc(unit)}${rule.remaining!==null&&rule.remaining!==undefined?` · 缺 ${esc(rule.remaining)}`:''}</small></td><td>${statusBadge(rule.result||'pending')}</td><td>${esc(rule.reason || '等待适用规则和记录确认')}</td></tr>`;}).join('')}</tbody></table></div>${!ruleRows.length?'<p class="inline-empty">当前无可执行要求。</p>':''}</div>${(audit.warnings||[]).length?notice((audit.warnings||[]).map(item=>typeof item==='string'?item:JSON.stringify(item)).join('；')):''}<div class="section-container"><div class="section-title-bar"><div><h2>修读与认定记录</h2><p>已通过、挂科、在修、重修、待认定及重复记录分别处理。</p></div></div>${records.length?`<div class="table-wrap"><table class="data-table"><thead><tr><th>课程 / 认定</th><th>修读状态</th><th>原始数值</th><th>归属与记录</th></tr></thead><tbody>${records.map(record=>{const course=courses.get(record.course_id);const status=academicStatus(record);const statusLabels={passed:'已通过',failed:'未通过',in_progress:'在修',retaking:'重修在修',pending_recognition:'待认定',recognized:'已认定',replaced:'被替代',unknown:'待确认'};return `<tr><td><strong>${esc(course?.course_name || record.title || record.course_id || record.recognition_tag || '课堂/活动认定')}</strong><small>${esc(record.course_id || record.recognition_id || '')}</small></td><td>${esc(statusLabels[status] || status)}</td><td>${academicMeasurements(record).map(value=>esc(value)).join('<br>')}<small>认定 ${esc({not_applicable:'不适用',recognized:'已认定',pending:'待认定',rejected:'未获认定',unknown:'待确认'}[record.recognition_status]||showValue(record.recognition_status))}</small></td><td>${esc(record.ledger || record.credit_ledger || '待确认')}<small>${esc(record.record_id || '')}</small></td></tr>`;}).join('')}</tbody></table></div>`:'<p class="inline-empty">尚无本人真实学业记录。合成记录保存在独立演示目录。</p>'}</div>${ledger.accounting_note?notice(ledger.accounting_note,'neutral'):''}${renderWishes()}${state.mode==='demo'?`<div class="section-container"><div class="inline-editor"><details><summary>开发者入口：导入或编辑本人合成数据</summary><p>仅支持带 synthetic 标记的完整演示 bundle。只能更新当前合成学生；学生、学业记录、规则和教学班一起校验；原始交接资料保持只读。导入后当前候选方案会清空，已有保存版本保留原快照。</p><textarea id="bundle-editor" aria-label="合成演示 bundle JSON" rows="12"></textarea><div class="editor-actions"><input type="file" id="bundle-file" class="file-input" accept=".json,application/json" aria-label="选择合成演示 JSON 文件"><button class="button primary" data-action="import-bundle">校验并导入演示数据</button><button class="button" data-action="reset-demo">恢复内置演示</button></div></details></div></div>`:''}<details class="details-list"><summary>查看账本明细、适用状态与版本</summary><pre id="audit-json"></pre></details>`;
  $('#audit-json').textContent=JSON.stringify(audit,null,2);if($('#bundle-editor'))$('#bundle-editor').value=JSON.stringify(demo(),null,2);
  $('#bundle-file')?.addEventListener('change',async event=>{const file=event.target.files[0];if(!file)return;try{const text=await file.text();JSON.parse(text);$('#bundle-editor').value=text;message('已读取 JSON，点击「校验并导入演示数据」提交。');}catch(error){message(`JSON 文件读取失败：${error.message}`,true);}});
}
function renderWishes() {
  if(state.mode!=='demo')return ''; const groups=demo().wish_groups || [];const items=demo().wish_items || demo().offering_snapshot?.wish_items || [];const map=new Map(rows(demo().offering_snapshot).map(section=>[section.section_id,section]));
  return `<div class="section-container"><div class="section-title-bar"><div><h2>备选志愿与预置记录</h2><p>备选组内最多取一个教学班；备选和待定不计作同时中选。</p></div><span class="badge synthetic">synthetic</span></div>${groups.map(group=>`<div class="wish-group"><strong>${esc(group.wish_group_id)}</strong> · 最多 ${esc(group.selection_limit)} 个<p>${items.filter(item=>item.wish_group_id===group.wish_group_id).sort((a,b)=>a.rank-b.rank).map(item=>`${esc(item.rank)}. ${esc(map.get(item.section_id)?.course_name || item.section_id)}（${esc(item.section_id)}，${esc(item.official_enrollment_status_raw || '待定')}）`).join('<br>')}</p></div>`).join('')}${items.filter(item=>!item.wish_group_id).map(item=>`<div class="wish-group"><strong>${esc(item.section_id)}</strong><p>${esc(item.official_enrollment_status_raw || item.official_enrollment_status)} · 状态来自合成测试记录，非学校结果。</p></div>`).join('')}${!groups.length&&!items.length?'<p class="inline-empty">暂无志愿记录。</p>':''}</div>`;
}
function renderSources(filter='all') {
  const data=source();const plans=rows(data.plans);const candidates=rows(data.candidates);const issues=rows(data.issues);const courses=rows(data.courses);
  const filteredCandidates=candidates.filter(candidate=>filter==='all'||candidate.plan_id===filter);const filteredIssues=issues.filter(issue=>filter==='all'||issue.plan_ids?.includes(filter));
  $('#panel-sources').innerHTML=pageHeading('资料与规则审核','保留来源、差异与待补信息，计算使用单独版本的规则。')+notice('原始 23 条候选规则全部待人工审核，enforce_automatically=false。交接材料完整性校验不能替代校方规则认定。')+`<div class="source-overview">${plans.map(plan=>`<article class="source-plan"><h3>${esc(plan.program_name)}</h3><p>${esc(plan.plan_version_label)} · 原文最低学分 ${esc(plan.minimum_graduation_credits_raw)}<br>${esc(plan.sample_course_count)} 条课程样本 · ${esc(plan.sample_requirement_candidate_count)} 条候选</p><span class="badge warning">学生适用性未确认</span>${sourceLinks(plan.source_evidence || [plan.source_document])}</article>`).join('')}</div><div class="filter-bar"><label for="source-plan-filter">筛选资料范围</label><select id="source-plan-filter"><option value="all">全部培养方案</option>${plans.map(plan=>`<option value="${esc(plan.plan_id)}" ${filter===plan.plan_id?'selected':''}>${esc(plan.program_name)}</option>`).join('')}</select><span class="muted">PDF 页码为物理页码，从 1 开始</span></div><div class="section-container"><div class="section-title-bar"><h2>候选规则 <span class="page-count">${filteredCandidates.length} / ${candidates.length}</span></h2><span class="badge warning">未启用</span></div>${filteredCandidates.map(candidate=>`<div class="source-row"><details><summary><span class="summary-text">${esc(candidate.title)}<small>${esc(candidate.rule_id)} · ${esc(candidate.plan_id)}</small></span><span class="badge warning">待人工审核</span></summary><div class="detail-content">${(candidate.source_quotes||[]).map(quote=>`<p>「${esc(quote)}」</p>`).join('')}${sourceLinks(candidate.sources || [{file_name:candidate.source_file,pdf_pages:candidate.source_pages}])}<ul>${(candidate.review_notes||[]).map(note=>`<li>${esc(note)}</li>`).join('')}</ul><p>自动执行：${candidate.enforce_automatically===false?'禁止':'未确认'} · 当前官方版本：未确认</p><pre>${esc(JSON.stringify(candidate.candidate_expression,null,2))}</pre></div></details></div>`).join('')}</div><div class="section-container"><div class="section-title-bar"><h2>来源问题与证据缺口 <span class="page-count">${filteredIssues.length} / ${issues.length}</span></h2></div>${filteredIssues.map(issue=>`<div class="source-row"><details><summary><span class="summary-text">${esc(issue.title)}<small>${esc(issue.issue_id)}</small></span><span class="badge warning">待补依据</span></summary><div class="detail-content"><p><strong>影响：</strong>${esc(issue.impact)}</p>${(issue.evidence||[]).map(evidence=>`${evidence.excerpt?`<p>「${esc(evidence.excerpt)}」</p>`:''}${sourceLinks([evidence])}`).join('')}<p><strong>待办：</strong>${esc(issue.proposed_resolution)}</p><p>保留状态：${esc(issue.status)}</p></div></details></div>`).join('')}</div><div class="section-container"><div class="section-title-bar"><div><h2>课程定义样本 <span class="page-count">${courses.filter(course=>filter==='all'||course.plan_id===filter).length} / ${courses.length}</span></h2><p>课程定义与建议修读学期，不代表当前教学班或正式先修要求。</p></div></div><div class="table-wrap"><table class="data-table"><thead><tr><th>课程号 / 名称</th><th>学分</th><th>方案列示归属</th><th>原文与页码</th></tr></thead><tbody>${courses.filter(course=>filter==='all'||course.plan_id===filter).map(course=>`<tr><td><strong>${esc(course.course_name)}</strong><small>${esc(course.course_code)}</small></td><td>${esc(course.credits)}</td><td>${esc((course.requirement_path||[]).join(' / '))}<small>建议：${esc(course.suggested_year_term_raw)}，当前开课待导入</small></td><td>${sourceLinks(course.sources)}</td></tr>`).join('')}</tbody></table></div></div><details><summary>完整性校验与原始数据版本</summary><pre id="integrity-json"></pre></details>`;
  $('#integrity-json').textContent=JSON.stringify({integrity:data.integrity,counts:data.counts,metadata:data.metadata,empty_offering_snapshot:data.empty_offering_snapshot},null,2);
  $('#source-plan-filter').addEventListener('change',event=>renderSources(event.target.value));
}
function savedRows(value){return Array.isArray(value)?value:(value?.saved_plans || value?.revisions || value?.plans || []);}
function renderComparison(comparison) {
  const frozen=comparison.mode==='frozen';
  const entries=frozen?(comparison.summaries||[]):(comparison.plans||[]).map((validation,index)=>({plan:{validation,section_ids:validation.section_ids},index}));
  if(entries.length<2)return '';
  const label=(entry,index)=>frozen?`保存版 #${entry.saved_id}`:planName(null,index);
  const metricRow=(title,read)=>`<tr><th scope="row">${esc(title)}</th>${entries.map((entry,index)=>`<td>${read(entry,index)}</td>`).join('')}</tr>`;
  return `<section class="section-container comparison-panel" aria-label="方案比较"><div class="section-title-bar"><div><h2>${frozen?'冻结保存版本比较':'当前方案比较'}</h2><p>${esc(comparison.note || '按当前同一组输入重新校验。')}</p></div></div>${frozen&&comparison.different_input_versions?notice('各保存版的输入版本不同。下表分别呈现历史结果，不能作为同条件排名。'):''}<div class="table-wrap"><table class="data-table"><thead><tr><th>比较项</th>${entries.map((entry,index)=>`<th>${esc(label(entry,index))}</th>`).join('')}</tr></thead><tbody>${metricRow('校验结果',entry=>statusBadge(entry.plan.validation?.result || 'unknown'))}${metricRow('预计修读学分',entry=>esc(showValue(planCredits(entry.plan))))}${metricRow('周五上课时段',entry=>esc(showValue(entry.plan.validation?.preference_scores?.friday_meeting_count)))}${metricRow('教学班组合',entry=>(entry.plan.section_ids||[]).map(id=>esc(id)).join('<br>'))}${metricRow(frozen?'锁定条件':'相对方案 A 的班次变化',(entry,index)=>frozen?esc((entry.constraints?.locked_section_ids||[]).join('、')||'无'):index===0?'比较基准':`新增 ${esc((comparison.differences?.[index]?.added_section_ids||[]).join('、')||'无')}<br>移除 ${esc((comparison.differences?.[index]?.removed_section_ids||[]).join('、')||'无')}`)}${frozen?metricRow('数据与偏好版本',entry=>`快照 ${esc(entry.versions?.offering_snapshot||'待确认')}<br>条件版本 ${esc(showValue(entry.versions?.constraints_revision))}`):''}</tbody></table></div>${!frozen&&comparison.ranking?.length?`<p class="table-caption">按校验结果及周五软偏好排序：${comparison.ranking.map(index=>esc(planName(null,index))).join(' → ')}。个人选择仍需核对详细依据。</p>`:''}<details class="comparison-details"><summary>查看比较依据与版本</summary><pre>${esc(JSON.stringify(comparison,null,2))}</pre></details></section>`;
}
function renderSaved() {
  if(isAcademicIdentity()){$('#panel-saved').innerHTML=notice('真实推荐与保存能力尚未接入。');return;}
  $('#panel-saved').innerHTML=`<div class="page-heading"><h1>已保存</h1><button class="button small" data-action="refresh-saved">刷新</button></div>${state.saved.length?[...state.saved].sort((left,right)=>right.saved_id-left.saved_id).map(saved=>{const plan=saved.plan||saved;return `<article class="saved-entry"><div class="saved-entry-header"><h2>课表 #${esc(saved.saved_id)}</h2>${statusBadge(plan.validation?.result||'unknown')}</div><details class="saved-preview"><summary>查看课表</summary>${agentPlanTable(plan,saved.constraints||{})}${agentCheckList(plan.validation?.checks)}</details>${agentExportMenu('export-saved',saved.saved_id,!state.agentActionBusy)}</article>`;}).join(''):'<p class="agent-empty">还没有保存的课表。</p>'}`;
}
async function exportSaved(savedId,format){
  if(isAcademicIdentity())return;
  if(state.agentActionBusy||!state.saved.some(saved=>saved.saved_id===Number(savedId)))return;
  const epoch=state.agentEpoch;state.agentActionBusy=true;renderSaved();updateAgentSend();
  try{
    const response=await request('/api/export',{saved_id:Number(savedId),format},epoch);
    const blob=await response.blob();if(epoch!==state.agentEpoch)return;
    const filename=response.headers.get('Content-Disposition')?.match(/filename="?([^";]+)"?/)?.[1]||`synthetic-course-plan.${format}`;
    const url=URL.createObjectURL(blob);const anchor=document.createElement('a');anchor.href=url;anchor.download=filename;document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
    message(`已导出保存时的课表 #${savedId}`);
  }catch(error){if(epoch===state.agentEpoch)message(error.message,true);}
  finally{if(epoch===state.agentEpoch){state.agentActionBusy=false;renderSaved();updateAgentSend();}}
}

async function refreshAudit(){try{const result=await api('/api/audit',{mode:state.mode});if(state.mode==='demo')state.audit=result;else state.sourceAudit=result;renderAudit();}catch(error){message(error.message,true);}}
async function refreshSaved(){if(isAcademicIdentity())return;const epoch=state.agentEpoch;try{const saved=await api('/api/saved',undefined,epoch);if(epoch===state.agentEpoch){state.saved=savedRows(saved);renderSaved();}}catch(error){if(epoch===state.agentEpoch)message(error.message,true);}}
function workspaceIcon(key){
  const paths={graduation_audit:'M3 8l9-5 9 5-9 5zM6 10v7c4 3 8 3 12 0v-7M21 8v7',enrollment:'M5 4h14v17H5zM9 2v4h6V2M8 11l2 2 5-5M8 17h8',academic:'M4 19V5m0 14h16M8 15v-4m5 4V7m5 8v-6',timetable:'M7 3v4m10-4v4M4 10h16M4 5h16v16H4z',exams:'M12 8v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',profile:'M14 3H5v18h14V8zM14 3v5h5M8 12h8m-8 4h6'};
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[key]||paths.profile}"/></svg>`;
}
const workspaceSchoolPages={
  graduation_audit:{title:'培养进度'},
  academic:{title:'成绩记录'},
  timetable:{title:'我的课表'},
  exams:{title:'考试安排'},
  enrollment:{title:'当前选课'}
};
function renderWorkspaceNavigation(){
  const nav=$('#workspace-navigation');if(!nav)return;
  nav.hidden=!isAcademicIdentity();
  nav.innerHTML=Object.entries(workspaceSchoolPages).map(([key,page])=>`<button type="button" class="workspace-link" data-action="workspace-page" data-page="${key}" aria-current="${state.tab==='school'&&schoolSection()===key?'page':'false'}" ${state.schoolBusy?'disabled':''}><span aria-hidden="true">${workspaceIcon(key)}</span>${page.title}</button>`).join('');
}
function openWorkspacePage(page){
  if(!isAcademicIdentity())return;
  if(Object.hasOwn(workspaceSchoolPages,page)){
    if(state.schoolBusy||state.busy)return;
    selectSchoolSection(page);switchTab('school');
    $(`#workspace-navigation [data-page="${page}"]`)?.focus();
  }else if(page==='assistant')switchTab(page);
}
function renderPlanComparison(result,options){
  const rows=[
    {title:'课程门数',values:options.map(option=>Array.isArray(option.classes)?String(option.classes.length):'待确认')},
    {title:'总学分',values:options.map(option=>personalText(option.total_credits))},
    {title:'周五占用',values:options.map(option=>Number.isSafeInteger(option.friday_meeting_count)&&option.friday_meeting_count>=0?`${option.friday_meeting_count} 段`:'待确认')}
  ];
  const courses=new Map();
  options.forEach((option,optionIndex)=>{const seen=new Map();(Array.isArray(option.classes)?option.classes:[]).forEach((course,index)=>{
    const code=typeof course.course_code==='string'&&course.course_code.trim()?course.course_code:null;
    const occurrence=(seen.get(code)||0)+1;seen.set(code,occurrence);
    const key=code?JSON.stringify([code,occurrence]):`unknown:${optionIndex}:${index}`;
    if(!courses.has(key))courses.set(key,{title:personalText(course.course_name,'课程名称待确认'),code,values:options.map(()=>null)});
    courses.get(key).values[optionIndex]=course;
  });});
  const cells=(values,render)=>values.map(value=>`<td>${render(value)}</td>`).join('');
  const summaries=rows.map(row=>`<tr${new Set(row.values).size>1?' class="plan-difference"':''}><th scope="row">${row.title}</th>${cells(row.values,esc)}</tr>`).join('');
  const courseRows=[...courses.values()].map(row=>{
    const keys=row.values.map(course=>course?JSON.stringify([course.class_ref||null,course.credits??null,course.weekday_period_text||[],course.locked===true]):null);
    const different=new Set(keys).size>1;
    return `<tr${different?' class="plan-difference"':''}><th scope="row">${esc(row.title)}<small>${esc(row.code||'课程标识待确认')}${different?' · 有差异':''}</small></th>${cells(row.values,course=>course?`<strong>${esc(personalText(course.credits))} 学分</strong>${course.locked===true?'<span class="badge neutral">已锁定</span>':''}<p>${Array.isArray(course.weekday_period_text)&&course.weekday_period_text.length?course.weekday_period_text.map(esc).join('；'):'观察节次待确认'}</p>`:'<span class="muted">本方案未列出</span>')}</tr>`;
  }).join('');
  return `<div class="plan-comparison-wrap" tabindex="0" role="region" aria-label="方案对比表"><table class="plan-comparison"><caption>按课程代码对齐，底色标出不同项；缺少代码的课程分别列出。</caption><thead><tr><th scope="col">比较项</th>${options.map(option=>`<th scope="col">方案 ${esc(option.label)}</th>`).join('')}</tr></thead><tbody>${summaries}${courseRows}</tbody></table></div>`;
}
function switchTab(tab) {
  const previousTab=state.tab;if(previousTab==='assistant')captureAgentScroll();
  if(tab==='plans'||isAcademicIdentity()&&!['assistant','school'].includes(tab))tab='assistant';
  if(tab==='school'&&!isAcademicIdentity())tab='assistant';
  state.tab=tab;renderWorkspaceNavigation();if(tab==='assistant')renderAssistant();$$('.nav-tab').forEach(button=>{const selected=button.dataset.tab===tab;button.classList.toggle('active',selected);button.setAttribute('aria-selected',String(selected));button.tabIndex=selected||tab==='school'&&button.dataset.tab==='assistant'?0:-1;});
  $$('.tab-panel').forEach(panel=>panel.hidden=panel.id!==`panel-${tab}`);
  if(tab==='audit'&&!(state.mode==='demo'?state.audit:state.sourceAudit))refreshAudit();
  if(tab==='school'){renderSchool();ensureSchoolQueryContext();}
  if(tab==='assistant'&&previousTab!=='assistant'&&isAcademicIdentity())refreshAgentStatus();
}
async function changeMode(mode) {state.mode=mode;const badge=$('#mode-badge');badge.textContent=mode==='demo'?'合成演示':'待审核资料';badge.className=`badge ${mode==='demo'?'synthetic':'warning'}`;clearMessage();renderAssistant();renderPlanning();renderAudit();if(state.tab==='audit'&&!(mode==='demo'?state.audit:state.sourceAudit))await refreshAudit();}
async function savePlan() {
  const plan=currentPlan();if(!plan||state.dirty||state.loadedSaved)return;
  try{await api('/api/save',{plan,constraints:state.constraints});await refreshSaved();message('已保存本人冻结版本；仍是规划草案，未确认正式中选。');}catch(error){message(error.message,true);}
}
async function validatePlan() {
  const previous=currentPlan();if(!previous||state.loadedSaved||state.busy)return;
  state.busy=true;
  try{
    const constraints=state.dirty?readConstraints():state.constraints;
    const result=await api('/api/validate',{section_ids:previous.section_ids,constraints:{...constraints,expected_revision:(state.lastConstraints||state.constraints).revision}});
    await refreshWorkspace();
    const plan=result.plan || {...previous,validation:result,versions:result.versions,sections:(result.section_ids||previous.section_ids).map(id=>sectionMap().get(id)).filter(Boolean),expected_rule_contributions:[],projection_note:'预计贡献待重新生成'};
    state.constraints=clone(result.constraints);state.lastConstraints=clone(result.constraints);state.generated={plans:[plan],status:'revalidated_current',versions:result.versions};state.selected=0;state.dirty=false;state.comparison=null;state.audit=null;
    renderPlanning();renderAudit();message(`已按当前数据与条件重新校验：${(statusNames[plan.validation.result] || [plan.validation.result])[0]}。`);
  }catch(error){message(error.message,true);}finally{state.busy=false;renderPlanning();}
}
async function exportPlan(format) {
  const plan=currentPlan();if(!plan||state.dirty)return;
  try{
    const body=state.loadedSaved?{saved_id:state.loadedSaved.saved_id,format}:{plan,constraints:state.constraints,format};
    const response=await request('/api/export',body);const blob=await response.blob();
    const filename=response.headers.get('Content-Disposition')?.match(/filename="?([^";]+)"?/)?.[1] || `synthetic-course-plan.${format}`;
    const url=URL.createObjectURL(blob);const anchor=document.createElement('a');anchor.href=url;anchor.download=filename;document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
    message(`已导出 ${format.toUpperCase()}，使用${state.loadedSaved?'保存时的冻结':'当前已校验'}输入并保留 synthetic 标记。`);
  }catch(error){message(error.message,true);}
}
async function lockPlan(){const plan=currentPlan();if(!plan||state.loadedSaved)return;const constraints=state.dirty?readConstraints():clone(state.constraints);constraints.locked_section_ids=[...new Set([...(constraints.locked_section_ids||[]),...plan.section_ids])];constraints.revision=Number(constraints.revision||0)+1;await generate(constraints);}
async function applyConstraints(){try{const edited=JSON.parse($('#constraints-editor').value);const preserved=state.lastConstraints || state.constraints;edited.locked_section_ids=[...new Set([...(preserved.locked_section_ids||[]),...(edited.locked_section_ids||[])])];edited.preloaded_section_ids=[...new Set([...(preserved.preloaded_section_ids||[]),...(edited.preloaded_section_ids||[])])];const fixed=new Map([...(preserved.fixed_meetings||[]),...(edited.fixed_meetings||[])].map(meeting=>[meeting.meeting_id || JSON.stringify(meeting),meeting]));edited.fixed_meetings=[...fixed.values()];await generate(edited);}catch(error){message(`条件 JSON 无法应用：${error.message}`,true);}}
async function refreshWorkspace(expectedEpoch) {
  const boot=await api('/api/bootstrap',undefined,expectedEpoch);
  if(expectedEpoch!==undefined&&expectedEpoch!==state.agentEpoch)return;
  if(state.boot?.identity && identityKey(state.boot.identity)!==identityKey(boot.identity)){
    clearAgentConversation();clearSchoolWorkspace();
    state.agentHistory=[];state.agentHistoryError='';state.agentHistoryHasMore=false;renderAgentHistory();
  }
  state.boot=boot;state.constraints=clone(state.boot.demo?.constraints || {});state.saved=savedRows(state.boot.saved_plans || []);
  if(state.auth)state.auth.identity=state.boot.identity;
  if(isAcademicIdentity()){state.schoolStatus=boot.school||{};state.agentStatus=boot.chat||{configured:false,reason:'real_recommendation_not_ready'};$('#mode-badge').textContent=agentDataKind()==='synthetic'?'模拟数据':'本人教务';$('#mode-badge').className='badge';}
}
function resetCurrentPlan(){state.loadedSaved=null;state.lastConstraints=null;state.generated=null;state.comparison=null;state.audit=null;state.dirty=false;state.selected=0;}
async function importBundle() {
  try{const bundle=JSON.parse($('#bundle-editor').value);await api('/api/import',{bundle});resetCurrentPlan();await refreshWorkspace();renderPlanning();await refreshAudit();renderSaved();message('本人合成数据已校验导入，锁定条件继承；请重新生成方案。');}catch(error){message(`演示导入失败：${error.message}`,true);}
}
async function resetDemo() {
  try{await api('/api/reset-demo',{});resetCurrentPlan();await refreshWorkspace();renderPlanning();await refreshAudit();renderSaved();message('已恢复本人内置合成数据，已建立的锁定条件继续保留。');}catch(error){message(error.message,true);}
}
async function loadSaved(index) {
  const saved=state.saved[index];if(!saved)return;
  try{const loaded=await api('/api/load',{saved_id:saved.saved_id});state.loadedSaved=loaded;state.constraints=clone(loaded.constraints);state.lastConstraints=null;state.generated={plans:[loaded.plan],status:'frozen_saved',versions:loaded.versions,current_versions:loaded.current_versions};state.comparison=null;state.selected=0;state.dirty=false;await changeMode('demo');switchTab('planning');message('已打开冻结版本。历史校验未被当前数据覆盖；可直接导出冻结版或明确重新校验。');}catch(error){message(error.message,true);}
}
async function revalidateSaved(index) {
  const saved=index===undefined?state.loadedSaved:state.saved[index];if(!saved||state.busy)return;
  state.busy=true;
  try{const result=await api('/api/revalidate',{saved_id:saved.saved_id});resetCurrentPlan();await refreshWorkspace();state.generated={plans:[result.plan],status:'revalidated_saved',versions:result.versions,previous_versions:result.previous_versions};state.constraints=clone(result.constraints);state.lastConstraints=clone(result.constraints);await changeMode('demo');switchTab('planning');message('已按当前本人数据与当前偏好重新校验，继承当前与保存版的锁定、预置及固定安排；历史保存版保持原样。');}catch(error){message(error.message,true);}finally{state.busy=false;renderPlanning();}
}
async function compareSaved() {
  try{state.savedComparison=await api('/api/compare',{saved_ids:state.compareSavedIds});renderSaved();message('已比较所选本人冻结版本；不同输入版本不会作为同条件排名。');}catch(error){message(error.message,true);}
}
async function useCurrent(){resetCurrentPlan();await refreshWorkspace();await changeMode('demo');renderSaved();message('已返回本人当前工作区；历史冻结版本保留。');}
function schoolPlainText(value,missing='待确认'){
  if(value===null||value===undefined||value==='unknown'||typeof value==='string'&&!value.trim())return missing;
  const text=typeof value==='object'?JSON.stringify(value):String(value);
  return text.replace(/(?:<br\s*\/?>|&lt;br\s*\/?&gt;)/gi,'\n').replace(/\r\n?/g,'\n');
}
function schoolTextMarkup(value,missing='待确认'){return esc(schoolPlainText(value,missing)).replace(/\n/g,'<br>');}
function schoolMeetingSegments(value){
  const parts=[];let part='',depth=0;
  for(const character of schoolPlainText(value,'')){
    if('{（(【['.includes(character))depth+=1;
    if('}）)】]'.includes(character)&&depth)depth-=1;
    const entityEnd=character===';'&&/&(?:#[0-9]+|#x[0-9a-f]+|[a-z][a-z0-9]*)$/i.test(part);
    if((character==='\n'||character===';'&&!entityEnd||character==='；')&&!depth){if(part.trim())parts.push(part.trim());part='';}
    else part+=character;
  }
  if(part.trim())parts.push(part.trim());return parts;
}
function schoolKnownField(record,key){return !record.field_states?.[key]||record.field_states[key]==='known';}
function schoolHasDisplayField(record,key){return schoolKnownField(record,key)&&schoolPlainText(record[key],'')!=='';}
function schoolChineseSeasons(record){
  if(record.field_states?.xxq_raw==='conflicting')return '';
  const value=schoolPlainText(record.xxq_raw,'').trim();
  return /^[秋冬春夏](?:\s*[、/，,]?\s*[秋冬春夏])*$/.test(value)?value:'';
}
function schoolChineseParity(record){
  if(record.field_states?.dsz_raw==='conflicting')return '';
  const value=schoolPlainText(record.dsz_raw,'').trim();return /^(?:单周|双周|单双周)$/.test(value)?value:'';
}
function schoolPeriodTimes(result){
  const source=result.period_times_source,values=result.period_times,times=new Map(),clock=/^(?:[01]\d|2[0-3]):[0-5]\d$/;
  if(source?.state!=='known'||source.page_path!=='/jwglxt/kbcx/xskbcx_cxXskbcxIndex.html'||!Array.isArray(values)||!values.length||values.length>200)return times;
  for(const value of values){
    if(!value||!Number.isSafeInteger(value.period)||value.period<1||value.period>200||typeof value.start_time!=='string'||typeof value.end_time!=='string'||!clock.test(value.start_time)||!clock.test(value.end_time)||value.start_time>=value.end_time)return new Map();
    const label=`${value.start_time}–${value.end_time}`;if(times.has(value.period)&&times.get(value.period)!==label)return new Map();times.set(value.period,label);
  }
  return times;
}
const schoolMainCampusClockReference=Object.freeze({
  urls:Object.freeze({
    '2025-2026':'https://ism.zju.edu.cn/_upload/article/files/19/cc/f1e962aa41b79062691319510e3f/92a3bd16-7bde-4c69-a522-8c6134fb2bf4.pdf',
    '2026-2027':'https://ugrs.zju.edu.cn/_upload/article/files/28/cc/ce06b4d9483eb58be317e94a7749/eeeb30e1-336a-4d3f-8aca-5890d2cc2ac4.pdf'
  }),
  times:Object.freeze(['08:00–08:45','08:50–09:35','10:00–10:45','10:50–11:35','11:40–12:25','13:25–14:10','14:15–15:00','15:05–15:50','16:15–17:00','17:05–17:50','18:50–19:35','19:40–20:25','20:30–21:15'])
});
function schoolCampusLocationText(value){
  return schoolPlainText(value,'').replace(/&#(?:x([0-9a-f]{1,6})|([0-9]{1,7}));/gi,(entity,hex,decimal)=>{
    const point=parseInt(hex||decimal,hex?16:10);return point>=32&&point<=0x10ffff&&!(point>=0xd800&&point<=0xdfff)?String.fromCodePoint(point):entity;
  });
}
function schoolPeriodTimeDisplay(result){
  if(result.operation!=='timetable')return {times:new Map(),origin:'none'};
  const times=schoolPeriodTimes(result);if(times.size)return {times,origin:'school_page'};
  const none={times,origin:'none'},term=typeof result.target_term==='string'?result.target_term.match(/^(\d{4}-\d{4})-(AW|SS)$/):null,year=term?.[1],url=year?schoolMainCampusClockReference.urls[year]:null;
  if(!url||result.period_times_source?.state==='known')return none;
  const locations=[];
  for(const record of Array.isArray(result.records)?result.records:[]){
    if(!record||typeof record!=='object')continue;
    for(const value of [record,...(Array.isArray(record.meetings)?record.meetings:[])]){
      if(value&&typeof value==='object'&&typeof value.place_raw==='string'&&schoolHasDisplayField(value,'place_raw'))locations.push(schoolCampusLocationText(value.place_raw));
    }
  }
  if(!locations.some(value=>/紫金港|玉泉|西溪|华家池|之江/.test(value))||locations.some(value=>/海宁|国际校区|工程师学院|义乌|舟山|宁波/.test(value)))return none;
  return {times:new Map(schoolMainCampusClockReference.times.map((time,index)=>[index+1,time])),origin:'public_reference',reference_year:year,reference_url:url};
}
function schoolPeriodReferenceLink(display){return `<a href="${esc(display.reference_url)}" target="_blank" rel="noopener noreferrer">节次时间参考${esc(display.reference_year.replace('-','–'))}校历（校本部）</a>`;}
function schoolNumberSet(value){
  return Array.isArray(value)&&value.length>0&&value.length<=200&&value.every(item=>Number.isSafeInteger(item)&&item>0)?[...new Set(value)].sort((left,right)=>left-right):[];
}
function schoolParsePeriodSet(value){
  if(!/^\d+(?:\s*[-–—~～至,，、]\s*\d+)*$/.test(value.trim()))return [];
  const periods=[];
  for(const part of value.split(/[,，、]/)){
    const match=part.trim().match(/^(\d+)(?:\s*[-–—~～至]\s*(\d+))?$/);if(!match)return [];
    const start=Number(match[1]),end=match[2]?Number(match[2]):start;
    if(!Number.isSafeInteger(start)||!Number.isSafeInteger(end)||start<1||end<start||end-start>200)return [];
    for(let period=start;period<=end;period+=1)periods.push(period);
  }
  return schoolNumberSet(periods);
}
function schoolParsedMeetingTime(value){
  const text=schoolPlainText(value,'');
  const days=[...text.matchAll(/(?:星期|周)([一二三四五六日天])/g)];
  if(days.length!==1)return {weekday:null,periods:[]};
  const weekday={'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'日':7,'天':7}[days[0][1]]||Number(days[0][1]);
  const matches=[...text.matchAll(/第?\s*(\d+(?:\s*[-–—~～至,，、]\s*\d+)*)\s*节/g)];let periods=[];
  for(const match of matches){
    const before=text[match.index-1]||'',after=text[match.index+match[0].length]||'';
    if(/[\d\-–—~～至,，、]/.test(before)||/[\d\-–—~～至]/.test(after))return {weekday,periods:[]};
    const parsed=schoolParsePeriodSet(match[1]);if(!parsed.length)return {weekday,periods:[]};periods.push(...parsed);
  }
  return {weekday,periods:schoolNumberSet(periods)};
}
function schoolSetLabel(values,unit){
  const groups=[];
  for(const value of values){const last=groups.at(-1);if(last&&last[1]+1===value)last[1]=value;else groups.push([value,value]);}
  return groups.map(([start,end])=>start===end?String(start):`${start}–${end}`).join('、')+unit;
}
function schoolMeetingSeasonLabel(meeting,record,text){
  if(meeting.field_states?.season_codes==='conflicting'||meeting.field_states?.xxq_raw==='conflicting')return '';
  const normalized=schoolKnownField(meeting,'season_codes')&&Array.isArray(meeting.season_codes)&&
    meeting.season_codes.length&&meeting.season_codes.every(season=>['秋','冬','春','夏'].includes(season))?meeting.season_codes:[];
  if(normalized.length)return [...new Set(normalized)].join('/');
  const explicit=schoolChineseSeasons(meeting);
  const inText=[...text.matchAll(/秋冬|春夏|秋|冬|春|夏/g)].map(match=>match[0]);
  if(explicit||inText.length)return explicit||[...new Set(inText)].join('/');
  return schoolPlainText(meeting.xxq_raw,'')===''?schoolChineseSeasons(record):'';
}
function schoolMeetingScope(meeting,record,text,seasonLabel=schoolMeetingSeasonLabel(meeting,record,text)){
  const weeks=schoolKnownField(meeting,'week_set')?schoolNumberSet(meeting.week_set):[];
  const alternateWeeks=schoolKnownField(meeting,'week_numbers')?schoolNumberSet(meeting.week_numbers):[];
  const weekText=(schoolHasDisplayField(meeting,'week_annotation_raw')?schoolPlainText(meeting.week_annotation_raw,''):'')||text.match(/(?:第\s*)?\d+(?:\s*[-–—~～至,，、]\s*\d+)*\s*周(?:\s*[（(]?[单双]周?[）)]?)?/)?.[0];
  const weekLabel=weeks.length?schoolSetLabel(weeks,'周'):alternateWeeks.length?schoolSetLabel(alternateWeeks,'周'):weekText||'';
  const paritySource=schoolPlainText(meeting.dsz_raw,'')!==''||meeting.field_states?.dsz_raw==='conflicting'?meeting:record;
  const parityText=text.match(/单双周|单周|双周/)?.[0]||schoolChineseParity(paritySource);
  return [seasonLabel,weekLabel,...(parityText&&!weekLabel.includes(parityText)?[parityText]:[])].filter(Boolean).join(' · ');
}
function schoolTimetableMeetings(result){
  const records=Array.isArray(result?.records)?result.records:[],items=[];
  records.forEach((record,recordIndex)=>{
    if(!record||typeof record!=='object')return;
    let sourceMeetings=Array.isArray(record.meetings)&&record.meetings.length?record.meetings:[{time_raw:record.time_raw,place_raw:record.place_raw,field_states:record.field_states}];
    const encodedBreak=typeof record.time_raw==='string'&&/&lt;br\s*\/?&gt;/i.test(record.time_raw);
    const hasEntityFragments=sourceMeetings.some(value=>{const text=typeof value==='string'?value:value?.time_raw;return typeof text==='string'&&(/&lt\s*$/i.test(text)||/^\s*br\s*\/?&gt\s*$/i.test(text));});
    const hasNormalizedPosition=sourceMeetings.some(value=>value&&typeof value==='object'&&(
      ['weekday','day_of_week'].some(key=>schoolKnownField(value,key)&&Number.isInteger(value[key])&&value[key]>=1&&value[key]<=7)||
      ['periods','period_indices'].some(key=>schoolKnownField(value,key)&&schoolNumberSet(value[key]).length)));
    // Parser v3 split encoded-break entity semicolons. Use intact source text,
    // never the fragments, and retain any reliable normalized positioning.
    if(encodedBreak&&hasEntityFragments&&!hasNormalizedPosition&&schoolKnownField(record,'time_raw'))sourceMeetings=[{time_raw:record.time_raw,place_raw:record.place_raw,field_states:record.field_states}];
    sourceMeetings.forEach((rawMeeting,meetingIndex)=>{
      const meeting=typeof rawMeeting==='string'?{time_raw:rawMeeting}:rawMeeting&&typeof rawMeeting==='object'?rawMeeting:{};
      const sourceTime=schoolKnownField(meeting,'time_raw')?(meeting.time_raw??(sourceMeetings.length===1&&schoolKnownField(record,'time_raw')?record.time_raw:null)):null;
      const segments=schoolMeetingSegments(sourceTime),times=segments.length?segments:[''];
      const placeText=schoolKnownField(meeting,'place_raw')?(meeting.place_raw??(sourceMeetings.length===1&&schoolKnownField(record,'place_raw')?record.place_raw:null)??null):null,places=schoolMeetingSegments(placeText);
      times.forEach((time,segmentIndex)=>{
        const parsed=schoolKnownField(meeting,'time_raw')?schoolParsedMeetingTime(time):{weekday:null,periods:[]};
        const normalizedDay=schoolKnownField(meeting,'weekday')?meeting.weekday:null;
        const alternateDay=schoolKnownField(meeting,'day_of_week')?meeting.day_of_week:null;
        const day=Number.isInteger(normalizedDay)&&normalizedDay>=1&&normalizedDay<=7?normalizedDay:Number.isInteger(alternateDay)&&alternateDay>=1&&alternateDay<=7?alternateDay:null;
        const normalizedPeriods=schoolKnownField(meeting,'periods')?schoolNumberSet(meeting.periods):[];
        const alternatePeriods=schoolKnownField(meeting,'period_indices')?schoolNumberSet(meeting.period_indices):[];
        const blocked=key=>['conflicting','pending_verification'].includes(meeting.field_states?.[key]);
        const weekday=times.length===1&&day?day:blocked('weekday')?null:parsed.weekday;
        const periods=times.length===1&&(normalizedPeriods.length||alternatePeriods.length)?normalizedPeriods.length?normalizedPeriods:alternatePeriods:blocked('periods')?[]:parsed.periods;
        const seasonLabel=schoolMeetingSeasonLabel(meeting,record,time);
        items.push({id:`${recordIndex}-${meetingIndex}-${segmentIndex}`,record,meeting,time,weekday,periods,seasonLabel,
          place:places.length===times.length?places[segmentIndex]:times.length===1?placeText:null,
          scope:schoolMeetingScope(meeting,record,time,seasonLabel)});
      });
    });
  });
  return items;
}
function schoolFormalEnrollmentText(record){
  return schoolKnownField(record,'formal_enrollment_status')?{selected:'正式中选',tentative:'待定',preloaded:'预置安排',cancelled:'已取消'}[record.formal_enrollment_status]||'':'';
}
function schoolMeetingMarkup(item,pending=false){
  const record=item.record,name=record.course_name_raw||record.course_name||record.course_code_raw||'未命名课程';
  const position=[dayNames[item.weekday],item.periods.length?schoolSetLabel(item.periods,'节'):''].filter(Boolean).join(' · '),enrollment=schoolFormalEnrollmentText(record);
  return `<article class="school-meeting${pending?' school-meeting-pending':''}"><strong>${record.presentation_key?`<button type="button" class="school-course-link" data-arrangement-key="${esc(record.presentation_key)}">${schoolTextMarkup(name)}</button>`:schoolTextMarkup(name)}</strong>${item.scope?`<span class="school-meeting-scope">${esc(item.scope)}</span>`:''}${pending?`${position?`<span>${esc(position)}</span>`:''}${item.time?`<span>${schoolTextMarkup(item.time)}</span>`:''}`:''}${schoolPlainText(item.place,'')!==''?`<span class="school-meeting-place">${schoolTextMarkup(item.place)}</span>`:''}${schoolHasDisplayField(record,'instructor_raw')?`<span class="school-meeting-teacher">${schoolTextMarkup(record.instructor_raw)}</span>`:''}${enrollment?`<span class="school-meeting-status">${esc(enrollment)}</span>`:''}</article>`;
}
function schoolTimetableRawMeetings(record){
  if(!Array.isArray(record.meetings)||!record.meetings.length)return '';
  const rows=record.meetings.map((value,index)=>{
    const meeting=typeof value==='string'?{time_raw:value}:value&&typeof value==='object'?value:{};
    const fields=[['time_raw','上课时间'],['place_raw','上课地点'],['week_annotation_raw','周次']].filter(([key])=>schoolHasDisplayField(meeting,key)).map(([key,label])=>`<div><dt>${label}</dt><dd>${schoolTextMarkup(meeting[key])}</dd></div>`).join('');
    const seasons=schoolChineseSeasons(meeting),parity=schoolChineseParity(meeting);
    return fields||seasons||parity?`<li>${record.meetings.length>1?`<span>时段${index+1}</span>`:''}<dl>${fields}${seasons?`<div><dt>分学期</dt><dd>${esc(seasons)}</dd></div>`:''}${parity?`<div><dt>单双周</dt><dd>${esc(parity)}</dd></div>`:''}</dl></li>`:'';
  }).join('');
  return rows?`<ul class="school-timetable-source-meetings">${rows}</ul>`:'';
}
function schoolTimetableMarkup(result,items=schoolTimetableMeetings(result)){
  if(!Array.isArray(result?.records)||!result.records.some(record=>record&&typeof record==='object'))return '<p class="inline-empty">本次未返回可展示课表；这不表示没有课程。</p>';
  const records=[...new Set(items.map(item=>item.record))];
  if(!records.length)return '<p class="inline-empty">当前分学期筛选下没有可展示的时段。</p>';
  const located=items.filter(item=>item.weekday&&item.periods.length),pending=items.filter(item=>!item.weekday||!item.periods.length),periodDisplay=schoolPeriodTimeDisplay({...result,records}),periodTimes=periodDisplay.times;
  const allPeriods=[...new Set(located.flatMap(item=>item.periods))].sort((left,right)=>left-right);
  const periodRows=allPeriods.at(-1)<=30?Array.from({length:allPeriods.at(-1)||0},(_,index)=>index+1):allPeriods;
  const cells=dayNames.slice(1).map((_,index)=>periodRows.map(period=>located.filter(item=>item.weekday===index+1&&item.periods.includes(period))));
  const table=located.length?`<div class="school-timetable-scroll" role="region" aria-label="课表可横向滚动" tabindex="0"><table class="school-timetable"><caption class="sr-only">周课表</caption><thead><tr><th scope="col">节次</th>${dayNames.slice(1).map(day=>`<th scope="col">${day}</th>`).join('')}</tr></thead><tbody>${periodRows.map((period,rowIndex)=>`<tr><th scope="row" class="school-timetable-period">${period}${periodTimes.has(period)?`<br><small class="school-period-clock">${esc(periodTimes.get(period))}</small>`:''}</th>${cells.map((dayCells,dayIndex)=>{
    const matches=dayCells[rowIndex],key=matches.map(item=>item.id).join(',');
    if(rowIndex&&periodRows[rowIndex-1]+1===period&&key&&key===dayCells[rowIndex-1].map(item=>item.id).join(','))return '';
    if(!matches.length)return '<td class="school-timetable-empty" aria-label="本次返回未定位时段" title="本次返回未定位时段"></td>';
    let span=1;while(rowIndex+span<periodRows.length&&periodRows[rowIndex+span]===period+span&&key===dayCells[rowIndex+span].map(item=>item.id).join(','))span+=1;
    return `<td rowspan="${span}" class="school-timetable-occupied" data-weekday="${dayIndex+1}" data-period="${period}"><div class="school-timetable-cell">${matches.map(item=>schoolMeetingMarkup(item)).join('')}</div></td>`;
  }).join('')}</tr>`).join('')}</tbody></table></div>`:'<p class="inline-empty">本次课表尚无可定位到星期和节次的时段。</p>';
  return `<div class="school-timetable-view"><p class="school-timetable-summary">${records.length}条课表记录 · 已定位${located.length}个时段${pending.length?` · 未排入课表${pending.length}个时段`:''}</p>${table}${located.length&&periodDisplay.origin==='public_reference'?`<p class="school-timetable-note">${schoolPeriodReferenceLink(periodDisplay)}</p>`:''}<p class="school-timetable-note">空白时段未返回课程信息。</p>${pending.length?`<section class="school-timetable-pending"><h3>未排入课表的课程</h3><div class="school-timetable-pending-list">${pending.map(item=>schoolMeetingMarkup(item,true)).join('')}</div></section>`:''}</div>`;
}
const schoolOperationNames={profile:'本人资料',academic:'学期成绩',major_academic:'主修历史',graduation_audit:'毕业资格自审',timetable:'我的课表',exams:'考试安排'};
const schoolStatusNames={ok:'已读取',complete:'已读取',complete_within_scope:'本次范围已读取',partial:'部分读取',partial_unknown:'部分读取，覆盖待确认',ready:'会话有效',connected:'会话有效',authenticated:'会话有效',live:'本次查询',historical_cache:'历史缓存',acquired_pending_review:'已获取，内容待核验',cached:'已缓存',unknown:'待确认',captcha_required:'需要验证码',interactive_verification_required:'需要学校交互验证',source_session_expired:'教务会话已失效',authentication_required:'需要重新登录',awaiting_user_login:'需要重新登录',auth_expired:'需要重新登录',user_confirmation_required:'需要本人确认',not_configured:'尚未配置',profile_fields_missing:'适用范围信息缺失',plan_missing:'尚未取得方案',plan_not_found:'未找到对应方案',plan_ambiguous:'方案匹配不唯一',no_match:'未找到对应方案',ambiguous:'方案匹配不唯一'};
Object.assign(schoolStatusNames,{synthetic_fixture:'模拟数据',not_ready:'资料尚未提供',invalid_credentials:'账号或密码不正确',schema_changed:'学校响应格式尚未适配',identity_not_observed:'学校已返回资料但未确认本人身份',route_not_confirmed:'学校接口尚未确认',source_unavailable:'学校连接暂不可用',permission_denied:'学校拒绝本次访问',source_subject_changed:'学校返回的本人身份已变化',source_epoch_changed:'学校会话已变化',source_generation_changed:'学校会话已变化',partial_acquisition:'已读取，覆盖范围待确认',ambiguous_plan:'方案匹配不唯一'});
function schoolStatusText(status){return Object.hasOwn(schoolStatusNames,status)?schoolStatusNames[status]:'未完成或待确认';}
const schoolAccessFailures=['source_session_expired','authentication_required','awaiting_user_login','auth_expired','source_subject_changed','source_epoch_changed','source_generation_changed','permission_denied','user_confirmation_required','interactive_verification_required'];
Object.assign(schoolStatusNames,{local_service_unavailable:'本机服务暂不可用',local_response_invalid:'本机响应不完整或格式无效',invalid_login_challenge:'验证码已失效，请换一张后重试'});
function schoolCoverageText(result){
  const coverage=result.coverage&&typeof result.coverage==='object'&&!Array.isArray(result.coverage)?result.coverage:{};
  const count=coverage.captured_rows,total=coverage.reported_total;
  const countKnown=Number.isInteger(count)&&count>=0,totalKnown=Number.isInteger(total)&&total>=0;
  const conflict=countKnown&&totalKnown&&(count>total||coverage.complete_within_scope===true&&count!==total)||coverage.complete_within_scope===true&&coverage.has_more===true;
  const completeness=conflict?'完整性待确认':coverage.has_more===true?'还有未读取内容':coverage.complete_within_scope===true?'已完整读取当前查询范围':'完整性待确认';
  return `${countKnown?'本次读取 '+count+' 条':'读取条数待确认'} · ${completeness}`;
}
function schoolCaptureTime(value){
  const millis=typeof value==='number'&&Number.isFinite(value)?value*1000:typeof value==='string'&&value.trim()?Date.parse(value):NaN;
  if(!Number.isFinite(millis))return '待确认';const date=new Date(millis);if(!Number.isFinite(date.getTime()))return '待确认';
  return date.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
}
function schoolCredentialForm(reauth){const prefix=reauth?'school-reauth':'school-login';return `<form id="${prefix}-form" class="school-login-form"><div class="field"><label for="${prefix}-username">账号</label><input id="${prefix}-username" name="username" type="text" autocomplete="username" maxlength="64" required ${state.busy||state.schoolBusy?'disabled':''}></div><div class="field"><label for="${prefix}-password">密码</label><input id="${prefix}-password" name="password" type="password" autocomplete="off" maxlength="256" required ${state.busy||state.schoolBusy?'disabled':''}></div>${state.schoolChallengeId?`${notice('请重新输入学号、密码，并填写学校验证码。')}${state.schoolCaptchaUrl?`<img class="school-captcha" src="${esc(state.schoolCaptchaUrl)}" alt="学校登录验证码">`:''}<div class="field"><label for="${prefix}-captcha">验证码</label><input id="${prefix}-captcha" type="text" autocomplete="off" pattern="[A-Za-z0-9]{1,16}" maxlength="16" required ${state.busy||state.schoolBusy?'disabled':''}></div>`:''}<button class="button primary" type="submit" ${state.busy||state.schoolBusy?'disabled':''}>${state.busy||state.schoolBusy?'正在登录…':reauth?'重新验证当前账号':'登录'}</button></form>`;}
function acceptSchoolLoginChallenge(result){
  state.schoolChallengeId=null;state.schoolQueryChallengeId=null;if(state.schoolCaptchaUrl)URL.revokeObjectURL(state.schoolCaptchaUrl);state.schoolCaptchaUrl=null;
  if(typeof result.challenge_id!=='string'||result.challenge_id.length>128||!['image/png','image/jpeg','image/gif'].includes(result.captcha?.content_type)||typeof result.captcha.data_base64!=='string'||result.captcha.data_base64.length>2800000)return;
  try{const bytes=Uint8Array.from(atob(result.captcha.data_base64),char=>char.charCodeAt(0));if(!bytes.length||bytes.length>2*1024*1024)return;state.schoolCaptchaUrl=URL.createObjectURL(new Blob([bytes],{type:result.captcha.content_type}));state.schoolChallengeId=result.challenge_id;}catch{/* Invalid challenge images are not displayed. */}
}
function bindSchoolCredentialForm(reauth){const form=$(`#${reauth?'school-reauth':'school-login'}-form`);form?.addEventListener('submit',event=>{event.preventDefault();schoolLogin(reauth);});}
async function schoolLogin(reauth=false){
  if(state.busy||state.schoolBusy||reauth&&!isAcademicIdentity())return;
  const prefix=reauth?'school-reauth':'school-login';const passwordField=$(`#${prefix}-password`);const username=$(`#${prefix}-username`).value.trim();let password=passwordField.value;passwordField.value='';
  if(!username||!password){message('请输入账号和密码。',true);password='';return;}
  const captchaText=state.schoolChallengeId?$(`#${prefix}-captcha`).value.trim():null;if(state.schoolChallengeId)$(`#${prefix}-captcha`).value='';
  const epoch=state.schoolEpoch,agentEpoch=state.agentEpoch,owner=identityKey(state.auth?.identity);state.busy=true;if(reauth)renderSchool();else renderAuth();
  try{
    const payload={username,password};if(state.schoolChallengeId){payload.challenge_id=state.schoolChallengeId;payload.captcha_text=captchaText;}
    const pending=api(reauth?'/api/school/reauthenticate':state.auth?.academic_login_enabled?'/api/auth/login':'/api/auth/school-login',payload,agentEpoch);password='';payload.password='';const result=await pending;
    if(epoch!==state.schoolEpoch||owner!==identityKey(state.auth?.identity))return;
    if(result.authenticated===true||reauth&&['ok','ready'].includes(result.status)){
      if(reauth){clearPersonalLocal();state.schoolReauth=false;state.schoolChallengeId=null;state.schoolPending=null;state.schoolStatus=result;await refreshAgentStatus();message('当前账号已重新验证，请重新查询。');}
      else{clearWorkspace();state.auth={...state.auth,authenticated:true,identity:result.identity};state.schoolProfile=result.profile||null;state.csrfToken=result.csrf_token;await bootstrap();message('已登录，可直接提问或从左侧查看教务信息。');}
    }else{acceptSchoolLoginChallenge(result);message(schoolStatusText(result.status||result.state)+'，请核对学校认证状态后重试。',true);}
  }catch(error){if(epoch===state.schoolEpoch&&owner===identityKey(state.auth?.identity)){acceptSchoolLoginChallenge({});message(error.code==='local_service_unavailable'?error.message:Object.hasOwn(schoolStatusNames,error.code)?schoolStatusText(error.code)+'，请核对学校认证状态后重试。':'登录未完成，请核对账号、密码或所需验证后重试。',true);}}
  finally{password='';if(epoch===state.schoolEpoch){state.busy=false;if(reauth)renderSchool();else renderAuth();}}
}
function schoolSection(operation=state.schoolOperation){return ['major_academic'].includes(operation)?'academic':operation==='profile'?'graduation_audit':Object.hasOwn(workspaceSchoolPages,operation)?operation:'academic';}
function schoolCurrentYear(){
  const date=new Date().toLocaleDateString('en-CA',{timeZone:'Asia/Shanghai'}).split('-').map(Number);
  const start=date[0]-(date[1]<9?1:0);return `${start}-${start+1}`;
}
async function ensureSchoolQueryContext(){
  if(!isAcademicIdentity()||state.schoolProfile||state.schoolStatus?.query_context||state.schoolBusy||state.busy)return;
  const capture=currentSchoolRequest();state.schoolBusy=true;renderSchool();
  try{const result=await api('/api/school/profile',undefined,capture.agentEpoch);if(sameSchoolRequest(capture)){if(result.profile)state.schoolProfile=result.profile;else await schoolFailure(result.status,capture,null);}}
  catch(error){await schoolFailure(error.code,capture,null);}finally{if(sameSchoolRequest(capture)){state.schoolBusy=false;renderSchool();}}
}
function schoolYearChoices(){
  const configured=state.schoolStatus?.query_context?.available_academic_years;if(agentDataKind()==='synthetic'&&Array.isArray(configured)&&configured.length)return configured.filter(year=>/^20\d{2}-20\d{2}$/.test(year)).slice().sort().reverse();
  const current=Number(schoolCurrentYear().slice(0,4));
  const context=state.schoolStatus?.query_context;
  const cohort=context?.cohort_state==='known'?context.cohort:state.schoolProfile?.field_states?.cohort==='known'?state.schoolProfile.cohort:null;
  const first=Number.isInteger(cohort)&&cohort>=2000&&cohort<=current?cohort:current;
  const years=new Set(Array.from({length:current-first+1},(_,index)=>`${current-index}-${current-index+1}`));
  for(const record of state.schoolResult?.records||[]){const year=String(record.academic_year_raw??'');if(/^20\d{2}-20\d{2}$/.test(year)&&Number(year.slice(5))===Number(year.slice(0,4))+1&&Number(year.slice(0,4))<=current)years.add(year);}
  // Keep a previously selected, valid past year even when the profile is unavailable.
  if(/^20\d{2}-20\d{2}$/.test(state.schoolYear)&&Number(state.schoolYear.slice(5))===Number(state.schoolYear.slice(0,4))+1&&Number(state.schoolYear.slice(0,4))<=current)years.add(state.schoolYear);
  return [...years].sort().reverse();
}
function schoolSeasonChoices(operation=state.schoolOperation){return operation==='exams'?{A:'秋',W:'冬',S:'春',SU:'夏'}:{AW:'秋冬',SS:'春夏'};}
function schoolMatchSeason(operation,season){
  const allowed=schoolSeasonChoices(operation);if(Object.hasOwn(allowed,season))return season;
  return operation==='exams'?({AW:'A',SS:'S'}[season]||'A'):({A:'AW',W:'AW',S:'SS',SU:'SS'}[season]||'AW');
}
function schoolRememberQuery(){
  const year=$('#school-year')?.value,season=$('#school-season')?.value;
  if(year)state.schoolYear=year;if(season)state.schoolSeason=season;
  state.schoolPageQueries??={};state.schoolPageQueries[schoolSection()]={year:state.schoolYear,season:state.schoolSeason};
}
function selectSchoolSection(operation){
  if(state.schoolBusy||state.busy||!Object.hasOwn(workspaceSchoolPages,schoolSection(operation))||!['major_academic','profile',...Object.keys(workspaceSchoolPages)].includes(operation))return;
  schoolRememberQuery();state.schoolOperation=operation;
  const query=state.schoolPageQueries?.[schoolSection()];state.schoolYear=query?.year||schoolCurrentYear();state.schoolSeason=schoolMatchSeason(operation,query?.season||(operation==='exams'?'A':'AW'));
  clearMessage();renderSchool();
}

function schoolCompletionStatusText(value){return typeof value==='string'?({passed:'通过',failed:'未通过',in_progress:'在修',pending_recognition:'待认定',replaced:'被替代'}[value]||value):value;}
function schoolFieldMarkup(record,key){
  let value=record[key];
  if(key==='completion_status_raw')value=schoolCompletionStatusText(value);
  return schoolHasDisplayField(record,key)?schoolTextMarkup(value):'<span class="muted">—</span>';
}
function schoolCourseMarkup(record){
  const name=schoolHasDisplayField(record,'course_name_raw')?record.course_name_raw:schoolHasDisplayField(record,'course_code_raw')?record.course_code_raw:'未命名课程';
  return `${schoolTextMarkup(name)}${schoolHasDisplayField(record,'course_name_raw')&&schoolHasDisplayField(record,'course_code_raw')?`<small class="muted">${schoolTextMarkup(record.course_code_raw)}</small>`:''}`;
}
function schoolGradeRows(result){
  const records=Array.isArray(result.records)?result.records:[];
  const query=state.schoolGradeSearch.trim().toLocaleLowerCase();
  const filtered=records.filter(record=>(!state.schoolGradeYear||schoolHasDisplayField(record,'academic_year_raw')&&record.academic_year_raw===state.schoolGradeYear)&&(!query||['course_name_raw','course_code_raw'].some(key=>schoolHasDisplayField(record,key)&&schoolPlainText(record[key],'').toLocaleLowerCase().includes(query))));
  if(state.schoolGradeSort==='name')filtered.sort((a,b)=>schoolPlainText(a.course_name_raw,'').localeCompare(schoolPlainText(b.course_name_raw,''),'zh-CN'));
  const key={grade:'grade_raw',grade_asc:'grade_raw',credits:'nominal_credits',credits_asc:'nominal_credits'}[state.schoolGradeSort];
  if(key){const value=row=>schoolHasDisplayField(row,key)&&/^-?\d+(?:\.\d+)?$/.test(String(row[key]).trim())?Number(row[key]):null;filtered.sort((a,b)=>{const x=value(a),y=value(b);return x===null?(y===null?0:1):y===null?-1:state.schoolGradeSort.endsWith('_asc')?x-y:y-x;});}
  return filtered;
}
function schoolGradeTable(result){
  const records=Array.isArray(result.records)?result.records:[],filtered=schoolGradeRows(result),size=15;
  const pages=Math.max(1,Math.ceil(filtered.length/size));state.schoolGradePage=Math.min(Math.max(1,state.schoolGradePage),pages);
  const start=(state.schoolGradePage-1)*size,end=Math.min(start+size,filtered.length);
  const columns=[['course_name_raw','课程名称'],... [['grade_raw','成绩'],['grade_points_raw','绩点'],['nominal_credits','学分'],['academic_year_raw','学年'],['term_raw','学期'],['completion_status_raw','修读状态']].filter(([key])=>records.some(record=>schoolHasDisplayField(record,key)))];
  const table=filtered.length?`<div class="table-wrap"><table class="data-table school-data-table school-grade-record-table"><caption class="sr-only">${esc(schoolOperationNames[result.operation])}，仅在已读取的 ${records.length} 条记录中展示</caption><thead><tr>${columns.map(([key,label])=>{const sort={grade_raw:'grade',nominal_credits:'credits'}[key],active=state.schoolGradeSort===sort,ascending=state.schoolGradeSort===sort+'_asc';return sort?`<th scope="col" aria-sort="${active?'descending':ascending?'ascending':'none'}"><button type="button" class="school-sort-button" data-action="school-grade-sort" data-sort="${sort}" aria-label="${label}：${active?'当前降序，点击升序':ascending?'当前升序，点击恢复来源顺序':'点击降序排列'}">${label} <span aria-hidden="true">${active?'↓':ascending?'↑':'↕'}</span></button></th>`:`<th scope="col">${label}</th>`;}).join('')}</tr></thead><tbody>${filtered.slice(start,end).map(record=>`<tr>${columns.map(([key],index)=>`<td>${index===0?`<details class="school-grade-record" ${schoolDisclosure('grade:'+JSON.stringify([result.operation,result.target_term,records.indexOf(record)]))}><summary>${schoolCourseMarkup(record)}</summary><dl><div><dt>选课课号</dt><dd>${schoolFieldMarkup(record,'source_selection_id_raw')}</dd></div></dl></details>`:schoolFieldMarkup(record,key)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`:'<p class="inline-empty">已读取记录中没有匹配项。可清除筛选查看本次结果。</p>';
  return `${table}<div class="school-pagination" role="group" aria-label="已读取成绩的展示分页"><p>${filtered.length?`显示 ${start+1}–${end} 条`:'显示 0 条'} / ${filtered.length} 条${filtered.length!==records.length?`（已读取 ${records.length} 条）`:''}</p>${pages>1?`<div class="button-group"><button class="button small" data-action="school-grade-page" data-page="${state.schoolGradePage-1}" ${state.schoolGradePage<=1?'disabled':''}>上一页</button><span aria-live="polite">${state.schoolGradePage} / ${pages} 页</span><button class="button small" data-action="school-grade-page" data-page="${state.schoolGradePage+1}" ${state.schoolGradePage>=pages?'disabled':''}>下一页</button></div>`:''}</div>`;
}
function schoolEnrollmentUsable(result,courseCode){
  const year=result?.academic_year;
  if(result?.status!=='available'||typeof year!=='string'||!/^20\d{2}-20\d{2}$/.test(year)||Number(year.slice(5))!==Number(year.slice(0,4))+1||!['1','2'].includes(result.season))return false;
  const validRows=['selected','courses','sections'].every(key=>Array.isArray(result[key])&&result[key].every(row=>row&&typeof row==='object'&&!Array.isArray(row)));
  return validRows&&(!courseCode||result.courses.some(row=>row.kcdm===courseCode)&&result.sections.every(row=>!row.kcdm||row.kcdm===courseCode));
}
function schoolEnrollmentMarkup(){
  if(agentDataKind()==='synthetic')return '';
  const p=state.schoolEnrollment,error=state.schoolViewErrors?.[schoolViewKey('enrollment')];
  const header=`<section class="school-result enrollment-preview"><div class="toolbar"><h2>已选教学班</h2><button class="button" type="button" data-action="enrollment-preview" ${state.schoolBusy?'disabled':''}>刷新选课</button></div>${error?`<p class="school-provenance"><span class="school-source-error">${esc(schoolStatusText(error))}，本次更新未完成${p?.status==='available'?'，保留上次数据':''}</span></p>`:''}`;
  if(!p)return header+'</section>';
  if(p.status!=='available')return header+`<p>${esc(schoolStatusText(p.status))}</p></section>`;
  const selected=(p.selected||[]).map(r=>`<tr><td>${esc(r.kcmc)}<small>${esc(r.kcdm)}</small></td><td>${esc(r.xkkh)}</td><td>${esc(r.sxbj==='1'?'已选上':r.sxbj==='0'?'待筛选':'状态待核对')}</td><td>${esc(r.xkzy??'—')}</td><td>${esc(r.jsxm??'—')}</td><td>${esc(r.xxq??'—')}</td><td>${esc(r.sksj??'—')}</td><td>${esc(r.skdd??'—')}</td></tr>`).join('');
  const courses=(p.courses||[]).map(r=>`<button class="button" type="button" data-preview-course="${esc(r.kcdm)}" ${state.schoolBusy?'disabled':''}>${esc(r.kcmc)} · 查看教学班</button>`).join(' ');
  const sections=(p.sections||[]).map(r=>`<tr>${['xkkh','jsxm','xxq','sksj','skdd','kssj','rs','sfxz'].map(k=>`<td>${esc(r[k]??'—')}</td>`).join('')}</tr>`).join('');
  const course=p.queried_course_code&&(p.courses||[]).find(row=>row.kcdm===p.queried_course_code);
  const sectionTitle=course?`${course.kcmc||p.queried_course_code}的教学班`:'教学班';
  const selectedMarkup=selected?schoolPresentationTable(['课程 / 代码','选课课号','页面状态','志愿','教师','学期','上课时间','地点'],[selected],'school-selected-table'):'<p class="inline-empty">本次未返回页面已选教学班。</p>';
  const sectionMarkup=sections?schoolPresentationTable(['选课课号','教师','学期','时间','地点','考试','余量 / 容量','状态原码'],[sections]):'<p class="inline-empty">本次未返回该课程的教学班。</p>';
  return header+`${selectedMarkup}<h3>本类（专业）课程</h3><div>${courses}</div>${p.queried_course_code||sections?`<h3>${esc(sectionTitle)}</h3>${sectionMarkup}`:''}</section>`;
}
async function loadEnrollmentPreview(courseCode){
  if(!isAcademicIdentity()||state.schoolBusy||state.busy||agentDataKind()==='synthetic')return;
  const capture=currentSchoolRequest(),key=schoolViewKey('enrollment'),pending={operation:'enrollment',course_code:courseCode||null};
  clearSchoolQueryCaptcha();
  state.schoolViewErrors??={};state.schoolBusy=true;renderSchool();
  try{
    const result=await api('/api/school/enrollment-preview',courseCode?{course_code:courseCode}:{},capture.agentEpoch);
    if(await applySchoolPresentation(result,pending,capture))clearMessage();
  }catch(error){
    if(!sameSchoolRequest(capture))return;
    state.schoolViewErrors[key]=error.code||'source_unavailable';await schoolFailure(state.schoolViewErrors[key],capture,pending);
  }
  finally{if(sameSchoolRequest(capture)){state.schoolBusy=false;renderSchool();}}
}
function schoolExamDate(value){
  const text=schoolPlainText(value,'');
  const match=text.match(/^(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})(?:日|\b)/);
  if(!match||(text.match(/\d{4}[-/年]\d{1,2}[-/月]\d{1,2}/g)||[]).length!==1)return null;
  const [year,month,day]=match.slice(1).map(Number),date=new Date(Date.UTC(year,month-1,day));
  return date.getUTCFullYear()===year&&date.getUTCMonth()===month-1&&date.getUTCDate()===day?`${year}-${String(month).padStart(2,'0')}-${String(day).padStart(2,'0')}`:null;
}
function schoolExamAgenda(records){
  if(!records.length)return '<p class="inline-empty">本次未返回可展示记录；这不表示没有考试。</p>';
  const groups=new Map();
  for(const record of records){let count=0;for(const [prefix,label] of [['final','期末考试'],['midterm','期中考试']]){
    const timeKey=prefix+'_exam_time_raw',placeKey=prefix+'_exam_place_raw';
    if(!schoolHasDisplayField(record,timeKey)&&!schoolHasDisplayField(record,placeKey))continue;
    const day=schoolHasDisplayField(record,timeKey)?schoolExamDate(record[timeKey]):null,key=day||'日期未明确';
    if(!groups.has(key))groups.set(key,[]);groups.get(key).push({record,timeKey,placeKey,label});count++;
  }if(!count){const day=schoolHasDisplayField(record,'exam_date')?schoolExamDate(record.exam_date):schoolHasDisplayField(record,'exam_time_raw')?schoolExamDate(record.exam_time_raw):null;const key=day||'日期未明确';if(!groups.has(key))groups.set(key,[]);groups.get(key).push({record});}}
  function timeText(item,day){
    const r=item.record;
    if(item.timeKey){
      if(!schoolHasDisplayField(r,item.timeKey))return '来源未提供时间';
      const raw=schoolPlainText(r[item.timeKey],'');
      const compact=raw.match(/^\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?\s*[（(]?\s*(\d{1,2}:\d{2}\s*[-–—至]\s*\d{1,2}:\d{2})\s*[）)]?$/);
      return day!=='日期未明确'&&compact?compact[1]:raw;
    }
    const values=['exam_time_raw',...(day==='日期未明确'?['exam_date']:[]),'start_time','end_time'].filter(k=>schoolHasDisplayField(r,k)).map(k=>schoolPlainText(r[k],''));
    return values.join(' ')||'来源未提供考试时间';
  }
  function startMinutes(item){
    const record=item.record,key=item.timeKey||(schoolHasDisplayField(record,'start_time')?'start_time':'exam_time_raw');
    if(!schoolHasDisplayField(record,key))return 1440;
    const raw=schoolPlainText(record[key],'').trim();
    const match=raw.match(/^(?:\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?\s*[（(]?\s*)?([01]?\d|2[0-3]):([0-5]\d)(?:\s*[-–—至]\s*(?:[01]?\d|2[0-3]):[0-5]\d\s*[）)]?)?$/);
    return match?Number(match[1])*60+Number(match[2]):1440;
  }
  for(const [day,items] of groups){
    if(day!=='日期未明确')items.sort((a,b)=>startMinutes(a)-startMinutes(b));
  }
  return `<div class="exam-agenda exam-agenda-compact">${[...groups].sort(([a],[b])=>a==='日期未明确'?1:b==='日期未明确'?-1:a.localeCompare(b)).map(([day,items])=>`<section class="exam-day"><h3>${esc(day)}</h3><ul>${items.map(item=>{const placeKey=item.placeKey||'place_raw';return `<li class="exam-compact-row"><span class="exam-compact-time" aria-label="考试时间">${esc(timeText(item,day))}</span><strong>${schoolCourseMarkup(item.record)}</strong><span class="exam-compact-type">${esc(item.label||'类型未提供')}</span><span class="exam-compact-place" aria-label="考试地点">${schoolHasDisplayField(item.record,placeKey)?schoolFieldMarkup(item.record,placeKey):'来源未提供地点'}</span></li>`;}).join('')}</ul></section>`).join('')}</div>`;
}

// Session-only presentation snapshots. Raw source records are never rewritten.
function resetSchoolPresentation(){state.schoolViews={};state.schoolViewErrors={};state.schoolModuleFailed=false;state.schoolArrangementKey='';state.schoolSeasonFilter='';state.schoolDisclosures={};state.schoolOpenCourses={};state.schoolPageQueries={};}
function schoolDisclosure(key,defaultOpen=false){return `data-school-disclosure="${esc(key)}" ${(state.schoolDisclosures?.[key]??defaultOpen)?'open':''}`;}
function schoolViewKey(operation,term=''){return operation+':'+term;}
function schoolView(operation,term=''){return state.schoolViews?.[schoolViewKey(operation,term)]||null;}
function schoolUsable(result){return result&&['ok','complete','complete_within_scope','partial','partial_unknown','partial_acquisition'].includes(result.status)&&Array.isArray(result.records);}
function schoolSourceLine(result,operation,term=''){
  const error=state.schoolViewErrors?.[schoolViewKey(operation,term)],parts=[];
  if(result){
    const captured=schoolCaptureTime(result.captured_at);if(captured!=='待确认')parts.push(`更新于 ${captured}`);
    if(result.data_source==='historical_cache')parts.push('历史缓存');
    if(!['ok','complete','complete_within_scope'].includes(result.status))parts.push(schoolStatusText(result.status));
    if(operation!=='profile'){const coverage=schoolCoverageText(result);if(!coverage.endsWith('已完整读取当前查询范围'))parts.push(coverage);}
  }
  return parts.length||error?`<p class="school-provenance">${esc(parts.join(' · '))}${error?`<span class="school-source-error">${esc(schoolStatusText(error))}，本次更新未完成${result?'，保留上次数据':''}</span>`:''}</p>`:'';
}

function schoolAuditStatus(row){return schoolHasDisplayField(row,'completion_status_raw')?schoolCompletionStatusText(row.completion_status_raw):row.completion_status==='in_progress'?'在修':'未明确';}
function schoolPresentationTable(headers,rows,className=''){return `<div class="table-wrap"><table class="data-table school-data-table ${className}"><thead><tr>${headers.map(x=>`<th scope="col">${esc(x)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`;}

function schoolModuleCourses(records,key){
  if(!records.length)return '<p class="inline-empty">本模块未返回直接归属的课程记录。</p>';
  return schoolPresentationTable(['课程','学分','自审成绩','自审状态'],records.map((r,index)=>{
    const extra=['requirement_type_raw','study_note_raw','substitution_raw','nonduplicated_recognition_credits_raw'].filter(k=>schoolHasDisplayField(r,k));
    const labels={requirement_type_raw:'选必修',study_note_raw:'修读备注',substitution_raw:'替代说明',nonduplicated_recognition_credits_raw:'不重复认定学分'};
    const name=schoolCourseMarkup(r),detail=extra.length?`<details class="school-recognition-detail" ${schoolDisclosure('recognition:'+key+':'+index)}><summary>${name}</summary><dl>${extra.map(k=>`<div><dt>${labels[k]}</dt><dd>${schoolFieldMarkup(r,k)}</dd></div>`).join('')}</dl></details>`:name;
    return `<tr class="school-course-row"><td>${detail}</td><td>${schoolFieldMarkup(r,'nominal_credits')}</td><td>${schoolFieldMarkup(r,'grade_raw')}</td><td>${esc(schoolAuditStatus(r))}</td></tr>`;
  }),'school-module-courses');
}
function schoolModuleMarkup(audit,modules){
  if(!audit)return '<p class="inline-empty">点击“更新培养进度”，读取模块要求与本人完成情况。</p>';
  if(!schoolUsable(audit))return '<p class="inline-empty">本次没有取得可用的自审记录。</p>';
  const courses=audit.records.filter(r=>r.node_kind==='course'),visited=new Set();let sequence=0;
  function moduleRow(r,depth=0){
    if(visited.has(r.node_id))return '';visited.add(r.node_id);
    const own=!state.schoolModuleFailed||['不通过','未通过'].includes(schoolAuditStatus(r));
    const records=courses.filter(c=>c.parent_node_id===r.node_id),key='module-courses:'+r.node_id,id='school-module-courses-'+sequence++,open=state.schoolOpenCourses?.[key]===true;
    const title=records.length?`<button type="button" class="school-course-toggle" data-course-disclosure="${esc(key)}" aria-expanded="${open}" aria-controls="${id}"><span>${schoolTextMarkup(r.course_name_raw)}</span></button>`:schoolTextMarkup(r.course_name_raw);
    const row=own?`<tr class="${depth===0?'school-module-root':''}"><td style="padding-left:${12+Math.min(depth,8)*16}px">${title}</td><td>${schoolFieldMarkup(r,'earned_credits_raw')} / ${schoolFieldMarkup(r,'required_credits_raw')}</td><td>${schoolFieldMarkup(r,'completed_courses_raw')} / ${schoolFieldMarkup(r,'required_courses_raw')}</td><td>${esc(schoolAuditStatus(r))}${schoolHasDisplayField(r,'nonduplicated_recognition_credits_raw')?`<details ${schoolDisclosure('module:'+r.node_id)}><summary>认定说明</summary>不重复认定学分：${schoolFieldMarkup(r,'nonduplicated_recognition_credits_raw')}</details>`:''}</td></tr>${records.length?`<tr class="school-module-expanded" id="${id}" ${open?'':'hidden'}><td colspan="4"><div class="school-module-course-body">${schoolModuleCourses(records,key)}</div></td></tr>`:''}`:'';
    return row+[...modules.values()].filter(c=>c.parent_node_id===r.node_id).map(c=>moduleRow(c,depth+1)).join('');
  }
  let rows=[...modules.values()].filter(r=>!modules.has(r.parent_node_id)).map(r=>moduleRow(r)).join('');
  // Malformed/cyclic parent links must not hide source modules or their records.
  rows+=[...modules.values()].filter(r=>!visited.has(r.node_id)).map(r=>moduleRow(r)).join('');
  const unmatched=courses.filter(r=>!modules.has(r.parent_node_id));
  const unknown=unmatched.length?`<details class="school-unassigned-courses" ${schoolDisclosure('unassigned-audit-courses')}><summary>归属未明确的课程记录（${unmatched.length}）</summary>${schoolModuleCourses(unmatched,'unassigned')}</details>`:'';
  return `<div class="school-module-data"><label class="school-inline-check"><input id="school-module-failed" type="checkbox" ${state.schoolModuleFailed?'checked':''}>仅看未通过模块</label><div class="school-module-list">${rows?schoolPresentationTable(['培养模块','已获 / 要求学分','完成 / 要求门数','学校自审结果'],[rows],'school-module-table'):'<p class="inline-empty">暂无符合条件的模块。</p>'}</div>${unknown}</div>`;
}
function schoolProgressMarkup(busy){
  const audit=schoolView('graduation_audit','ALL-HISTORY'),modules=new Map((audit?.records||[]).filter(r=>r.node_kind==='module').map(r=>[r.node_id,r]));
  return `<section class="school-result"><div class="toolbar"><h2>培养模块</h2>${agentDataKind()==='synthetic'?'<span>当前模拟资料未提供毕业自审</span>':`<button class="button primary" data-read-operation="graduation_audit" ${busy?'disabled':''}>更新培养进度</button>`}</div>${schoolSourceLine(audit,'graduation_audit','ALL-HISTORY')}${agentDataKind()==='synthetic'&&!audit?'':schoolModuleMarkup(audit,modules)}</section>`;
}
function schoolTermForm(label,busy){
  const section=schoolSection();
  return `<form id="school-query-form" class="school-query-form"><input id="school-operation" type="hidden" value="${esc(section)}"><div class="field"><label for="school-year">学年</label><select id="school-year" ${busy?'disabled':''}>${schoolYearChoices().map(y=>`<option ${y===state.schoolYear?'selected':''}>${y}</option>`).join('')}</select></div><div class="field"><label for="school-season">学期</label><select id="school-season" ${busy?'disabled':''}>${Object.entries(schoolSeasonChoices(section)).map(([v,label])=>`<option value="${v}" ${v===state.schoolSeason?'selected':''}>${label}</option>`).join('')}</select></div><button class="button primary" ${busy?'disabled':''}>${busy?'正在读取…':label}</button></form>`;
}
function schoolGradeWorkspaceMarkup(busy){
  const operation='academic',term=state.schoolYear+'-'+state.schoolSeason,result=schoolView(operation,term);
  return `<div class="school-grade-toolbar">${schoolTermForm('查询成绩',busy)}<div class="field school-grade-search-field"><label for="school-grade-search">搜索已读取课程</label><input id="school-grade-search" type="search" maxlength="100" value="${esc(state.schoolGradeSearch)}" placeholder="课程名称或代码"></div></div><section class="school-result school-grade-workspace">${schoolSourceLine(result,operation,term)}${schoolUsable(result)?schoolGradeTable(result):'<p class="inline-empty">当前范围尚未读取成绩。</p>'}</section>`;
}

function schoolTimetableWorkspaceMarkup(busy){
  const term=state.schoolYear+'-'+state.schoolSeason,timetable=schoolView('timetable',term),all=schoolUsable(timetable)?timetable.records:[];
  const rendered={...timetable,records:all.map((record,index)=>({...record,presentation_key:String(index)}))};
  const meetings=schoolTimetableMeetings(rendered).filter(item=>!state.schoolSeasonFilter||!item.seasonLabel||item.seasonLabel.includes(state.schoolSeasonFilter));
  const selected=meetings.find(item=>item.record.presentation_key===state.schoolArrangementKey)?.record;
  return `${schoolTermForm('查询课表',busy)}<section class="school-result school-timetable-workspace"><div class="toolbar"><h2>周课表</h2><label class="school-season-control" for="school-season-filter">分学期<select id="school-season-filter"><option value="">全部（含未知）</option>${(state.schoolSeason==='AW'?['秋','冬']:['春','夏']).map(v=>`<option ${state.schoolSeasonFilter===v?'selected':''}>${v}</option>`).join('')}</select></label></div>${schoolSourceLine(timetable,'timetable',term)}${schoolUsable(timetable)?schoolTimetableMarkup(rendered,meetings):'<p class="inline-empty">当前学期尚未读取课表。</p>'}${selected?schoolTimetableDetailMarkup(selected):''}</section>`;
}
function schoolTimetableDetailMarkup(record){
  const singleMeeting=Array.isArray(record.meetings)&&record.meetings.length===1?record.meetings[0]:null;
  const fields=[['course_code_raw','课程代码'],['source_selection_id_raw','选课课号'],['instructor_raw','教师'],['time_raw','上课时间'],['place_raw','地点'],['term_raw','学期'],['selection_status_raw','页面状态']].filter(([key])=>schoolHasDisplayField(record,key)&&!(['time_raw','place_raw'].includes(key)&&singleMeeting&&schoolHasDisplayField(singleMeeting,key)&&schoolPlainText(record[key],'')===schoolPlainText(singleMeeting[key],'')));
  return `<section class="school-arrangement-detail"><h3>${schoolTextMarkup(record.course_name_raw)}</h3><dl class="school-detail-fields">${fields.map(([key,label])=>`<div><dt>${label}</dt><dd>${schoolFieldMarkup(record,key)}</dd></div>`).join('')}</dl>${schoolTimetableRawMeetings(record)}</section>`;
}
function schoolExamWorkspaceMarkup(busy){
  const term=state.schoolYear+'-'+state.schoolSeason,source=schoolView('exams',term);
  return `${schoolTermForm('查询考试',busy)}<section class="school-result school-exam-workspace">${schoolSourceLine(source,'exams',term)}${schoolUsable(source)?schoolExamAgenda(source.records):'<p class="inline-empty">当前分学期尚未读取考试。</p>'}</section>`;
}

function schoolEnrollmentWorkspaceMarkup(){
  const preview=state.schoolEnrollment;
  return `<div class="school-enrollment-workspace">${preview?.status==='available'?`<p class="field-help">学校当前选课期：${esc(preview.academic_year)} ${preview.season==='1'?'秋冬':preview.season==='2'?'春夏':preview.season} · 更新于 ${esc(schoolCaptureTime(preview.checked_at))}</p>`:''}${agentDataKind()==='synthetic'?'<section class="school-result"><p class="inline-empty">当前模拟资料未提供学校选课预览。</p></section>':schoolEnrollmentMarkup()}</div>`;
}

function renderSchool(){
  const panel=$('#panel-school');if(!isAcademicIdentity()){panel.innerHTML='';return;}
  const busy=state.schoolBusy||state.busy,section=schoolSection(),years=schoolYearChoices();if(!years.includes(state.schoolYear))state.schoolYear=years[0];state.schoolSeason=schoolMatchSeason(section,state.schoolSeason);
  renderWorkspaceNavigation();
  panel.innerHTML=`<div class="page-heading"><div><h1>${workspaceSchoolPages[section].title}</h1></div></div>${busy?'<p role="status" class="school-loading-line">正在读取所需资料…</p>':''}`+({graduation_audit:schoolProgressMarkup,academic:schoolGradeWorkspaceMarkup,timetable:schoolTimetableWorkspaceMarkup,exams:schoolExamWorkspaceMarkup,enrollment:schoolEnrollmentWorkspaceMarkup}[section](busy))+`${state.schoolNeedsCaptcha&&schoolSection(state.schoolPending?.operation)===section?`<section class="school-verification"><h2>学校验证码</h2>${state.schoolCaptchaUrl?`<img class="school-captcha" src="${esc(state.schoolCaptchaUrl)}" alt="学校图片验证码">`:'<p>验证码图片尚未载入。</p>'}<form id="school-captcha-form"><label for="school-captcha-text">验证码</label><div class="button-group"><input id="school-captcha-text" type="text" autocomplete="off" pattern="[A-Za-z0-9]{1,16}" maxlength="16" required ${busy?'disabled':''}><button class="button primary" ${busy||!state.schoolQueryChallengeId?'disabled':''}>提交并重试查询</button><button type="button" class="button" data-action="school-captcha-refresh" ${busy?'disabled':''}>换一张</button></div></form></section>`:''}${state.schoolReauth?`<section class="school-verification"><h2>重新登录教务</h2><p class="field-help">请验证当前账号。切换账号请先退出。</p>${schoolCredentialForm(true)}</section>`:''}`;
  document.querySelectorAll('details[data-school-disclosure]').forEach(detail=>detail.addEventListener('toggle',()=>{if(detail.isConnected){state.schoolDisclosures??={};state.schoolDisclosures[detail.dataset.schoolDisclosure]=detail.open;}}));
  document.querySelectorAll('[data-course-disclosure]').forEach(button=>button.addEventListener('click',()=>{
    const open=button.getAttribute('aria-expanded')!=='true';state.schoolOpenCourses??={};state.schoolOpenCourses[button.dataset.courseDisclosure]=open;
    button.setAttribute('aria-expanded',String(open));document.getElementById(button.getAttribute('aria-controls')).hidden=!open;
  }));
  $('#school-query-form')?.addEventListener('submit',event=>{event.preventDefault();schoolRememberQuery();schoolLoadPresentation(section);});
  document.querySelectorAll('[data-read-operation]').forEach(button=>button.addEventListener('click',()=>schoolLoadPresentation(button.dataset.readOperation)));
  $('#school-module-failed')?.addEventListener('change',event=>{state.schoolModuleFailed=event.target.checked;renderSchool();});
  document.querySelectorAll('[data-arrangement-key]').forEach(button=>button.addEventListener('click',()=>{state.schoolArrangementKey=button.dataset.arrangementKey;renderSchool();$('.school-arrangement-detail')?.scrollIntoView?.({block:'nearest'});}));
  $('#school-season-filter')?.addEventListener('change',event=>{state.schoolSeasonFilter=event.target.value;renderSchool();});
  for(const id of ['#school-year','#school-season'])$(id)?.addEventListener('change',()=>{schoolRememberQuery();if(section==='timetable'){state.schoolArrangementKey='';state.schoolSeasonFilter='';}if(section==='academic')state.schoolGradePage=1;renderSchool();});
  const filterGrades=event=>{if(event.isComposing)return;const input=event.target,start=input.selectionStart,end=input.selectionEnd;state.schoolGradeSearch=input.value;state.schoolGradePage=1;renderSchool();const next=$('#school-grade-search');next?.focus();if(start!==null&&start!==undefined)next?.setSelectionRange?.(start,end);};
  $('#school-grade-search')?.addEventListener('input',filterGrades);
  $('#school-grade-search')?.addEventListener('compositionend',filterGrades);
  $('[data-action="enrollment-preview"]')?.addEventListener('click',()=>loadEnrollmentPreview());
  document.querySelectorAll('[data-preview-course]').forEach(button=>button.addEventListener('click',()=>loadEnrollmentPreview(button.dataset.previewCourse)));
  $('#school-captcha-form')?.addEventListener('submit',event=>{event.preventDefault();submitSchoolCaptcha();});bindSchoolCredentialForm(true);
}
async function schoolLoadPresentation(kind){
  if(!isAcademicIdentity()||state.schoolBusy||state.busy)return;
  if(!['academic','major_academic','graduation_audit','profile','timetable','exams'].includes(kind))return;
  const capture=currentSchoolRequest(),term=state.schoolYear+'-'+state.schoolSeason;
  if(!['profile','graduation_audit','major_academic'].includes(kind)&&(!/^20\d{2}-20\d{2}$/.test(state.schoolYear)||Number(state.schoolYear.slice(5))!==Number(state.schoolYear.slice(0,4))+1||!Object.hasOwn(schoolSeasonChoices(kind),state.schoolSeason)))return;
  const pending={operation:kind,target_term:['graduation_audit','major_academic'].includes(kind)?'ALL-HISTORY':kind==='profile'?'':term,force_refresh:true};
  clearSchoolQueryCaptcha();
  state.schoolViews??={};state.schoolViewErrors??={};state.schoolBusy=true;renderSchool();
  try{
    const result=await api(kind==='profile'?'/api/school/profile':'/api/school/sync',kind==='profile'?undefined:pending,capture.agentEpoch);
    if(await applySchoolPresentation(result,pending,capture))clearMessage();
  }catch(error){
    if(!sameSchoolRequest(capture))return;
    state.schoolViewErrors[schoolViewKey(kind,pending.target_term)]=error.code||'source_unavailable';
    await schoolFailure(error.code,capture,pending);
  }finally{if(sameSchoolRequest(capture)){state.schoolBusy=false;renderSchool();}}
}
async function applySchoolPresentation(result,pending,capture){
  if(!sameSchoolRequest(capture))return false;
  const {operation,target_term}=pending,key=schoolViewKey(operation,target_term);
  state.schoolViews??={};state.schoolViewErrors??={};
  if(schoolAccessFailures.includes(result?.status)||['captcha_required','verification_required'].includes(result?.status)){
    state.schoolViewErrors[key]=result.status;
    await schoolFailure(result.status,capture,pending);return false;
  }
  if(operation==='enrollment'){
    if(!schoolEnrollmentUsable(result,pending.course_code)){
      const status=result?.status;
      state.schoolViewErrors[key]=typeof status==='string'&&status!=='available'?status:'schema_changed';
      await schoolFailure(state.schoolViewErrors[key],capture,pending);return false;
    }
    state.schoolEnrollment={...result,queried_course_code:pending.course_code||null};
    delete state.schoolViewErrors[key];clearSchoolQueryCaptcha();return true;
  }
  if(!result||operation!=='profile'&&(result.operation!==operation||result.target_term!==target_term)){
    state.schoolViewErrors[key]='schema_changed';return false;
  }
  if(!schoolUsable(result)&&!(operation==='profile'&&result.profile)){
    state.schoolViewErrors[key]=['ok','complete','complete_within_scope'].includes(result.status)?'schema_changed':result.status||'source_unavailable';return false;
  }
  state.schoolViews[key]={...result,operation,target_term};delete state.schoolViewErrors[key];
  if(operation==='profile')state.schoolProfile=result.profile;
  state.schoolResult=state.schoolViews[key];clearSchoolQueryCaptcha();return true;
}

function currentSchoolRequest(){return {epoch:state.schoolEpoch,agentEpoch:state.agentEpoch,owner:identityKey(state.auth?.identity)};}
function sameSchoolRequest(capture){return capture.epoch===state.schoolEpoch&&capture.owner===identityKey(state.auth?.identity)&&isAcademicIdentity();}
async function schoolFailure(status,capture,pending){
  if(!sameSchoolRequest(capture))return;
  if(status==='local_service_unavailable'){message('暂时无法连接本机服务，请确认服务正在运行后重试。',true);return;}
  if(status==='local_response_invalid'){message('本机服务返回的数据不完整或格式无效，请重试。',true);return;}
  if(['captcha_required','verification_required'].includes(status)){
    state.schoolNeedsCaptcha=true;state.schoolPending=pending;
    message(schoolStatusText(status)+'，请完成页面提示后重试。',true);
    await loadSchoolCaptcha(capture);return;
  }
  else if(['source_session_expired','authentication_required','awaiting_user_login','auth_expired','user_confirmation_required','interactive_verification_required'].includes(status)){clearPersonalLocal();state.schoolProfile=null;state.schoolReauth=true;state.schoolEnrollment=null;state.schoolResult=null;state.schoolPending=null;state.schoolNeedsCaptcha=false;acceptSchoolLoginChallenge({});}
  else if(['source_subject_changed','source_epoch_changed','source_generation_changed','permission_denied'].includes(status)){clearPersonalLocal();state.schoolProfile=null;state.schoolEnrollment=null;state.schoolResult=null;state.schoolPending=null;state.schoolNeedsCaptcha=false;acceptSchoolLoginChallenge({});message('本人来源或访问权限已变化，请退出后重新登录。',true);return;}
  message(schoolStatusText(status)+'，请完成页面提示后重试。',true);
}
async function loadSchoolCaptcha(capture=currentSchoolRequest()){
  if(!isAcademicIdentity())return;
  const pending=state.schoolPending;
  state.schoolQueryChallengeId=null;
  if(state.schoolCaptchaUrl)URL.revokeObjectURL(state.schoolCaptchaUrl);
  state.schoolCaptchaUrl=null;renderSchool();
  try{
    const response=await request('/api/school/captcha',undefined,capture.agentEpoch),blob=await response.blob();
    if(!sameSchoolRequest(capture)||state.schoolPending!==pending)return;
    const challengeId=response.headers?.get('X-School-Captcha-Id');
    if(!['image/png','image/jpeg','image/gif'].includes(blob.type)||typeof challengeId!=='string'||!challengeId||challengeId.length>128)throw Error('invalid image');
    if(state.schoolCaptchaUrl)URL.revokeObjectURL(state.schoolCaptchaUrl);
    state.schoolCaptchaUrl=URL.createObjectURL(blob);state.schoolQueryChallengeId=challengeId;state.schoolNeedsCaptcha=true;renderSchool();
  }catch(error){
    if(!sameSchoolRequest(capture)||state.schoolPending!==pending)return;
    if(schoolAccessFailures.includes(error.code)){
      await schoolFailure(error.code,capture,state.schoolPending);renderSchool();
    }else message('验证码图片读取失败，请重试。',true);
  }
}
function clearSchoolQueryCaptcha(){
  state.schoolNeedsCaptcha=false;state.schoolPending=null;state.schoolQueryChallengeId=null;
  if(state.schoolCaptchaUrl)URL.revokeObjectURL(state.schoolCaptchaUrl);
  state.schoolCaptchaUrl=null;
}
async function submitSchoolCaptcha(){
  if(!isAcademicIdentity()||state.schoolBusy||!state.schoolPending||!state.schoolQueryChallengeId)return;
  const text=$('#school-captcha-text').value.trim();$('#school-captcha-text').value='';if(!text)return;
  const capture=currentSchoolRequest(),pending=state.schoolPending;state.schoolBusy=true;renderSchool();
  try{
    const result=await api('/api/school/captcha',{text,challenge_id:state.schoolQueryChallengeId},capture.agentEpoch);
    if(!sameSchoolRequest(capture))return;
    if(!['captcha_required','verification_required'].includes(result?.status)){
      clearSchoolQueryCaptcha();
    }
    if(await applySchoolPresentation(result,pending,capture))message('已完成验证并更新数据。');
  }catch(error){
    if(!sameSchoolRequest(capture))return;
    state.schoolViewErrors??={};state.schoolViewErrors[schoolViewKey(pending.operation,pending.target_term)]=error.code||'source_unavailable';
    await schoolFailure(error.code,capture,pending);
  }finally{if(sameSchoolRequest(capture)){state.schoolBusy=false;renderSchool();}}
}
async function mockLogin(profileId) {
  if(state.busy)return;state.busy=true;renderAuth();
  try{const result=await api('/api/auth/mock-login',{profile_id:profileId});clearWorkspace();state.auth={...state.auth,authenticated:true,identity:result.identity};state.csrfToken=result.csrf_token;await bootstrap();clearMessage();}catch(error){message(error.message,true);}finally{state.busy=false;renderAuth();}
}
async function logout() {
  resetAgentDraftSync();clearAgentConversation();clearSchoolWorkspace();state.busy=true;renderAssistant();if(isAcademicIdentity())renderSchool();
  try{await api('/api/auth/logout',{});clearWorkspace();state.auth={...(state.auth||{}),authenticated:false};renderAuth();clearMessage();message('已退出，服务端会话已撤销。');}catch(error){message(error.message,true);}finally{state.busy=false;renderAssistant();}
}
async function bootstrap() {
  $('#loading').hidden=false;$('#boot-error').hidden=true;
  try{
    const session=await api('/api/auth/session');
    if(state.auth?.identity && identityKey(state.auth.identity)!==identityKey(session.identity))clearWorkspace();
    state.auth=session;
    if(!state.auth.authenticated){clearWorkspace();renderAuth();$('#loading').hidden=true;return;}
    state.csrfToken=state.auth.csrf_token;await refreshWorkspace();if(!isAcademicIdentity())await refreshAgentStatus();renderAuth();renderAssistant();renderPlanning();if(!isAcademicIdentity()){renderAudit();renderSources();}renderSaved();renderSchool();switchTab(state.tab);if(!isAcademicIdentity()||personalChatReady())await refreshAgentHistory();if(isAcademicIdentity()&&personalChatReady())await refreshAgentModels(false);await restoreAgentDraft();$('#loading').hidden=true;
  }catch(error){$('#loading').hidden=true;$('#boot-error').hidden=false;$('#boot-error-text').textContent=`${error.message}。请确认本地 Python 服务已启动，然后重试。`;}
}
$('.nav-tabs').addEventListener('click',event=>{const button=event.target.closest('[data-tab]');if(button){clearMessage();switchTab(button.dataset.tab);}});
$('.nav-tabs').addEventListener('keydown',event=>{if(!['ArrowRight','ArrowLeft','ArrowUp','ArrowDown','Home','End'].includes(event.key))return;event.preventDefault();const tabs=$$('.nav-tab').filter(tab=>!tab.hidden);let index=tabs.indexOf(document.activeElement);if(event.key==='Home')index=0;else if(event.key==='End')index=tabs.length-1;else index=(index+(event.key==='ArrowRight'||event.key==='ArrowDown'?1:-1)+tabs.length)%tabs.length;switchTab(tabs[index].dataset.tab);tabs[index].focus();});
$('#retry-bootstrap').addEventListener('click',bootstrap);
$('#logout').addEventListener('click',logout);
$('#panel-saved').addEventListener('change',event=>{
  const input=event.target.closest('input[data-saved-id]');if(!input)return;
  const selected=new Set(state.compareSavedIds);const identifier=Number(input.dataset.savedId);input.checked?selected.add(identifier):selected.delete(identifier);
  state.compareSavedIds=[...selected];$('#compare-saved-button').disabled=selected.size<2||selected.size>3;
});
// Enter opens a closed picker instead of submitting its enclosing query/composer.
$('.app-shell').addEventListener('keydown',event=>{
  const select=event.target.closest('select');
  if(event.key!=='Enter'||!select||select.disabled||select.matches(':open'))return;
  event.preventDefault();
  if(!event.repeat){try{select.showPicker?.();}catch{/* Unsupported pickers retain Space/arrow navigation. */}}
});
$('.app-shell').addEventListener('click',async event=>{
  const modelMenu=$('#agent-model-menu');if(modelMenu?.open&&!event.target.closest('#agent-model-menu'))modelMenu.open=false;
  const button=event.target.closest('[data-action]');if(!button||button.disabled)return;
  switch(button.dataset.action){
    case'mock-login':await mockLogin(button.dataset.profileId);break;
    case'show-school':switchTab('school');break;
    case'school-grade-sort':{const key=button.dataset.sort;if(!['grade','credits'].includes(key))break;state.schoolGradeSort=state.schoolGradeSort===key?key+'_asc':state.schoolGradeSort===key+'_asc'?'source':key;state.schoolGradePage=1;renderSchool();$(`[data-action="school-grade-sort"][data-sort="${key}"]`)?.focus();break;}
    case'school-grade-page':state.schoolGradePage=Number(button.dataset.page)||1;renderSchool();$('#school-grade-search')?.focus();break;
    case'school-grade-reset':state.schoolGradePage=1;state.schoolGradeSearch='';state.schoolGradeYear='';renderSchool();$('#school-grade-search')?.focus();break;
    case'school-captcha-refresh':await loadSchoolCaptcha();break;
    case'agent-models-refresh':await refreshAgentModels(true);break;
    case'agent-starter':fillAgentDraft(agentStarterQuestions[Number(button.dataset.example)]);break;
    case'open-citation-pdf':await openCitationPdf(button);break;
    case'copy-question':await copyMessageText(state.agentTurns[Number(button.dataset.turnIndex)]?.message||'',button.parentElement.querySelector('.copy-status'));break;
    case'regenerate-answer':openMessageDialog(button,'regenerate');break;
    case'share-answer':openMessageDialog(button,'share');break;
    case'copy-answer':await copyAgentAnswer(button);break;
    case'workspace-page':openWorkspacePage(button.dataset.page);break;
    case'new-agent-conversation':if(state.agentBusy||state.agentActionBusy)break;clearAgentConversation();queueAgentDraftSave(true);clearMessage();switchTab('assistant');renderAgentHistory();break;
    case'load-agent-conversation':await loadAgentConversation(button.dataset.conversationId);break;
    case'refresh-agent-history':await refreshAgentHistory();break;
    case'agent-example':chooseAgentExample(Number(button.dataset.example));break;
    case'clear-agent-draft':clearAgentDraft();break;
    case'save-agent-option':await saveAgentOption(button.dataset.optionId);break;
    case'export-agent-option':await exportAgentOption(button.dataset.optionId,button.dataset.format);break;
    case'export-saved':await exportSaved(button.dataset.savedId,button.dataset.format);break;
    case'preview-current-conditions':await previewCurrentConditions();break;
    case'switch-demo':await changeMode('demo');break;
    case'show-sources':switchTab('sources');break;
    case'show-planning':switchTab('planning');break;
    case'select-plan':state.selected=Number(button.dataset.index);renderPlanResults();break;
    case'save-plan':await savePlan();break;
    case'validate-plan':await validatePlan();break;
    case'lock-plan':await lockPlan();break;
    case'export':await exportPlan(button.dataset.format);break;
    case'apply-constraints':await applyConstraints();break;
    case'refresh-audit':await refreshAudit();break;
    case'refresh-saved':await refreshSaved();break;
    case'compare-saved':await compareSaved();break;
    case'import-bundle':await importBundle();break;
    case'reset-demo':await resetDemo();break;
    case'load-saved':await loadSaved(Number(button.dataset.index));break;
    case'revalidate-saved':await revalidateSaved(button.dataset.index===undefined?undefined:Number(button.dataset.index));break;
    case'use-current':try{await useCurrent();}catch(error){message(error.message,true);}break;
  }
});
$('#agent-history-search')?.addEventListener('input',searchAgentHistory);
$('#agent-history-search')?.addEventListener('compositionend',searchAgentHistory);
$('#conversation-history').open=typeof window==='undefined'||window.matchMedia('(min-width: 681px)').matches;

bootstrap();
