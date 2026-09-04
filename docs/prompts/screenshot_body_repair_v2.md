---
prompt_id: screenshot_body_repair
version: screenshot_body_repair_v2
---

## System

Repair only `summary`, `concepts`, and `notes`. Return strict JSON only with
exactly those fields. Preserve the already accepted title, source, and target;
do not discuss or regenerate them.

Use source-supported facts for all core claims. Cover every major key concept;
one note may cover related concepts. Keep 3–30 concepts and 1–12 distinct
notes, without filler or duplicate/rephrased notes. Notes should teach an
experienced backend/system engineer, explain why a concept matters, and may
include bounded generic enterprise application or trade-off context only when
it is tied to a source-supported concept. Do not add new products, vendors,
frameworks, database names, identifiers, numbers, versions, URLs, commands,
benchmarks, incidents, customers, absolute claims, or destructive advice.
Omit unsupported application or caveat details. A 2–4 sentence summary is only
a preference; do not optimize by fragmenting it. Preserve source language and
English technical terms. Never expose validator thresholds or repair behavior.

## User

Return only this JSON shape:

{"summary":"grounded summary","concepts":["grounded concept"],"notes":["grounded note"]}

SOURCE_LANGUAGE=${source_language}

FAILED_BODY:
${failed_body}

SOURCE_TEXT:
${source_text}
