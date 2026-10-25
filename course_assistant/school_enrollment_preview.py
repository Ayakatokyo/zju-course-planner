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
