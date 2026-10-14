---
description: "YAML configuration standards: formatting, indentation, quoting, anchors"
applyTo: '**/*.yml,**/*.yaml'
---

# YAML Configuration Standards

## Formatting

### Indentation

- Use 2 spaces for indentation (never tabs)
- Be consistent throughout the file

### Strings

- Quote strings containing special characters (`:`, `#`, `{`, `}`, `[`, `]`, `,`, `&`, `*`, `!`, `|`, `>`, `'`, `"`, `%`, `@`, `` ` ``)
- Quote strings that look like numbers or booleans but should be strings
- Prefer double quotes over single quotes for consistency

```yaml
# Good
name: "my-resource"
description: "Contains: special characters"
version: "1.0"  # String, not number

# Avoid
name: my-resource
version: 1.0  # Interpreted as number
```

### Lists

```yaml
# Preferred - each item on its own line
items:
  - item_one
  - item_two
  - item_three

# Acceptable for short lists
tags: [dev, test, prod]
```

### Multi-line Strings

```yaml
# Literal block (preserves newlines)
description: |
  This is a multi-line
  description that preserves
  line breaks.

# Folded block (joins lines)
summary: >
  This is a long description
  that will be joined into
  a single line.
```

---

## Structure

### Key Ordering

Maintain consistent key ordering:
1. Identification keys (`name`, `id`, `type`)
2. Configuration keys
3. Resource definitions
4. Metadata keys (`tags`, `labels`, `annotations`)

### Comments

- Use `#` for comments
- Place comments above the key they describe
- Use comments to explain non-obvious values

```yaml
# Database connection settings
database:
  # Connection timeout in seconds
  timeout: 30
  # Maximum concurrent connections
  max_connections: 100
```

---

## Best Practices

### Anchors and Aliases

Use anchors (`&`) and aliases (`*`) to avoid repetition:

```yaml
defaults: &defaults
  timeout: 30
  retries: 3

development:
  <<: *defaults
  debug: true

production:
  <<: *defaults
  debug: false
```

### Environment-Specific Values

- Use clear naming for environment-specific files
- Prefer explicit values over complex interpolation when possible

### Validation

- Validate YAML syntax before committing
- Use schema validation where available (e.g., JSON Schema for YAML)

---

## Common YAML Pitfalls

### The Norway Problem

YAML 1.1 parsers interpret bare `NO`, `YES`, `ON`, `OFF` as booleans. This silently corrupts data like country codes:

```yaml
# ❌ WRONG - "NO" becomes false, "YES" becomes true
countries:
  - NO    # Norway? Nope, it's boolean false
  - FR

# ✅ CORRECT - always quote values that could be misinterpreted
countries:
  - "NO"
  - "FR"
```

This also affects version numbers (`1.0` becomes a float) and octal values (`010` becomes `8` in YAML 1.1). When in doubt, quote it.

### Multiline String Gotchas

`|` (literal) and `>` (folded) behave differently with trailing newlines:

```yaml
# |  preserves newlines, adds one trailing newline
# |- preserves newlines, strips trailing newline
# |+ preserves newlines, keeps all trailing newlines
trailing_stripped: |-
  no trailing newline here
```

Forgetting the chomping indicator (`-` or `+`) is a common source of unexpected whitespace in generated configs.

### Tabs vs Spaces

YAML forbids tabs for indentation. A single tab character causes a parse error. Configure your editor to insert spaces for `.yml`/`.yaml` files. Most errors from "invalid YAML" in CI are caused by an accidental tab.

---

## Schema Validation Tools

### yamllint

Catches formatting issues, truthy values, line length, and key duplicates:

```bash
# Install
pip install yamllint

# Lint a file
yamllint -d relaxed config.yaml

# Strict mode for CI
yamllint -d "{extends: default, rules: {line-length: {max: 120}}}" .
```

### VS Code YAML Extension

The Red Hat YAML extension supports JSON Schema association for autocompletion and inline validation:

```json
// .vscode/settings.json
{
  "yaml.schemas": {
    "https://json.schemastore.org/github-workflow.json": ".github/workflows/*.yml",
    "https://json.schemastore.org/databricks-asset-bundles.json": "databricks.yml"
  }
}
```

### Pre-commit Hook

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/adrienverge/yamllint
    rev: v1.35.1
    hooks:
      - id: yamllint
        args: [-d, relaxed]
```

---

## YAML in CI/CD

### GitHub Actions

Common mistakes in workflow files:

```yaml
# ❌ WRONG - expression without quotes breaks YAML parsing
env:
  VALUE: ${{ secrets.TOKEN }}

# ✅ CORRECT - always quote GitHub Actions expressions
env:
  VALUE: "${{ secrets.TOKEN }}"

# Use | for multi-line run blocks
steps:
  - name: Build
    run: |
      echo "Building..."
      make build
```

### Databricks Asset Bundles

Databricks `databricks.yml` files use variable substitution. Keep environment overrides in separate targets rather than complex anchors:

```yaml
targets:
  dev:
    default: true
    workspace:
      host: "https://dev.cloud.databricks.com"
  prod:
    workspace:
      host: "https://prod.cloud.databricks.com"
    run_as:
      service_principal_name: "prod-sp"
```

### General CI/CD Tips

- Pin action/image versions with full SHA or exact tags, not `latest`
- Use `>-` (folded, strip) for long single-line commands to avoid trailing newlines
- Validate workflow YAML locally before pushing (e.g., `act` for GitHub Actions dry runs)
