"""Session-scoped display merging and invalidation, using synthetic records only."""
import unittest
from tests import test_web_contract as web

class SchoolPresentationTests(unittest.TestCase):
    run_js = web.WebContractTests.run_js

    def test_grouping_keeps_attempts_recognitions_and_ambiguous_names(self):
        self.run_js(r"""
context.sources=[{operation:'academic',status:'complete',records:[{course_code_raw:'C',course_name_raw:'Course',grade_raw:'60'},{course_code_raw:'C',course_name_raw:'Course',grade_raw:'90'},{course_name_raw:'Course',grade_raw:'80'}]},{operation:'graduation_audit',status:'complete',records:[{node_kind:'module',node_id:'M',course_name_raw:'Module'},{node_kind:'course',node_id:'N1',parent_node_id:'M',course_code_raw:'C',course_name_raw:'Course',grade_raw:'60'},{node_kind:'course',node_id:'N2',parent_node_id:'M2',course_code_raw:'C',course_name_raw:'Course',completion_status:'unknown'},{node_kind:'course',node_id:'N3',course_code_raw:'C',course_name_raw:'Conflicting name'}]}];
const before=JSON.stringify(context.sources);const groups=evaluate('schoolCourseGroups(sources)');assert.equal(groups.length,3);assert.equal(groups[0].entries.length,4);assert.equal(JSON.stringify(context.sources),before);assert.equal(evaluate("schoolAuditStatus({completion_status:'unknown'})"),'未明确');assert.equal(evaluate("schoolAuditStatus({completion_status:'in_progress'})"),'在修');
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
