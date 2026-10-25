"""Local PDF layout and optional OCR. All text remains unreviewed evidence.

Usable native words take precedence. OCR fills empty/corrupt pages and
conservatively detected sparse-text pages with large images. The original
extraction is retained and OCR values are marked unreviewed. Coordinates refer to the
original physical page (PDF points), including OCR coordinates after scaling.
"""
from collections import Counter
import csv
import io
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET

VERSION = "native-bbox-selective-ocr-v2"
MAX_WORDS = 100000
MAX_OCR_PAGES = 8


def quality(text):
    visible = re.sub(r"\s+", "", text)
    bad = sum(c == "\ufffd" or "\ue000" <= c <= "\uf8ff" for c in visible)
    if not visible:
        return "empty"
    if bad > max(2, len(visible) * .02):
        return "suspect_encoding"
    return "usable_text_unreviewed"


def _layout(words):
    """Merge words on the same baseline, preserving every word and its box."""
    lines = []
    for word in sorted(words, key=lambda w: (w["bbox"][1], w["bbox"][0])):
        x, y, right, bottom = word["bbox"]
        center, height = (y + bottom) / 2, bottom - y
        line = next((r for r in reversed(lines[-8:])
                     if abs(r["center"] - center) <= max(r["height"], height) * .5), None)
        if line is None:
            line = {"center": center, "height": height, "words": []}
            lines.append(line)
        line["words"].append(word)
    texts, spans, offset = [], [], 0
    for line in sorted(lines, key=lambda r: r["center"]):
        cells = sorted(line["words"], key=lambda w: w["bbox"][0])
        parts = []
        for index, word in enumerate(cells):
            if index:
                previous = cells[index - 1]
                gap = word["bbox"][0] - previous["bbox"][2]
                cjk = re.search(r"[\u3400-\u9fff]$", previous["text"]) and re.match(r"[\u3400-\u9fff]", word["text"])
                separator = "" if cjk and gap < line["height"] * .6 else "  "
                parts.append(separator)
                offset += len(separator)
            spans.append({"start": offset, "end": offset + len(word["text"]),
                          "bbox": word["bbox"], **({"confidence": word["confidence"]}
                          if "confidence" in word else {})})
            offset += len(word["text"])
            parts.append(word["text"])
        texts.append("".join(parts))
        offset += 1
    return "\n".join(texts) + ("\n" if texts else ""), spans


def native_pages(xml, raw_pages):
    # Poppler's fixed XHTML doctype is permitted; entity declarations are not.
    if "<!ENTITY" in xml:
        raise ValueError("invalid_pdf_layout")
    root = ET.fromstring(xml)
    pages = root.findall(".//{*}page")
    if len(pages) != len(raw_pages):
        raise ValueError("pdf_page_count_mismatch")
    result = []
    total = 0
    for page, raw in zip(pages, raw_pages):
        width, height = float(page.get("width")), float(page.get("height"))
        if not all(math.isfinite(v) and 0 < v <= 20000 for v in (width, height)):
            raise ValueError("invalid_pdf_dimensions")
        words = []
        for node in page.findall(".//{*}word"):
            text = node.text or ""
            if not text.strip():
                continue
            box = [float(node.get(k)) for k in ("xMin", "yMin", "xMax", "yMax")]
            if not all(math.isfinite(v) for v in box) or box[2] <= box[0] or box[3] <= box[1]:
                raise ValueError("invalid_pdf_coordinates")
            words.append({"text": text, "bbox": box})
        total += len(words)
        if total > MAX_WORDS:
            raise ValueError("pdf_word_limit")
        text, spans = _layout(words)
        removed = Counter(re.sub(r"\s+", "", raw)) - Counter(re.sub(r"\s+", "", text))
        unexplained_loss = any(char not in "浙江大学ZhejiangUniversityzhejianguniversity" for char in removed)
        text_quality = quality(text)
        if unexplained_loss and text_quality == "usable_text_unreviewed":
            text_quality = "layout_needs_review"
        result.append({"text": text, "raw_text": raw, "width": width, "height": height,
                       "word_spans": spans, "extraction_method": "native_layout",
                       "text_quality": text_quality, "ocr_status": "not_needed",
                       "diagonal_text_excluded": True})
    return result


def _run(args, *, deadline, maximum):
    """Bound both execution and output; no shell, stderr or inherited secrets."""
    with tempfile.TemporaryFile() as out:
        process = subprocess.Popen(args, stdout=out, stderr=subprocess.DEVNULL,
                                   env={"PATH": os.environ.get("PATH", ""), "OMP_THREAD_LIMIT": "2"}, shell=False)
        try:
            while process.poll() is None:
                if time.monotonic() >= deadline or os.fstat(out.fileno()).st_size > maximum:
                    raise ValueError("ocr_resource_limit")
                time.sleep(.02)
            if process.returncode or os.fstat(out.fileno()).st_size > maximum:
                raise ValueError("ocr_failed")
            out.seek(0)
            return out.read(maximum + 1)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()


def _ocr_page(body, number, page, deadline):
    renderer, engine = shutil.which("pdftoppm"), shutil.which("tesseract")
    if not renderer or not engine:
        raise ValueError("ocr_unavailable")
    data = os.environ.get("COURSE_OCR_TESSDATA")
    data_args = ["--tessdata-dir", data] if data else []
    languages = _run([engine, *data_args, "--list-langs"], deadline=deadline, maximum=20000).decode()
    if not {"chi_sim", "eng"} <= set(languages.splitlines()):
        raise ValueError("ocr_chinese_unavailable")
    with tempfile.TemporaryDirectory(prefix="curriculum-ocr-") as directory:
        pdf, raster = Path(directory) / "source.pdf", Path(directory) / "page.pgm"
        fd = os.open(pdf, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(body)
        image = _run([renderer, "-f", str(number), "-l", str(number), "-singlefile", "-gray",
                      "-scale-to", "2400", str(pdf)], deadline=deadline, maximum=24 * 1024 * 1024)
        raster.write_bytes(image)
        raster.chmod(0o600)
        header = re.match(rb"P5\s+(\d+)\s+(\d+)\s+255\s", image)
        if not header:
            raise ValueError("ocr_render_failed")
        sx, sy = page["width"] / int(header[1]), page["height"] / int(header[2])
        tsv = _run([engine, str(raster), "stdout", *data_args, "-l", "chi_sim+eng",
                    "--psm", "6", "-c", "tessedit_create_tsv=1"], deadline=deadline, maximum=4 * 1024 * 1024).decode("utf-8")
        if not tsv.startswith("level\tpage_num\t"):
            raise ValueError("ocr_failed")
        words = []
        for row in csv.DictReader(io.StringIO(tsv), delimiter="\t"):
            if row.get("level") != "5" or not (row.get("text") or "").strip():
                continue
            x, y, w, h = [int(row[k]) for k in ("left", "top", "width", "height")]
            confidence = float(row["conf"])
            if not math.isfinite(confidence) or not 0 <= confidence <= 100 or w <= 0 or h <= 0:
                continue
            words.append({"text": row["text"], "confidence": round(confidence, 2),
                          "bbox": [x * sx, y * sy, (x + w) * sx, (y + h) * sy]})
            if len(words) > 10000:
                raise ValueError("ocr_resource_limit")
        return _layout(words)


def _large_image_pages(body, pages, deadline):
    """A large embedded image plus sparse native text warrants local review.

    Image metadata alone never establishes that the image contains a table.
    Missing pdfimages leaves extraction conservative and does not erase text.
    """
    executable = shutil.which("pdfimages")
    if not executable:
        return set()
    try:
        with tempfile.TemporaryDirectory(prefix="curriculum-image-check-") as directory:
            path = Path(directory) / "source.pdf"
            with path.open("xb") as stream:
                os.chmod(path, 0o600)
                stream.write(body)
            output = _run([executable, "-list", str(path)], deadline=deadline, maximum=1024*1024).decode()
        found = set()
        for line in output.splitlines()[2:]:
            cells = line.split()
            if len(cells) < 16 or cells[2] != "image":
                continue
            number = int(cells[0])
            if not 1 <= number <= len(pages):
                continue
            width, height, xppi, yppi = (float(cells[i]) for i in (3, 4, 12, 13))
            page = pages[number - 1]
            if min(width, height, xppi, yppi) > 0 and width*height*72*72/(xppi*yppi) >= page["width"]*page["height"]*.2:
                found.add(number)
        return found
    except (ValueError, OSError, UnicodeError):
        return set()


def extract_document(body, raw_pages, bbox_xml):
    pages = native_pages(bbox_xml, raw_pages)
    deadline, attempted = time.monotonic() + 60, 0
    sparse = {i for i, page in enumerate(pages, 1) if 0 < len(re.sub(r"\s+", "", page["text"])) < 200
              and sum(.2*page["height"] < (word["bbox"][1]+word["bbox"][3])/2 < .9*page["height"]
                      for word in page["word_spans"]) < 6}
    image_pages = _large_image_pages(body, pages, min(deadline, time.monotonic()+5)) if sparse else set()
    for number, page in enumerate(pages, 1):
        mixed = number in sparse & image_pages
        if page["text_quality"] not in {"empty", "suspect_encoding"} and not mixed:
            continue
        if mixed:
            page.update(text_quality="sparse_image_needs_review", ocr_reason="sparse_native_with_large_image")
        # Corrupt native glyphs are preserved for comparison even after OCR.
        if attempted >= MAX_OCR_PAGES or time.monotonic() >= deadline:
            page["ocr_status"] = "ocr_resource_limit"
            continue
        attempted += 1
        try:
            text, spans = _ocr_page(body, number, page, min(deadline, time.monotonic() + 25))
            if mixed and len(re.sub(r"\s+", "", text)) <= len(re.sub(r"\s+", "", page["text"])) + 30:
                page["ocr_status"] = "no_additional_body_text"
                continue
            if text.strip():
                if mixed and re.sub(r"\s+", "", page["text"]) not in re.sub(r"\s+", "", text):
                    prefix = page["text"] + "\n"
                    spans = page["word_spans"] + [{**s, "start": s["start"]+len(prefix), "end": s["end"]+len(prefix)} for s in spans]
                    text = prefix + text
                page.update(text=text, word_spans=spans, extraction_method="local_ocr",
                            text_quality="ocr_needs_review", ocr_status="completed")
            else:
                page["ocr_status"] = "no_text_detected"
        except (ValueError, OSError, UnicodeError) as error:
            allowed = {"ocr_unavailable", "ocr_chinese_unavailable", "ocr_resource_limit", "ocr_failed", "ocr_render_failed"}
            page["ocr_status"] = str(error) if str(error) in allowed else "ocr_failed"
    return pages


def extraction_summary(pages):
    counts = Counter(p.get("extraction_method", "injected_text") for p in pages)
    attention = [p["page"] for p in pages if p.get("text_quality") in {"empty", "suspect_encoding", "ocr_needs_review", "layout_needs_review", "sparse_image_needs_review"}]
    return {"native_pages": counts["native_layout"], "ocr_pages": counts["local_ocr"],
            "pages_needing_review": attention, "quality_verified": False}
