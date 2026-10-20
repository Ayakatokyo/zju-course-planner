"""Owner-scoped local PDF evidence, without school requests or executable rules.

Callbacks are server-owned: ``authorize`` supplies a scope_key incorporating
owner/realm/session generation, ``describe_document`` supplies the current
source_alias/sha256, and ``read_document(alias)`` reads that permitted PDF.
An injected extractor accepts PDF bytes and returns one string per physical
page. The default is a fixed pdftotext command over pipes, never a shell.
All extracted content remains unreviewed data; no PDF text can select a file,
change authorization, run a command or become an approved requirement.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from functools import wraps
from hashlib import sha256
import base64
import fcntl
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import shutil
import stat
import subprocess
import time
import unicodedata

from .reference_retrieval import terms
from .semantic_retrieval import LocalSemanticRetriever

SCHEMA = "personal-evidence/1"
CHUNKING = "curriculum-semantic-source-spans-v3"
EXTRACTION = "pdftotext-layout-utf8-source-spans-v2"
CLEANING = "conservative-watermark-lines-v1"
PURPOSE = "personal_curriculum_local_reference"
MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_TEXT_BYTES = 4 * 1024 * 1024
MAX_STORE_BYTES = 16 * 1024 * 1024
MAX_PAGES = 300
MAX_PAGE_CHARS = 60000
MAX_CHUNKS = 2000
MAX_MODEL_FRAGMENT_CHARS = 1800
MAX_MODEL_CONTEXT_CHARS = 2400
MAX_MODEL_CONTEXT_ITEMS = 8
ALIAS = re.compile(r"PLAN-[a-f0-9]{24}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")
REFERENCE = re.compile(r"PERSONAL-REF-[A-Za-z0-9_-]{43}\Z")
CODE_TOKENS = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*(?![A-Za-z0-9_-])")
CODE_SHAPES = re.compile(r"(?:[A-Z]{1,12}\d{3,}[A-Z0-9]*|\d{2,}[A-Z][A-Z0-9]{2,}|[A-Z]{2,20}(?:-[A-Z]{2,20})*-\d{3,}[A-Z0-9]*|\d{6,10})\Z")
AUTH_ERRORS = frozenset({"grant_revoked", "grant_expired", "permission_denied", "grant_denied",
    "source_session_expired", "source_subject_changed", "source_generation_changed",
    "awaiting_user_login", "captcha_required", "source_authorization_changed",
    "school_connection_expired", "unauthenticated"})
WARNINGS = ["本机提取文本未经人工核对，不构成官方规则或本人适用认定。",
    "表格读序、跨页关系及附注作用域尚未审核；整页和相邻页只提供核对上下文。",
    "未调用远程生成模型；检索命中不等于答案支持、已获学分或推荐就绪。"]


class PersonalEvidenceError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _fail(code):
    raise PersonalEvidenceError(code)


def _safe_storage(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except OSError:
            _fail("private_store_unavailable")
    return wrapped


def _encoded(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _fail("schema_changed")


def _digest(value):
    return sha256(_encoded(value)).hexdigest()


def _codes(text):
    return {token for token in CODE_TOKENS.findall(unicodedata.normalize("NFKC", text).upper())
            if CODE_SHAPES.fullmatch(token)}


def _query_codes(text):
    normalized = unicodedata.normalize("NFKC", text).strip()
    candidates = _codes(normalized)
    # Bare counts and dates are not reliable course-code declarations. Pure
    # numeric identifiers need an isolated query or an explicit code label.
    result = {value for value in candidates if not value.isdecimal()}
    explicit = {match[1] for match in re.finditer(r"(?:课程代码|课程号|课号|课程编码|代码|course\s*code)\s*[:：]?\s*(\d{6,10})(?![A-Za-z0-9_-])", normalized, re.I)}
    result.update(candidates & explicit)
    if normalized in candidates and normalized.isdecimal():
        calendar_date = False
        if len(normalized) == 8 and 1900 <= int(normalized[:4]) <= 2100:
            try:
                datetime.strptime(normalized, "%Y%m%d")
                calendar_date = True
            except ValueError:
                pass
        if not calendar_date:
            result.add(normalized)
    return result


def _pdf(body):
    if not isinstance(body, bytes) or len(body) < 16 or len(body) > MAX_PDF_BYTES:
        _fail("document_limit_exceeded" if isinstance(body, bytes) and len(body) > MAX_PDF_BYTES else "invalid_pdf")
    if not body.startswith(b"%PDF-") or b"%%EOF" not in body[-4096:]:
        _fail("invalid_pdf")
    return body


def _extract_pdf(body):
    executable = shutil.which("pdftotext")
    if not executable:
        _fail("extractor_unavailable")
    process = None
    selector = selectors.DefaultSelector()
    output = bytearray()
    try:
        process = subprocess.Popen([executable, "-layout", "-enc", "UTF-8", "-", "-"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False)
        for stream, events in ((process.stdin, selectors.EVENT_WRITE), (process.stdout, selectors.EVENT_READ)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, events)
        offset, deadline = 0, time.monotonic() + 15
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _fail("extraction_timeout")
            for key, _ in selector.select(min(0.2, remaining)):
                stream = key.fileobj
                if stream is process.stdin:
                    try:
                        offset += os.write(stream.fileno(), body[offset:offset + 65536])
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        offset = len(body)
                    if offset == len(body):
                        selector.unregister(stream)
                        stream.close()
                else:
                    try:
                        part = os.read(stream.fileno(), 65536)
                    except BlockingIOError:
                        continue
                    if not part:
                        selector.unregister(stream)
                        stream.close()
                    else:
                        output.extend(part)
                        if len(output) > MAX_TEXT_BYTES:
                            _fail("extraction_limit_exceeded")
        if process.wait(timeout=max(0.01, deadline - time.monotonic())) != 0:
            _fail("extraction_failed")
        pages = output.decode("utf-8").split("\f")
        if len(pages) > 1 and pages[-1] == "":
            pages.pop()  # pdftotext emits one terminal form-feed, not a new page.
        return pages
    except PersonalEvidenceError:
        raise
    except FileNotFoundError:
        _fail("extractor_unavailable")
    except subprocess.TimeoutExpired:
        _fail("extraction_timeout")
    except (OSError, UnicodeError):
        _fail("extraction_failed")
    finally:
        selector.close()
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait()
            for stream in (process.stdin, process.stdout):
                if stream and not stream.closed:
                    stream.close()


def _pages(values):
    if not isinstance(values, list) or not values or len(values) > MAX_PAGES:
        _fail("extraction_failed")
    result, total = [], 0
    for number, text in enumerate(values, 1):
        if (not isinstance(text, str) or len(text) > MAX_PAGE_CHARS or "\x00" in text or "\f" in text):
            _fail("extraction_failed")
        try:
            raw = text.encode("utf-8")
        except UnicodeError:
            _fail("extraction_failed")
        total += len(raw)
        if total > MAX_TEXT_BYTES:
            _fail("extraction_limit_exceeded")
        result.append({"page": number, "text": text, "page_text_sha256": sha256(raw).hexdigest()})
    if not any(row["text"].strip() for row in result):
        _fail("no_extractable_text")
    return result


def _locator(page, start, end):
    return {"physical_page": page["page"], "char_start": start, "char_end": end,
        "char_unit": "unicode_codepoint", "page_text_sha256": page["page_text_sha256"]}


def _watermark_piece(text, *, isolated=False):
    """Remove only known watermark words/fragments in their own layout cell.

    Inline letters, course codes and ordinary Chinese prose are never rewritten.
    This is intentionally conservative: uncertain glyphs remain source data.
    """
    original = re.sub(r"\s+", "", text)
    value = original.casefold()
    if not value:
        return False
    if value in {"浙江大学", "zhejiang", "university", "zhejianguniversity"}:
        return True
    if len(value) >= 3 and (value in "zhejiang" or value in "university"):
        return True
    if not isolated:
        return False
    if len(value) == 1:
        # Preserve standalone grade/track/roman-numeral cells such as A/I/V;
        # only Z and U are uppercase letters of the known watermark words.
        return not (original.isupper() and original not in {"Z", "U"}) and value in "浙江大学zhejianguniversity"
    # Do not mistake useful acronyms (AI, IT) or words for arbitrary two-glyph
    # watermark combinations merely because they share its alphabet.
    return len(value) == 2 and original.islower() and (value in "zhejiang" or value in "university")


def _clean_source(page, start, end):
    """Return display/search text plus exact, retained source spans.

    The original contiguous excerpt and its locator remain untouched. Cleaning
    only removes standalone watermark cells, recording every kept character's
    provenance; it never manufactures a course value or changes the PDF.
    """
    retained, lines, position = [], [], start
    for raw in page["text"][start:end].splitlines(keepends=True):
        content = raw.rstrip("\r\n")
        stripped = content.strip()
        if stripped and not _watermark_piece(stripped, isolated=True):
            cells = []
            for match in re.finditer(r"\S+(?:[ \t]\S+)*", content):
                value = match[0]
                if _watermark_piece(value, isolated=True):
                    continue
                left, right = position + match.start(), position + match.end()
                retained.append({"text": value, "locator": _locator(page, left, right)})
                cells.append(value)
            if cells:
                lines.append(" ".join(cells))
        position += len(raw)
    return "\n".join(lines), retained


def _heading(text):
    compact = re.sub(r"\s+", "", text)
    if not compact or len(compact) > 200:
        return None
    if re.search(r"培养方案[：:]?$", compact) and not re.match(r"\d+[.、．]", compact):
        return (0, compact, "source_title")
    match = re.match(r"^(?:(\d+)[.、．]|[（(](\d+)[)）]|(\d+)[)）]|([一二三四五六七八九十]+)[、.])", compact)
    numbered = bool(match)
    label = compact[match.end():] if match else compact
    # A numbered course choice is a condition, not a new curriculum module.
    topic = re.match(r"(?:专业(?:模块|基础|必修|选修)?(?:课程|课)?|通识(?:选修)?(?:课程|类)?|"
        r"(?:创新创业|自然科学通识|外语|计算机|军体|思政|美育|劳育)(?:课程|类)?|"
        r"个性修读课程|实践教学环节|毕业论文(?:[（(]设计[)）])?|第二课堂|第三课堂|第四课堂|"
        r"培养目标|毕业要求|课程设置与学分分布|(?:必修|选修)课程)", label)
    if not topic or (not numbered and not ("学分" in label or label in {"培养目标", "毕业要求", "课程设置与学分分布"})):
        return None
    label = re.split(r"[，,:：；;]|(?:至少|不低于|不少于)?\d+(?:\.\d+)?(?:学分|门)", label, maxsplit=1)[0]
    if not label:
        return None
    depth = (1 if match and (match[1] or match[4]) else 2 if match and match[2] else 3) if match else 2
    return depth, label, "module_requirement"


def _line_role(text):
    if ("课程号" in text or "课程代码" in text) and ("学分" in text or "课程名称" in text):
        return "table_header"
    if "续表" in text and len(text) < 80:
        return "table_header"
    token = re.match(r"^([A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*)\s+", text)
    if token and token[1].upper() in _codes(token[1]):
        return "course_row"
    if re.match(r"^(?:附注|注[：:]|备注|说明[：:]|例外|[*＊△※])", text):
        return "module_note"
    return "paragraph"


def _chunks(pages):
    """Conservative document structure with exact raw spans and bounded model text.

    Headings and table headers survive page boundaries. Dependency links are
    candidate reading context, never reviewed table interpretation or rules.
    """
    chunks, modules, stack, current_header = [], {}, [], None

    def add(page, start, end, role, module_keys):
        cursor, group = start, len(chunks)
        indices = []
        while cursor < end:
            right = min(cursor + MAX_MODEL_FRAGMENT_CHARS, end)
            if right < end:
                segment = page["text"][cursor:right]
                split = max(segment.rfind("\n"), segment.rfind("。") + 1, segment.rfind("；") + 1)
                if split > MAX_MODEL_FRAGMENT_CHARS // 2:
                    right = cursor + split
            raw = page["text"][cursor:right]
            model_text, source_spans = _clean_source(page, cursor, right)
            if model_text.strip():
                first = next(line for line in model_text.splitlines() if line.strip())
                keys = list(module_keys)
                labels = [modules[key]["title"] for key in keys]
                row = {"page": page["page"], "title": first[:120], "text": raw,
                    "model_text": model_text, "model_text_spans": source_spans,
                    "text_sha256": sha256(raw.encode()).hexdigest(), "locator": _locator(page, cursor, right),
                    "review_status": "unreviewed", "context_completeness": "unverified",
                    "cleaning_version": CLEANING, "chunk_kind": "partial_fragment" if end - start > MAX_MODEL_FRAGMENT_CHARS else role,
                    "semantic_role": role, "fragment_group": group, "module_keys": keys,
                    "module_title": labels[-1] if labels else None, "module_path": labels,
                    "course_codes": sorted(_codes(model_text)), "table_header_ordinal": current_header,
                    "required_context_pages": list(range(max(1, page["page"] - 1), min(len(pages), page["page"] + 1) + 1))}
                indices.append(len(chunks))
                chunks.append(row)
                if len(chunks) > MAX_CHUNKS:
                    _fail("extraction_limit_exceeded")
            if right == end:
                break
            # Long prose has a small exact overlap and remains explicitly partial.
            cursor = max(cursor + 1, right - 100)
        return indices

    for page in pages:
        position, buffer_start, buffer_end, buffer_role = 0, None, None, None

        def flush():
            nonlocal buffer_start, buffer_end, buffer_role
            if buffer_start is not None:
                indices = add(page, buffer_start, buffer_end, buffer_role, [key for _, key in stack])
                if stack and buffer_role in {"paragraph", "module_note", "module_requirement"}:
                    modules[stack[-1][1]]["context"].extend(indices)
                buffer_start = buffer_end = buffer_role = None

        for raw in page["text"].splitlines(keepends=True):
            left, right = position, position + len(raw)
            position = right
            clean, _ = _clean_source(page, left, right)
            if not clean:
                if not raw.strip():
                    flush()
                continue
            heading = _heading(clean)
            if heading:
                flush()
                depth, label, role = heading
                if role == "source_title":
                    if re.match(r"(?:微?辅修|双学位)", label):
                        # A second study path must not inherit the previous
                        # main-path module or its table header.
                        stack.clear()
                        current_header = None
                    add(page, left, right, role, [])
                    continue
                while stack and stack[-1][0] >= depth:
                    stack.pop()
                key = "module-" + str(len(modules))
                modules[key] = {"title": label, "context": [], "heading": []}
                stack.append((depth, key))
                current_header = None
                indices = add(page, left, right, role, [value for _, value in stack])
                modules[key]["heading"].extend(indices)
                modules[key]["context"].extend(indices)
                continue
            role = _line_role(clean)
            if role in {"table_header", "course_row"}:
                flush()
                indices = add(page, left, right, role, [key for _, key in stack])
                if role == "table_header" and indices:
                    # A bare continuation marker does not replace the column header.
                    if "课程号" in clean or "课程代码" in clean or current_header is None:
                        current_header = indices[0]
                continue
            if buffer_start is not None and role != buffer_role:
                flush()
            if buffer_start is None:
                buffer_start, buffer_role = left, role
            buffer_end = right
        flush()

    # IDs do not recursively include dependency IDs; the entire rebuilt bundle
    # and its signed index hash bind the links, source and processing versions.
    for row in chunks:
        row["chunk_id"] = "PCH-" + _digest(row)
    for index, row in enumerate(chunks):
        dependencies = []
        def need(ordinal, context_role):
            if ordinal != index and all(value[0] != ordinal for value in dependencies):
                dependencies.append((ordinal, context_role))
        for key in row["module_keys"]:
            for ordinal in modules[key]["heading"]:
                need(ordinal, "module_heading")
        if row["table_header_ordinal"] is not None:
            need(row["table_header_ordinal"], "table_header")
        # Keep prose conditions and following notes with rows from the same
        # module, including continuation pages. Scope remains unreviewed.
        for note_first in (True, False):
            for key in reversed(row["module_keys"]):
                for ordinal in modules[key]["context"]:
                    is_note = chunks[ordinal]["semantic_role"] == "module_note"
                    if is_note == note_first:
                        need(ordinal, "module_note" if is_note else "module_condition")
        if row["chunk_kind"] == "partial_fragment":
            for ordinal, other in enumerate(chunks):
                if other["fragment_group"] == row["fragment_group"]:
                    need(ordinal, "continuation")
        row["semantic_context"] = [{"chunk_id": chunks[ordinal]["chunk_id"], "context_role": role} for ordinal, role in dependencies]
        row["required_context_pages"] = sorted(set(row["required_context_pages"]) | {chunks[ordinal]["page"] for ordinal, _ in dependencies})
    return chunks


class PersonalEvidenceService:
    """A private registry bound to the current callback authorization generation."""

    def __init__(self, root, authorize, read_document, describe_document, extractor=None):
        if not all(callable(fn) for fn in (authorize, read_document, describe_document)) or (extractor is not None and not callable(extractor)):
            _fail("invalid_arguments")
        self._authorize_callback, self._read_callback = authorize, read_document
        self._describe_callback, self._extractor = describe_document, extractor or _extract_pdf
        self._semantic_retriever = LocalSemanticRetriever.from_environment()
        self._extraction_version = EXTRACTION if extractor is None else "injected-page-extractor-v2"
        self.root = Path(root).expanduser().absolute()
        if self.root.resolve() != self.root:
            _fail("invalid_private_path")
        try:
            missing, parent = [], self.root
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for directory in reversed(missing):
                directory.mkdir(mode=0o700, exist_ok=True)
            self._directory(self.root)
            for name in ("versions", "scopes"):
                path = self.root / name
                if not path.exists():
                    path.mkdir(mode=0o700, exist_ok=True)
                self._directory(path)
            self._lock_path = self.root / "publication.lock"
            if not self._lock_path.exists():
                try:
                    self._write_new(self._lock_path, b"")
                except FileExistsError:
                    pass
            with self._locked():
                key_path = self.root / "signing.key"
                if not key_path.exists():
                    self._write_new(key_path, secrets.token_bytes(32))
                self._key = self._read(key_path, 32)
                if len(self._key) != 32:
                    _fail("private_store_unavailable")
        except OSError:
            _fail("private_store_unavailable")

    @staticmethod
    def _directory(path):
        if path.is_symlink() or not path.is_dir() or stat.S_IMODE(path.stat().st_mode) != 0o700:
            _fail("private_permissions_required")

    @staticmethod
    def _file(path):
        if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o600:
            _fail("private_permissions_required")

    def _check_store(self):
        if self.root.resolve() != self.root:
            _fail("invalid_private_path")
        for path in (self.root, self.root / "versions", self.root / "scopes"):
            self._directory(path)
        self._file(self._lock_path)
        if hasattr(self, "_key") and self._read(self.root / "signing.key", 32) != self._key:
            _fail("stale_input")

    @contextmanager
    def _locked(self):
        self._check_store()
        fd = os.open(self._lock_path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    @staticmethod
    def _write_new(path, body):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def _read(self, path, limit=MAX_STORE_BYTES):
        self._file(path)
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
                _fail("private_permissions_required")
            data = stream.read(limit + 1)
        if len(data) > limit:
            _fail("private_store_unavailable")
        return data

    def _sign(self, kind, value):
        return hmac.new(self._key, kind.encode() + b"\x00" + _encoded(value), "sha256").hexdigest()

    def _envelope(self, kind, payload):
        return _encoded({"payload": payload, "signature": self._sign(kind, payload)})

    def _open(self, kind, path):
        try:
            value = json.loads(self._read(path))
            if set(value) != {"payload", "signature"} or not isinstance(value["signature"], str) or not hmac.compare_digest(value["signature"], self._sign(kind, value["payload"])):
                _fail("stale_input")
            return value["payload"]
        except PersonalEvidenceError:
            raise
        except (ValueError, TypeError, OSError, RecursionError):
            _fail("private_store_unavailable")

    def _authorize(self, expected=None):
        try:
            receipt = self._authorize_callback()
        except Exception as error:
            code = getattr(error, "code", getattr(error, "reason", "permission_denied"))
            _fail(code if code in AUTH_ERRORS else "permission_denied")
        if not isinstance(receipt, dict) or not isinstance(receipt.get("scope_key"), str) or not 0 < len(receipt["scope_key"]) <= 512:
            _fail("permission_denied")
        key = _digest(receipt["scope_key"])
        if expected is not None and key != expected:
            _fail("source_authorization_changed")
        return key

    def _document(self):
        try:
            value = self._describe_callback()
        except Exception as error:
            code = getattr(error, "code", getattr(error, "reason", "source_unavailable"))
            _fail(code if code in AUTH_ERRORS else "source_unavailable")
        if not isinstance(value, dict) or not isinstance(value.get("source_alias"), str) or not ALIAS.fullmatch(value["source_alias"]) or not isinstance(value.get("sha256"), str) or not HASH.fullmatch(value["sha256"]):
            _fail("source_unavailable")
        result = {key: value[key] for key in ("source_alias", "sha256")}
        for key, default in (("binding_status", "not_verified"), ("rules_status", "not_reviewed"),
                             ("mapping_status", "unknown"), ("source_version", value["sha256"])):
            text = value.get(key, default)
            if text is None:
                text = default
            if not isinstance(text, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", text):
                _fail("schema_changed")
            result[key] = text
        return result

    def _current(self, key, document):
        self._authorize(key)
        if self._document() != document:
            _fail("stale_input")
        self._authorize(key)

    def _source(self, key, document):
        self._current(key, document)
        try:
            body = self._read_callback(document["source_alias"])
        except Exception as error:
            code = getattr(error, "code", getattr(error, "reason", "source_unavailable"))
            _fail(code if code in AUTH_ERRORS else "source_unavailable")
        self._current(key, document)
        body = _pdf(body)
        if sha256(body).hexdigest() != document["sha256"]:
            _fail("stale_input")
        return body

    def _state(self, key):
        path = self.root / "scopes" / (key + ".json")
        if not path.exists() and not path.is_symlink():
            return None
        result = self._open("state", path)
        if (not isinstance(result, dict) or result.get("schema") != SCHEMA or result.get("authorization_scope") != key
                or not isinstance(result.get("index_id"), str) or not HASH.fullmatch(result["index_id"])):
            _fail("stale_input")
        self._file(self.root / "versions" / (result["index_id"] + ".json"))
        return result

    def _load(self, key, document):
        state = self._state(key)
        if state is None:
            _fail("evidence_not_prepared")
        if (state.get("document") != document or state.get("extraction_version") != self._extraction_version or
                state.get("chunking_version") != CHUNKING):
            _fail("stale_input")
        bundle = self._open("bundle", self.root / "versions" / (state["index_id"] + ".json"))
        if (not isinstance(bundle, dict) or _digest(bundle) != state["index_id"] or
                bundle.get("authorization_scope") != key or bundle.get("document") != document or
                bundle.get("schema") != SCHEMA or bundle.get("chunking_version") != CHUNKING or
                bundle.get("extraction_version") != self._extraction_version):
            _fail("stale_input")
        try:
            pages = _pages([row["text"] for row in bundle["pages"]])
            if pages != bundle["pages"] or _chunks(pages) != bundle["chunks"]:
                _fail("stale_input")
        except (KeyError, TypeError):
            _fail("stale_input")
        return state, bundle

    @staticmethod
    def _summary(key, document, state=None, *, stale=False):
        ready = bool(state) and not stale
        return {"status": "ready_for_local_search" if ready else "stale_input" if stale else "not_prepared",
            "ready": ready, "scope": {"purpose": PURPOSE, "source_alias": document["source_alias"],
                "authorization_scope": key, "binding_status": document["binding_status"],
                "rules_status": document["rules_status"], "mapping_status": document["mapping_status"]},
            "coverage": deepcopy(state["coverage"]) if ready else {},
            "versions": {"source_sha256": document["sha256"], "source_version": document["source_version"],
                "corpus_version": state["index_id"] if ready else None, "index_version": CHUNKING,
                "extraction_version": state["extraction_version"] if ready else None},
            "warnings": list(WARNINGS), "review_status": "unreviewed", "answer_support_verified": False,
            "model_used": False, "official_rule_enabled": False}

    @_safe_storage
    def describe(self):
        """Metadata status only: no PDF/page body read, extraction, school or network."""
        key, document = self._authorize(), self._document()
        with self._locked():
            state = self._state(key)
            stale = state is not None and (state.get("document") != document or state.get("extraction_version") != self._extraction_version or state.get("chunking_version") != CHUNKING)
        self._current(key, document)
        return self._summary(key, document, state, stale=stale)

    @_safe_storage
    def prepare(self, source_alias):
        key, document = self._authorize(), self._document()
        if not isinstance(source_alias, str) or source_alias != document["source_alias"]:
            _fail("inaccessible_evidence")
        body = self._source(key, document)
        with self._locked():
            state = self._state(key)
            if (state and state.get("document") == document and state.get("extraction_version") == self._extraction_version and
                    state.get("chunking_version") == CHUNKING):
                state, _ = self._load(key, document)
                self._current(key, document)
                return {**self._summary(key, document, state), "reused": True}
        try:
            pages = _pages(self._extractor(body))
        except PersonalEvidenceError:
            raise
        except Exception:
            _fail("extraction_failed")
        chunks = _chunks(pages)
        bundle = {"schema": SCHEMA, "authorization_scope": key, "document": document,
            "chunking_version": CHUNKING, "extraction_version": self._extraction_version,
            "pages": pages, "chunks": chunks}
        index_id = _digest(bundle)
        coverage = {"physical_pages": len(pages), "pages_with_text": sum(bool(row["text"].strip()) for row in pages),
            "empty_pages": [row["page"] for row in pages if not row["text"].strip()], "chunk_count": len(chunks),
            "scope": "extractable_text_only", "layout_verified": False, "cross_page_context_verified": False,
            "human_reviewed_pages": 0, "complete_for_answer": False}
        state = {"schema": SCHEMA, "authorization_scope": key, "document": document, "index_id": index_id,
            "extraction_version": self._extraction_version, "chunking_version": CHUNKING, "coverage": coverage}
        self._source(key, document)  # Detect byte changes during extraction as well as metadata changes.
        with self._locked():
            self._current(key, document)
            path = self.root / "versions" / (index_id + ".json")
            if path.exists() or path.is_symlink():
                if self._open("bundle", path) != bundle:
                    _fail("stale_input")
            else:
                self._write_new(path, self._envelope("bundle", bundle))
            target = self.root / "scopes" / (key + ".json")
            if target.exists() or target.is_symlink():
                self._file(target)
            temporary = self.root / "scopes" / (".pending-" + secrets.token_hex(12))
            try:
                self._write_new(temporary, self._envelope("state", state))
                self._current(key, document)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        self._current(key, document)
        return {**self._summary(key, document, state), "reused": False}

    def _reference(self, key, state, chunk):
        payload = {"authorization_scope": key, "corpus_version": state["index_id"], "chunk_id": chunk["chunk_id"], "purpose": PURPOSE}
        raw = bytes.fromhex(self._sign("reference", payload))
        return "PERSONAL-REF-" + base64.urlsafe_b64encode(raw).decode().rstrip("=")

    def _view(self, key, state, bundle, chunk):
        contexts = [{"page": row["page"], "text": row["text"], "page_text_sha256": row["page_text_sha256"],
            "review_status": "unreviewed", "context_role": "full_page_unverified" if row["page"] == chunk["page"] else "adjacent_page_unverified"}
            for row in bundle["pages"] if row["page"] in chunk["required_context_pages"]]
        by_id = {row["chunk_id"]: row for row in bundle["chunks"]}
        model_context, used, omitted = [], 0, 0
        for dependency in chunk["semantic_context"]:
            other = by_id[dependency["chunk_id"]]
            amount = len(other["model_text"])
            if len(model_context) >= MAX_MODEL_CONTEXT_ITEMS or used + amount > MAX_MODEL_CONTEXT_CHARS:
                omitted += 1
                continue
            model_context.append({key_: deepcopy(other[key_]) for key_ in
                ("text", "model_text", "model_text_spans", "page", "locator", "review_status")} | {
                "context_role": dependency["context_role"], "context_completeness": "unverified"})
            used += amount
        return {key_: deepcopy(chunk[key_]) for key_ in ("title", "text", "model_text", "model_text_spans", "page", "locator", "text_sha256", "review_status", "context_completeness", "chunk_kind", "semantic_role", "module_title", "module_path", "cleaning_version")} | {
            "reference": self._reference(key, state, chunk), "required_context": contexts,
            "model_context": model_context, "model_context_chars": used,
            "model_context_omitted": omitted, "model_context_complete": omitted == 0,
            "source_alias": state["document"]["source_alias"], "answer_support_verified": False,
            "course_identity_verified": False, "official_rule_enabled": False}

    @_safe_storage
    def search(self, query, limit=5):
        if not isinstance(query, str) or not 0 < len(query.strip()) <= 400 or type(limit) is not int or not 1 <= limit <= 10:
            _fail("invalid_query")
        key, document = self._authorize(), self._document()
        with self._locked():
            state, bundle = self._load(key, document)
        self._source(key, document)
        tokens, codes, ranked, candidates = terms(query), _query_codes(query), [], []
        compact_query = re.sub(r"\s+", "", unicodedata.normalize("NFKC", query))
        module_matches = {}
        if not codes:
            for chunk in bundle["chunks"]:
                if chunk["semantic_role"] != "module_requirement" or not chunk["module_keys"]:
                    continue
                label = re.sub(r"(?:课程|类)$", "", chunk["module_title"] or "")
                if len(label) >= 2 and label in compact_query:
                    module_matches[chunk["module_keys"][-1]] = len(label)
            # A named child module outranks its generic parent (专业 vs 专业选修).
            if module_matches:
                longest = max(module_matches.values())
                module_matches = {key: size for key, size in module_matches.items() if size == longest}
        for ordinal, chunk in enumerate(bundle["chunks"]):
            exact = bool(codes & set(chunk["course_codes"]))
            if codes and not exact:
                continue
            if module_matches and not set(chunk["module_keys"]) & set(module_matches):
                continue
            score = len(tokens & terms(chunk["model_text"])) + (1000 if exact else 0)
            if module_matches:
                score += 50
                if chunk["semantic_role"] == "module_requirement":
                    score += 30
            candidates.append({"id": str(ordinal), "text": chunk["model_text"], "lexical_score": score})
            if score:
                ranked.append((-score, ordinal, chunk))
        ranked.sort(key=lambda row: row[:2])
        semantic = {"status": "not_used", "reason": "exact_course_code_route", "dense_used": False}
        if not codes:
            retrieval = self._semantic_retriever.rank(query, candidates, limit)
            semantic = {name: retrieval[name] for name in (
                "status", "reason", "dense_used", "candidate_count", "encoded_count",
                "candidate_truncated_count", "text_truncated_count", "token_truncated_count",
                "ranking_version", "dense_reserved_count")}
            if retrieval["dense_used"]:
                semantic["encoder"] = {name: retrieval[name] for name in ("model_id", "revision", "dimension", "artifact_sha256")}
                ranked = [(-(row["fusion_score"]), int(row["id"]), bundle["chunks"][int(row["id"])])
                          for row in retrieval["matches"]]
            # Failed or timed-out inference also takes time: a source/identity
            # change must not revive old lexical citations via the fallback.
            self._source(key, document)
        matches = [{**self._view(key, state, bundle, chunk), "score": -score} for score, _, chunk in ranked[:limit]]
        self._current(key, document)
        return {**self._summary(key, document, state), "status": "pending_review" if matches else "no_supported_answer_in_scope",
            "matches": matches, "retrieval_mode": "exact_course_code_text" if codes else (
                "hybrid_rrf_local_embedding" if semantic["dense_used"] else "lexical_han_bigram"),
            "semantic_retrieval": semantic,
            "search_candidate_count": len(candidates) if semantic["dense_used"] else len(ranked),
            "named_module_filter_applied": bool(module_matches)}

    @_safe_storage
    def resolve(self, reference):
        if not isinstance(reference, str) or not REFERENCE.fullmatch(reference):
            _fail("inaccessible_evidence")
        key, document = self._authorize(), self._document()
        with self._locked():
            state, bundle = self._load(key, document)
        self._source(key, document)
        chunk = next((row for row in bundle["chunks"] if hmac.compare_digest(reference, self._reference(key, state, row))), None)
        if chunk is None:
            _fail("inaccessible_evidence")
        result = self._view(key, state, bundle, chunk)
        self._current(key, document)
        return {**self._summary(key, document, state), **result, "status": "pending_review"}
