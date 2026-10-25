"""Repeatable local, source-bound RAG evaluation; never contacts the school/model.

Input JSON: documents [{id, pdf, sha256, cases: [{id, query, mode?,
required: [{page, text}], forbidden?: [text], module_path?: [label],
expect_empty?: bool}]}]. Paths are relative to the input.
Gold must be checked against the original pages; this tool does not create gold.
Detailed evidence stays in the explicit private output directory. A passing
retrieval/projection check is not an evaluation of generated answers or formal eligibility.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
import unicodedata
from time import monotonic

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from course_assistant.personal_evidence import PersonalEvidenceService
from course_assistant.semantic_retrieval import LocalSemanticRetriever
from course_assistant.personal_dialogue import PersonalDialogueService


def compact(text):
    return re.sub(r"\s+", "", unicodedata.normalize('NFKC', text))


def evaluate(manifest, output, *, dense=False, service_class=PersonalEvidenceService):
    data = json.loads(manifest.read_text())
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    report = {'scope': 'fixed_local_pdf_retrieval_and_model_projection', 'dense_requested': dense,
              'gold_status': data.get('gold_status', 'unspecified'), 'cases': []}
    for document in data['documents']:
        body = (manifest.parent / document['pdf']).resolve().read_bytes()
        digest = sha256(body).hexdigest()
        if digest != document['sha256']:
            raise ValueError('PDF version differs from reviewed gold')
        alias = 'PLAN-' + digest[:24]
        service = service_class((output / document['id']).resolve(),
            lambda: {'scope_key': 'offline-rag-evaluation-' + digest}, lambda key: body,
            lambda: {'source_alias': alias, 'sha256': digest})
        if not dense:
            service._semantic_retriever = LocalSemanticRetriever()
        service.prepare(alias)
        for case in document['cases']:
            kwargs = {'mode': case['mode']} if case.get('mode') else {}
            start = monotonic()
            result = service.search(case['query'], limit=5, **kwargs)
            elapsed = monotonic() - start
            _, wire = PersonalDialogueService._project('search_my_curriculum', result,
                {'query': case['query'], 'limit': 5, **kwargs}, 'eval', {}, 0, [0, 0, {}], set())
            fragments = []
            for match in result['matches']:
                fragments.append({'page': match['page'], 'text': match.get('model_text', match['text'])})
                fragments.extend({'page': c['page'], 'text': c.get('model_text', c['text'])} for c in match.get('model_context', []))
            hits = [any(f['page'] == item['page'] and compact(item['text']) in compact(f['text']) for f in fragments)
                    for item in case['required']]
            forbidden = any(compact(t) in compact(f['text']) for t in case.get('forbidden', []) for f in fragments)
            projected = []
            by_id = {m['evidence_id']: m for m in wire['matches']}
            for match in wire['matches']:
                projected.append({'page': match['page'], 'text': match['text']})
                for context in match.get('model_context', []):
                    if 'context_source_evidence_id' in context:
                        context = by_id[context['context_source_evidence_id']]['model_context'][context['context_index']]
                    projected.append(context)
            wire_hits = [any(f['page'] == item['page'] and compact(item['text']) in compact(f['text']) for f in projected)
                         for item in case['required']]
            module_ok = not case.get('module_path') or all(m.get('module_path') == case['module_path'] for m in result['matches'])
            empty_ok = not case.get('expect_empty') or not result['matches']
            row = {'document': document['id'], 'case': case['id'], 'required': len(hits), 'found': sum(hits),
                   'projected_found': sum(wire_hits), 'passed': all(hits) and all(wire_hits) and not forbidden and module_ok and empty_ok,
                   'module_path_correct': module_ok, 'empty_result_correct': empty_ok, 'forbidden_found': forbidden,
                   'search_seconds': round(elapsed, 4), 'wire_chars': len(json.dumps(wire, ensure_ascii=False)),
                   'document_encoded_count': result.get('semantic_retrieval', {}).get('encoded_count', 0),
                   'document_cache_hit': result.get('semantic_retrieval', {}).get('document_cache_hit', False),
                   'retrieval_mode': result.get('retrieval_mode'), 'dense_used': result.get('semantic_retrieval', {}).get('dense_used', False)}
            report['cases'].append(row)
            (output / (document['id'] + '-' + case['id'] + '.json')).write_text(
                json.dumps({'case': case, 'result': result, 'model_projection': wire, 'evaluation': row}, ensure_ascii=False, indent=2))
    report.update(total=len(report['cases']), passed=sum(r['passed'] for r in report['cases']),
                  required=sum(r['required'] for r in report['cases']), found=sum(r['found'] for r in report['cases']),
                  projected_found=sum(r['projected_found'] for r in report['cases']))
    (output / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--dense', action='store_true')
    args = parser.parse_args()
    report = evaluate(args.manifest.resolve(), args.output.resolve(), dense=args.dense)
    print(json.dumps({k: v for k, v in report.items() if k != 'cases'}, ensure_ascii=False))
    sys.exit(0 if report['passed'] == report['total'] else 1)
