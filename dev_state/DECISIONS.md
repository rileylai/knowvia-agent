# Knowvia Agent 決策記錄

本文件記錄目前有效的產品與工程決策。若要改變其中一項，先新增明確的 decision，
再修改相關 spec。

| ID | Decision | Reason | Trade-off | Status |
| --- | --- | --- | --- | --- |
| D001 | 正式產品名稱是 Knowvia Agent | 統一產品與 repository 語義 | 舊 LearnLoop 名稱只保留歷史標記 | Accepted |
| D002 | 產品定位是 Enterprise Knowledge Agent | 聚焦企業知識與可追溯回答 | 不承諾通用 autonomous platform | Accepted |
| D003 | 產品只有一個 bounded Knowledge Agent | 降低 execution 與 permission 複雜度 | capability 不拆成獨立 agents | Accepted |
| D004 | 不建立 Multi-Agent system | 不需要 agent-to-agent routing | 複雜協作場景留給 future | Accepted |
| D005 | Notion listing 與 sync 是 deterministic backend operation | scope 與 page identity 必須可控 | 不由 LLM 決定 sync 範圍 | Accepted |
| D006 | Knowledge ingestion 是 deterministic | parse、chunk、embed 與 eligibility 需要可驗證 | parser 不由 Agent 自由改寫 | Accepted |
| D007 | MCP 是 adapter boundary | 對外提供標準 tool contract | MCP 不擁有 business logic | Accepted |
| D008 | Backend 擁有 permission、validation、persistence、citation | LLM 不應持有安全權限 | Agent loop 需要較多 backend code | Accepted |
| D009 | KnowledgeChunk 與 LongTermMemory 分開 | external evidence 與 conversational context authority 不同 | 需要兩套 model 與 retrieval scope | Accepted |
| D010 | Long-term memory 只接受 explicit save | 避免未確認的對話內容永久保存 | 使用者要多一次明確操作 | Accepted |
| D011 | Long-term memory 使用 vector semantic search | 先完成簡單、可控的 recall path | 不做 temporal/importance ranking | Accepted |
| D012 | Citation 由 backend 產生 | source locator 必須能被驗證 | LLM 不能自行輸出 citation authority | Accepted |
| D013 | 保留 `insufficient_info` | 無 evidence 時避免企業幻覺 | 部分問題不提供答案 | Accepted |
| D014 | SSE 是 frontend streaming transport | UI 需要 execution status 與 answer delta | SSE 不負責 Agent policy | Accepted |
| D015 | Redis/RQ 不列入 Knowvia MVP core | Telegram queue 不是新產品核心 | async/background capability 延後 | Accepted |
| D016 | Parser Golden Set deferred | 不讓 parser governance 阻塞 Agent MVP | 早期 parser 品質以小型 gate 驗證 | Accepted |
| D017 | Parser migration 是 time-boxed、non-blocking | candidate regression 不應拖延主線 | 必要時回到 current parser | Accepted |
| D018 | Runtime logs 使用英文 | 便於 production troubleshooting 與 aggregation | 文件語言與 log 語言不同 | Accepted |
| D019 | Project documentation 使用繁體中文 | 統一團隊閱讀與 review 語境 | 技術名詞保留英文 | Accepted |
| D020 | 文件寫作使用 `/avoid-ai-writing` | 降低空泛與模板化文字 | 修改文件多一個必要檢查 | Accepted |
| D021 | Foundation cleanup 之後，每個主要 feature 以 end-to-end vertical slice 實作，並提供最小 frontend surface 供人工驗收 | 避免 backend 累積不可見行為，讓使用者直接確認每次迭代的產品行為 | Frontend 會較早出現，初期 UI 可能很薄，但能提早發現 API、workflow 與產品行為問題 | Accepted |
| D022 | Roadmap 使用 stable hierarchical major.minor IDs，並允許 `N.M.K` verification follow-up | 驗證後可 append `2.1.1`、`2.1.2` 等新工作，不必重編後續 roadmap | 新增工作需要維持 ID hierarchy 與 parent slice 的對應關係 | Accepted |
| D023 | Codex 依 task 讀取最少必要文件，不預先 preload 全部 project documentation | 降低 context/token 使用，也避免 legacy 或 irrelevant information 干擾目前 task | 跨多個 concern 的 task 需要先判斷並選取對應 source of truth | Accepted |
| D024 | LongTermMemory 的 original `content` 是 user authority；derived `retrieval_text` 只供 semantic retrieval，且只有 explicit-save authorization 通過後才可產生 | 防止 retrieval representation 取得 persistence authority，並維持 `Save strict; recall forgiving.` | Semantic canonicalization 必須 bounded，不得新增 fact、改 entity/value 或猜 unknown acronym；failure fallback 到 original `content` | Accepted |
| D025 | 當 saved company/project context 能 materially improve 當前 Knowledge task 時，單一 bounded Agent 可在同一 run 依序選擇 `search_knowledge` 與 `search_memory`；兩者 authority 維持分離 | 讓回答能以 relevant saved context 作 bounded application，而不把 memory lookup 擴散到每個 Knowledge query | 需要 task-oriented contextual memory query 與最多 3 次 tool calls；memory 不成為 citation，Knowledge-only 不強制 memory | Accepted |
| D026 | `search_knowledge` 完成後，bounded runtime 必須在下一輪 provider decision 前重新提供 original user task、Knowledge availability 與 contextual memory rule；若 provider 明確使用 `retrieval_mode=contextual`，必須保留其 task-oriented query，不受 direct-recall metadata 覆寫 | Live provider 可能在取得 Knowledge evidence 後直接 final，導致 contextual saved facts 未被應用；post-Knowledge decision contract 將 runtime capability 與 provider tool-selection reliability 分開 | 增加一段 bounded system decision context；provider 仍判斷 material relevance，Knowledge-only 可直接 final，且不建立 classifier、planner 或第二個 Agent | Accepted |
| D027 | 具備 structured output capability 的 provider 先產生 typed `ContextRequirementDecision`；backend 驗證後 deterministic 執行 required Knowledge / Memory capability，再以分離 context 做一次 final generation。Mixed Knowledge + Memory decision 使用最多 2 個 bounded `contextual_facets`，direct memory-only recall 保留 `memory_query` compatibility。Structured decision 只描述 authority requirements，不包含 save、planner 或 tool trace | `tool_choice=auto` 讓同一 mixed query 在 Knowledge 後可能直接 final，造成 live routing 不穩定；free-form mixed Memory query 又會造成 embedding similarity 浮動 | 增加一次 internal selector provider call 與 bounded response format；mixed facet text 直接作 contextual Memory query；Malformed decision fail closed，provider 不支援 structured output 時保留既有 bounded tool-loop fallback；Knowledge citation 與 Memory disclosure 維持分離 | Accepted |
| D028 | Same-session history 可協助 selector 理解 reference 與 task intent，但 previous assistant answer 不具 current Knowledge authority，previous assistant mention of saved facts 也不取代 current LongTermMemory retrieval；新的 substantive query 必須依當次需求重新選擇 authority，既有 conversational transform 仍可使用 previous answer | 避免同一 substantive query 在同一 session 的第二輪因 history 已有答案而省略 Knowledge/Memory retrieval，最後錯誤落入 `insufficient_info` | 需要 selector prompt contract 與 repeated-query regression；不加入 query equality、keyword/regex forcing、company-specific special case 或 general transform hardening | Accepted |
| D029 | Knowledge Evidence Acceptance Gate 使用 bounded pgvector candidate pool 與 inclusive `knowledge_relevance_floor=0.30`；只有通過 eligibility 與 floor 的 chunk 才能成為 Knowledge evidence、context 與 citation authority | top-k nearest chunk 不保證與 query 相關；需要由 backend deterministic 判斷 grounding sufficiency，讓 unsupported enterprise query 回傳 `insufficient_info` 而不是交給 final provider 猜測 | 需要依現有 positive/mixed/negative score inspection 維護 floor，並保留 lexical `score > 0` fallback semantics；不引入 LLM reranker 或大型 retrieval redesign | Accepted |
| D030 | `5.0.3.3` target：Incidental raw conversation history 只可出現在 bounded reference resolution；reference resolution 產生 backend-validatable reference bindings，不產生 rewritten natural-language task。Structured substantive final synthesis 不接 incidental raw history；explicit conversation recall/transform 維持 dedicated authority path | 將 history 的 reference interpretation 與 enterprise Knowledge、saved Memory、citation、sufficiency authority 分開，避免 provider 直接把 transcript 帶入後續 retrieval 或 final synthesis | 需要 bounded resolver-visible history、同 session/owner/role/span validation 與初始最多 2 個 bindings；`.3` 先保留 current selector 的 `needs_knowledge`、`needs_memory`、`contextual_facets` 與 `memory_query` | Accepted |
