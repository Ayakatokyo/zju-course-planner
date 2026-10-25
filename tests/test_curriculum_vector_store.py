import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from course_assistant.curriculum_vector_store import CurriculumVectorStore

class FakeEncoder:
    identity='test-model-v1'
    encoded=0
    def split(self,text):return [text]
    def encode(self,texts):
        self.encoded+=len(texts)
        return [[1.0]+[0.0]*511 for text in texts]

@unittest.skipUnless(importlib.util.find_spec('qdrant_client'),'optional Qdrant environment required')
class VectorStoreTests(unittest.TestCase):
    def test_resume_restart_scope_and_content_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf=Path(tmp)/'source.pdf';pdf.write_bytes(b'%PDF-synthetic')
            metadata={'cohort':2025,'plan_number':'20250001','sha256':hashlib.sha256(pdf.read_bytes()).hexdigest()}
            encoder=FakeEncoder();store=CurriculumVectorStore(Path(tmp)/'db',encoder)
            try:
                with patch('course_assistant.curriculum_vector_store._extract_document',return_value=['专业必修课程\n必须修满20学分。']):
                    first=store.ingest(metadata,pdf)
                self.assertGreater(first['points'],0)
                self.assertTrue(store.audit()['counts_match'])
            finally:store.close()
            encoder=FakeEncoder();store=CurriculumVectorStore(Path(tmp)/'db',encoder)
            try:
                with patch('course_assistant.curriculum_vector_store._extract_document',side_effect=AssertionError('must reuse')):
                    self.assertEqual(store.ingest(metadata,pdf)['status'],'reused')
                self.assertEqual(encoder.encoded,0)
                result=store.search('必修学分',cohort=2025,plan_number='20250001')
                self.assertTrue(result);self.assertEqual(result[0]['page'],1)
                self.assertEqual(store.search('必修学分',cohort=2024,plan_number='20250001'),[])
                pdf.write_bytes(b'%PDF-changed')
                with self.assertRaisesRegex(ValueError,'source_hash_mismatch'):store.ingest(metadata,pdf)
                changed={**metadata,'sha256':hashlib.sha256(pdf.read_bytes()).hexdigest()}
                self.assertNotEqual(store.document_id(metadata),store.document_id(changed))
            finally:store.close()

    def test_partial_upload_is_hidden_and_resume_reuses_vectors(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf=Path(tmp)/'source.pdf';pdf.write_bytes(b'%PDF-synthetic')
            metadata={'cohort':2023,'plan_number':'20230001','sha256':hashlib.sha256(pdf.read_bytes()).hexdigest()}
            encoder=FakeEncoder();store=CurriculumVectorStore(Path(tmp)/'db',encoder)
            try:
                with patch('course_assistant.curriculum_vector_store._extract_document',return_value=['专业必修课程\n必须修满20学分。']), patch.object(store.client,'upsert',side_effect=RuntimeError('interrupted')):
                    with self.assertRaises(RuntimeError):store.ingest(metadata,pdf)
                self.assertEqual(store.search('必修',cohort=2023,plan_number='20230001'),[])
                encoded=encoder.encoded
                with patch('course_assistant.curriculum_vector_store._extract_document',side_effect=AssertionError('must reuse extraction')):
                    result=store.ingest(metadata,pdf)
                self.assertEqual(result['encoded'],0)
                self.assertEqual(encoder.encoded,encoded)
                self.assertTrue(store.audit()['counts_match'])
            finally:store.close()

    def test_failed_update_preserves_active_version_and_success_switches(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf=Path(tmp)/'source.pdf';pdf.write_bytes(b'%PDF-version1')
            metadata={'cohort':2025,'plan_number':'20250001','sha256':hashlib.sha256(pdf.read_bytes()).hexdigest()}
            store=CurriculumVectorStore(Path(tmp)/'db',FakeEncoder())
            try:
                with patch('course_assistant.curriculum_vector_store._extract_document',return_value=['必修要求20学分。']):store.ingest(metadata,pdf)
                pdf.write_bytes(b'%PDF-version2')
                newer={**metadata,'sha256':hashlib.sha256(pdf.read_bytes()).hexdigest()}
                with patch('course_assistant.curriculum_vector_store._extract_document',return_value=['必修要求25学分。']), patch.object(store.client,'upsert',side_effect=RuntimeError('interrupted')):
                    with self.assertRaises(RuntimeError):store.ingest(newer,pdf)
                self.assertEqual({r['pdf_sha256'] for r in store.search('必修',cohort=2025,plan_number='20250001')},{metadata['sha256']})
                store.ingest(newer,pdf)
                self.assertEqual({r['pdf_sha256'] for r in store.search('必修',cohort=2025,plan_number='20250001')},{newer['sha256']})
            finally:store.close()

    def test_limited_ocr_retry_replaces_derived_points_without_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf=Path(tmp)/'source.pdf';pdf.write_bytes(b'%PDF-synthetic')
            metadata={'cohort':2025,'plan_number':'20250001','sha256':hashlib.sha256(pdf.read_bytes()).hexdigest()}
            store=CurriculumVectorStore(Path(tmp)/'db',FakeEncoder())
            try:
                pages=[{'text':'专业课程要求20学分。','extraction_method':'native_layout'},
                       {'text':'','ocr_status':'ocr_resource_limit','text_quality':'empty'}]
                with patch('course_assistant.curriculum_vector_store._extract_document',return_value=pages):store.ingest(metadata,pdf)
                with patch('course_assistant.curriculum_vector_store._ocr_page',return_value=('毕业论文设计6学分。',[])):
                    store.retry_limited_ocr(metadata,pdf)
                self.assertTrue(store.audit()['counts_match'])
                hits=store.search('毕业论文',cohort=2025,plan_number='20250001')
                self.assertTrue(any(r['page']==2 and '6学分' in r['text'] for r in hits))
                self.assertEqual(store.retry_limited_ocr(metadata,pdf)['status'],'no_retry_needed')
            finally:store.close()

if __name__=='__main__':unittest.main()
