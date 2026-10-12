# Behavioral Forward-Testing

Use this conditional check after strict validation when acceptance depends on a model interpretation or choice that structural checks cannot prove.

Run it for discovery or routing, branch or precedence selection, tool or handoff choice, permission or stop behavior, or output quality that is not deterministically enforced.
Skip it for behavior-neutral edits or changes that a deterministic validator already proves, and record the skip reason in the task checkpoint or final report.

For skill-description routing, use [scripts/routing_probe.py](../scripts/routing_probe.py) with [assets/routing-cases.json](../assets/routing-cases.json). It validates a small synthetic corpus and compares external captures; it does not invoke a model or prove a client selected a skill.

## Two-stage routing capture

1. Run `python -B scripts/routing_probe.py plan` locally and give the external evaluator only that JSON. Its opaque `trial-001` style IDs and prompts do not expose case names, groups, expected skills, corpus hashes, revisions, or capture provenance.
2. Run each evaluator trial in a fresh context without explicit invocation. Keep the evaluator transcript outside the plan and record only its opaque trial ID, observed selected skills, transcript hash, and bounded uncertainty.
3. After capture, a trusted operator runs `python -B scripts/routing_probe.py score --template --skills-root <skills-root> --changed-skill <name> --skill-revision <label>`. `<skills-root>` must be the full discovery root used by the evaluated client, not merely the plugin directory that happens to package this skill. Supply `--changed-skill` once per changed skill in strictly sorted, unique order. This trusted-only path fills the corpus and discovery hashes without leaking them into the evaluator plan.
4. Replace the explicit placeholders in the template, then run `score --observation <capture.json>` or `compare` locally. Their outputs retain only canonical observation and binding hashes, observed counts, and observed violations. Do not send either output back as hidden steering.

### Canonical discovery bindings

The template hashes the discovery fields that the host sees initially, without emitting their values. It enumerates each immediate `<skills-root>/<folder>/SKILL.md` skill, takes its frontmatter `name` and `description`, and represents it as exactly:

```json
{"path":"<skills-root-name>/<folder>/SKILL.md","name":"<frontmatter-name>","description":"<frontmatter-description>"}
```

The path is slash-separated and relative to the parent of `<skills-root>`; no absolute path is part of the representation. Records are sorted by top-level folder name. The script serializes the ordered array with JSON object keys sorted, ASCII escaping, compact `,`/`:` separators, and one trailing newline, then SHA-256 hashes those UTF-8 bytes. `full_discovery_sha256` uses every record. `non_target_discovery_sha256` excludes exactly the declared sorted, unique `changed_skills` folder names. Consequently, a changed target can change the full hash while leaving the non-target hash stable.

```json
{
  "schema_version": "skill-routing-observation/v1",
  "corpus_sha256": "<64-lowercase-hex>",
  "full_discovery_sha256": "<64-lowercase-hex>",
  "non_target_discovery_sha256": "<64-lowercase-hex>",
  "changed_skills": ["<known-skill-name>"],
  "skill_revision": "<revision-label>",
  "client": {"name": "<client>", "version": "<version>", "surface": "<surface>"},
  "model": {"provider": "<provider>", "id": "<model>", "reasoning": "<reasoning>"},
  "context": {
    "mode": "<mode-or-unknown>",
    "freshness": "<freshness-or-unknown>",
    "path_visibility": "<visibility-or-unknown>",
    "instruction_profile": "<profile-or-unknown>"
  },
  "explicit_invocation": false,
  "evidence_method": "external-fresh-context-capture",
  "trials": [
    {
      "trial_id": "trial-001",
      "selected_skills": ["<observed-skill-name>"],
      "transcript_sha256": "<unique-64-lowercase-hex>",
      "uncertainty": "none"
    }
  ]
}
```

Repeat the trial object exactly once for every plan ID. The template is intentionally non-scoreable until every `<...>` placeholder is replaced with a bounded observed value; placeholder and sentinel values are rejected with a fixed validation error. Transcript hashes bind a capture reference but do not authenticate a capture or prove a selected skill.
`changed_skills` and every `selected_skills` list must be sorted and duplicate-free. The only allowed `uncertainty` values are `none`, `reported`, `unknown`, and `ambiguous`.

For the optional live-catalog test, explicitly set `AGENTS_FULL_SKILLS_ROOT` to the absolute full discovery root used by the client before running the skill-author suite. The test validates the current complete catalog and skips when that variable is unset, invalid, synthetic, or only a partial plugin projection; it never infers that a root is full from the routing corpus references or a folder count.

## Procedure

1. Define the scenarios and pass conditions before the run. Keep the oracle hidden from the evaluating model or agent.
2. Use fresh, isolated contexts. Do not reuse a prior transcript, answer, or generated artifact as hidden steering.
3. Use the smallest realistic prompt set: at least one in-scope prompt and one adjacent out-of-scope prompt when routing matters.
4. Keep the run read-only or confined to an isolated workspace. Do not cause side effects or external mutation without separate authority.
5. Record the prompt transcript, generated artifact when applicable, client, model, context conditions, observed result, and uncertainty.
6. State the conclusion narrowly. One client or model run does not support a cross-client or cross-model behavior claim.

## Routing capture and release boundary

1. Run `python -B scripts/routing_probe.py plan` from the skill root. It emits only synthetic prompt/trial material; keep the expected-skill oracle private to the scorer.
2. Capture each trial in a fresh external client context without explicit skill invocation. Record the selected-skill names, unique transcript hash, bounded uncertainty, and client/model/context/discovery provenance using the template above.
3. Run `python -B scripts/routing_probe.py score --observation <capture.json>` to report only clear `must_select` and `must_not_select` violations. Extra skill loads are allowed, and uncertainty is neither compliance nor a violation.
4. Use `compare --baseline <capture.json> --candidate <capture.json>` only when corpus, declared changed-skill list, client, model, context, evidence method, and non-target discovery bindings match. The full discovery hash remains bound provenance and may differ only because the declared changed skills are the subject of the capture. An `incomparable` result is a stop for automated comparison, not a result to reinterpret.
5. Keep the final release decision human. The helper establishes bounded validation and observed counts; it cannot establish deterministic routing, broader client behavior, or release readiness.

This is forward-testing, not a requirement to build an eval framework, add test assets, or create a reusable runner.
