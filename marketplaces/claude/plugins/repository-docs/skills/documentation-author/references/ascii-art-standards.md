# ASCII Art Standards

All architecture and diagram documentation must follow these standards for consistency.

## Character Reference

```
+  Corner or junction
-  Horizontal line
|  Vertical line
=  Major section divider (double line)
v  Downward arrow
^  Upward arrow
>  Rightward arrow
<  Leftward arrow
+---->  Horizontal flow arrow
```

## Box Styles

```
Single line box:        Double line box (headers):
+---------------+       +===============+
| Content       |       | HEADER        |
+---------------+       +===============+
```

## Flow Arrows

```
Vertical:          Horizontal:         Junction:
    |                                      |
    v              +------>           -----+-----
    |                                      |
```

## Rules

- Maximum width: **80 characters** for compatibility
- Use `+`, `-`, `|` for box borders
- Use `=` for major section dividers
- Use `v`, `^`, `<`, `>` for directional arrows
- Add descriptive labels inside or beside each box
- Group related components within bordered sections
- Align columns consistently
- Use present tense in labels

## Writing Style (applies to all documentation)

- **Present tense**: "The system processes..." not "The system will process..."
- **Active voice** where possible
- **Specific** — avoid vague language
- **Define acronyms** on first use
- **Consistent terminology** throughout all documents

## Table Formatting

```markdown
| Header 1 | Header 2 | Header 3 |
|----------|----------|----------|
| Cell 1   | Cell 2   | Cell 3   |
```

Align columns for readability in source markdown.
