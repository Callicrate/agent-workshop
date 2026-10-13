# Prompt Iteration Workflow

Use this when the prompt already exists and behavior needs to change.

## 1. Build a Small Test Set

- Collect 5 to 10 representative inputs.
- Include at least one blocker case, one normal case, and one edge case.
- Write the expected behavior before editing the prompt.

## 2. Run the Baseline

- Keep the exact prompt version under test.
- Capture failures as observed behavior, not opinions.
- Group failures by repeated pattern.
- Record the execution method: repo-owned evaluation harness, manual observation, session log replay, user-provided failure report, or LLM-as-judge.
- If no harness exists, use session logs or user-provided failures as the baseline and label the evidence source.
- If using LLM-as-judge, treat it as heuristic evidence and keep at least one concrete observed transcript or output sample.
- Name the model target for the baseline. If the prompt must work across model families, run at least one representative input on each target model before finalizing.

## 3. Classify the Failure

| Failure Type | Typical Fix |
|--------------|-------------|
| Missing constraint | Add explicit must or never rule |
| Ambiguous instruction | Rewrite into one interpretation |
| Conflicting directives | Remove or prioritize one rule |
| Missing context | Load or define the missing input |
| Over-broad rule | Narrow the condition or scope |

## 4. Edit Minimally

- Change the smallest span that can fix the failure.
- Fix one failure class at a time when possible.
- Do not rewrite the entire prompt unless the section layout is the real problem.

## 5. Re-Test

- Re-run the same inputs first.
- Add a new golden case only when it catches a real bug.
- Revert edits that create more regression than improvement.

## 6. Record Iteration Evidence

Every iteration pass must produce a lightweight evidence artifact (inline in the PR, commit message, or a dedicated file). Minimum fields:

| Field | Content |
|-------|---------|
| Baseline failures | List of inputs that failed before the edit |
| Edited span | Section or line range changed |
| Rerun outcomes | Pass/fail per test input after the edit |
| Regression status | Whether any previously passing input now fails |
| Execution method | Harness, manual observation, session log, user report, or LLM-as-judge |
| Model target | Model family or runtime used for the baseline and rerun |
| Prompt version | Commit, file hash, or before/after path when available |

Without this artifact, the iteration cannot be considered verified.

The canonical schema is [iteration-evidence.schema.json](../assets/iteration-evidence.schema.json). Evidence files should validate against it.

## Exit Criteria

- The target failure no longer reproduces.
- The golden set still passes.
- The prompt stayed the same length or only grew for rules that earned their cost.
- The evidence names the execution method and model target.

Run [audit_prompt_structure.py](../scripts/audit_prompt_structure.py) before finalizing structural changes.
