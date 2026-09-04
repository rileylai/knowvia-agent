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
