# Knowvia Agent 工作流程

本文件區分 current implementation 與 target workflow。Current flow 以 code
與 tests 為準；target flow 是後續 SDD/TDD 的行為依據。

## Knowledge ingestion

### Target

```text
Source
  -> validate
  -> parse
  -> normalize
  -> SourceDocument
  -> chunk
  -> embed
  -> KnowledgeChunk
  -> searchable Knowledge Layer
```

所有 source adapter 都必須在 expensive parse 前檢查大小、格式、redirect、
pixel、page count 或其他 source-specific limits。Parser 失敗時不得寫入不完整
的可檢索 snapshot。Grouped image OCR 在 normalization 後若完全沒有 usable text，
會以現有 `OCR_FAILED` structured failure fail closed，不建立 indexed source 或
eligible chunk。

### Current

PDF、URL 與 Image/OCR 已走完整的 synchronous generic pipeline：validate、parse、normalize、
保存 `SourceDocument`、deterministic chunk、existing embedding provider abstraction、
`KnowledgeChunk` 與 production eligibility。URL 會在每次 redirect 後重新驗證，並
以 final URL 與內容 hash 做 indexed snapshot dedup。Image/OCR 會在 OCR 前驗證 decoded
image、以 raw bytes SHA-256 做 exact duplicate guard，並沿用同一套 generic retrieval；
multipart 的多張 image 會先依 frontend 確認的順序固定 `sequence_index`，再以一個
grouped `SourceDocument` 保存 ordered image parts，透過既有 generic indexing service
產生多個 page-labeled `KnowledgeChunk`，API 回傳 aggregate 與 per-image result；不提供
screenshot region/line provenance。Grouped exact duplicate 使用 ordered image hashes，
filename 只作 provenance。YouTube 與 chat text 仍只 parse/normalize 並
保存 `SourceDocument`，尚未進入 QA retrieval。

Notion page index 已能讀取 page tree、建立 deterministic paths、chunk、embed，
並寫入 Notion-derived `KnowledgeChunk`。

## Notion sync

```text
Frontend
  -> list Notion pages
  -> user selects page
  -> deterministic sync request
  -> read current page tree
  -> build paths and chunks
  -> embed complete page snapshot
  -> atomically replace derived rows
```

Full index、single-page index 與 incremental sync 都不透過 LLM 或 MCP tool
calling。Manual Notion changes 需要明確 sync，不做 always-on watcher。

Notion 是 Knowledge Source，不是 Knowledge Agent。新的 Knowvia path 不寫回
Notion；既有 Notion writer 與 Supplement flow 屬於 legacy。

## Knowledge search

```text
search_knowledge
  -> validate scope and top-k
  -> apply source ownership and eligibility filters
  -> pgvector cosine retrieval
  -> lexical fallback when configured conditions require it
  -> return evidence and citation metadata
```

Retriever 先套 eligibility 與 owner/scope filter，再排序。不能把 pending、
rejected、stale、synthetic 或不符合 source policy 的資料混入結果。

## Chat

### Target

```text
User message
  -> load conversation_session
  -> select recent short-term messages
  -> decide whether an allowed tool is needed
  -> validate ToolCall
  -> run Retrieval or Memory Service
  -> assemble bounded context
  -> generate grounded answer
  -> persist message and safe run metadata
  -> stream SSE events
```

Agent 可以在一次 run 中 chaining allowed tools，但初始最多 3 次 tool calls。
Backend 在每一步檢查 timeout、argument、permission、context budget 與 termination。

### Current

目前已有 synchronous `/api/qa`，以及以 `ConversationSession`、`ConversationMessage`
為 authority 的 conversation endpoints。首次進入 `/chat` 時，frontend 先讀取 session
list；URL 有合法 `session_id` 時載入該 session，沒有 URL 時載入 `updated_at` 最新的
session，只有成功確認 list 為空時才建立第一個 session。Active identity 使用
`/chat?session_id={session_id}`。

送出 message 時，backend 先保存 user message，再以同一 session 最近 6 則 history
與 token budget 組成 bounded context；current question 仍作為主要 retrieval query。
QA success 才保存 assistant message。Provider 失敗時不保存 fake assistant message。
Frontend session switch 先完成 backend load，成功後才替換 active identity 與 messages；
失敗時保留原本 session、messages 與 URL。Conversation session、bounded Agent loop
與 SSE 尚未實作。

## Memory search

```text
search_memory
  -> verify owner_id
  -> semantic search LongTermMemory
  -> top-k
  -> label results as saved memory
  -> add to context
```

Memory 結果不能被引用成 enterprise document citation。

## Explicit memory save

```text
User explicitly asks to remember something
  -> Agent selects save_memory
  -> backend validates type and owner
  -> persist LongTermMemory
  -> embed memory
  -> return saved status
```

第一版只接受 `decision`、`preference` 與 `project_context`。一般對話內容不會
自動轉成 persistent memory。

## Session isolation 與 New Chat

```text
New Chat
  -> create new conversation_session
  -> omit previous session messages
  -> keep owner-scoped LongTermMemory searchable
```

不同 session 不共享 short-term context。跨 session 可使用的只有通過 policy
保存的 LongTermMemory。

Frontend 的 session list 依 `updated_at DESC` 顯示，active session 只使用 visual
highlight。Desktop 使用 persistent left sidebar；mobile 使用可由 menu button 開啟的
overlay drawer。Invalid 或無權存取的 URL session 不會保留，frontend 會顯示 generic
error，並 fallback 到合法 session 或在合法 list 為空時建立第一個 session。

## Citation 與 insufficient info

```text
retrieved KnowledgeChunk metadata
  -> backend Citation
  -> answer response
```

若 evidence 不足：

```text
search_knowledge
  -> no sufficient enterprise evidence
  -> insufficient_info
  -> no fabricated enterprise claim
```

LLM-generated citation text 不具 authority。Retriever metadata 才是 citation
來源。

## SSE execution events

可發送的事件包括：

```text
execution_status
answer_delta
citations
done
```

執行狀態可呈現 search、context assembly 與 generation 的簡短摘要。不得暴露
private model chain-of-thought、provider secret 或原始私有內容。

## 錯誤與停止

以下情況由 backend 終止 run：

- tool 不在 allowlist。
- arguments 不符合 schema。
- tool timeout。
- 超過 3 次 tool calls 或 max iterations。
- owner、source scope 或 memory policy 驗證失敗。
- context 超過 token budget。
- evidence 不足而無法安全回答。

停止結果要有可測試的 `error_code`、`termination_reason` 或
`insufficient_info`，不依賴自然語言猜測。

## Legacy workflow boundary

以下流程不屬於 Knowvia active workflow：

```text
Source
  -> Supplement proposal
  -> ChangeRequest
  -> Human review
  -> Notion write-back
```

Telegram ingestion、review、queue 與 worker 也不在新 workflow。既有 code 可以
暫存，但不應被新 Agent path 呼叫。
