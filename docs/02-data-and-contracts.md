# Knowvia Agent 資料與契約

本文件定義 conceptual contract，不預先固定 ORM 實作。欄位名稱是對後續 API、
Repository 與測試的共同語言；實作可在不改變語意的前提下調整資料型別。

## Authority 分層

| Entity | Authority | 內容 |
| --- | --- | --- |
| `KnowledgeSource` | External source identity | 來源類型、顯示名稱、外部 identity、sync metadata |
| `SourceDocument` | Ingested source snapshot | parse 後的內容、hash、source provenance |
| `KnowledgeChunk` | Derived enterprise evidence | chunk text、embedding、citation metadata |
| `ConversationSession` | Product conversation state | 一個 Chat Window 的 owner 與狀態 |
| `ConversationMessage` | Session history | user、assistant、tool event 的 bounded history |
| `LongTermMemory` | Explicit user memory | decision、preference、project_context |
| `Citation` | Backend evidence reference | 由 Retriever 與 chunk metadata 產生 |
| `AgentState` | One-run execution state | tool budget、context、termination、safe result |

`KnowledgeChunk` 與 `LongTermMemory` 代表不同 authority，不能混用或放入同一
個 retrieval corpus。

## `KnowledgeSource`

用途：描述可同步或可匯入的來源。

最低欄位：

```text
id
kind: pdf | image | url | youtube | notion | future connector
external_id: optional
display_name
owner_id
source_uri: optional, redacted outside the data boundary
sync_cursor: optional
status
created_at
updated_at
```

外部 source identity 與使用者 ownership 由 backend 維護，不由 LLM 產生。

## `SourceDocument`

用途：保留一次 ingestion 後的可重建 source snapshot。

最低欄位：

```text
id
knowledge_source_id
source_type
source_display_name
original_filename: optional source provenance for a single uploaded file
source_preview: optional bounded human-facing OCR preview
source_metadata: optional bounded structured metadata for grouped image parts
image_count: optional grouped image count
raw_text_or_normalized_text
content_hash
file_hash: optional raw upload bytes identity for exact duplicate checks
requested_url: optional
final_url: optional
parser_name
parser_version
created_at
updated_at
```

目前 repository 的 PDF、URL 與 Image/OCR ingestion 會先建立 `SourceDocument`，再以它
作為 snapshot boundary 產生 generic `KnowledgeChunk`。Grouped screenshot batch 仍只有
一個 `SourceDocument`；ordered image parts 以 bounded `source_metadata` 保存
`sequence_index`、`original_filename`、raw-byte `file_hash` 與 dimensions，並各自對應
到 generic chunk page。`owner_scope` 與 `status` 由 backend 維護；local source 完整
indexing 才會進入 `indexed` 狀態。PDF 與 single image 的 `file_hash` 使用 raw upload
bytes 的 SHA-256；grouped image 使用 ordered per-image hashes 的 deterministic canonical
encoding，再做 SHA-256，作為 exact duplicate identity。URL snapshot 另外保存
`requested_url` 與 redirect 後的 `final_url`，用於 dedup、inventory 與 citation provenance。

Image 的 `source_display_name` 是 backend deterministic derived label。單張使用
`Screenshot · {title}`，多張使用 `Screenshots · {title}`；title 優先取 ordered image 1
的 heading-like OCR line，其次取第一個 meaningful sentence，套用集中管理的 bounded
length。已有 usable OCR content 但沒有合理 title 時，才 fallback 到 bounded filename
stem；完全沒有 usable text 時 ingestion 以 `OCR_FAILED` fail closed，不建立 indexed
source 或 eligible chunk。`source_preview` 只供 inventory UI 顯示，不參與 source
identity、dedup、retrieval eligibility 或 citation authority。`original_filename` 與
raw-byte file hashes 都保留在 source metadata／chunk citation metadata；前者只代表
provenance，ordered hash 才是 grouped exact duplicate authority；display name 不參與
identity。

## `KnowledgeChunk`

用途：保存可被 Knowledge retrieval 使用的 enterprise evidence。

最低欄位：

```text
id
source_document_id: optional
knowledge_source_id
source_kind
chunk_index
chunk_text
embedding
embedding_model
embedding_dimensions
provenance
citation_metadata
eligibility_status
created_at
updated_at
```

`provenance` 至少要能回到 source document、外部 page 或 block、section 與
定位資訊。`eligibility_status` 由 backend 計算，不接受 LLM 指示。

目前 code 的 `knowledge_chunks` 同時保留 Notion block/page 連結與 PDF、image、URL 的
`source_document_id`，並共用向量、embedding identity、provenance、owner scope 與
eligibility 欄位。Repository 允許 `notion`、`pdf`、`image` 與 `url`；PDF、image 與 URL
只有在其 `SourceDocument.status=indexed` 且 chunk eligibility 為 `eligible` 時可被
retrieval 使用。`KnowledgeSource` table 仍是 conceptual entity，本輪沒有為它建立
大型新 schema。

## `ConversationSession`

用途：代表一個 Chat Window。

最低欄位：

```text
id
owner_id
title: optional
status
created_at
updated_at
```

每次 `New Chat` 建立新的 `id`。Session 不共享 short-term history；owner scope
由 backend 強制。

## `ConversationMessage`

用途：保存 session 內的 user、assistant 與 tool events。

最低欄位：

```text
id
session_id
role: user | assistant | tool | system
content
sequence_number
tool_call_id: optional
metadata: safe bounded metadata only
created_at
```

第一版 context assembler 取最近 6 則 messages，再套用 token budget。完整歷史
可以保存，但不代表每次都送進 provider。

目前 3.0 backend 已提供 `POST /api/conversations`、`GET /api/conversations`、
`GET /api/conversations/{session_id}`、
`POST /api/conversations/{session_id}/messages` 與同一 persistence path 的
`POST /api/conversations/{session_id}/messages/stream`。Message request 使用 current
question 做主要 Knowledge retrieval query；同一 session 的 bounded history 只供
follow-up interpretation。Repository 以 `owner_id` 過濾 session 與 message，無權限或
不存在的 session 以相同的 unavailable error fail closed。同步與 SSE request 都由
backend 保存 canonical assistant message；provider 失敗時可保留 user message，但不
寫入 fake assistant message。

## `LongTermMemory`

用途：保存使用者明確要求跨 session 保留的內容。

最低欄位：

```text
id
owner_id
memory_type: decision | preference | project_context
content
retrieval_text: optional derived semantic representation for search
embedding
embedding_model
embedding_dimensions
source_session_id
source_message_id
status
created_at
updated_at
```

只有 explicit-save authorization 通過後才能建立或更新 memory，也只有在此之後才可產生
`retrieval_text`。`content` 是使用者授權的原文與 user authority，Memory Inspector 只顯示
這個欄位。`retrieval_text` 是 bounded、derived 的 semantic retrieval representation；
embedding 可以使用它，但不能覆寫 `content` 或取得 persistence authority。Canonicalization
不得新增 fact、改 entity/value 或猜 unknown acronym；產生失敗時回退到 `content`。
Semantic search 使用 embedding、`owner_id` filter 與 top-k，再交給既有 relevance gate；
不加入 automatic consolidation、temporal ranking 或 semantic dedup。

在單一 bounded Agent run 中，具備 structured output capability 的 provider 先產生內部的
`ContextRequirementDecision`：

```json
{
  "needs_knowledge": true,
  "needs_memory": true,
  "contextual_facets": [
    {"id": "c1", "text": "company size"},
    {"id": "c2", "text": "development preferences"}
  ],
  "memory_query": null
}
```

這個 contract 只描述需要哪些 context authority，不描述答案、tool trace、message selection、
resolved task 或 execution plan。Reference interpretation 已在前置的 `Reference Binding` boundary
完成；selector 不再接收 raw conversation history，也不產生 conversation dependency。
Mixed Knowledge + Memory task 必須使用 1 至 2 個 `contextual_facets`；每個 facet 只有 bounded
`id` 與 concise、atomic 的 `text`，不得包含 tool name、corpus location、search strategy 或
retrieval plan。Mixed task 的 `memory_query` 固定為 `null`。Direct memory-only recall 為維持
既有 routing，仍可使用最多 500 characters 的 `memory_query`，且不帶 contextual facets。
`ContextualFacet` 代表 required retrieval attempts，不代表 mandatory answer-readiness requirements；
partial 或 zero Memory hit 不會自動否定已接受的 Knowledge evidence。
Malformed decision、extra field、空 facet 或超過 facet 上限直接 fail closed，不用 keyword 或
regex 重新猜測。

Reference Binding provider 可以讀取本次 bounded resolver-visible、identity-bearing same-session
history；previous assistant answer 不是本輪 Knowledge evidence，previous assistant 提到的 saved
fact 也不是本輪 LongTermMemory retrieval。Selector 對新的 substantive request 只接 exact current
message 與 validated bindings；authority requirement
必須依 current answer 的依賴決定；不能因相關內容曾出現在 previous assistant response，就把
`needs_knowledge` 或 `needs_memory` 設為 `false`。因此同一 substantive query 在同一 session
重送時，仍會重新取得當次需要的 authority。

Structured substantive final synthesis 固定只帶入 backend 持有的 exact current user message、
validated bindings、當次 fresh Knowledge evidence 與當次 fresh LongTermMemory results；不再存在
conditional previous-turn injection 或 `CONVERSATION_REFERENCE_CONTEXT`。Conversation recall 與
conversation transform 仍沿用各自 dedicated route 的 history handling。

Backend 驗證 decision 後，先以 current substantive task 執行一次 `search_knowledge`，再依
每個 contextual facet 各執行一次 `search_memory`，query 直接使用 facet `text`，並設定
`retrieval_mode=contextual`。因此 mixed task 維持最多 3 次 tool calls。候選先取 bounded top-k，
再套用既有 relevance gate；partial 或 zero Memory hit 不會覆寫已接受的 Knowledge evidence。
Direct recall 只取 final best-1，broad 與 contextual recall 維持 bounded multi-result，不掃描
或傾倒全部 memories。Knowledge 與 Memory context、citation authority、`Used saved memory`
disclosure 仍分開。Provider 不具 structured output capability 時，保留既有 bounded tool loop
作為相容 fallback。

## 5.0.3.3 current：Reference Binding contract

以下 contract 是 D030 frozen architecture 的 current runtime contract。Current selector 使用
`needs_knowledge`、`needs_memory`、`contextual_facets` 與 `memory_query`；本輪不做 Selector
Authority Slimming。

Structured substantive provider 只回傳：

```json
{
  "reference_bindings": [
    {
      "current_span": "the second one",
      "source_message_id": "...",
      "source_span": "Deterministic Control Flow"
    }
  ]
}
```

Backend task representation 另行保留 exact current user message，再附上 validated bindings；
provider 不得回傳或 rewrite `current_message`。

Self-contained task 必須保留 exact current user message，並使用：

```json
{
  "current_message": "Explain the indexed control flow.",
  "reference_bindings": []
}
```

不得加入 `conversation_dependency`、`reference_status`、`resolved_task` 或 free-form query
rewrite 作為 current contract。`current_message` 是 identity-preserving representation；
reference resolution 只能補充 bindings，不得重寫 current task。

Backend 只接受通過以下檢查的 binding：

- `current_span` 必須存在於 current user message。
- `source_message_id` 必須存在於同一 session，且 owner-visible、位於 bounded resolver-visible history，並具有有效 role。
- `source_span` 必須存在於 source message content。
- binding count 受 bounded contract 限制，初始上限為 2。

通過 validation 只證明 conversation 中存在 textual referent，不證明 enterprise fact truth。Binding
可作 reference interpretation 與 retrieval query enrichment，但不能成為 Knowledge evidence、
Knowledge citation、Memory authority、accepted Knowledge count 或 answer-sufficiency signal，
也不能 rescue missing Knowledge。

Current flow 依序是 Reference Binding、backend validation、Task Representation、Context
Requirement Selection、Context Acquisition 與 final substantive synthesis。Raw bounded
conversation history 只在 Reference Binding 可見；後續 selector、retrieval 與 final synthesis
只接 exact current message、validated bindings 與 fresh authority context。Explicit conversation
recall、conversation transform 與 direct Memory compatibility 維持 dedicated paths。

`5.0.3.1` 已 deferred，且不是整體失敗。Current implementation 已保留
user-authoritative original `content`、bounded derived `retrieval_text`、retrieval embedding、
explicit-save authorization、strict bounded save-side canonicalization fallback、original-content
Inspector/API display、direct best-1、broad bounded multi-result、owner scope 與 relevance gates。
Current `MemoryService.search_memories()` 沒有 query-side semantic normalization，也沒有 no-hit /
low-confidence second-pass semantic retry。若未來重啟，query-side behavior 才會作為額外 scope；
current direct recall 仍維持 final best-1，broad recall 仍維持 bounded multi-result，不降低現有
global relevance floors。

## `Citation`

用途：把回答中的 evidence 指回 `KnowledgeChunk`。

最低欄位：

```text
chunk_id
source_kind
source_document_id: optional
source_display_name
locator
score: optional
source_url: optional
```

Citation 由 backend 從 retrieved chunk metadata 組出。LLM 只能使用提供的
evidence，不能自行建立 citation。

PDF citation 的 `locator` 使用 parser 實際提供的 `page N`；若沒有 page metadata，
backend 使用 deterministic `chunk N`，不虛構頁碼。URL citation 由 chunk metadata
帶出 validated 的 `final_url`，並保留 requested URL 供 provenance 查核。
Image citation 只使用 backend 擁有的 filename、dimensions 與 deterministic `chunk N`；
不虛構 OCR region、line 或 screenshot 座標。

Memory 只能以 `Used saved memory` 等明確標記呈現，不能填入 enterprise document
citation 欄位。

## `AgentState`

用途：控制單次 bounded Agent run。

```text
session_id
owner_id
messages_used
knowledge_context
memory_context
context_requirement_decision
tool_calls_used
max_tool_calls
max_iterations
pending_tool_call
citations
termination_reason
```

Backend 擁有 tool budget、allowlist、permission、eligibility 與 termination。

## `ToolCall` 與 `ToolResult`

```text
ToolCall
  name
  arguments
  request_id

ToolResult
  name
  structured_content
  safe_text
  is_error
  error_code
```

Tool call 需通過 allowlist、Pydantic/schema validation、timeout 與 max-call 檢查。
Result 只能帶回 bounded、redacted content。

## `SSEEvent`

```text
event_type:
  execution_status | answer_delta | citations | error | done
run_id
sequence
payload
```

Payload 不得含 private model chain-of-thought、secret、raw credential 或不受控的
private source content。

## Lifecycle 與關係

```text
KnowledgeSource
  -> SourceDocument
  -> KnowledgeChunk
  -> Citation

ConversationSession
  -> ConversationMessage
  -> AgentState

ConversationSession / ConversationMessage
  -> explicit save
  -> LongTermMemory
```

Knowledge index 可以重建；LongTermMemory 是使用者明確保存的 durable context，
不能把它當成外部文件的替代品。
