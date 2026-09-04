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
