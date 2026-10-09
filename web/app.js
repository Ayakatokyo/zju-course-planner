'use strict';
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
const clone = value => JSON.parse(JSON.stringify(value));
const rows = value => Array.isArray(value) ? value : (value?.records || []);
const showValue = value => value === null || value === undefined ? '待确认' : (typeof value === 'object' ? JSON.stringify(value) : String(value));
const state = {auth:null, csrfToken:null, loadedSaved:null, comparison:null, savedComparison:null, compareSavedIds:[], agentDraft:'', previewBusy:false, boot:null, mode:'demo', tab:'assistant', constraints:{}, lastConstraints:null, generated:null, selected:0, audit:null, sourceAudit:null, saved:[], week:1, season:'冬', dirty:false, busy:false};
const statusNames = {pass:['满足','success'],fail:['不满足','danger'],satisfied:['满足','success'],unsatisfied:['不满足','danger'],pending:['待确认','warning'],unknown:['待确认','warning'],valid:['可行','success'],invalid:['不满足','danger'],passed:['满足','success'],failed:['不满足','danger'],needs_confirmation:['待确认','warning'],not_applicable:['不适用',''],candidate_pending_human_review:['待审核','warning']};
function statusBadge(result) {const [name, cls] = statusNames[result] || [showValue(result),'']; return `<span class="badge ${cls}">${esc(name)}</span>`;}
function notice(text, kind = 'warning'){return `<div class="notice ${kind}"><span class="notice-icon" aria-hidden="true">${kind === 'success' ? '✓' : 'ⓘ'}</span><p>${esc(text)}</p></div>`;}
function message(text, error = false) {const element = $('#global-status'); element.textContent=text; element.className=`status-message${error?' error':''}`; element.hidden=false;}
function clearMessage() {$('#global-status').hidden=true;}
function clearWorkspace() {
  Object.assign(state,{mode:'demo',tab:'assistant',agentDraft:'',previewBusy:false,week:1,season:'冬',boot:null,csrfToken:null,loadedSaved:null,comparison:null,savedComparison:null,compareSavedIds:[],constraints:{},lastConstraints:null,generated:null,audit:null,sourceAudit:null,saved:[],selected:0,dirty:false,busy:false});
  $$('.tab-panel').forEach(panel=>{panel.innerHTML='';panel.hidden=true;});
  $('#saved-count').textContent='0';$('#data-mode').value='demo';$('#mode-badge').textContent='synthetic';$('#mode-badge').className='badge synthetic';$('#session-control').hidden=true;$('#dataset-control').hidden=true;
  $('.sidebar').hidden=true;
}
function renderAuth() {
  const auth=state.auth || {};const authenticated=auth.authenticated===true;
  $('.sidebar').hidden=!authenticated;$('#dataset-control').hidden=!authenticated;$('#session-control').hidden=!authenticated;
  $('#auth-panel').hidden=authenticated;
  if(authenticated){$('#identity-label').textContent=`${auth.identity?.name || '合成学生'} · synthetic`;return;}
  $$('.tab-panel').forEach(panel=>panel.hidden=true);
  $('#auth-panel').innerHTML=pageHeading('登录与开发演示','学校认证和个人学业数据源尚未接入。')+
    notice('学校统一身份认证尚未配置；本项目不收集学校密码，也没有本人档案、成绩或选课 API。登录和个人数据接入需要分别确认。')+
    `<div class="section-container auth-card"><h2>${auth.mock_enabled?'本机合成身份演示':'开发演示尚未开启'}</h2><p>${auth.mock_enabled?'选择服务端固定的合成学生，验证本人工作区、方案与导出隔离。这是本机 mock 演示，不是学校身份登录。':'学校登录待获准接入。开发者可显式开启仅连接 synthetic 数据的本机演示；默认不会启用 mock。'}</p>${auth.mock_enabled?`<div class="auth-profile-list">${(auth.mock_profiles||[]).map(profile=>`<button class="button primary" data-action="mock-login" data-profile-id="${esc(profile.profile_id)}" ${state.busy?'disabled':''}>进入 ${esc(profile.name)} 的演示工作区</button>`).join('')}</div>`:'<p class="field-help">仅需验证本机演示时，在本目录执行：</p><pre>python3 -m course_assistant.server --port 8765 --enable-mock</pre>'}<p class="field-help">任意姓名、学号或对象编号都不能改变本人权限。真实 Excel 与截图只作字段参考，无须在此上传。</p></div>`;
}
async function request(path, body) {
  const options={cache:'no-store',credentials:'same-origin'};
  if(body!==undefined){options.method='POST';options.headers={'Content-Type':'application/json'};if(state.csrfToken)options.headers['X-CSRF-Token']=state.csrfToken;options.body=JSON.stringify(body);}
  const response=await fetch(path,options);
  if(!response.ok){let detail;const raw=await response.text();try{detail=JSON.parse(raw);}catch{detail={error:raw};}
    if(response.status===401){clearWorkspace();state.auth={...(state.auth||{}),authenticated:false};renderAuth();}
    throw new Error(detail.error?.message || detail.error || detail.message || `请求失败（${response.status}）`);
  }
  return response;
}
async function api(path,body){return (await request(path,body)).json();}
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
function planName(plan,index=0){return plan?.title || `方案 ${String.fromCharCode(65+index)}`;}
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
  {label:'补齐专业选修',draft:'我想补齐专业选修，优先考虑 AI 和计算材料方向。请先核对适用要求，再比较本学期可选的课表。'},
  {label:'保留体育，周五尽量空闲',draft:'保留已经确认的体育安排，尽量让周五空出来。请确认哪些条件必须保留，再给我两到三种课表选择。'},
  {label:'比较方案，再调整',draft:'请比较两种课表的时间安排和培养要求贡献。我选定教学班后，希望继续调整偏好，并重新核对锁定条件。'}
];
function renderAssistant() {
  const panel=$('#panel-assistant');if(!state.boot){panel.innerHTML='';return;}
  const synthetic=state.mode==='demo';
  const constraints=state.loadedSaved?state.boot.demo?.constraints || {}:state.constraints;
  const names=new Map(rows(demo().courses).map(course=>[course.course_id,course.course_name]));
  const requested=(constraints.requested_course_ids||[]).map(id=>names.get(id)||id);
  const friday={soft:'尽量周五空闲',hard:'周五必须空闲',off:'未设置周五偏好'}[constraints.friday_free] || '周五条件待确认';
  panel.innerHTML=pageHeading('和选课助手一起规划','描述修读目标与时间偏好，逐步确认条件、比较课表。')+
    `<div class="assistant-workspace"><section class="dialogue-composer" aria-label="选课需求草稿"><form id="agent-draft-form"><label for="agent-draft" class="agent-draft-label">你的选课需求</label><textarea id="agent-draft" rows="5" maxlength="4000" placeholder="想补齐专业选修，尽量周五没课，并保留体育安排…" aria-describedby="agent-draft-status"></textarea><div class="agent-examples" role="group" aria-label="需求示例">${agentExamples.map((example,index)=>`<button type="button" class="button small" data-action="agent-example" data-example="${index}">${esc(example.label)}</button>`).join('')}</div><div class="agent-compose-footer"><p id="agent-draft-status" class="field-help">模型待接入，草稿仅保留在此页面，暂不发送。</p><div class="button-group"><button type="button" class="button small" data-action="clear-agent-draft">清空草稿</button><button type="submit" class="button primary" disabled aria-disabled="true">发送需求 · 待接入</button></div></div></form></section><section class="assistant-preview" aria-label="当前合成条件预览"><div class="assistant-preview-heading"><h2>先看一份合成课表</h2><span class="badge synthetic">synthetic</span></div><p>${synthetic?'使用已确认的合成条件，草稿不参与计算。':'原交接资料缺少本人学业和真实教学班；课表预览请使用本人合成演示。'}</p>${synthetic?`<p class="assistant-condition-summary">${state.loadedSaved?'当前正在查看冻结版，预览将重新读取本人当前条件。':`${esc(requested.join('、')||'尚未选择计划课程')}<br>${esc(friday)} · 保留 ${esc((constraints.locked_section_ids||[]).length)} 个锁定教学班`}</p>`:''}<div class="button-group assistant-preview-actions"><button class="button primary" data-action="preview-current-conditions" ${!synthetic||state.busy||state.previewBusy?'disabled':''}>${state.previewBusy?'正在读取并计算…':'用当前合成条件预览课表'}</button>${synthetic?'<button class="button" data-action="show-planning">查看条件与课表</button>':'<button class="button" data-action="switch-demo">切换本人合成演示</button>'}</div></section><details class="assistant-next-steps"><summary>对话如何连接课表与依据</summary><ol><li>描述需求，澄清目标与缺失信息。</li><li>确认必须条件、偏好和锁定安排。</li><li>调用计算工具，形成课表与校验结果。</li><li>比较方案，查看规则与来源依据。</li><li>继续对话调整，保留锁定和版本后重新校验。</li></ol><p class="field-help">此流程保留为 Agent 入口。自然语言解析、模型对话和 RAG 尚未接入；当前预览仅调用已有的合成数据计算，不解析草稿、不接真实资料，也不自动执行学校选课。</p></details></div>`;
  $('#agent-draft').value=state.agentDraft;
  $('#agent-draft').addEventListener('input',event=>{state.agentDraft=event.target.value;});
  $('#agent-draft-form').addEventListener('submit',event=>event.preventDefault());
}
function chooseAgentExample(index){const example=agentExamples[index];if(!example)return;state.agentDraft=example.draft;$('#agent-draft').value=state.agentDraft;$('#agent-draft').focus();}
function clearAgentDraft(){state.agentDraft='';$('#agent-draft').value='';$('#agent-draft').focus();}
async function previewCurrentConditions() {
  if(state.busy||state.previewBusy||!state.boot||state.mode!=='demo')return;
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
  if(state.busy||state.loadedSaved)return;
  const previous=state.lastConstraints || state.constraints;state.busy=true;state.constraints=constraints;renderPlanning();clearMessage();
  try {
    const result=await api('/api/generate',{constraints:{...constraints,expected_revision:previous.revision}});
    state.generated=result;state.lastConstraints=clone(result.constraints || constraints);state.constraints=clone(result.constraints || constraints);state.selected=0;state.dirty=false;state.comparison=null;
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
  $('#saved-count').textContent=state.saved.length;
  const selected=new Set(state.compareSavedIds);
  $('#panel-saved').innerHTML=pageHeading('已保存方案','只列出本人的冻结规划版本；历史结果保留原快照。','<button class="button" data-action="refresh-saved">刷新版本列表</button>')+notice('保存版本属于规划草案。只有经核验的正式选课结果才能确认中选；模拟记录始终保留 synthetic 标记。','neutral')+(state.saved.length?`<div class="toolbar"><p>勾选两至三个保存版本，比较各自冻结结果。</p><button class="button" id="compare-saved-button" data-action="compare-saved" ${selected.size<2||selected.size>3?'disabled':''}>比较勾选版本</button></div>${state.savedComparison?renderComparison(state.savedComparison):''}<div class="section-container">${[...state.saved].reverse().map((saved,reverseIndex)=>{const index=state.saved.length-1-reverseIndex;const plan=saved.plan || saved;return `<article class="saved-entry"><div><label class="checkbox-line saved-selection"><input type="checkbox" data-saved-id="${saved.saved_id}" ${selected.has(saved.saved_id)?'checked':''}><span><strong>保存版 #${esc(saved.saved_id)}</strong></span></label><h2>${esc(saved.title || planName(plan))}</h2><p>${esc(saved.created_at || saved.saved_at || '保存时间未提供')} · ${(plan.section_ids||[]).length} 个教学班</p><p>条件版本 ${esc(showValue(saved.versions?.constraints_revision))} · 快照 ${esc(saved.versions?.offering_snapshot || '待确认')}</p><div class="snapshot-line"><span class="badge synthetic">synthetic</span>${statusBadge(plan.validation?.result || 'unknown')}<span>规划草案 · 冻结版本</span></div><details><summary>查看保存输入与版本</summary><pre>${esc(JSON.stringify(saved,null,2))}</pre></details></div><div class="button-group"><button class="button small" data-action="load-saved" data-index="${index}">查看冻结版</button><button class="button small primary" data-action="revalidate-saved" data-index="${index}">按当前数据重新校验</button></div></article>`;}).join('')}</div>`:`<div class="empty-state"><div class="empty-glyph" aria-hidden="true">◷</div><h2>保存一版，留住比较的依据</h2><p>生成方案后点击「保存方案」。每次保存都会记录当时的条件与版本，便于后续调整和复算。</p><button class="button primary" data-action="show-planning">返回选课规划</button></div>`);
}
async function refreshAudit(){try{const result=await api('/api/audit',{mode:state.mode});if(state.mode==='demo')state.audit=result;else state.sourceAudit=result;renderAudit();}catch(error){message(error.message,true);}}
async function refreshSaved(){try{state.saved=savedRows(await api('/api/saved'));renderSaved();}catch(error){message(error.message,true);}}
function switchTab(tab) {
  state.tab=tab;if(tab==='assistant')renderAssistant();$$('.nav-tab').forEach(button=>{const selected=button.dataset.tab===tab;button.classList.toggle('active',selected);button.setAttribute('aria-selected',String(selected));button.tabIndex=selected?0:-1;});
  $$('.tab-panel').forEach(panel=>panel.hidden=panel.id!==`panel-${tab}`);
  if(tab==='audit'&&!(state.mode==='demo'?state.audit:state.sourceAudit))refreshAudit();
}
async function changeMode(mode) {state.mode=mode;$('#data-mode').value=mode;const badge=$('#mode-badge');badge.textContent=mode==='demo'?'synthetic':'待审核资料';badge.className=`badge ${mode==='demo'?'synthetic':'warning'}`;clearMessage();renderAssistant();renderPlanning();renderAudit();if(state.tab==='audit'&&!(mode==='demo'?state.audit:state.sourceAudit))await refreshAudit();}
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
async function refreshWorkspace() {
  const boot=await api('/api/bootstrap');
  if(state.boot?.identity?.student_id && state.boot.identity.student_id!==boot.identity?.student_id){
    state.agentDraft='';const draft=$('#agent-draft');if(draft)draft.value='';
  }
  state.boot=boot;state.constraints=clone(state.boot.demo?.constraints || {});state.saved=savedRows(state.boot.saved_plans || []);
  if(state.auth)state.auth.identity=state.boot.identity;
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
async function mockLogin(profileId) {
  if(state.busy)return;state.busy=true;renderAuth();
  try{const result=await api('/api/auth/mock-login',{profile_id:profileId});clearWorkspace();state.auth={...state.auth,authenticated:true,identity:result.identity};state.csrfToken=result.csrf_token;await bootstrap();message('已进入本人 synthetic 演示工作区；学校认证和真实数据源尚未接入。');}catch(error){message(error.message,true);}finally{state.busy=false;renderAuth();}
}
async function logout() {
  try{await api('/api/auth/logout',{});clearWorkspace();state.auth={...(state.auth||{}),authenticated:false};renderAuth();clearMessage();message('已退出，服务端会话已撤销。');}catch(error){message(error.message,true);}
}
async function bootstrap() {
  $('#loading').hidden=false;$('#boot-error').hidden=true;
  try{
    const session=await api('/api/auth/session');
    if(state.auth?.identity?.student_id && state.auth.identity.student_id!==session.identity?.student_id)clearWorkspace();
    state.auth=session;
    if(!state.auth.authenticated){clearWorkspace();renderAuth();$('#loading').hidden=true;return;}
    state.csrfToken=state.auth.csrf_token;await refreshWorkspace();renderAuth();renderAssistant();renderPlanning();renderAudit();renderSources();renderSaved();switchTab(state.tab);$('#loading').hidden=true;
  }catch(error){$('#loading').hidden=true;$('#boot-error').hidden=false;$('#boot-error-text').textContent=`${error.message}。请确认本地 Python 服务已启动，然后重试。`;}
}
$('.nav-tabs').addEventListener('click',event=>{const button=event.target.closest('[data-tab]');if(button){clearMessage();switchTab(button.dataset.tab);}});
$('.nav-tabs').addEventListener('keydown',event=>{if(!['ArrowRight','ArrowLeft','ArrowUp','ArrowDown','Home','End'].includes(event.key))return;event.preventDefault();const tabs=$$('.nav-tab');let index=tabs.indexOf(document.activeElement);if(event.key==='Home')index=0;else if(event.key==='End')index=tabs.length-1;else index=(index+(event.key==='ArrowRight'||event.key==='ArrowDown'?1:-1)+tabs.length)%tabs.length;switchTab(tabs[index].dataset.tab);tabs[index].focus();});
$('#data-mode').addEventListener('change',event=>changeMode(event.target.value));
$('#retry-bootstrap').addEventListener('click',bootstrap);
$('#logout').addEventListener('click',logout);
$('#panel-saved').addEventListener('change',event=>{
  const input=event.target.closest('input[data-saved-id]');if(!input)return;
  const selected=new Set(state.compareSavedIds);const identifier=Number(input.dataset.savedId);input.checked?selected.add(identifier):selected.delete(identifier);
  state.compareSavedIds=[...selected];$('#compare-saved-button').disabled=selected.size<2||selected.size>3;
});
$('#main').addEventListener('click',async event=>{
  const button=event.target.closest('[data-action]');if(!button||button.disabled)return;
  switch(button.dataset.action){
    case'mock-login':await mockLogin(button.dataset.profileId);break;
    case'agent-example':chooseAgentExample(Number(button.dataset.example));break;
    case'clear-agent-draft':clearAgentDraft();break;
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
bootstrap();
