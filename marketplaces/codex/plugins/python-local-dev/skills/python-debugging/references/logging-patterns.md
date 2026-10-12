# Logging Patterns

Use logging only when the failure is not already obvious from the traceback. The goal is to expose the bad value, bad branch, or bad call site with the fewest added lines.

## Default Pattern

```python
import logging

logger = logging.getLogger(__name__)

def process_data(data):
    logger.debug("process_data input type=%s size=%s", type(data).__name__, len(data))
    try:
        return transform(data)
    except Exception:
        logger.exception("transform failed")
        raise
```

Use this when you need to know what entered a function and where it failed.

## What To Log

- Runtime type and small shape data: `type(value).__name__`, `len(value)`, available keys, `df.columns`
- Boundary identifiers: file path, record id, request id, notebook cell input
- Branch choice: which code path executed before the crash

## What Not To Log

- Entire large payloads when a shape summary is enough
- Secrets, tokens, or credentials
- Duplicate logs at every stack frame for the same failure

## Exception Logging

When you re-raise, log once near the boundary that adds useful context.

```python
try:
    response = api.call(payload)
except APIError:
    logger.exception("api call failed payload_id=%s", payload["id"])
    raise
```

When failure is optional and you intentionally continue, catch the narrowest exception and say why continuation is safe.

## Databricks And Spark

- Suppress noisy loggers only if they obscure the application failure: `py4j`, `py4j.java_gateway`, `pyspark`
- Prefer logging schema and column names over full DataFrame contents
- In notebooks, keep temporary logging close to the failing cell and remove it after the root cause is confirmed

## Removal Rule

After the failing value or call site is identified, remove temporary debug logging unless it is still useful as durable diagnostics.

## File Logging

```python
import logging
from datetime import datetime
from pathlib import Path

def setup_file_logging(log_dir: str = "logs") -> logging.Logger:
    """Configure logging to file with rotation."""
    Path(log_dir).mkdir(exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = Path(log_dir) / f"app_{timestamp}.log"
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(),  # Also log to console
        ]
    )
    
    return logging.getLogger(__name__)
```

---

## Anti-Patterns to Avoid

```python
# ❌ Don't use print for logging
print(f"Processing {count} records")

# ✅ Use logger
logger.info(f"Processing {count} records")

# ❌ Don't catch and ignore
try:
    risky_operation()
except Exception:
    pass

# ✅ At least log it
try:
    risky_operation()
except Exception:
    logger.warning("Operation failed", exc_info=True)

# ❌ Don't log sensitive data
logger.info(f"Login with password: {password}")

# ✅ Redact sensitive info
logger.info(f"Login attempt for user: {username}")
```
