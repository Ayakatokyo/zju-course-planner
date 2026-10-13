"""Bounded, deterministic course selection for fixed synthetic scenarios.

Authorization and stable run references belong to the server. This pure layer
never writes academic facts and never treats reference material as a rule.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from itertools import combinations

from .audit import audit_progress
from .planner import (SearchBudget, _number, _json_number, _add, _overall, _protected_sections,
                      generate_plans, validate_plan, fingerprint)

ENGINE_VERSION = 'DEMO-RECOMMENDATION-v1'


def normalize_intent(demo, intent=None):
    defaults = demo.get('recommendation', {})
    value = {**deepcopy(defaults), **deepcopy(intent or {})}
    known = {course['course_id'] for course in demo['courses']}
    for key in ('optional_course_ids', 'required_course_ids', 'excluded_course_ids'):
        ids = value.get(key, [])
        if not isinstance(ids, list) or any(not isinstance(x, str) or x not in known for x in ids):
            raise ValueError(f'{key} 必须引用当前合成课程池')
        value[key] = list(dict.fromkeys(ids))
    target = _number(value.get('target_credits'))
    if target is None:
        raise ValueError('target_credits 必须是有限非负十进制数')
    value['target_credits'] = _json_number(target)
    if not isinstance(value.get('target_module'), str):
        raise ValueError('必须指定已登记目标模块')
    if value.get('interest') is not None and not isinstance(value['interest'], str):
        raise ValueError('interest 必须为文本或 null')
    priority = value.get('priority', ['goal', 'interest', 'time', 'change'])
    if (not isinstance(priority, list) or set(priority) != {'goal', 'interest', 'time', 'change'}
            or len(priority) != 4):
        raise ValueError('priority 必须包含 goal、interest、time、change 各一次')
    value['priority'] = list(priority)
    return value


def _audit(demo, records):
    return audit_progress(demo['student'], records, demo['rules'], demo['courses'],
                          record_completeness=demo['academic_snapshot'].get('completeness', 'unknown'))


def forecast_contribution(demo, sections, target_module):
    """Predict on a copy, preserving the actual ledger and repeat policies."""
    before = _audit(demo, demo['academic_snapshot']['records'])
    records = deepcopy(demo['academic_snapshot']['records'])
    courses = {course['course_id']: course for course in demo['courses']}
    for section in sections:
        course = courses.get(section['course_id'], {})
        records.append({'record_id': 'DEMO-PREDICT-' + section['section_id'],
                        'attempt_id': 'DEMO-PREDICT-' + section['section_id'],
                        'student_key': demo['student']['student_key'],
                        'plan_id': demo['student'].get('plan_id'), 'record_kind': 'course',
                        'course_id': section['course_id'], 'completion_status': 'passed',
                        'recognition_status': 'not_applicable', 'earned_credits': section.get('credits'),
                        'ledger': 'base', 'tags': deepcopy(course.get('tags')),
                        'data_kind': 'synthetic', 'snapshot_id': 'DEMO-PREDICT-v1',
                        'source_refs': deepcopy(section.get('source_refs', []))})
    after = _audit(demo, records)
    prior = next((rule for rule in before['rules'] if rule['rule_id'] == target_module), None)
    projected = next((rule for rule in after['rules'] if rule['rule_id'] == target_module), None)
    known = (prior is not None and projected is not None and prior['metric'] == 'credits'
             and prior['result'] != 'unknown' and projected['result'] != 'unknown')
    delta = (max(Decimal(0), _add(Decimal(str(projected['actual'])),
                                       Decimal(str(prior['actual'])).copy_negate())) if known else None)
    return {'state': 'known' if known else 'unknown',
            'module_actual_before': prior.get('actual') if prior else None,
            'module_expected_after': projected.get('actual') if projected else None,
            'module_new_contribution': _json_number(delta) if delta is not None else None,
            'expected_rule_contributions': after['rules'],
            'earned_ledger_before': before['ledger'],
            'source_refs': deepcopy(projected.get('source_refs', [])) if projected else [],
            'reason': projected.get('reason') if projected else '目标模块规则未配置',
            'note': '假设所选课程未来通过的预计新增贡献；不修改已获账本。'}


def _interest(course, interest):
    evidence = course.get('description_evidence')
    known = (isinstance(evidence, dict) and evidence.get('data_kind') == 'synthetic'
             and evidence.get('verified') is True and bool(evidence.get('source_refs'))
             and isinstance(evidence.get('topics'), list))
    score = int(bool(interest) and interest in evidence['topics']) if known else 0
    refs = [{**deepcopy(ref), 'course_id': course.get('course_id'), 'matched': bool(score),
             'text': f"{course.get('course_name', course.get('course_id'))}：{evidence.get('text', '合成说明')}；{'匹配' if score else '未匹配'}本轮兴趣"}
            for ref in evidence.get('source_refs', [])] if known else []
    return score, refs


def _rank(plan, intent):
    metrics = plan['metrics']
    scores = {'goal': Decimal(str(metrics['goal_contribution'])).copy_negate(), 'interest': -metrics['interest_match_count'],
              'time': metrics['friday_penalty'], 'change': metrics['change_cost']}
    return tuple(scores[key] for key in intent['priority']) + (
        Decimal(str(metrics['surplus_module_credits'])), len(plan['course_ids']), tuple(sorted(plan['section_ids'])))


def _slots(plan, index):
    return {(meeting.get('day_of_week'), tuple(meeting.get('period_indices') or []),
             tuple(meeting.get('season_codes') or []), tuple(meeting.get('week_numbers') or []))
            for sid in plan['section_ids'] if sid in index
            for meeting in index[sid].get('meetings') or []}


def _select(pool, intent, protected_course_ids, index, limit):
    remaining = sorted(pool, key=lambda p: _rank(p, intent))
    chosen = []
    while remaining and len(chosen) < limit:
        if not chosen:
            pick = remaining[0]
        else:
            def distance(plan):
                courses = set(plan['course_ids']) - protected_course_ids
                values = []
                for previous in chosen:
                    other = set(previous['course_ids']) - protected_course_ids
                    union = courses | other
                    ratio = len(courses ^ other) / len(union) if union else 0
                    slots, other_slots = _slots(plan, index), _slots(previous, index)
                    time_ratio = len(slots ^ other_slots) / len(slots | other_slots) if slots | other_slots else 0
                    preference_difference = sum(plan['metrics'][key] != previous['metrics'][key]
                                                for key in ('interest_match_count', 'friday_meeting_count'))
                    values.append((ratio, preference_difference, time_ratio))
                return min(values)
            pick = min(remaining, key=lambda p: tuple(-x for x in distance(p)) + _rank(p, intent))
        chosen.append(pick)
        remaining.remove(pick)
    base = chosen[0] if chosen else None
    for number, plan in enumerate(chosen):
        plan['label'] = chr(65 + number)
        plan['differences'] = {'added_course_ids': sorted(set(plan['course_ids']) - set(base['course_ids'])),
                               'removed_course_ids': sorted(set(base['course_ids']) - set(plan['course_ids'])),
                               'added_section_ids': sorted(set(plan['section_ids']) - set(base['section_ids'])),
                               'removed_section_ids': sorted(set(base['section_ids']) - set(plan['section_ids']))}
    return chosen


def _evaluate_option(demo, option_constraints, intent, validation):
    validation = deepcopy(validation)
    index = {section['section_id']: section for section in demo['offering_snapshot']['records']}
    courses = {course['course_id']: course for course in demo['courses']}
    ids = tuple(sorted(validation['section_ids']))
    sections = [index[sid] for sid in ids if sid in index]
    forecast = forecast_contribution(demo, sections, intent['target_module'])
    if any(sid not in index for sid in ids):
        forecast.update(state='unknown', module_new_contribution=None, module_expected_after=None,
                        reason='所选班次已不在当前快照，不能把缺失课程当作零贡献')
    target = Decimal(str(intent['target_credits']))
    delta = forecast['module_new_contribution']
    target_state = ('unknown' if forecast['state'] != 'known' else
                    'pass' if Decimal(str(delta)) >= target else 'fail')
    validation['checks'].append({'code': 'module_target', 'result': target_state,
                                 'section_ids': list(ids), 'source_refs': forecast['source_refs'],
                                 'reason': (forecast['reason'] if target_state == 'unknown' else
                                            f'预计新增模块贡献 {delta}；本轮合成目标 {target}')})
    course_ids = sorted({s['course_id'] for s in sections})
    excluded = sorted(set(course_ids) & set(intent['excluded_course_ids']))
    if excluded:
        validation['checks'].append({'code': 'excluded_course', 'result': 'fail',
                                     'section_ids': list(ids), 'course_ids': excluded,
                                     'reason': '方案包含本轮明确排除的课程', 'source_refs': []})
    validation['result'] = _overall(validation['checks'])
    validation['status'] = {'pass': 'validated_draft', 'unknown': 'needs_information', 'fail': 'invalid_draft'}[validation['result']]
    interest_count, evidence = 0, []
    for cid in course_ids:
        score, refs = _interest(courses.get(cid, {}), intent.get('interest'))
        interest_count += score
        evidence.extend(refs)
    metrics = {'goal_contribution': _json_number(min(target, Decimal(str(delta)))) if delta is not None else 0,
               'module_new_contribution': delta, 'module_actual_before': forecast['module_actual_before'],
               'module_expected_after': forecast['module_expected_after'], 'contribution_state': forecast['state'],
               'interest_match_count': interest_count,
               'friday_meeting_count': validation['preference_scores']['friday_meeting_count'],
               'friday_penalty': validation['preference_scores']['friday_penalty'],
               'change_cost': len(set(ids) ^ set(intent.get('baseline_section_ids', []))) if intent.get('baseline_section_ids') else 0,
               'surplus_module_credits': _json_number(max(Decimal(0), _add(Decimal(str(delta)), target.copy_negate()))) if delta is not None else 0}
    return {**validation, 'constraints': deepcopy(option_constraints), 'course_ids': course_ids,
            'metrics': metrics, 'evidence': {'module': forecast['source_refs'], 'interest': evidence},
            'expected_rule_contributions': forecast['expected_rule_contributions'],
            'ranking_components': list(intent['priority']), 'recommendation_engine': ENGINE_VERSION}


def recommend(demo, constraints, intent=None, *, max_nodes=10000, limit=3,
              max_candidates=256, max_diagnostics=200, max_pool_courses=12, max_seconds=5):
    """Choose courses and classes; each option carries its own low-level request.

    target_credits is this scenario's desired *new planned module contribution*.
    It is neither an official threshold nor permission to assume missing study.
    """
    if demo.get('metadata', {}).get('data_kind') != 'synthetic':
        raise ValueError('推荐首片仅接受已登记 synthetic 数据')
    if type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError('limit 必须为 1—10')
    if type(max_pool_courses) is not int or not 1 <= max_pool_courses <= 24:
        raise ValueError('max_pool_courses 必须为 1—24')
    intent = normalize_intent(demo, intent)
    budget = SearchBudget(max_nodes, max_candidates=max_candidates,
                          max_diagnostics=max_diagnostics, max_seconds=max_seconds)
    snapshot, student = demo['offering_snapshot'], demo['student']
    index = {section['section_id']: section for section in snapshot['records']}
    courses = {course['course_id']: course for course in demo['courses']}
    protected = _protected_sections(snapshot, student, constraints)
    protected_courses = {index[sid]['course_id'] for sid in protected if sid in index}
    required = list(dict.fromkeys(intent['required_course_ids'] + constraints.get('requested_course_ids', [])
                                  + sorted(protected_courses)))
    exclusions = set(intent['excluded_course_ids'])
    rule = next((r for r in demo['rules'] if r.get('rule_id') == intent['target_module']), None)
    module_pool = set(rule.get('course_pool') or []) if rule else set()
    optional_all = [cid for cid in intent['optional_course_ids'] if cid not in required and cid not in exclusions]
    optional = optional_all[:max_pool_courses]
    pool_clipped = len(optional) != len(optional_all)
    target = Decimal(str(intent['target_credits']))
    candidates, diagnostics, passed, pending = [], [], [], []
    zero_contribution = set()
    leaf_counts = {'pass': 0, 'unknown': 0}
    unknown_diagnostics = False
    baseline = _audit(demo, demo['academic_snapshot']['records'])
    baseline_rule = next((r for r in baseline['rules'] if r['rule_id'] == intent['target_module']), {})
    def diagnose(items):
        nonlocal unknown_diagnostics
        unknown_diagnostics |= any(item.get('result') == 'unknown' for item in items)
        room = budget.max_diagnostics - len(diagnostics)
        diagnostics.extend(deepcopy(items[:max(0, room)]))
        budget.diagnostics_clipped |= len(items) > room
    for cid in list(dict.fromkeys(required + intent['optional_course_ids'])):
        if not budget.spend('course_precheck'):
            break
        interest_score, evidence = _interest(courses.get(cid, {}), intent.get('interest'))
        excluded = cid in exclusions
        status, reason = ('excluded', '用户排除课程') if excluded else ('candidate', '当前合成模块/必选候选')
        available = [section for section in index.values() if section['course_id'] == cid]
        if cid in module_pool and available and cid in baseline_rule.get('matched_course_ids', []):
            forecast = forecast_contribution(demo, [available[0]], intent['target_module'])
            if forecast['state'] == 'known' and forecast['module_new_contribution'] == 0 and cid not in required:
                zero_contribution.add(cid)
                status, reason = 'excluded', '已有可靠获分，适用重复规则下无新增目标贡献'
        eligible, uncertain, rejected, section_reasons = [], [], [], []
        if status != 'excluded':
            single_constraints = {**deepcopy(constraints), 'requested_course_ids': [cid],
                                  'locked_section_ids': [], 'preloaded_section_ids': []}
            single_constraints.pop('min_credits', None)
            for section in available:
                if not budget.spend('candidate_section_precheck'):
                    break
                validation = validate_plan([section['section_id']], snapshot, student, single_constraints,
                                           demo['academic_snapshot']['records'], require_protected=False)
                bucket = {'pass': eligible, 'unknown': uncertain, 'fail': rejected}[validation['result']]
                if len(bucket) < budget.max_candidates:
                    bucket.append(section['section_id'])
                for check in validation['checks']:
                    if check['result'] != 'pass':
                        if len(section_reasons) < budget.max_diagnostics:
                            section_reasons.append(deepcopy(check))
                        diagnose([check])
            if not available:
                status, reason = 'unknown', '当前范围未提供该课程教学班'
            elif not eligible and uncertain:
                status, reason = 'unknown', '教学班证据不足，保留待确认候选'
            elif not eligible and not uncertain and rejected and not budget.limit_reached:
                status, reason = 'excluded', '所有已核对班次均被当前硬条件或资格明确排除'
        candidates.append({'course_id': cid, 'status': status, 'reason': reason,
                           'target_relation_known': cid in module_pool,
                           'interest_match': interest_score, 'interest_evidence': evidence,
                           'section_count': sum(s['course_id'] == cid for s in index.values()),
                           'already_earned': cid in baseline_rule.get('matched_course_ids', []),
                           'eligible_section_ids': eligible, 'pending_section_ids': uncertain,
                           'excluded_section_ids': rejected, 'section_reasons': section_reasons})
    optional = [cid for cid in optional if cid not in zero_contribution]
    conflicting = sorted(set(required) & exclusions)
    if conflicting:
        diagnose([{'code': 'excluded_required_course', 'result': 'fail', 'reason': '排除课程与必选/保护来源冲突',
                   'course_ids': conflicting, 'section_ids': protected}])
    def collect(validation, option_constraints):
        if validation['result'] == 'fail':
            return
        plan = _evaluate_option(demo, option_constraints, intent, validation)
        if plan['result'] == 'fail':
            diagnose([check for check in plan['checks'] if check['result'] == 'fail'])
            return
        collection = passed if plan['result'] == 'pass' else pending
        leaf_counts[plan['result']] += 1
        collection.append(plan)
        if len(collection) > budget.max_candidates:
            collection.sort(key=lambda p: _rank(p, intent))
            del collection[budget.max_candidates:]
            budget.candidate_retention_clipped = True
    subsets_visited = 0
    if not conflicting:
        for size in range(len(optional) + 1):
            if budget.limit_reached:
                break
            for subset in combinations(optional, size):
                if not budget.spend('course_subset'):
                    break
                subsets_visited += 1
                local = {**deepcopy(constraints), 'requested_course_ids': list(dict.fromkeys(required + list(subset)))}
                generated = generate_plans(snapshot, student, local, demo['academic_snapshot']['records'],
                                           budget=budget, limit=limit,
                                           leaf_sink=lambda leaf, local=local: collect(leaf, local))
                diagnose(generated['diagnostics'])
                if generated['status'] == 'incomplete_data':
                    unknown_diagnostics = True
                if budget.limit_reached:
                    break
    complete_scope = (snapshot.get('completeness') == 'complete_within_scope'
                      and set(required + optional_all) <= set(snapshot.get('filter_scope', {}).get('course_ids') or []))
    if budget.limit_reached or pool_clipped:
        status, reason = 'search_limit', '推荐搜索或候选范围达到预算；不保证完整搜索或全局最优'
    elif passed:
        status, reason = 'feasible', '已在当前合成范围找到完整复核且满足目标的课程/班次组合'
    elif (pending or not complete_scope or baseline_rule.get('result', 'unknown') == 'unknown'
          or unknown_diagnostics):
        status, reason = 'incomplete_data', '当前范围/目标贡献存在未知，未得到全部通过的推荐'
    else:
        status, reason = 'scoped_infeasible', '完整合成范围内未找到同时满足保护条件与目标的组合'
    selected = _select(passed, intent, protected_courses, index, limit)
    waiting = _select(pending, intent, protected_courses, index, limit)
    return {'status': status, 'reason': reason, 'plans': selected, 'pending_plans': waiting,
            'diagnostics': diagnostics, 'suggestions': [], 'constraints': deepcopy(constraints),
            'intent': intent, 'candidates': candidates,
            'search': {**budget.report(), 'complete_within_scope': complete_scope,
                       'candidate_scope_clipped': pool_clipped, 'course_pool_size': len(optional_all),
                       'course_subsets_visited': subsets_visited, 'validated_plan_count': leaf_counts['pass'],
                       'pending_plan_count': leaf_counts['unknown'], 'retained_validated_count': len(passed),
                       'retained_pending_count': len(pending),
                       'globally_optimal': False},
            'engine_version': ENGINE_VERSION, 'run_input_fingerprint': fingerprint([demo, constraints, intent])}


def revalidate_option(demo, constraints, intent, section_ids):
    """Return current scheduling *and* recommendation validation for one option.

    Server callers must resolve the option reference and its intent before this
    function. Module unknown/failure may never be replaced by a class-only pass.
    """
    if demo.get('metadata', {}).get('data_kind') != 'synthetic':
        raise ValueError('推荐首片仅接受已登记 synthetic 数据')
    if (not isinstance(section_ids, list) or len(section_ids) > 100
            or any(not isinstance(sid, str) or not sid for sid in section_ids)):
        raise ValueError('方案须提供至多100个有效班次引用')
    intent = normalize_intent(demo, intent)
    local = {**deepcopy(constraints), 'requested_course_ids': list(dict.fromkeys(
        constraints.get('requested_course_ids', []) + intent['required_course_ids']))}
    validation = validate_plan(section_ids, demo['offering_snapshot'], demo['student'], local,
                               demo['academic_snapshot']['records'])
    evaluated = _evaluate_option(demo, local, intent, validation)
    evaluated['validation'] = {key: deepcopy(evaluated[key]) for key in validation}
    return evaluated


def compare_recommendations(plans, demo, constraints, intent=None):
    """Revalidate differing course sets against one global intent and versions."""
    if not isinstance(plans, list) or not 2 <= len(plans) <= 10:
        raise ValueError('比较需要 2—10 套推荐')
    intent = normalize_intent(demo, intent)
    index = {section['section_id']: section for section in demo['offering_snapshot']['records']}
    required = set(intent['required_course_ids']) | set(constraints.get('requested_course_ids', []))
    result = []
    for plan in plans:
        ids = plan['section_ids']
        local = {**deepcopy(constraints), 'requested_course_ids': sorted(required | {
            index[sid]['course_id'] for sid in ids if sid in index})}
        evaluated = revalidate_option(demo, local, intent, ids)
        result.append({**deepcopy(plan), **evaluated, 'revalidated': True,
                       'previous_result': plan.get('result')})
    return {'plans': result, 'ranking': sorted(range(len(result)), key=lambda n: (
        {'pass': 0, 'unknown': 1, 'fail': 2}[result[n]['result']], _rank(result[n], intent))),
            'compared_using_current_inputs': True, 'intent': intent,
            'run_input_fingerprint': fingerprint([demo, constraints, intent])}
