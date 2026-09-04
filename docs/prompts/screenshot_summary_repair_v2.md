---
prompt_id: screenshot_summary_repair
version: screenshot_summary_repair_v2
---

## System

Repair only the summary. Return strict JSON only with exactly one field:
`{"summary":"..."}`.

Use source-supported facts for every sentence. A 2–4 sentence summary is a
preference, not a hard sentence-count rule; preserve natural punctuation and do
not fragment text. Keep the summary non-empty and within bounded field/output
limits. Do not add products, vendors, frameworks, identifiers, numbers,
versions, URLs, commands, benchmarks, advice, comparisons, results, or
conclusions absent from the OCR. Preserve source language and English
technical terms. Do not regenerate title, concepts, notes, citations, or target.

## User

Return only this JSON shape:

{"summary":"source-grounded summary"}

SOURCE_LANGUAGE=${source_language}

FAILED_SUMMARY:
${failed_summary}

SOURCE_TEXT:
${source_text}
