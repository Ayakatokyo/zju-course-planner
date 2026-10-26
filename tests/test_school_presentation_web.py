"""Session-scoped display merging and invalidation, using synthetic records only."""
import unittest
from tests import test_web_contract as web

class SchoolPresentationTests(unittest.TestCase):
    run_js = web.WebContractTests.run_js

    def test_grade_first_does_not_claim_a_failed_audit_link(self):
        self.run_js(r"""
evaluate("resetSchoolPresentation();state.schoolViews={'academic:2024-2025-AW':{operation:'academic',target_term:'2024-2025-AW',status:'complete',records:[{course_name_raw:'Synthetic course',grade_raw:'90',source_selection_id_raw:'selection-one'}]}}");
let html=evaluate('schoolAcademicMarkup(false)');assert.match(html,/自审尚未读取/);assert.match(html,/需先读取培养进度/);assert.match(html,/读取培养进度并核对/);assert.doesNotMatch(html,/未能关联自审|未关联自审|未关联培养模块|归属待核对/);
assert.match(html,/<select id="school-course-status" disabled/);assert.match(html,/<option value="unlinked"[^>]*disabled/);
evaluate("state.schoolViewErrors={'graduation_audit:ALL-HISTORY':'source_unavailable'}");html=evaluate('schoolAcademicMarkup(false)');assert.match(html,/自审读取未完成/);assert.doesNotMatch(html,/自审尚未读取|未能关联自审|归属待核对/);assert.match(html,/90/);
""")

    def test_unmatched_supplements_stay_separate_from_audit_courses(self):
        self.run_js(r"""
evaluate("resetSchoolPresentation();state.schoolViews={audit:{operation:'graduation_audit',target_term:'ALL-HISTORY',status:'complete',records:[{node_kind:'module',node_id:'M',course_name_raw:'Module'},{node_kind:'course',node_id:'N',parent_node_id:'M',course_code_raw:'C',course_name_raw:'Shared name',completion_status_raw:'通过',grade_raw:'80'},{node_kind:'course',node_id:'D',parent_node_id:'M',course_code_raw:'D',course_name_raw:'Audit only',completion_status_raw:'在修'}]},grades:{operation:'academic',target_term:'2024-2025-AW',status:'complete',records:[{course_code_raw:'C',course_name_raw:'Shared name',grade_raw:'90'},{course_name_raw:'Shared name',grade_raw:'70'},{course_code_raw:'X',course_name_raw:'Unmatched grade',grade_raw:'60'}]}};state.schoolViews['graduation_audit:ALL-HISTORY']=state.schoolViews.audit;delete state.schoolViews.audit");
const before=evaluate('JSON.stringify(state.schoolViews)'),html=evaluate('schoolAcademicMarkup(false)'),parts=html.split('<details class="school-unlinked-records"');assert.equal(parts.length,2);assert.equal((parts[0].match(/<tr class="school-course-row">/g)||[]).length,2);assert.equal((parts[1].match(/<tr class="school-course-row">/g)||[]).length,2);
assert.match(parts[0],/通过/);assert.match(parts[0],/在修/);assert.match(parts[0],/多条成绩（2）/);assert.doesNotMatch(parts[0],/未能关联自审|归属待核对/);assert.match(parts[1],/归属待核对/);assert.match(parts[1],/Shared name/);assert.match(parts[1],/Unmatched grade/);assert.equal(evaluate('JSON.stringify(state.schoolViews)'),before);
evaluate("state.schoolViewErrors={'graduation_audit:ALL-HISTORY':'source_unavailable'}");assert.doesNotMatch(evaluate('schoolAcademicMarkup(false)'),/自审尚未读取|自审读取未完成/);
evaluate("state.schoolStatusFilter='unlinked'");const filtered=evaluate('schoolAcademicMarkup(false)');assert.match(filtered,/0 个自审课程显示组 · 2 个待核对的补充显示组/);assert.doesNotMatch(filtered,/Audit only/);
""")

    def test_course_columns_and_open_details_survive_filter_and_source_refresh(self):
        self.run_js(r"""
evaluate("state.auth={authenticated:true,identity:{student_id:'SYN',auth_mode:'school_client',security_realm:'personal_school_client'}};state.schoolYear='2026-2027';state.schoolSeason='AW';resetSchoolPresentation();state.schoolViews={'academic:2026-2027-AW':{operation:'academic',target_term:'2026-2027-AW',status:'complete',records:[{course_code_raw:'C',course_name_raw:'Long course name',grade_raw:'60'},{course_code_raw:'C',course_name_raw:'Long course name',grade_raw:'90'}]}}");
let html=evaluate('schoolAcademicMarkup(false)');
const row=html.match(/<tr class="school-course-row">([\s\S]*?)<\/tr>/)[1];
assert.equal((row.match(/<td>/g)||[]).length,5);assert.doesNotMatch(row,/colspan/);assert.match(row,/多条成绩（2）/);
const button=element('course-button'),detail=element('course-detail');
button.dataset={courseDisclosure:evaluate('schoolCourseDisclosureKey(schoolCourseGroups(Object.values(state.schoolViews))[0])')};button.setAttribute('aria-controls','course-detail');button.setAttribute('aria-expanded','false');detail.hidden=true;
context.document.getElementById=id=>id==='course-detail'?detail:null;
const fold=element('identity-fold');fold.dataset={schoolDisclosure:'identity'};fold.open=true;fold.isConnected=true;
context.document.querySelectorAll=selector=>selector==='[data-course-disclosure]'?[button]:selector==='details[data-school-disclosure]'?[fold]:[];
evaluate('renderSchool()');button.listeners.click();fold.listeners.toggle();assert.equal(detail.hidden,false);assert.equal(button.getAttribute('aria-expanded'),'true');
evaluate("state.schoolCourseQuery='Long';state.schoolViews.extra={operation:'major_academic',status:'complete',target_term:'ALL-HISTORY',records:[{course_code_raw:'C',course_name_raw:'Long course name',grade_raw:'80'}]};renderSchool()");
html=element('#panel-school').innerHTML;assert.match(html,/aria-expanded="true"/);assert.match(html,/<tr class="school-course-expanded" id="school-course-detail-0" >/);assert.match(html,/多条成绩（3）/);assert.match(html,/data-school-disclosure="identity" open/);
button.listeners.click();assert.equal(detail.hidden,true);
evaluate('resetSchoolPresentation()');assert.equal(evaluate('Object.keys(state.schoolOpenCourses).length'),0);assert.equal(evaluate('Object.keys(state.schoolDisclosures).length'),0);
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
context.fetch=async()=>({ok:false,status:502,text:async()=>JSON.stringify({code:'source_unavailable'})});await evaluate("schoolLoadPresentation('academic')");assert.equal(evaluate("schoolView('academic','2026-2027-AW').records[0].course_name_raw"),'OLD');assert.match(element('#panel-school').innerHTML,/上次读取/);assert.match(element('#panel-school').innerHTML,/本次更新未完成/);
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
