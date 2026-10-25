"""Arithmetic over parsed curriculum rows; never credit recognition or rules."""
from decimal import Decimal
import re
import unicodedata


def summarize_course_rows(rows):
    def credit(value):
        text = str(value)
        return Decimal(text) if re.fullmatch(r"\d{1,3}(?:\.\d{1,4})?", text) and Decimal(text) <= 100 else None

    def summarize(values):
        known = [value for value in values if value is not None]
        return {"course_rows": len(values), "known_credit_rows": len(known),
                "unknown_credit_rows": len(values) - len(known),
                "known_nominal_credits_sum": str(sum(known, Decimal('0')))}

    values, groups, unknown_terms = [], {}, []
    for row in rows:
        value = credit(row.get('credits'))
        values.append(value)
        term = row.get('suggested_term_raw')
        if not isinstance(term, str) or not term.strip() or len(term) > 80:
            unknown_terms.append(value)
            continue
        term = re.sub(r'\s+', '', unicodedata.normalize('NFKC', term))
        groups.setdefault(term, []).append(value)
    return {**summarize(values),
            "suggested_terms": [{"suggested_term_raw": term, **summarize(group)} for term, group in list(groups.items())[:20]],
            "omitted_term_groups": max(0, len(groups) - 20), "unknown_term_rows": len(unknown_terms),
            "scope": "all_parsed_module_rows_not_credit_requirement_or_earned_credits",
            "note": "代码按来源行相加，不按课程代码去重。已知名义学分合计不等于模块要求、已获学分或毕业认定；建议学期分组不代表实际开课，跨季原值不拆分计数。"}
