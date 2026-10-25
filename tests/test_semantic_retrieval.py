"""Local encoder contracts. Test vectors below are labeled doubles, not embeddings.

Real model tests require both explicit COURSE_SEMANTIC_* settings. They never
download dependencies or models and use only synthetic Chinese passages.
"""
from copy import deepcopy
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from course_assistant import semantic_retrieval as semantic


def unit_vector(first, second=0):
    norm = math.hypot(first, second)
    return [first / norm, second / norm] + [0.0] * (semantic.DIMENSION - 2)


def synthetic_manifest(model_hash):
    return {"schema_version": semantic.MANIFEST_SCHEMA, "model_id": semantic.MODEL_ID,
            "revision": semantic.MODEL_REVISION, "source_weights_sha256": semantic.SOURCE_WEIGHTS_SHA256,
            "dimension": semantic.DIMENSION, "pooling": "cls_l2", "query_prefix": semantic.QUERY_PREFIX,
            "max_sequence_length": semantic.MAX_SEQUENCE_LENGTH,
            "runtime": {"onnxruntime": "1.23.1", "tokenizers": "0.22.1", "numpy": "2.5.4"},
            "artifacts": {"model": {"file": "synthetic-model.onnx", "sha256": model_hash},
                          "tokenizer": {"file": "synthetic-tokenizer.json", "sha256": semantic.TOKENIZER_SHA256}}}


class SemanticRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.candidates = [{"id": "SYNTHETIC-A", "text": "合成体育要求", "lexical_score": 0},
                           {"id": "SYNTHETIC-B", "text": "合成数学要求", "lexical_score": 3},
                           {"id": "SYNTHETIC-C", "text": "合成考试安排", "lexical_score": 3}]
        self.retriever = semantic.LocalSemanticRetriever()

    def available(self):
        return patch.object(self.retriever, "status", return_value={
            "status": "available", "reason": None, "model_id": semantic.MODEL_ID,
            "revision": semantic.MODEL_REVISION, "dimension": semantic.DIMENSION,
            "artifact_sha256": "a" * 64, "manifest_sha256": "b" * 64})

    def vectors(self):
        return {"status": "available", "token_truncated_count": 0,
                "identity": {"artifact_sha256": "a" * 64, "manifest_sha256": "b" * 64, "revision": semantic.MODEL_REVISION},
                "vectors": [unit_vector(1), unit_vector(1), unit_vector(0, 1), unit_vector(-1)]}

    def test_no_configuration_keeps_complete_stable_lexical_order(self):
        original = deepcopy(self.candidates)
        with patch.object(self.retriever, "_run_encoder") as run:
            result = self.retriever.rank("合成问题", self.candidates)
        self.assertEqual(result["status"], "not_configured")
        self.assertFalse(result["dense_used"])
        self.assertEqual([row["id"] for row in result["matches"]], ["SYNTHETIC-B", "SYNTHETIC-C"])
        self.assertEqual(self.candidates, original)
        run.assert_not_called()

    def test_identical_derived_ranking_reuses_work_without_sharing_mutable_results(self):
        with self.available(), patch.object(self.retriever, "_run_encoder", return_value=self.vectors()) as run:
            first = self.retriever.rank("合成问题", self.candidates, 3)
            expected = deepcopy(first["matches"])
            first["matches"].clear()
            second = self.retriever.rank("合成问题", self.candidates, 3)
            self.assertEqual(second["matches"], expected)
            self.assertTrue(second["cache_hit"])
            self.assertEqual(second["encoded_count"], 0)
            run.assert_called_once()
            changed = deepcopy(self.candidates)
            changed[0]["text"] += "变更"
            self.retriever.rank("合成问题", changed, 3)
            self.retriever.rank("另一个问题", self.candidates, 3)
            self.retriever.rank("合成问题", self.candidates, 2)
            self.assertEqual(run.call_count, 4)

    def test_warm_ranking_rechecks_encoder_identity_and_availability(self):
        with self.available(), patch.object(self.retriever, "_run_encoder", return_value=self.vectors()):
            self.retriever.rank("合成问题", self.candidates)
        with patch.object(self.retriever, "status", return_value={"status":"unavailable", "reason":"changed"}), patch.object(self.retriever, "_run_encoder") as run:
            result = self.retriever.rank("合成问题", self.candidates)
            self.assertFalse(result["dense_used"])
            self.assertNotIn("cache_hit", result)
            run.assert_not_called()
        with self.available() as status, patch.object(self.retriever, "_run_encoder", side_effect=semantic._Unavailable("changed")) as run:
            status.return_value["manifest_sha256"] = "c" * 64
            result = self.retriever.rank("合成问题", self.candidates)
            self.assertFalse(result["dense_used"])
            run.assert_called_once()

    def test_ranking_cache_is_instance_local_and_bounded(self):
        with self.available(), patch.object(self.retriever, "_run_encoder", side_effect=lambda texts, **kw: {**self.vectors(), "vectors": [unit_vector(1)] * len(texts)}) as run:
            for index in range(9):
                self.retriever.rank(f"合成问题{index}", self.candidates)
            self.retriever.rank("合成问题0", self.candidates)
            self.assertEqual(run.call_count, 10)
        self.assertFalse(semantic.LocalSemanticRetriever()._rank_cache)

    def test_changed_query_reuses_documents_but_changed_source_or_encoder_does_not(self):
        def encode(texts, **kwargs):
            return {**self.vectors(), "vectors": [unit_vector(1)] * len(texts)}
        with self.available(), patch.object(self.retriever, "_run_encoder", side_effect=encode) as run:
            cold = self.retriever.rank("合成问题", self.candidates)
            warm = self.retriever.rank("另一个问题", self.candidates)
            self.assertEqual(cold['encoded_count'], 3)
            self.assertTrue(warm['dense_used'])
            self.assertEqual(warm['encoded_count'], 0)
            self.assertEqual(warm['query_encoded_count'], 1)
            self.assertEqual(len(run.call_args.args[0]), 1)
            changed = deepcopy(self.candidates)
            changed[0]['text'] += '修改'
            self.assertEqual(self.retriever.rank('另一个问题', changed)['encoded_count'], 3)
            for i in range(3):
                changed[0]['text'] += str(i)
                self.retriever.rank('另一个问题', changed)
            self.assertLessEqual(len(self.retriever._document_vectors), 2)
        self.assertFalse(semantic.LocalSemanticRetriever()._document_vectors)

    def test_document_truncation_remains_visible_when_only_query_is_encoded(self):
        def encode(texts, **kwargs):
            indices = [1] if len(texts) > 1 else []
            return {**self.vectors(), "vectors": [unit_vector(1)] * len(texts),
                    "token_truncated_count": len(indices), "token_truncated_indices": indices}
        with self.available(), patch.object(self.retriever, "_run_encoder", side_effect=encode):
            first = self.retriever.rank('问题一', self.candidates)
            second = self.retriever.rank('问题二', self.candidates)
            self.assertTrue(second['dense_used'])
            self.assertEqual(second['encoded_count'], 0)
            self.assertEqual(first['token_truncated_count'], 1)
            self.assertEqual(second['token_truncated_count'], 1)

    def test_only_explicit_application_environment_configures_encoder(self):
        env = {"HF_HOME": "/SYNTHETIC", "OPENAI_BASE_URL": "https://invalid.example"}
        self.assertEqual(semantic.LocalSemanticRetriever.from_environment(env).status()["status"], "not_configured")
        configured = semantic.LocalSemanticRetriever.from_environment({
            "COURSE_SEMANTIC_ENCODER_MANIFEST": "/SYNTHETIC/manifest.json",
            "COURSE_SEMANTIC_PYTHON": "/SYNTHETIC/python"})
        self.assertEqual(configured.manifest_path, "/SYNTHETIC/manifest.json")
        self.assertEqual(configured.python_path, "/SYNTHETIC/python")

    def test_remote_manifest_is_not_fetched_or_used(self):
        retriever = semantic.LocalSemanticRetriever("https://invalid.example/model.json")
        with patch.object(retriever, "_run_encoder") as run:
            result = retriever.rank("合成问题", self.candidates)
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse(result["dense_used"])
        self.assertNotIn("invalid.example", json.dumps(result))
        run.assert_not_called()

    def test_model_revision_hash_and_artifact_path_are_admission_requirements(self):
        with tempfile.TemporaryDirectory(prefix="synthetic-encoder-") as directory:
            root = Path(directory)
            weights = b"SYNTHETIC-NOT-A-MODEL"
            (root / "synthetic-model.onnx").write_bytes(weights)
            (root / "synthetic-tokenizer.json").write_bytes(b"SYNTHETIC-NOT-A-TOKENIZER")
            manifest = synthetic_manifest(sha256(weights).hexdigest())
            path = root / "manifest.json"
            cases = []
            changed = deepcopy(manifest)
            changed["revision"] = "0" * 40
            cases.append((changed, "manifest_invalid"))
            changed = deepcopy(manifest)
            changed["source_weights_sha256"] = "0" * 64
            cases.append((changed, "manifest_invalid"))
            changed = deepcopy(manifest)
            changed["artifacts"]["model"]["file"] = "../outside.onnx"
            cases.append((changed, "manifest_invalid"))
            changed = deepcopy(manifest)
            changed["artifacts"]["model"]["sha256"] = "0" * 64
            cases.append((changed, "artifact_hash_mismatch"))
            changed = deepcopy(manifest)
            changed["artifacts"]["tokenizer"]["sha256"] = "0" * 64
            cases.append((changed, "manifest_invalid"))
            cases.append((manifest, "artifact_hash_mismatch"))
            for value, reason in cases:
                with self.subTest(reason=reason):
                    path.write_text(json.dumps(value))
                    result = semantic.LocalSemanticRetriever(path).rank("合成问题", self.candidates)
                    self.assertEqual(result["reason"], reason)
                    self.assertFalse(result["dense_used"])
                    self.assertEqual(len(result["matches"]), 2)
                    self.assertNotIn(directory, json.dumps(result))

    def test_hybrid_receives_zero_lexical_candidates_and_uses_both_rankings(self):
        with self.available(), patch.object(self.retriever, "_run_encoder", return_value=self.vectors()) as run:
            result = self.retriever.rank("改写问题", self.candidates, limit=3)
        self.assertTrue(result["dense_used"])
        self.assertEqual(result["retrieval_mode"], "hybrid_rrf_local_embedding")
        sent = run.call_args.args[0]
        self.assertEqual(sent[0], semantic.QUERY_PREFIX + "改写问题")
        self.assertEqual(sent[1:], [row["text"] for row in self.candidates])
        self.assertEqual(result["encoded_count"], 3)
        by_id = {row["id"]: row for row in result["matches"]}
        self.assertEqual(by_id["SYNTHETIC-A"]["dense_score"], 1)
        self.assertAlmostEqual(by_id["SYNTHETIC-B"]["fusion_score"], 1 / 61 + 1 / 62)
        self.assertEqual(by_id["SYNTHETIC-A"]["lexical_score"], 0)
        self.assertNotIn("vectors", result)
        self.assertNotIn("合成体育", json.dumps(result, ensure_ascii=False))

    def test_zero_lexical_pool_has_dense_recall_instead_of_empty_answer(self):
        for row in self.candidates:
            row["lexical_score"] = 0
        self.assertEqual(self.retriever.rank("改写问题", self.candidates)["matches"], [])
        with self.available(), patch.object(self.retriever, "_run_encoder", return_value=self.vectors()):
            result = self.retriever.rank("改写问题", self.candidates)
        self.assertEqual(result["matches"][0]["id"], "SYNTHETIC-A")

    def test_dense_quota_keeps_paraphrase_when_weak_lexical_noise_has_two_votes(self):
        rows = [{"id": "SYNTHETIC-CORRECT", "text": "正确语义片段", "lexical_score": 0}]
        rows += [{"id": f"SYNTHETIC-NOISE-{i}", "text": "弱词法噪声", "lexical_score": 1} for i in range(5)]
        output = self.vectors()
        output["vectors"] = [unit_vector(1), unit_vector(1)] + [unit_vector(-1)] * 5
        with self.available(), patch.object(self.retriever, "_run_encoder", return_value=output):
            result = self.retriever.rank("合成改写", rows, limit=5)
        self.assertTrue(result["dense_used"])
        self.assertEqual(len(result["matches"]), 5)
        self.assertIn("SYNTHETIC-CORRECT", [row["id"] for row in result["matches"]])
        self.assertEqual(result["dense_reserved_count"], 2)
        self.assertEqual(result["ranking_version"], semantic.RANKING_VERSION)

    def test_literal_clause_survives_generic_dense_neighbours(self):
        rows = [{'id': 'CLAUSE', 'text': '明确核心要求', 'lexical_score': 9}]
        rows += [{'id': str(i), 'text': '泛化相似文字', 'lexical_score': 1} for i in range(8)]
        output = self.vectors()
        output['vectors'] = [unit_vector(1), unit_vector(-1)] + [unit_vector(1)] * 8
        with self.available(), patch.object(self.retriever, '_run_encoder', return_value=output):
            result = self.retriever.rank('核心要求', rows, limit=5)
        self.assertIn('CLAUSE', [row['id'] for row in result['matches']])
        self.assertEqual(result['lexical_reserved_count'], 1)
        self.assertEqual(len(result['matches']), 5)

    def test_invalid_queries_candidates_and_nonfinite_scores_do_not_run_encoder(self):
        for query, rows, limit in [("", self.candidates, 5), ("问" * 401, self.candidates, 5),
                                   ("问", self.candidates, True), ("问", self.candidates, 11),
                                   ("问", self.candidates * 2, 5),
                                   ("问", [{"id": "x", "text": "合成", "lexical_score": float("nan")}], 5),
                                   ("问", [{"id": "x", "text": "", "lexical_score": 1}], 5)]:
            with self.subTest(query=query[:10], limit=limit), patch.object(self.retriever, "_run_encoder") as run:
                with self.assertRaises(ValueError):
                    self.retriever.rank(query, rows, limit)
                run.assert_not_called()

    def test_candidate_limit_preserves_late_lexical_match_and_skips_dense(self):
        rows = [{"id": str(i), "text": "合成片段", "lexical_score": 0}
                for i in range(semantic.MAX_CANDIDATES + 1)]
        rows[-1]["lexical_score"] = 9
        with self.available(), patch.object(self.retriever, "_run_encoder") as run:
            result = self.retriever.rank("合成问题", rows)
        self.assertEqual(result["reason"], "encoder_candidate_limit")
        self.assertEqual(result["candidate_truncated_count"], 1)
        self.assertEqual(result["matches"][0]["id"], str(semantic.MAX_CANDIDATES))
        self.assertFalse(result["dense_used"])
        run.assert_not_called()

    def test_character_and_token_truncation_are_reported(self):
        self.candidates[0]["text"] = "合" * (semantic.MAX_TEXT_CHARS + 10)
        output = self.vectors()
        output["token_truncated_count"] = 1
        with self.available(), patch.object(self.retriever, "_run_encoder", return_value=output) as run:
            result = self.retriever.rank("合成问题", self.candidates)
        self.assertEqual(len(run.call_args.args[0][1]), semantic.MAX_TEXT_CHARS)
        self.assertEqual(result["text_truncated_count"], 1)
        self.assertEqual(result["token_truncated_count"], 1)

    def test_invalid_vector_shape_norm_or_nan_never_reports_dense_success(self):
        for vector in ([0.0] * semantic.DIMENSION, [float("nan")] * semantic.DIMENSION,
                       [1.0], [True] + [0.0] * (semantic.DIMENSION - 1)):
            output = self.vectors()
            output["vectors"][1] = vector
            with self.subTest(size=len(vector)), self.available(), patch.object(
                    self.retriever, "_run_encoder", return_value=output):
                result = self.retriever.rank("合成问题", self.candidates)
            self.assertFalse(result["dense_used"])
            self.assertEqual(result["reason"], "encoder_output_invalid")
            self.assertEqual([row["id"] for row in result["matches"]], ["SYNTHETIC-B", "SYNTHETIC-C"])

    def test_worker_receives_only_pipe_text_and_no_inherited_credentials(self):
        self.retriever.manifest_path = "/SYNTHETIC/manifest.json"
        process = subprocess.CompletedProcess([], 0, json.dumps(self.vectors()).encode())
        with self.available(), patch.dict(os.environ, {"DEEPSEEK_API_KEY": "SYNTHETIC-SECRET"}), patch.object(
                semantic.subprocess, "run", return_value=process) as run:
            result = self.retriever.rank("合成私有问题", self.candidates)
        self.assertTrue(result["dense_used"])
        args, kwargs = run.call_args
        self.assertNotIn("合成私有问题", " ".join(args[0]))
        self.assertNotIn("DEEPSEEK_API_KEY", kwargs["env"])
        self.assertEqual(kwargs["env"]["HF_HUB_OFFLINE"], "1")
        self.assertIn("-I", args[0])
        self.assertFalse(kwargs.get("shell", False))
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)

    def test_worker_failures_and_malformed_json_keep_lexical_with_safe_reason(self):
        self.retriever.manifest_path = "/SYNTHETIC/manifest.json"
        cases = [(subprocess.CompletedProcess([], 1, b'{"status":"unavailable","reason":"runtime_dependencies_missing"}'),
                  "runtime_dependencies_missing"),
                 (subprocess.CompletedProcess([], 1, b'{"status":"unavailable","reason":"SYNTHETIC-PRIVATE-PATH"}'),
                  "encoder_failed"),
                 (subprocess.CompletedProcess([], 0, b'[]'), "encoder_output_invalid"),
                 (subprocess.CompletedProcess([], 0, b'not json'), "encoder_output_invalid")]
        for process, reason in cases:
            with self.subTest(reason=reason), self.available(), patch.object(
                    semantic.subprocess, "run", return_value=process):
                result = self.retriever.rank("合成问题", self.candidates)
            self.assertFalse(result["dense_used"])
            self.assertEqual(result["reason"], reason)
            self.assertEqual(len(result["matches"]), 2)
            self.assertNotIn("SYNTHETIC-PRIVATE-PATH", json.dumps(result))
        with self.available(), patch.object(semantic.subprocess, "run", side_effect=subprocess.TimeoutExpired("fixed", 1)):
            result = self.retriever.rank("合成问题", self.candidates)
        self.assertEqual(result["reason"], "encoder_timeout")
        self.assertFalse(result["dense_used"])

    def test_changed_worker_identity_is_rejected_even_with_valid_vectors(self):
        self.retriever.manifest_path = "/SYNTHETIC/manifest.json"
        output = self.vectors()
        output["identity"]["artifact_sha256"] = "c" * 64
        process = subprocess.CompletedProcess([], 0, json.dumps(output).encode())
        with self.available(), patch.object(semantic.subprocess, "run", return_value=process) as run:
            result = self.retriever.rank("合成问题", self.candidates)
        request = json.loads(run.call_args.kwargs["input"])
        self.assertEqual(request["expected_identity"]["manifest_sha256"], "b" * 64)
        self.assertFalse(result["dense_used"])
        self.assertEqual(result["reason"], "encoder_identity_changed")

    def test_pipe_input_and_output_byte_limits_fail_without_dense_success(self):
        self.retriever.manifest_path = "/SYNTHETIC/manifest.json"
        with self.available(), patch.object(semantic, "MAX_INPUT_BYTES", 8), patch.object(
                semantic.subprocess, "run") as run:
            limited = self.retriever.rank("合成问题", self.candidates)
        run.assert_not_called()
        self.assertEqual(limited["reason"], "encoder_input_limit")
        self.assertFalse(limited["dense_used"])
        process = subprocess.CompletedProcess([], 0, b"x" * 9)
        with self.available(), patch.object(semantic, "MAX_OUTPUT_BYTES", 8), patch.object(
                semantic.subprocess, "run", return_value=process):
            limited = self.retriever.rank("合成问题", self.candidates)
        self.assertEqual(limited["reason"], "encoder_output_invalid")
        self.assertFalse(limited["dense_used"])


@unittest.skipUnless(os.environ.get("COURSE_SEMANTIC_ENCODER_MANIFEST") and os.environ.get("COURSE_SEMANTIC_PYTHON"),
                     "explicit local encoder configuration required; no download in tests")
class ActualLocalEncoderTests(unittest.TestCase):
    def test_actual_encoder_vectors_and_chinese_paraphrase_recall(self):
        retriever = semantic.LocalSemanticRetriever.from_environment()
        rows = [{"id": "SYNTHETIC-PE", "text": "体育课程修读要求：学生每学年应修读一门体育课程。", "lexical_score": 0},
                {"id": "SYNTHETIC-MATH", "text": "高等数学课程介绍微积分与线性代数的计算。", "lexical_score": 0},
                {"id": "SYNTHETIC-EXAM", "text": "考试安排包含考试日期、教室和考试时间。", "lexical_score": 0}]
        result = retriever.rank("运动课一年要上几次", rows, limit=3)
        self.assertTrue(result["dense_used"], result)
        self.assertEqual(result["matches"][0]["id"], "SYNTHETIC-PE")
        actual = retriever._run_encoder([semantic.QUERY_PREFIX + "运动课一年要上几次", rows[0]["text"]])
        self.assertEqual(len(actual["vectors"]), 2)
        for vector in actual["vectors"]:
            self.assertEqual(len(vector), semantic.DIMENSION)
            self.assertAlmostEqual(sum(value * value for value in vector), 1, places=5)

    def test_actual_encoder_mixed_weak_lexical_noise_preserves_dense_recall(self):
        from course_assistant.reference_retrieval import terms
        retriever = semantic.LocalSemanticRetriever.from_environment()
        query = "练身体的课每年都得修吗"
        texts = ["体育课程修读要求：学生每学年应修读一门体育课程。",
                 "每年举行图书馆藏书盘点，核对书目数量。", "每年组织花展，介绍园艺植物品种。",
                 "每年对校园建筑进行设施保养。", "每年开展食堂餐饮卫生检查。", "每年登记学校邮局的包裹处理数量。"]
        rows = [{"id": f"SYNTHETIC-{i}", "text": text, "lexical_score": len(terms(query) & terms(text))}
                for i, text in enumerate(texts)]
        lexical = semantic.LocalSemanticRetriever().rank(query, rows, limit=5)
        self.assertNotIn("SYNTHETIC-0", [row["id"] for row in lexical["matches"]])
        hybrid = retriever.rank(query, rows, limit=5)
        self.assertTrue(hybrid["dense_used"], hybrid)
        self.assertIn("SYNTHETIC-0", [row["id"] for row in hybrid["matches"]])
        correct = next(row for row in hybrid["matches"] if row["id"] == "SYNTHETIC-0")
        self.assertEqual(correct["lexical_score"], 0)
        self.assertGreater(correct["dense_score"], max(row["dense_score"] for row in hybrid["matches"] if row["id"] != "SYNTHETIC-0"))

    def test_actual_encoder_token_budget_and_runtime_version_mismatch(self):
        retriever = semantic.LocalSemanticRetriever.from_environment()
        rows = [{"id": "SYNTHETIC-LONG", "text": "体育" * 900, "lexical_score": 2}]
        result = retriever.rank("运动课程", rows)
        self.assertTrue(result["dense_used"], result)
        self.assertEqual(result["token_truncated_count"], 1)
        warm = retriever.rank("每年运动课", rows)
        self.assertTrue(warm['dense_used'])
        self.assertTrue(warm['document_cache_hit'])
        self.assertEqual(warm['encoded_count'], 0)
        self.assertEqual(warm['token_truncated_count'], 1)
        path = Path(retriever.manifest_path)
        manifest = json.loads(path.read_text())
        manifest["runtime"]["numpy"] = "0.0.0"
        # Sibling manifest keeps the same explicit artifact root. The original
        # weights, tokenizer and manifest are never changed.
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", prefix="synthetic-version-",
                                         dir=path.parent, encoding="utf-8") as fixture:
            json.dump(manifest, fixture)
            fixture.flush()
            mismatch = semantic.LocalSemanticRetriever(fixture.name, python_path=retriever.python_path)
            failed = mismatch.rank("运动课程", rows)
        self.assertFalse(failed["dense_used"])
        self.assertEqual(failed["reason"], "runtime_version_mismatch")
        self.assertEqual(failed["matches"][0]["id"], "SYNTHETIC-LONG")

    def test_actual_worker_rejects_manifest_change_since_parent_verification(self):
        retriever = semantic.LocalSemanticRetriever.from_environment()
        path = Path(retriever.manifest_path)
        manifest = json.loads(path.read_text())
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".json", prefix="synthetic-race-",
                                         dir=path.parent, encoding="utf-8") as fixture:
            json.dump(manifest, fixture)
            fixture.flush()
            local = semantic.LocalSemanticRetriever(fixture.name, python_path=retriever.python_path)
            previous = local.status()
            manifest["synthetic_test_note"] = "manifest changed after parent verification"
            fixture.seek(0)
            json.dump(manifest, fixture)
            fixture.truncate()
            fixture.flush()
            with self.assertRaises(semantic._Unavailable) as caught:
                local._run_encoder([semantic.QUERY_PREFIX + "运动课", "体育修读要求"], expected_identity=previous)
        self.assertEqual(str(caught.exception), "encoder_identity_changed")


if __name__ == "__main__":
    unittest.main()
