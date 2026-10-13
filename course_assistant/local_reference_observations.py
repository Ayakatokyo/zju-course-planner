"""Local reference observations, kept outside mock students and remote models.

Exact code links are observed reference mappings, not verified school identity
or student applicability. Every result remains a conditional, unknown draft.
"""
from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from itertools import combinations
import json
import re

from .planner import SearchBudget, _add, _number

SHARED_COMPONENTS = frozenset({'courses', 'course_observations', 'sections', 'section_observations',
                              'meeting_snapshots', 'meetings', 'exams', 'capacity_snapshots'})
SCOPE_POLICY = 'local-reference/1'
BASE_UNKNOWNS = ['real_academic_progress_not_connected', 'student_eligibility_unknown',
                 'normalized_week_sets_not_verified', 'reliable_calendar_not_available',
                 'official_section_identity_not_verified', 'student_applicability_unknown',
                 'capacity_is_historical_observation', 'requirement_relationship_pending_human_review']


class ReferenceObservationError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _text(value):
    if not isinstance(value, str):
        return None
    if re.search(r'https?://|file://|/(?:Users|home|tmp|private|Volumes|var|etc|root|opt|mnt|srv|proc|run|workspaces)(?:/|\b)'
                 r'|[A-Za-z]:[\\/]|\.\.[\\/]|(?<![A-Za-z0-9])/[A-Za-z0-9._~-]+/[A-Za-z0-9._~-]+'
                 r'|(?:password|token|cookie|authorization|session)\s*[:=]', value, re.I):
        return None
    return value[:2000]


def _identifier(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}', value))


def _decimal_text(value):
    number = _number(value)
    return format(number, 'f') if number is not None else None


def _unique(rows, key):
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not _identifier(row.get(key)) or row[key] in result:
            raise ReferenceObservationError('invalid_component_identity', '参考组件标识缺失或重复，不能确认完整观察束')
        result[row[key]] = deepcopy(row)
    return result


class ObservationCatalog:
    def __init__(self, components, dataset_version, authorize, allowed_course_codes, term='2026-2027-1'):
        if not callable(authorize):
            raise ReferenceObservationError('authorization_required', '本机参考场景必须提供当前用途授权检查')
        self._authorize_callback = authorize
        self._authorization_fingerprint = None
        self._authorize()
        if (not isinstance(components, dict) or set(components) != SHARED_COMPONENTS
                or any(not isinstance(rows, list) for rows in components.values())):
            raise ReferenceObservationError('shared_components_only', '仅接受八个共享组件；个人页面状态和私有来源登记不得加载')
        if not _identifier(dataset_version) or not _identifier(term):
            raise ReferenceObservationError('invalid_version', '数据版本与目标学期必须明确')
        if (not isinstance(allowed_course_codes, list) or not allowed_course_codes
                or any(not _identifier(code) for code in allowed_course_codes)):
            raise ReferenceObservationError('invalid_reference_scope', '参考方案课程池必须使用明确的精确代码')
        self.dataset_version, self.term = dataset_version, term
        self.allowed_course_codes = tuple(sorted(set(allowed_course_codes)))
        self.scope_fingerprint = sha256(json.dumps([self.allowed_course_codes, term], ensure_ascii=False).encode()).hexdigest()
        scoped_courses = [row for row in components['courses'] if isinstance(row, dict)
                          and row.get('code') in self.allowed_course_codes]
        self._courses = _unique(scoped_courses, 'code')
        if any(not _identifier(row.get('course_id')) for row in self._courses.values()):
            raise ReferenceObservationError('ambiguous_course_identity', '授权代码缺少合法课程父身份；不能据此关联班次')
        course_ids = {row['course_id'] for row in self._courses.values()}
        if len(course_ids) != len(self._courses):
            raise ReferenceObservationError('ambiguous_course_identity', '授权代码之间存在缺失或歧义课程父身份；不能据此关联班次')
        scoped_sections = []
        for row in components['sections']:
            if not isinstance(row, dict) or row.get('term') != term:
                continue
            if row.get('course_code') in self._courses:
                if row.get('course_id') != self._courses[row['course_code']]['course_id']:
                    raise ReferenceObservationError('invalid_course_mapping', '课程代码与候选班父身份不一致；禁止按名称修正')
                scoped_sections.append(row)
            elif row.get('course_id') in course_ids:
                raise ReferenceObservationError('invalid_course_mapping', '候选班代码与课程父身份不一致；禁止跨代码关联')
        self._sections = _unique(scoped_sections, 'section_id')
        self._observations = _unique([row for row in components['section_observations']
                                      if isinstance(row, dict) and row.get('section_id') in self._sections], 'id')
        observation_ids = set(self._observations)
        self._sets = _unique([row for row in components['meeting_snapshots']
                             if isinstance(row, dict) and row.get('observation_id') in observation_ids], 'id')
        self._meetings = _unique([row for row in components['meetings']
                                 if isinstance(row, dict) and row.get('observation_id') in observation_ids], 'id')
        self._exams = _unique([row for row in components['exams']
                              if isinstance(row, dict) and row.get('observation_id') in observation_ids], 'id')
        self._capacities = _unique([row for row in components['capacity_snapshots']
                                   if isinstance(row, dict) and row.get('observation_id') in observation_ids], 'id')
        self._by_code = {code: [] for code in self.allowed_course_codes}
        for observation in self._observations.values():
            section = self._sections[observation['section_id']]
            if observation['id'] not in section.get('observation_ids', []):
                raise ReferenceObservationError('unbound_observation', '观察不在该候选班的完整来源清单中')
            self._by_code[section['course_code']].append(observation['id'])
        for section in self._sections.values():
            if set(section.get('observation_ids', [])) != {
                    obs['id'] for obs in self._observations.values() if obs['section_id'] == section['section_id']}:
                raise ReferenceObservationError('incomplete_observation_scope', '候选班来源清单与观察组件不一致')
        for code in self._by_code:
            self._by_code[code].sort(key=lambda oid: (self._observations[oid]['section_id'], oid))
        self._authorize()

    def _authorize(self):
        try:
            approved = self._authorize_callback()
        except Exception:
            raise ReferenceObservationError('reference_authorization_unavailable', '本机参考用途或来源授权已失效') from None
        if approved is False:
            raise ReferenceObservationError('reference_authorization_denied', '本机参考用途或来源授权不允许当前操作')
        if isinstance(approved, dict):
            try:
                snapshot = approved
                if 'grant_version' in approved:
                    if not isinstance(approved['grant_version'], str) or not approved['grant_version']:
                        raise ValueError()
                    # LocalReferenceIndex computes this digest from the live
                    # grant; controls contain private Path objects, not scope.
                    snapshot = {key: approved[key] for key in ('grant_version', 'scope_version', 'versions', 'scope') if key in approved}
                fingerprint = sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True,
                                                 separators=(',', ':'), allow_nan=False).encode()).hexdigest()
            except (TypeError, ValueError):
                raise ReferenceObservationError('invalid_authorization_snapshot', '本机参考授权版本无法固定') from None
            if self._authorization_fingerprint is None:
                self._authorization_fingerprint = fingerprint
            elif self._authorization_fingerprint != fingerprint:
                raise ReferenceObservationError('reference_scope_changed', '本机参考授权范围或版本已变化，请重新建立课程查询范围')
        elif self._authorization_fingerprint is not None:
            raise ReferenceObservationError('reference_scope_changed', '本机参考授权版本契约已变化，请重新建立课程查询范围')

    def _course(self, code):
        course = self._courses.get(code)
        if course is None:
            return {'course_code': code, 'course_name': None, 'credits': None, 'credits_state': 'unknown',
                    'mapping_status': 'not_observed_in_authorized_pool'}
        definitions = [row for row in course.get('definitions', []) if row.get('term') == self.term]
        credit_values, names, unknown = set(), set(), not definitions
        for definition in definitions:
            fields = definition.get('fields', {})
            package = fields.get('credits', {})
            credit = _decimal_text(package.get('value')) if isinstance(package, dict) and package.get('state') == 'known' else None
            unknown |= credit is None
            if credit is not None:
                credit_values.add(Decimal(credit))
            name = fields.get('name', {})
            if isinstance(name, dict) and name.get('state') == 'known' and _text(name.get('value')):
                names.add(_text(name['value']))
        credit = format(next(iter(credit_values)), 'f') if len(credit_values) == 1 and not unknown else None
        return {'course_code': code, 'course_name': next(iter(names)) if len(names) == 1 else None,
                'credits': credit, 'credits_state': 'known' if credit is not None else 'unknown',
                'mapping_status': 'exact_code_observed_reference', 'official_course_identity': 'unknown',
                'student_applicability': 'unknown'}

    @staticmethod
    def _bound(record, observation, section_id):
        if (not isinstance(record, dict) or record.get('observation_id') != observation['id']
                or record.get('section_id') != section_id):
            raise ReferenceObservationError('observation_bundle_mismatch', '时段、考试或容量没有绑定到同一个完整观察；禁止拼接')
        return record

    def _bundle(self, observation_id):
        observation = self._observations[observation_id]
        section_id = observation['section_id']
        section = self._sections[section_id]
        packet = self._bound(self._sets.get(observation.get('meeting_set_id')), observation, section_id)
        exam = self._bound(self._exams.get(observation.get('exam_id')), observation, section_id)
        capacity = self._bound(self._capacities.get(observation.get('capacity_id')), observation, section_id)
        if any(record.get('observed_at') != observation.get('observed_at') for record in (exam, capacity)):
            raise ReferenceObservationError('observation_time_mismatch', '动态字段时间与所属观察不一致；禁止拼接')
        meetings = []
        mids = packet.get('meeting_ids')
        if not isinstance(mids, list) or len(mids) != len(set(mids)):
            raise ReferenceObservationError('invalid_meeting_set', '完整时段束缺少有效片段清单')
        own_meetings = {mid for mid, row in self._meetings.items() if row['observation_id'] == observation_id}
        if set(mids) != own_meetings:
            raise ReferenceObservationError('incomplete_meeting_set', '时段片段与完整时段束清单不一致')
        for mid in mids:
            record = self._bound(self._meetings.get(mid), observation, section_id)
            fields = record.get('fields') or {}
            day, periods = fields.get('weekday'), fields.get('periods')
            day = day if type(day) is int and 1 <= day <= 7 else None
            periods = periods if isinstance(periods, list) and periods and all(type(n) is int and 1 <= n <= 60 for n in periods) else None
            weeks = fields.get('week_set')
            weeks = weeks if isinstance(weeks, list) and all(type(n) is int and 1 <= n <= 60 for n in weeks) else None
            meetings.append({'meeting_id': mid, 'weekday': day, 'periods': deepcopy(periods),
                             'week_set': deepcopy(weeks), 'calendar_version': _text(fields.get('calendar_version')),
                             'time_raw': _text(fields.get('time_raw')), 'place_raw': _text(fields.get('place_raw')),
                             'week_annotation_raw': _text(fields.get('week_annotation_raw')),
                             'schedule_state': 'partial_reference' if day is not None and periods else 'unknown'})
        efields, cfields = exam.get('fields') or {}, capacity.get('fields') or {}
        known_empty = packet.get('status') == 'known_empty' and packet.get('declared_state') == 'known_empty'
        unknowns = list(BASE_UNKNOWNS)
        if self._observed_time({'observed_at': observation.get('observed_at'), 'observation_id': observation_id})[0] == float('-inf'):
            unknowns.append('observation_time_unknown')
        if not meetings and not known_empty:
            unknowns.append('meeting_set_missing_not_confirmed_empty')
        if any(m['weekday'] is None or m['periods'] is None for m in meetings):
            unknowns.append('weekday_or_period_unknown')
        if any(m['week_set'] is None for m in meetings):
            unknowns.append('week_set_unknown')
        if any(m['calendar_version'] is None for m in meetings):
            unknowns.append('calendar_version_unknown')
        if not efields.get('date') or not efields.get('start') or not efields.get('end') or not efields.get('timezone'):
            unknowns.append('exam_time_or_timezone_unknown')
        course = self._course(section['course_code'])
        if course['credits_state'] != 'known':
            unknowns.append('course_credits_unknown_or_conflicting')
        remaining = cfields.get('remaining')
        remaining = remaining if type(remaining) is int else None
        declared_capacity = cfields.get('capacity')
        declared_capacity = declared_capacity if type(declared_capacity) is int and declared_capacity >= 0 else None
        if remaining is None:
            unknowns.append('observed_remaining_unknown')
        return {**course, 'section_id': section_id, 'observation_id': observation_id,
                'dataset_version': self.dataset_version, 'term': self.term,
                'observed_at': _text(observation.get('observed_at')),
                'teacher_names': _text((observation.get('fields') or {}).get('teacher_names')),
                'term_part': _text((observation.get('fields') or {}).get('term_part')),
                'meeting_set_id': packet['id'], 'meetings': meetings, 'meeting_set_status': packet.get('status'),
                'exam': {'exam_id': exam['id'], **{key: _text(efields.get(key)) for key in ('date', 'start', 'end', 'timezone', 'status')}},
                'capacity': {'capacity_id': capacity['id'], 'capacity': declared_capacity, 'remaining': remaining,
                             'observed_at': _text(capacity.get('observed_at'))},
                'unknowns': list(dict.fromkeys(unknowns)), 'result': 'unknown', 'status': 'conditional_reference'}

    def lookup(self, code, limit=50, cursor=None):
        self._authorize()
        if code not in self.allowed_course_codes:
            raise ReferenceObservationError('course_outside_reference_scope', '课程不属于已授权参考方案的精确代码池')
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ReferenceObservationError('invalid_page_limit', '每页观察数量须为1至100')
        offset = 0
        if cursor is not None:
            try:
                decoded = json.loads(urlsafe_b64decode(cursor.encode()).decode())
                expected = {'version', 'term', 'course_code', 'scope', 'offset'}
                if (set(decoded) != expected or decoded['version'] != self.dataset_version
                        or decoded['term'] != self.term or decoded['course_code'] != code
                        or decoded['scope'] != self.scope_fingerprint
                        or type(decoded['offset']) is not int or decoded['offset'] < 0):
                    raise ValueError()
                offset = decoded['offset']
            except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
                raise ReferenceObservationError('stale_or_invalid_cursor', '分页引用与课程、学期、范围或数据版本不一致') from None
        ids = self._by_code[code]
        if offset > len(ids):
            raise ReferenceObservationError('stale_or_invalid_cursor', '分页偏移超出当前完整观察范围')
        rows = [self._bundle(oid) for oid in ids[offset:offset + limit]]
        following = offset + len(rows)
        has_more = following < len(ids)
        next_cursor = urlsafe_b64encode(json.dumps({'version': self.dataset_version, 'term': self.term,
            'course_code': code, 'scope': self.scope_fingerprint, 'offset': following}, sort_keys=True).encode()).decode() if has_more else None
        self._authorize()
        return {'course_code': code, 'mapping_status': self._course(code)['mapping_status'], 'course': self._course(code),
                'observations': rows, 'pagination': {'offset': offset, 'total': len(ids), 'limit': limit,
                    'has_more': has_more, 'next_cursor': next_cursor}, 'dataset_version': self.dataset_version,
                'versions': {'dataset_version': self.dataset_version, 'scope_policy': SCOPE_POLICY},
                'unknowns': list(BASE_UNKNOWNS), 'result': 'unknown', 'status': 'conditional_reference',
                'personal_data_used': False, 'remote_model_used': False}

    @staticmethod
    def _observed_time(bundle):
        try:
            observed = datetime.fromisoformat(bundle['observed_at'])
            if observed.tzinfo is None or observed.utcoffset() is None:
                raise ValueError()
            timestamp = observed.timestamp()
        except (ValueError, TypeError, OverflowError):
            timestamp = float('-inf')
        return timestamp, bundle['observation_id']

    @staticmethod
    def _conflicts(sections):
        conflicts = []
        for left, right in combinations(sections, 2):
            for lm in left['meetings']:
                for rm in right['meetings']:
                    if (lm['weekday'] is not None and lm['weekday'] == rm['weekday']
                            and lm['periods'] and rm['periods'] and set(lm['periods']) & set(rm['periods'])):
                        conflicts.append({'type': 'possible_conflict', 'result': 'unknown',
                            'section_ids': [left['section_id'], right['section_id']],
                            'meeting_ids': [lm['meeting_id'], rm['meeting_id']],
                            'weekday': lm['weekday'], 'periods': sorted(set(lm['periods']) & set(rm['periods'])),
                            'reason': '已知星期/节次重叠，实际周集合与校历未核验，不能确认实际冲突或无冲突'})
        return conflicts

    def conditional_recommend(self, target_credits=8, friday_free='soft', node_limit=2000, limit=3):
        self._authorize()
        target = _number(target_credits)
        if target is None or target <= 0 or friday_free not in {'off', 'soft', 'hard'}:
            raise ReferenceObservationError('invalid_scenario', '须明确正数场景目标及周五条件强度')
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ReferenceObservationError('invalid_result_limit', '参考组合数量须为1至10')
        budget = SearchBudget(node_limit, max_candidates=64, max_diagnostics=100, max_seconds=10)
        diagnostics, choices = [], {}
        observed_codes = {code for code in self.allowed_course_codes if self._by_code[code]}
        unobserved = [code for code in self.allowed_course_codes if code not in observed_codes]
        fully_checked_codes = set()
        observation_count = 0
        def diagnose(code, **fields):
            if len(diagnostics) < budget.max_diagnostics:
                diagnostics.append({'code': code, **fields})
            else:
                budget.diagnostics_clipped = True
        for code in self.allowed_course_codes:
            self._authorize()
            observations = self._by_code[code]
            if not observations:
                continue
            latest, times, counts = {}, {}, {}
            for observation_id in observations:
                if not budget.spend('observation_precheck'):
                    break
                observation_count += 1
                try:
                    bundle = self._bundle(observation_id)
                except ReferenceObservationError:
                    diagnose('observation_bundle_unusable', course_code=code, result='unknown')
                    continue
                section = bundle['section_id']
                times.setdefault(section, []).append(self._observed_time(bundle)[0])
                counts[section] = counts.get(section, 0) + 1
                if section not in latest or self._observed_time(bundle) > self._observed_time(latest[section]):
                    latest[section] = bundle
            allowed = []
            for bundle in latest.values():
                unordered = counts[bundle['section_id']] > 1 and float('-inf') in times[bundle['section_id']]
                bundle['observation_selection'] = ('deterministic_bundle_with_unordered_observations' if unordered
                                                    else 'latest_timestamped_observation' if counts[bundle['section_id']] > 1
                                                    else 'sole_observation')
                if unordered:
                    bundle['unknowns'].append('observation_time_order_unknown')
                seats = bundle['capacity']['remaining']
                if seats is not None and seats <= 0:
                    diagnose('observed_capacity_unavailable', course_code=code, section_id=bundle['section_id'], result='fail')
                    continue
                if friday_free == 'hard' and any(m['weekday'] == 5 for m in bundle['meetings']):
                    diagnose('hard_friday', course_code=code, section_id=bundle['section_id'], result='fail')
                    continue
                allowed.append(bundle)
            if allowed:
                choices[code] = sorted(allowed, key=lambda b: (b['section_id'], b['observation_id']))
            if budget.limit_reached:
                break
            fully_checked_codes.add(code)
        self._authorize()
        codes = sorted(choices)
        candidates, leaf_count = [], 0
        def rank(option):
            return (option['credits_state'] != 'known', len(option['possible_conflicts']), option['metrics']['friday_penalty'],
                    Decimal(option['metrics']['surplus_credits']), len(option['course_codes']),
                    tuple(option['course_codes']), tuple(s['section_id'] for s in option['sections']))
        def visit(position, selected, known_credits, credits_known):
            nonlocal leaf_count
            if not budget.spend('course_and_section_search'):
                return
            if budget.nodes_visited % 128 == 0:
                self._authorize()
            if position == len(codes):
                if not selected or credits_known and known_credits < target:
                    return
                leaf_count += 1
                conflicts = self._conflicts(selected)
                friday_count = sum(m['weekday'] == 5 for section in selected for m in section['meetings'])
                unknowns = list(dict.fromkeys(BASE_UNKNOWNS + [item for s in selected for item in s['unknowns']]))
                if conflicts: unknowns.append('possible_time_conflicts_require_week_and_calendar_review')
                if not credits_known: unknowns.append('scenario_credit_target_not_confirmed')
                option = {'option_id': sha256(json.dumps([self.dataset_version, [s['observation_id'] for s in selected],
                    format(target, 'f'), friday_free], sort_keys=True).encode()).hexdigest(),
                    'course_codes': [s['course_code'] for s in selected], 'sections': deepcopy(selected),
                    'total_credits': format(known_credits, 'f') if credits_known else None,
                    'known_credit_lower_bound': format(known_credits, 'f'), 'credits_state': 'known' if credits_known else 'unknown',
                    'metrics': {'friday_meeting_count': friday_count, 'friday_penalty': friday_count if friday_free == 'soft' else 0,
                        'surplus_credits': format(max(Decimal(0), _add(known_credits, target.copy_negate())), 'f'),
                        'possible_conflict_count': len(conflicts)},
                    'possible_conflicts': conflicts, 'unknowns': unknowns, 'result': 'unknown', 'status': 'conditional_reference'}
                candidates.append(option)
                if len(candidates) > budget.max_candidates:
                    candidates.sort(key=rank)
                    del candidates[budget.max_candidates:]
                    budget.candidate_retention_clipped = True
                return
            visit(position + 1, selected, known_credits, credits_known)
            if budget.limit_reached: return
            for section in choices[codes[position]]:
                credits = _number(section['credits'])
                visit(position + 1, selected + [section], _add(known_credits, credits) if credits is not None else known_credits,
                      credits_known and credits is not None)
                if budget.limit_reached: return
        if not budget.limit_reached:
            visit(0, [], Decimal(0), True)
        candidates.sort(key=rank)
        chosen, seen_course_sets = [], set()
        for option in candidates:
            course_set = tuple(option['course_codes'])
            if course_set not in seen_course_sets:
                seen_course_sets.add(course_set)
                chosen.append(option)
                if len(chosen) >= limit: break
        for option in candidates:
            if len(chosen) >= limit: break
            if option not in chosen: chosen.append(option)
        assumptions = {'mode': 'local_reference', 'use': 'explicit_hypothetical_new_course_combination',
                       'target_new_credits': format(target, 'f'), 'friday_free': friday_free,
                       'academic_progress': 'not_provided', 'eligibility': 'unknown', 'official_selection': 'not_performed',
                       'requirement_completion': 'not_evaluated', 'interest_content_basis': 'not_provided'}
        self._authorize()
        return {'status': 'conditional_reference', 'result': 'unknown', 'options': chosen,
                'assumptions': assumptions, 'unknowns': list(BASE_UNKNOWNS), 'diagnostics': diagnostics,
                'versions': {'dataset_version': self.dataset_version, 'scope_policy': SCOPE_POLICY},
                'dataset_version': self.dataset_version, 'term': self.term, 'personal_data_used': False,
                'remote_model_used': False, 'school_eligibility_verified': False,
                'search': {**budget.report(), 'observation_count_considered': observation_count,
                    'observed_course_count': len(observed_codes), 'candidate_course_count': len(choices),
                    'authorized_course_count': len(self.allowed_course_codes),
                    'filtered_observed_course_codes': sorted(fully_checked_codes - set(choices)),
                    'incompletely_checked_observed_course_codes': sorted(observed_codes - fully_checked_codes),
                    'observation_precheck_complete': fully_checked_codes == observed_codes,
                    'unobserved_course_codes': unobserved, 'conditional_leaf_count': leaf_count,
                    'retained_candidate_count': len(candidates), 'complete_within_observed_scope': not budget.limit_reached,
                    'source_course_coverage_complete': not unobserved and not budget.limit_reached,
                    'observation_selection': 'latest_when_times_verified_else_explicit_unordered_bundle', 'globally_optimal': False},
                'note': '真实观察与场景假设分开；未接本人学业/资格、可靠周次/校历，结果仅为本机条件性参考。'}
