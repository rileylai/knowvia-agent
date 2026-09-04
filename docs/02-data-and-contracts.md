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
raw_text_or_normalized_text
content_hash
parser_name
parser_version
source_metadata
created_at
updated_at
```

目前 repository 的 PDF ingestion 會先建立 `SourceDocument`，再以它作為 snapshot
boundary 產生 generic `KnowledgeChunk`。`owner_scope` 與 `status` 由 backend 維護；
本輪 local PDF 使用 `owner_scope=local`，完整 indexing 才會進入 `indexed` 狀態。

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

目前 code 的 `knowledge_chunks` 同時保留 Notion block/page 連結與 PDF
`source_document_id`，並共用向量、embedding identity、provenance、owner scope 與
eligibility 欄位。Repository 允許 `notion` 與 `pdf`；PDF 只有在其
`SourceDocument.status=indexed` 且 chunk eligibility 為 `eligible` 時可被 retrieval
使用。`KnowledgeSource` table 仍是 conceptual entity，本輪沒有為它建立大型新
schema。

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

## `LongTermMemory`

用途：保存使用者明確要求跨 session 保留的內容。

最低欄位：

```text
id
owner_id
memory_type: decision | preference | project_context
content
embedding
embedding_model
embedding_dimensions
source_session_id
source_message_id
status
created_at
updated_at
```

只有 explicit save 才能建立或更新 memory。第一版 semantic search 使用
embedding、`owner_id` filter 與 top-k，不加入 automatic consolidation、temporal
ranking 或 semantic dedup。

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
```

Citation 由 backend 從 retrieved chunk metadata 組出。LLM 只能使用提供的
evidence，不能自行建立 citation。

PDF citation 的 `locator` 使用 parser 實際提供的 `page N`；若沒有 page metadata，
backend 使用 deterministic `chunk N`，不虛構頁碼。

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
