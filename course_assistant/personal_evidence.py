"""Owner-scoped local PDF evidence, without school, model or executable rules.

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

SCHEMA = "personal-evidence/1"
CHUNKING = "page-paragraph-context-v2"
EXTRACTION = "pdftotext-layout-utf8-v1"
PURPOSE = "personal_curriculum_local_reference"
MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_TEXT_BYTES = 4 * 1024 * 1024
MAX_STORE_BYTES = 16 * 1024 * 1024
MAX_PAGES = 300
MAX_PAGE_CHARS = 60000
MAX_CHUNKS = 2000
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
    "未调用模型；检索命中不等于答案支持、已获学分或推荐就绪。"]


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


def _chunks(pages):
    chunks = []
    for page in pages:
        text, start, position = page["text"], None, 0
        spans = []
        for line in text.splitlines(keepends=True):
            if line.strip():
                if start is None:
                    start = position
            elif start is not None:
                spans.append((start, position))
                start = None
            position += len(line)
        if start is not None:
            spans.append((start, len(text)))
        for left, right in spans:
            # Long fragments retain full page context and are never called complete clauses.
            cursor = left
            while cursor < right:
                end = min(cursor + 4000, right)
                excerpt = text[cursor:end]
                if excerpt.strip():
                    locator = {"physical_page": page["page"], "char_start": cursor, "char_end": end,
                        "char_unit": "unicode_codepoint", "page_text_sha256": page["page_text_sha256"]}
                    row = {"page": page["page"], "title": excerpt.strip().splitlines()[0][:120],
                        "text": excerpt, "text_sha256": sha256(excerpt.encode()).hexdigest(),
                        "locator": locator, "review_status": "unreviewed", "context_completeness": "unverified",
                        "chunk_kind": "page_paragraph" if right - left <= 4000 else "partial_fragment",
                        "course_codes": sorted(_codes(excerpt)),
                        "required_context_pages": list(range(max(1, page["page"] - 1), min(len(pages), page["page"] + 1) + 1))}
                    row["chunk_id"] = "PCH-" + _digest(row)
                    chunks.append(row)
                    if len(chunks) > MAX_CHUNKS:
                        _fail("extraction_limit_exceeded")
                if end == right:
                    break
                cursor = end - 160
    return chunks


class PersonalEvidenceService:
    """A private registry bound to the current callback authorization generation."""

    def __init__(self, root, authorize, read_document, describe_document, extractor=None):
        if not all(callable(fn) for fn in (authorize, read_document, describe_document)) or (extractor is not None and not callable(extractor)):
            _fail("invalid_arguments")
        self._authorize_callback, self._read_callback = authorize, read_document
        self._describe_callback, self._extractor = describe_document, extractor or _extract_pdf
        self._extraction_version = EXTRACTION if extractor is None else "injected-page-extractor-v1"
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
        return {key_: deepcopy(chunk[key_]) for key_ in ("title", "text", "page", "locator", "text_sha256", "review_status", "context_completeness", "chunk_kind")} | {
            "reference": self._reference(key, state, chunk), "required_context": contexts,
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
        tokens, codes, ranked = terms(query), _query_codes(query), []
        for ordinal, chunk in enumerate(bundle["chunks"]):
            exact = bool(codes & set(chunk["course_codes"]))
            if codes and not exact:
                continue
            score = len(tokens & terms(chunk["text"])) + (1000 if exact else 0)
            if score:
                ranked.append((-score, ordinal, chunk))
        ranked.sort(key=lambda row: row[:2])
        matches = [{**self._view(key, state, bundle, chunk), "score": -score} for score, _, chunk in ranked[:limit]]
        self._current(key, document)
        return {**self._summary(key, document, state), "status": "pending_review" if matches else "no_supported_answer_in_scope",
            "matches": matches, "retrieval_mode": "exact_course_code_text" if codes else "lexical_han_bigram",
            "search_candidate_count": len(ranked)}

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
