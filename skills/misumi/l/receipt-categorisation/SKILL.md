---
name: receipt-categorisation
description: Categorise receipt lines while preserving ambiguous items.
category: receipt
tags: [misumi, l, receipt]
status: published
confidence: 1.0
source: first-party
---
## When to Use
Use for a locally provided receipt or transaction list.
## Procedure
1. Preserve raw merchant and line text.
2. Assign narrow categories with confidence.
3. Leave ambiguous items uncategorised with a question.
## Pitfalls
- Do not infer sensitive purchases beyond the text.
## Verification
- Totals reconcile and uncertain lines remain visible.
