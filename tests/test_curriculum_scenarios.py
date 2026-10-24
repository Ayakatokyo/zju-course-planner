"""Synthetic-only tests: source integrity, stages and unsupported assertions."""
from copy import deepcopy
import unittest

from scripts.build_curriculum_scenarios import build_student, extract_rows, schedule, validate_student


def fixture():
    courses = []
    for code, term, credits, selected in [('SYN1001M', '一(秋冬)', '2.5', True),
                                          ('SYN1002M', '一(春夏)', '3.0', True),
                                          ('SYN2001M', '二(秋冬)', '4.0', True),
                                          ('SYN3001M', '三(春夏)', '2.0', True),
                                          ('SYN3002M', '三(短)', '2.0', True),
                                          ('SYN4001M', '四(春夏)', '6.0', True),
                                          ('SYN1003M', '一(秋冬)', '3.0', False)]:
        courses.append({'course_code':code, 'course_name':'合成课程'+code, 'credits':credits,
                        'suggested_term_raw':term, 'suggested_term_choices':schedule(term),
                        'source':{'pdf_sha256':'synthetic-hash','physical_page':1},
                        'module':'合成模块', 'selected':selected})
    return {'plan_key':'TEST','program_name':'虚构测试专业','cohort':2099,
            'pdf_sha256':'synthetic-hash','courses':courses,
            'modules':[{'name':'合成模块','minimum_credits':'19.5'}],
            'unresolved_requirements':[{'name':'合成未确认规则'}],
            'prerequisite_status':'not_verified_no_prerequisites_invented'}


class CurriculumScenarioTests(unittest.TestCase):
    def test_stage_progression_and_short_term(self):
        p=fixture();students=[build_student(p,y) for y in (1,2,3)]
        self.assertEqual([s['summary']['completed_records'] for s in students],[1,3,3])
        self.assertEqual([s['summary']['completed_nominal_credits'] for s in students],['2.5','9.5','9.5'])
        self.assertEqual([r['course_code'] for r in students[0]['planned_next_term']],['SYN1002M'])
        self.assertEqual([r['course_code'] for r in students[2]['planned_next_term']],['SYN3001M'])
        self.assertIn('SYN3002M',[r['course_code'] for r in students[2]['future_courses']])
        for s in students:
            self.assertIsNone(s['graduation_eligible'])
            self.assertIsNone(s['overall_remaining_credits'])
            self.assertFalse(s['offerings'])

    def test_marks_are_stable_across_stages_not_source_grades(self):
        p=fixture();a=build_student(p,1)['academic_records'][0];b=build_student(p,3)['academic_records'][0]
        self.assertEqual(a['grade'],b['grade'])
        self.assertIsNone(a['grade_points'])
        self.assertEqual(a['grade_kind'],'synthetic_generated_65_to_95')
        self.assertEqual(a['recognition_status'],'not_officially_recognized')

    def test_wrong_program_or_cohort_rejected(self):
        for field,value in [('plan_key','OTHER'),('cohort',2098),('pdf_sha256','other')]:
            with self.subTest(field=field):
                p=fixture();s=build_student(p,1);s[field]=value
                with self.assertRaises(ValueError):validate_student(s,p)

    def test_tampered_source_name_credits_and_page_rejected(self):
        for field,value in [('credits','99'),('course_name','其他课程'),('source',{'physical_page':99})]:
            with self.subTest(field=field):
                p=fixture();s=build_student(p,1);s['academic_records'][0][field]=value
                with self.assertRaises(ValueError):validate_student(s,p)

    def test_future_course_cannot_be_completed_early(self):
        p=fixture();s=build_student(p,1)
        row=s['planned_next_term'].pop();row.update(status='simulated_passed',grade='80')
        s['academic_records'].append(row)
        with self.assertRaises(ValueError):validate_student(s,p)

    def test_suggested_term_cannot_be_backdated(self):
        p=fixture();s=build_student(p,1);s['academic_records'][0]['assigned_term']={'study_year':0,'seasons':['秋']}
        with self.assertRaises(ValueError):validate_student(s,p)

    def test_credits_and_counts_must_recompute(self):
        for key in ['completed_nominal_credits','completed_records','next_term_nominal_credits','next_term_courses']:
            with self.subTest(key=key):
                p=fixture();s=build_student(p,1);s['summary'][key]='999'
                with self.assertRaises(ValueError):validate_student(s,p)

    def test_missing_and_duplicate_rows_rejected(self):
        for duplicate in (True,False):
            p=fixture();s=build_student(p,1)
            if duplicate:s['future_courses'].append(deepcopy(s['future_courses'][0]))
            else:s['future_courses'].pop()
            with self.assertRaises(ValueError):validate_student(s,p)

    def test_unknowns_not_silently_filled(self):
        for field,value in [('graduation_eligible',True),('overall_remaining_credits','0'),('offerings',[{}]),('timetable',[]),('exams',[]),('unresolved_requirements',[]),('prerequisite_status','eligible')]:
            with self.subTest(field=field):
                p=fixture();s=build_student(p,1);s[field]=value
                with self.assertRaises(ValueError):validate_student(s,p)

    def test_uncompleted_course_has_no_grade(self):
        p=fixture();s=build_student(p,1);s['planned_next_term'][0]['grade']='95'
        with self.assertRaises(ValueError):validate_student(s,p)

    def test_source_term_alternatives_and_invalid_terms(self):
        self.assertEqual(len(schedule('四(秋冬)/四(春夏)')),2)
        for term in ['一(春秋)','一(秋秋)','2026-2027','五(秋)','一()']:
            with self.assertRaises(ValueError):schedule(term)

    def test_bbox_parser_keeps_source_coordinates_and_drops_watermark(self):
        cells=[('SYN1001M',70),('合成课程',140),('2.5',340),('2.0-1.0',370),('48',415),('一(秋冬)',440)]
        words=''.join(f'<word xMin="{x}" yMin="100" xMax="{x+30}" yMax="109">{text}</word>' for text,x in cells)
        xml='<doc><page>'+words+'<word xMin="150" yMin="100" xMax="200" yMax="122">watermark</word></page></doc>'
        rows=extract_rows(xml,[1],'synthetic-hash')
        self.assertEqual(rows[0]['source']['physical_page'],1)
        self.assertEqual(rows[0]['course_name'],'合成课程')
        self.assertEqual(rows[0]['credits'],'2.5')
        with self.assertRaises(ValueError):extract_rows(xml.replace('<doc>','<doc><page>'+words+'</page>'),[1,2],'synthetic-hash')


if __name__=='__main__':unittest.main()
