"""20 local integration checks. Engineering evidence; never human retrieval gold."""
from __future__ import annotations
import argparse
from copy import deepcopy
from datetime import datetime,timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from course_assistant.local_reference import LocalReferenceIndex,LocalReferenceError
from course_assistant.local_reference_observations import ReferenceObservationError
from scripts.run_local_reference import catalogue,csv_reference,output_private


def recommendation_binding(result, description, rows):
    """Bind supplied output to this live, authorized reference snapshot."""
    expected_versions = dict(description['versions'])
    for row in rows.values():
        expected_versions.update(row.get('versions', {}))
    actual_versions = result.get('versions') if isinstance(result, dict) else None
    version_errors = [key for key, value in expected_versions.items()
                      if not isinstance(actual_versions, dict) or actual_versions.get(key) != value]
    authorized = {observation['observation_id']: observation
                  for row in rows.values() for observation in row['observations']}
    option_errors = []
    options = result.get('options') if isinstance(result, dict) else None
    if not isinstance(options, list):
        return version_errors, ['options_not_a_list']
    required = ('observation_id', 'course_code', 'section_id', 'meeting_set_id', 'meetings', 'exam', 'capacity')
    for index, option in enumerate(options, 1):
        prefix = f'option_{index}'
        if not isinstance(option, dict) or not isinstance(option.get('sections'), list) or not option['sections']:
            option_errors.append(prefix + ':sections_missing_or_invalid')
            continue
        seen, courses, credits, credits_known = set(), [], Decimal(0), True
        for ordinal, section in enumerate(option['sections'], 1):
            label = f'{prefix}:section_{ordinal}'
            identifier = section.get('observation_id') if isinstance(section, dict) else None
            observation = authorized.get(identifier) if isinstance(identifier, str) else None
            if observation is None:
                option_errors.append(label + ':observation_outside_live_scope')
                credits_known = False
                continue
            if observation['observation_id'] in seen:
                option_errors.append(label + ':duplicate_observation')
            seen.add(observation['observation_id'])
            fields = required + tuple(key for key in ('course_name', 'credits', 'credits_state') if key in observation)
            changed = [key for key in fields if key not in section or section[key] != observation[key]]
            if changed:
                option_errors.append(label + ':fields_mismatch:' + ','.join(changed))
            courses.append(observation['course_code'])
            if observation.get('credits_state') != 'known' or observation.get('credits') is None:
                credits_known = False
            else:
                try:
                    amount = Decimal(str(observation['credits']))
                    if not amount.is_finite():
                        raise InvalidOperation
                    credits += amount
                except (InvalidOperation, ValueError):
                    option_errors.append(label + ':authorized_credits_invalid')
                    credits_known = False
        declared = option.get('course_codes')
        if (not isinstance(declared, list) or any(not isinstance(code, str) for code in declared)
                or sorted(declared) != sorted(courses) or len(courses) != len(set(courses))):
            option_errors.append(prefix + ':course_set_mismatch')
        try:
            total = Decimal(str(option.get('total_credits'))) if credits_known else None
            if credits_known and (not total.is_finite() or total != credits):
                option_errors.append(prefix + ':total_credits_mismatch')
            elif not credits_known and option.get('total_credits') is not None:
                option_errors.append(prefix + ':unknown_credits_reported_as_total')
        except (InvalidOperation, ValueError):
            option_errors.append(prefix + ':total_credits_missing_or_invalid')
    return version_errors, option_errors


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--delivery-index',type=Path,required=True);p.add_argument('--grant',type=Path,required=True)
    p.add_argument('--bundle',type=Path,required=True);p.add_argument('--result',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();index=LocalReferenceIndex(a.delivery_index,a.grant,a.bundle);cat=catalogue(index)
    description=index.describe();result=json.loads(a.result.read_text());codes=index.course_codes
    rows={code:cat.lookup(code,limit=100) for code in codes}
    observed=[code for code in codes if rows[code]['observations']];missing=[code for code in codes if not rows[code]['observations']]
    actual=[row for value in rows.values() for row in value['observations']]
    sample=observed[0];query=index.search(sample,mode='L0',limit=1);hit=query['matches'][0]
    components=index.shared_components();source_obs={row['id']:row for row in components['section_observations']}
    source_meetings={row['id']:row for row in components['meeting_snapshots']}
    cases=[]
    def check(name,passed,details=None):
        case={'case_id':f'R3-LIVE-{len(cases)+1:02d}','name':name,'passed':bool(passed)}
        if not passed and details:case['failure_details']=details
        cases.append(case)
    def denied(fn):
        try:fn()
        except (LocalReferenceError,ReferenceObservationError) as exc:return exc.code in {'inaccessible_resource','stale_input','course_outside_reference_scope','stale_or_invalid_cursor','reference_scope_changed'}
        return False
    check('目录登记与获准正文分别计数',description['source_count']==162 and description['admitted_source_count']==1)
    check('首片块与视觉课程池对账',description['chunk_count']==56 and len(codes)==47)
    check('模块学分与目录数量分开',description['module']['requirement_credits']=='8' and sum(description['module']['catalog_subgroup_counts'])==47 and not description['module']['official_rule_enabled'])
    check('精确代码先过滤再词法匹配',len(query['matches'])==1 and sample in hit['text'])
    check('近码不误连',not index.search(sample+'X',mode='L0')['matches'])
    check('课程行带必要条款表头附注',len(hit['context'])>=2 and all(c['review_status']=='assistant_reviewed_pending_human' for c in hit['context']))
    check('全池观察连接与未观察分别计数',len(observed)==21 and len(missing)==26 and len({r['section_id'] for r in actual})==21)
    paged_code=next(c for c in observed if rows[c]['pagination']['total']>1)
    first=cat.lookup(paged_code,limit=1);collected=list(first['observations']);cursor=first['pagination']['next_cursor']
    while cursor:
        page=cat.lookup(paged_code,limit=1,cursor=cursor);collected.extend(page['observations']);cursor=page['pagination']['next_cursor']
    check('版本分页枚举完整观察',len(collected)==rows[paged_code]['pagination']['total'] and len({r['observation_id'] for r in collected})==len(collected))
    another=next(c for c in observed if c!=paged_code)
    check('分页不可改绑课程',denied(lambda:cat.lookup(another,cursor=first['pagination']['next_cursor'])))
    projected=json.dumps(actual,ensure_ascii=False)
    check('共享投影不返回个人字段或定位',not any(term in projected for term in ['/Users/','local-only/','http://','https://','volunteer_status','restricted_personal_page','owner_student_id']))
    version_errors,option_errors=recommendation_binding(result,description,rows)
    options=result.get('options',[]) if isinstance(result,dict) else []
    valid_options=isinstance(options,list) and all(isinstance(option,dict) for option in options)
    check('推荐输入版本绑定且三套课程组合有实际差异',not version_errors and not option_errors and valid_options and len(options)==3 and len({tuple(sorted(o['course_codes'])) for o in options})==3,
          {'version_fields_mismatched':version_errors,'option_binding_errors':option_errors})
    check('计划贡献不变本人通过',valid_options and bool(options) and all(o.get('total_credits')=='8' and o.get('result')=='unknown' for o in options) and result.get('result')=='unknown' and 'real_academic_progress_not_connected' in result.get('unknowns',[]))
    check('共享全局预算与截断真实呈现',result['search']['nodes_visited']<=result['search']['max_nodes'] and result['search']['limit_reached'] and not result['search']['globally_optimal'])
    check('推荐班次时段考试容量和学分回查同一授权观察束',not option_errors and all(source_obs[r['observation_id']]['meeting_set_id']==r['meeting_set_id'] and source_obs[r['observation_id']]['exam_id']==r['exam']['exam_id'] and source_obs[r['observation_id']]['capacity_id']==r['capacity']['capacity_id'] and len(source_meetings[r['meeting_set_id']]['meeting_ids'])==len(r['meetings']) for r in actual),
          {'option_binding_errors':option_errors})
    check('真实周次校历考试未补演示值',all(r['result']=='unknown' and 'normalized_week_sets_not_verified' in r['unknowns'] and 'reliable_calendar_not_available' in r['unknowns'] for r in actual))
    check('二三四课堂普通查询暂缓',all(index.search(q)['status']=='deferred_by_business_scope' for q in ['第二课堂','第四课堂']))
    check('普通劳动不按活动词排除',index.search('普通劳动课程')['status']!='deferred_by_business_scope' and index.search('课程难度')['answer_support_verified'] is False)
    reloaded=LocalReferenceIndex(a.delivery_index,a.grant,a.bundle);resolved=reloaded.resolve(hit['reference'])
    check('签名跨实例回查与伪引用拒绝',resolved['text_sha256']==hit['text_sha256'] and denied(lambda:reloaded.resolve('LOCAL-REF-fabricated')))
    base=json.loads(a.grant.read_text());folder=a.output.parent/'grant_checks';folder.mkdir(parents=True,exist_ok=True)
    def changed(name,mutate):
        g=deepcopy(base);mutate(g);path=folder/(name+'.json');path.write_text(json.dumps(g,ensure_ascii=False));return LocalReferenceIndex(a.delivery_index,path,a.bundle)
    def rejected_grant(name,mutate):return denied(lambda:changed(name,mutate).search(sample))
    check('撤销过期与mock域拒绝',all([rejected_grant('revoked',lambda g:g.update(revoked=True)),rejected_grant('expired',lambda g:g.update(expires_at='2000-01-01T00:00:00+00:00')),rejected_grant('wrong_realm',lambda g:g.update(security_realm='mock_development'))]))
    narrowed=changed('narrowed',lambda g:g['allowed_pages']['EVAL-A'].remove(g['curriculum_selector']['path_note_span']['page']))
    def change_selector(g):g['curriculum_selector']['requirement_span']['line_end']+=1
    check('缩减范围与selector变更使旧引用失效',not narrowed.course_codes and denied(lambda:narrowed.resolve(hit['reference'])) and rejected_grant('selector',change_selector))
    index.authorize();assert len(cases)==20
    report={'schema':'r3-local-integration/1','source_alias':'EVAL-A','kind':'engineering_integration_not_human_gold','human_gold_count':0,'cases':cases,'passed':sum(c['passed'] for c in cases),'total':len(cases),'versions':description['versions'],'counts':{'registered_documents':162,'rechecked_admitted_documents':1,'chunks':56,'curriculum_codes':47,'observed_courses':len(observed),'unobserved_courses':len(missing),'candidate_sections':len({r['section_id'] for r in actual}),'observation_bundles':len(actual),'conditional_options':len(options) if isinstance(options,list) else 0},'model_called':False,'real_data_sent':False}
    output_private(a.output,json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ['kind','human_gold_count','passed','total','counts']},ensure_ascii=False))
    return 0 if report['passed']==report['total'] else 1


if __name__=='__main__':raise SystemExit(main())
