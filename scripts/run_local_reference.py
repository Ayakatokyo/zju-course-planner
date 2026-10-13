#!/usr/bin/env python3
"""Explicit local-reference CLI. No model, network, mock identity or personal workspace."""
from __future__ import annotations
import argparse
import csv
import html
import io
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from course_assistant.local_reference import LocalReferenceIndex
from course_assistant.local_reference_observations import ObservationCatalog


def output_private(path, text):
    path = Path(path).resolve()
    root = Path(__file__).resolve().parents[1] / 'local-only'
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Outputs require the local-only directory')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(text)


def catalogue(index):
    versions = index.describe()['versions']
    codes = index.course_codes
    def authorize_snapshot():
        index.authorize()
        if index.course_codes != codes or index.describe()['versions'] != versions:
            raise ValueError('Reference scope changed; build a new catalogue')
        return {'versions': versions, 'course_codes': codes}
    return ObservationCatalog(index.shared_components(), versions['dataset_version'],
                              authorize_snapshot, codes, term='2026-2027-1')


def csv_reference(result):
    buf = io.StringIO(newline='')
    writer = csv.writer(buf)
    writer.writerow(['data_kind', 'result', 'option', 'course_code', 'course_name', 'credits',
                     'section', 'meetings', 'unknown', 'assumptions', 'versions'])
    for i, plan in enumerate(result.get('options', result.get('plans', [])), 1):
        for section in plan.get('sections', []):
            writer.writerow(['conditional_reference', plan.get('result', 'unknown'), i,
                             section.get('course_code'), section.get('course_name'), section.get('credits'),
                             section.get('section_id'), json.dumps(section.get('meetings', []), ensure_ascii=False),
                             json.dumps(plan.get('unknowns', result.get('unknowns', [])), ensure_ascii=False),
                             json.dumps(result.get('assumptions', {}), ensure_ascii=False),
                             json.dumps(result.get('versions', {}), ensure_ascii=False)])
    return buf.getvalue()


def reference_html(description, searches, result):
    esc = lambda value: html.escape(str(value if value is not None else '待确认'))
    blocks = []
    for search in searches:
        matches = search.get('matches', [])
        blocks.append(f'<section><h2>{esc(search.get("query", "培养依据"))}</h2>')
        if not matches:
            blocks.append('<p>当前首片没有可支持的依据。</p>')
        for match in matches:
            pages = match.get('physical_pages', [])
            blocks.append(f'<article><h3>{esc(match.get("title", "参考候选"))}</h3>'
                          f'<p class="meta">{esc(description.get("source_alias", "EVAL-A"))} · 第 {esc("、".join(map(str,pages)))} 页 · 待人工审核</p>'
                          f'<p class="quote">{esc(match.get("text", ""))}</p></article>')
            for context in match.get('context', []):
                blocks.append(f'<p class="meta">{esc(context.get("title", "必要上下文"))}</p>'
                              f'<p class="quote">{esc(context.get("text", ""))}</p>')
        blocks.append('</section>')
    if result.get('search', {}).get('limit_reached'):
        blocks.append('<p class="status">搜索达到本轮预算，仅展示已计算组合。</p>')
    for i, plan in enumerate(result.get('options', result.get('plans', [])), 1):
        blocks.append(f'<section><h2>条件性课表 {i}</h2><p>{esc(plan.get("total_credits", plan.get("planned_credits")))} 计划学分 · 待确认</p>'
                      '<div class="table-wrap"><table><thead><tr><th>课程</th><th>已观察时段</th><th>考试</th></tr></thead><tbody>')
        for section in plan.get('sections', []):
            meetings = section.get('meetings', [])
            times = '；'.join(str(m.get('time_raw') or m.get('fields', {}).get('time_raw') or '待确认') for m in meetings)
            blocks.append(f'<tr><td>{esc(section.get("course_name",section.get("course_code")))}<br>{esc(section.get("course_code"))}</td>'
                          f'<td>{esc(times)}</td><td>{esc(section.get("exam_state","待确认"))}</td></tr>')
        unknown_labels = {'real_academic_progress_not_connected':'本人学业未接入',
                          'student_eligibility_unknown':'资格待确认',
                          'normalized_week_sets_not_verified':'周次未核验',
                          'reliable_calendar_not_available':'缺可靠校历',
                          'official_section_identity_not_verified':'班次身份待核验',
                          'student_applicability_unknown':'本人适用待确认',
                          'capacity_is_historical_observation':'余量为历史观察',
                          'requirement_relationship_pending_human_review':'要求关系待人工审核',
                          'exam_time_or_timezone_unknown':'考试日期或时区待确认',
                          'possible_time_conflicts_require_week_and_calendar_review':'可能时段冲突，需周次与校历核对'}
        unknowns = plan.get('unknowns', [])
        labels = [unknown_labels.get(value, value) for value in unknowns]
        blocks.append('</tbody></table></div><p class="meta">'+esc('；'.join(labels))+'</p>')
        for conflict in plan.get('possible_conflicts', []):
            blocks.append('<p class="status">可能冲突：'+esc(json.dumps(conflict,ensure_ascii=False))+'</p>')
        blocks.append('</section>')
    if not result.get('options', result.get('plans', [])):
        blocks.append('<section><h2>课表组合</h2><p>当前范围未形成可展示组合，请查看本机结果中的缺项与搜索状态。</p></section>')
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>R3 本机参考</title><style>
body{margin:0;background:#fff;color:#252638;font:16px/1.7 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}main{max-width:960px;margin:auto;padding:32px 20px}h1{font-size:26px}h2{font-size:21px}h3{font-size:17px}section{margin:32px 0;border-top:1px solid #ddd;padding-top:24px}article{margin:20px 0}p{max-width:80ch}.meta{color:#72552c}.quote{white-space:pre-wrap}.status{padding:14px;background:#fff4dd;border-radius:6px}.table-wrap{overflow:auto}table{border-collapse:collapse;width:100%}th,td{text-align:left;border-bottom:1px solid #ddd;padding:12px;vertical-align:top}
</style><main><h1>R3 本机参考</h1><p class="status">独立本机参考场景 · 人工审核未完成。周次、校历、资格和本人学业仍待确认，组合仅作条件性参考。</p>''' + ''.join(blocks) + '</main></html>\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--delivery-index', type=Path, required=True)
    parser.add_argument('--grant', type=Path, required=True)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('build')
    commands.add_parser('describe')
    search = commands.add_parser('search');search.add_argument('query');search.add_argument('--mode',choices=['L0','L1'],default='L1')
    resolve = commands.add_parser('resolve');resolve.add_argument('reference')
    lookup = commands.add_parser('lookup');lookup.add_argument('course_code');lookup.add_argument('--cursor')
    recommend = commands.add_parser('recommend');recommend.add_argument('--credits',default='8');recommend.add_argument('--friday',choices=['off','soft','hard'],default='soft');recommend.add_argument('--format',choices=['json','csv','ics'],default='json')
    commands.add_parser('report')
    args = parser.parse_args()
    if args.command == 'build':
        LocalReferenceIndex.build(args.delivery_index,args.grant,args.bundle)
        index = LocalReferenceIndex(args.delivery_index,args.grant,args.bundle)
        operation_versions = index.describe()['versions']
        result = index.describe()
    else:
        index = LocalReferenceIndex(args.delivery_index,args.grant,args.bundle)
        index.authorize()
        operation_versions = index.describe()['versions']
        if args.command == 'describe':result=index.describe()
        elif args.command == 'search':result=index.search(args.query,mode=args.mode)
        elif args.command == 'resolve':result=index.resolve(args.reference)
        elif args.command == 'lookup':
            result=catalogue(index).lookup(args.course_code,cursor=args.cursor)
            mapping=index.search(args.course_code,mode='L0',limit=1)
            result['mapping_evidence']=mapping['matches']
            result['versions']={**operation_versions, **result.get('versions', {})}
            result['scope']=index.describe()['scope']
        else:
            if args.command == 'recommend' and args.format == 'ics':
                print(json.dumps({'status':'incomplete_data','error':'reliable_calendar_required','result':'unknown'},ensure_ascii=False))
                return 2
            result=catalogue(index).conditional_recommend(target_credits=args.credits if args.command=='recommend' else '8',friday_free=args.friday if args.command=='recommend' else 'soft')
            result['versions']={**operation_versions, **result.get('versions', {})}
            result['scope']=index.describe()['scope']
            if args.command == 'report':
                if not args.output:raise ValueError('HTML report requires a private output file')
                searches=[{**index.search(query), 'query':query} for query in ['专业模块课程 修满学分','模块课程表 跨页','辅修 星号']]
                rendered=reference_html(index.describe(),searches,result)
                if index.describe()['versions'] != operation_versions:
                    raise ValueError('Reference authorization or source version changed during the report')
                output_private(args.output,rendered)
                print(json.dumps({'status':'conditional_reference','result':'unknown','report_created':True,'human_gold_count':0},ensure_ascii=False));return 0
    text = csv_reference(result) if args.command=='recommend' and args.format=='csv' else json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if index.describe()['versions'] != operation_versions:
        raise ValueError('Reference authorization or source version changed before output')
    if args.output:
        output_private(args.output,text)
        print(json.dumps({'status':result.get('status','local_reference'),'private_output_created':True,'human_gold_count':0},ensure_ascii=False))
    else:print(text)
    return 0


if __name__ == '__main__':
    try:raise SystemExit(main())
    except (OSError,ValueError,KeyError,TypeError,PermissionError) as exc:
        print(json.dumps({'status':'local_reference_failed','error_type':type(exc).__name__,
                          'message':'本机资料、授权或输入无法核验；请核对本机配置。'},ensure_ascii=False),file=sys.stderr)
        raise SystemExit(2)
