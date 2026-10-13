# Import-Time Dependency Triage

Use this when an ML workload fails before the selected behavior actually starts, or when a loud CUDA warning appears before a different fatal exception.

## First Blocker Ladder

Classify log lines in this order:

1. warning-only noise, such as local `Could not find cuda drivers`
2. first fatal exception or nonzero child process
3. import-time dependency failure
4. missing external data or model resource
5. wrapper that swallowed the original child failure
6. true package, CUDA, DBR, or HuggingFace incompatibility

Do not pin GPU packages, change DBR, or install large optional stacks until the first fatal blocker requires that fix.

## Optional Backend Rule

Separate required core imports from optional backend or behavior imports.

Bad pattern:

```python
import spacy
import vllm

def hash_check(text: str) -> str:
    return stable_hash(text)
```

Good pattern:

```python
def run_spacy_behavior(text: str) -> object:
    try:
        import spacy
    except ImportError as exc:
        raise ImportError("Install the nlp extra to use spaCy behavior") from exc

    return spacy.blank("en")(text)
```

If only one backend, behavior, mutator, or model family needs a package, import it inside that selected path and make the error name the extra or package that unlocks it.

## Missing Data Resources

For NLTK or similar resource failures:

- distinguish the Python package from the external data resource
- probe the requested corpus or tokenizer with `collect_env_snapshot.py --nltk-data punkt,wordnet`
- fail with an error naming the resource and expected installation path
- avoid downloading resources at module import time

## CUDA Warning Triage

Treat this line as context when the requested mode is local or CPU-compatible:

```text
Could not find cuda drivers on your machine, GPU will not be used
```

Record the warning, then keep scanning for the first exception, missing package, missing data resource, or child-process failure.
Only escalate to GPU pins, CUDA checks, or cluster changes when the workload requires GPU or the first fatal exception is GPU-specific.

## Wrapper And Child Failure Rule

Wrappers must preserve child failure evidence:

- surface the first failing child command or iteration
- preserve exit code and stderr
- fail loudly when zero iterations, zero outputs, or zero artifacts were produced
- do not crash later because the wrapper assumed at least one successful child result

Runtime repair is not complete until the selected backend path runs directly and through the wrapper that originally hid the failure.