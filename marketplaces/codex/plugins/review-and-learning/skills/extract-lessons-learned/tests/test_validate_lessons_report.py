from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import validate_lessons_report as validator  # noqa: E402


IMPLEMENTATION_HEADER = (
    "| Lesson ID | Destination Checked | Status | Existing Coverage | Gap To Patch |"
)
IMPLEMENTATION_SEPARATOR = "|---|---|---|---|---|"
ROUTING_HEADER = (
    "| Lesson ID | Disposition | Destination | Primary Root Evidence | Independent Root Families | "
    "One-off Correctness Impact | Prior Synthesis Match | Prior Synthesis Decision | "
    "Confidence | Limitations / Counter-evidence | Action Kind | Action | Validation Gate | "
    "Defer Trigger | Why |"
)
ROUTING_SEPARATOR = "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"


def valid_implementation_rows() -> list[str]:
    return [
        "| L1 | skills/extract-lessons-learned/SKILL.md | new | none | add the missing preflight validation step |",
        "| L3 | instructions/python-general.instructions.md | already covered | current rule already requires the behavior | none |",
        "| L4 | docs/lessons-learned/current.md | conflicts | prior synthesis contradicts current primary evidence | none |",
        "| L5 | skills/extract-lessons-learned/references/routing-guide.md | partial | workflow exists but omits stable call identifiers | add call identifier validation after capability ships |",
    ]


def valid_routing_rows() -> list[str]:
    return [
        "| L1 | apply | skills/extract-lessons-learned/SKILL.md | root-a@sessions/root-a.jsonl#turn-7; root-b@sessions/root-b.jsonl#turn-3 | 2 | none | none | none | high | no contrary successful path observed | validate | validate the completed lessons report | strict validation exits zero and two fixtures pass | n/a | repeated workflow gap |",
        "| L2 | monitor | docs/retrospective-monitor.md | root-c@sessions/root-c.jsonl#turn-5 | 1 | none | none | none | low | only one independent root observed | observe | observe:artifact:reports/root-family-count.json | two independent root families are recorded | n/a | recurrence is not established |",
        "| L3 | already-covered | instructions/python-general.instructions.md | root-d@sessions/root-d.jsonl#turn-2 | 1 | none | docs/lessons-learned/previous.md#L7 | dedupe | medium | one independent root but implementation already covers it | none | none | existing rule path is recorded | n/a | current implementation owns the behavior |",
        "| L4 | supersede | docs/lessons-learned/current.md | root-e@sessions/root-e.jsonl#turn-8; root-f@sessions/root-f.jsonl#turn-4 | 2 | none | docs/lessons-learned/previous.md#L4 | supersede | high | older synthesis used incomplete evidence | none | none | superseding lesson citation is recorded | n/a | current evidence replaces old synthesis |",
        "| L5 | defer | skills/extract-lessons-learned/references/routing-guide.md | root-g@sessions/root-g.jsonl#turn-6; root-h@sessions/root-h.jsonl#turn-9 | 2 | none | none | none | medium | upstream capability is not available | defer | defer until trigger | focused fixture exits zero after trigger | when:artifact-change:schemas/call-ids.json \\| evidence:artifact:schemas/call-ids.json | useful gap has an unresolved prerequisite |",
        "| L6 | discard | discard | root-i@sessions/root-i.jsonl#turn-1 | 1 | none | none | none | low | incident-only fact with no reusable guidance | none | none | discard reason and source are recorded | n/a | no reusable behavior exists |",
    ]


def build_report(
    *,
    headings: list[str] | None = None,
    implementation_rows: list[str] | None = None,
    routing_rows: list[str] | None = None,
    include_implementation: bool = True,
) -> str:
    """Return a complete report fixture covering every disposition."""

    headings = headings or [
        "### [L1] Apply repeated correction",
        "### [L2] Monitor sparse signal",
        "### [L3] Already covered behavior",
        "### [L4] Superseded synthesis",
        "### [L5] Deferred destination gap",
        "### [L6] Discard incident-only fact",
    ]
    implementation_rows = (
        valid_implementation_rows()
        if implementation_rows is None
        else implementation_rows
    )
    routing_rows = valid_routing_rows() if routing_rows is None else routing_rows
    lines = ["# Lessons", "", *headings, "", "- Evidence: primary sources", ""]
    if include_implementation:
        lines.extend(
            [
                "## Implementation Audit",
                "",
                IMPLEMENTATION_HEADER,
                IMPLEMENTATION_SEPARATOR,
                *implementation_rows,
                "",
            ]
        )
    lines.extend(
        [
            "## Routing",
            "",
            ROUTING_HEADER,
            ROUTING_SEPARATOR,
            *routing_rows,
            "",
        ]
    )
    return "\n".join(lines)


class ValidateLessonsReportTests(unittest.TestCase):
    def codes(self, text: str) -> list[str]:
        _, diagnostics = validator.validate_report(text)
        return [item.code for item in diagnostics]

    def test_accepts_all_six_dispositions(self) -> None:
        lesson_ids, diagnostics = validator.validate_report(build_report())

        self.assertEqual(lesson_ids, ["L1", "L2", "L3", "L4", "L5", "L6"])
        self.assertEqual(diagnostics, [])

    def test_monitor_and_discard_need_no_implementation_section(self) -> None:
        text = build_report(
            headings=["### [L2] Monitor", "### [L6] Discard"],
            routing_rows=[valid_routing_rows()[1], valid_routing_rows()[5]],
            include_implementation=False,
        )

        self.assertEqual(self.codes(text), [])

    def test_rejects_monitor_and_discard_implementation_rows(self) -> None:
        rows = valid_implementation_rows() + [
            "| L2 | docs/retrospective-monitor.md | partial | one root only is visible | collect another independent occurrence |",
            "| L6 | docs/discard-log.md | new | none | record the discarded incident |",
        ]

        self.assertEqual(
            self.codes(build_report(implementation_rows=rows)).count(
                "unexpected_implementation_audit"
            ),
            2,
        )

    def test_rejects_missing_audit_for_apply(self) -> None:
        rows = [
            row for row in valid_implementation_rows() if not row.startswith("| L1")
        ]

        self.assertIn(
            "missing_implementation_audit",
            self.codes(build_report(implementation_rows=rows)),
        )

    def test_one_root_apply_requires_machine_checkable_correctness_impact(self) -> None:
        text = build_report().replace(
            "root-a@sessions/root-a.jsonl#turn-7; root-b@sessions/root-b.jsonl#turn-3 | 2 | none",
            "root-a@sessions/root-a.jsonl#turn-7 | 1 | none",
            1,
        )
        self.assertIn("invalid_correctness_impact", self.codes(text))

        justified = text.replace(
            "| 1 | none | none | none | high",
            "| 1 | correctness:data-loss:user-record \\| evidence:test:tests/test_save.py::test_atomic_write | none | none | high",
            1,
        )
        self.assertNotIn("invalid_correctness_impact", self.codes(justified))
        self.assertEqual(self.codes(justified), [])

        zero_roots = build_report().replace(
            "root-a@sessions/root-a.jsonl#turn-7; root-b@sessions/root-b.jsonl#turn-3 | 2",
            "none | 0",
            1,
        )
        self.assertIn("unsupported_apply", self.codes(zero_roots))

    def test_rejects_vague_structured_correctness_impact(self) -> None:
        for impact in (
            "correctness: thing fails \\| evidence: test:atomic-save",
            "correctness: this is very bad \\| evidence: check:save",
        ):
            with self.subTest(impact=impact):
                text = build_report().replace(
                    "root-a@sessions/root-a.jsonl#turn-7; root-b@sessions/root-b.jsonl#turn-3 | 2 | none",
                    f"root-a@sessions/root-a.jsonl#turn-7 | 1 | {impact}",
                    1,
                )
                self.assertIn("invalid_correctness_impact", self.codes(text))

    def test_accepts_every_structured_correctness_and_evidence_kind(self) -> None:
        for kind in sorted(validator.CORRECTNESS_KINDS):
            with self.subTest(correctness_kind=kind):
                self.assertTrue(
                    validator.validate_correctness_impact(
                        f"correctness:{kind}:affected-record | evidence:test:tests/case.py::test_case"
                    )
                )
        for kind in sorted(validator.STRUCTURED_EVIDENCE_KINDS):
            with self.subTest(evidence_kind=kind):
                self.assertTrue(
                    validator.validate_correctness_impact(
                        f"correctness:data-loss:affected-record | evidence:{kind}:evidence/ref-1"
                    )
                )

    def test_rejects_non_integer_root_family_count(self) -> None:
        text = build_report().replace(
            "| 2 | none | none | none | high",
            "| two | none | none | none | high",
            1,
        )

        self.assertIn("invalid_root_family_count", self.codes(text))

    def test_primary_root_evidence_must_match_declared_unique_count(self) -> None:
        one_entry_claiming_two = build_report().replace(
            "root-a@sessions/root-a.jsonl#turn-7; root-b@sessions/root-b.jsonl#turn-3",
            "root-a@sessions/root-a.jsonl#turn-7",
            1,
        )
        self.assertIn("root_family_count_mismatch", self.codes(one_entry_claiming_two))

        duplicate_family = build_report().replace(
            "root-a@sessions/root-a.jsonl#turn-7; root-b@sessions/root-b.jsonl#turn-3",
            "root-a@sessions/root-a.jsonl#turn-7; root-a@sessions/root-b.jsonl#turn-3",
            1,
        )
        self.assertIn("duplicate_root_family", self.codes(duplicate_family))

    def test_prior_synthesis_cannot_be_primary_root_evidence(self) -> None:
        prior_claiming_two = build_report().replace(
            "root-a@sessions/root-a.jsonl#turn-7; root-b@sessions/root-b.jsonl#turn-3",
            "root-a@docs/lessons-learned/previous.md#L1; root-b@docs/lessons-learned/previous.md#L2",
            1,
        )
        self.assertIn(
            "prior_synthesis_as_primary_evidence", self.codes(prior_claiming_two)
        )

        exact_prior_match = (
            build_report()
            .replace(
                "root-d@sessions/root-d.jsonl#turn-2",
                "root-d@docs/prior.md#L8",
                1,
            )
            .replace(
                "docs/lessons-learned/previous.md#L7",
                "docs/prior.md#L7",
                1,
            )
        )
        self.assertIn(
            "prior_synthesis_as_primary_evidence", self.codes(exact_prior_match)
        )

    def test_rejects_placeholder_gap(self) -> None:
        text = build_report().replace(
            "add the missing preflight validation step |",
            "{gap} |",
            1,
        )

        self.assertIn("incomplete_gap", self.codes(text))

    def test_rejects_generic_done_gate(self) -> None:
        text = build_report().replace(
            "strict validation exits zero and two fixtures pass",
            "done",
            1,
        )

        self.assertIn("invalid_validation_gate", self.codes(text))

    def test_rejects_generic_later_trigger(self) -> None:
        for trigger in (
            "later",
            "when: project feels ready \\| evidence: check:readiness",
            "when: someone feels sufficiently motivated \\| evidence: check:motivation",
        ):
            with self.subTest(trigger=trigger):
                text = build_report().replace(
                    "when:artifact-change:schemas/call-ids.json \\| evidence:artifact:schemas/call-ids.json",
                    trigger,
                    1,
                )
                self.assertIn("missing_defer_trigger", self.codes(text))

    def test_accepts_every_structured_event_and_evidence_kind(self) -> None:
        for kind in sorted(validator.DEFER_EVENT_KINDS):
            with self.subTest(event_kind=kind):
                self.assertTrue(
                    validator.validate_defer_trigger(
                        f"when:{kind}:event/ref-1 | evidence:artifact:artifacts/event.json"
                    )
                )
        for kind in sorted(validator.STRUCTURED_EVIDENCE_KINDS):
            with self.subTest(evidence_kind=kind):
                self.assertTrue(
                    validator.validate_defer_trigger(
                        f"when:recurrence:event/ref-1 | evidence:{kind}:evidence/ref-1"
                    )
                )

        oversized = "x" * 161
        self.assertFalse(
            validator.validate_defer_trigger(
                f"when:recurrence:{oversized} | evidence:test:tests/case.py"
            )
        )
        self.assertFalse(
            validator.validate_defer_trigger("when:recurrence:x | evidence:test:y")
        )
        self.assertFalse(
            validator.validate_correctness_impact(
                "correctness:data-loss:! | evidence:test:!"
            )
        )

    def test_rejects_new_status_with_already_covered_disposition(self) -> None:
        text = build_report().replace(
            "| L3 | instructions/python-general.instructions.md | already covered | current rule already requires the behavior | none |",
            "| L3 | instructions/python-general.instructions.md | new | none | none |",
            1,
        )

        self.assertIn("status_disposition_mismatch", self.codes(text))

    def test_rejects_discard_action_suffix_edit(self) -> None:
        text = build_report().replace(
            "| none | none | discard reason and source are recorded |",
            "| none | none; update the rule | discard reason and source are recorded |",
            1,
        )

        self.assertIn("action_prefix_mismatch", self.codes(text))

    def test_rejects_monitor_action_with_immediate_edit(self) -> None:
        text = build_report().replace(
            "observe:artifact:reports/root-family-count.json",
            "observe and update the destination immediately",
            1,
        )

        self.assertIn("invalid_observe_action", self.codes(text))

    def test_action_kind_must_match_disposition_and_action_prefix(self) -> None:
        wrong_kind = build_report().replace(
            "| high | no contrary successful path observed | validate |",
            "| high | no contrary successful path observed | observe |",
            1,
        )
        self.assertIn("action_kind_disposition_mismatch", self.codes(wrong_kind))

        wrong_prefix = build_report().replace(
            "| validate | validate the completed lessons report |",
            "| validate | edit the completed lessons report |",
            1,
        )
        self.assertIn("action_prefix_mismatch", self.codes(wrong_prefix))

        for mutation in (
            "revise",
            "refactor",
            "delete",
            "write",
            "rename",
            "move",
            "rewrite",
            "alter",
            "append",
            "truncate",
            "deploy",
            "commit",
            "merge",
            "overwrite",
        ):
            with self.subTest(mutation=mutation):
                text = build_report().replace(
                    "observe:artifact:reports/root-family-count.json",
                    f"observe and {mutation} the destination",
                    1,
                )
                self.assertIn("invalid_observe_action", self.codes(text))

    def test_monitor_accepts_each_structured_evidence_kind(self) -> None:
        for kind in sorted(validator.STRUCTURED_EVIDENCE_KINDS):
            with self.subTest(evidence_kind=kind):
                text = build_report().replace(
                    "observe:artifact:reports/root-family-count.json",
                    f"observe:{kind}:evidence/root-family-count.json",
                    1,
                )
                self.assertEqual(self.codes(text), [])

    def test_apply_accepts_each_productive_action_kind(self) -> None:
        for kind in ("validate", "test", "edit", "create", "document"):
            with self.subTest(kind=kind):
                text = build_report().replace(
                    "| validate | validate the completed lessons report |",
                    f"| {kind} | {kind} the completed lessons report |",
                    1,
                )
                self.assertEqual(self.codes(text), [])

    def test_rejects_literal_new_skill_candidate(self) -> None:
        text = build_report().replace(
            "skills/extract-lessons-learned/SKILL.md | root-a@sessions/root-a.jsonl#turn-7",
            "new skill candidate | root-a@sessions/root-a.jsonl#turn-7",
            1,
        )

        self.assertIn("invalid_destination", self.codes(text))

    def test_rejects_missing_duplicate_and_orphan_ids(self) -> None:
        headings = [
            "### [L1] First",
            "### [L1] Duplicate",
            "### [L2] Monitor",
            "### [L3] Covered",
            "### [L4] Supersede",
            "### [L5] Defer",
            "### [L6] Discard",
            "### [L7] Missing route",
        ]
        routes = valid_routing_rows() + [valid_routing_rows()[5].replace("L6", "L8", 1)]

        codes = self.codes(build_report(headings=headings, routing_rows=routes))
        self.assertIn("duplicate_lesson_id", codes)
        self.assertIn("missing_lesson_id", codes)
        self.assertIn("orphan_lesson_id", codes)

    def test_prior_match_and_decision_are_terminal_routing_fields(self) -> None:
        mismatched = build_report().replace(
            "| none | none | high | no contrary",
            "| docs/prior.md#L7 | none | high | no contrary",
            1,
        )
        self.assertIn("prior_synthesis_mismatch", self.codes(mismatched))

        dedupe_apply = mismatched.replace(
            "| docs/prior.md#L7 | none | high",
            "| docs/prior.md#L7 | dedupe | high",
            1,
        )
        self.assertIn("dedupe_disposition_mismatch", self.codes(dedupe_apply))

    def test_exact_destinations_are_portable(self) -> None:
        self.assertTrue(validator.is_exact_destination("docs/My Report.md"))
        self.assertTrue(
            validator.is_exact_destination("memory:user-preferences/autonomy")
        )
        self.assertFalse(validator.is_exact_destination("new skill candidate"))
        self.assertFalse(validator.is_exact_destination("skills/<candidate>/SKILL.md"))
        self.assertFalse(validator.is_exact_destination("../AGENTS.md"))
        self.assertTrue(validator.is_exact_prior_match("docs/My Report.md#L2"))
        self.assertFalse(validator.is_exact_prior_match("docs/My Report.md"))

    def test_report_wide_placeholders_fail_but_fenced_examples_are_ignored(
        self,
    ) -> None:
        template = (
            Path(__file__).resolve().parents[1] / "references" / "output-template.md"
        )
        _, diagnostics = validator.validate_report(template.read_text(encoding="utf-8"))
        placeholder_diagnostics = [
            item for item in diagnostics if item.code == "unresolved_placeholder"
        ]
        self.assertGreater(len(placeholder_diagnostics), 10)
        self.assertEqual(
            placeholder_diagnostics[0].message,
            "replace unresolved placeholder at line 1, column 20",
        )
        envelope = validator.build_envelope(
            template, template.read_text(encoding="utf-8")
        )
        self.assertFalse(envelope["ok"])

        unresolved = build_report().replace("# Lessons", "# Lessons {Project}", 1)
        self.assertIn("unresolved_placeholder", self.codes(unresolved))

        fenced = build_report() + "\n```text\n{literal example}\n```\n"
        self.assertEqual(self.codes(fenced), [])

    def test_cli_emits_stable_json_and_nonzero_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            report = Path(temp_dir) / "report.md"
            report.write_text(build_report(), encoding="utf-8", newline="\n")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = validator.main([str(report)])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["schema_version"], 1)
            self.assertEqual(
                payload["result"]["lesson_ids"],
                ["L1", "L2", "L3", "L4", "L5", "L6"],
            )
            self.assertEqual(payload["result"]["diagnostics"], [])

            report.write_text(
                build_report().replace("| L1 | apply |", "| L1 | TBD |"),
                encoding="utf-8",
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = validator.main([str(report)])
            self.assertEqual(exit_code, 1)
            self.assertFalse(json.loads(stdout.getvalue())["ok"])


if __name__ == "__main__":
    unittest.main()
