# Knowvia Agent 產品規格

本文件是 product scope、MVP boundary、user-facing behavior 與 non-goal 的 canonical source。
Current implementation status 以 application code、tests、migrations、config 與 dependency
lockfile 為準；priority 與 verification evidence 請看
[`dev_state/PROJECT_ROADMAP.md`](../dev_state/PROJECT_ROADMAP.md)。

## 產品問題

企業知識分散在文件、圖片、網站、影片與 workspace page 中。使用者通常
需要反覆尋找、閱讀、整理，再把問題帶回另一個對話介面。單純的 chatbot
不能提供企業內容的來源、權限邊界與跨對話上下文。

Knowvia Agent 將這些來源轉成可檢索的 Knowledge Layer，並把企業 evidence
與 conversational context 分開管理。

## 產品定義

Knowvia Agent 是 Enterprise Knowledge Agent。它只有一個 bounded Knowledge
Agent，透過受控的 retrieval、memory 與 MCP-compatible tools 回答問題。

產品價值是：

```text
fragmented enterprise knowledge
  + conversational context
  -> grounded context that an Agent can use repeatedly
```

這不是 autonomous Agent platform，也不是 Multi-Agent system。

## 使用者與主要情境

主要使用者需要在同一個介面中：

- 管理企業知識來源。
- 查詢有 evidence 的企業內容。
- 在同一個 Chat Window 中自然追問。
- 明確保存少量跨 session 的 decision、preference 或 project context。
- 在回答中區分 enterprise citation 與 saved memory。

## Knowledge Sources

### MVP

```text
PDF
Screenshot / Image
Web URL
Notion
```

YouTube transcript 與 chat text 目前只完成 `SourceDocument` parse/normalize/persist，尚未
進入 searchable Knowledge QA；其 generic Knowledge indexing、retrieval 與 citation flow
不列為目前已完成的 MVP source path。

### Future

```text
Google Drive
Slack
Confluence
Email
SharePoint
```

每個 source 經過 deterministic parse、normalize、chunk、embed 與 index。來源
identity、provenance、eligibility 與 citation 不由 LLM 決定。

## Notion

Notion 只作為 Knowledge Source。使用流程是：

```text
Frontend
  -> list Notion pages
  -> user selects a page
  -> deterministic backend sync
  -> parse
  -> chunk
  -> embed
  -> knowledge_chunks
```

Page listing 與 sync 不透過 LLM，也不透過 MCP Agent Tool Calling。

舊 LearnLoop 的 Notion write-back、Supplement、ChangeRequest 與 AI Supplement
Zone 不是 Knowvia active product flow。既有 code 可以暫時保留，但新的
execution path 不使用它們。

## RAG

MVP retrieval contract：

```text
query
  -> embedding
  -> eligibility and metadata filtering
  -> pgvector cosine retrieval
  -> top-k
  -> lexical fallback when needed
```

保留以下行為：

- backend-owned citations
- `insufficient_info`
- 現有 pgvector foundation
- deterministic lexical fallback

目前 production retrieval freeze 為 existing pgvector cosine retrieval、relevance acceptance
與 lexical fallback；BM25、RRF、production hybrid retrieval rebuild、reranker、HyDE、
multi-query retrieval 與 semantic chunking 不屬於目前 MVP contract。Quality gate 與 evaluation
規則由 [`docs/04-quality-and-guardrails.md`](04-quality-and-guardrails.md) 維護。

## Single Agent

產品只有：

```text
Knowledge Agent
```

以下名稱代表 capability 或 service，不代表 Agent：

```text
Memory Service
Retrieval Service
Notion sync
MCP adapter
```

## MCP 與 Tool Calling

MVP Agent tools：

```text
search_knowledge
search_memory
save_memory
```

MCP 是 standardized tool boundary / adapter，不擁有 business logic。例：

```text
MCP search_knowledge
  -> Retrieval Service
  -> Chunk Repository
  -> pgvector
```

LLM 可以決定是否使用允許的 tool，以及下一個允許的 tool。Backend 必須控制：

- tool allowlist
- argument schema validation
- tool timeout
- max iterations
- max tool calls，初始上限為 3
- database write permission
- memory persistence policy
- knowledge eligibility
- citation generation
- termination

## Conversation Session

一個 Chat Window 對應一個 `conversation_session`。`New Chat` 建立新的
`session_id`。

第一版 short-term memory 使用最近 6 則 messages，並受 configurable context
與 token budget 限制。不同 session 不共用 short-term context。

## Long-term Memory

Long-term memory 跨 session 存在，但只接受 explicit save。

```text
記住，我們 production 最後使用 pgvector。
```

這類明確請求才可觸發 `save_memory`。第一版 memory types：

```text
decision
preference
project_context
```

儲存使用 embedding、`owner_id` filter、pgvector semantic similarity 與 top-k。
MVP 不做 BM25、temporal ranking、importance ranking、memory graph、semantic
dedup 或 automatic consolidation。

## Knowledge 與 Memory authority

`KnowledgeChunk` 是 external enterprise evidence，具有 source provenance、
citation metadata 與 document authority。

`LongTermMemory` 是 persistent conversational context，來自使用者明確保存的
內容。兩者不能放進同一個 retrieval corpus，也不能讓 memory citation 冒充
enterprise document citation。

Memory 可以顯示：

```text
Used saved memory
```

但不能偽裝成文件來源。

## Grounding 與 Citation

正式 citation 流程：

```text
Retriever
  -> Chunk metadata
  -> Backend Citation
```

不要信任 LLM-generated citation text。

如果企業問題的 `search_knowledge` 沒有足夠 evidence，回答必須是
`insufficient_info`。不要使用 general model knowledge 補成企業內部事實。

## Web UI 與 SSE

Target UI 至少包含 Knowledge Tab、Chat、Memory Inspector 與基本 source status。

```text
Backend
  -> SSE
  -> Frontend
```

SSE 可傳送：

```text
Searching enterprise knowledge...
Found 4 relevant sources.
Searching saved memories...
Generating answer...
answer_delta
citations
done
```

不得傳送 private model chain-of-thought。

## 狀態標記

| 能力 | 狀態 |
| --- | --- |
| Notion read、listing、page/full/incremental index | `IMPLEMENTED` |
| PDF、Image/OCR、URL generic chunk/index/retrieval | `IMPLEMENTED` |
| YouTube、chat text parse/normalize/persist | `PARTIAL`（只建立 `SourceDocument`） |
| Conversation sessions 與 short-term memory | `IMPLEMENTED` |
| LongTermMemory 與 explicit save | `IMPLEMENTED` |
| Bounded Agent loop 與 MCP adapters | `IMPLEMENTED` |
| SSE 與 Web UI | `IMPLEMENTED` |
| Telegram、Supplement、Notion write-back、RQ | `LEGACY` |

`done`、`automated_verified`、`manual_verification`、`deferred` 與 `rejected` 的 roadmap
status 及其 evidence 不在本表重複；以 [`PROJECT_ROADMAP.md`](../dev_state/PROJECT_ROADMAP.md)
為準。

## 明確非目標

```text
Multi-Agent
LangGraph migration
LangChain migration
native multimodal LLM reasoning
automatic memory consolidation
semantic memory dedup
temporal conflict resolution
memory graph
BM25/RRF/reranker rebuild
RBAC
authentication redesign
Google Drive / Slack integrations
full Parser Golden Set
production cloud deployment
```

## Success Criteria

Demo 必須能展示：

1. 使用者選取 Notion page。
2. 使用者上傳 PDF。
3. 來源完成 parse、chunk、embedding 與 indexing。
4. Agent 使用 `search_knowledge` 回答並提供 citations。
5. 同一 session 支援 follow-up。
6. 使用者明確保存一個 memory。
7. New Chat 後能透過 `search_memory` 找回該 memory。
8. 沒有 evidence 時回傳 `insufficient_info`。

Parser migration 不得阻塞 Agent MVP。Docling 只作為 high-value、time-boxed
candidate，以 3 至 5 份代表性文件與 current parser 做比較。
