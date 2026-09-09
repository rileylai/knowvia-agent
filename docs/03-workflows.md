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

### Current: 5.0.3.3 Context Authority Consolidation

這是已完成 implementation 的 current workflow。Unresolved actual-provider stability 與 browser
acceptance deferred；目前下一個唯一主線 priority 是 `8.5 User-Facing Answer Quality Diagnostic`，
只做 user-facing failure diagnosis，後續 retrieval、readiness 與 synthesis work 依 evidence 再決定。
`7.0 Evaluation and Demo Hardening` 維持 `manual_verification`，Formal Browser Demo Story 保留但暫排在
`8.5` diagnosis 後。Explicit save、conversation
recall、conversation transform 與 direct Memory compatibility 先走既有 dedicated routes；其餘
substantive request 走 structured path：

```text
User message
  -> Reference Binding
  -> backend validates reference_bindings
  -> Task Representation
       exact current user message + validated reference bindings
  -> Context Requirement Selection
  -> Context Acquisition
       Knowledge + contextual Memory
  -> current pre-final sufficiency behavior
  -> final substantive synthesis
       exact current message + validated bindings + fresh Knowledge + fresh Memory
```

Bounded raw conversation history 只在 `Reference Binding` 可見；resolver 使用的 internal history
至少保留 `message_id`、`sequence_number`、`role` 與 `content`，並由 backend 先完成 session/owner
scope。Self-contained request 的 `reference_bindings` 為空，current user message 保持 exact identity。
Current path 不產生
`conversation_dependency`、`reference_status`、`resolved_task` 或 free-form query rewrite。
Backend 驗證 `current_span`、同 session 且 owner-visible 的 `source_message_id`、source
message 內的 `source_span`、valid role 與最多 2 個 bindings。

Validated binding 只證明 conversation 中存在 textual referent，可作 reference interpretation
與 retrieval query enrichment；它不能成為 Knowledge evidence、Memory authority、citation、
accepted Knowledge count 或 answer-sufficiency signal，也不能 rescue missing Knowledge。
Context Requirement Selection、後續 retrieval 與 final synthesis 都不接 incidental raw
conversation transcript。這項 restriction 不套用到明確要求 conversation evidence 的 recall
或 transform dedicated path。

### 8.4 current：Evidence Readiness（automated verified, manual pending）

Current flow 是：

```text
Accepted Knowledge Evidence
  -> Evidence Readiness
  -> backend gate
  -> Final Synthesis only when ready
```

Readiness provider 只接 exact current substantive task、validated reference bindings、accepted
Knowledge evidence 與 bounded source metadata。它不接 incidental raw transcript、previous
assistant answer、rejected chunks、raw candidate pool 或 LongTermMemory content 作為 Knowledge
evidence。若 mixed task 需要辨識 Memory-side dependency，provider 最多取得 bounded
`needs_memory` indication 與 facet labels，不取得 Memory content。`contextual_facets` 仍是
Memory retrieval dependencies，不是 Knowledge requirements。

v1 contract 只有 `{"ready": true}`。若 required Knowledge 的 accepted evidence 為零，沿用
existing zero-evidence gate，可直接回傳 insufficient result，不必呼叫 readiness provider。
其餘有 accepted evidence 的 Knowledge task 才進入 readiness。`ready=false` 時 backend 回傳
`insufficient_info=true`、`citations=[]`，不呼叫 final provider，也不 retry retrieval、增加
top-k、rewrite query 或以 Memory 取代缺少的 Knowledge。`ready=true` 時才呼叫 final synthesis，
並沿用同一批 accepted Knowledge evidence 與既有 authorized supplemental Memory。

Current structured substantive path 約有 4 個 semantic LLM calls：Reference Binding、Context
Requirement Selection、Evidence Readiness 與 Final Synthesis。`/api/qa` 為 readiness 加 final
synthesis，共 2 個。這增加 latency、token 與 cost，但不增加 Agent tool-call budget。

Readiness provider/runtime failure 沿用既有 provider error 或 contract error mapping，不偽裝成
`insufficient_info`。`qa_answer_v4` 只用於 ready path 的 final synthesis；`qa_answer_v3` 保持
不變。

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
失敗時保留原本 session、messages 與 URL。Tool-capable provider 會進入 bounded Agent
loop，最多執行 3 次 allowlisted tool calls；不支援 tool calling 的 provider 保留 QA
fallback。Streaming endpoint 會在同一個 persistence path 上發送 bounded SSE events。

具 structured output capability 的 provider 會先對 current task 產生
`ContextRequirementDecision`。Mixed Knowledge + Memory decision 會帶 1 至 2 個 bounded
`contextual_facets`，每個 facet 是一個 atomic context dependency。Backend 先以 current
substantive task 執行 `search_knowledge`，再將每個 facet `text` 原樣作為一個
`search_memory` query，並設定 `retrieval_mode=contextual`。所有 retrieval 仍計入最多 3 次
tool calls；required tools 超過 budget 時 fail closed。純 Knowledge factual question 不強制
Memory，direct memory-only recall 保留既有 `memory_query` routing，也不強制 Knowledge。

Reference Binding provider 可使用 bounded resolver-visible history 理解 reference；selector 只
接 exact current message 與 backend-validated bindings。Previous assistant answer 不具 Knowledge
authority，previous assistant 提到的 saved fact 也不取代當次 LongTermMemory retrieval。新的
substantive request 即使與前一輪完全相同，也依當次需求重新選擇並取得 authority；只有既有
bounded conversational transform 才使用 previous answer 作 transformation target。

Runtime 固定以 exact current message、validated bindings、fresh Knowledge evidence 與
fresh saved-memory context 組裝 structured substantive final provider request，不再加入
previous user/assistant transcript 或 `CONVERSATION_REFERENCE_CONTEXT`。Previous conversation
只透過 validated binding 參與 reference interpretation，不是 Knowledge、Memory、sufficiency
或 citation authority。Knowledge 是
enterprise claim 與 citation 的唯一 authority；Memory 只能作 optional personalization
context。部分或全部 contextual Memory miss 時，只要已接受 Knowledge evidence，final
provider 仍可產生 Knowledge-only answer；Memory 不得取代缺少的 required Knowledge。
Malformed selector 或 provider error 不執行未授權的 retrieval。Provider 不具 structured
output capability 時，保留既有 bounded tool loop。

## Memory search

```text
search_memory
  -> verify owner_id
  -> embed natural user query
  -> semantic search LongTermMemory
  -> top-k
  -> label results as saved memory
  -> add to context
```

Memory 結果不能被引用成 enterprise document citation。

## Explicit memory save

```text
User explicitly asks to remember something
  -> backend validates ExplicitSaveIntent
  -> deterministic trusted save through MemoryService
  -> build bounded retrieval_text
  -> embed retrieval_text
  -> persist original content and retrieval_text
  -> return saved status
```

第一版只接受 `decision`、`preference` 與 `project_context`。Public conversation 的
explicit save 不依賴 provider tool selection，也不進 final-answer generation。一般對話內容
不會自動轉成 persistent memory。`save_memory` tool 保留給 MCP 與其他 tool execution
boundary；沒有 trusted explicit-save authorization 時仍 fail closed。Agent 可用自然問題搜尋
已保存的 personal、company 或 project context，不要求 query 包含 `memory`、`remember` 或
`saved`。

Mixed explicit-save 加 substantive task 目前沒有 typed split contract，本 slice 不支援；
目前 parser 會將 `記住` 後的 remainder 視為同一段 memory content。

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

目前 answer delta 是 backend 在 provider 完成完整回答後切出的 bounded Unicode-safe
chunks，不是 provider-native token streaming。SSE replay、reconnect 與 distributed
cancellation 不屬於目前 MVP contract。

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
