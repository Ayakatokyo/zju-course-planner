"""Session-scoped display merging and invalidation, using synthetic records only."""
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

    def test_grouping_keeps_attempts_recognitions_and_ambiguous_names(self):
        self.run_js(r"""
context.sources=[{operation:'academic',status:'complete',records:[{course_code_raw:'C',course_name_raw:'Course',grade_raw:'60'},{course_code_raw:'C',course_name_raw:'Course',grade_raw:'90'},{course_name_raw:'Course',grade_raw:'80'}]},{operation:'graduation_audit',status:'complete',records:[{node_kind:'module',node_id:'M',course_name_raw:'Module'},{node_kind:'course',node_id:'N1',parent_node_id:'M',course_code_raw:'C',course_name_raw:'Course',grade_raw:'60'},{node_kind:'course',node_id:'N2',parent_node_id:'M2',course_code_raw:'C',course_name_raw:'Course',completion_status:'unknown'},{node_kind:'course',node_id:'N3',course_code_raw:'C',course_name_raw:'Conflicting name'}]}];
const before=JSON.stringify(context.sources);const groups=evaluate('schoolCourseGroups(sources)');assert.equal(groups.length,3);assert.equal(groups[0].entries.length,4);assert.equal(JSON.stringify(context.sources),before);assert.equal(evaluate("schoolAuditStatus({completion_status:'unknown'})"),'未明确');assert.equal(evaluate("schoolAuditStatus({completion_status:'in_progress'})"),'在修');
""")

    def test_enrollment_fields_have_separate_columns_and_keep_unknowns(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'SYN',auth_mode:'school_client',security_realm:'personal_school_client'}};state.schoolEnrollment={status:'available',academic_year:'2026-2027',scope:'current',selected:[{kcmc:'Synthetic course',kcdm:'C',xkkh:'SECTION-LONG',sxbj:'0',xkzy:0,jsxm:'Teacher',xxq:'秋',sksj:'周五2-3节',skdd:'Room'},{kcmc:'Unknown course'}]}");
const html=evaluate('schoolEnrollmentMarkup()'),table=html.match(/<table[^>]*school-selected-table[\s\S]*?<\/table>/)[0],rows=[...table.matchAll(/<tbody>([\s\S]*?)<\/tbody>/g)][0][1];
assert.equal((table.match(/<th scope="col">/g)||[]).length,8);
for(const row of rows.match(/<tr>[\s\S]*?<\/tr>/g))assert.equal((row.match(/<td>/g)||[]).length,8);
assert.match(rows,/<td>待筛选<\/td><td>0<\/td><td>Teacher<\/td><td>秋<\/td><td>周五2-3节<\/td><td>Room<\/td>/);assert.match(rows,/状态待核对/);assert.match(rows,/<td>—<\/td>/);assert.doesNotMatch(html,/提交选课<\/button>/);
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

    def test_exam_link_does_not_turn_duplicate_selection_into_unique_match(self):
        self.run_js(r"""
context.source={operation:'timetable',target_term:'2026-2027-AW',records:[{course_name_raw:'One',source_selection_id_raw:'X',field_states:{source_selection_id_raw:'known'}},{course_name_raw:'Two',source_selection_id_raw:'X',field_states:{source_selection_id_raw:'known'}}]};context.exams=[{operation:'exams',target_term:'2026-2027-A',status:'complete',records:[{source_selection_id_raw:'X',field_states:{source_selection_id_raw:'known'},final_exam_time_raw:'2026-11-01',midterm_exam_time_raw:'2026-10-01'}]}];assert.equal(evaluate('schoolAttachRelatedExams(source,exams).records.flatMap(r=>r.related_exams).length'),0);const html=evaluate('schoolExamAgenda(exams[0].records)');assert.match(html,/期中考试/);assert.match(html,/期末考试/);
""")

    def test_selected_enrollment_requires_same_term_unique_instance(self):
        self.run_js(r"""
context.record={course_code_raw:'C',source_selection_id_raw:'X',field_states:{source_selection_id_raw:'known'}};context.preview={status:'available',academic_year:'2026-2027',season:'1',selected:[{xkkh:'X',kcdm:'C',sxbj:'1'}]};
assert.equal(evaluate("schoolSelectedEnrollment(record,[record],preview,'2026-2027','AW').sxbj"),'1');assert.equal(evaluate("schoolSelectedEnrollment(record,[record],preview,'2025-2026','AW')"),null);assert.equal(evaluate("schoolSelectedEnrollment(record,[record,record],preview,'2026-2027','AW')"),null);context.preview.selected.push({...context.preview.selected[0]});assert.equal(evaluate("schoolSelectedEnrollment(record,[record],preview,'2026-2027','AW')"),null);
""")

    def test_normalized_exam_fields_are_not_discarded(self):
        self.run_js(r"""
context.examFixture=[{course_name_raw:'Synthetic exam',exam_date:'2026-11-01',start_time:'09:00',end_time:'11:00',place_raw:'Room'}];const html=evaluate('schoolExamAgenda(examFixture)');for(const text of ['2026-11-01','09:00','11:00','Room'])assert.ok(html.includes(text));assert.doesNotMatch(html,/来源未提供考试时间/);
""")
