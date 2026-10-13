"""Five fixed synthetic, real-DeepSeek conversations. Raw receipts stay local."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from course_assistant.model_client import ModelClient, read_config
from course_assistant.server import Application

LOADED_SOURCE_SHA256 = {str(file.relative_to(ROOT)): hashlib.sha256(file.read_bytes()).hexdigest()
                        for file in sorted((ROOT / 'course_assistant').glob('*.py'))}

GOAL = '这是纯合成测试：优先补专业选修6学分，偏向AI，尽量周五空闲，保留体育。请用推荐工具选择课程和具体班次，给出不同课程组合。'
SCENARIOS = [
    ('multi_compare_save', 'default', [GOAL, '请调用比较工具比较当前A和B的课程、班次、目标贡献和周五取舍。', '保存B', '导出B为JSON']),
    ('soft_to_hard', 'default', [GOAL, '周五必须空闲，其他锁定、预置和固定项保持。请用明确操作重新推荐。']),
    ('replace_and_relax', 'default', [GOAL, '把B的模型方法换成AI实践，其他条件保持。请按本轮明确换课操作重新推荐。',
                                   '周五必须空闲，其他条件保持。请重新计算。', '周五可以有课，其他条件保持。请按本轮明确放宽重新推荐。']),
    ('lock_conflict', 'locked_friday', [GOAL, '周五必须空闲，体育锁定和预置仍保留，不要自动放宽。请重新推荐并给出实际冲突。']),
    ('unknown', 'unknown', [GOAL, '请再次调用推荐工具按相同合成目标推荐。周次和校历未知仍须显示待确认，不能声称完整通过。']),
]


def run():
    config = read_config(ROOT / '.env')
    config['COURSE_ALLOW_REFERENCE_TEXT'] = 'false'
    model = ModelClient(config)
    status = model.status()
    if not status['configured']:
        raise SystemExit('model_not_configured：保留工程验证，不伪造真实模型证据')
    destination = ROOT / 'local-only' / 'recommendation_acceptance' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    destination.mkdir(parents=True, exist_ok=False)
    records = []
    for name, fixture_case, messages in SCENARIOS:
        print(f'开始 {name}（固定synthetic，{len(messages)}轮）', flush=True)
        with tempfile.TemporaryDirectory() as directory:
            app = Application(Path(directory) / 'live.sqlite3', mock_enabled=True, model=model,
                              recommendation_demo=True, recommendation_case=fixture_case)
            token, _ = app.auth.login_mock({'profile_id': 'demo-a'})
            context = app.auth.authenticate(token)
            initial = deepcopy(app.workspace(context))
            previous, turns = None, []
            for message in messages:
                request = {'message': message, 'request_id': str(uuid.uuid4()), 'consent_remote': True,
                           'conversation_id': previous['conversation_id'] if previous else None,
                           'expected_turn': previous['turn_id'] if previous else 0}
                response = app.chat.turn(request, context)
                turns.append({'request': request, 'response': response, 'workspace_unchanged': app.workspace(context) == initial})
                previous = response
                print(f"  第{response['turn_id']}轮 {response['status']} / {[tool['name'] for tool in response['tool_results']]}", flush=True)
            checks = {'two_or_more_real_turns': len(turns) >= 2,
                      'all_completed': all(row['response']['status'] == 'completed' for row in turns),
                      'real_tool_each_turn': all(bool(row['response']['tool_results']) for row in turns),
                      'preview_did_not_change_workspace': all(row['workspace_unchanged'] for row in turns[:2] if name == 'multi_compare_save') if name == 'multi_compare_save' else app.workspace(context) == initial}
            recommendations = [tool['result'] for row in turns for tool in row['response']['tool_results']
                               if tool['name'] in {'recommend_my_courses', 'revise_my_recommendation'}]
            if name == 'multi_compare_save':
                checks['multiple_course_sets'] = bool(recommendations) and len({tuple(plan['course_ids']) for plan in recommendations[0]['plans']}) >= 2
                checks['actual_comparison'] = any(tool['name'] == 'compare_my_options' for tool in turns[1]['response']['tool_results'])
                checks['language_save'] = bool(turns[2]['response'].get('saved_action'))
                checks['language_export'] = bool(turns[3]['response'].get('download'))
                if recommendations and recommendations[0]['plans']:
                    chosen = recommendations[0]['plans'][1]
                    action = {'conversation_id': previous['conversation_id'], 'expected_turn': previous['turn_id'],
                              'run_id': chosen['run_id'], 'option_id': chosen['option_id'], 'action_id': str(uuid.uuid4())}
                    saved = turns[2]['response'].get('saved_action')
                    replayed = app.chat.turn(turns[2]['request'], context)
                    checks['real_saved_and_idempotent'] = bool(saved) and saved['saved_id'] == replayed.get('saved_action', {}).get('saved_id') and len(app.store.saved(context)) == 1
                    downloads = []
                    for format in ('json', 'csv', 'ics'):
                        data, mime, suffix = app.post('/api/chat/export-option', {**action, 'format': format}, context)
                        downloads.append({'format': format, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
                        (destination / f'{name}.{suffix}').write_bytes(data)
                    checks['three_actual_exports'] = all(item['bytes'] > 0 for item in downloads)
                    turns.append({'explicit_action': action, 'saved': saved, 'downloads': downloads})
                    reopened_request = {'message': GOAL, 'request_id': str(uuid.uuid4()), 'consent_remote': True,
                                        'conversation_id': None, 'expected_turn': 0}
                    reopened = app.chat.turn(reopened_request, context)
                    reopen_results = [tool['result'] for tool in reopened['tool_results'] if tool['name'] == 'recommend_my_courses']
                    checks['new_conversation_preserves_global_intent'] = reopened['status'] == 'completed' and bool(reopen_results) and len({tuple(plan['course_ids']) for plan in reopen_results[-1]['plans']}) >= 2
                    turns.append({'reopened_request': reopened_request, 'response': reopened})
            elif name == 'soft_to_hard':
                checks['one_passed_option'] = bool(recommendations) and len(recommendations[-1]['plans']) == 1 and recommendations[-1]['constraints']['friday_free'] == 'hard'
            elif name == 'replace_and_relax':
                checks['explicit_replace'] = len(recommendations) >= 4 and 'DEMO-AI' in recommendations[1]['intent']['required_course_ids']
                checks['hard_conflict_then_explicit_relax'] = len(recommendations) >= 4 and recommendations[-2]['status'] == 'scoped_infeasible' and recommendations[-1]['status'] == 'feasible' and recommendations[-1]['constraints']['friday_free'] == 'off'
            elif name == 'lock_conflict':
                checks['no_silent_relaxation'] = bool(recommendations) and recommendations[-1]['status'] == 'scoped_infeasible' and recommendations[-1]['plans'] == [] and 'DEMO-PE-01' in recommendations[-1]['constraints']['preloaded_section_ids']
            elif name == 'unknown':
                checks['unknown_not_passed'] = bool(recommendations) and all(result['status'] == 'incomplete_data' and not result['plans'] and result['pending_plans'] for result in recommendations)
            passed = all(checks.values())
            records.append({'scenario': name, 'fixture_case': fixture_case, 'checks': checks, 'passed': passed, 'turns': turns})
            (destination / f'{name}.json').write_text(json.dumps(records[-1], ensure_ascii=False, indent=2), encoding='utf-8')
            print(f'{name}: {"PASS" if passed else "FAIL"} {checks}', flush=True)
    report = {'recorded_at_utc': datetime.now(timezone.utc).isoformat(), 'provider': status['provider'], 'model': status['model'],
              'scope': 'fixed synthetic real model conversations; no real school facts/text, human gold or school acceptance',
              'reference_text_transmitted': False, 'passed': sum(row['passed'] for row in records), 'total': len(records),
              'scenarios': [{key: row[key] for key in ('scenario', 'fixture_case', 'checks', 'passed')} for row in records],
              'source_sha256': LOADED_SOURCE_SHA256,
              'sources_changed_during_run': [name for name, sha in LOADED_SOURCE_SHA256.items()
                                            if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != sha]}
    (destination / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"实际模型通过 {report['passed']}/{report['total']}；本机回执 {destination / 'summary.json'}", flush=True)
    return 0 if report['passed'] == report['total'] else 1


if __name__ == '__main__':
    sys.exit(run())
