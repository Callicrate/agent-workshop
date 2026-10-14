from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
CASES_PATH = SKILL_ROOT / "assets" / "routing-cases.json"
SCRIPT_PATH = SCRIPTS / "routing_probe.py"
FULL_SKILLS_ROOT_ENVIRONMENT_VARIABLE = "AGENTS_FULL_SKILLS_ROOT"
EXPECTED_FULL_SKILL_CATALOG = frozenset(
    {
        "agent-deployment-sync",
        "agent-prompt-engineering",
        "agents-md",
        "api-documentation-author",
        "beepboop-userscript-development",
        "consult",
        "critically-review",
        "databricks-api-calls",
        "databricks-asset-bundles",
        "databricks-batch-inference",
        "databricks-deploy-monitor",
        "databricks-ml-training",
        "databricks-model-serving",
        "databricks-project-status",
        "databricks-runtime-doctor",
        "databricks-spark-etl",
        "elasticsearch-queries",
        "extract-lessons-learned",
        "frontend-product-ui",
        "graphiti-memory",
        "local-project-execution",
        "documentation-author",
        "mlflow-run-auditor",
        "personal-prose",
        "python-debugging",
        "skill-author",
        "spark-diagnostics",
        "tend",
        "user-preferences",
    }
)

sys.path.insert(0, str(SCRIPTS))

import routing_probe  # noqa: E402


def digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


class RoutingProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        source_corpus = json.loads(CASES_PATH.read_text(encoding="utf-8"))
        referenced_skills = sorted(
            {
                skill
                for case in source_corpus["cases"]
                for skill in case["must_select"] + case["must_not_select"]
            }
        )
        cls._discovery_root_temporary_directory = tempfile.TemporaryDirectory()
        cls.discovery_root = (
            Path(cls._discovery_root_temporary_directory.name) / "skills"
        )
        for skill in referenced_skills:
            cls.write_skill(
                cls.discovery_root,
                skill,
                f"Use when testing {skill}. Do not trigger for unrelated work.",
            )
        cls.corpus, cls.cases, cls.corpus_hash = routing_probe.load_cases(
            CASES_PATH, cls.discovery_root
        )
        cls.known_skills = routing_probe._known_skills(cls.discovery_root)
        cls.full_canonical_skills_root, cls.full_canonical_skills_root_reason = (
            cls.configured_full_catalog_root()
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._discovery_root_temporary_directory.cleanup()

    @classmethod
    def configured_full_catalog_root(cls) -> tuple[Path | None, str]:
        configured = os.environ.get(FULL_SKILLS_ROOT_ENVIRONMENT_VARIABLE)
        if not configured:
            return None, f"{FULL_SKILLS_ROOT_ENVIRONMENT_VARIABLE} is unset"
        root = Path(configured)
        if not root.is_absolute() or not root.is_dir():
            return (
                None,
                f"{FULL_SKILLS_ROOT_ENVIRONMENT_VARIABLE} is not an absolute existing root",
            )
        resolved_root = root.resolve()
        if resolved_root == cls.discovery_root.resolve():
            return (
                None,
                f"{FULL_SKILLS_ROOT_ENVIRONMENT_VARIABLE} points to the synthetic discovery root",
            )
        try:
            known_skills = routing_probe._known_skills(resolved_root)
            routing_probe.discovery_snapshot_hash(resolved_root)
        except routing_probe.ContractError:
            return (
                None,
                f"{FULL_SKILLS_ROOT_ENVIRONMENT_VARIABLE} is not a valid discovery root",
            )
        if known_skills != EXPECTED_FULL_SKILL_CATALOG:
            return (
                None,
                f"{FULL_SKILLS_ROOT_ENVIRONMENT_VARIABLE} does not match the complete catalog",
            )
        return resolved_root, ""

    def observation(
        self, *, selected_overrides: dict[str, list[str]] | None = None
    ) -> dict[str, object]:
        selected_overrides = selected_overrides or {}
        trials = []
        for trial_id, case_id in routing_probe._opaque_trial_ids(self.cases).items():
            case = self.cases[case_id]
            trials.append(
                {
                    "trial_id": trial_id,
                    "selected_skills": sorted(
                        selected_overrides.get(case_id, case["must_select"])
                    ),
                    "transcript_sha256": digest(f"transcript:{trial_id}"),
                    "uncertainty": "none",
                }
            )
        return {
            "schema_version": routing_probe.OBSERVATION_SCHEMA,
            "corpus_sha256": self.corpus_hash,
            "full_discovery_sha256": digest("full-discovery-a"),
            "non_target_discovery_sha256": digest("non-target-a"),
            "changed_skills": ["skill-author"],
            "skill_revision": "routing-probe-v1",
            "client": {"name": "Codex CLI", "version": "1.0", "surface": "cli"},
            "model": {"provider": "openai", "id": "model-a", "reasoning": "high"},
            "context": {
                "mode": "fresh",
                "freshness": "fresh",
                "path_visibility": "visible",
                "instruction_profile": "unknown",
            },
            "explicit_invocation": False,
            "evidence_method": "external-fresh-context-capture",
            "trials": trials,
        }

    def validated_observation(self, raw: dict[str, object]) -> dict[str, object]:
        return routing_probe.validate_observation(
            raw,
            cases=self.cases,
            corpus_hash=self.corpus_hash,
            known_skills=self.known_skills,
        )

    def materialize_template(self, template: dict[str, object]) -> dict[str, object]:
        observation = copy.deepcopy(template)
        observation["client"] = {
            "name": "Codex CLI",
            "version": "1.0",
            "surface": "cli",
        }
        observation["model"] = {
            "provider": "openai",
            "id": "model-a",
            "reasoning": "high",
        }
        for trial in observation["trials"]:
            trial["transcript_sha256"] = digest(f"template:{trial['trial_id']}")
        return observation

    @staticmethod
    def write_skill(root: Path, name: str, description: str) -> None:
        skill_dir = root / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            f"name: {name}\n"
            f"description: {json.dumps(description)}\n"
            "metadata:\n"
            "  short-description: Test skill.\n"
            "---\n",
            encoding="utf-8",
        )

    def test_corpus_is_small_synthetic_and_covers_each_fragile_group(self) -> None:
        self.assertEqual(self.corpus["schema_version"], routing_probe.CASES_SCHEMA)
        self.assertEqual(len(self.corpus["cases"]), 15)
        self.assertLessEqual(len(self.corpus["cases"]), routing_probe.MAX_CASES)
        groups: dict[str, set[str]] = {}
        for case in self.corpus["cases"]:
            self.assertNotIn("$", case["prompt"])
            groups.setdefault(case["group"], set()).add(case["kind"])
        self.assertEqual(set(groups), routing_probe.REQUIRED_GROUPS)
        self.assertTrue(
            all(kinds == routing_probe.ALLOWED_CASE_KINDS for kinds in groups.values())
        )

    def test_corpus_covers_the_architected_cases_with_the_intended_oracles(
        self,
    ) -> None:
        expected = {
            "murmuration-read-context": (
                "consult-tend",
                "positive",
                ["consult"],
                ["tend"],
            ),
            "murmuration-participate": (
                "consult-tend",
                "adjacent-negative",
                ["tend"],
                ["consult"],
            ),
            "murmuration-local-only": (
                "consult-tend",
                "boundary",
                [],
                ["consult", "tend"],
            ),
            "python-traceback": (
                "python-debugging-databricks-runtime-doctor",
                "positive",
                ["python-debugging"],
                ["databricks-runtime-doctor"],
            ),
            "databricks-cuda-runtime": (
                "python-debugging-databricks-runtime-doctor",
                "adjacent-negative",
                ["databricks-runtime-doctor"],
                ["python-debugging"],
            ),
            "python-databricks-boundary": (
                "python-debugging-databricks-runtime-doctor",
                "boundary",
                [],
                ["databricks-runtime-doctor", "python-debugging"],
            ),
            "skill-resource-update": (
                "skill-author-agents-md-make-documentation",
                "positive",
                ["skill-author"],
                ["agents-md", "documentation-author"],
            ),
            "agents-guidance-update": (
                "skill-author-agents-md-make-documentation",
                "adjacent-negative",
                ["agents-md"],
                ["documentation-author", "skill-author"],
            ),
            "readme-documentation-update": (
                "skill-author-agents-md-make-documentation",
                "boundary",
                ["documentation-author"],
                ["agents-md", "skill-author"],
            ),
            "graphiti-durable-convention": (
                "graphiti-durable-transient",
                "positive",
                ["graphiti-memory"],
                [],
            ),
            "graphiti-transient-status": (
                "graphiti-durable-transient",
                "adjacent-negative",
                [],
                ["graphiti-memory"],
            ),
            "graphiti-canonical-boundary": (
                "graphiti-durable-transient",
                "boundary",
                [],
                ["graphiti-memory"],
            ),
            "spark-execution-oom": (
                "spark-diagnostics-databricks-spark-etl",
                "positive",
                ["spark-diagnostics"],
                ["databricks-spark-etl"],
            ),
            "spark-etl-scd2-write": (
                "spark-diagnostics-databricks-spark-etl",
                "adjacent-negative",
                ["databricks-spark-etl"],
                ["spark-diagnostics"],
            ),
            "spark-etl-model-boundary": (
                "spark-diagnostics-databricks-spark-etl",
                "boundary",
                [],
                ["databricks-spark-etl", "spark-diagnostics"],
            ),
        }
        actual = {
            case["id"]: (
                case["group"],
                case["kind"],
                case["must_select"],
                case["must_not_select"],
            )
            for case in self.corpus["cases"]
        }

        self.assertEqual(actual, expected)

    def test_corpus_hash_uses_canonical_json_not_source_key_order(self) -> None:
        reordered = {
            key: copy.deepcopy(self.corpus[key]) for key in reversed(tuple(self.corpus))
        }

        self.assertEqual(
            routing_probe.sha256_json(self.corpus), routing_probe.sha256_json(reordered)
        )

    def test_live_full_catalog_requires_explicit_authorized_root(self) -> None:
        if self.full_canonical_skills_root is None:
            self.skipTest(self.full_canonical_skills_root_reason)

        corpus, cases, corpus_hash = routing_probe.load_cases(
            CASES_PATH, self.full_canonical_skills_root
        )

        self.assertEqual(corpus, self.corpus)
        self.assertEqual(cases, self.cases)
        self.assertEqual(corpus_hash, self.corpus_hash)

    def test_full_catalog_authorization_never_infers_from_synthetic_or_unset_roots(
        self,
    ) -> None:
        original = os.environ.pop(FULL_SKILLS_ROOT_ENVIRONMENT_VARIABLE, None)
        try:
            root, reason = self.configured_full_catalog_root()
            self.assertIsNone(root)
            self.assertIn("unset", reason)

            os.environ[FULL_SKILLS_ROOT_ENVIRONMENT_VARIABLE] = str(
                self.discovery_root.resolve()
            )
            root, reason = self.configured_full_catalog_root()
            self.assertIsNone(root)
            self.assertIn("synthetic", reason)

            with tempfile.TemporaryDirectory() as directory:
                partial_root = Path(directory) / "partial-skills"
                for skill in sorted(self.known_skills):
                    self.write_skill(
                        partial_root,
                        skill,
                        f"Use when testing {skill}. Do not trigger for unrelated work.",
                    )
                os.environ[FULL_SKILLS_ROOT_ENVIRONMENT_VARIABLE] = str(
                    partial_root.resolve()
                )
                root, reason = self.configured_full_catalog_root()
                self.assertIsNone(root)
                self.assertIn("complete catalog", reason)
        finally:
            if original is None:
                os.environ.pop(FULL_SKILLS_ROOT_ENVIRONMENT_VARIABLE, None)
            else:
                os.environ[FULL_SKILLS_ROOT_ENVIRONMENT_VARIABLE] = original

    def test_plan_hides_oracle_and_emits_only_trials_and_non_oracle_metadata(
        self,
    ) -> None:
        plan = routing_probe.make_plan(self.cases)

        self.assertEqual(plan["schema_version"], routing_probe.PLAN_SCHEMA)
        self.assertEqual(plan["trial_count"], 15)
        self.assertEqual(len(plan["trials"]), 15)
        self.assertEqual(set(plan), {"schema_version", "trial_count", "trials"})
        self.assertNotIn("must_select", json.dumps(plan, sort_keys=True))
        self.assertNotIn("must_not_select", json.dumps(plan, sort_keys=True))
        self.assertTrue(
            all(set(trial) == {"trial_id", "prompt"} for trial in plan["trials"])
        )
        self.assertEqual(
            [trial["trial_id"] for trial in plan["trials"]],
            [f"trial-{index:03d}" for index in range(1, 16)],
        )
        rendered = json.dumps(plan, sort_keys=True)
        self.assertNotIn("corpus_sha256", rendered)
        self.assertNotIn("skill_revision", rendered)
        self.assertNotIn(self.corpus_hash, rendered)
        self.assertNotIn(self.corpus["skill_revision"], rendered)
        for case in self.corpus["cases"]:
            self.assertNotIn(case["id"], rendered)
            self.assertNotIn(case["group"], rendered)
            self.assertNotIn(case["kind"], rendered)

    def test_template_binds_canonical_hashes_and_materializes_to_an_accepted_observation(
        self,
    ) -> None:
        template = routing_probe.make_observation_template(
            self.cases,
            corpus_hash=self.corpus_hash,
            skills_root=self.discovery_root,
            changed_skills=["skill-author"],
            skill_revision="routing-probe-v1",
        )
        observation = self.materialize_template(template)

        self.assertEqual(template["corpus_sha256"], self.corpus_hash)
        self.assertEqual(template["changed_skills"], ["skill-author"])
        self.assertEqual(
            [trial["trial_id"] for trial in template["trials"]],
            [f"trial-{index:03d}" for index in range(1, 16)],
        )
        self.assertTrue(
            all(trial["uncertainty"] == "unknown" for trial in template["trials"])
        )
        self.assertEqual(
            self.validated_observation(observation)["full_discovery_sha256"],
            template["full_discovery_sha256"],
        )

    def test_template_is_not_scoreable_until_every_placeholder_is_replaced(
        self,
    ) -> None:
        template = routing_probe.make_observation_template(
            self.cases,
            corpus_hash=self.corpus_hash,
            skills_root=self.discovery_root,
            changed_skills=["skill-author"],
            skill_revision="routing-probe-v1",
        )
        partial = copy.deepcopy(template)
        for trial in partial["trials"]:
            trial["transcript_sha256"] = digest(f"partial:{trial['trial_id']}")

        with self.assertRaises(routing_probe.ContractError) as error:
            self.validated_observation(partial)
        self.assertEqual(error.exception.code, "unpopulated-template-placeholder")
        self.assertEqual(
            self.validated_observation(self.materialize_template(template))[
                "corpus_sha256"
            ],
            self.corpus_hash,
        )

    def test_every_documented_placeholder_is_rejected_in_an_observation(self) -> None:
        mutations = (
            ("client", lambda raw: raw["client"].update(name="<client>")),
            ("model", lambda raw: raw["model"].update(id="<model>")),
            (
                "context",
                lambda raw: raw["context"].update(
                    path_visibility="<visibility-or-unknown>"
                ),
            ),
            ("revision", lambda raw: raw.update(skill_revision="<revision-label>")),
            (
                "hash",
                lambda raw: raw.update(full_discovery_sha256="<64-lowercase-hex>"),
            ),
            (
                "evidence",
                lambda raw: raw.update(evidence_method="<evidence-method>"),
            ),
            (
                "changed-skill",
                lambda raw: raw.update(changed_skills=["<known-skill-name>"]),
            ),
            (
                "selection",
                lambda raw: raw["trials"][0].update(
                    selected_skills=["<observed-skill-name>"]
                ),
            ),
            (
                "uncertainty",
                lambda raw: raw["trials"][0].update(uncertainty="<uncertainty>"),
            ),
        )
        for label, mutation in mutations:
            with self.subTest(label=label):
                raw = self.observation()
                mutation(raw)
                with self.assertRaises(routing_probe.ContractError) as error:
                    self.validated_observation(raw)
                self.assertEqual(
                    error.exception.code, "unpopulated-template-placeholder"
                )

    def test_raw_asset_byte_hash_is_not_the_canonical_corpus_hash(self) -> None:
        raw_hash = hashlib.sha256(CASES_PATH.read_bytes()).hexdigest()
        raw = self.observation()
        raw["corpus_sha256"] = raw_hash

        self.assertNotEqual(raw_hash, self.corpus_hash)
        with self.assertRaises(routing_probe.ContractError) as error:
            self.validated_observation(raw)
        self.assertEqual(error.exception.code, "corpus-hash-mismatch")

    def test_discovery_snapshot_hashes_are_sorted_and_isolate_changed_targets(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            first = base / "first" / "skills"
            second = base / "second" / "skills"
            self.write_skill(
                first, "alpha", "Use when alpha. Do not trigger for other work."
            )
            self.write_skill(
                first, "beta", "Use when beta. Do not trigger for other work."
            )
            self.write_skill(
                second, "beta", "Use when beta. Do not trigger for other work."
            )
            self.write_skill(
                second, "alpha", "Use when alpha. Do not trigger for other work."
            )

            full_before = routing_probe.discovery_snapshot_hash(first)
            non_target_before = routing_probe.discovery_snapshot_hash(first, ["alpha"])
            self.assertEqual(full_before, routing_probe.discovery_snapshot_hash(second))
            self.assertEqual(
                non_target_before,
                routing_probe.discovery_snapshot_hash(second, ["alpha"]),
            )

            (first / "alpha").rename(first / "zeta")
            self.write_skill(
                first, "zeta", "Use when alpha. Do not trigger for other work."
            )
            full_path_drift = routing_probe.discovery_snapshot_hash(first)
            non_target_path_drift = routing_probe.discovery_snapshot_hash(
                first, ["zeta"]
            )
            self.assertNotEqual(full_before, full_path_drift)
            self.assertEqual(non_target_before, non_target_path_drift)

            self.write_skill(
                first, "zeta", "Use when changed alpha. Do not trigger for other work."
            )
            full_description_drift = routing_probe.discovery_snapshot_hash(first)
            non_target_description_drift = routing_probe.discovery_snapshot_hash(
                first, ["zeta"]
            )
            self.assertNotEqual(full_path_drift, full_description_drift)
            self.assertEqual(non_target_path_drift, non_target_description_drift)

            self.write_skill(
                first, "beta", "Use when changed beta. Do not trigger for other work."
            )
            self.assertNotEqual(
                non_target_description_drift,
                routing_probe.discovery_snapshot_hash(first, ["zeta"]),
            )

    def test_case_validation_rejects_unknown_or_vacuous_or_overlapping_oracles(
        self,
    ) -> None:
        mutations = (
            (
                "unknown",
                lambda corpus: corpus["cases"][0].update(
                    must_select=["not-a-known-skill"]
                ),
            ),
            (
                "vacuous",
                lambda corpus: corpus["cases"][0].update(
                    must_select=[], must_not_select=[]
                ),
            ),
            (
                "overlap",
                lambda corpus: corpus["cases"][0].update(must_not_select=["consult"]),
            ),
        )
        for label, mutation in mutations:
            with self.subTest(label=label):
                corpus = copy.deepcopy(self.corpus)
                mutation(corpus)
                with self.assertRaises(routing_probe.ContractError):
                    routing_probe.validate_cases(corpus, self.discovery_root)

    def test_case_validation_rejects_duplicates_and_unsupported_groups(self) -> None:
        duplicate = copy.deepcopy(self.corpus)
        duplicate["cases"][1]["id"] = duplicate["cases"][0]["id"]
        with self.assertRaises(routing_probe.ContractError):
            routing_probe.validate_cases(duplicate, self.discovery_root)

        missing_kind = copy.deepcopy(self.corpus)
        missing_kind["cases"][2]["kind"] = "positive"
        with self.assertRaises(routing_probe.ContractError):
            routing_probe.validate_cases(missing_kind, self.discovery_root)

        wrong_group = copy.deepcopy(self.corpus)
        wrong_group["cases"][0]["group"] = "another-routing-group"
        with self.assertRaises(routing_probe.ContractError):
            routing_probe.validate_cases(wrong_group, self.discovery_root)

    def test_observation_binds_all_required_provenance_and_implicit_invocation(
        self,
    ) -> None:
        raw = self.observation()
        observation = self.validated_observation(raw)

        self.assertEqual(observation["corpus_sha256"], self.corpus_hash)
        self.assertEqual(observation["context"]["instruction_profile"], "unknown")
        self.assertEqual(observation["explicit_invocation"], False)

        raw["explicit_invocation"] = True
        with self.assertRaises(routing_probe.ContractError) as error:
            self.validated_observation(raw)
        self.assertEqual(error.exception.code, "explicit-invocation-not-allowed")

    def test_observation_enforces_sorted_unique_skill_lists_and_uncertainty_enum(
        self,
    ) -> None:
        mutations = (
            (
                "changed",
                lambda raw: raw.update(changed_skills=["skill-author", "agents-md"]),
            ),
            (
                "selected",
                lambda raw: raw["trials"][0].update(
                    selected_skills=["skill-author", "agents-md"]
                ),
            ),
            (
                "uncertainty",
                lambda raw: raw["trials"][0].update(uncertainty="uncertain"),
            ),
        )
        for label, mutation in mutations:
            with self.subTest(label=label):
                raw = self.observation()
                mutation(raw)
                with self.assertRaises(routing_probe.ContractError):
                    self.validated_observation(raw)

    def test_observation_rejects_unknown_selected_skill_and_hash_mismatch(self) -> None:
        raw = self.observation()
        raw["trials"][0]["selected_skills"] = ["not-a-known-skill"]
        with self.assertRaises(routing_probe.ContractError) as error:
            self.validated_observation(raw)
        self.assertEqual(error.exception.code, "unknown-selected-skill")

        raw = self.observation()
        raw["corpus_sha256"] = digest("wrong-corpus")
        with self.assertRaises(routing_probe.ContractError) as error:
            self.validated_observation(raw)
        self.assertEqual(error.exception.code, "corpus-hash-mismatch")

    def test_observation_rejects_duplicate_transcript_hashes(self) -> None:
        raw = self.observation()
        raw["trials"][1]["transcript_sha256"] = raw["trials"][0]["transcript_sha256"]

        with self.assertRaises(routing_probe.ContractError) as error:
            self.validated_observation(raw)

        self.assertEqual(error.exception.code, "duplicate-transcript-hash")

    def test_score_binds_the_canonical_observation_deterministically(self) -> None:
        baseline = self.validated_observation(self.observation())
        baseline_score = routing_probe.score_observation(self.cases, baseline)
        repeated_score = routing_probe.score_observation(
            self.cases, self.validated_observation(self.observation())
        )
        revision_raw = self.observation()
        revision_raw["skill_revision"] = "routing-probe-v2"
        revision_score = routing_probe.score_observation(
            self.cases, self.validated_observation(revision_raw)
        )
        context_raw = self.observation()
        context_raw["context"]["freshness"] = "stale"
        context_score = routing_probe.score_observation(
            self.cases, self.validated_observation(context_raw)
        )

        self.assertEqual(baseline_score, repeated_score)
        self.assertNotEqual(
            baseline_score["observation_sha256"], revision_score["observation_sha256"]
        )
        self.assertNotEqual(
            baseline_score["binding_hashes"]["skill_revision_sha256"],
            revision_score["binding_hashes"]["skill_revision_sha256"],
        )
        self.assertNotEqual(
            baseline_score["observation_sha256"], context_score["observation_sha256"]
        )
        self.assertNotEqual(
            baseline_score["binding_hashes"]["context_sha256"],
            context_score["binding_hashes"]["context_sha256"],
        )
        rendered = json.dumps(baseline_score, sort_keys=True)
        self.assertNotIn(baseline["skill_revision"], rendered)
        self.assertNotIn(json.dumps(baseline["changed_skills"]), rendered)

    def test_json_reader_rejects_oversize_and_deep_inputs_without_writes(self) -> None:
        payloads = {
            "oversize.json": b'{"padding":"'
            + b"x" * routing_probe.MAX_JSON_BYTES
            + b'"}',
            "deep.json": (
                "[" * (routing_probe.MAX_JSON_DEPTH + 1)
                + "0"
                + "]" * (routing_probe.MAX_JSON_DEPTH + 1)
            ).encode("utf-8"),
            "long-string.json": json.dumps(
                "x" * (routing_probe.MAX_JSON_STRING_CHARACTERS + 1)
            ).encode("utf-8"),
            "long-list.json": json.dumps(
                [0] * (routing_probe.MAX_JSON_LIST_ITEMS + 1)
            ).encode("utf-8"),
            "large-object.json": json.dumps(
                {
                    f"key-{index}": 0
                    for index in range(routing_probe.MAX_JSON_OBJECT_ITEMS + 1)
                }
            ).encode("utf-8"),
            "many-nodes.json": json.dumps(
                [
                    [0] * routing_probe.MAX_JSON_LIST_ITEMS
                    for _ in range(routing_probe.MAX_JSON_LIST_ITEMS // 2)
                ]
            ).encode("utf-8"),
            "invalid-utf8.json": b"\xff",
            "malformed.json": b"{",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, payload in payloads.items():
                with self.subTest(name=name):
                    path = root / name
                    path.write_bytes(payload)
                    before = {
                        child.name: hashlib.sha256(child.read_bytes()).hexdigest()
                        for child in root.iterdir()
                    }

                    with self.assertRaises(routing_probe.ContractError) as error:
                        routing_probe._read_json(path)

                    after = {
                        child.name: hashlib.sha256(child.read_bytes()).hexdigest()
                        for child in root.iterdir()
                    }
                    self.assertEqual(error.exception.code, "invalid-json-input")
                    self.assertEqual(after, before)

    def test_json_reader_rejects_duplicate_object_keys_in_corpus_and_observation(
        self,
    ) -> None:
        duplicate_corpus = (
            '{"schema_version":"skill-routing-cases/v1",'
            '"schema_version":"skill-routing-cases/v1"}'
        )
        observation = json.dumps(self.observation(), separators=(",", ":"))
        duplicate_observation = observation.replace(
            '"explicit_invocation":false',
            '"explicit_invocation":false,"explicit_invocation":true',
            1,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, payload in {
                "duplicate-corpus.json": duplicate_corpus,
                "duplicate-observation.json": duplicate_observation,
            }.items():
                with self.subTest(name=name):
                    path = root / name
                    path.write_text(payload, encoding="utf-8")
                    with self.assertRaises(routing_probe.ContractError) as error:
                        routing_probe._read_json(path)
                    self.assertEqual(error.exception.code, "invalid-json-input")

    def test_score_allows_other_skill_loads(self) -> None:
        case_id = "python-traceback"
        raw = self.observation(
            selected_overrides={case_id: ["agents-md", "python-debugging"]}
        )
        score = routing_probe.score_observation(
            self.cases, self.validated_observation(raw)
        )

        self.assertEqual(score["observed_counts"]["observed_violations"], 0)

    def test_score_records_only_clear_must_select_and_must_not_select_violations(
        self,
    ) -> None:
        raw = self.observation(
            selected_overrides={
                "python-traceback": [],
                "murmuration-local-only": ["consult"],
            }
        )
        score = routing_probe.score_observation(
            self.cases, self.validated_observation(raw)
        )

        self.assertEqual(
            score["observed_violations"],
            [
                {
                    "trial_id": "trial-003",
                    "rule": "must_not_select",
                    "skill": "consult",
                },
                {
                    "trial_id": "trial-004",
                    "rule": "must_select",
                    "skill": "python-debugging",
                },
            ],
        )

    def test_uncertainty_is_not_evidence_of_compliance_or_violation(self) -> None:
        raw = self.observation(selected_overrides={"python-traceback": []})
        raw["trials"][3]["uncertainty"] = "reported"
        score = routing_probe.score_observation(
            self.cases, self.validated_observation(raw)
        )

        self.assertNotIn(
            {
                "trial_id": "trial-004",
                "rule": "must_select",
                "skill": "python-debugging",
            },
            score["observed_violations"],
        )
        self.assertGreater(score["observed_counts"]["inconclusive_assertions"], 0)
        self.assertIn("not evidence", score["limitation"])

    def test_compare_is_incomparable_on_context_drift(self) -> None:
        baseline = self.validated_observation(self.observation())
        candidate_raw = self.observation()
        candidate_raw["context"]["freshness"] = "stale"
        candidate = self.validated_observation(candidate_raw)

        comparison = routing_probe.compare_observations(baseline, candidate, self.cases)

        self.assertEqual(comparison["status"], "incomparable")
        self.assertEqual(comparison["reasons"], ["context"])
        self.assertIsNone(comparison["observed_counts"])

    def test_compare_is_incomparable_on_other_required_provenance_drift(self) -> None:
        baseline = self.validated_observation(self.observation())
        mutations = (
            (
                "corpus_sha256",
                lambda candidate: candidate.update(
                    corpus_sha256=digest("other-corpus")
                ),
            ),
            ("client", lambda candidate: candidate["client"].update(version="2.0")),
            ("model", lambda candidate: candidate["model"].update(id="model-b")),
            (
                "changed_skills",
                lambda candidate: candidate.update(changed_skills=["agents-md"]),
            ),
            (
                "evidence_method",
                lambda candidate: candidate.update(
                    evidence_method="external-isolated-context-capture"
                ),
            ),
        )
        for expected_reason, mutation in mutations:
            with self.subTest(reason=expected_reason):
                candidate = copy.deepcopy(baseline)
                mutation(candidate)
                comparison = routing_probe.compare_observations(
                    baseline, candidate, self.cases
                )
                self.assertEqual(comparison["status"], "incomparable")
                self.assertEqual(comparison["reasons"], [expected_reason])

    def test_compare_is_incomparable_on_non_target_drift_and_allows_target_surface_change(
        self,
    ) -> None:
        baseline = self.validated_observation(self.observation())
        candidate_raw = self.observation()
        candidate_raw["full_discovery_sha256"] = digest("full-discovery-b")
        candidate = self.validated_observation(candidate_raw)
        self.assertEqual(
            routing_probe.compare_observations(baseline, candidate, self.cases)[
                "status"
            ],
            "observed-comparison",
        )

        candidate_raw = self.observation()
        candidate_raw["full_discovery_sha256"] = digest("full-discovery-c")
        candidate_raw["changed_skills"] = ["agents-md"]
        candidate = self.validated_observation(candidate_raw)
        comparison = routing_probe.compare_observations(baseline, candidate, self.cases)
        self.assertEqual(comparison["status"], "incomparable")
        self.assertEqual(comparison["reasons"], ["changed_skills"])

        candidate_raw = self.observation()
        candidate_raw["non_target_discovery_sha256"] = digest("non-target-b")
        candidate = self.validated_observation(candidate_raw)
        comparison = routing_probe.compare_observations(baseline, candidate, self.cases)
        self.assertEqual(comparison["status"], "incomparable")
        self.assertEqual(comparison["reasons"], ["non_target_discovery_sha256"])

    def test_compare_reports_new_observed_violations_without_release_claims(
        self,
    ) -> None:
        baseline = self.validated_observation(self.observation())
        candidate = self.validated_observation(
            self.observation(selected_overrides={"python-traceback": []})
        )
        comparison = routing_probe.compare_observations(baseline, candidate, self.cases)
        rendered = json.dumps(comparison, sort_keys=True)

        self.assertEqual(comparison["status"], "observed-comparison")
        self.assertEqual(
            comparison["comparability_fields"],
            [
                "corpus_sha256",
                "non_target_discovery_sha256",
                "changed_skills",
                "client",
                "model",
                "context",
                "evidence_method",
            ],
        )
        self.assertEqual(
            comparison["observation_bindings"]["baseline"]["observation_sha256"],
            routing_probe.score_observation(self.cases, baseline)["observation_sha256"],
        )
        self.assertEqual(
            comparison["observation_bindings"]["candidate"]["observation_sha256"],
            routing_probe.score_observation(self.cases, candidate)[
                "observation_sha256"
            ],
        )
        self.assertEqual(
            comparison["new_observed_violations"],
            [
                {
                    "trial_id": "trial-004",
                    "rule": "must_select",
                    "skill": "python-debugging",
                }
            ],
        )
        self.assertNotIn("PASS", rendered)
        self.assertNotIn("READY", rendered)

    def test_script_imports_no_network_or_model_client_modules(self) -> None:
        parsed = ast.parse(
            SCRIPT_PATH.read_text(encoding="utf-8"), filename=str(SCRIPT_PATH)
        )
        forbidden = {
            "openai",
            "requests",
            "http",
            "httpx",
            "socket",
            "subprocess",
            "urllib",
            "webbrowser",
        }
        imports = set()
        for node in ast.walk(parsed):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        self.assertFalse(imports.intersection(forbidden))
        calls = {
            node.func.id
            for node in ast.walk(parsed)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertFalse({"OpenAI", "Client", "urlopen"}.intersection(calls))

    def test_cli_help_and_plan_are_read_only_json_outputs(self) -> None:
        help_result = subprocess.run(
            [sys.executable, "-B", str(SCRIPT_PATH), "--help"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("plan", help_result.stdout)
        self.assertIn("score", help_result.stdout)
        self.assertIn("compare", help_result.stdout)

        plan_result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(SCRIPT_PATH),
                "--skills-root",
                str(self.discovery_root),
                "plan",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(plan_result.returncode, 0)
        self.assertEqual(plan_result.stderr, "")
        self.assertEqual(
            json.loads(plan_result.stdout)["schema_version"], routing_probe.PLAN_SCHEMA
        )

    def test_cli_template_is_value_safe_and_supplies_accepted_bindings(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(SCRIPT_PATH),
                "score",
                "--template",
                "--skills-root",
                str(self.discovery_root),
                "--changed-skill",
                "skill-author",
                "--skill-revision",
                "routing-probe-v1",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        template = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(template["corpus_sha256"], self.corpus_hash)
        self.assertNotIn(str(self.discovery_root.resolve()), result.stdout)
        self.assertNotIn(self.cases["python-traceback"]["group"], result.stdout)
        self.assertEqual(
            self.validated_observation(self.materialize_template(template))[
                "corpus_sha256"
            ],
            self.corpus_hash,
        )

    def test_cli_errors_do_not_echo_untrusted_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cases_path = Path(directory) / "cases.json"
            corpus = copy.deepcopy(self.corpus)
            secret = "UNTRUSTED-VALUE-MUST-NOT-ECHO"
            corpus["cases"][0]["must_select"] = [secret.lower()]
            cases_path.write_text(json.dumps(corpus), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(SCRIPT_PATH),
                    "--cases",
                    str(cases_path),
                    "--skills-root",
                    str(self.discovery_root),
                    "plan",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(json.loads(result.stderr)["error"], "unknown-skill-reference")
        self.assertNotIn(secret, result.stderr)

        secret = "UNTRUSTED-COMMAND-LINE-VALUE"
        result = subprocess.run(
            [sys.executable, "-B", str(SCRIPT_PATH), "plan", f"--{secret.lower()}"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(json.loads(result.stderr)["error"], "invalid-command-line")
        self.assertNotIn(secret, result.stderr)

        secret = "UNTRUSTED-CHANGED-SKILL"
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(SCRIPT_PATH),
                "score",
                "--template",
                "--skills-root",
                str(self.discovery_root),
                "--changed-skill",
                secret.lower(),
                "--skill-revision",
                "routing-probe-v1",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(json.loads(result.stderr)["error"], "invalid-changed-skills")
        self.assertNotIn(secret, result.stderr)


if __name__ == "__main__":
    unittest.main()
