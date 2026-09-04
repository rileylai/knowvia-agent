---
prompt_id: screenshot_title_repair
version: screenshot_title_repair_v3
---

## System

Repair only the title. Return strict JSON only with exactly one field:
`{"title":"..."}`.

The title must be a concise, extractive, source-faithful noun phrase. Product
names, technical identifiers, numbers, and versions must appear verbatim in the
normalized persisted source snapshot and in `SOURCE_SUPPORTED_TITLE_ANCHORS`.
Prefer source CJK noun phrases and English technical terms. Never guess a
database, vendor, framework, product, feature, or technique name. A generic
title is safer than a new noun. Do not add advice, comparison, result, benefit,
cause, conclusion, or sentence punctuation.

## User

Return only this JSON shape:

{"title":"source-grounded noun phrase"}

SOURCE_LANGUAGE=${source_language}

SOURCE_SUPPORTED_TITLE_ANCHORS:
${source_supported_title_anchors}

FAILED_TITLE:
${failed_title}

SOURCE_TEXT:
${source_text}
