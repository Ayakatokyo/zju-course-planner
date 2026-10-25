"""Offline curriculum corpus ingestion and scoped retrieval, never an authorization layer.

Optional dependencies live in an isolated environment. Raw PDFs stay in their
source batch; extraction, embedding cache and Qdrant are private derived data.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import importlib.metadata
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from .personal_evidence import _extract_document, _pages, _chunks, EXTRACTION, CHUNKING, CLEANING
from .curriculum_pdf import extraction_summary, _ocr_page
from .reference_retrieval import terms
from .semantic_retrieval import _manifest, DIMENSION, QUERY_PREFIX


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def prepare_pdf(metadata, source):
    body = Path(source).read_bytes()
    if sha256(body).hexdigest() != metadata['sha256']:
        raise ValueError('source_hash_mismatch')
    pages = _pages(_extract_document(body))
    return {'pages': pages, 'chunks': _chunks(pages), 'quality': extraction_summary(pages)}


class Encoder:
    """One verified local ONNX session per batch, no network or model downloads."""
    def __init__(self, manifest):
        os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')
        os.environ.setdefault('HF_HUB_OFFLINE', '1')
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer
        data, paths, identity = _manifest(manifest)
        for name, version in data['runtime'].items():
            if importlib.metadata.version(name) != version:
                raise ValueError('runtime_version_mismatch')
        self.identity = identity
        self.np = np
        bodies = {key: path.read_bytes() for key, path in paths.items()}
        for key, body in bodies.items():
            if sha256(body).hexdigest() != data['artifacts'][key]['sha256']:
                raise ValueError('artifact_hash_mismatch')
        self.tokenizer = Tokenizer.from_str(bodies['tokenizer'].decode())
        self.tokenizer.enable_padding(pad_id=0, pad_token='[PAD]')
        # No truncation: oversized source fragments are split before embedding.
        self.tokenizer.no_truncation()
        ort.disable_telemetry_events()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(bodies['model'], sess_options=options,
                                           providers=['CPUExecutionProvider'])
        self.encoded = 0

    def split(self, text):
        encoded = self.tokenizer.encode(text, add_special_tokens=False)
        if len(encoded.ids) <= 480:
            return [text]
        return [text[encoded.offsets[i][0]:encoded.offsets[min(i+480, len(encoded.ids))-1][1]]
                for i in range(0, len(encoded.ids), 440)]

    def encode(self, texts):
        np = self.np
        result = []
        for start in range(0, len(texts), 16):
            rows = self.tokenizer.encode_batch(texts[start:start+16])
            if any(len(r.ids) > 512 for r in rows):
                raise ValueError('embedding_token_limit')
            inputs = {key: np.asarray([getattr(r, attr) for r in rows], dtype=np.int64)
                      for key, attr in [('input_ids', 'ids'), ('attention_mask', 'attention_mask'),
                                        ('token_type_ids', 'type_ids')]}
            vectors = self.session.run(None, inputs)[0]
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            if vectors.shape != (len(rows), DIMENSION) or not np.isfinite(vectors).all() or (norms < 1e-8).any():
                raise ValueError('invalid_embedding')
            result.extend((vectors / norms).tolist())
        self.encoded += len(texts)
        return result


class CurriculumVectorStore:
    def __init__(self, root, encoder, *, url=None):
        from qdrant_client import QdrantClient, models
        self.root, self.encoder, self.models = Path(root), encoder, models
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.root/'catalogue.sqlite3')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, metadata TEXT NOT NULL,
            bundle TEXT NOT NULL, ready INTEGER NOT NULL DEFAULT 0, point_count INTEGER NOT NULL DEFAULT 0);
          CREATE TABLE IF NOT EXISTS vectors (id TEXT PRIMARY KEY, vector TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS active (scope TEXT PRIMARY KEY, document_id TEXT NOT NULL);
        ''')
        self.version = digest([EXTRACTION, CHUNKING, CLEANING, 'token-window-480-overlap40-v1', encoder.identity])
        self.collection = 'curriculum_' + self.version[:24]
        if url:
            from .persistent_semantics import QdrantCache
            QdrantCache(url)
        self.client = QdrantClient(url=url,timeout=30) if url else QdrantClient(path=str(self.root/'qdrant'))
        if not self.client.collection_exists(self.collection):
            self.client.create_collection(self.collection, vectors_config=models.VectorParams(size=DIMENSION, distance=models.Distance.COSINE))
            # A lost/replaced Qdrant collection is not a successful warm cache.
            for doc_id, raw in self.db.execute('SELECT id,metadata FROM documents').fetchall():
                if self.document_id(json.loads(raw)) == doc_id:
                    self.db.execute('UPDATE documents SET ready=0 WHERE id=?', (doc_id,))
            self.db.commit()

    def close(self):
        self.client.close()
        self.db.close()

    def document_id(self, metadata):
        return digest([self.version, metadata['sha256'], metadata['cohort'], metadata['plan_number']])

    def ingest(self, metadata, source, *, prepared=None):
        body = Path(source).read_bytes()
        if sha256(body).hexdigest() != metadata['sha256']:
            raise ValueError('source_hash_mismatch')
        doc_id = self.document_id(metadata)
        cached = self.db.execute('SELECT bundle, ready, point_count FROM documents WHERE id=?', (doc_id,)).fetchone()
        if cached and cached[1]:
            return {'status': 'reused', 'document_id': doc_id, 'points': cached[2], 'encoded': 0}
        if cached:
            bundle = json.loads(cached[0])
        else:
            bundle = prepared if prepared is not None else prepare_pdf(metadata, source)
            self.db.execute('INSERT INTO documents(id, metadata, bundle) VALUES(?,?,?)',
                            (doc_id, json.dumps(metadata, ensure_ascii=False), json.dumps(bundle, ensure_ascii=False)))
            self.db.commit()
        passages = []
        for chunk in bundle['chunks']:
            for part, text in enumerate(self.encoder.split(chunk['model_text'])):
                passages.append((chunk, part, text))
        if not passages:
            raise ValueError('no_indexable_passages')
        keys = [digest([self.encoder.identity, text]) for _, _, text in passages]
        vectors, missing = {}, {}
        for key, (_, _, text) in zip(keys, passages):
            if key in vectors or key in missing:
                continue
            row = self.db.execute('SELECT vector FROM vectors WHERE id=?', (key,)).fetchone()
            if row:
                vectors[key] = json.loads(row[0])
            else:
                missing[key] = text
        # Checkpoint each modest batch; interruption never marks a partial doc ready.
        pending = sorted(missing, key=lambda k: len(missing[k]))
        for start in range(0, len(pending), 64):
            batch = pending[start:start+64]
            encoded = self.encoder.encode([missing[k] for k in batch])
            for key, vector in zip(batch, encoded):
                vectors[key] = vector
                self.db.execute('INSERT OR IGNORE INTO vectors VALUES(?,?)', (key, json.dumps(vector)))
            self.db.commit()
        points = []
        for key, (chunk, part, text) in zip(keys, passages):
            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, doc_id+':'+chunk['chunk_id']+':'+str(part)))
            payload = {'document_id': doc_id, 'pdf_sha256': metadata['sha256'],
                       'cohort': int(metadata['cohort']), 'plan_number': str(metadata['plan_number']),
                       'major': metadata.get('major'), 'college': metadata.get('college'),
                       'source_observed_at': metadata.get('observed_at'), 'source_kind': metadata.get('source'),
                       'page': chunk['page'], 'chunk_id': chunk['chunk_id'], 'part': part,
                       'text': text, 'locator': chunk['locator'], 'module_path': chunk['module_path'],
                       'semantic_context': chunk.get('semantic_context', []),
                       'quality': bundle['pages'][chunk['page']-1].get('text_quality'),
                       'review_status': 'unreviewed', 'pipeline_version': self.version}
            points.append(self.models.PointStruct(id=point_id, vector=vectors[key], payload=payload))
        for start in range(0, len(points), 128):
            self.client.upsert(self.collection, points[start:start+128], wait=True)
        if cached and cached[2]:
            flt = self.models.Filter(must=[self.models.FieldCondition(key='document_id', match=self.models.MatchValue(value=doc_id))])
            keep, offset, obsolete = {p.id for p in points}, None, []
            while True:
                old, offset = self.client.scroll(self.collection, scroll_filter=flt, offset=offset, limit=256, with_payload=False)
                obsolete.extend(p.id for p in old if p.id not in keep)
                if offset is None:break
            if obsolete:
                self.client.delete(self.collection, points_selector=self.models.PointIdsList(points=obsolete), wait=True)
        self.db.execute('UPDATE documents SET ready=1,point_count=? WHERE id=?', (len(points),doc_id))
        scope = digest([self.version, int(metadata['cohort']), str(metadata['plan_number'])])
        self.db.execute('INSERT OR REPLACE INTO active VALUES(?,?)', (scope, doc_id))
        self.db.commit()
        return {'status':'indexed','document_id':doc_id,'points':len(points),'encoded':len(missing),
                'quality':bundle['quality']}

    def retry_limited_ocr(self, metadata, source):
        """Explicit bounded retry for pages skipped by the interactive OCR budget."""
        doc_id = self.document_id(metadata)
        row = self.db.execute('SELECT bundle FROM documents WHERE id=?', (doc_id,)).fetchone()
        if not row:raise ValueError('document_not_imported')
        bundle=json.loads(row[0])
        pending=[p for p in bundle['pages'] if p.get('ocr_status')=='ocr_resource_limit']
        if not pending:return {'status':'no_retry_needed'}
        body=Path(source).read_bytes()
        if sha256(body).hexdigest()!=metadata['sha256']:raise ValueError('source_hash_mismatch')
        for page in pending:
            text,spans=_ocr_page(body,page['page'],page,time.monotonic()+25)
            if not text.strip():raise ValueError('retry_no_text_detected')
            page.update(text=text,word_spans=spans,extraction_method='local_ocr',
                        text_quality='ocr_needs_review',ocr_status='completed')
        bundle['pages']=_pages(bundle['pages'])
        bundle['chunks']=_chunks(bundle['pages'])
        bundle['quality']=extraction_summary(bundle['pages'])
        # Keep the original extraction evidence before replacing a derived index.
        backup=self.root/(doc_id+'.before_ocr_retry.json')
        if not backup.exists():backup.write_text(row[0])
        self.db.execute('UPDATE documents SET bundle=?,ready=0 WHERE id=?',
                        (json.dumps(bundle,ensure_ascii=False),doc_id))
        self.db.commit()
        return self.ingest(metadata,source)

    def search(self, query, *, cohort, plan_number, limit=5):
        """Local operator API. Production callers must independently admit documents."""
        if not isinstance(query, str) or not query.strip() or len(query)>400 or not 1<=limit<=10:
            raise ValueError('invalid_query')
        admitted = []
        for doc_id, raw in self.db.execute('SELECT d.id,d.metadata FROM documents d JOIN active a ON a.document_id=d.id WHERE d.ready=1'):
            row = json.loads(raw)
            if int(row['cohort'])==int(cohort) and str(row['plan_number'])==str(plan_number) and self.document_id(row)==doc_id:
                admitted.append(doc_id)
        if not admitted:
            return []
        flt = self.models.Filter(must=[self.models.FieldCondition(key='document_id',match=self.models.MatchAny(any=admitted))])
        q = self.encoder.encode([QUERY_PREFIX+query])[0]
        dense = self.client.query_points(self.collection, query=q, query_filter=flt, limit=max(20,limit), with_payload=True).points
        # Complete lexical pool within the exact document scope, then RRF fusion.
        pool, offset = [], None
        while True:
            rows, offset = self.client.scroll(self.collection, scroll_filter=flt, limit=256, offset=offset, with_payload=True, with_vectors=False)
            pool.extend(rows)
            if offset is None:
                break
        wanted = set(terms(query))
        lexical = sorted(((len(wanted & set(terms(r.payload['text']))),r) for r in pool), key=lambda x:(-x[0],str(x[1].id)))
        lexical = [r for score,r in lexical if score>0]
        scores, by_id = {}, {str(r.id):r for r in pool}
        for ranking in (dense,lexical):
            for rank,r in enumerate(ranking,1):
                scores[str(r.id)] = scores.get(str(r.id),0)+1/(60+rank)
        ordered=sorted(scores,key=lambda i:-scores[i])
        return [{**by_id[i].payload,'fusion_score':scores[i]} for i in ordered[:limit]]

    def audit(self):
        counts=Counter(); quality=Counter(); expected=0
        for doc_id,metadata,bundle,points in self.db.execute('SELECT id,metadata,bundle,point_count FROM documents WHERE ready=1'):
            m=json.loads(metadata); b=json.loads(bundle)
            if self.document_id(m) != doc_id:
                continue
            counts[str(m['cohort'])]+=1;expected+=points
            for p in b['pages']:
                quality[p.get('extraction_method','unknown')]+=1
                if p.get('ocr_status'):quality['ocr_status:'+p['ocr_status']]+=1
                if not p['text'].strip():quality['empty_text_pages']+=1
            quality['pages_needing_review']+=len(b['quality']['pages_needing_review'])
        actual=self.client.count(self.collection,exact=True).count
        return {'documents_by_cohort':dict(counts),'documents':sum(counts.values()),'expected_points':expected,
                'persisted_points':actual,'counts_match':actual==expected,'page_methods':dict(quality),
                'collection':self.collection,'encoder_identity':self.encoder.identity}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['ingest','search','audit','retry-ocr'])
    parser.add_argument('--root',required=True);parser.add_argument('--encoder-manifest',required=True)
    parser.add_argument('--qdrant-url');parser.add_argument('--manifest');parser.add_argument('--cohort',type=int);parser.add_argument('--plan-number');parser.add_argument('--query')
    args=parser.parse_args();os.umask(0o077)
    os.environ.setdefault('OMP_THREAD_LIMIT','1')
    if args.action in {'ingest','retry-ocr'} and not args.manifest:parser.error('manifest is required')
    store=CurriculumVectorStore(args.root,Encoder(args.encoder_manifest),url=args.qdrant_url)
    try:
        if args.action=='ingest':
            manifest=Path(args.manifest).resolve();rows=json.loads(manifest.read_text())['documents']
            errors=[]
            # Only extraction runs concurrently. SQLite/Qdrant and encoding have
            # one owner; at most four document bundles are prefetched.
            with ThreadPoolExecutor(max_workers=2) as workers:
                futures={}
                def submit(index):
                    if index >= len(rows):return
                    row=rows[index]
                    cached=store.db.execute('SELECT 1 FROM documents WHERE id=?',(store.document_id(row),)).fetchone()
                    if not cached:
                        futures[index]=workers.submit(prepare_pdf,row,manifest.parent/row['source_root']/row['object'])
                for index in range(min(4,len(rows))):submit(index)
                for index,row in enumerate(rows):
                    try:
                        future=futures.pop(index,None)
                        prepared=future.result() if future else None
                        result=store.ingest(row,manifest.parent/row['source_root']/row['object'],prepared=prepared)
                        print(json.dumps({'position':index+1,'total':len(rows),'cohort':row['cohort'],'plan':row['plan_number'],**result},ensure_ascii=False),flush=True)
                    except Exception as error:
                        errors.append({'plan':row['plan_number'],'error':type(error).__name__,'reason':str(error)[:200]})
                        print(json.dumps(errors[-1]),flush=True)
                    submit(index+4)
            report={**store.audit(),'errors':errors,'encoded_this_run':store.encoder.encoded}
            (store.root/'last_ingest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
            print(json.dumps(report,ensure_ascii=False),flush=True)
            if errors: return 1
        elif args.action=='retry-ocr':
            manifest=Path(args.manifest).resolve()
            for row in json.loads(manifest.read_text())['documents']:
                result=store.retry_limited_ocr(row,manifest.parent/row['source_root']/row['object'])
                if result['status']!='no_retry_needed':print(json.dumps(result,ensure_ascii=False),flush=True)
            print(json.dumps(store.audit(),ensure_ascii=False,indent=2))
        elif args.action=='audit': print(json.dumps(store.audit(),ensure_ascii=False,indent=2))
        else:
            if args.cohort is None or not args.plan_number or not args.query:parser.error('search requires cohort, plan-number and query')
            print(json.dumps(store.search(args.query,cohort=args.cohort,plan_number=args.plan_number),ensure_ascii=False,indent=2))
    finally:store.close()
    return 0


if __name__=='__main__':
    raise SystemExit(main())
