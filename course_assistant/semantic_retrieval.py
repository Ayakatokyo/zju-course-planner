"""Optional, local-only BGE retrieval; evidence authorization stays with the caller.

The application itself needs only the standard library. An explicitly configured
Python interpreter runs this same file as a bounded ONNX worker. No text is sent
to a model service, persisted, or treated as an identity/rule decision here.
"""
from hashlib import sha256
from collections import OrderedDict
from copy import deepcopy
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import threading


MODEL_ID = "BAAI/bge-small-zh-v1.5"
MODEL_REVISION = "7999e1d3359715c523056ef9478215996d62a620"
SOURCE_WEIGHTS_SHA256 = "354763b9b1357bc9c44f62c6be2276321081ed2567773608c0d0785b61d5a026"
TOKENIZER_SHA256 = "48cea5d44424912a6fd1ea647bf4fe50b55ab8b1e5879c3275f80e339e8fae26"
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："
MANIFEST_SCHEMA = "local-bge-onnx/1"
RANKING_VERSION = "rrf60-dual-quota-v2"
DIMENSION = 512
MAX_CANDIDATES = 2000
MAX_TEXT_CHARS = 1800
MAX_QUERY_CHARS = 400
MAX_INPUT_BYTES = 12 * 1024 * 1024
MAX_OUTPUT_BYTES = 24 * 1024 * 1024
MAX_SEQUENCE_LENGTH = 512
BATCH_SIZE = 16
HASH = re.compile(r"[0-9a-f]{64}\Z")
SAFE_ID = re.compile(r"[^\s\x00-\x1f]{1,200}\Z")


class _Unavailable(Exception):
    pass


def _digest(path):
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _manifest(path, *, verify_files=True):
    """Only the fixed, locally exported official model is supported."""
    try:
        path = Path(path).resolve(strict=True)
        if not path.is_file() or path.stat().st_size > 16384:
            raise _Unavailable("manifest_invalid")
        with path.open("rb") as stream:
            raw = stream.read(16385)
        if len(raw) > 16384:
            raise _Unavailable("manifest_invalid")
        data = json.loads(raw)
        if (not isinstance(data, dict) or data.get("schema_version") != MANIFEST_SCHEMA
                or data.get("model_id") != MODEL_ID or data.get("revision") != MODEL_REVISION
                or data.get("source_weights_sha256") != SOURCE_WEIGHTS_SHA256
                or data.get("dimension") != DIMENSION or data.get("pooling") != "cls_l2"
                or data.get("query_prefix") != QUERY_PREFIX
                or data.get("max_sequence_length") != MAX_SEQUENCE_LENGTH):
            raise _Unavailable("manifest_invalid")
        runtime = data.get("runtime")
        if (not isinstance(runtime, dict) or set(runtime) != {"onnxruntime", "tokenizers", "numpy"}
                or not all(isinstance(value, str) and re.fullmatch(r"[0-9][0-9A-Za-z.+-]{0,39}", value)
                           for value in runtime.values())):
            raise _Unavailable("manifest_invalid")
        artifacts = data.get("artifacts")
        if not isinstance(artifacts, dict) or set(artifacts) != {"model", "tokenizer"}:
            raise _Unavailable("manifest_invalid")
        resolved = {}
        for key, maximum in (("model", 256 * 1024 * 1024), ("tokenizer", 2 * 1024 * 1024)):
            value = artifacts[key]
            if (not isinstance(value, dict) or set(value) != {"file", "sha256"}
                    or not isinstance(value["file"], str) or not value["file"]
                    or Path(value["file"]).is_absolute() or ".." in Path(value["file"]).parts
                    or not isinstance(value["sha256"], str) or not HASH.fullmatch(value["sha256"])
                    or (key == "tokenizer" and value["sha256"] != TOKENIZER_SHA256)):
                raise _Unavailable("manifest_invalid")
            artifact = (path.parent / value["file"]).resolve(strict=True)
            if (not artifact.is_relative_to(path.parent) or not artifact.is_file()
                    or not 0 < artifact.stat().st_size <= maximum):
                raise _Unavailable("artifact_invalid")
            if verify_files and _digest(artifact) != value["sha256"]:
                raise _Unavailable("artifact_hash_mismatch")
            resolved[key] = artifact
        return data, resolved, sha256(raw).hexdigest()
    except _Unavailable:
        raise
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        raise _Unavailable("manifest_unavailable") from None


def _lexical(candidates, limit):
    rows = [row for row in candidates if row["lexical_score"] > 0]
    rows.sort(key=lambda row: (-row["lexical_score"], row["ordinal"]))
    return [{"id": row["id"], "lexical_score": row["lexical_score"],
             "dense_score": None, "fusion_score": None} for row in rows[:limit]]


def _identity(value):
    return {key: value[key] for key in ("manifest_sha256", "artifact_sha256", "revision")}


class LocalSemanticRetriever:
    """Rank already-admitted candidates without reading any evidence repository.

    Candidates are ``{id, text, lexical_score}``; zero-lexical candidates must be
    included for paraphrase recall. Exact-code/module filtering belongs upstream.
    Similarity is a retrieval ordering, never proof of answer support.
    """

    def __init__(self, manifest_path=None, *, python_path=None, timeout_seconds=45):
        self.manifest_path = str(manifest_path) if manifest_path else None
        self.python_path = str(python_path or sys.executable)
        if type(timeout_seconds) not in (int, float) or not 1 <= timeout_seconds <= 60:
            raise ValueError("invalid_semantic_timeout")
        self.timeout_seconds = timeout_seconds
        # Pure ranking results only, bounded to this retriever instance. Personal
        # evidence owns one instance per authenticated generation and still
        # validates fresh source bytes and authorization before/after ranking.
        self._rank_cache = OrderedDict()
        self._document_vectors = OrderedDict()
        self._rank_cache_lock = threading.Lock()

    @classmethod
    def from_environment(cls, environ=None):
        environ = os.environ if environ is None else environ
        return cls(environ.get("COURSE_SEMANTIC_ENCODER_MANIFEST"),
                   python_path=environ.get("COURSE_SEMANTIC_PYTHON"))

    def status(self):
        if not self.manifest_path:
            return {"status": "not_configured", "reason": "encoder_not_configured"}
        try:
            data, _, manifest_hash = _manifest(self.manifest_path)
            if not Path(self.python_path).is_file() or not os.access(self.python_path, os.X_OK):
                raise _Unavailable("runtime_unavailable")
        except _Unavailable as error:
            return {"status": "unavailable", "reason": str(error)}
        return {"status": "available", "reason": None, "model_id": MODEL_ID,
                "revision": MODEL_REVISION, "dimension": DIMENSION,
                "artifact_sha256": data["artifacts"]["model"]["sha256"], "manifest_sha256": manifest_hash}

    def _run_encoder(self, texts, *, expected_identity=None):
        expected_identity = _identity(expected_identity or self.status())
        try:
            request = json.dumps({"texts": texts, "expected_identity": expected_identity}, ensure_ascii=False).encode("utf-8")
        except (UnicodeError, ValueError):
            raise _Unavailable("encoder_input_invalid") from None
        if len(request) > MAX_INPUT_BYTES:
            raise _Unavailable("encoder_input_limit")
        try:
            process = subprocess.run(
                [self.python_path, "-I", str(Path(__file__).resolve()), "--worker", self.manifest_path],
                input=request, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=self.timeout_seconds, check=False,
                env={"TOKENIZERS_PARALLELISM": "false", "HF_HUB_OFFLINE": "1",
                     "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
                     "OMP_NUM_THREADS": "2"})
        except subprocess.TimeoutExpired:
            raise _Unavailable("encoder_timeout") from None
        except (OSError, ValueError):
            raise _Unavailable("runtime_unavailable") from None
        if len(process.stdout) > MAX_OUTPUT_BYTES:
            raise _Unavailable("encoder_output_invalid")
        try:
            output = json.loads(process.stdout)
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise _Unavailable("encoder_output_invalid") from None
        if not isinstance(output, dict):
            raise _Unavailable("encoder_output_invalid")
        if process.returncode or output.get("status") != "available":
            reason = output.get("reason") if isinstance(output, dict) else None
            allowed = {"runtime_dependencies_missing", "runtime_version_mismatch", "artifact_hash_mismatch",
                       "manifest_invalid", "manifest_unavailable", "artifact_invalid", "encoder_model_invalid",
                       "encoder_failed", "encoder_input_invalid", "encoder_identity_changed"}
            raise _Unavailable(reason if reason in allowed else "encoder_failed")
        if output.get("identity") != expected_identity:
            raise _Unavailable("encoder_identity_changed")
        return output

    def rank(self, query, candidates, limit=5):
        if (not isinstance(query, str) or not 0 < len(query.strip()) <= MAX_QUERY_CHARS
                or type(limit) is not int or not 1 <= limit <= 10
                or not isinstance(candidates, (list, tuple)) or len(candidates) > 10000):
            raise ValueError("invalid_semantic_query")
        prepared, seen, text_truncated = [], set(), 0
        # Validate the complete list before dropping over-budget candidates.
        for ordinal, row in enumerate(candidates):
            if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                    or not SAFE_ID.fullmatch(row["id"]) or row["id"] in seen
                    or not isinstance(row.get("text"), str) or not row["text"].strip()
                    or type(row.get("lexical_score")) not in (int, float)
                    or not 0 <= row["lexical_score"] <= 1000000000
                    or not math.isfinite(row["lexical_score"])):
                raise ValueError("invalid_semantic_candidate")
            seen.add(row["id"])
            prepared.append({"id": row["id"], "text": row["text"][:MAX_TEXT_CHARS],
                             "lexical_score": row["lexical_score"], "ordinal": ordinal})
            text_truncated += int(len(row["text"]) > MAX_TEXT_CHARS)
        status = self.status()
        result = {**status, "dense_used": False, "retrieval_mode": "lexical_han_bigram",
                  "matches": _lexical(prepared, limit), "candidate_count": len(prepared), "encoded_count": 0,
                  "candidate_truncated_count": max(0, len(prepared) - MAX_CANDIDATES),
                  "text_truncated_count": text_truncated, "token_truncated_count": 0,
                  "ranking_version": RANKING_VERSION, "dense_reserved_count": 0}
        if status["status"] != "available" or not prepared:
            return result
        # An over-budget pool must not silently displace a lexical match beyond
        # the cut. Keep complete lexical results and report that dense did not run.
        if len(prepared) > MAX_CANDIDATES:
            result.update(status="unavailable", reason="encoder_candidate_limit")
            return result
        selected = prepared
        cache_key = sha256(json.dumps([_identity(status), RANKING_VERSION, query.strip(), candidates, limit],
            ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with self._rank_cache_lock:
            cached = self._rank_cache.get(cache_key)
            if cached is not None:
                self._rank_cache.move_to_end(cache_key)
                return {**deepcopy(cached), "encoded_count": 0, "query_encoded_count": 0,
                        "document_cache_hit": True, "cache_hit": True}
        document_key = sha256(json.dumps([_identity(status), [row["text"] for row in selected]],
            ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        with self._rank_cache_lock:
            document_cached = self._document_vectors.get(document_key)
            document_vectors = document_cached[0] if document_cached is not None else None
            if document_vectors is not None:
                self._document_vectors.move_to_end(document_key)
        try:
            texts = [QUERY_PREFIX + query.strip()] + ([] if document_vectors is not None else [row["text"] for row in selected])
            output = self._run_encoder(texts,
                                       expected_identity=status)
            if isinstance(output, dict) and output.get("identity") != _identity(status):
                raise _Unavailable("encoder_identity_changed")
            vectors = output.get("vectors") if isinstance(output, dict) else None
            if (not isinstance(vectors, list) or len(vectors) != len(texts)
                    or any(not isinstance(row, list) or len(row) != DIMENSION
                           or any(type(value) not in (int, float) or not math.isfinite(value) for value in row)
                           or abs(sum(value * value for value in row) - 1.0) > 0.02 for row in vectors)):
                raise _Unavailable("encoder_output_invalid")
            truncated = output.get("token_truncated_count")
            if type(truncated) is not int or not 0 <= truncated <= len(vectors):
                raise _Unavailable("encoder_output_invalid")
            truncated_indices = output.get("token_truncated_indices", [] if not truncated else None)
            if truncated_indices is not None and (not isinstance(truncated_indices, list)
                    or any(type(i) is not int or not 0 <= i < len(vectors) for i in truncated_indices)
                    or len(set(truncated_indices)) != truncated or len(truncated_indices) != truncated):
                raise _Unavailable("encoder_output_invalid")
            if document_vectors is not None:
                vectors = vectors + list(document_vectors)
                truncated += document_cached[1]
            elif truncated_indices is not None:
                # No persistence or cross-instance sharing. Source admission
                # remains upstream; the encoder identity and all input text are
                # bound into this key. Carry document truncation counts forward
                # even though only the new query needs encoding on a warm call.
                with self._rank_cache_lock:
                    self._document_vectors[document_key] = (tuple(tuple(v) for v in vectors[1:]),
                                                           sum(i > 0 for i in truncated_indices))
                    while len(self._document_vectors) > 2:
                        self._document_vectors.popitem(last=False)
            dense = {row["id"]: sum(left * right for left, right in zip(vectors[0], vector))
                     for row, vector in zip(selected, vectors[1:])}
            dense_order = sorted(selected, key=lambda row: (-dense[row["id"]], row["ordinal"]))
            lexical_order = sorted((row for row in selected if row["lexical_score"] > 0),
                                   key=lambda row: (-row["lexical_score"], row["ordinal"]))
            scores = {row["id"]: 1 / (60 + rank) for rank, row in enumerate(dense_order, 1)}
            for rank, row in enumerate(lexical_order, 1):
                scores[row["id"]] += 1 / (60 + rank)
            ordered = sorted(selected, key=lambda row: (-scores[row["id"]], -dense[row["id"]], row["ordinal"]))
            # RRF's second vote can otherwise remove every dense-only passage
            # from a short result list. Reserve a bounded dense-recall quota;
            # this is not a similarity threshold or evidence-quality decision.
            reserved = {row["id"] for row in dense_order[:max(1, (limit + 2) // 3)]}
            chosen = set(reserved)
            # Retain the strongest literal evidence as well. Generic dense
            # neighbours must not displace an explicit clause/name match.
            lexical_reserved = {lexical_order[0]["id"]} if lexical_order and limit >= 2 else set()
            chosen.update(lexical_reserved)
            for row in ordered:
                if len(chosen) >= limit:
                    break
                chosen.add(row["id"])
            ordered = [row for row in ordered if row["id"] in chosen]
            result.update(dense_used=True, retrieval_mode="hybrid_rrf_local_embedding",
                          encoded_count=0 if document_vectors is not None else len(selected), query_encoded_count=1,
                          document_cache_hit=document_vectors is not None,
                          token_truncated_count=truncated, dense_reserved_count=len(reserved),
                          lexical_reserved_count=len(lexical_reserved),
                          matches=[{"id": row["id"], "lexical_score": row["lexical_score"],
                                    "dense_score": dense[row["id"]], "fusion_score": scores[row["id"]]}
                                   for row in ordered[:limit]])
            with self._rank_cache_lock:
                self._rank_cache[cache_key] = deepcopy(result)
                self._rank_cache.move_to_end(cache_key)
                while len(self._rank_cache) > 8:
                    self._rank_cache.popitem(last=False)
        except _Unavailable as error:
            result.update(status="unavailable", reason=str(error))
        return result


def _encode_worker(manifest_path, request):
    data, artifacts, manifest_hash = _manifest(manifest_path)
    identity = {"manifest_sha256": manifest_hash, "artifact_sha256": data["artifacts"]["model"]["sha256"],
                "revision": data["revision"]}
    if not isinstance(request, dict) or request.get("expected_identity") != identity:
        raise _Unavailable("encoder_identity_changed")
    try:
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer
    except ImportError:
        raise _Unavailable("runtime_dependencies_missing") from None
    if any(importlib.metadata.version(name) != version for name, version in data["runtime"].items()):
        raise _Unavailable("runtime_version_mismatch")
    ort.disable_telemetry_events()
    texts = request.get("texts") if isinstance(request, dict) else None
    if (not isinstance(texts, list) or not 1 <= len(texts) <= MAX_CANDIDATES + 1
            or any(not isinstance(text, str) or not 0 < len(text) <= MAX_TEXT_CHARS for text in texts)
            or not texts[0].startswith(QUERY_PREFIX)):
        raise _Unavailable("encoder_input_invalid")
    # Load exactly the bytes checked for this request, closing file-replacement
    # races between verification and the native runtime's model/tokenizer load.
    bodies = {}
    for name, maximum in (("model", 256 * 1024 * 1024), ("tokenizer", 2 * 1024 * 1024)):
        with artifacts[name].open("rb") as stream:
            body = stream.read(maximum + 1)
        if len(body) > maximum or sha256(body).hexdigest() != data["artifacts"][name]["sha256"]:
            raise _Unavailable("artifact_hash_mismatch")
        bodies[name] = body
    tokenizer = Tokenizer.from_str(bodies.pop("tokenizer").decode("utf-8"))
    tokenizer.enable_truncation(max_length=MAX_SEQUENCE_LENGTH)
    tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.enable_profiling = False
    session = ort.InferenceSession(bodies.pop("model"), sess_options=options,
                                   providers=["CPUExecutionProvider"])
    if ({value.name for value in session.get_inputs()} != {"input_ids", "attention_mask", "token_type_ids"}
            or len(session.get_outputs()) != 1):
        raise _Unavailable("encoder_model_invalid")
    vectors, token_truncated, truncated_indices = [], 0, []
    for start in range(0, len(texts), BATCH_SIZE):
        batch = tokenizer.encode_batch(texts[start:start + BATCH_SIZE])
        token_truncated += sum(bool(row.overflowing) for row in batch)
        truncated_indices.extend(start + i for i, row in enumerate(batch) if row.overflowing)
        inputs = {"input_ids": np.asarray([row.ids for row in batch], dtype=np.int64),
                  "attention_mask": np.asarray([row.attention_mask for row in batch], dtype=np.int64),
                  "token_type_ids": np.asarray([row.type_ids for row in batch], dtype=np.int64)}
        encoded = session.run(None, inputs)[0]
        if encoded.shape != (len(batch), DIMENSION) or not np.isfinite(encoded).all():
            raise _Unavailable("encoder_model_invalid")
        norms = np.linalg.norm(encoded, axis=1, keepdims=True)
        if (norms < 1e-8).any():
            raise _Unavailable("encoder_model_invalid")
        vectors.extend((encoded / norms).tolist())
    return {"status": "available", "identity": identity, "vectors": vectors,
            "token_truncated_count": token_truncated, "token_truncated_indices": truncated_indices}


def _worker_main():
    try:
        payload = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(payload) > MAX_INPUT_BYTES:
            raise _Unavailable("encoder_input_invalid")
        output = _encode_worker(sys.argv[2], json.loads(payload))
        code = 0
    except _Unavailable as error:
        output, code = {"status": "unavailable", "reason": str(error)}, 1
    except Exception:
        # Dependency/model messages may contain source text or local paths.
        output, code = {"status": "unavailable", "reason": "encoder_failed"}, 1
    sys.stdout.write(json.dumps(output, separators=(",", ":"), allow_nan=False))
    return code


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--worker":
        raise SystemExit(2)
    raise SystemExit(_worker_main())
