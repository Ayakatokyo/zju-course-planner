from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from course_assistant.personal_evidence import PersonalEvidenceService
from course_assistant.semantic_retrieval import LocalSemanticRetriever
from course_assistant.curriculum_extraction_cache import ExtractionCache

class ExtractionCacheTests(unittest.TestCase):
    def test_cross_login_reuse_binds_owner_and_exact_pdf(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve();calls=[]
            def extract(body):calls.append(body);return ['2027合成培养方案\n专业必修课程6学分。']
            def prepare(session,owner,body):
                metadata={'source_alias':'PLAN-'+sha256(body).hexdigest()[:24],'sha256':sha256(body).hexdigest()}
                with patch.object(LocalSemanticRetriever,'from_environment',return_value=SimpleNamespace(allow_write=False)):
                    e=PersonalEvidenceService(root/session,lambda:{'scope_key':session,'owner_student_id':owner,'security_realm':'synthetic'},lambda _:body,lambda:metadata,extract,cohort=2027)
                e.prepare(metadata['source_alias'])
            with patch.dict('os.environ',{'COURSE_CURRICULUM_CORPUS':str(root/'corpus.sqlite3')}):
                body=b'%PDF-1.4\nSYNTHETIC-A\n%%EOF\n'
                prepare('session-1','one',body);prepare('session-2','one',body)
                self.assertEqual(len(calls),1)
                prepare('session-3','two',body)
                self.assertEqual(len(calls),2)
                prepare('session-4','one',b'%PDF-1.4\nSYNTHETIC-B\n%%EOF\n')
                self.assertEqual(len(calls),3)

    def test_revoked_write_rolls_back(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache=ExtractionCache(Path(temporary)/'corpus.sqlite3');calls=[]
            def authorize():
                calls.append(1)
                if len(calls)==3:raise ValueError('revoked')
            with self.assertRaisesRegex(ValueError,'revoked'):cache.write('key',{'pages':[]},authorize)
            self.assertIsNone(cache.read('key'))
