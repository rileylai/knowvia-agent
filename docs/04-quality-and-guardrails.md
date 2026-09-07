# Knowvia Agent 品質與 Guardrails

## Grounding

企業問題必須先取得 Knowledge evidence。Agent 可以說明 evidence 不足，但不
能用 general model knowledge 補成企業內部事實。

```text
question
  -> search_knowledge
  -> eligibility-filtered evidence
  -> backend citations
  -> grounded answer
```

`KnowledgeChunk` 是 external enterprise evidence。`LongTermMemory` 是使用者
明確保存的 conversational context。兩者不能混入同一個 retrieval corpus。

## Backend-owned citation

Citation 由 Retriever 使用 chunk metadata 產生。LLM-generated citation text
不具 authority，也不能建立不存在的 source、page、section 或 locator。

Memory 只能以 `Used saved memory` 等明確標記顯示。Memory 不得冒充 enterprise
document citation。

## `insufficient_info`

當 `search_knowledge` 沒有足夠 evidence 時，backend 回傳：

```text
insufficient_info
```

此結果不得包含看似確定的企業內部答案，也不得產生沒有 evidence 的 citation。

## Tool allowlist

MVP 只允許：

```text
search_knowledge
search_memory
save_memory
```

`fetch_source` 是 bonus。Notion listing 與 sync 不由 Agent tool calling 執行，
而是 deterministic backend operation。

每次 ToolCall 都必須經過：

1. allowlist 檢查。
2. argument schema validation。
3. owner、scope 與 permission 檢查。
4. timeout。
5. bounded result 與 redaction。

LLM 不能新增 tool、提高權限、改變 source eligibility 或直接寫 database。

## Tool loop bounds

初始每次 Agent run：

```text
max tool calls = 3
```

同時設定 max iterations、context/token budget 與單一 tool timeout。以下情況
必須停止：

- tool call 超過 allowlist。
- arguments 無法通過 schema。
- tool timeout 或 provider failure。
- 超過 tool call 或 iteration 上限。
- context 超過 budget。
- owner、scope 或 persistence policy 失敗。
- retrieval evidence 不足，無法安全回答。

停止結果需有可測試的 `error_code`、`termination_reason` 或
`insufficient_info`。

## Memory persistence policy

Long-term memory 只在使用者明確要求保存時建立。第一版允許：

```text
decision
preference
project_context
```

一般對話、tool result 或模型推測不會自動寫入 LongTermMemory。寫入前由
backend 驗證 owner、類型、內容長度與 persistence policy。

Exact duplicate 可以被拒絕或回傳既有 memory。MVP 不做 semantic dedup、
automatic consolidation、memory graph、importance ranking 或 temporal ranking。

Memory 可另外保存 bounded `retrieval_text` 作為 semantic search representation。它只能在
explicit-save authorization 通過後產生，不能改寫 user-authoritative `content`，也不能取得
persistence authority。Planned `5.0.3.1` representation slice 的 canonicalization 不得新增 fact、改
entity/value 或猜 unknown acronym；產生失敗時，embedding 回退使用原始 `content`。
Memory search 可接受自然 user query，不要求 query 包含 `memory`、`remember` 或 `saved`。

Query-side semantic normalization 只作 no-hit 或 low-confidence fallback。Retrieval candidate
先取 top-k，再套用既有 relevance gate；direct recall 只回傳 final best-1，broad recall 維持
bounded multi-result。既有 global relevance floors 不因單一案例降低，也不建立大型 hard-coded
synonym dictionary。English/Chinese cross-language recall 與 unrelated-memory fail-closed
behavior 是 `5.0.3.1` follow-up 的 regression contract。

當 saved company/project context 能 materially improve current Knowledge task 時，bounded
Agent 可以同一 run 使用 `search_knowledge` 與 `search_memory`。Contextual memory search
必須使用 task-oriented query，候選經既有 relevance gate 後才進入 bounded context；不得因
mixed flow 降低既有 relevance floors，也不得 dump all memories。Knowledge-only query 不
強制 memory，memory-only query 不強制 Knowledge。Knowledge citation 只能來自 Knowledge
metadata；Memory 只以 `Used saved memory` 表示，兩者 authority 不得混合。

Context requirement routing 使用 typed `ContextRequirementDecision`，只允許
`needs_knowledge`、`needs_memory`、`conversation_dependency`、bounded `contextual_facets` 與
direct recall 用的 `memory_query`。`conversation_dependency` 是 required enum，只允許 `none`
與 `required`，不可用 default 代替 provider 欄位。Mixed task 的 facets 上限為 2，facet text 必須是 bounded、single-line 的
atomic context dependency；每個 facet 會直接成為一個 contextual Memory query。Backend 先
schema validate，再 deterministic 執行 required capability；unknown field、錯誤型別、空
facet、超過上限、空 direct memory query 與 provider failure 都 fail closed。Provider 不具
structured output capability 時，既有 bounded tool loop 仍可作相容 fallback，但不以 regex
或 keyword 代替 selector。

Current pre-final guard 以 accepted Knowledge evidence 作為 required Knowledge 的 deterministic
gate。Contextual Memory 是 optional supplemental context；部分或全部 facet 沒有 relevant hit
時，仍可用 accepted Knowledge 產生 Knowledge-only answer，不得因此回傳 request failure。若
required Knowledge 缺失，Memory hit 也不能 rescue，結果仍是 `insufficient_info` 與 zero
citations。Whole-answer semantic sufficiency 目前仍是 `HYBRID`，尚未由單一 backend verifier
完整接管。

Regression 必須同時驗證 decision flags、實際 tool execution、owner scope、tool count 與
final context authority。測試只記錄 bounded decision labels、tool names、availability 與
finalization reason，不記錄 private source content 或 raw provider response。

Selector 可以參考 same-session history 來理解 current task，但 previous assistant answer
不等於 current Knowledge evidence，previous assistant 提到的 saved fact 也不等於當次
LongTermMemory retrieval。新的 substantive query 不得因 history 已包含相同內容而省略必要
authority；若 decision 要求 Knowledge，Backend 必須先取得當次 Knowledge evidence，只有實際
無 evidence 時才可進入 `insufficient_info`。既有 conversational transform 仍可只使用
previous answer，不在本輪擴張 transform classifier。

Structured substantive final synthesis 在 `conversation_dependency=none` 時不帶入 previous
conversation；`required` 時只帶入 backend deterministic 選出的最近 completed user/assistant
pair，並以 `CONVERSATION_REFERENCE_CONTEXT` 標記。Previous conversation 只供 reference 或
intent interpretation，不會成為 Knowledge、Memory、citation 或 sufficiency authority。Final
provider 仍只依 current task、accepted Knowledge、當次 retrieved Memory 與必要 reference
context 產生答案。相同 substantive query 重送時也使用 fresh authority。這項 backend isolation
不套用到 selector、conversation recall 或 transform path。

### 5.0.3.3 target authority boundary

以下 boundary 是下一個 planned implementation slice 的 frozen target。Incidental raw conversation
history 只能出現在 bounded `Reference Binding` boundary；後續 Context Requirement Selection、
Knowledge/Memory retrieval 與 final substantive synthesis 都不得直接接收 raw conversation
transcript。

```text
User message
  -> Reference Binding
       raw bounded conversation history visible only here
  -> reference_bindings = [] / [...]
  -> backend validation
  -> exact current user message + validated bindings
  -> Context Requirement Selection without raw history
  -> Knowledge + contextual Memory acquisition
  -> current pre-final sufficiency behavior unchanged
  -> final synthesis with exact message, bindings, fresh Knowledge and fresh Memory
```

Reference validation 只確認 textual referent 存在於 owner-visible、同 session、bounded history
中的合法 message，不確認 enterprise fact truth。Binding 可作 reference interpretation 與
retrieval query enrichment，但不能成為 Knowledge evidence、citation、Memory authority、accepted
Knowledge count 或 answer-sufficiency signal，也不能 rescue missing Knowledge。Target representation
不引入 `conversation_dependency`、`reference_status`、`resolved_task` 或 free-form query rewrite；
self-contained request 保留 exact current user message，使用空的 `reference_bindings`。

### Evidence Readiness：post-5.0.3.3 follow-up

Current `knowledge_relevance_floor=0.30` 只表示 chunk relevance acceptance，不表示 whole-answer
sufficiency。Current runtime behavior 是：Knowledge required 且 accepted Knowledge 為 0 時，
backend deterministic 回傳 `insufficient_info`；accepted Knowledge 大於 0 時，交給 final
provider，而 provider 仍可能輸出 `INSUFFICIENT_INFO`，此時會成為 `provider_contract_error`。
這個 semantic sufficiency authority 目前是 `HYBRID`。

Evidence Readiness 會在 `.3` 完成後另行定義 answer-readiness authority、bounded coverage/readiness
contract 與 fail-closed policy。本輪不設計 verifier，也不把 retrieval acceptance 寫成 whole-answer
readiness。

SSE 只公開實際執行的 tool phase 與一次 final `generating` status。Provider 在 Knowledge
result 後的 internal continuation decision 不另外顯示 `generating`，避免 mixed flow 出現
`Generating → Searching saved memory → Generating` 的誤導狀態；SSE 仍不得包含 reasoning
或 tool arguments。

產品原則：`Save strict; recall forgiving.`

## Session isolation

每個 Chat Window 對應一個 `conversation_session`。Short-term context 只來自
該 session 的最近 6 則 messages，並受 token budget 限制。

`New Chat` 建立新的 `session_id`，不能讀取前一個 session 的 short-term history。
跨 session 可使用的只有 owner-scoped、通過 explicit-save policy 的
LongTermMemory。

## Knowledge eligibility

Retriever 在排序前套用：

- source ownership。
- source kind policy。
- page、section 或其他 metadata scope。
- chunk completeness 與 index status。
- production eligibility。

對 PDF、URL 與 Image 而言，`SourceDocument.status` 必須是 `indexed`，且所有 derived
chunks 必須在完整 embedding persistence 後才標記為 `eligible`。Indexing failure 的
snapshot 保持不可檢索。Image 另須通過 MIME、byte size、batch、pixel、decoder 與
non-empty OCR validation；OCR failure 不建立可檢索 snapshot。

URL ingestion 只接受 HTTP/HTTPS 的 HTML、XHTML 或 plain text。Backend 會限制 URL
長度、redirect 次數、response 大小與 fetch timeout，並在每次 redirect 後重新檢查
DNS 結果，拒絕 localhost、loopback、private 與 link-local address。Validation
失敗時不建立可檢索 snapshot。

Pending、rejected、stale、synthetic、uncommitted 或不符合 source policy 的
資料不得進入 production retrieval。

## Prompt injection boundary

User content、source text、retrieved text 與 memory content 都是不可信資料。
Prompt delimiter 只能幫助區分資料，不能提供 authorization。

這些內容不能：

- 取得新 tool。
- 改變 target 或 owner。
- 繞過 memory save confirmation。
- 改變 citation。
- 修改 retrieval eligibility。
- 改變 workflow state。

## No chain-of-thought exposure

SSE 與 API response 可以提供有限 execution status，例如 searching、found
sources、generating answer、answer delta、citations 與 done。不得向使用者
輸出 private model chain-of-thought。

## Sensitive data

以下內容不得進入一般 log、metrics、error response 或 committed fixture：

- API keys、provider token、Notion token、完整 database URL。
- private source text、OCR text、Notion content。
- private page identity，除非該 identity 是必要的 bounded citation metadata。
- raw provider response、embedding input、vector 與 callback secret。

Runtime log 使用英文；產品文件使用繁體中文。

## Evaluation contract

| 類別 | 必測行為 |
| --- | --- |
| Retrieval | source eligibility、owner/scope filter、top-k、fallback |
| Grounding | answer 只使用提供的 enterprise evidence |
| Citation | citation 來自 backend metadata，不信任模型文字 |
| Insufficient info | 無 evidence 時回傳 `insufficient_info`，不捏造答案 |
| Conversation | same-session follow-up 能看到最近 context |
| Session isolation | New Chat 不帶入前一 session messages |
| Persistent memory | explicit save 後跨 session 可找回 |
| Memory authority | memory 不冒充 document citation |
| Tool selection | 只執行 allowlisted tool |
| Tool chaining | 最多 3 次 tool calls，結果受限 |
| Tool safety | schema、owner、timeout 與 permission 失敗時 fail closed |
| Termination | 超過 budget 或 evidence 不足時停止 |
| SSE | 只輸出允許的 execution event、delta、citation 與 done |

測試預設使用 fixtures、injected clients 與 isolated database。Live Notion、
provider、PostgreSQL 或 Telegram checks 必須明確 opt-in，且不得接觸 production
資源。

## 7.0 Deterministic Golden Set

`eval/golden_set.yaml` 以 29 個小型 scenario 覆蓋 Knowledge retrieval、grounding、
citation、conversation、session isolation、explicit memory、authority separation、
bounded Agent、Knowledge/Memory contextual routing、structured context requirement、tool
safety、native MCP 與 SSE lifecycle。Runner 使用 scripted provider
與 in-memory fixtures，不比較完整自然語言答案，也不需要 live LLM 或 private source。

正式 command：

```bash
uv run --no-env-file --frozen python -m eval.run_agent_eval \
  --report /tmp/knowvia-eval.json
```

Core scenario 全部通過才算 regression pass。Report 只包含 scenario id、category、
bounded check name、結果與失敗原因，不輸出 prompt、raw provider response、source
chunk 或 embedding。
