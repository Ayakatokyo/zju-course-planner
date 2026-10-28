"""Bounded read-only official enrollment preview; no mutation endpoints."""
from html import unescape
from html.parser import HTMLParser
import re

PAGE = '/jwglxt/xsxk/zzxkghb_cxZzxkGhbIndex.html'
COURSES = '/jwglxt/xsxk/zzxkghb_cxZzxkGhbKcList.html'
SECTIONS = '/jwglxt/xsxk/zzxkghb_cxZzxkGhbJxbList.html'
CHOSEN = '/jwglxt/xsxk/zzxkghb_cxZzxkGhbChoosed.html'
READ_ROUTES = {PAGE, COURSES, SECTIONS, CHOSEN}
PUBLIC_URL = 'https://zdbk.zju.edu.cn' + PAGE + '?gnmkdm=N253530&layout=default'
CONTEXT = {'xn', 'xq', 'nj', 'zydm', 'ylxs'}
SELECTED_FIELDS = {
    'kcmc': 'course_name_raw', 'kcdm': 'course_code_raw', 'xkkh': 'section_id_raw',
    'sxbj': 'selection_status_raw', 'xkzy': 'preference_raw', 'jsxm': 'instructor_raw',
    'xxq': 'term_raw', 'sksj': 'time_raw', 'skdd': 'place_raw',
}


def plain(value):
    if value is None:
        return None
    if type(value) not in (str, int, float) or len(str(value)) > 2000:
        raise ValueError('invalid_enrollment_field')
    return unescape(re.sub(r'<[^>]*>', ' ', str(value))).strip()


class PreviewPage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.context = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input' and attrs.get('id') in CONTEXT:
            key = attrs['id']
            if key in self.context:
                raise ValueError('duplicate_enrollment_context')
            self.context[key] = plain(attrs.get('value'))
    def validated(self):
        if not CONTEXT <= self.context.keys() or not re.fullmatch(r'20\d{2}-20\d{2}', self.context['xn'] or '') or self.context['xq'] not in {'1','2'}:
            raise ValueError('enrollment_context_missing')
        return self


def project_list(value, fields, limit):
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError('invalid_enrollment_list')
    if any(not isinstance(row, dict) for row in value):
        raise ValueError('invalid_enrollment_row')
    return [{key: plain(row.get(key)) for key in fields} for row in value]


def validate_selection_query(args):
    if not isinstance(args, dict) or set(args) - {'query', 'offset', 'limit'}:
        raise ValueError('invalid_selection_query')
    query, offset, limit = args.get('query', ''), args.get('offset', 0), args.get('limit', 15)
    if (not isinstance(query, str) or len(query) > 100 or type(offset) is not int
            or not 0 <= offset <= 100 or type(limit) is not int or not 1 <= limit <= 15):
        raise ValueError('invalid_selection_query')
    return {'query': query, 'offset': offset, 'limit': limit}


def selected_snapshot(preview):
    """Only current-page selected rows; never expose catalogue/context fields."""
    year, season = preview.get('academic_year'), preview.get('season')
    match = re.fullmatch(r'(20\d{2})-(20\d{2})', year) if isinstance(year, str) else None
    if not match or int(match[2]) != int(match[1]) + 1 or season not in {'1', '2'}:
        raise ValueError('invalid_selection_scope')
    rows = project_list(preview.get('selected'), SELECTED_FIELDS, 100)
    records = []
    for raw in rows:
        row = {field: raw[key] for key, field in SELECTED_FIELDS.items()}
        row['field_states'] = {key: 'unknown' if value in (None, '', '-', '--', '—') else 'known'
                               for key, value in row.items()}
        row['page_status'] = {'1': '已选上', '0': '待筛选'}.get(row['selection_status_raw'], '状态待核对')
        records.append(row)
    return {'status': 'available', 'operation': 'current_enrollment',
            'target_term': year + ('-AW' if season == '1' else '-SS'),
            'data_kind': 'personal_source', 'data_source': 'live',
            'freshness': 'fetched_in_current_turn', 'records': records,
            'coverage': {'captured_rows': len(records), 'reported_total': None,
                         'has_more': None, 'complete_within_scope': False},
            'formal_enrollment_verified': False}


def query_selected(snapshot, args):
    from copy import deepcopy
    from unicodedata import normalize
    query = validate_selection_query(args)
    normalized = lambda value: re.sub(r'\s+', '', normalize('NFKC', value or '')).casefold()
    needle = normalized(query['query'])
    matched = [(i, row) for i, row in enumerate(snapshot['records']) if not needle
               or needle == normalized(row['course_code_raw'])
               or needle in normalized(row['course_name_raw'])]
    offset, limit = query['offset'], query['limit']
    selected = matched[offset:offset + limit]
    next_offset = offset + len(selected) if offset + len(selected) < len(matched) else None
    result = {**deepcopy(snapshot), 'records': [deepcopy(row) for _, row in selected],
              'source_record_indices': [i for i, _ in selected], 'offset': offset,
              'total_records': len(snapshot['records']), 'matched_records': len(matched),
              'returned_records': len(selected), 'next_offset': next_offset,
              'selection_summary': {'matched_records': len(matched),
                  'page_selected_count': sum(row['selection_status_raw'] == '1' for _, row in matched),
                  'page_pending_count': sum(row['selection_status_raw'] == '0' for _, row in matched),
                  'page_unknown_count': sum(row['selection_status_raw'] not in {'0', '1'} for _, row in matched)}}
    return result
