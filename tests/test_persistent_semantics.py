import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from course_assistant.persistent_semantics import PersistentSemanticRetriever,QdrantCache,text_key
from course_assistant.semantic_retrieval import LocalSemanticRetriever
from course_assistant.personal_evidence import PersonalEvidenceService

IDENTITY={'manifest_sha256':'a'*64,'artifact_sha256':'b'*64,'revision':'test','status':'available'}
VECTOR=[1.0]+[0.0]*511

def encode(texts,**kwargs):
    return {'vectors':[VECTOR[:] for _ in texts],'token_truncated_indices':[],
            'token_truncated_count':0,'identity':{k:IDENTITY[k] for k in ('manifest_sha256','artifact_sha256','revision')}}

class Cache:
    def __init__(self):self.rows={};self.puts=0
    def ensure(self,*args,**kwargs):pass
    def retrieve(self,collection,keys):return {k:self.rows[k] for k in keys if k in self.rows}
    def put(self,collection,rows):
        self.puts+=len(rows)
        self.rows.update({k:(v,t) for k,v,t in rows})

class PersistentTests(unittest.TestCase):
    def retriever(self):
        r=PersistentSemanticRetriever('unused',url='http://127.0.0.1:6333')
        r.cache=Cache();r.status=lambda:IDENTITY
        return r

    def test_existing_cohorts_check_only_no_encoding_or_writes(self):
        r=self.retriever();r.cache.rows[text_key('a'*64,'已有条款')]=(VECTOR,False)
        with patch.object(LocalSemanticRetriever,'_run_encoder',side_effect=AssertionError('must not encode')):
            result=r.warm(['已有条款','缺失条款'],lambda:None)
        self.assertEqual(result['status'],'existing_corpus_missing_vectors')
        self.assertEqual(result['missing_vectors'],1);self.assertEqual(r.cache.puts,0)

    def test_new_cohort_first_checks_cache_then_only_encodes_missing(self):
        r=self.retriever();r.allow_write=True;r.cache.rows[text_key('a'*64,'已有条款')]=(VECTOR,False)
        with patch.object(LocalSemanticRetriever,'_run_encoder',side_effect=encode) as worker:
            first=r.warm(['已有条款','新条款'],lambda:None)
            second=r.warm(['已有条款','新条款'],lambda:None)
        self.assertEqual(first['encoded_count'],1);self.assertEqual(second['encoded_count'],0)
        self.assertEqual(r.cache.puts,1);self.assertEqual(worker.call_count,1)

    def test_readonly_query_miss_is_transient_not_an_increment(self):
        r=self.retriever()
        with patch.object(LocalSemanticRetriever,'_run_encoder',side_effect=encode):
            result=r._run_encoder(['查询','未入库文本'],expected_identity=IDENTITY)
        self.assertEqual(len(result['vectors']),2);self.assertEqual(r.cache.puts,0)

    def test_revoked_warm_does_not_publish(self):
        r=self.retriever();r.allow_write=True
        def revoked():raise ValueError('revoked')
        with patch.object(LocalSemanticRetriever,'_run_encoder',side_effect=AssertionError('no encoding')):
            with self.assertRaisesRegex(ValueError,'revoked'):r.warm(['内容'],revoked)
        self.assertEqual(r.cache.puts,0)

    def test_revocation_while_encoding_blocks_qdrant_write(self):
        r=self.retriever();r.allow_write=True;revoked=[False]
        def authorize():
            if revoked[0]:raise ValueError('revoked_during_encoding')
        def worker(texts,**kwargs):
            result=encode(texts,**kwargs);revoked[0]=True;return result
        with patch.object(LocalSemanticRetriever,'_run_encoder',side_effect=worker):
            with self.assertRaisesRegex(ValueError,'revoked_during_encoding'):
                r.warm(['新增文本'],authorize)
        self.assertEqual(r.cache.puts,0)

    def test_verified_cohort_policy_and_loopback_only(self):
        for cohort,allowed in [(2023,False),('2024',False),(2025,False),(2026,False),(2027,True),(2022,True),(None,False),('unknown',False)]:
            with self.subTest(cohort=cohort),tempfile.TemporaryDirectory() as root:
                fake=SimpleNamespace(allow_write=False)
                with patch.object(LocalSemanticRetriever,'from_environment',return_value=fake):
                    PersonalEvidenceService(Path(root).resolve()/'evidence',lambda:{'scope_key':'test'},lambda _:b'',lambda:{},cohort=cohort)
                self.assertEqual(fake.allow_write,allowed)
        for url in ['https://example.com','http://127.0.0.1@evil.test','http://localhost/path']:
            with self.assertRaises(ValueError):QdrantCache(url)
