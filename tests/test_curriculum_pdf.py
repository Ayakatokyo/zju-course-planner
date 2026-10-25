"""Synthetic physical-layout/OCR contracts; no school documents in fixtures."""
from unittest import TestCase, skipUnless
from unittest.mock import patch
import os
import shutil
import subprocess
import re
import zlib
from course_assistant import curriculum_pdf as pdf


def xml_pages(contents):
    return '<html><body><doc>' + ''.join(
        '<page width="600" height="800">' + text + '</page>' for text in contents) + '</doc></body></html>'


def word(text, x, y, width=35):
    return f'<word xMin="{x}" yMin="{y}" xMax="{x+width}" yMax="{y+10}">{text}</word>'


def synthetic_scan_pdf(text, header_text=None):
    from tests.test_personal_evidence import synthetic_pdf
    raster = subprocess.run(['pdftoppm', '-singlefile', '-gray', '-scale-to', '2400', '-'],
        input=synthetic_pdf([text]), capture_output=True, check=True).stdout
    header = re.match(rb'P5\s+(\d+)\s+(\d+)\s+255\s', raster)
    pixels = zlib.compress(raster[header.end():])
    command = b'q 595 0 0 842 0 0 cm /Im0 Do Q'
    if header_text:
        command += (' BT /F1 12 Tf 50 810 Td (' + header_text + ') Tj ET').encode('ascii')
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>',
        f'<< /Type /XObject /Subtype /Image /Width {int(header[1])} /Height {int(header[2])} /ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode /Length {len(pixels)} >>\nstream\n'.encode() + pixels + b'\nendstream',
        f'<< /Length {len(command)} >>\nstream\n'.encode() + command + b'\nendstream']
    if header_text:
        objects[2] = objects[2].replace(b'/Resources <<', b'/Resources << /Font << /F1 6 0 R >>')
        objects.append(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')
    body, offsets = bytearray(b'%PDF-1.4\n'), [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(body)); body.extend(f'{i} 0 obj\n'.encode()+obj+b'\nendobj\n')
    start = len(body)
    body.extend(f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode())
    for offset in offsets[1:]: body.extend(f'{offset:010d} 00000 n \n'.encode())
    body.extend(f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n'.encode())
    return bytes(body)


class CurriculumPDFTests(TestCase):
    def test_column_order_is_reconstructed_from_coordinates_with_exact_spans(self):
        xml = xml_pages([word('3.0', 300, 50) + word('Synthetic', 150, 50) + word('SYN1001M', 50, 50) + word('Fall', 400, 50)])
        page = pdf.native_pages(xml, ['raw native order retained'])[0]
        self.assertEqual(page['text'], 'SYN1001M  Synthetic  3.0  Fall\n')
        self.assertEqual(page['raw_text'], 'raw native order retained')
        for span, text in zip(page['word_spans'], ('SYN1001M', 'Synthetic', '3.0', 'Fall')):
            self.assertEqual(page['text'][span['start']:span['end']], text)
        self.assertEqual(page['word_spans'][0]['bbox'], [50, 50, 85, 60])

    def test_ocr_only_fills_unusable_pages_without_rewriting_native_values(self):
        xml = xml_pages([word('SYN1001M 3.0', 30, 50), '', word('\ufffd\ufffd\ufffd', 30, 50)])
        with patch.object(pdf, '_ocr_page', return_value=('识别课程 2.5\n', [])) as ocr:
            pages = pdf.extract_document(b'pdf', ['SYN1001M 3.0', '', 'corrupt'], xml)
        self.assertEqual([call.args[1] for call in ocr.call_args_list], [2, 3])
        self.assertEqual(pages[0]['text'], 'SYN1001M 3.0\n')
        self.assertEqual(pages[2]['raw_text'], 'corrupt')
        self.assertEqual(pages[1]['text_quality'], 'ocr_needs_review')

    def test_ocr_failure_does_not_turn_a_missing_page_into_zero_results(self):
        with patch.object(pdf, '_ocr_page', side_effect=ValueError('ocr_chinese_unavailable')):
            pages = pdf.extract_document(b'pdf', ['native', ''], xml_pages([word('native', 10, 10), '']))
        self.assertEqual(pages[1]['ocr_status'], 'ocr_chinese_unavailable')
        self.assertEqual(pages[1]['text_quality'], 'empty')
        self.assertEqual(pdf.extraction_summary([dict(p, page=i+1) for i,p in enumerate(pages)])['pages_needing_review'], [2])

    def test_sparse_native_header_with_large_image_gets_ocr_and_retains_failed_native(self):
        xml = xml_pages([word('课程标题', 30, 20)])
        body = '合成标题及图像课程内容 ' * 10 + ' SYN1002M 2.5'
        with patch.object(pdf, '_large_image_pages', return_value={1}), patch.object(pdf, '_ocr_page', return_value=(body, [])):
            page = pdf.extract_document(b'pdf', ['课程标题'], xml)[0]
        self.assertEqual(page['extraction_method'], 'local_ocr')
        self.assertIn('课程标题', page['text'])
        self.assertIn('2.5', page['text'])
        self.assertEqual(page['raw_text'], '课程标题')
        with patch.object(pdf, '_large_image_pages', return_value={1}), patch.object(pdf, '_ocr_page', side_effect=ValueError('ocr_unavailable')):
            page = pdf.extract_document(b'pdf', ['课程标题'], xml)[0]
        self.assertEqual(page['text'], '课程标题\n')
        self.assertEqual(page['text_quality'], 'sparse_image_needs_review')
        with patch.object(pdf, '_large_image_pages', return_value=set()), patch.object(pdf, '_ocr_page') as ocr:
            pdf.extract_document(b'pdf', ['课程标题'], xml)
            ocr.assert_not_called()

    def test_resource_limit_retains_all_physical_pages(self):
        with patch.object(pdf, '_ocr_page', return_value=('', [])) as ocr:
            pages = pdf.extract_document(b'pdf', ['']*10, xml_pages(['']*10))
        self.assertEqual(len(pages), 10)
        self.assertEqual(ocr.call_count, pdf.MAX_OCR_PAGES)
        self.assertEqual(pages[-1]['ocr_status'], 'ocr_resource_limit')

    def test_bad_layout_and_entities_are_rejected(self):
        for xml in (xml_pages([]), '<!ENTITY x SYSTEM "file:///private">'+xml_pages(['']),
                    xml_pages([word('x', 0, 0).replace('xMin="0"', 'xMin="nan"')])):
            with self.assertRaises((ValueError, TypeError)):
                pdf.native_pages(xml, ['x'])

    def test_chinese_ocr_characters_are_joined_without_joining_columns(self):
        text, spans = pdf._layout([{'text': '数', 'bbox': [0, 0, 10, 10]},
            {'text': '据', 'bbox': [11, 0, 21, 10]}, {'text': '3.0', 'bbox': [80, 0, 95, 10]}])
        self.assertEqual(text, '数据  3.0\n')
        self.assertEqual([text[s['start']:s['end']] for s in spans], ['数', '据', '3.0'])

    def test_punctuation_stays_on_its_text_line_and_unexplained_loss_is_flagged(self):
        text, _ = pdf._layout([{'text': '附注', 'bbox': [0, 0, 20, 10]},
                              {'text': '。', 'bbox': [21, 7, 24, 10]}])
        self.assertEqual(len(text.splitlines()), 1)
        page = pdf.native_pages(xml_pages([word('课程要求', 30, 10)]), ['课程要求\n仅限辅修'])[0]
        self.assertEqual(page['text_quality'], 'layout_needs_review')

    @skipUnless(os.environ.get('COURSE_OCR_TESSDATA') and shutil.which('tesseract') and shutil.which('pdftoppm'),
                'Real local OCR requires explicit Chinese/English data')
    def test_real_image_only_pdf_retrieves_codes_and_decimal_without_a_text_layer(self):
        from course_assistant.personal_evidence import _extract_document
        body = synthetic_scan_pdf('SYN1001M SyntheticCourse 2.5')
        page = _extract_document(bytes(body))[0]
        self.assertEqual(page['extraction_method'], 'local_ocr')
        self.assertEqual(page['text_quality'], 'ocr_needs_review')
        self.assertIn('SYN1001M', page['text'])
        self.assertIn('2.5', page['text'])
        self.assertTrue(page['word_spans'])

    @skipUnless(os.environ.get('COURSE_OCR_TESSDATA') and shutil.which('tesseract') and shutil.which('pdfimages'),
                'Real mixed PDF test requires local OCR and image metadata tools')
    def test_real_mixed_pdf_detects_native_header_and_scanned_course_body(self):
        from course_assistant.personal_evidence import _extract_document
        body = synthetic_scan_pdf('SYN1001M SyntheticCourse 2.5 Fall SYN1002M OtherCourse 3.0 Spring', 'Native header')
        page = _extract_document(body)[0]
        self.assertIn('Native header', page['raw_text'])
        self.assertEqual(page['ocr_reason'], 'sparse_native_with_large_image')
        self.assertEqual(page['extraction_method'], 'local_ocr')
        self.assertIn('SYN1001M', page['text'])
        self.assertIn('2.5', page['text'])
        self.assertIn('SYN1002M', page['text'])
        self.assertEqual(page['text_quality'], 'ocr_needs_review')
