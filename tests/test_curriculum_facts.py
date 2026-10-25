import unittest

from course_assistant.curriculum_facts import summarize_course_rows


class CurriculumFactsTests(unittest.TestCase):
    def test_exact_arithmetic_and_cross_season_groups_preserve_source_rows(self):
        result = summarize_course_rows([
            {'course_code': 'SYN1', 'credits': '1.5', 'suggested_term_raw': '二（秋冬）'},
            {'course_code': 'SYN1', 'credits': 2.0, 'suggested_term_raw': '二 (秋冬)'},
            {'credits': '3', 'suggested_term_raw': '一(夏)'}])
        self.assertEqual(result['course_rows'], 3)
        self.assertEqual(result['known_nominal_credits_sum'], '6.5')
        self.assertEqual(len(result['suggested_terms']), 2)
        self.assertEqual(result['suggested_terms'][0]['course_rows'], 2)
        self.assertEqual(result['suggested_terms'][0]['known_nominal_credits_sum'], '3.5')

    def test_unknown_credit_or_term_is_not_filled_with_zero_or_an_inferred_term(self):
        result = summarize_course_rows([{'credits': None}, {'credits': True},
            {'credits': 'nan'}, {'credits': '1.25', 'suggested_term_raw': '一(夏)'}])
        self.assertEqual(result['unknown_credit_rows'], 3)
        self.assertEqual(result['unknown_term_rows'], 3)
        self.assertEqual(result['known_nominal_credits_sum'], '1.25')
