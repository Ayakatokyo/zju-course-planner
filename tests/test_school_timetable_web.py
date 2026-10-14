"""Verify timetable positioning and safe rendering with explicit synthetic data."""
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "Node is needed for timetable Web verification")
class SchoolTimetableWebTests(unittest.TestCase):
    def run_js(self, assertions):
        setup = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const element=()=>({innerHTML:'',hidden:false,value:'',addEventListener(){},setAttribute(){},classList:{toggle(){}}});
const context=vm.createContext({document:{querySelector:element,querySelectorAll:()=>[]},setTimeout(){}});
const source=fs.readFileSync(process.argv[1],'utf8').replace(/\nbootstrap\(\);\s*$/,'');
vm.runInContext(source,context);
const evaluate=code=>vm.runInContext(code,context);
const plain=expression=>JSON.parse(evaluate('JSON.stringify('+expression+')'));
"""
        result = subprocess.run([NODE, "-e", setup + "\n" + assertions,
                                 str(ROOT / "web" / "app.js")],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_only_plain_linebreaks_are_interpreted(self):
        self.run_js(r"""
assert.equal(evaluate(`schoolPlainText('合成甲<br>合成乙&lt;br /&gt;合成丙')`),'合成甲\n合成乙\n合成丙');
const markup=evaluate(`schoolTextMarkup('<img src=x onerror=bad()><br>&lt;br&gt;<script>bad()</script>')`);
assert.doesNotMatch(markup,/<(?:img|script)\b/);
assert.match(markup,/&lt;img src=x onerror=bad\(\)&gt;<br><br>&lt;script&gt;/);
assert.equal(evaluate('schoolTextMarkup(null)'),'待确认');
assert.equal(evaluate("schoolTextMarkup('unknown')"),'待确认');
assert.equal(evaluate("schoolTextMarkup('')"),'待确认');
assert.equal(evaluate("schoolTextMarkup(0)"),'0');
assert.deepEqual(plain("schoolMeetingSegments('周二第1节 &lt;注&gt;；周三第2节')"),['周二第1节 &lt;注&gt;','周三第2节']);
""")

    def test_normalized_fields_take_precedence_without_filling_period_gaps(self):
        self.run_js(r"""
const items=plain(`schoolTimetableMeetings({records:[{course_name_raw:'合成课程甲',meetings:[{weekday:2,periods:[1,3],week_set:[1,2,4],season_codes:['秋'],time_raw:'星期待定',place_raw:null}]}]})`);
assert.equal(items.length,1);assert.equal(items[0].weekday,2);assert.deepEqual(items[0].periods,[1,3]);
assert.equal(items[0].scope,'秋 · 1–2、4周');assert.equal(items[0].place,null);
const markup=evaluate(`schoolTimetableMarkup({records:[{course_name_raw:'合成课程甲',meetings:[{weekday:2,periods:[1,3],week_set:[1,2,4],season_codes:['秋']}]}]})`);
assert.match(markup,/data-weekday="2" data-period="1"/);assert.match(markup,/data-weekday="2" data-period="3"/);
assert.doesNotMatch(markup,/data-weekday="2" data-period="2"|地点待确认|school-meeting-place/);
""")

    def test_raw_multiple_meetings_and_places_align_across_encoded_breaks(self):
        self.run_js(r"""
const items=plain(`schoolTimetableMeetings({records:[{course_name_raw:'合成多时段',time_raw:'秋周一第1,2节{第1-8周}&lt;br&gt;冬星期三第3至4节{第2,4,6周}',place_raw:'合成教室甲<br>合成教室乙'}]})`);
assert.equal(items.length,2);assert.deepEqual(items.map(item=>item.weekday),[1,3]);
assert.deepEqual(items.map(item=>item.periods),[[1,2],[3,4]]);
assert.deepEqual(items.map(item=>item.place),['合成教室甲','合成教室乙']);
assert.match(items[0].scope,/秋/);assert.match(items[0].scope,/1-8周/);
assert.match(items[1].scope,/冬/);assert.match(items[1].scope,/2,4,6周/);
const markup=evaluate(`schoolTimetableMarkup({records:[{course_name_raw:'合成多时段',time_raw:'秋周一第1,2节&lt;br&gt;冬周三第3,4节',place_raw:'合成教室甲<br>合成教室乙'}]})`);
assert.match(markup,/rowspan="2"/);assert.doesNotMatch(markup,/&amp;lt;br/);
""")

    def test_legacy_parser_entity_fragments_use_intact_source_without_replacing_normalized_meetings(self):
        self.run_js(r"""
const expression=`{records:[{course_name_raw:'合成旧解析记录',
 time_raw:'秋周一第1,2节{第1-8周}&lt;br&gt;冬周三第3,4节{第2,4,6周}',
 place_raw:'合成教室甲&lt;br&gt;合成教室乙',field_states:{time_raw:'known',meetings:'known'},
 meetings:[
  {time_raw:'秋周一第1,2节{第1-8周}&lt',place_raw:'合成教室甲&lt',weekday:null,periods:null,week_set:null},
  {time_raw:'br&gt',place_raw:'br&gt',weekday:null,periods:null,week_set:null},
  {time_raw:'冬周三第3,4节{第2,4,6周}',place_raw:'合成教室乙',weekday:null,periods:null,week_set:null}
 ]}]}`;
const items=plain(`schoolTimetableMeetings(${expression})`);
assert.equal(items.length,2);assert.deepEqual(items.map(item=>item.weekday),[1,3]);
assert.deepEqual(items.map(item=>item.periods),[[1,2],[3,4]]);
assert.deepEqual(items.map(item=>item.place),['合成教室甲','合成教室乙']);
assert.ok(items.every(item=>!/&lt$|^br&gt$/.test(item.time)));
const markup=evaluate(`schoolTimetableMarkup(${expression})`);
assert.match(markup,/已定位2个时段/);assert.doesNotMatch(markup,/未排入课表/);
const reliable=plain(`schoolTimetableMeetings({records:[{
 time_raw:'周一第1节&lt;br&gt;周三第3节',
 meetings:[{time_raw:'原文&lt',weekday:5,periods:[8,9],week_set:[2,4],field_states:{weekday:'known',periods:'known'}}]
}]})`);
assert.equal(reliable.length,1);assert.equal(reliable[0].weekday,5);assert.deepEqual(reliable[0].periods,[8,9]);
const fragmentOnly=plain(`schoolTimetableMeetings({records:[{time_raw:'来源时间待定',meetings:[{time_raw:'br&gt',weekday:null,periods:null}]}]})`);
assert.equal(fragmentOnly[0].weekday,null);assert.deepEqual(fragmentOnly[0].periods,[]);
""")

    def test_missing_or_ambiguous_time_is_preserved_in_pending_section(self):
        self.run_js(r"""
const cases=[null,'星期待定','周二节次待定','第3,4节','周一、周三第1,2节','周1第2节'];
for(const time of cases){
  const markup=evaluate(`schoolTimetableMarkup({records:[{course_name_raw:'合成待定',time_raw:${JSON.stringify(time)}}]})`);
  assert.match(markup,/未排入课表的课程/);assert.match(markup,/school-timetable-pending/);
  assert.doesNotMatch(markup,/<table class="school-timetable"/);
}
const partial=plain(`schoolTimetableMeetings({records:[{time_raw:'周二第3,4节'}]})`)[0];
assert.equal(partial.weekday,2);assert.deepEqual(partial.periods,[3,4]);
assert.equal(partial.scope,'');
const odd=plain(`schoolTimetableMeetings({records:[{time_raw:'周二第3,4节{单周}'}]})`)[0];
assert.equal(odd.scope,'单周');
const annotated=plain(`schoolTimetableMeetings({records:[{time_raw:'周二第3,4节{第1-8周;双周}'}]})`);
assert.equal(annotated.length,1);assert.match(annotated[0].scope,/1-8周/);assert.match(annotated[0].scope,/双周/);
const knownDay=evaluate(`schoolTimetableMarkup({records:[{course_name_raw:'合成部分时间',meetings:[{weekday:4,periods:null,time_raw:null}]}]})`);
assert.match(knownDay,/<span>周四<\/span>/);assert.doesNotMatch(knownDay,/节次待确认|分学期待确认|周次待确认/);
""")

    def test_unreviewed_codes_do_not_create_weeks_terms_or_enrollment(self):
        self.run_js(r"""
const item=plain(`schoolTimetableMeetings({records:[{time_raw:'周四第5,6节',sfqd_raw:'1',dsz_raw:'2',xxq_raw:'3',formal_enrollment_status:'unknown'}]})`)[0];
assert.equal(item.scope,'');assert.deepEqual(item.periods,[5,6]);
const markup=evaluate(`schoolTimetableMarkup({records:[{course_name_raw:'合成原码',time_raw:'周四第5,6节',sfqd_raw:'1',dsz_raw:'2',xxq_raw:'3',formal_enrollment_status:'unknown'}]})`);
assert.doesNotMatch(markup,/正式中选|是否确定原值|单双周原值|分学期原值|待确认|待核验/);
assert.doesNotMatch(markup,/已获学分|已确认中选|周次已确认/);
""")

    def test_overlap_is_parallel_without_claiming_same_week_conflict(self):
        self.run_js(r"""
const markup=evaluate(`schoolTimetableMarkup({records:[
  {course_name_raw:'合成秋时段',meetings:[{weekday:1,periods:[1,2],week_set:[1,3],season_codes:['秋']}]},
  {course_name_raw:'合成冬时段',meetings:[{weekday:1,periods:[1,2],week_set:[2,4],season_codes:['冬']}]}
]})`);
const occupied=markup.match(/<td rowspan="2" class="school-timetable-occupied" data-weekday="1" data-period="1">([\s\S]*?)<\/td>/)[1];
assert.match(occupied,/合成秋时段/);assert.match(occupied,/合成冬时段/);
assert.match(occupied,/秋 · 1、3周/);assert.match(occupied,/冬 · 2、4周/);
assert.equal((occupied.match(/<article/g)||[]).length,2);assert.doesNotMatch(markup,/冲突/);
""")

    def test_explicit_chinese_seasons_are_displayed_without_interpreting_codes(self):
        self.run_js(r"""
const seasonFixture=`{records:[{time_raw:'周二第1节',xxq_raw:'秋、冬',field_states:{xxq_raw:'pending_verification'},meetings:[{weekday:2,periods:[1],xxq_raw:'春/夏',field_states:{xxq_raw:'pending_verification'}}]}]}`;
const item=plain(`schoolTimetableMeetings(${seasonFixture})`)[0];assert.equal(item.scope,'春/夏');
const inherited=plain(`schoolTimetableMeetings({records:[{time_raw:'周二第1节',xxq_raw:'秋、冬',field_states:{xxq_raw:'pending_verification'}}]})`)[0];assert.equal(inherited.scope,'秋、冬');
for(const raw of ['1','秋冬1','秋冬课','<img src=x onerror=bad()>秋','unknown']){
 const items=plain(`schoolTimetableMeetings({records:[{time_raw:'周二第1节',xxq_raw:${JSON.stringify(raw)}}]})`);assert.equal(items[0].scope,'');
 const markup=evaluate(`schoolTimetableMarkup({records:[{course_name_raw:'合成无代码推断',time_raw:'周二第1节',xxq_raw:${JSON.stringify(raw)}}]})`);assert.doesNotMatch(markup,/分学期原文|school-meeting-scope|待核验|待确认|<img/);
}
const conflict=plain(`schoolTimetableMeetings({records:[{time_raw:'周二第1节',xxq_raw:'秋',meetings:[{weekday:2,periods:[1],xxq_raw:'冬',field_states:{xxq_raw:'conflicting'}}]}]})`)[0];assert.equal(conflict.scope,'');
""")

    def test_unknown_course_and_only_known_fields_survive_without_placeholders(self):
        self.run_js(r"""
const markup=evaluate(`schoolTimetableMarkup({records:[{course_name_raw:'合成未排课程',course_code_raw:'SYNTHETIC-A',instructor_raw:null,time_raw:null,place_raw:null,formal_enrollment_status:'unknown',meetings:[{weekday:4,periods:null,time_raw:null}]}]})`);
assert.match(markup,/未排入课表的课程/);assert.match(markup,/合成未排课程/);assert.match(markup,/<span>周四<\/span>/);
assert.match(markup,/空格不表示空闲/);assert.doesNotMatch(markup,/地点待确认|星期待确认|节次待确认|正式中选待确认|周次待确认|分学期待确认|school-meeting-place|school-meeting-teacher|school-meeting-status/);
assert.doesNotMatch(markup,/<dt>教师<\/dt>|<dt>上课时间<\/dt>|<dt>上课地点<\/dt>|<dt>正式中选<\/dt>/);
const known=evaluate(`schoolTimetableMarkup({records:[{course_name_raw:'合成已排课程',time_raw:'秋周一第1节{第1-8周}',place_raw:'合成地点',instructor_raw:'合成教师',formal_enrollment_status:'selected'}]})`);
assert.match(known,/合成地点/);assert.match(known,/合成教师/);assert.match(known,/正式中选/);assert.match(known,/秋 · 第1-8周/);
const conflicting=evaluate(`schoolTimetableMarkup({records:[{course_name_raw:'合成冲突来源',time_raw:'周一第1节',place_raw:'不展示冲突地点',instructor_raw:'不展示冲突教师',field_states:{place_raw:'conflicting',instructor_raw:'conflicting'}}]})`);assert.doesNotMatch(conflicting,/不展示冲突地点|不展示冲突教师/);
""")

    def test_related_exams_match_unique_verified_selection_ids_and_remain_independent(self):
        self.run_js(r"""
context.timetable={operation:'timetable',target_term:'2025-2026-AW',records:[{course_name_raw:'合成课程',source_selection_id_raw:'SYNTHETIC-X',course_code_raw:'SYNTHETIC-C',time_raw:'周一第1节',place_raw:'合成上课地点',field_states:{source_selection_id_raw:'known'}}]};
const exam={source_selection_id_raw:'SYNTHETIC-X',course_code_raw:'SYNTHETIC-C',final_exam_time_raw:'合成期末时间',final_exam_place_raw:'合成期末地点',midterm_exam_time_raw:'合成期中时间',midterm_exam_place_raw:null,exam_time_raw:'合成期末时间',field_states:{source_selection_id_raw:'known'}};
context.examSources=[{operation:'exams',target_term:'2025-2026-A',status:'complete_within_scope',records:[exam,{...exam}]},{operation:'exams',target_term:'2025-2026-W',status:'partial_acquisition',records:[{...exam,final_exam_time_raw:'合成冬期末时间',exam_time_raw:'合成冬期末时间',midterm_exam_time_raw:null}]}];
const linked=plain('schoolAttachRelatedExams(timetable,examSources)');assert.equal(linked.records.length,1);assert.equal(linked.records[0].related_exams.length,2);assert.deepEqual(linked.records[0].related_exams.map(exam=>exam.source_season),['A','W']);assert.equal(linked.records[0].time_raw,'周一第1节');assert.equal(linked.records[0].place_raw,'合成上课地点');assert.equal(linked.records[0].course_code_raw,'SYNTHETIC-C');assert.equal(context.timetable.records[0].related_exams,undefined);
context.linked=linked;const markup=evaluate('schoolTimetableMarkup(linked)');assert.match(markup,/考试（秋）/);assert.match(markup,/考试（冬）/);assert.match(markup,/期末时间：合成期末时间/);assert.match(markup,/期中时间：合成期中时间/);assert.match(markup,/期末地点：合成期末地点/);assert.doesNotMatch(markup,/期中地点：|<br>考试时间：合成期末时间|待确认|无考试/);
""")

    def test_related_exams_never_use_course_names_or_ambiguous_selection_ids(self):
        self.run_js(r"""
const known={source_selection_id_raw:'known'};
context.timetable={operation:'timetable',target_term:'2025-2026-AW',records:[
 {course_name_raw:'同名合成课',source_selection_id_raw:'SYNTHETIC-A',course_code_raw:'CA',field_states:known},
 {course_name_raw:'同名合成课',source_selection_id_raw:'SYNTHETIC-B',course_code_raw:'CB',field_states:known},
 {course_name_raw:'重复选课号甲',source_selection_id_raw:'SYNTHETIC-DUP',field_states:known},
 {course_name_raw:'重复选课号乙',source_selection_id_raw:'SYNTHETIC-DUP',field_states:known},
 {course_name_raw:'未验证选课号',source_selection_id_raw:'SYNTHETIC-UNVERIFIED',field_states:{source_selection_id_raw:'conflicting'}},
 {course_name_raw:'无选课号',source_selection_id_raw:null,field_states:known}
]};
context.examSources=[{operation:'exams',target_term:'2025-2026-A',status:'complete',records:[
 {course_name_raw:'同名合成课',source_selection_id_raw:'SYNTHETIC-B',course_code_raw:'CB',final_exam_time_raw:'仅关联B',field_states:known},
 {course_name_raw:'同名合成课',source_selection_id_raw:null,final_exam_time_raw:'不得按名关联',field_states:known},
 {source_selection_id_raw:'SYNTHETIC-DUP',final_exam_time_raw:'歧义不关联',field_states:known},
 {source_selection_id_raw:'SYNTHETIC-A',course_code_raw:'WRONG-CODE',final_exam_time_raw:'代码冲突不关联',field_states:known},
 {source_selection_id_raw:'SYNTHETIC-UNVERIFIED',final_exam_time_raw:'未知选课号不关联',field_states:known},
 {source_selection_id_raw:'SYNTHETIC-A',final_exam_time_raw:'未验证考试号不关联',field_states:{source_selection_id_raw:'unknown'}}
]}];
const linked=plain('schoolAttachRelatedExams(timetable,examSources)');assert.deepEqual(linked.records.map(record=>record.related_exams.length),[0,1,0,0,0,0]);assert.equal(linked.records[1].related_exams[0].final_exam_time_raw,'仅关联B');
""")

    def test_related_exam_scope_section_conflicts_and_empty_values_are_not_filled(self):
        self.run_js(r"""
const known={source_selection_id_raw:'known'};
context.timetable={operation:'timetable',target_term:'2025-2026-SS',records:[{course_name_raw:'合成课',source_selection_id_raw:'SYNTHETIC-A',source_section_id:'SYNTHETIC-SECTION',field_states:known}]};
context.examSources=[{operation:'exams',target_term:'2025-2026-S',status:'complete',records:[{source_selection_id_raw:'SYNTHETIC-A',source_section_id:'WRONG-SECTION',final_exam_time_raw:'不展示班号冲突',field_states:known},{source_selection_id_raw:'SYNTHETIC-A',final_exam_time_raw:null,final_exam_place_raw:'',midterm_exam_time_raw:null,field_states:known}]},{operation:'academic',target_term:'2025-2026-SU',status:'complete',records:[{source_selection_id_raw:'SYNTHETIC-A',final_exam_time_raw:'不展示错操作',field_states:known}]},{operation:'exams',target_term:'2025-2026-A',status:'complete',records:[{source_selection_id_raw:'SYNTHETIC-A',final_exam_time_raw:'不展示错范围',field_states:known}]}];
const linked=plain('schoolAttachRelatedExams(timetable,examSources)');assert.equal(linked.records[0].related_exams.length,1);context.linked=linked;const markup=evaluate('schoolTimetableMarkup(linked)');assert.doesNotMatch(markup,/school-meeting-exam|不展示|期末时间|期末地点|期中时间|待确认|无考试/);
assert.deepEqual(plain("schoolRelatedExamTerms('2025-2026-SS')"),['2025-2026-S','2025-2026-SU']);assert.deepEqual(plain("schoolRelatedExamTerms('2025-2026-A')"),[]);
context.exam={related_exams:[{source_season:'S',place_raw:'<img src=x onerror=bad()>',final_exam_time_raw:'<script>bad()</script>',midterm_exam_place_raw:'不展示冲突地点',field_states:{midterm_exam_place_raw:'conflicting'}}]};const safe=evaluate('schoolRelatedExamMarkup(exam)');assert.match(safe,/考试（春）/);assert.match(safe,/&lt;script&gt;/);assert.match(safe,/&lt;img/);assert.doesNotMatch(safe,/<script>|<img|不展示冲突地点/);
""")

    def test_cross_season_exam_dedup_requires_identical_full_raw_and_business_values(self):
        self.run_js(r"""
context.timetable={operation:'timetable',target_term:'2025-2026-AW',records:[{course_name_raw:'合成课',source_selection_id_raw:'SYNTHETIC-X',field_states:{source_selection_id_raw:'known'}}]};
const exam={source_selection_id_raw:'SYNTHETIC-X',source_exam_id:'SYNTHETIC-EXAM',final_exam_time_raw:'合成考试时间',exam_status_raw:'scheduled',source_row_index:0,field_states:{source_selection_id_raw:'known',final_exam_time_raw:'known'},raw_row:{xkkh:'SYNTHETIC-X',ks_id:'SYNTHETIC-EXAM',kssj:'合成考试时间',exam_status:'scheduled',extra:{a:1,b:[2,3]}}};
const twin={...exam,source_row_index:7,field_states:{final_exam_time_raw:'known',source_selection_id_raw:'known'},raw_row:{extra:{b:[2,3],a:1},exam_status:'scheduled',kssj:'合成考试时间',ks_id:'SYNTHETIC-EXAM',xkkh:'SYNTHETIC-X'}};
const linked=other=>{context.examSources=[{operation:'exams',target_term:'2025-2026-A',status:'complete',records:[exam]},{operation:'exams',target_term:'2025-2026-W',status:'complete',records:[other]}];return plain('schoolAttachRelatedExams(timetable,examSources)');};
const merged=linked(twin);assert.equal(merged.records[0].related_exams.length,1);assert.deepEqual(merged.records[0].related_exams[0].source_seasons,['A','W']);assert.deepEqual(merged.records[0].related_exams[0].source_target_terms,['2025-2026-A','2025-2026-W']);assert.deepEqual(merged.related_exam_sources.map(source=>source.matched_rows),[1,1]);
context.merged=merged;const markup=evaluate('schoolTimetableMarkup(merged)');assert.equal((markup.match(/school-meeting-exam/g)||[]).length,1);assert.match(markup,/考试（秋\/冬来源）/);
for(const other of [
 {...twin,raw_row:undefined},
 {...twin,raw_row:[]},
 {...twin,raw_row:{...twin.raw_row,exam_status:'cancelled'}},
 {...twin,raw_row:{...twin.raw_row,ks_id:'DIFFERENT-RAW-ID'}},
 {...twin,raw_row:{...twin.raw_row,any_extra_key:'DIFFERENT'}},
 {...twin,source_exam_id:'DIFFERENT-NORMALIZED-ID'},
 {...twin,exam_status_raw:'cancelled'},
 {...twin,field_states:{source_selection_id_raw:'known',final_exam_time_raw:'pending_verification'}}
])assert.equal(linked(other).records[0].related_exams.length,2);
const noRaw={...exam};delete noRaw.raw_row;context.examSources=[{operation:'exams',target_term:'2025-2026-A',status:'complete',records:[noRaw]},{operation:'exams',target_term:'2025-2026-W',status:'complete',records:[{...noRaw}]}];assert.equal(plain('schoolAttachRelatedExams(timetable,examSources)').records[0].related_exams.length,2);
assert.equal(exam.source_seasons,undefined);assert.equal(twin.source_target_terms,undefined);
""")

    def test_related_exam_rejects_known_row_year_or_chinese_season_scope_conflicts(self):
        self.run_js(r"""
const base={source_selection_id_raw:'OPAQUE-SYNTHETIC-X',field_states:{source_selection_id_raw:'known',academic_year_raw:'known',term_raw:'known'}};
const linked=(courseYear,examYear,term='秋、冬',sourceSeason='A',extra={})=>{
 context.timetable={operation:'timetable',target_term:'2025-2026-AW',records:[{...base,academic_year_raw:courseYear,xxq_raw:'秋',course_name_raw:'合成秋课'}]};
 context.examSources=[{operation:'exams',target_term:`2025-2026-${sourceSeason}`,status:'complete',records:[{...base,academic_year_raw:examYear,term_raw:term,final_exam_time_raw:'合成考试时间',...extra}]}];
 return plain('schoolAttachRelatedExams(timetable,examSources)').records[0].related_exams.length;
};
assert.equal(linked('2025-2026','2024-2025'),0);assert.equal(linked('2024-2025','2025-2026'),0);assert.equal(linked('2024-2025','2024-2025'),0);
assert.equal(linked('2025-2026','2025'),1);assert.equal(linked(2025,'2025-2026'),1);assert.equal(linked('2025-2026',null),1);assert.equal(linked(null,'2025'),1);
assert.equal(linked('2025-2026','未核验年份2024'),1);
assert.equal(linked('2025-2026','2024','秋','A',{field_states:{source_selection_id_raw:'known',academic_year_raw:'unknown',term_raw:'known'}}),1);
assert.equal(linked('2025-2026','2025','冬','A'),0);assert.equal(linked('2025-2026','2025','秋','W'),0);assert.equal(linked('2025-2026','2025','秋/春','A'),0);
assert.equal(linked('2025-2026','2025','秋、冬','A'),1);assert.equal(linked('2025-2026','2025','秋冬','W'),1);assert.equal(linked('2025-2026','2025','冬','W'),1);
assert.equal(linked('2025-2026','2025','1','A'),1);assert.equal(linked('2025-2026','2025','春','A',{field_states:{source_selection_id_raw:'known',academic_year_raw:'known',term_raw:'pending_verification'}}),1);
assert.deepEqual(plain("schoolRelatedExamTerms('2025-2028-AW')"),[]);
""")

    def test_explicit_chinese_parity_is_preserved_without_decoding_numeric_codes(self):
        self.run_js(r"""
const known=plain(`schoolTimetableMeetings({records:[{time_raw:'周一第1节',dsz_raw:'单周',field_states:{dsz_raw:'pending_verification'},meetings:[{weekday:1,periods:[1],dsz_raw:'双周',field_states:{dsz_raw:'pending_verification'}}]}]})`)[0];assert.equal(known.scope,'双周');
const inherited=plain(`schoolTimetableMeetings({records:[{time_raw:'周一第1节',dsz_raw:'单周',field_states:{dsz_raw:'pending_verification'}}]})`)[0];assert.equal(inherited.scope,'单周');
const noDuplicate=plain(`schoolTimetableMeetings({records:[{time_raw:'周一第1节{第1-8周;单周}',dsz_raw:'单周'}]})`)[0];assert.equal((noDuplicate.scope.match(/单周/g)||[]).length,1);
for(const raw of ['1','双周1','隔周','unknown'])assert.equal(plain(`schoolTimetableMeetings({records:[{time_raw:'周一第1节',dsz_raw:${JSON.stringify(raw)}}]})`)[0].scope,'');
const conflict=plain(`schoolTimetableMeetings({records:[{time_raw:'周一第1节',dsz_raw:'单周',field_states:{dsz_raw:'conflicting'}}]})`)[0];assert.equal(conflict.scope,'');
""")

    def test_period_clock_uses_only_validated_school_page_metadata(self):
        self.run_js(r"""
context.result={operation:'timetable',records:[{course_name_raw:'合成课',meetings:[{weekday:1,periods:[1,2]}]}],period_times_source:{state:'known',page_path:'/jwglxt/kbcx/xskbcx_cxXskbcxIndex.html'},period_times:[{period:1,start_time:'08:05',end_time:'08:50'},{period:2,start_time:'09:00',end_time:'09:45'}]};
let markup=evaluate('schoolTimetableMarkup(result)');assert.match(markup,/<th scope="row" class="school-timetable-period">1<br><small class="school-period-clock">08:05–08:50<\/small>/);assert.match(markup,/09:00–09:45/);assert.doesNotMatch(markup,/08:00/);assert.match(evaluate('schoolResultDetails(result)'),/节次起止时间来自本次读取的学校课表页面/);
const original=context.result.period_times;
for(const invalid of [[{period:0,start_time:'08:05',end_time:'08:50'}],[{period:'1',start_time:'08:05',end_time:'08:50'}],[{period:1,start_time:'25:00',end_time:'25:30'}],[{period:1,start_time:'8:05',end_time:'08:50'}],[{period:1,start_time:'09:00',end_time:'08:50'}],[...original,{period:1,start_time:'08:00',end_time:'08:50'}],[],null]){
 context.result.period_times=invalid;assert.doesNotMatch(evaluate('schoolTimetableMarkup(result)'),/school-period-clock/);
}
context.result.period_times=original;context.result.period_times_source.state='unknown';assert.doesNotMatch(evaluate('schoolTimetableMarkup(result)'),/school-period-clock/);context.result.period_times_source.state='known';context.result.period_times_source.page_path='/unverified-page';assert.doesNotMatch(evaluate('schoolTimetableMarkup(result)'),/school-period-clock/);
delete context.result.period_times_source;delete context.result.period_times;assert.doesNotMatch(evaluate('schoolTimetableMarkup(result)'),/school-period-clock|08:05|09:00/);
""")

    def test_main_campus_reference_includes_verified_nighttime_and_explicit_source(self):
        self.run_js(r"""
context.result={operation:'timetable',target_term:'2025-2026-AW',records:[{course_name_raw:'合成课',place_raw:'合成地点（紫金港校区）',field_states:{place_raw:'known'},meetings:[{weekday:1,periods:[1,10,11,12,13]}]}]};
assert.equal(evaluate('schoolPeriodTimes(result).size'),0);assert.equal(evaluate('schoolPeriodTimeDisplay(result).origin'),'public_reference');assert.equal(evaluate('schoolPeriodTimeDisplay(result).times.get(1)'),'08:00–08:45');assert.equal(evaluate('schoolPeriodTimeDisplay(result).times.get(10)'),'17:05–17:50');
assert.equal(evaluate('schoolPeriodTimeDisplay(result).times.get(11)'),'18:50–19:35');assert.equal(evaluate('schoolPeriodTimeDisplay(result).times.get(12)'),'19:40–20:25');assert.equal(evaluate('schoolPeriodTimeDisplay(result).times.get(13)'),'20:30–21:15');assert.equal(evaluate('schoolPeriodTimeDisplay(result).times.has(14)'),false);
const markup=evaluate('schoolTimetableMarkup(result)');assert.match(markup,/08:00–08:45/);assert.match(markup,/17:05–17:50/);assert.match(markup,/节次时间参考2025–2026校历（校本部）/);assert.match(markup,/https:\/\/ism.zju.edu.cn\//);
assert.match(markup,/class="school-timetable-period">11<br><small class="school-period-clock">18:50–19:35/);assert.match(markup,/19:40–20:25/);assert.match(markup,/20:30–21:15/);assert.doesNotMatch(markup,/18:30/);const details=evaluate('schoolResultDetails(result)');assert.match(details,/公开作息参考，非本次课表接口返回值/);assert.doesNotMatch(details,/来自本次读取的学校课表页面/);
""")

    def test_main_campus_reference_requires_matching_semester_and_known_place_evidence(self):
        self.run_js(r"""
context.result={operation:'timetable',target_term:'2025-2026-SS',records:[{place_raw:'合成玉泉校区地点',field_states:{place_raw:'known'}}]};assert.equal(evaluate('schoolPeriodTimeDisplay(result).origin'),'public_reference');
for(const term of ['2026-2027-AW','2026-2027-SS']){context.result.target_term=term;assert.equal(evaluate('schoolPeriodTimeDisplay(result).origin'),'public_reference');assert.equal(evaluate('schoolPeriodTimeDisplay(result).reference_year'),'2026-2027');assert.match(evaluate('schoolPeriodTimeDisplay(result).reference_url'),/^https:\/\/ugrs.zju.edu.cn\//);assert.match(evaluate('schoolResultDetails(result)'),/节次时间参考2026–2027校历（校本部）/);}
for(const term of ['2024-2025-AW','2027-2028-SS','2025-2026-A','2025-2026-W',undefined]){context.result.target_term=term;assert.equal(evaluate('schoolPeriodTimeDisplay(result).origin'),'none');}
context.result.target_term='2025-2026-AW';
for(const campus of ['紫金港','玉泉','西溪','华家池','之江']){context.result.records=[{meetings:[{place_raw:`合成${campus}地点`,field_states:{place_raw:'known'}}]}];assert.equal(evaluate('schoolPeriodTimeDisplay(result).origin'),'public_reference');}
const encoded='&#x7d2b;&#x91d1;&#x6e2f;&lt;br&gt;合成地点';context.result.records=[{place_raw:encoded,field_states:{place_raw:'known'}}];assert.equal(evaluate('schoolPeriodTimeDisplay(result).origin'),'public_reference');assert.equal(context.result.records[0].place_raw,encoded);
context.result.records.push({place_raw:'&#28023;&#23425;国际校区合成地点',field_states:{place_raw:'known'}});assert.equal(evaluate('schoolPeriodTimeDisplay(result).origin'),'none');
for(const campus of ['海宁国际校区','国际校区','工程师学院','义乌','舟山','宁波']){context.result.records=[{place_raw:'合成紫金港地点'},{meetings:[{place_raw:`合成${campus}地点`,field_states:{place_raw:'known'}}]}];assert.equal(evaluate('schoolPeriodTimeDisplay(result).origin'),'none');}
for(const records of [[{place_raw:null}],[{place_raw:'合成未知地点'}],[{place_raw:'合成紫金港地点',field_states:{place_raw:'unknown'}}],[{place_raw:'合成玉泉地点',field_states:{place_raw:'conflicting'}}],[{course_name_raw:'合成玉泉课程',major_name:'合成紫金港专业',source_subject_id:'SYNTHETIC-玉泉'}]]){context.result.records=records;assert.equal(evaluate('schoolPeriodTimeDisplay(result).origin'),'none');}
context.result.records=[{place_raw:'合成紫金港地点'}];context.result.reference_url='https://example.invalid/caller-override.pdf';assert.match(evaluate('schoolPeriodTimeDisplay(result).reference_url'),/^https:\/\/ism.zju.edu.cn\//);assert.equal(evaluate('Object.isFrozen(schoolMainCampusClockReference.urls)'),true);
for(const operation of ['academic','major_academic','profile','exams',undefined]){context.result.operation=operation;assert.equal(evaluate('schoolPeriodTimeDisplay(result).origin'),'none');assert.doesNotMatch(evaluate('schoolResultDetails(result)'),/节次时间参考|节次起止时间/);}
""")

    def test_valid_page_times_take_priority_without_reference_gap_filling(self):
        self.run_js(r"""
context.result={operation:'timetable',target_term:'2025-2026-AW',records:[{place_raw:'合成紫金港地点',meetings:[{weekday:1,periods:[1,2,11]}]}],period_times_source:{state:'known',page_path:'/jwglxt/kbcx/xskbcx_cxXskbcxIndex.html'},period_times:[{period:1,start_time:'08:05',end_time:'08:50'}]};
assert.equal(evaluate('schoolPeriodTimeDisplay(result).origin'),'school_page');assert.equal(evaluate('schoolPeriodTimeDisplay(result).times.size'),1);assert.equal(evaluate('schoolPeriodTimeDisplay(result).times.has(2)'),false);assert.equal(evaluate('schoolPeriodTimeDisplay(result).times.has(11)'),false);
const markup=evaluate('schoolTimetableMarkup(result)');assert.match(markup,/08:05–08:50/);assert.doesNotMatch(markup,/08:00–08:45|08:50–09:35|18:50|节次时间参考/);const details=evaluate('schoolResultDetails(result)');assert.match(details,/来自本次读取的学校课表页面/);assert.doesNotMatch(details,/公开作息参考/);
context.result.target_term='2026-2027-AW';context.result.records[0].place_raw='合成海宁地点';assert.equal(evaluate('schoolPeriodTimeDisplay(result).origin'),'school_page');
""")

    def test_known_invalid_page_times_do_not_silently_switch_to_public_reference(self):
        self.run_js(r"""
context.result={operation:'timetable',target_term:'2025-2026-AW',records:[{place_raw:'合成紫金港地点',meetings:[{weekday:1,periods:[1]}]}],period_times_source:{state:'known',page_path:'/jwglxt/kbcx/xskbcx_cxXskbcxIndex.html'}};
for(const values of [null,[],[{period:1,start_time:'25:00',end_time:'25:45'}],[{period:1,start_time:'08:05',end_time:'08:50'},{period:1,start_time:'08:00',end_time:'08:45'}]]){
 context.result.period_times=values;assert.equal(evaluate('schoolPeriodTimeDisplay(result).origin'),'none');assert.doesNotMatch(evaluate('schoolTimetableMarkup(result)'),/school-period-clock|节次时间参考/);
}
context.result.period_times=[{period:1,start_time:'08:05',end_time:'08:50'}];context.result.period_times_source.page_path='/unverified-page';assert.equal(evaluate('schoolPeriodTimeDisplay(result).origin'),'none');
""")

    def test_rowspans_preserve_grid_positions_when_overlap_members_change(self):
        self.run_js(r"""
const markup=evaluate(`schoolTimetableMarkup({records:[
 {course_name_raw:'合成A',meetings:[{weekday:1,periods:[1,2,3]}]},
 {course_name_raw:'合成B',meetings:[{weekday:1,periods:[2,3]}]},
 {course_name_raw:'合成C',meetings:[{weekday:1,periods:[3,4]}]},
 {course_name_raw:'合成D',meetings:[{weekday:2,periods:[1,3]}]},
 {course_name_raw:'合成E',meetings:[{weekday:7,periods:[2,3,4]}]}
]})`);
const body=markup.match(/<tbody>([\s\S]*?)<\/tbody>/)[1];
const grid=[],remaining=Array(7).fill(0),content=Array.from({length:7},()=>[]);
for(const row of body.matchAll(/<tr>([\s\S]*?)<\/tr>/g)){
 const cells=[...row[1].matchAll(/<td\b([^>]*)>([\s\S]*?)<\/td>/g)];let cursor=0;
 const dayValues=[];
 for(let day=0;day<7;day+=1){
  if(remaining[day])remaining[day]-=1;
  else{
   assert.ok(cursor<cells.length,'each row must have all seven weekday positions');const cell=cells[cursor++];
   const position=cell[1].match(/data-weekday="(\d+)"/);if(position)assert.equal(Number(position[1]),day+1);
   remaining[day]=Number(cell[1].match(/rowspan="(\d+)"/)?.[1]||1)-1;
   content[day]=[...cell[2].matchAll(/<strong>(合成[A-E])<\/strong>/g)].map(match=>match[1]).sort();
  }
  dayValues.push([...content[day]]);
 }
 assert.equal(cursor,cells.length,'rowspan cells must not push later weekday columns');grid.push(dayValues);
}
assert.deepEqual(grid.map(row=>row[0]),[['合成A'],['合成A','合成B'],['合成A','合成B','合成C'],['合成C']]);
assert.deepEqual(grid.map(row=>row[1]),[['合成D'],[],['合成D'],[]]);
assert.deepEqual(grid.map(row=>row[6]),[[],['合成E'],['合成E'],['合成E']]);
assert.ok(grid.every(row=>row.slice(2,6).every(cell=>cell.length===0)));
""")

    def test_conflicting_normalized_fields_are_not_positioned(self):
        self.run_js(r"""
const items=plain(`schoolTimetableMeetings({records:[{meetings:[{weekday:1,periods:[1,2],time_raw:'周一第1,2节',field_states:{weekday:'conflicting',periods:'pending_verification'}}]}]})`);
assert.equal(items[0].weekday,null);assert.deepEqual(items[0].periods,[]);
assert.equal(evaluate("schoolFormalEnrollmentText({formal_enrollment_status:'selected',field_states:{formal_enrollment_status:'conflicting'}})"),'');
""")

    def test_unaligned_places_remain_unknown_in_grid_and_visible_as_source(self):
        self.run_js(r"""
const expression=`{records:[{course_name_raw:'合成地点未对齐',time_raw:'周一第1节<br>周二第2节',place_raw:'合成教室未分配'}]}`;
const items=plain(`schoolTimetableMeetings(${expression})`);
assert.deepEqual(items.map(item=>item.place),[null,null]);
const markup=evaluate(`schoolTimetableMarkup(${expression})`);
assert.doesNotMatch(markup,/地点待确认|school-meeting-place/);assert.match(markup,/合成教室未分配/);
""")

    def test_large_or_invalid_period_numbers_do_not_invent_a_long_calendar(self):
        self.run_js(r"""
assert.deepEqual(plain("schoolParsedMeetingTime('周一第0,1节')").periods,[]);
assert.deepEqual(plain("schoolParsedMeetingTime('周一第1-3000节')").periods,[]);
assert.deepEqual(plain("schoolParsedMeetingTime('周一第1-2-3节')").periods,[]);
const markup=evaluate(`schoolTimetableMarkup({records:[{course_name_raw:'合成大节次',meetings:[{weekday:7,periods:[10000]}]}]})`);
assert.match(markup,/data-weekday="7" data-period="10000"/);
assert.equal((markup.match(/<th scope="row"/g)||[]).length,1);
assert.doesNotMatch(markup,/2026-\d{2}-\d{2}|校历|已确认空闲|无课时段/);
""")

    def test_timetable_xss_is_escaped_in_grid_pending_and_source(self):
        self.run_js(r"""
const markup=evaluate(`schoolTimetableMarkup({records:[
 {course_name_raw:'<script>bad()</script>',instructor_raw:'<img src=x onerror=bad()>',time_raw:'周二第1,2节',place_raw:'<svg onload=bad()>'},
 {course_name_raw:'合成待定<img src=x>',time_raw:'<iframe src=javascript:bad()>',place_raw:'<b>合成地点</b>'}
]})`);
assert.doesNotMatch(markup,/<(?:script|img|svg|iframe|b)\b/);
assert.match(markup,/&lt;script&gt;/);assert.match(markup,/&lt;svg onload=bad\(\)&gt;/);
assert.match(markup,/&lt;iframe src=javascript:bad\(\)&gt;/);
assert.match(markup,/空格不表示空闲/);assert.match(markup,/本次返回未定位时段/);
""")


if __name__ == "__main__":
    unittest.main()
