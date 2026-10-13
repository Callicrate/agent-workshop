---
description: "Project-level standards: context analysis, documentation quality, code quality, git conventions"
applyTo: '**'
---

# Project-Level Standards

## Context Analysis Requirements

### File and Notebook Context

Before modifications:

- Read and understand full content of explicitly mentioned files
- Analyze entire notebooks to understand dependencies and data flow
- Treat every `.ipynb` notebook as a standalone primary execution entry point for its task or job; do not design notebooks to be called from other notebooks.
- Cross-reference related files, modules, and documentation

### Adjacent Code Review

When editing small files, analyze related code for consistency:

**Source files (< 300 lines):**

- Sibling files in the same directory
- Package `__init__.py` and shared modules
- Directly imported modules

**Markdown files (< 400 lines):**

- Source code being documented
- Files linked in the documentation

**Notebooks (< 300 lines):**

- Imported modules and dependencies
- Related notebooks in the same directory

---

## Documentation Standards

### Docstring Quality

Write accurate, useful docstrings—avoid AI-generated filler:

```python
# ✅ CORRECT - explains non-obvious behavior
def normalize_timestamps(doc: dict) -> dict:
    """Convert epoch timestamps to datetime objects.
 
    Args:
        doc: Document with potential epoch timestamp fields
        
    Returns:
        Document with timestamps converted to datetime objects

    Raises:
        ValueError: If a timestamp field is not a valid epoch integer
    """

# ❌ WRONG - obvious from signature, adds no value
def normalize_timestamps(doc: dict) -> dict:
    """Normalize timestamps in a document.
    
    This function takes a document dictionary and normalizes the timestamps
    contained within it. It processes the document and returns the normalized
    version with all timestamps properly normalized.
    
    Args:
        doc: The document to normalize timestamps for
        
    Returns:
        The normalized document with timestamps adjusted accordingly and fixed
    
    Raises:
        ValueError: If normalization fails or input is invalid or any other error occurs
    """
```

### Inline Comments

Comment complex business logic, not obvious operations:

```python
# ✅ CORRECT - explains business rule
# Exclude synthetic sources to avoid training bias
query["filter"].append({"term": {"source_is_synthetic": False}})

# ❌ WRONG - obvious from code
# Add filter to query
query["filter"].append({"term": {"source_is_synthetic": False}})
```

### Documentation Accuracy

Ensure docstrings match actual function behavior. Incorrect documentation is worse than no documentation.

---

## Code Quality

### Unused Code

Remove unused imports, functions, and variables before committing.

### Code Formatters

Use a code formatter with configuration committed to the repository:

- Python: `ruff`, `black`, or `yapf` with config in `pyproject.toml` or `.ruff.toml`
- SQL: `sqlfluff` with config in `pyproject.toml` or `.sqlfluff`

### Consistent Patterns

Follow existing patterns in the codebase:

- Variable naming conventions
- Error handling approaches
- Logging patterns
- Configuration management

### Cross-Stage Contracts

When one pipeline stage produces a value consumed by later stages, make the contract explicit. Fail loudly if the upstream value is missing rather than using a hardcoded fallback or silent default.

### Contract-Bearing Docs Must Stay In Sync

When a change alters query semantics, output tables, job parameters, or public interfaces, update the contract-bearing documentation in the **same commit**. Contract-bearing docs include:

- `docs/architecture.md` or equivalent architecture descriptions
- `status/*.md` or project status/runbook files
- `AGENTS.md` sections that describe data flow, table names, or CLI interfaces
- README sections that describe inputs, outputs, or operational procedures

If a doc cannot be updated immediately (e.g., the change is exploratory), add a clearly visible `<!-- STALE: updated X but not yet reflected here -->` comment at the top of the affected section so drift is explicit rather than silent.

### Production-Activation Gate

Before any job, pipeline, or service transitions from dev/dry-run to production, the following checklist must be satisfied. Do not unpause a schedule or remove a dry-run flag until every item is confirmed:

1. **Service-principal `run_as` configured** - The production job must run under a service principal, not a personal user account. Verify `run_as` in the bundle or job settings.
2. **Analyst/stakeholder review criteria defined** - Document what an analyst or domain expert must verify in the output before the job is considered production-ready (e.g., row counts, value distributions, no unexpected nulls).
3. **Explicit production approval documented** - A written approval (PR comment, Slack message, or ticket) from the designated approver must exist before flipping to production. Reference the approval in the commit message or PR description.

If any gate is not yet met, keep the job paused or in dry-run mode and document which gates remain open in the project status or PR description.

### Registry Pattern for Pluggable Components

For pipelines with pluggable analytical components (scorers, processors, validators), use a registry pattern with a standardized interface (ABC + registry list). The cost is minimal; the payoff is independent testing, easy enable/disable, and clean extensibility.

### Git-Backed Projects

When working in a git-backed repository:

- Be aware of the current branch and uncommitted changes
- Follow the project's commit convention (if defined in AGENTS.md)
- Do not make commits unless instructed or the project convention expects it

---

## AI Agent Behavior

- Prefer refactoring existing logic over creating redundant code
- Choose the simplest implementation that aligns with project patterns
- Maintain consistency with established conventions
- Prioritize readability and maintainability
