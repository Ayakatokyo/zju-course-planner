"""Independent school pages and session-scoped invalidation, using synthetic records only."""
import unittest
from tests import test_web_contract as web

class SchoolPresentationTests(unittest.TestCase):
    run_js = web.WebContractTests.run_js

    def test_grades_show_only_selected_scope_without_audit_columns(self):
        self.run_js(r"""
evaluate("resetSchoolPresentation();state.schoolYear='2024-2025';state.schoolSeason='AW';state.schoolViews={'academic:2024-2025-AW':{operation:'academic',target_term:'2024-2025-AW',status:'complete',records:[{course_name_raw:'This semester',grade_raw:'90',source_selection_id_raw:'selection-one'}]},'academic:2024-2025-SS':{operation:'academic',target_term:'2024-2025-SS',status:'complete',records:[{course_name_raw:'Another semester',grade_raw:'70'}]},'major_academic:ALL-HISTORY':{operation:'major_academic',status:'complete',records:[{course_name_raw:'Major record'}]},'graduation_audit:ALL-HISTORY':{operation:'graduation_audit',status:'complete',records:[{node_kind:'course',course_code_raw:'C',course_name_raw:'Audit only'}]}}");
let html=evaluate('schoolGradeWorkspaceMarkup(false)');assert.match(html,/This semester/);assert.match(html,/selection-one/);assert.doesNotMatch(html,/Another semester|Audit only|Major record|归属模块|自审状态|未关联/);
evaluate("state.schoolSeason='SS'");html=evaluate('schoolGradeWorkspaceMarkup(false)');assert.match(html,/Another semester/);assert.doesNotMatch(html,/This semester/);
evaluate("state.schoolGradeSource='major_academic'");html=evaluate('schoolGradeWorkspaceMarkup(false)');assert.match(html,/Another semester/);assert.doesNotMatch(html,/Major record|This semester|school-grade-source|主修历史/);
evaluate("state.schoolGradeSource='academic';state.schoolYear='2023-2024'");assert.doesNotMatch(evaluate('schoolGradeWorkspaceMarkup(false)'),/This semester|Another semester|Major record/);
""")

    def test_grade_form_requests_only_the_selected_semester(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'SYN',auth_mode:'school_client',security_realm:'personal_school_client'}};state.csrfToken='memory-only';resetSchoolPresentation();state.schoolOperation='academic';state.schoolYear='2024-2025';state.schoolSeason='AW';state.schoolGradeSource='major_academic';renderSchool()");
assert.doesNotMatch(element('#panel-school').innerHTML,/school-grade-source|主修历史|仅看主修/);
element('#school-year').value='2024-2025';element('#school-season').value='AW';const requests=[];
context.fetch=async(path,options)=>{const body=JSON.parse(options.body);requests.push({path,body});return {ok:true,json:async()=>({...body,status:'complete',records:[{course_name_raw:'Semester only',grade_raw:'90'}]})};};
const finished=evaluate("new Promise(resolve=>{const original=schoolLoadPresentation;schoolLoadPresentation=async kind=>{await original(kind);resolve();};})");
element('#school-query-form').listeners.submit({preventDefault(){}});await finished;
assert.equal(requests.length,1);assert.deepEqual(requests[0].body,{operation:'academic',target_term:'2024-2025-AW',force_refresh:true});assert.match(element('#panel-school').innerHTML,/Semester only/);
""")

    def test_progress_never_mixes_grade_or_major_sources(self):
        self.run_js(r"""
evaluate("resetSchoolPresentation();state.schoolViews={'graduation_audit:ALL-HISTORY':{operation:'graduation_audit',target_term:'ALL-HISTORY',status:'complete',records:[{node_kind:'module',node_id:'M',course_name_raw:'Module'},{node_kind:'course',node_id:'N',parent_node_id:'M',course_code_raw:'C',course_name_raw:'Audit course',completion_status_raw:'通过',grade_raw:'80'}]},grades:{operation:'academic',status:'complete',records:[{course_code_raw:'C',course_name_raw:'Audit course',grade_raw:'90'},{course_name_raw:'Grade only'}]},major:{operation:'major_academic',status:'complete',records:[{course_name_raw:'Major only'}]}}");
assert.equal(evaluate("schoolAuditStatus({completion_status:'unknown'})"),'未明确');assert.equal(evaluate("schoolAuditStatus({completion_status:'in_progress'})"),'在修');
const before=evaluate('JSON.stringify(state.schoolViews)'),html=evaluate('schoolProgressMarkup(false)');assert.match(html,/Audit course/);assert.match(html,/通过/);assert.equal((html.match(/<tr class="school-course-row">/g)||[]).length,1);assert.doesNotMatch(html,/Grade only|Major only|未关联|补充学期成绩|school-year|多条成绩/);assert.equal(evaluate('JSON.stringify(state.schoolViews)'),before);
""")

    def test_module_expansion_preserves_ownership_records_and_open_state(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'SYN',auth_mode:'school_client',security_realm:'personal_school_client'}};state.schoolOperation='graduation_audit';resetSchoolPresentation();state.schoolViews={'graduation_audit:ALL-HISTORY':{operation:'graduation_audit',target_term:'ALL-HISTORY',status:'complete',records:[{node_kind:'module',node_id:'M',course_name_raw:'Parent',earned_credits_raw:'6+2',required_credits_raw:'9+8',completion_status_raw:'不通过'},{node_kind:'module',node_id:'N',parent_node_id:'M',course_name_raw:'Child',completion_status_raw:'通过'},{node_kind:'course',parent_node_id:'M',course_code_raw:'C',course_name_raw:'Same',grade_raw:'60',substitution_raw:'Original replacement'},{node_kind:'course',parent_node_id:'M',course_code_raw:'C',course_name_raw:'Same',grade_raw:'90'},{node_kind:'course',parent_node_id:'N',course_code_raw:'C',course_name_raw:'Same',grade_raw:'80'},{node_kind:'course',parent_node_id:'missing',course_name_raw:'Orphan'}]}}");
const before=evaluate('JSON.stringify(state.schoolViews)');let html=evaluate('schoolProgressMarkup(false)');assert.doesNotMatch(html,/school-course-records|school-course-filter/);assert.equal((html.match(/class="school-course-row"/g)||[]).length,4);assert.match(html,/6\+2/);assert.match(html,/9\+8/);assert.match(html,/归属未明确的课程记录（1）/);assert.match(html,/Original replacement/);
const parent=html.split('id="school-module-courses-0"')[1].split('class="school-module-root"')[0];assert.ok(parent.indexOf('60')<parent.indexOf('Child'));assert.ok(parent.indexOf('90')<parent.indexOf('Child'));
const button=element('module-button'),detail=element('module-detail');button.dataset={courseDisclosure:'module-courses:M'};button.setAttribute('aria-controls','module-detail');button.setAttribute('aria-expanded','false');detail.hidden=true;
context.document.getElementById=id=>id==='module-detail'?detail:null;context.document.querySelectorAll=selector=>selector==='[data-course-disclosure]'?[button]:[];
let calls=0;context.fetch=()=>{calls++;throw Error('no request');};evaluate('renderSchool()');button.listeners.click();assert.equal(detail.hidden,false);
evaluate("state.schoolModuleFailed=true;renderSchool()");html=element('#panel-school').innerHTML;assert.match(html,/aria-expanded="true"/);assert.doesNotMatch(html,/Child/);assert.match(html,/Orphan/);assert.equal(calls,0);assert.equal(evaluate('JSON.stringify(state.schoolViews)'),before);
evaluate("state.schoolModuleFailed=false;renderSchool()");assert.match(element('#panel-school').innerHTML,/Child/);assert.match(element('#panel-school').innerHTML,/aria-expanded="true"/);
evaluate('resetSchoolPresentation()');assert.equal(evaluate('Object.keys(state.schoolOpenCourses).length'),0);
""")

    def test_cyclic_modules_do_not_drop_course_records(self):
        self.run_js(r"""
evaluate("resetSchoolPresentation();state.schoolViews={'graduation_audit:ALL-HISTORY':{operation:'graduation_audit',status:'complete',records:[{node_kind:'module',node_id:'A',parent_node_id:'B',course_name_raw:'A'},{node_kind:'module',node_id:'B',parent_node_id:'A',course_name_raw:'B'},{node_kind:'course',parent_node_id:'B',course_name_raw:'Cyclic course',grade_raw:'70'}]}}");
const html=evaluate('schoolProgressMarkup(false)');assert.equal((html.match(/Cyclic course/g)||[]).length,1);assert.match(html,/70/);
""")

    def test_pages_keep_primary_fields_without_auxiliary_panels_or_links(self):
        self.run_js(r"""
evaluate("resetSchoolPresentation();state.schoolYear='2026-2027';state.schoolSeason='AW';state.schoolProfile={name:'PRIVATE-PROFILE',college_name:'College'};state.schoolPlan={status:'available',source_alias:'PLAN'};state.schoolViews={'graduation_audit:ALL-HISTORY':{operation:'graduation_audit',target_term:'ALL-HISTORY',status:'complete',records:[{node_kind:'module',node_id:'M',course_name_raw:'Module',earned_credits_raw:'4',required_credits_raw:'6'},{node_kind:'course',parent_node_id:'M',course_code_raw:'C',course_name_raw:'Audit course',grade_raw:'90',completion_status_raw:'通过'}]},'timetable:2026-2027-AW':{operation:'timetable',target_term:'2026-2027-AW',status:'complete',records:[{course_name_raw:'Timetable course',source_selection_id_raw:'X',instructor_raw:'Teacher',time_raw:'周一第1节',place_raw:'Room'}]},'exams:2026-2027-A':{operation:'exams',target_term:'2026-2027-A',status:'complete',records:[{course_name_raw:'Exam course',source_selection_id_raw:'X',final_exam_time_raw:'2026-11-01 09:00',final_exam_place_raw:'Exam room'}]}}");
let html=evaluate('schoolProgressMarkup(false)');for(const value of ['Module','4','6','Audit course','90','通过'])assert.ok(html.includes(value));assert.doesNotMatch(html,/PRIVATE-PROFILE|本人资料与培养原文|获取培养方案|data-module-id|点击模块|显示组/);
evaluate("state.schoolArrangementKey='0'");html=evaluate('schoolTimetableWorkspaceMarkup(false)');for(const value of ['Timetable course','Teacher','Room','选课课号'])assert.ok(html.includes(value));assert.doesNotMatch(html,/Exam course|查看考试|关联考试|data-school-exam-term|查看课表原文/);
evaluate("state.schoolSeason='A'");html=evaluate('schoolExamWorkspaceMarkup(false)');for(const value of ['Exam course','2026-11-01','09:00','Exam room'])assert.ok(html.includes(value));assert.doesNotMatch(html,/Timetable course|data-arrangement-key|data-arrangement-term/);
""")

    def test_five_page_navigation_remembers_independent_query_ranges(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'SYN',auth_mode:'school_client',security_realm:'personal_school_client'}};resetSchoolPresentation();state.schoolOperation='academic';state.schoolYear='2024-2025';state.schoolSeason='SS';selectSchoolSection('timetable');state.schoolYear='2026-2027';state.schoolSeason='AW';selectSchoolSection('exams');state.schoolSeason='W';selectSchoolSection('academic')");assert.equal(evaluate('state.schoolYear'),'2024-2025');assert.equal(evaluate('state.schoolSeason'),'SS');evaluate("selectSchoolSection('exams')");assert.equal(evaluate('state.schoolSeason'),'W');evaluate("selectSchoolSection('timetable')");assert.equal(evaluate('state.schoolSeason'),'AW');assert.equal(evaluate('Object.keys(workspaceSchoolPages).length'),5);evaluate('clearWorkspace()');assert.equal(evaluate('Object.keys(state.schoolPageQueries).length'),0);
""")

    def test_other_pages_do_not_reset_grade_paging_or_timetable_filter(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'SYN',auth_mode:'school_client',security_realm:'personal_school_client'}};resetSchoolPresentation();state.schoolGradePage=2;state.schoolSeasonFilter='冬';state.schoolArrangementKey='1';selectSchoolSection('exams')");
element('#school-year').value='2025-2026';element('#school-season').value='W';element('#school-season').listeners.change();assert.equal(evaluate('state.schoolGradePage'),2);assert.equal(evaluate('state.schoolSeasonFilter'),'冬');assert.equal(evaluate('state.schoolArrangementKey'),'1');
""")

    def test_single_meeting_details_do_not_repeat_identical_time_and_place(self):
        self.run_js(r"""
context.detailRecord={course_name_raw:'Course',time_raw:'Friday 2-3',place_raw:'Room-A',meetings:[{time_raw:'Friday 2-3',place_raw:'Room-A',week_annotation_raw:'1-8'}]};let html=evaluate('schoolTimetableDetailMarkup(detailRecord)');assert.equal((html.match(/Friday 2-3/g)||[]).length,1);assert.equal((html.match(/Room-A/g)||[]).length,1);assert.match(html,/1-8/);
context.detailRecord.meetings[0].place_raw='Room-B';html=evaluate('schoolTimetableDetailMarkup(detailRecord)');assert.match(html,/Room-A/);assert.match(html,/Room-B/);
""")

    def test_timetable_season_filter_uses_each_meeting_and_keeps_unknowns(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'SYN',auth_mode:'school_client',security_realm:'personal_school_client'}};resetSchoolPresentation();state.schoolOperation='timetable';state.schoolYear='2026-2027';state.schoolSeason='AW'");
context.source={operation:'timetable',target_term:'2026-2027-AW',status:'complete',records:[
 {course_name_raw:'双分学期课程',xxq_raw:'秋冬',meetings:[{weekday:1,periods:[1],season_codes:['秋'],place_raw:'秋教室'},{weekday:3,periods:[3],season_codes:['冬'],place_raw:'冬教室'}]},
 {course_name_raw:'时段明确冬课',xxq_raw:'秋',meetings:[{weekday:2,periods:[2],xxq_raw:'冬'}]},
 {course_name_raw:'分学期缺失',meetings:[{weekday:4,periods:[4]}]},
 {course_name_raw:'分学期冲突',xxq_raw:'秋',meetings:[{weekday:5,periods:[5],season_codes:['秋'],field_states:{season_codes:'conflicting'}}]},
 {course_name_raw:'来源代码待核验',meetings:[{weekday:6,periods:[6],xxq_raw:'1',field_states:{xxq_raw:'pending_verification'}}]}
]};
evaluate("state.schoolViews={'timetable:2026-2027-AW':source};state.schoolSeasonFilter='冬';renderSchool()");
const before=JSON.stringify(context.source),winter=element('#panel-school').innerHTML;
assert.doesNotMatch(winter,/秋教室|data-weekday="1" data-period="1"/);assert.match(winter,/冬教室/);assert.match(winter,/时段明确冬课/);assert.match(winter,/5条课表记录 · 已定位5个时段/);
for(const name of ['分学期缺失','分学期冲突','来源代码待核验'])assert.ok(winter.includes(name));
element('#school-season-filter').listeners.change({target:{value:'秋'}});
const autumn=element('#panel-school').innerHTML;assert.match(autumn,/秋教室/);assert.doesNotMatch(autumn,/冬教室|时段明确冬课/);assert.match(autumn,/4条课表记录 · 已定位4个时段/);
element('#school-season-filter').listeners.change({target:{value:''}});assert.match(element('#panel-school').innerHTML,/5条课表记录 · 已定位6个时段/);assert.equal(JSON.stringify(context.source),before);
""")

    def test_timetable_raw_segment_seasons_override_course_wide_range(self):
        self.run_js(r"""
evaluate("resetSchoolPresentation();state.schoolYear='2026-2027';state.schoolSeason='AW';state.schoolSeasonFilter='冬'");
context.source={operation:'timetable',target_term:'2026-2027-AW',status:'complete',records:[{course_name_raw:'原文跨季课程',xxq_raw:'秋冬',time_raw:'秋周一第1节&lt;br&gt;冬周三第3节',place_raw:'秋地点<br>冬地点'}]};
const before=JSON.stringify(context.source);evaluate("state.schoolViews={'timetable:2026-2027-AW':source}");
let html=evaluate('schoolTimetableWorkspaceMarkup(false)');assert.match(html,/冬地点/);assert.doesNotMatch(html,/秋地点|data-weekday="1" data-period="1"/);assert.match(html,/1条课表记录 · 已定位1个时段/);
evaluate("state.schoolSeasonFilter='秋'");html=evaluate('schoolTimetableWorkspaceMarkup(false)');assert.match(html,/秋地点/);assert.doesNotMatch(html,/冬地点|data-weekday="3" data-period="3"/);assert.equal(JSON.stringify(context.source),before);
""")

    def test_timetable_filter_hides_unmatched_detail_and_retains_source_record_key(self):
        self.run_js(r"""
evaluate("resetSchoolPresentation();state.schoolYear='2026-2027';state.schoolSeason='AW';state.schoolSeasonFilter='冬';state.schoolArrangementKey='0'");
context.source={operation:'timetable',target_term:'2026-2027-AW',status:'complete',records:[
 {course_name_raw:'秋课',xxq_raw:'秋',course_code_raw:'AUTUMN-DETAIL',time_raw:'周一第1节'},
 {course_name_raw:'冬课',xxq_raw:'冬',course_code_raw:'WINTER-DETAIL',time_raw:'周二第2节'}
]};evaluate("state.schoolViews={'timetable:2026-2027-AW':source}");
let html=evaluate('schoolTimetableWorkspaceMarkup(false)');assert.doesNotMatch(html,/AUTUMN-DETAIL|school-arrangement-detail/);assert.match(html,/data-arrangement-key="1"/);
evaluate("state.schoolArrangementKey='1'");html=evaluate('schoolTimetableWorkspaceMarkup(false)');assert.match(html,/WINTER-DETAIL/);
context.source.records=[context.source.records[0]];html=evaluate('schoolTimetableWorkspaceMarkup(false)');assert.match(html,/当前分学期筛选下没有可展示的时段/);assert.doesNotMatch(html,/本次未返回可展示课表|school-timetable-pending|school-arrangement-detail/);
""")

    def test_enrollment_fields_have_separate_columns_and_keep_unknowns(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'SYN',auth_mode:'school_client',security_realm:'personal_school_client'}};state.schoolEnrollment={status:'available',academic_year:'2026-2027',scope:'current',selected:[{kcmc:'Synthetic course',kcdm:'C',xkkh:'SECTION-LONG',sxbj:'0',xkzy:0,jsxm:'Teacher',xxq:'秋',sksj:'周五2-3节',skdd:'Room'},{kcmc:'Unknown course'}]}");
const html=evaluate('schoolEnrollmentMarkup()'),table=html.match(/<table[^>]*school-selected-table[\s\S]*?<\/table>/)[0],rows=[...table.matchAll(/<tbody>([\s\S]*?)<\/tbody>/g)][0][1];
assert.equal((table.match(/<th scope="col">/g)||[]).length,8);
for(const row of rows.match(/<tr>[\s\S]*?<\/tr>/g))assert.equal((row.match(/<td>/g)||[]).length,8);
assert.match(rows,/<td>待筛选<\/td><td>0<\/td><td>Teacher<\/td><td>秋<\/td><td>周五2-3节<\/td><td>Room<\/td>/);assert.match(rows,/状态待核对/);assert.match(rows,/<td>—<\/td>/);assert.doesNotMatch(html,/提交选课<\/button>/);
""")

    def test_enrollment_refresh_preserves_last_result_until_successful_retry(self):
        self.run_js(r"""
evaluate("clearWorkspace();state.auth={authenticated:true,identity:{student_id:'SYN',auth_mode:'school_client',security_realm:'personal_school_client'}};state.schoolOperation='enrollment'");
const preview={status:'available',academic_year:'2026-2027',season:'1',checked_at:1793971200,
  selected:[{kcmc:'Previously selected',kcdm:'SELECTED',sxbj:'0',xkzy:0}],
  courses:[{kcmc:'Candidate A',kcdm:'A'},{kcmc:'Candidate B',kcdm:'B'}],
  sections:[{kcdm:'A',xkkh:'A-SECTION'}]};
const original=JSON.stringify(preview),requests=[];
context.fetch=async(path,options)=>{requests.push(JSON.parse(options.body));return {ok:true,json:async()=>preview};};
await evaluate("loadEnrollmentPreview('A')");
const stored=evaluate('JSON.stringify(state.schoolEnrollment)');
let release;
context.fetch=(path,options)=>{requests.push(JSON.parse(options.body));return new Promise(resolve=>{release=resolve;});};
const pending=evaluate("loadEnrollmentPreview('B')");
assert.equal(evaluate('JSON.stringify(state.schoolEnrollment)'),stored);
assert.match(element('#panel-school').innerHTML,/Previously selected|A-SECTION/);
await evaluate('loadEnrollmentPreview()');assert.equal(requests.length,2);
release({ok:false,status:503,text:async()=>JSON.stringify({code:'source_unavailable'})});await pending;
assert.equal(evaluate('JSON.stringify(state.schoolEnrollment)'),stored);
assert.match(element('#panel-school').innerHTML,/本次更新未完成.*保留上次数据/);
assert.match(element('#panel-school').innerHTML,/Candidate A的教学班/);
assert.doesNotMatch(element('#panel-school').innerHTML,/Candidate B的教学班/);
assert.equal(evaluate('state.schoolBusy'),false);
context.fetch=async(path,options)=>{requests.push(JSON.parse(options.body));return {ok:true,json:async()=>({status:'source_unavailable'})};};
await evaluate('loadEnrollmentPreview()');
assert.equal(evaluate('JSON.stringify(state.schoolEnrollment)'),stored);
assert.match(element('#panel-school').innerHTML,/本次更新未完成.*保留上次数据/);
for(const invalidJson of [false,true]){
  context.fetch=async(path,options)=>{
    requests.push(JSON.parse(options.body));
    if(!invalidJson)throw new TypeError('synthetic connection failure');
    return {ok:true,json:async()=>{throw new SyntaxError('synthetic invalid JSON');}};
  };
  await evaluate('loadEnrollmentPreview()');
  assert.equal(evaluate('JSON.stringify(state.schoolEnrollment)'),stored);
  assert.match(element('#panel-school').innerHTML,invalidJson?/本机响应不完整或格式无效.*保留上次数据/:/本机服务暂不可用.*保留上次数据/);
}
const refreshed={...preview,academic_year:'2027-2028',season:'2',checked_at:1793971800,
  selected:[{kcmc:'Refreshed selected',sxbj:'1'}],sections:[{kcdm:'B',xkkh:'B-SECTION'}]};
context.fetch=async(path,options)=>{requests.push(JSON.parse(options.body));return {ok:true,json:async()=>refreshed};};
await evaluate("loadEnrollmentPreview('B')");
assert.match(element('#panel-school').innerHTML,/Refreshed selected|B-SECTION|Candidate B的教学班|2027-2028 春夏/);
assert.doesNotMatch(element('#panel-school').innerHTML,/Previously selected|A-SECTION|保留上次数据|本次更新未完成/);
assert.equal(element('#global-status').hidden,true);
assert.deepEqual(requests,[{course_code:'A'},{course_code:'B'},{},{},{},{course_code:'B'}]);
assert.equal(JSON.stringify(preview),original);
""")

    def test_malformed_enrollment_does_not_replace_rows_or_break_retry(self):
        self.run_js(r"""
evaluate("clearWorkspace();state.auth={authenticated:true,identity:{student_id:'SYN',auth_mode:'school_client',security_realm:'personal_school_client'}};state.schoolOperation='enrollment'");
const preview={status:'available',academic_year:'2026-2027',season:'1',selected:[{kcmc:'Verified old rows'}],courses:[{kcmc:'Candidate A',kcdm:'A'}],sections:[]};
context.fetch=async()=>({ok:true,json:async()=>preview});await evaluate('loadEnrollmentPreview()');
const old=evaluate('JSON.stringify(state.schoolEnrollment)');
for(const result of [null,[],{status:'available'},{...preview,selected:{}},{...preview,courses:[null]},
  {...preview,sections:['malformed']},{...preview,selected:null},{...preview,academic_year:'2026-2029'},
  {...preview,season:'3'},{...preview,courses:[]},{...preview,sections:[{kcdm:'B',xkkh:'WRONG-SECTION'}]}]){
  context.fetch=async()=>({ok:true,json:async()=>result});
  await evaluate("loadEnrollmentPreview('A')");
  assert.equal(evaluate('JSON.stringify(state.schoolEnrollment)'),old);
  assert.equal(evaluate('state.schoolBusy'),false);
  assert.match(element('#panel-school').innerHTML,/学校响应格式尚未适配.*保留上次数据/);
  assert.doesNotMatch(element('#panel-school').innerHTML,/WRONG-SECTION/);
}
evaluate('state.schoolEnrollment=null');context.fetch=async()=>({ok:true,json:async()=>({status:'available',selected:{}})});
await evaluate('loadEnrollmentPreview()');
assert.equal(evaluate('state.schoolEnrollment'),null);
assert.match(element('#panel-school').innerHTML,/本次更新未完成/);
assert.doesNotMatch(element('#panel-school').innerHTML,/保留上次数据|school-selected-table/);
""")

    def test_enrollment_auth_failures_and_late_results_clear_or_preserve_current_scope(self):
        self.run_js(r"""
const setup=()=>evaluate("clearWorkspace();state.auth={authenticated:true,identity:{student_id:'SYN-A',auth_mode:'school_client',security_realm:'personal_school_client'}};state.schoolOperation='enrollment';state.schoolEnrollment={status:'available',academic_year:'2026-2027',season:'1',selected:[{kcmc:'OLD OWNER'}],courses:[],sections:[]};state.schoolViews={'academic:2026-2027-AW':{records:[{course_name_raw:'OLD GRADES'}]}}");
for(const status of ['source_session_expired','permission_denied','source_subject_changed','user_confirmation_required']){
  setup();context.fetch=async()=>({ok:true,json:async()=>({status})});await evaluate('loadEnrollmentPreview()');
  assert.equal(evaluate('state.schoolEnrollment'),null,status);
  assert.equal(evaluate('Object.keys(state.schoolViews).length'),0);
  assert.doesNotMatch(element('#panel-school').innerHTML,/OLD OWNER|OLD GRADES|保留上次数据/);
}
for(const successful of [true,false]){
  setup();let release;context.fetch=()=>new Promise(resolve=>{release=resolve;});const pending=evaluate('loadEnrollmentPreview()');
  evaluate("clearSchoolWorkspace();state.auth={authenticated:true,identity:{student_id:'SYN-B',auth_mode:'school_client',security_realm:'personal_school_client'}};state.schoolOperation='enrollment';state.schoolEnrollment={status:'available',academic_year:'2026-2027',season:'1',selected:[{kcmc:'CURRENT OWNER'}],courses:[],sections:[]};renderSchool()");
  release(successful?{ok:true,json:async()=>({status:'available',academic_year:'2026-2027',season:'1',selected:[{kcmc:'LATE PRIVATE'}],courses:[],sections:[]})}:{ok:false,status:401,text:async()=>JSON.stringify({code:'auth_expired'})});
  await pending;
  assert.equal(evaluate('state.auth.authenticated'),true);
  assert.match(element('#panel-school').innerHTML,/CURRENT OWNER/);
  assert.doesNotMatch(element('#panel-school').innerHTML,/LATE PRIVATE|保留上次数据/);
}
""")

    def test_enrollment_empty_response_and_course_empty_sections_are_explicit(self):
        self.run_js(r"""
evaluate("clearWorkspace();state.auth={authenticated:true,identity:{student_id:'SYN',auth_mode:'school_client',security_realm:'personal_school_client'}};state.schoolOperation='enrollment'");
const preview={status:'available',academic_year:'2026-2027',season:'1',selected:[],courses:[{kcmc:'<img>Candidate',kcdm:'A'}],sections:[]};
context.fetch=async()=>({ok:true,json:async()=>preview});await evaluate("loadEnrollmentPreview('A')");
assert.match(element('#panel-school').innerHTML,/本次未返回页面已选教学班/);
assert.match(element('#panel-school').innerHTML,/&lt;img&gt;Candidate的教学班/);
assert.match(element('#panel-school').innerHTML,/本次未返回该课程的教学班/);
assert.doesNotMatch(element('#panel-school').innerHTML,/<img>|已选课程数为0/);
let calls=0;context.fetch=()=>{calls++;throw Error('unexpected request');};evaluate('state.auth={authenticated:false}');
await evaluate('loadEnrollmentPreview()');assert.equal(calls,0);
""")

    def test_failed_refresh_preserves_old_source_with_explicit_label(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'SYN',auth_mode:'school_client',security_realm:'personal_school_client'}};state.schoolYear='2026-2027';state.schoolSeason='AW';resetSchoolPresentation();state.schoolViews={'academic:2026-2027-AW':{operation:'academic',target_term:'2026-2027-AW',status:'complete',captured_at:1791952661,records:[{course_name_raw:'OLD'}]}}");
context.fetch=async()=>({ok:false,status:502,text:async()=>JSON.stringify({code:'source_unavailable'})});await evaluate("schoolLoadPresentation('academic')");assert.equal(evaluate("schoolView('academic','2026-2027-AW').records[0].course_name_raw"),'OLD');assert.match(element('#panel-school').innerHTML,/保留上次数据/);assert.match(element('#panel-school').innerHTML,/本次更新未完成/);
""")

    def test_new_fetch_late_response_and_auth_failure_clear_every_view(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'A',auth_mode:'school_client',security_realm:'personal_school_client'}};state.schoolYear='2026-2027';state.schoolSeason='AW';resetSchoolPresentation()");let release;context.fetch=()=>new Promise(resolve=>release=resolve);const pending=evaluate("schoolLoadPresentation('academic')");evaluate("clearWorkspace();state.auth={authenticated:true,identity:{student_id:'B',auth_mode:'school_client',security_realm:'personal_school_client'}}");release({ok:true,json:async()=>({operation:'academic',target_term:'2026-2027-AW',status:'complete',records:[{course_name_raw:'A-PRIVATE'}]})});await pending;assert.equal(evaluate('Object.keys(state.schoolViews).length'),0);assert.doesNotMatch(element('#panel-school').innerHTML,/A-PRIVATE/);
evaluate("state.schoolYear='2026-2027';state.schoolSeason='AW';state.schoolProfile={cohort:2023};state.schoolViews={secret:{records:[]}};state.schoolEnrollment={status:'available'}");context.fetch=async()=>({ok:false,status:409,text:async()=>JSON.stringify({code:'source_session_expired'})});await evaluate("schoolLoadPresentation('academic')");assert.equal(evaluate('Object.keys(state.schoolViews).length'),0);assert.equal(evaluate('state.schoolProfile'),null);assert.equal(evaluate('state.schoolEnrollment'),null);assert.equal(evaluate('state.schoolReauth'),true);
""")

    def test_enrollment_captcha_retries_with_image_challenge_and_publishes_the_course(self):
        self.run_js(r"""
evaluate("clearWorkspace();state.auth={authenticated:true,identity:{student_id:'SYN',auth_mode:'school_client',security_realm:'personal_school_client'}};state.schoolOperation='enrollment'");
const preview={status:'available',academic_year:'2026-2027',season:'1',selected:[{kcmc:'Old selection'}],courses:[{kcdm:'SYN-A',kcmc:'Candidate A'}],sections:[],checked_at:1};
context.fetch=async()=>({ok:true,json:async()=>preview});await evaluate('loadEnrollmentPreview()');
const requests=[];let imageCount=0,submissions=0;
context.fetch=async(path,options)=>{
  const body=options.body?JSON.parse(options.body):null;requests.push({path,body});
  if(path==='/api/school/enrollment-preview')return {ok:true,json:async()=>({status:'captcha_required'})};
  assert.equal(path,'/api/school/captcha');
  if(!body){imageCount++;return {ok:true,headers:{get:()=>`SYN-IMAGE-${imageCount}`},blob:async()=>new Blob(['synthetic'],{type:'image/png'})};}
  submissions++;assert.equal(body.challenge_id,`SYN-IMAGE-${imageCount}`);
  return {ok:true,json:async()=>submissions===1?{status:'captcha_required'}:{...preview,checked_at:2,sections:[{kcdm:'SYN-A',xkkh:'SYN-SECTION'}]}};
};
await evaluate("loadEnrollmentPreview('SYN-A')");
assert.equal(evaluate('state.schoolPending.course_code'),'SYN-A');
assert.equal(evaluate('state.schoolNeedsCaptcha'),true);assert.equal(imageCount,1);
assert.match(element('#panel-school').innerHTML,/Old selection|school-captcha-form/);
element('#school-captcha-text').value='WRONG';await evaluate('submitSchoolCaptcha()');
assert.equal(imageCount,2);assert.equal(evaluate('state.schoolNeedsCaptcha'),true);
assert.equal(evaluate('state.schoolEnrollment.checked_at'),1);
element('#school-captcha-text').value='TEST';await evaluate('submitSchoolCaptcha()');
assert.equal(evaluate('state.schoolEnrollment.checked_at'),2);
assert.equal(evaluate('state.schoolEnrollment.queried_course_code'),'SYN-A');
assert.equal(evaluate('state.schoolPending'),null);assert.equal(evaluate('state.schoolCaptchaUrl'),null);
assert.equal(evaluate('state.schoolQueryChallengeId'),null);
assert.match(element('#panel-school').innerHTML,/Candidate A的教学班/);
assert.match(element('#panel-school').innerHTML,/SYN-SECTION/);
assert.doesNotMatch(element('#panel-school').innerHTML,/school-captcha-form|本次更新未完成/);
await evaluate('submitSchoolCaptcha()');assert.equal(submissions,2);assert.equal(requests.length,5);
""")

    def test_new_query_clears_obsolete_captcha_and_discards_its_late_image(self):
        self.run_js(r"""
evaluate("clearWorkspace();state.auth={authenticated:true,identity:{student_id:'SYN',auth_mode:'school_client',security_realm:'personal_school_client'}};state.schoolOperation='enrollment';state.schoolNeedsCaptcha=true;state.schoolPending={operation:'academic',target_term:'2026-2027-AW'};state.schoolQueryChallengeId='OLD';state.schoolCaptchaUrl='blob:old'");
evaluate('renderSchool()');assert.doesNotMatch(element('#panel-school').innerHTML,/school-captcha-form/);
evaluate("selectSchoolSection('academic')");assert.match(element('#panel-school').innerHTML,/school-captcha-form/);
evaluate("selectSchoolSection('enrollment')");assert.doesNotMatch(element('#panel-school').innerHTML,/school-captcha-form/);
let release;context.fetch=()=>new Promise(resolve=>release=resolve);
const image=evaluate('loadSchoolCaptcha()');
context.fetch=async()=>({ok:true,json:async()=>({status:'available',academic_year:'2026-2027',season:'1',selected:[],courses:[],sections:[]})});
await evaluate('loadEnrollmentPreview()');
release({ok:true,headers:{get:()=> 'LATE'},blob:async()=>new Blob(['synthetic'],{type:'image/png'})});await image;
assert.equal(evaluate('state.schoolPending'),null);assert.equal(evaluate('state.schoolNeedsCaptcha'),false);
assert.equal(evaluate('state.schoolQueryChallengeId'),null);assert.equal(evaluate('state.schoolCaptchaUrl'),null);
assert.doesNotMatch(element('#panel-school').innerHTML,/school-captcha-form/);
""")

    def test_captcha_retry_rechecks_session_and_query_scope_before_publishing(self):
        self.run_js(r"""
const setup=()=>evaluate("state.auth={authenticated:true,identity:{student_id:'SYNTHETIC-A',auth_mode:'school_client',security_realm:'personal_school_client'}};state.schoolBusy=false;state.schoolReauth=false;state.schoolYear='2026-2027';state.schoolSeason='AW';state.schoolOperation='academic';resetSchoolPresentation();state.schoolNeedsCaptcha=true;state.schoolQueryChallengeId='SYN-IMAGE';state.schoolPending={operation:'academic',target_term:'2026-2027-AW',force_refresh:true};state.schoolViews={'academic:2026-2027-AW':{operation:'academic',target_term:'2026-2027-AW',status:'complete',records:[{course_name_raw:'SYNTHETIC OLD'}]}};");
for(const status of ['source_session_expired','authentication_required','source_subject_changed','source_epoch_changed','permission_denied','user_confirmation_required']){
  setup();element('#school-captcha-text').value='TEST';
  context.fetch=async()=>({ok:true,json:async()=>({status,records:[]})});
  await evaluate('submitSchoolCaptcha()');
  assert.equal(evaluate('Object.keys(state.schoolViews).length'),0,status);
  assert.equal(evaluate('state.schoolPending'),null);assert.equal(evaluate('state.schoolNeedsCaptcha'),false);
  assert.doesNotMatch(element('#global-status').textContent,/已提交|已完成验证/);
}
for(const target_term of ['2025-2026-AW','2026-2027-AW']){
  setup();element('#school-captcha-text').value='TEST';
  context.fetch=async()=>({ok:true,json:async()=>({operation:'academic',target_term,status:'complete',records:[{course_name_raw:'SYNTHETIC NEW'}]})});
  await evaluate('submitSchoolCaptcha()');
  const record=evaluate("schoolView('academic','2026-2027-AW').records[0].course_name_raw");
  assert.equal(record,target_term==='2026-2027-AW'?'SYNTHETIC NEW':'SYNTHETIC OLD');
  if(target_term!=='2026-2027-AW')assert.match(element('#panel-school').innerHTML,/本次更新未完成.*保留上次数据/);
}
""")

    def test_captcha_image_auth_failure_clears_source_views_without_retry_loop(self):
        self.run_js(r"""
for(const code of ['source_session_expired','permission_denied','user_confirmation_required','source_unavailable']){
  evaluate("state.auth={authenticated:true,identity:{student_id:'SYNTHETIC-A',auth_mode:'school_client',security_realm:'personal_school_client'}};state.schoolReauth=false;state.schoolOperation='academic';state.schoolYear='2026-2027';state.schoolSeason='AW';resetSchoolPresentation();state.schoolNeedsCaptcha=true;state.schoolQueryChallengeId='SYN-IMAGE';state.schoolPending={operation:'academic',target_term:'2026-2027-AW'};state.schoolViews={'academic:2026-2027-AW':{operation:'academic',target_term:'2026-2027-AW',status:'complete',records:[{course_name_raw:'SYNTHETIC OLD'}]}}");
  let calls=0;context.fetch=async()=>{calls++;return {ok:false,status:403,text:async()=>JSON.stringify({code})};};
  await evaluate("schoolFailure('captcha_required',currentSchoolRequest(),state.schoolPending)");assert.equal(calls,1);
  if(code==='source_unavailable'){
    assert.equal(evaluate('Object.keys(state.schoolViews).length'),1);
    assert.equal(evaluate('state.schoolNeedsCaptcha'),true);
    assert.match(element('#global-status').textContent,/验证码图片读取失败/);
  }else{
    assert.equal(evaluate('Object.keys(state.schoolViews).length'),0,code);
    assert.equal(evaluate('state.schoolNeedsCaptcha'),false);
    assert.equal(evaluate('state.schoolPending'),null);
    assert.doesNotMatch(element('#panel-school').innerHTML,/SYNTHETIC OLD/);
    assert.doesNotMatch(element('#global-status').textContent,/需要验证码/);
  }
}
""")

    def test_malformed_query_response_keeps_previous_data_and_allows_retry(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'SYNTHETIC-A',auth_mode:'school_client',security_realm:'personal_school_client'}};state.schoolYear='2026-2027';state.schoolSeason='AW';state.schoolOperation='academic';resetSchoolPresentation();state.schoolViews={'academic:2026-2027-AW':{operation:'academic',target_term:'2026-2027-AW',status:'complete',records:[{course_name_raw:'SYNTHETIC OLD'}]}}");
for(const response of [null,[],{status:'complete',operation:'academic',target_term:'2026-2027-AW'}]){
  context.fetch=async()=>({ok:true,json:async()=>response});
  await evaluate("schoolLoadPresentation('academic')");
  assert.equal(evaluate('state.schoolBusy'),false);
  assert.equal(evaluate("schoolView('academic','2026-2027-AW').records[0].course_name_raw"),'SYNTHETIC OLD');
  assert.match(element('#panel-school').innerHTML,/本次更新未完成.*保留上次数据/);
}
""")

    def test_exam_events_preserve_duplicate_source_rows(self):
        self.run_js(r"""
context.records=Array.from({length:2},()=>({source_selection_id_raw:'X',course_name_raw:'独立来源记录',field_states:{source_selection_id_raw:'known'},final_exam_time_raw:'2026-11-01',midterm_exam_time_raw:'2026-10-01'}));
const before=JSON.stringify(context.records),html=evaluate('schoolExamAgenda(records)');
assert.equal((html.match(/期中考试/g)||[]).length,2);
assert.equal((html.match(/期末考试/g)||[]).length,2);
assert.equal((html.match(/独立来源记录/g)||[]).length,4);
assert.equal(JSON.stringify(context.records),before);
""")

    def test_grade_search_is_local_and_sort_keeps_unknowns_and_source_records(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'SYN',auth_mode:'school_client',security_realm:'personal_school_client'}};resetSchoolPresentation();state.schoolOperation='academic';state.schoolYear='2024-2025';state.schoolSeason='AW';state.schoolViews={'academic:2024-2025-AW':{operation:'academic',target_term:'2024-2025-AW',status:'complete',records:[{course_name_raw:'Low',grade_raw:'60'},{course_name_raw:'Text',grade_raw:'良好'},{course_name_raw:'Zero',grade_raw:'0'},{course_name_raw:'Conflict',grade_raw:'100',field_states:{grade_raw:'conflicting'}},{course_name_raw:'High',grade_raw:'90'}]}};renderSchool()");
const source=evaluate("JSON.stringify(schoolView('academic','2024-2025-AW').records)");let calls=0;context.fetch=()=>{calls++;throw Error('no fetch');};
const input=element('#school-grade-search');input.value='High';input.listeners.input({target:input,isComposing:true});assert.equal(evaluate('state.schoolGradeSearch'),'');input.listeners.compositionend({target:input});assert.match(element('#panel-school').innerHTML,/High/);assert.doesNotMatch(element('#panel-school').innerHTML,/良好|上一页|下一页|school-grade-filter|id="school-grade-sort"/);assert.equal(calls,0);
evaluate("state.schoolGradeSearch='';state.schoolGradeSort='grade'");assert.equal(evaluate("schoolGradeRows(schoolView('academic','2024-2025-AW')).map(r=>r.course_name_raw).join(',')"),'High,Low,Zero,Text,Conflict');
evaluate("state.schoolGradeSort='grade_asc'");assert.equal(evaluate("schoolGradeRows(schoolView('academic','2024-2025-AW')).map(r=>r.course_name_raw).join(',')"),'Zero,Low,High,Text,Conflict');
evaluate("state.schoolGradeSort='source'");assert.equal(evaluate("JSON.stringify(schoolGradeRows(schoolView('academic','2024-2025-AW')))"),source);assert.equal(evaluate("JSON.stringify(schoolView('academic','2024-2025-AW').records)"),source);
""")

    def test_compact_exams_keep_each_event_and_nonstandard_times(self):
        self.run_js(r"""
context.events=[{course_name_raw:'Two events',final_exam_time_raw:'2026年11月10日(08:00-10:00)',final_exam_place_raw:'A',midterm_exam_time_raw:'2026-10-26 09:00-11:00',midterm_exam_place_raw:'B'},{course_name_raw:'Unknown',final_exam_time_raw:'按学校通知（待确认）',final_exam_place_raw:'<img>'}];
const html=evaluate('schoolExamAgenda(events)');assert.equal((html.match(/class="exam-compact-row"/g)||[]).length,3);assert.equal((html.match(/Two events/g)||[]).length,2);assert.match(html,/08:00-10:00/);assert.match(html,/按学校通知（待确认）/);assert.match(html,/&lt;img&gt;/);assert.doesNotMatch(html,/<img>|<dl>/);assert.ok(html.indexOf('2026-10-26')<html.indexOf('2026-11-10'));assert.ok(html.indexOf('2026-11-10')<html.indexOf('日期未明确'));
""")

    def test_same_day_exams_sort_known_times_stably_without_rewriting_sources(self):
        self.run_js(r"""
context.events=[
  {course_name_raw:'Afternoon',final_exam_time_raw:'2026-11-10(14:00-16:00)'},
  {course_name_raw:'Unknown clock',final_exam_time_raw:'2026-11-10 时间待通知'},
  {course_name_raw:'Morning',midterm_exam_time_raw:'2026年11月10日(8:30-10:30)'},
  {course_name_raw:'Same time',exam_date:'2026-11-10',start_time:'08:30',end_time:'09:00'},
  {course_name_raw:'Invalid clock',final_exam_time_raw:'2026-11-10(25:70-26:90)'},
  {course_name_raw:'Conflicting clock',exam_date:'2026-11-10',start_time:'07:00',field_states:{start_time:'conflicting'}},
  {course_name_raw:'Undated afternoon',final_exam_time_raw:'14:00-16:00'},
  {course_name_raw:'Undated morning',final_exam_time_raw:'08:00-10:00'}
];
const original=JSON.stringify(context.events),html=evaluate('schoolExamAgenda(events)');
const order=['Morning','Same time','Afternoon','Unknown clock','Invalid clock','Conflicting clock','Undated afternoon','Undated morning'];
for(let i=1;i<order.length;i++)assert.ok(html.indexOf(order[i-1])<html.indexOf(order[i]),order.join(','));
assert.equal(JSON.stringify(context.events),original);assert.equal((html.match(/class="exam-compact-row"/g)||[]).length,context.events.length);
assert.match(html,/时间待通知/);assert.match(html,/25:70-26:90/);
""")

    def test_normalized_exam_fields_are_not_discarded(self):
        self.run_js(r"""
context.examFixture=[{course_name_raw:'Synthetic exam',exam_date:'2026-11-01',start_time:'09:00',end_time:'11:00',place_raw:'Room'}];const html=evaluate('schoolExamAgenda(examFixture)');for(const text of ['2026-11-01','09:00','11:00','Room'])assert.ok(html.includes(text));assert.doesNotMatch(html,/来源未提供考试时间/);
""")
