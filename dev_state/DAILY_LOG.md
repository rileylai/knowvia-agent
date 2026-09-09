# 2026-09-04

## Done

- 將 Knowvia development state 統一到 `dev_state/`。
- 清除舊 LearnLoop development-state 內容，不遷移舊 history。
- 建立 `PROJECT_ROADMAP.md`、`DAILY_LOG.md` 與 `DECISIONS.md`。
- 將 development workflow 改為 SDD、TDD 與 Frontend Manual Acceptance 的
  incremental vertical slices。
- 保持本輪為 documentation/workflow correction，未開始 runtime implementation。

## Automated Evidence

- Repository scan 沒有找到舊 development directory 的 active reference。
- Repository scan 沒有找到已刪除 LearnLoop state subpath 的 active reference。
- `find dev_state -maxdepth 2 -type f | sort` 只列出三份正式 state 文件。
- `git diff --check` 通過。
- 只檢查與修改 documentation、development state、`.gitignore` 與指定 references。
- 未執行 application、worker、live integration 或 runtime implementation。

## Manual Verification

本輪沒有 frontend implementation，因此未進行 feature frontend acceptance。

## Issues

- Phase 0 foundation cleanup 尚未開始。
- Current QA 仍是 Notion-only baseline。

## Next

- 人工 review Full Roadmap table 與 `AGENTS.md` Documentation Navigation；確認後再
  開始下一個 implementation slice。

## Documentation refinement

### Done

- 將 Roadmap 轉為 compact table。
- 加入 stable hierarchical IDs。
- 在 `AGENTS.md` 加入 Documentation Navigation。
- 未修改 implementation。

### Automated Evidence

- Roadmap、navigation references 與 status vocabulary 已完成 structural review。
- `git diff --check` 通過。

### Manual Verification

Documentation reviewed structurally; no runtime manual verification required.

## 2026-09-04 Foundation Runtime Cleanup

### Done

- Production `src.app.main:app` 只註冊 Knowvia core routes，Telegram 與 Supplement
  改由明確 opt-in 的 `src.app.legacy:app` 提供 compatibility verification path。
- Default tool registry 保留 Notion reader 與 source parser tools，不再建立
  Notion writer 或 Telegram bot tool。
- Core readiness 不建立或要求 Redis/RQ。`rq` 移到 `legacy-worker` extra；API
  preflight 不再檢查 RQ，並保留 `legacy-worker` preflight profile。
- 保留 Notion indexing、QA、source ingestion 與 legacy implementation files。

### Automated Evidence

- Foundation acceptance、preflight、legacy compatibility 與 active baseline 測試均
  通過。
- 受影響的 Notion indexing、QA、source ingestion 測試通過。
- `src.app.main` import probe 未載入 `rq` 或 Telegram/Supplement route、orchestrator、
  service、repository、Notion writer 與 Telegram bot modules。
- 初始 broad pytest discovery 為 `1045 passed, 5 skipped, 1 failed`；唯一失敗來自
  ignored 的 `tests/evals/parser_note_completeness/`，其中一個 test 直接讀取不存在的
  `dev_state/parser-note-completeness/human-review-intake.json`。
- 確認 Parser Completeness governance 已 deferred，未建立舊 `dev_state` tree；在
  `pyproject.toml` 排除該 deferred eval directory 後，frozen full pytest 為
  `628 passed, 5 skipped`。
- Focused verification：foundation、health 與 Notion backend wiring 為 `15 passed`；
  active app startup probe 與 legacy explicit opt-in import probe 均通過。

### Manual Verification

- backend startup：PASS
- `/health`：PASS
- `/ready`：PASS
- PostgreSQL connection：PASS
- migration current：PASS
- pgvector extension：PASS
- Redis/RQ 已不再是 core readiness dependency：PASS
- Telegram routes 未掛載於 active app：PASS
- Supplement routes 未掛載於 active app：PASS
- QA baseline：PASS
- backend-owned citation：PASS
- `insufficient_info`：PASS
- source ingestion baseline：PASS
- Notion indexing automated regression：PASS
- live Notion single-page indexing：PASS
- live indexing 結果：`http_status=200`、`status=succeeded`、
  `indexed_block_count=35`、`workflow_run_id=490`

Frontend 不在本輪驗收範圍內，未啟動。

### Issues

- Parser Completeness governance 的 ignored legacy test 仍可在 explicit path 下執行，
  但不再屬於 Knowvia full regression。

### Next

- 0.2 文件與 backend manual verification 已完成。下一個 roadmap priority 是
  `1.0 Thin Frontend Harness`，本輪未開始。

## 2026-09-04 Thin Frontend Harness

### Done

- 新增 Vite、React 與 TypeScript frontend。Knowledge、Chat、Memory 三個 surface
  使用單一 client-side navigation，不含 session persistence。
- Chat 連接現有 `POST /api/qa`，顯示 idle、loading、success、
  `insufficient_info` 與 error state。送出期間會停用輸入與按鈕。
- Citation 只讀取 backend response 的 source metadata 與 `score`，保留既有 Notion
  citation 相容性。
- Knowledge 顯示 PDF baseline；PDF upload 已在本輪啟用，URL control 維持 disabled。
- Memory 只顯示 phase 4.0 placeholder，沒有 localStorage 或模擬資料。
- Local Vite proxy 將 `/api` 轉送至 `KNOWVIA_API_BASE_URL`。若 backend 設定
  `API_BEARER_TOKEN`，token 由 dev server 加入 request，不進入 browser bundle。

### Automated Evidence

- TDD Red：frontend test suite 先因 `src/App` 不存在而失敗。
- `npm test`：`6 passed`。
- `npm run build`：TypeScript check 與 Vite production build 通過。
- Vite dev server startup probe：`http://127.0.0.1:5173/` 回傳 frontend HTML。
- QA、trust boundary 與 foundation focused regression：`14 passed`。
- Frozen backend full regression：`628 passed, 5 skipped`。

### Manual Verification

- 使用者已完成 navigation、Chat success、backend citation、`insufficient_info`、
  backend offline error，以及 Knowledge 與 Memory placeholder 人工驗收。

### Issues

- 本輪只設定 local Vite dev/preview proxy。若直接部署 `dist/`，hosting layer 仍需將
  `/api` reverse proxy 到 FastAPI。

### Next

- `0.2.1`、`1.0`、`1.0.1` 與 `1.0.2` 的人工驗收已完成；下一個 implementation
  slice 為 `2.0 Generic Knowledge Contract` 與 `2.1 PDF Knowledge Pipeline`。

## 2026-09-04 Runtime isolation and frontend follow-ups

### Done

- `0.2.1`：active Compose project 改為 `knowvia`。PostgreSQL 使用
  `knowvia-postgres`、`knowvia-postgres-data`、`knowvia` role/database 與 host port
  5433。舊 `learnloop-postgres` 已停止，原 bind mount 未刪除或修改。
- 新 database 已執行 Alembic migration。Database identity、pgvector、migration、
  schema、`/health` 與 `/ready` 均通過 local verification。
- `mock_data/` 現在包含三份 Knowvia PDF。三份 Notion JSON 已搬到
  `tests/fixtures/notion_pages/`，Notion mock tests 改讀 test-only fixtures。
- `1.0.1`：Chat 支援 Enter submit、Shift+Enter newline 與 IME composition guard。
  Empty query 與 loading 中的 Enter 不會送出 request。
- `1.0.2`：QA prompt v3 要求 evidence 不足時回傳 `INSUFFICIENT_INFO`。Backend 將
  sentinel 與已觀察到的 legacy insufficient phrase 映射為 canonical answer、
  `insufficient_info=true` 與空 citations。
- Frontend 在新 request 開始時清除前次 answer、citation 與 error；insufficient
  response 不顯示 citations。

### Automated Evidence

- Runtime isolation TDD：Red 為 `2 failed, 8 passed`；Green 為 `10 passed`。
- Keyboard TDD：Red 為 `2 failed, 10 passed`；Green 為 `12 passed`，加入 citation
  frontend regression 後為 `13 passed`。
- Insufficient citation TDD：Red 為 `4 failed, 10 passed`；Green 為 `14 passed`。
- Mock Notion relocation regression：`15 passed`。
- Combined targeted regression：`39 passed`。
- Frontend production build 通過。
- 第一次 full pytest 為 `631 passed, 5 skipped, 1 failed`。失敗項目是未修改的
  SQLite concurrent idempotency test；單獨重跑通過。第二次 full pytest 為
  `632 passed, 5 skipped`。

### Local Runtime Verification

- `knowvia-postgres`：healthy，使用 `knowvia-postgres-data`。
- PostgreSQL identity：role 與 database 均為 `knowvia`。
- pgvector：0.8.2。
- Alembic：`9c5e7b1a2d4f (head)`。
- Public schema：10 tables。
- `/health`：PASS。
- `/ready`：PASS。
- Local `hi` QA probe：`insufficient_info=true`、zero citations、canonical answer。
- 驗證期間 `knowvia-postgres` 曾短暫不存在，但 named volume 仍存在。使用同一
  volume 重建 container 後，schema、pgvector 與 Alembic revision 均保留；目前無法
  從現有 evidence 判定移除來源。

### Manual Verification

- `0.2.1` Docker state、Knowvia database identity、pgvector、health/readiness 與
  LearnLoop data preservation：PASS。
- `1.0` navigation、Chat loading/success/error、Knowledge/Memory placeholder：PASS。
- `1.0.1` Enter、Shift+Enter 與 IME 行為：PASS。
- `1.0.2` 有 evidence 與無 evidence 的 Chat、`insufficient_info` 與 zero citations：PASS。

### Next

- `2.0` 與 `2.1` implementation 已開始，完成後由使用者進行本輪 PDF manual
  verification。

## 2026-09-04 Generic Knowledge Contract and PDF Knowledge Pipeline

### Scope

- Local positive QA source 改用 `mock_data/` 既有三份 PDF；本輪不進行 Notion
  discovery、page selection、private content reading 或 Notion local baseline 建立。
- `2.0` 只抽出 PDF vertical slice 真正需要的 generic contract，未建立新的
  `KnowledgeSource` table 或 PDF-specific chunk table。

### Done

- `SourceDocument` 增加 `owner_scope`、`status`；`KnowledgeChunk` 增加 source
  display、locator、citation metadata、embedding identity、owner scope 與
  eligibility metadata。
- PDF ingestion endpoint 現在同步執行 validate → parse → normalize → persist
  `SourceDocument` → deterministic chunk → existing embedding batch/provider
  abstraction → persist `KnowledgeChunk` → mark indexed。
- Incomplete indexing 會將 snapshot 標為 failed，且不會進入 retrieval。Retriever
  只擴充 source eligibility，未改變 pgvector 或 lexical fallback 演算法。
- QA citation 改由 backend retrieved metadata 組成，PDF 使用 parser 可可靠提供的
  `page N` locator；無 page metadata 時使用 deterministic chunk locator。
- Knowledge surface 已啟用 PDF upload 的 idle、uploading/indexing、success、error
  states；`Add URL` 維持 disabled。Chat citation 同時支援 PDF 與既有 Notion metadata。
- Telegram legacy PDF path 維持 parse-only，不接回 Knowvia active indexing path。

### Automated Evidence

- PDF pipeline tests：`3 passed`。
- PDF API tests：`15 passed`。
- QA API regression：`5 passed`；QA orchestrator PDF citation regression 已加入。
- Parser、retriever、chunk repository targeted regression：`19 passed`。
- Frontend App tests：`16 passed`。
- Frontend production build：PASS。
- Alembic migration fresh SQLite upgrade/downgrade：`2 passed`。
- Frozen full backend suite：`637 passed, 5 skipped`。

### Live Local Verification

- 三份 sample PDF 均完成正式 `/api/ingest/document` live upload/index，結果為
  `22/22`、`17/17`、`40/40` chunks/embeddings，三個 snapshots 均為 `indexed`。
- Bounded live positive QA：`HTTP 200`、`insufficient_info=false`、retrieved
  `5` 個 PDF chunks、`5` 筆 backend-owned PDF citations；citation locator 使用
  parser provenance 的 `page N`。
- Bounded live insufficient-info QA：`HTTP 200`、`insufficient_info=true`、`citations=[]`。
- 執行時只回報 bounded metadata，不輸出 PDF raw text、embedding input 或 vector。

### Manual Verification

後續 browser manual verification 已完成，詳見下方 `PDF Follow-up Manual Verification` 與
`PDF Source Inventory and Exact Duplicate Guard` 紀錄。

請依本輪回覆中的 guide 驗證 Knowledge PDF upload/index、Chat PDF citation、negative
insufficient-info、invalid upload error，以及 `Add URL` disabled。

### Next

- 完成本輪 frozen regression 與 bounded live local verification後，等待 `2.0`、`2.1`
  manual verification。
- 不開始 `2.2 URL Knowledge Pipeline`。

## 2026-09-05 URL Knowledge Pipeline

### Scope

- 實作 `2.2 URL Knowledge Pipeline`。
- URL 走既有 generic flow：validate、fetch、parse、normalize、`SourceDocument`、
  chunk、embedding、`KnowledgeChunk`、retrieval 與 backend citation。
- 本輪不開始 `2.3 Screenshot / Image Knowledge Pipeline`。

### Implementation

- URL parser 保留 requested URL、redirect 後的 final URL 與 HTML title；title 缺失時
  使用 bounded URL fallback。
- Backend 只允許 HTTP/HTTPS、HTML/XHTML/plain text，限制 URL 長度、redirect 次數、
  response 大小與 timeout。每次 redirect 都重新檢查 DNS，private、loopback、
  link-local 與 localhost 會被拒絕。
- 新增 URL snapshot identity 欄位與 migration。相同 owner、final URL、content hash
  且已 indexed 的 snapshot 回傳 `already_indexed`；同 URL 的內容變更建立新 snapshot。
- PDF 與 URL 共用 `KnowledgeIndexingService`、chunk、embedding、eligibility、
  retrieval 與 citation metadata。沒有建立 URL-specific chunk、retriever 或 vector
  table。
- Knowledge UI 新增 URL input 與 idle、loading、success、duplicate、error state；
  inventory 顯示 indexed URL 與 final URL。

### Automated Evidence

- TDD URL pipeline tests：URL indexing、generic retrieval、dedup、embedding failure、
  inventory、citation 與 parser provenance 通過。
- Backend full suite：`651 passed, 5 skipped`。
- Frontend tests：`24 passed`。
- Frontend production build：PASS。
- `git diff --check`：PASS。

### Manual Verification

- Knowledge UI 可以正常加入公開 URL，並顯示 visible loading、indexing 與 success state。
- 成功後 Indexed Sources 正確顯示 URL source 與 chunk count。
- Chat 可以根據 indexed URL evidence 產生 grounded answer。
- URL citation 由 backend metadata 提供，包含 source URL、display name、deterministic
  `chunk N` locator 與 score。
- 再次加入相同且內容未變的 URL 顯示 `Already indexed`，沒有新增重複 searchable
  source 或 chunks。
- 沒有足夠 evidence 的問題回傳 `insufficient_info`，且 citations 為空。
- localhost、loopback 與 non-public URL fail closed，並顯示 visible error。
- Existing PDF upload、inventory、retrieval、citation 與 duplicate flow 沒有 regression。
- 本輪未開始 `2.3` 或其他後續能力。

### Next

- `2.2` 已完成 Paste URL → indexing → Chat → citation 的 frontend manual acceptance，
  狀態更新為 `done`。
- `2.3 Screenshot / Image Knowledge Pipeline` 維持 `planned`。

## 2026-09-04 PDF Positive QA Follow-ups

### Scope

- 針對使用者 browser 回報的 PDF positive QA failure，使用已 indexed 的
  `Choose a design pattern for your agentic AI system` PDF 做 A/B/C bounded probes。
- 不執行 Notion discovery、sync 或 private content QA；不加入新的 retrieval subsystem。

### Diagnosis

- A、B、C 的 pgvector top-5 都只命中目標 PDF，且每筆 metadata 都是
  `source_status=indexed`、`eligibility_status=eligible`。
- A：`insufficient_info=false`、`5` citations；C：`insufficient_info=false`、`3`
  citations。
- B：top-5 時 `insufficient_info=true`、`citations=[]`；擴至 top-10 後為
  `insufficient_info=false`、`7` citations。
- Ownership 判定為 retrieval coverage 與 evidence sufficiency 的最小組合，並非
  eligibility failure 或 mixed-language retrieval failure。

### Implementation

- 新增 isolated DB + fake provider 的 public `/api/qa` regression，先以 default
  `top_k=5` 重現，再將 generic QA default 改為 `top_k=10` 後通過。
- 保留 `insufficient_info=true → citations=[]`；未修改 similarity threshold、prompt、
  reranker、BM25、RRF 或其他 retrieval algorithm。
- `2.1.2` 的 success card 已使用 backend `indexed_chunk_count` 與
  `embedded_chunk_count`；既有 frontend test 已覆蓋實際 count rendering。

### Post-fix Live Verification

- 未指定 `top_k` 的正式 `/api/qa` default=10：A 為 `HTTP 200`、
  `insufficient_info=false`、`10` retrieved、`9` citations；B 為
  `HTTP 200`、`insufficient_info=false`、`10` retrieved、`7` citations；C 為
  `HTTP 200`、`insufficient_info=false`、`10` retrieved、`7` citations。
- Negative PDF question 仍為 `HTTP 200`、`insufficient_info=true`、`citations=[]`。
- Positive citations 均為目標 PDF 的 backend-owned `page N` locator；沒有輸出 raw
  PDF text、完整 prompt 或 provider response。

### Manual Verification

後續 browser manual verification 已完成，詳見下方 `PDF Follow-up Manual Verification`
紀錄。

上述項目已由後續 browser manual verification 確認，結果記錄於下方驗收紀錄。

## 2026-09-04 PDF Follow-up Manual Verification

### Verified

- PDF upload/indexing 成功，Knowledge success card 顯示實際 chunk count 與 embedded
  count。
- `What is an agent design pattern?` 成功回答並提供 PDF backend citations。
- `What design patterns for agentic AI systems?` 成功回答並提供 PDF citations。
- Unsupported query `什麼是claude` 回傳 `insufficient_info` 與 zero citations。
- `2.1.1` retrieval coverage 修正與 `2.1.2` chunk count UI 均通過 browser manual
  verification，roadmap 更新為 `done`。

### Known Limitation

- `What design patterns for agentic AI systems?` 已通過 PDF QA。
- 帶有 `in this document` 的 query 目前沒有 deterministic referent，因為 Chat 尚未
  有 Conversation Session 或 current-source scope。這不是 `2.1` blocker；相關
  conversational referent 與 current source context 留到後續 Conversation Sessions /
  context work。

### Next

- Append `2.1.3 PDF Source Inventory and Exact Duplicate Guard`，目前維持 `planned`。
- `2.0` 與 `2.1` 維持 `manual_verification`；本輪不開始 `2.1.3` implementation，
  也不開始 `2.2`。

## 2026-09-05 PDF Source Inventory and Exact Duplicate Guard

### Scope

- 實作 `2.1.3 PDF Source Inventory and Exact Duplicate Guard`。
- 本輪只處理 indexed PDF inventory 與 exact duplicate protection；不開始 `2.2`
  URL Knowledge Pipeline，也不執行 Notion discovery、sync 或 private content 操作。

### Implementation

- 沿用 `SourceDocument` 的 `owner_scope`、`source_type`、`content_hash`、`status`
  與 `updated_at`，新增 raw PDF `file_hash`；沒有新增 `KnowledgeSource` table。
- `file_hash` 使用 raw uploaded PDF bytes 的 SHA-256，既有 `content_hash` 維持
  normalized extracted text 的 SHA-256。既有 records 的 `file_hash=NULL` 未 backfill。
- 新增只回傳 source-level metadata 的 `GET /api/knowledge/sources`。Inventory 只列
  `local` owner、`pdf`、`indexed` source，chunk count 只計入 eligible PDF chunks。
- PDF index flow 在 parser 前以 raw `file_hash` 查找同 owner、同 source kind、同 hash 的
  indexed source。命中時回傳 `already_indexed`、reuse existing source 與 chunk count，
  不重新 parse、chunk、embedding 或建立 searchable chunks。
- 相同 raw bytes 即使 filename 不同仍會 dedup；同 filename、不同 raw bytes 仍允許建立
  新 source。filename 不作 authoritative identity。
- Knowledge surface 新增 Indexed Sources 的 loading、success、empty、error states，
  upload 成功後重新載入 inventory；`Add URL` 維持 disabled。

### Automated Evidence

- `2.1.3` hash 與 source-management targeted backend tests：`12 passed`。
- PDF/source/retrieval/citation/API targeted regression：`52 passed`。
- Frontend tests：`21 passed`。
- Frontend production build：PASS。
- Frozen full backend suite：`645 passed, 5 skipped`。
- `git diff --check`：PASS。
- Local Knowvia migration 已升級至新增 `file_hash` 欄位與 index 的 revision；既有資料未
  修改。

### Existing Duplicate Report

- local indexed PDF 有 `1` 組既存 normalized-content duplicate：`2` 個
  `SourceDocument`、合計 `80` 個 eligible chunks。這些 records 的 raw `file_hash` 為
  `NULL`，因此不將該組報告當成 raw-file exact duplicate。
- 既有 duplicate records 未刪除、未重建 database 或 volume；後續另行決定 cleanup
  policy。

### Bounded Live Verification

- `GET /api/knowledge/sources`：`HTTP 200`，回傳 `4` 筆 indexed PDF source-level
  records，合計 `119` 個 eligible chunks。
- Migration 前的既有 duplicate probe 使用 normalized-text identity，不作為本輪 raw
  file identity 的 verification evidence。
- 本輪 browser manual verification 已使用新版 pipeline 建立含 raw `file_hash` 的 source，
  再以不同 filename 上傳相同 raw bytes；結果為 `Already indexed`，沒有新增
  `SourceDocument` 或 searchable chunks。
- Verification 只使用 `mock_data/` PDF，沒有呼叫 Notion，也沒有輸出 raw PDF text、
  embedding、provider response 或 secrets。

### Manual Verification

已完成 browser manual verification。

- Knowledge 頁面可看到 Indexed Sources；每筆 PDF source 顯示 filename、source kind、
  Indexed status、chunk count 與可用的 updated time。
- 使用新版 pipeline 建立含 raw `file_hash` 的 PDF source 後，再上傳完全相同 raw bytes
  但不同 filename 的 PDF，顯示 `Already indexed`。
- Exact duplicate 沒有重新 indexing，沒有新增 `SourceDocument`，也沒有增加 searchable
  chunk count。
- 不同 raw bytes 的 PDF 可以建立新 source。
- PDF positive QA 正常回答並提供 backend-owned PDF citations；unsupported query 仍為
  `insufficient_info` 且 zero citations。
- `Add URL` 維持 disabled。
- 舊有 `file_hash=NULL` records 未修改或刪除。

### Known Limitation

- 帶有 `in this document` 的 query 仍缺少 deterministic referent，因為目前沒有
  Conversation Session 或 current-source scope。這不是 `2.1.3` blocker；
  conversational referent 與 current source context 留到後續 Conversation Sessions /
  context work，不新增新的 `3.x` implementation follow-up。

### Next

- `2.0`、`2.1` 與 `2.1.3` 已完成 browser manual acceptance，狀態更新為 `done`。
- 不開始 `2.2 URL Knowledge Pipeline`。

## 2026-09-05 Screenshot / Image Knowledge Pipeline

### Scope

- 實作 `2.3 Screenshot / Image Knowledge Pipeline`，只加入 image 到既有 generic
  Knowledge path；不建立 image-specific chunk、vector table、retriever 或第二套 RAG。
- OCR 使用既有 Pillow/Tesseract adapter；default automated tests 使用 fake OCR 與 fake
  embedding，不依賴 machine Tesseract 或 private image content。

### Implementation

- Image flow 現在同步執行 validate → decoded image inspection → OCR → normalize →
  `SourceDocument` → generic chunk → embedding → `KnowledgeChunk` → retrieval eligibility。
- Image source 使用 `source_type=image`、`owner_scope=local`；exact duplicate authority
  是同 owner、indexed image、raw upload bytes SHA-256 `file_hash`。命中時回傳
  `already_indexed`，跳過 OCR、chunk、embedding 與新 records。
- Image inventory 沿用 `GET /api/knowledge/sources`；image citation 只使用 backend-owned
  filename、dimensions 與 deterministic `chunk N`，不虛構 region、line 或座標。
- Knowledge UI 已加入 image upload、processing、success、duplicate、error、inventory
  refresh 與 image citation metadata rendering；Telegram legacy screenshot path 維持
  parse-only，不接回 active indexing path。

### Automated Evidence

- TDD image pipeline、OCR、upload validation 與 image API tests：`36 passed`。
- Frontend tests：`28 passed`；frontend production build：PASS。
- Backend full suite：`660 passed, 5 skipped`。

### Manual Verification

Frontend manual verification 已完成。確認 multi-image selection、natural ordering、sequence
number、upload 前 reorder、processing state、grouped logical source、single inventory row、
image count、chunk count、title、bounded preview，以及 backend image provenance citation。
同時確認 existing single-image、PDF、URL behavior 沒有因 image flow 改變；empty OCR 會
fail closed，不建立 indexed zero-chunk source，並顯示 frontend no-text error。

### Known Limitations

- OCR 品質依賴 local Tesseract runtime、`eng+chi_tra+chi_sim` language data 與圖片品質；
  本輪沒有使用 private screenshot 做 live browser verification。
- Image citation 沒有可靠的 OCR region/line provenance，因此只回傳 filename、dimensions
  與 deterministic chunk locator。

### Next

- `2.3`、`2.3.1` 與 `2.3.2` 已完成 frontend manual verification，狀態更新為 `done`。
- `2.4 YouTube Knowledge Pipeline` 維持 `planned`，本輪不開始。

## 2026-09-05 Screenshot / Image Knowledge Follow-up

### Scope

- 依 stable hierarchical roadmap append `2.3.1 Multi-image Grouped Source UX` 與 `2.3.2 Screenshot Display Name + Preview`。
- `2.3` parent 與 verification follow-ups 已完成；本輪不開始 `2.4`。

### Implementation

- 確認既有 `POST /api/ingest/image-ocr` 已接受 repeated `images` fields 與既有 max 10 images / 20 MiB limits；active indexed path 現在將一次 upload batch 建立為一個 grouped `SourceDocument`，沒有新增 batch ingestion architecture。
- Frontend file picker 支援 `multiple`，先以 natural filename ordering 排列並顯示 `01` 起的 sequence，提供最小 Move Up / Move Down；確認後以既有 `images` multipart field 送出，loading 時 disabled 並阻止 duplicate submit。
- Backend 在 OCR/indexing 前固定 ordered `sequence_index`，以 ordered per-image raw-byte hashes 的 deterministic canonical encoding 建立 batch `file_hash`；同順序相同 bytes 回傳 `already_indexed`，不同順序視為不同 source。Source metadata 保存 sequence、filename、per-image hash 與 dimensions，所有 chunks 仍走既有 `KnowledgeIndexingService`。
- Batch response 回傳一個 source 與 aggregate summary，同時保留每張 image 的 indexed、already indexed 或 error state；成功後 refresh generic source inventory，因此 top-level inventory 只顯示一筆 source。
- `source_display_name` 使用 deterministic bounded title：ordered image 1 優先 heading-like OCR line，其次第一個 meaningful sentence；有 usable OCR content 但沒有合理 title 時才 fallback bounded filename stem。完全沒有 usable text 時以現有 `OCR_FAILED` fail closed。單張 prefix 為 `Screenshot ·`，多張為 `Screenshots ·`；`source_preview` 只作 inventory UI display。
- Citation metadata 保留 `image_index`、`sequence_index`、`original_filename`、file hash 與 dimensions，locator 由 backend 形成 `Image N · chunk M`；沒有 embedding 或額外 LLM title-generation call。

### Automated Evidence

- Focused backend image/OCR/API/QA regression：`43 passed`。
- Frontend App suite：`30 passed`；frontend production build：PASS。
- Backend full suite：`667 passed, 5 skipped`；working tree 的 existing compose identity context 本輪未修改 `docker-compose.yml`。

### Manual Verification

Frontend manual verification 已完成並判定 PASS：

- multi-image selection、deterministic ordering、sequence number 與 upload 前 reorder。
- visible processing state、grouped logical source，以及 Indexed Sources 只有一筆 inventory row。
- inventory 的 image count、chunk count、bounded title 與 OCR preview。
- per-image provenance 保留供 backend citation 使用；original filename 不作 top-level display-name authority。
- empty OCR fail closed，不建立 indexed zero-chunk source，且 frontend 顯示 visible no-text error。
- existing single-image、PDF、URL behavior 維持原有使用方式。

### Known Limitations

- Display name、preview 與 image locator 都是 deterministic backend metadata，不使用 embedding 或 LLM title service；preview 不會成為 retrieval evidence。
- Empty OCR 會以現有 `OCR_FAILED` structured failure fail closed；不建立 indexed source，
  不產生 eligible chunk，frontend 顯示可見的 no-text error。Filename fallback 僅適用於
  有 usable OCR content 但沒有合理 display title 的情況。
- OCR-derived title 偶爾會保留少量 OCR noise，屬目前可接受的 OCR limitation，不阻塞 `2.3`。
- Grouped batch indexing 以 source-level OCR/index transaction 完成；若 OCR 或 generic indexing 失敗，整個 source 失敗，不將不完整 parts 宣稱為成功。

### Next

- `2.3.1` 與 `2.3.2` 已完成 frontend manual verification，狀態為 `done`。
- `2.3` parent 狀態為 `done`；`2.4` 維持 `planned`，不開始 runtime implementation。

## 2026-09-05 Conversation Sessions 3.0

### Scope

- 實作 `ConversationSession` 與 `ConversationMessage` durable persistence、owner
  isolation、deterministic title、recent context limit 與 token budget。
- 將 synchronous QA 接到 session message flow；不開始 Persistent Memory、MCP、bounded
  Agent loop 或 SSE。
- Frontend 加入 New Chat、conversation list、URL `session_id` identity、same-session
  follow-up、desktop sidebar 與 mobile drawer。

### Implementation

- 新增 Alembic migration `f1a2b3c4d5e6_add_conversation_sessions.py`，建立 sessions、messages、
  sequence uniqueness 與 cascade foreign key。
- Backend 新增 conversation repository、orchestrator、schemas 與四個 conversation API
  endpoint。Session list 依 `updated_at DESC`，owner filter 由 backend 強制。
- 第一則 user message 使用 deterministic 前 48 chars 作 title；空 session 顯示
  `New conversation`。QA retrieval 仍只使用 current question，history 只作 bounded
  follow-up context。
- Provider failure 會保留已保存的 user message，不建立 fake assistant message；
  `insufficient_info` 仍保持 zero citations。
- Frontend 首次進入先 GET list；只有成功確認 list 為空才 bootstrap session。Invalid URL
  session 會 generic error、fallback 到合法 session，且不保留 invalid identity。Session
  switch 先 load，失敗時保留原 active session、messages 與 URL。

### Automated Evidence

- Conversation context、API、owner isolation、provider failure 與 insufficient-info tests：
  `10 passed` focused。
- Backend full suite：`675 passed, 5 skipped`。
- Frontend session tests 與既有 App suite：`37 passed`；production build：PASS。
- Alembic head：`f1a2b3c4d5e6`；migration 與 conversation focused tests：`10 passed`。

### Manual Verification

Browser manual verification completed on 2026-09-06; final results are recorded in the
completion entry below.

### Known Limitations

- Current owner identity 使用 single-user auth contract 的 backend owner boundary，尚未
  擴充多使用者登入 provider。
- Chat 仍是 synchronous request/response；streaming lifecycle、Agent tool loop 與
  persistent memory 留在後續 roadmap slices。

### Next

- 完成 browser manual acceptance：首次載入、URL restore、follow-up、New Chat、session
  switch failure、mobile drawer 與 refresh。
- Manual acceptance 完成前維持 roadmap `3.0=manual_verification`；`4.0 Persistent
  Memory` 維持 `planned`。

## 2026-09-05 Same-session Conversational Recall 3.0.1

### Scope

- 依 browser manual verification 的 blocking repro，新增 `3.0.1 Same-session
  Conversational Recall`。
- `3.0` 維持 `manual_verification`；不開始 `4.0 Persistent Memory`、MCP、Agent
  loop、SSE 或 semantic history retrieval。

### Diagnosis

- `ConversationSession` 與 `ConversationMessage` persistence、owner scope、recent
  history load 已確認正常。
- 原本所有 conversation request 都進入 enterprise QA；沒有 Knowledge evidence 時，
  `QAOrchestrator` 在 provider 前直接回傳 `insufficient_info`。
- 原本的 `qa_answer_v3` 只允許 production-note context，沒有 conversational-only
  authority path。

### Implementation

- 新增 bounded deterministic `classify_conversation_recall`，只處理 previous user
  utterance、previous assistant answer、previous choice/recommendation，以及 previous
  recommendation reasoning。
- Recall request 使用獨立 `conversation_recall_v1` prompt，history 以同一 session 的
  last-6 與既有 token budget 為界；不執行 Knowledge retrieval，不產生 citations。
- New Chat 沒有 earlier user message 時回傳 deterministic no-history response，不讀取
  其他 session。
- Enterprise request 保留原本 Knowledge retrieval、backend-owned citations 與
  `insufficient_info=true`、`citations=[]` contract。Assistant history 不會進入
  Knowledge evidence。

### Automated Evidence

- Focused conversation、context、prompt safety 與 recall tests：`40 passed`。
- Backend full suite：`690 passed, 5 skipped`。
- Frontend tests：`37 passed`。
- Frontend production build：PASS。
- `git diff --check`：PASS。

### Manual Verification

Browser core verification completed; the bounded paraphrase limitation remains documented
below.

### Known Limitations

- Recall mode 只支援明確、bounded 的 conversational reference 類型，不建立 general
  intent framework、semantic history search 或 generic query rewrite。
- 部分 paraphrase 仍可能 fallback 到 enterprise QA，例如 `hi I'm Nicole` 後詢問
  `what is my name`。Broader intent selection deferred 到後續 bounded Agent runtime。
- 同時需要新的 enterprise claim 與複雜 pronoun/reference resolution 的 follow-up，若
  目前 retrieval 無法安全處理，仍會保留 `insufficient_info`。
- Conversation recall 使用 provider 時，provider 只收到 bounded same-session history；
  response 的 citations 固定為空。

### Next

- 依下方 browser guide 重新驗證 `3.0` 與 `3.0.1` 的 session、recall、authority
  boundary 與 failure behavior。
- Manual acceptance 完成前，`3.0=manual_verification`、`3.0.1=manual_verification`；
  `4.0 Persistent Memory` 維持 `planned`。

## 2026-09-05 Conversation Citation Follow-up 3.0.2

### Manual Verification

- `3.0.1` browser core verification：PASS。
- Same-session previous answer recall：PASS。
- Previous recommendation reasoning：PASS。
- Conversation recall 不產生 enterprise citations：PASS。
- New Chat/session isolation：PASS。

### Scope

- 新增 `3.0.2 Per-message Citation Persistence & Disclosure`。
- Browser 發現 grounded answer 的 citation 只停留在目前 request response；送出下一題
  後，舊 assistant answer 的 citation disclosure 消失。

### Focused Discovery

- `POST /api/conversations/{session_id}/messages` 的 top-level response 目前包含
  backend-owned `citations`。
- `ConversationMessage.metadata_json` 已存在於 3.0 migration，但目前 append、snapshot
  與 API response 都沒有保存或回傳 citation metadata。
- `GET /api/conversations/{session_id}` 的 message contract 目前只有 content、role、
  sequence 與 timestamps，frontend 因此只能 restore assistant content。
- Frontend 的 `response` state 只代表最後一次 request；`CitationList` 也只 render
  這個 state，下一次送出時會清除。

### Known Limitation

- 3.0.1 bounded recall classifier 對部分 paraphrase 仍有限制。Broader intent selection
  deferred 到後續 bounded Agent runtime，本 follow-up 不擴張 classifier。

### Status

- `3.0=manual_verification`。
- `3.0.1=done`，browser core verification 已通過。
- `3.0.2=manual_verification`，automated implementation 已完成，等待 browser
  manual verification。
- 不開始 `4.0 Persistent Memory`、global citation manager、Agent runtime 或 SSE。

## 2026-09-05 Conversation Citation Persistence Implementation 3.0.2

### Implementation

- 新增 typed、versioned `ConversationCitation` metadata contract，沿用
  `conversation_messages.metadata_json`。
- citation metadata 只保存 backend QA result 的 bounded display provenance；最多 20 筆，並
  限制 source name、locator、URL、Notion path 與 filename 長度。
- assistant content 與 citation metadata 在同一次 message append 中寫入。Provider failure
  仍只保留 user message，不建立 assistant message。
- GET session 與 POST message response 的每則 `ConversationMessage` 現在固定回傳
  `citations: []` 或該 message 的 citations。null、legacy、malformed 與 unsupported
  metadata version 都 safe fallback 為空 list。
- frontend 改由每則 assistant message render collapsed `Sources · N` disclosure；展開與
  折疊狀態只存在 frontend，不建立 global citation panel。
- conversation recall 與 `insufficient_info` message 不顯示 Sources disclosure。
- ConversationMessage citation metadata 沒有進入 context assembly，不會成為 Knowledge
  evidence authority。

### Automated Evidence

- Focused citation persistence、bounded metadata、legacy fallback、message attachment 與
  authority boundary tests：`6 passed`。
- Backend full suite：`696 passed, 5 skipped`。
- Frontend tests：`39 passed`。
- Frontend production build：PASS。
- `git diff --check`：PASS。

### Manual Verification

Browser manual verification completed on 2026-09-06; final results are recorded in the
completion entry below.

### Status

- `3.0=manual_verification`。
- `3.0.1=done`，browser core verification 已通過；paraphrase classifier limitation 維持
  Known Limitation，broader intent selection deferred 到後續 bounded Agent runtime。
- `3.0.2=manual_verification`，automated implementation 完成，等待 browser verification。
- 不開始 `4.0 Persistent Memory`、global citation manager、Agent runtime 或 SSE。

## 2026-09-06 Conversation Roadmap Completion Verification

### Browser Manual Verification

- Grounded assistant message 顯示自己的 `Sources · N`，預設 collapsed，且可獨立
  expand / collapse。
- 後續問題不會移除舊 assistant message 的 citations；多則 assistant messages 的
  citation lists 各自附屬於正確 message。
- Browser refresh 後，durable session restore 會恢復既有 assistant message citations；
  切換其他 session 再切回後，citations 仍屬於原 conversation message。
- Conversational recall 維持 `citations=[]`，不顯示 Sources；`insufficient_info` 也維持
  `citations=[]`，不顯示 Sources。
- Citation 仍由 backend metadata 提供，未從 answer text 重建；未發現 session isolation、
  ordering、restore 或 grounding regression。

### Final Status

- `3.0=done`。
- `3.0.1=done`。
- `3.0.2=done`。
- `4.0 Persistent Memory` 維持下一個正式 implementation slice，尚未開始。

### Known Limitation

- `3.0.1` bounded recall classifier 對部分 paraphrase 仍有限制，例如 `hi I'm Nicole` 後
  詢問 `what is my name` 可能 fallback 到 enterprise QA / `insufficient_info`。Broader
  intent selection deferred 到後續 bounded Agent runtime；本輪不擴張 classifier。

## 2026-09-06 Persistent Memory 4.0 Implementation

### Implementation

- 建立獨立的 `LongTermMemory` table、Alembic migration、repository 與 `MemoryService`。
- explicit save 只接受 deterministic wording；普通陳述不會建立 memory。Memory type 限定為
  `decision`、`preference`、`project_context`，無法判定時使用 `project_context`。
- embedding、owner filter、active filter、pgvector semantic search、exact duplicate guard
  與 hard delete 都由 backend 控制。
- Conversation flow 支援 `Memory saved`、`Already saved`，並可在新 session 以 saved
  memory 回答；saved memory 不進 enterprise citations。
- Memory Inspector 支援 loading、empty、list、delete loading、delete error 與 durable
  refresh。

### Automated Evidence

- Memory service、API 與 owner isolation tests：`11 passed`。
- Backend full regression：`707 passed, 5 skipped`。
- Frontend tests：`44 passed`。
- Frontend production build：PASS。
- `git diff --check`：待 final verification 執行。

### Manual Verification

Browser manual verification PASS is recorded in the final completion entry below.

### Status

- `4.0=manual_verification`。
- `5.0=planned`，本輪不開始 MCP 或 bounded Agent Runtime。

## 2026-09-06 Persistent Memory 4.0.1 Direct Saved-Memory Recall Coverage

### Diagnosis and Implementation

- Root cause：原本的 `is_memory_recall_query` 只檢查記憶、偏好、決定等 marker，漏掉
  `我的名字是？`、`What is my name?` 與 `What did we decide about production?`。
- 只加入 bounded deterministic direct question patterns；沒有建立 general intent classifier，
  也沒有使用 LLM routing 或搜尋所有 Knowledge 與 Memory。
- explicit saved memory 經過 New Chat 後，matching query 會進入 owner-scoped semantic
  `LongTermMemory` retrieval，回答標記 `used_saved_memory=true`，且 enterprise citations 維持空陣列。
- non-memory enterprise query 維持既有 Knowledge QA fallback。

### Automated Evidence

- 先以六個實際 API seam cases 驗證 red；修正後六個中英文 cross-session recall cases：`6 passed`。
- Backend focused memory conversation tests：`8 passed`。
- Backend full regression：`713 passed, 5 skipped`。
- Frontend tests：`44 passed`；production build：PASS。
- `compileall`、Alembic head check 與 `git diff --check`：PASS。

### Manual Verification

Browser manual verification PASS is recorded in the final completion entry below.

### Status

- `4.0=manual_verification`。
- `4.0.1=manual_verification`。
- `5.0=planned`，本輪不開始 MCP、Agent loop 或 tool calling。

## 2026-09-06 Persistent Memory 4.0.1 Browser Blocker Follow-up

### Diagnosis and Fix

- 先確認原本 port 8000 的 process 已停止，再從目前 Knowvia workspace 重新啟動 backend。新 process 的
  cwd 是 `/Users/rileylai/Desktop/code/project/knowvia-agent`，`/health` 與 `/ready` 均通過。
- Exact classifier probe 顯示 `我的職業？` 與 `What is my occupation?` 原先未命中；問題不是
  LongTermMemory persistence 或 owner scope。
- Conversation route 會先 `strip()` query，再把同一 normalized value 傳入 QA。Classifier 命中後，
  `MemoryService.search_memories` 確實使用 request owner scope；實際資料庫 search 找到名字與職業兩筆 memory。
- Production-like failure 是 memory search 已找到兩筆結果，但 enterprise retrieval 同時回傳 chunks，後續
  LLM 仍可能回 `insufficient_info`。現在 memory-recall intent 有結果時直接回 saved memory，不進 enterprise
  retrieval 或 LLM；一般 enterprise query 仍只走既有 Knowledge QA。
- Recall routing 改為 bounded structural question shapes，例如中文 `我的...？`、`你記得我的...？`，以及
  English `what is my...`、`what do I...`、`what did we decide...`。不再列舉 name、occupation 等 field。

### Automated and Process Evidence

- Classifier regression：`15 passed`；conversation memory regression：`12 passed`。
- Backend full regression：`724 passed, 5 skipped`。
- Frontend tests：`44 passed`；production build：PASS。
- Process HTTP probe：occupation 三個 query、name 三個 query 都回 `used_saved_memory=true`、
  `citations=[]` 且 answer match；`What database does production use?` 維持 `used_saved_memory=false`。
- `compileall`、Alembic head check 與 `git diff --check`：PASS。

### Manual Verification

Browser manual verification PASS is recorded in the final completion entry below.

### Status

- `4.0=manual_verification`。
- `4.0.1=manual_verification`。
- `5.0=planned`，本輪不開始 MCP、Agent loop 或 tool calling。

## 2026-09-06 Persistent Memory 4.0.2 Saved-Memory Relevance Selection

### Diagnosis and Implementation

- Root cause：LongTermMemory semantic search 原本回傳 top-k raw matches；direct recall 沒有
  relevance gate，也沒有在 singular query 只選 best match，因此 `What is my name?` 會帶出職業
  memory，無 interest memory 時也可能帶出 unrelated memories。
- 先確認 repository 的 score semantics：Postgres `pgvector` 使用 cosine distance，repository 轉成
  normalized cosine similarity `[0, 1]`；SQLite fallback 使用相同範圍的 cosine similarity。較高分代表
 相關性較高。
- 依 live score evidence：direct matching 約 `0.46`、目前 unrelated cross-field 約 `0.35`、broad
  query 約 `0.25`，採 bounded deterministic floors：direct `0.40`、broad `0.20`。這不是 LLM
  reranker、importance/temporal ranking 或 semantic dedup。
- Direct/singular recall 通過 gate 後只取排序第一筆 best memory；broad recall 通過較寬 gate 後保留既有
  request `top_k` bounded multiple memories。無 sufficiently relevant match 時回傳空結果，維持
  `used_saved_memory=false`，不輸出 unrelated saved-memory content。

### Automated and Process Evidence

- 先新增 failing tests，再完成 minimal Memory Service selection：service `16 passed`、conversation
  `13 passed`。
- Backend full regression：`726 passed, 5 skipped`。
- Frontend tests：`44 passed`；production build：PASS。
- `compileall`、Alembic head check 與 `git diff --check`：PASS。
- 最新 backend process 已重新啟動；`/health` 與 `/ready` 均回 HTTP 200。Process HTTP probe：
  name 只回 name、occupation 只回 occupation、interest 無 memory hit 且
  `used_saved_memory=false`、broad 回 bounded name + occupation；四者 `citations=[]`。

### Manual Verification

Browser manual verification PASS is recorded in the final completion entry below.

### Status

- `4.0=manual_verification`。
- `4.0.1=manual_verification`。
- `4.0.2=manual_verification`。
- `5.0=planned`，本輪不開始 MCP、Agent loop 或 tool calling。

## 2026-09-06 Persistent Memory 4.0.x Completion

### Browser Manual Verification

Browser manual verification PASS：

- Explicit save 顯示 `Memory saved`；exact duplicate 顯示 `Already saved`。
- Memory Inspector 在 refresh、New Chat 與 session restore 後仍保留資料，確認 durable persistence。
- Session A explicit save 後，New Chat 的 matching query 可完成 cross-session recall。
- Direct/singular recall 只使用 best relevant saved memory，不帶出其他 memory。
- 沒有 sufficiently relevant memory 時，不使用或輸出 unrelated saved memory。
- Broad recall 可 bounded 使用 multiple saved memories。
- `Used saved memory` 獨立顯示，未混入 enterprise Sources 或 enterprise citations。
- Memory Inspector hard delete 後，future session 不再 recall 該 memory。
- Knowledge QA、enterprise citations 與 `insufficient_info` 行為沒有 regression。

### Final Status

- `4.0=done`。
- `4.0.1=done`。
- `4.0.2=done`。
- `5.0=planned`，不在本輪開始 MCP、Agent loop 或 tool calling。

## 2026-09-06 MCP and Bounded Agent Runtime 5.0 Implementation

### Focused Discovery

- 現行 `ConversationOrchestrator` 保留 3.0 same-session context 與 conversational recall；4.x
  explicit save 與 direct saved-memory recall 是 deterministic backend routing。
- 現行 `QAOrchestrator` 保留 Knowledge retrieval、backend citation projection、
  `insufficient_info` 與 workflow audit；既有 `ToolRegistry` 只服務 ingestion/Notion tools，
  沒有直接擴大成 Agent registry。
- `ProviderRouter` 原本只支援 text completion；本輪補上 bounded provider tool-call contract，
  並保留不支援 tool calling 的 provider fallback。

### Implementation

- 新增 `src/agent/` bounded per-run state、typed termination reason、三個 allowlisted tools、
  schema validation、owner scope、timeout、context budget 與 max 3 tool calls。
- 新增 in-process `MCPToolAdapter` boundary。`search_knowledge` 呼叫既有 retriever，
  `search_memory` 與 `save_memory` 呼叫既有 `MemoryService`；adapter 不直接 query database。
- OpenAI provider 支援 structured tool calls。Tool result 進入下一 iteration 前會 bounded，
  只保留 safe text、typed structured content 與 backend citation metadata。
- Explicit save policy、memory authority、owner scope、citation authority 與
  `insufficient_info` invariant 維持 backend-owned；raw tool call、arguments、trace 與
  chain-of-thought 不寫入 conversation persistence。
- Tool-capable conversation path 會保存 final assistant message 與 backend citations；既有
  direct QA 與非 tool-capable provider regression path 保持不變。

### Automated Evidence

- Agent/provider focused tests：`16 passed`。
- Tool-capable conversation integration：`1 passed`。
- Backend full regression：`738 passed, 5 skipped`。
- Frontend tests：`44 passed`；production build：PASS。
- `compileall`：PASS。
- `git diff --check`：PASS。

### Manual Verification

This entry predates the final 5.0 completion sync recorded at the end of this log.

### Status

- `5.0=manual_verification`。
- `6.0=planned`，本輪不開始 SSE Streaming and UX Hardening。

## 2026-09-06 Agent Browser Verification Fixes 5.0.1

### Scope

本輪只處理 5.0 browser verification 的三個 blocker：explicit save failure、session draft
leakage/retry duplicate、same-session conversational transform failure。不開始 6.0，也不進行
MCP protocol migration。

### Root Cause and Fix

- Explicit save failure 發生在 `save_memory` adapter 的 explicit-intent boundary。Provider 將
  memory content 轉成 paraphrase 後，adapter 以 provider content 做 exact match，導致
  `permission_denied`，Agent 沒有進入 `MemoryService`。現在 backend 保留 explicit-save
  permission 與 memory type validation，並以 backend parsed explicit intent 作為 content/type
  persistence authority。普通陳述仍拒絕 `save_memory`。
- Draft leakage 來自 Chat 只有單一 `question` state。現在使用 `draftsBySessionId`，只存在
  frontend memory，切換 session 時讀寫各自的 draft；沒有 draft table 或 schema change。
- Retry duplicate 的 ownership 已確認：第一次 failed request 在 provider failure 前已保存 user
  message，frontend 沒有 optimistic user bubble。第二次相同 query 原本會再 append 一筆 user
  message。現在只在最新 message 是相同 pending user message 且沒有 assistant result 時 reuse，
  不建立大型 idempotency framework。
- Transform failure 來自 tool-capable Agent 的 blank-evidence guard 把 direct answer 視為
  `insufficient_info`。現在只對固定 bounded transform shapes 啟用 conversation-only Agent
  path，禁止 tools、enterprise citations 與跨 session context；沒有同 session assistant
  answer 時直接回應無可重述內容。

### Automated Evidence

- 先新增 red tests，再完成 minimal fix。
- Public conversation API regression：explicit save success、non-explicit rejection、save
  failure no fake success、retry reuse、Chinese transform、English rephrase、New Chat isolation。
- Backend full regression：`745 passed, 5 skipped`。
- Frontend tests：`46 passed`。
- Frontend production build：PASS。
- `compileall`、`git diff --check`：PASS。

### Manual Verification

This entry predates the final 5.0.1 completion sync recorded at the end of this log.

### Status

- `5.0=manual_verification`。
- `5.0.1=manual_verification`。
- `6.0=planned`，本輪未開始 SSE Streaming、MCP protocol migration 或其他 6.0 work。

## 2026-09-06 Agent Memory Recall and Transform Follow-up

### Browser findings

- Tool-capable memory recall 缺少明確 routing contract。Provider 可直接回
  `INSUFFICIENT_INFO`，沒有選擇 `search_memory`。
- `search_memory` 接受 provider 的 `top_k`，但 Agent adapter 沒有明確保存 4.0.2 的
  direct/broad retrieval bounds。
- Broad wording `你有記住我什麼資訊？` 沒有進入既有 broad recall semantics。
- Conversational transform prompt 禁止新增 enterprise facts，但沒有明確允許重述 previous
  assistant answer 內已有的內容。Production provider 可因此回 `INSUFFICIENT_INFO`。

### Implementation

- Agent prompt 現在要求 saved personal context 使用 `search_memory`，並把完整 recall request
  作為 query。Direct recall 要求一筆；broad recall 最多三筆。
- `MemorySearchTool` 在 adapter boundary 套用 direct `effective_top_k=1` 與 broad
  `effective_top_k<=3`。`MemoryService` 的 ranking、relevance floors、temporal ordering、
  reranker 與 dedup 未修改。
- ToolResult 給下一次 provider iteration 的 bounded metadata 包含 retrieval mode、requested
  與 effective `top_k`、hit count、best similarity。Memory context 與 Knowledge evidence 使用不同
  authority；memory citations 固定為空。
- Transform prompt 明確允許翻譯、重述、摘要或簡化 previous assistant answer 內已有的內容，
  但禁止新增 claim、呼叫 tools 或建立 citations。New Chat 沒有 previous assistant answer 時不會
  取得其他 session context。

### Automated evidence

- Public conversation orchestration 與 Agent/Memory focused regression：`63 passed`。
- Backend full regression：`751 passed, 5 skipped`。
- Frontend tests：`46 passed`。測試仍輸出既有 React list key warning，未造成失敗。
- Frontend production build：PASS。
- `compileall`：PASS。
- `git diff --check`：PASS。

### Manual verification

這些 browser findings 後續被判定為 non-blocking follow-up，並移至 5.0.3 與 5.0.4；completion
結論記錄於本 log 尾端。

### Status

- `5.0=manual_verification`。
- `5.0.1=manual_verification`。
- `6.0=planned`，本輪未開始 Native MCP integration 或其他 6.0 work。

## 2026-09-06 Broad Saved-Preference Recall Follow-up

### Failure boundary

Public conversation red test 在 provider iteration 1 重現問題。`search_memory` schema 無法表達
saved-memory category，Agent prompt 也沒有說明 category filter。Provider 因此回
`INSUFFICIENT_INFO`，沒有執行 memory tool。Preference category query 即使進入 tool，既有
broad 判斷也會把它當作 direct recall。

### Implementation

- `search_memory` 新增 optional allowlisted `memory_type` argument。Preference query 可傳
  `memory_type=preference`。
- `MemoryService` 與 `MemoryRepository` 沿用現有 schema，在 owner、active scope 內先套用
  optional type filter，再執行既有 vector ranking。Data model 沒有變更。
- Preference category query 使用 compact token semantics 判斷 plural/broad intent，不建立
  phrase-by-phrase regex list。Direct recall 仍取一筆；broad recall 最多三筆。
- ToolResult 的 bounded metadata 包含 retrieval mode、memory type、effective `top_k`、hit count
  與 best similarity。Saved memory 不建立 enterprise citations。

### Automated evidence

- 五個中英文 preference category shapes 與 broad top-three public API tests：`6 passed`。
- Public conversation、Agent 與 Memory focused regression：`70 passed`。
- Backend full regression：`758 passed, 5 skipped`。
- Frontend tests：`46 passed`。測試仍輸出既有 React list key warning，未造成失敗。
- Frontend production build、`compileall` 與 `git diff --check`：PASS。

### Manual verification

這項 browser finding 後續被列為 5.0.3 的 non-blocking follow-up；completion 結論記錄於本 log 尾端。

### Status

- `5.0=manual_verification`。
- `5.0.1=manual_verification`。
- `6.0=planned`，本輪未開始 Native MCP integration 或其他 6.0 work。

## 2026-09-06 Live Provider Memory and Transform Parity Follow-up

### Live boundary evidence

- Browser Case B 對應兩次 agent workflow。兩次都是 `tool_calls_used=1`、
  `used_saved_memory=false`、`retrieved_chunk_count=0`、`citation_count=0`，最後為
  `insufficient_info`。舊 workflow metadata 沒有 tool name 與 retrieval metrics，無法在事後
  區分錯選 tool 與 memory no-hit。
- Browser Case C 對應 `tool_calls_used=0`、`used_saved_memory=false`、
  `citation_count=0` 與 `insufficient_info`。Request 已進 Agent runtime，但既有 transform
  classifier 沒辨識「用英文說你剛才的回答」。
- Workflow metadata 現在保存 available tool count、provider termination type、memory fallback
  flag、conversation authority flag，以及 memory mode、type、effective top-k、hit count 與 best
  similarity。Metadata 不含 conversation content、memory content、provider response 或 reasoning。

### Implementation

- Clear saved-memory recall 若在 provider iteration 1 收到 `INSUFFICIENT_INFO` 且沒有 tool call，
  backend 會 deterministic 呼叫 `search_memory`。Fallback 沿用 owner scope、type filter、
  relevance gate 與 direct/broad bounds，不搜尋 Knowledge。
- Preference category fallback 傳入 `memory_type=preference`。Broad recall 最多三筆；
  `project_context` 不會進入 preference ToolResult。
- Conversational transform 改用 previous-answer reference 與 translate/rephrase/summarize/simplify
  behavior 的 bounded 組合判定。Same-session assistant context 存在時使用 conversation authority；
  New Chat 沒有 previous assistant answer 時仍 fail closed。
- Final grounding guard 分開檢查 Knowledge evidence、saved-memory evidence 與 same-session
  conversation authority。Conversation transform 不建立 enterprise citation。

### Automated evidence

- Production-like Agent/Memory/Conversation focused regression：`80 passed`。
- Backend full suite 曾通過 `766 passed, 5 skipped`；加入兩個 broad-memory coverage 後，完整
  suite 的既有 concurrent idempotency test 連續兩次失敗，其餘 `767 passed, 5 skipped`。排除該
  test 的 suite 為 `767 passed, 5 skipped, 1 deselected`，該 test 單獨重跑為 `1 passed`。本輪未
  修改 idempotency architecture。
- Frontend tests：`46 passed`。測試仍輸出既有 React list key warning，未造成失敗。
- Frontend production build與 `compileall`：PASS。

### Manual verification

這些 browser findings 後續被列為 5.0.3 與 5.0.4 的 non-blocking follow-ups；completion 結論記錄
於本 log 尾端。

### Status

- `5.0=manual_verification`。
- `5.0.1=manual_verification`。
- `6.0=planned`，本輪未開始 Native MCP integration 或其他 6.0 work。

## 2026-09-06 Final Response Language Resolution

### Scope

- 新增單一 `response_language` resolver，只讀 current user message。
- Explicit language instruction 優先；其餘依 English、Chinese plus English、Traditional Chinese
  script signal、Simplified Chinese script signal 判定。
- Ambiguous shared Chinese characters 使用 Knowvia 的 Traditional Chinese default，不建立 language
  detection framework。

### Integration

- Agent system prompt、Knowledge QA prompt、conversation recall/transform prompt 都收到同一個
  `FINAL_RESPONSE_LANGUAGE` contract。
- Insufficient-info、沒有 conversation context 與 save confirmation 的 backend fallback 依 current
  user message 使用 English、繁體中文或簡體中文。
- Tool selection、Memory retrieval、Knowledge retrieval、citations、`Used saved memory` 與 authority
  boundary 沒有使用 response language，也沒有被改動。

### Automated evidence

- Response-language unit 與 public conversation/Agent/QA regression：`79 passed`。
- Backend full suite：`779 passed, 5 skipped`。
- 既有 frontend regression 保持 `46 passed`，production build PASS。

### Status

- `5.0=manual_verification`。
- `5.0.1=manual_verification`。
- `6.0=planned`，本輪未開始 Native MCP integration 或其他 6.0 work。

## 2026-09-06 Broad All-Memory Recall and Implicit Transform Follow-up

### Root causes

- Generic `What do you know about me?` 沒有被保留為 backend memory intent，且 adapter 在缺少 backend intent metadata 時會採用 provider 傳入的 `memory_type=preference`，所以 `project_context` 被排除。
- Action-only 的 `用中文說`、`In English`、`簡單一點` 沒有通過既有的 previous-answer reference 條件，因而落入一般 QA path，最後被 no-evidence guard 回覆 `insufficient_info`。

### Implementation

- Generic saved-memory query 現在會進入 Agent memory intent metadata。當 backend 已取得原始 query 時，Memory adapter 只接受 backend 判定的 category；generic broad query 保留 `memory_type=None`，provider 不能自行縮窄結果。
- Broad category recall 維持 `preference` filter 與最多三筆結果。All-memory broad recall 維持跨允許 memory types 的 bounded selection，不改 ranking、relevance floor、temporal ranking、reranker 或 semantic dedup。
- Conversation transform 改用 bounded action/reference 組合，支援 implicit language switch 與 simplify request。Same-session 只使用 immediately available assistant context，transform path 不提供 Knowledge/Memory tools，也不建立 enterprise citations；New Chat 沒有 previous assistant answer 時仍 fail closed。
- Final grounding guard 繼續分開處理 Knowledge evidence、saved memory 與 same-session conversation authority；合法 transform answer 不會因 Knowledge context 為空而被覆寫。

### Automated evidence

- 新增 public conversation seam 的 generic all-memory narrowing test，以及 `用中文說`、`用中文說你剛才的回答`、`In English`、`簡單一點` 與 New Chat isolation tests：`6 passed`。
- Memory、transform、Knowledge focused regression：`48 passed, 24 deselected`。
- Backend full suite：`785 passed, 5 skipped`。
- Frontend tests：`46 passed`。既有 React list key warning 仍輸出，但未造成失敗。
- Frontend production build、`compileall` 與 `git diff --check`：PASS。
- 未保留 `[DEBUG-...]` instrumentation。

### Manual verification

這些 browser findings 後續被列為 5.0.3 與 5.0.4 的 non-blocking follow-ups；completion 結論記錄
於本 log 尾端。

- `What do you know about me?` 包含 Riley 與 relevant preferences，顯示 `Used saved memory`，且沒有 enterprise Sources。
- `What are my preferences?` 只返回 preference，不包含 Riley。
- 同 session 依序送出 `What do you know about me?`、`用中文說`、`簡單一點`，確認繁體中文 transform 與簡化結果。
- New Chat 送出 `用中文說`，確認不會取得前一 session 的 assistant answer。

### Status

- `5.0=manual_verification`。
- `5.0.1=manual_verification`。
- `6.0=planned`，本輪未開始 Native MCP integration 或其他 6.0 work。

## 2026-09-06 5.0 and 5.0.1 Completion Documentation Sync

### Browser manual verification conclusion

- Core 5.0 goals are verified: bounded Knowledge/Memory/save tool flow、explicit save policy、persistence、grounding boundary、citation behavior 與 session isolation。
- Core 5.0.1 browser acceptance is verified: explicit save、exact duplicate、Memory Inspector persistence、session-specific drafts、same-session English to Chinese transform 與 New Chat isolation。
- Remaining browser observations are non-blocking follow-ups。Generic broad recall 的 single-memory 或 `insufficient_info` behavior 移至 5.0.3；`用中文說`、`你簡單說` 的 implicit transform behavior 移至 5.0.4。

### Roadmap sync

- `5.0=done`。
- `5.0.1=done`。
- `5.0.2=planned`，保留給 Native MCP Protocol Integration。
- `5.0.3=planned`，Broad All-Memory Recall Hardening。
- `5.0.4=planned`，Same-Session Conversational Transform Hardening。
- `6.0=planned`。

### Scope

本輪只更新 roadmap 與 daily log。沒有修改 runtime、tests、migration、dependencies 或 frontend，
也沒有 commit、push、merge、stash、reset 或 clean。

## 2026-09-06 5.0.2 Native MCP Protocol Integration

### Focused discovery

- 既有 `src/mcp` 只是 `AgentToolRegistry` 與 `AgentToolAdapter` 的 in-process alias，沒有 native MCP SDK、server、transport 或 protocol entrypoint。
- `search_knowledge`、`search_memory`、`save_memory` 已在 `src/agent/tools.py` 定義 schema、owner boundary、citation / saved-memory authority 與 explicit-save policy。
- Internal Agent 直接使用同一個 `AgentToolRegistry`，沒有改成 MCP self-call。
- Single-user auth contract 的 authoritative owner 是 backend 固定的 `local`；本輪沒有擴張 OAuth、RBAC 或 multi-tenant identity。

### Implementation

- 加入 official Python MCP SDK v1 line，使用 low-level `Server` 與單一 stdio transport。
- 新增 `NativeMCPServer`，只從既有 registry 宣告三個 allowlisted tools，並將 native `tools/call` mapping 回既有 `ToolResult`。
- MCP server 建立 server-side `ToolContext`。MCP arguments 不接受 `owner_id`、`user_id`、`tenant_id` 或 explicit-save authorization；沒有 trusted explicit-save metadata 時，`save_memory` fail closed。
- Native result 保留 bounded structured evidence、backend citations 與 saved-memory authority；錯誤只回傳 bounded error code / message，不回傳 stack trace 或 provider detail。
- 新增 `python -m src.mcp.server` local stdio entrypoint；production builder 重用既有 `ProductionChunkRetriever`、`MemoryService` 與 tool registry。

### Automated evidence

- Native MCP protocol integration：`10 passed`，涵蓋 initialization、allowlist discovery、schema、knowledge、memory、authorized / unauthorized save、owner override、malformed arguments、unknown tool、timeout 與 safe tool error mapping。
- Focused Agent / tool / memory / conversation / QA regression：`90 passed`。
- Backend full suite：`793 passed, 5 skipped`。
- Subprocess stdio probe：`initialize` 與 `tools/list` 成功，只發現 `save_memory`、`search_knowledge`、`search_memory`；authorized save 與 production unauthorized save 均經 native `ClientSession → stdio → tools/call` 驗證。

### Manual verification

- Native MCP `initialize` 成功。
- `tools/list` 只公開 `search_knowledge`、`search_memory`、`save_memory`。
- `search_knowledge` 與 `search_memory` 可透過 native `tools/call` 正常執行。
- Production stdio direct `save_memory` 在沒有 trusted explicit-save context 時回傳 `permission_denied`。
- Client 無法透過 `owner_id`、`explicit_save=true` 或其他 arguments 自行取得 owner / persistence permission。
- Authorized save 已由 automated native subprocess MCP path 驗證；保存內容來自 trusted context。
- Internal Agent 仍使用既有 `AgentToolRegistry`，沒有改成 self-MCP call；既有 Knowledge、Memory、Agent behavior 沒有 regression。
- Browser 沒有新增 MCP UI。

### Roadmap state

- `5.0.2=done`。
- `5.0.3=planned`。
- `5.0.4=planned`。
- `6.0=planned`。

### Scope confirmation

本輪未開始 5.0.3、5.0.4 或 6.0；未加入 remote MCP、SSE、OAuth、RBAC、multi-tenant auth、new Agent tools、memory ranking 或 conversation transform changes。

## 2026-09-06 6.0 SSE Streaming and UX Hardening

### Implementation

- 保留 `POST /api/conversations/{session_id}/messages`，新增同一 orchestrator、Agent runtime 與 persistence path 共用的 `/messages/stream` endpoint。
- 新增 bounded SSE event sink。Public events 只有 `execution_status`、`answer_delta`、`citations`、`error` 與 `done`；每個 event 帶 `run_id` 與 monotonic `sequence`。
- Agent tool execution 只映射為 `searching_knowledge`、`searching_memory`、`saving_memory`；answer generation 映射為 `generating`。沒有輸出 prompt、tool arguments、raw tool result 或 reasoning。
- 目前 provider contract 只有完整 `generate()`。Backend 會在 final answer 完成後以 deterministic Unicode-safe chunks 發送 `answer_delta`，沒有修改 Provider architecture。
- Partial answer 只存在 frontend memory。Assistant canonical message、citation metadata 與 saved-memory metadata 只在 successful completion path 寫入；provider failure 不建立 fake assistant success。
- Frontend 使用 `fetch` POST、`ReadableStream`、streaming `TextDecoder` 與 SSE frame parser。active run 期間停用 input，session switch 會 abort 舊 stream 並檢查 session／run identity。

### Automated evidence

- Backend streaming tests：`8 passed`。
- Backend full suite：`804 passed, 5 skipped`；`tests/test_api_idempotency.py::test_concurrent_claims_have_one_owner` 在 full-suite 下出現 scheduler-sensitive failure，隔離重跑通過。該既有測試與 implementation 未在本輪修改。
- Frontend full suite：`53 passed`。
- Frontend production build、Python `compileall` 與 `git diff --check`：PASS。
- 覆蓋 ordered lifecycle、monotonic sequence、multilingual delta reconstruction、citations、saved-memory metadata、explicit save、insufficient info、safe error、provider failure persistence、session race、partial failure 與 existing keyboard/session regressions。

### Manual verification

2026-09-07 browser manual verification PASS：`Searching knowledge…` 與 `Generating answer…` 可見，answer 會 progressive rendering，完成後 `Sources · N` 正確附屬於 assistant message。

### Roadmap state

- `6.0=done`。
- `6.0.1=done`。
- `7.0=planned`。

### Scope confirmation

本輪未開始 5.0.3、5.0.4 或 7.0。未加入 WebSocket、GraphQL subscription、new Agent tools、MCP over SSE、native provider streaming、automatic memory、retrieval architecture changes、conversation summarization、distributed cancellation 或 replay／resume。

## 2026-09-07 6.0.1 Visible SSE Progressive Rendering

### Discovery

- Browser manual report：`Searching knowledge…` 可見；`Generating answer…` 不可見；answer 一次完整出現；Sources 正常。
- 原本 Agent timeline 為 `provider-1 → searching_knowledge → provider-2 → generating`。`generating` 在 final provider call 完成後才發送。
- 原本 backend 在 task 完成後一次把所有 `answer_delta`、citations 與 `done` 放入 queue。Frontend parser 可能在同一個 `ReadableStream.read()` callback 內同步 dispatch 多個 frame，React 因此只產生一次可見 render。
- 初始修正階段的 CUA browser service 與 local Vite HTTP probe 無法取得 browser paint timing；後續 2026-09-07 browser manual verification 已完成並 PASS。Timing 判斷同時使用 local ASGI、Agent timeline 與 frontend event tests。

### Implementation

- Agent runtime 在已完成 tool execution、即將進入下一次 provider generation 前發送 `generating`；explicit save 已完成時不新增 generation status。
- Backend completion events 改為每次只產生一個 event，再交由 async generator yield；不使用 blocking sleep 或人工長 latency。
- Deterministic answer delta default chunk size 改為 32 Unicode characters。Chunk concat 仍等於 canonical answer。
- Frontend event handler 支援 async return，對 `execution_status` 與 `answer_delta` 等待 `requestAnimationFrame`，非 browser environment 使用 zero-delay task fallback，讓 React 有 paint boundary。
- 保留既有 provider contract、sync endpoint、Agent tool policy、citation authority、memory policy 與 persistence behavior。

### Automated evidence

- Agent timing、stream lifecycle、delta reconstruction 與 disconnect regression：`11 passed`。
- Frontend full suite：`54 passed`。
- Backend full suite：`807 passed, 5 skipped`。
- Frontend production build、Python `compileall` 與 `git diff --check`：PASS。

### Manual verification

2026-09-07 browser manual verification PASS：`Searching knowledge…` 可見，`Generating answer…` 可見，answer 會 progressive rendering，完成後 `Sources · N` 正確附屬於 assistant message。SSE UI lifecycle 符合本輪人工驗收要求。

### Roadmap state

- `6.0=done`。
- `6.0.1=done`。
- `7.0=planned`。

### Scope confirmation

本輪未開始 5.0.3、5.0.4 或 7.0。未加入 provider-native token streaming、WebSocket、GraphQL subscription、new Agent tools、MCP over SSE、retrieval architecture changes、automatic memory、conversation summarization、distributed queue 或 SSE replay／resume。

## 2026-09-07 6.0.2 Independent Chat Pane Scrolling and Bottom Composer UX

### Focused discovery

- 原本 Chat DOM 順序是 `header → ask-form → result-region`，`.chat-layout` 沒有 bounded height，因此長對話會撐高整個 document，composer 也會跟著 page flow 移動。
- Follow-up 檢查確認 `app-shell` 與 `main` 沒有 `overflow:hidden` ownership；desktop `.sidebar` 使用 `sticky + min-height`，會跟著被長 content 撐高的 grid row 參與 page flow；`.conversation-panel` 也沒有把 pane overflow 封裝起來。這使 body/page 成為實際 root scroll owner，session pane 與 Chat pane 可能互相帶動。
- Target ownership 為 app shell / main bounded viewport、Global Navigation full-height no-scroll、session pane hidden overflow + `.conversation-list` scroll、Chat pane hidden overflow + `.chat-history` scroll；mobile drawer 仍保留 fixed overlay。
- Browser 100% zoom follow-up 顯示右側仍有 viewport allocation 問題：hero 在 history 之外且過高，4-row composer 也壓縮 message area，導致 cursor 放在 hero 時不能捲動 conversation。

### Implementation

- Chat pane 改為 bounded flex column。`chat-history` 使用 `flex: 1`、`min-height: 0` 與 `overflow-y: auto`；composer 放在 history 之後並維持 normal flow，不使用 viewport-level fixed positioning。
- App shell、`main`、`.chat-layout`、`.conversation-panel` 與 `.chat-pane` 補上 bounded height、`min-height: 0` 與 overflow ownership；`.conversation-list` 補上 flex growth，確保 session list 是唯一的 session pane vertical scroll container。
- Hero header 移入 `.conversation-scroll`，讓 hero、message、citation 共用右側 scroll owner；有 conversation content 時使用 compact header，empty conversation 保留較大的 hero。
- Composer 改為 `rows=2`，依文字內容 auto-grow，最大高度 156px，超過後由 textarea 自己 vertical scroll；保留原有 submit、IME 與 active-run 行為。
- Visual token 收斂為 `--ink: #182019`、`--accent: #a8b39a` 與 `--accent-hover: #cbd2c1`；sidebar 改為 solid background，移除 neon gradient、圓形旋轉 `K` icon 與 fluorescent selected state。
- Chat composer 改為低密度 inline form，隱藏視覺 label、縮短 metadata、縮小 action button、移除 card shadow；placeholder 使用較小字級。
- 新增 near-bottom 判斷。使用者距離 history 底部 80px 內時，新的 message 或 streamed delta 會跟隨到底部；使用者往上閱讀後保留目前 scroll position。
- Desktop 保留 conversation sidebar。Mobile 保留 drawer，並在窄 viewport 收斂既有 Chat header、composer spacing 與 textarea 高度，確保 composer 與最後一則 message 不互相遮住。
- 保留 SSE progressive rendering、execution status、Sources disclosure、Used saved memory、Enter / Shift+Enter、IME safety、active-run disable、session switching、New Chat 與 AbortController behavior。

### Automated evidence

- 新增 `ChatLayout.test.tsx`：app shell / Global Navigation / session list / Chat history scroll ownership、hero state、hero scroll ownership、bounded composer visual sizing / auto-grow、restrained sidebar token 與 text-only brand、near-bottom auto-follow、上讀保留位置與 mobile drawer interaction：`6 passed`。
- Frontend full suite：`60 passed`。
- Frontend production build 與 `git diff --check`：PASS。
- Existing React `ConversationSidebar` list key warning 仍存在，但未造成測試失敗。

### Manual verification

- Local browser structural probe：mobile viewport 的 document 沒有額外 page overflow，conversation scroll area 有獨立 scroll range，composer 為 normal flow 並與 Chat pane bottom 對齊；fresh load 後最後 assistant message 在 history 可見範圍內。
- 2026-09-07 browser manual verification PASS（desktop 100% zoom）：Global Navigation 固定 full-height；Conversation session list 與 Chat conversation 可獨立 scroll；compact bottom composer 保持在 Chat pane 底部；不需要 browser zoom out。
- Knowledge 與 Memory 在 desktop 100% zoom 可正常向下 scroll，沒有被 app shell 裁掉。

### Roadmap state

- `6.0=done`。
- `6.0.1=done`。
- `6.0.2=done`。
- `7.0=planned`。

### Scope confirmation

本輪只修改 frontend Chat layout、scroll follow behavior 與 frontend regression tests。未修改 SSE、Agent、Memory、Knowledge、backend contract 或 7.0 evaluation work。

## 2026-09-07 6.0.2 Knowledge Scroll and 6.0.3 Explicit Save Streaming Regression

### Focused discovery

- Knowledge 與 Memory route 共用 `main` 的 bounded hidden overflow，但 `.surface--quiet` 原本只是一般 block，沒有 `height`、`min-height` 與 vertical scroll ownership。`main` 因而在 100% browser zoom 裁掉後續 Indexed Sources，body 也不能接手 scroll。
- Knowledge source records 仍由 `/api/knowledge/sources` 回傳，資料沒有消失。Memory Inspector 使用同一個 route surface pattern，因此一併納入 regression。
- Exact request `記住我的公司叫做Knowvia` 會被 `detect_explicit_save_intent` 分類為 `project_context`。Agent 已選取 `save_memory`，orchestrator 也已傳入 trusted `explicit_save_allowed`、content 與 memory type。
- 失敗發生在 `MemorySaveTool`：provider 傳入合法但不同的 `memory_type=preference`，tool 回傳 `permission_denied`。Agent termination reason 是 `permission_denied`，workflow failure reason 是 `AUTHORIZATION_FAILED`，orchestrator 再包成 `AGENT_RUNTIME_FAILED`，SSE 只送出 `saving_memory` 後的 `error`。`MemoryService`、embedding、persistence 與 final `done` 尚未被執行。

### Implementation

- `.surface--quiet` 現在是 `flex: 1`、`height: 100%`、`min-height: 0`、`overflow-y: auto` 的 route scroll container；Knowledge 與 Memory 都留在 bounded app shell 內，Global Navigation 不受影響。
- 新增 Knowledge long inventory regression，確認最後一筆 source 仍存在於 route scroll owner；Memory route 同步檢查相同的 computed layout contract。
- `MemorySaveTool` 保留 provider tool argument schema validation，但 persistence 只使用 trusted explicit-save content/type。provider 的分類誤差不再阻斷原始明確請求，owner、authorization、embedding、duplicate 與 persistence policy 沒有放寬。
- 新增 exact company statement 的 streaming regression 與 cross-session recall case。普通陳述仍拒絕 save，duplicate 仍回 `already_saved`。

### Automated evidence

- TDD red reproduction：Knowledge route test 的 `.surface--quiet` `flexGrow` 為空；exact save streaming test 在 `saving_memory` 後收到 `error`，且沒有 memory row。
- TDD green：Knowledge route scroll owner、最後一筆 source、Memory route regression：`7 passed` focused frontend layout tests。
- Explicit save streaming、MemoryService、Agent runtime、Knowledge/citation/SSE focused backend tests：PASS。
- Backend suite excluding `tests/test_native_mcp_protocol.py`：`799 passed, 5 skipped`。
- Native MCP targeted regression 使用 repository `.venv`：`tests/test_native_mcp_protocol.py`，`10 passed`。涵蓋 `initialize`、3 個 allowlisted tools、knowledge / memory calls、unauthorized direct save、owner / explicit-save spoof rejection 與 trusted server-side authorized save。
- Frontend full suite：`62 passed`。production build 與 `git diff --check`：PASS。
- 未加入 debug instrumentation，沒有留下 `[DEBUG-...]` log。

### Manual verification

- 2026-09-07 browser manual verification PASS（desktop 100% zoom）：workspace independent scrolling 已確認；Global Navigation full-height；Conversation session list 與 Chat conversation 可獨立 scroll；Knowledge / Memory 可向下 scroll；compact bottom composer 保持在 Chat pane 底部；不需要 browser zoom out。
- Explicit save 已確認：`記住我的公司叫做Knowvia` → `Saving memory…` → `Memory saved`。
- Memory Inspector 可看到 persisted memory；New Chat 的 `我的公司叫什麼？` 正確回答 `Knowvia`，顯示 `Used saved memory`，且沒有冒充 enterprise Sources。
- Duplicate save 行為正常；普通陳述不會自動建立 `LongTermMemory`。

### Roadmap state

- `6.0.2=done`。
- `6.0.3=done`。
- `7.0=planned`。

### Scope confirmation

Knowledge scroll 只修改 frontend route layout 與 regression test。Explicit save 只修正既有 Agent/tool path 的 trusted explicit-save handling；未修改 API contract、SSE protocol、Knowledge retrieval、citation authority、Memory Inspector contract 或 7.0。

## 2026-09-07 7.0 Evaluation and Demo Hardening

### Focused discovery

- Existing `tests/evals/` contains legacy Notion and step-98/99 evaluation surfaces, but
  no deterministic runner for the current Agent, Memory, MCP and SSE contracts.
- Existing Agent runtime, scripted provider tests, native MCP protocol tests, SSE tests,
  `mock_data/` PDFs, `scripts/preflight.py` and frontend workspace were reusable.
- Formal browser verification is still a separate gate. This implementation does not
  use live LLM routing, live URLs, live OCR, private Notion or a production database.

### Implementation

- Added `eval/golden_set.yaml` with 18 scenarios covering Knowledge retrieval,
  insufficient information, PDF/URL/Image citations, conversation context, session
  isolation, explicit and non-explicit memory, cross-session recall, authority separation,
  unknown tools, invalid arguments, max tool calls, native MCP discovery/permission and
  both SSE lifecycle branches.
- Added `python -m eval.run_agent_eval`, which reuses the bounded Agent runtime, tool
  registry, native MCP protocol and deterministic fixture provider. Reports contain only
  scenario ids, categories, bounded checks and failure reasons.
- Added a small Atlas fixture containing metadata-level PDF, URL and Image evidence;
  it does not copy production source text.
- Added `scripts/demo_preflight.py` for read-only health, readiness, migration, frontend,
  indexed-source, Agent allowlist and native MCP checks.
- Updated current-state README, architecture/workflow/quality/deployment docs and the
  roadmap. The README now separates completed runtime capabilities from deferred
  YouTube indexing, Notion UX, provider-native streaming and 5.0.3/5.0.4 follow-ups.

### Automated evidence

- 7.0 focused tests: `4 passed`.
- Deterministic Golden Set: `18/18` scenarios passed, pass rate `1.0`.
- Report command: `uv run --no-env-file --frozen python -m eval.run_agent_eval`.
- Demo preflight report builder covers seven required checks and keeps source names and
  dependency error details out of report detail strings.

### Manual verification

Not yet manually verified. Run the formal browser Demo Story in
`docs/06-deployment-and-demo.md`, then record the observed result here.

### Roadmap state

- `7.0=manual_verification`.
- `5.0.3=planned`。
- `5.0.4=planned`。

### Scope confirmation

本輪未加入新的 Knowledge source、Agent tool、MCP transport、provider-native streaming、
SSE replay/reconnect、retrieval reranker、automatic memory、cloud deployment 或 frontend
visual redesign。沒有修改資料庫 schema，也沒有執行 destructive demo reset。

## 2026-09-07 5.0.3 Semantic Memory Recall Hardening

### Focused diagnosis

- `what database do we use?`、`what DB do we use?`、`我們 DB 用什麼？`、`我們公司的 database 是什麼？` 與 `what datastore do we use?` 不符合原本的 deterministic memory query shape。
- `what is our company size?` 原本會因 `our` 的 broad shape 被誤標為 memory query；本輪將 deterministic shape 收窄到 `my`，project fact 交給 Agent tool selection。
- Generic query 若 provider 沒有選 `search_memory`，原本 Agent 會直接回到 `INSUFFICIENT_INFO`；這是 routing guidance 缺口，不是 memory permission failure。
- LongTermMemory 原本只以 `content` 做 embedding，沒有可分開調整的 retrieval representation。Bounded fixture diagnostic 顯示原始 `DB` representation 對 `database` query 得分為 `0.000000`，更新後 representation 得分為 `0.577350`，direct relevance floor 仍為 `0.40`。
- Bounded fixture 中 unrelated `what frontend framework do we use?` 得分為 `0.000000`，被 relevance gate 拒絕；`what is our company size?` 是否為 negative control 取決於是否存在對應的 explicit saved memory。沒有降低任何 global similarity threshold，也沒有新增 general intent classifier。

### Implementation

- LongTermMemory 新增 nullable `retrieval_text`；既有 rows 由 migration 以 `content` 作為 fallback，新 save 才建立 derived representation。
- Explicit save 保留原始 `content` 與 owner/type/persistence policy。只做 bounded `DB` → `database (DB)` safety normalization；未知 `PG` 不自行展開。Normalization failure 回退到 `content`。
- Memory embedding 使用 `retrieval_text`，Memory Inspector/API 仍使用 `content`。Search query 維持自然 user query，owner scope、top-k、relevance gate 與 zero-citation authority boundary 不變。
- Agent system guidance 與 `search_memory` tool description 明確支援不含 `memory`、`remember` 或 `saved` 的自然 project-memory question。

### Automated evidence

- TDD red：新增 hardening tests 3 failed，分別暴露 missing `retrieval_text`、自然 query no-hit 與 Agent no-tool routing。
- TDD green：`tests/test_memory_recall_hardening.py` `7 passed`；migration regression `3 passed`；memory/Agent/conversation focused regression `88 passed`。
- Backend full suite：`822 passed, 5 skipped`。Frontend full suite：`62 passed`。Frontend production build、Python compileall 與 `git diff --check`：PASS。
- Fresh SQLite migration、existing-memory fallback、owner scope、explicit-save rejection、MCP 與 Golden Set regression 均通過。

### Manual verification

- Browser positive-path verification 已完成：`Remember that our DB uses PostgreSQL.` 可以 explicit save；`What DB do we use?`、`我們公司用什麼 db？` 可透過 saved memory 回答 PostgreSQL；已存在的 company-size memory 也可由 Agent 選擇 saved-memory retrieval 並回答約 1000 人。
- Memory Inspector 仍顯示 original saved content；`Used saved memory` 與 enterprise Sources authority 維持分離。
- Final unrelated-memory negative-control browser verification 尚未完成，因此 `5.0.3` 維持 `manual_verification`。

### Roadmap state

- `5.0.3=manual_verification`。
- `5.0.3.1=planned`。
- `5.0.4=deferred`。
- `7.0=manual_verification`。

### Scope confirmation

本輪未加入 automatic memory extraction、structured-output LLM normalization、large synonym dictionary、global threshold 放寬、retrieval reranker、new tool、MCP transport 或 Knowledge/Memory corpus mixing。

## 2026-09-07 Documentation-only roadmap planning sync

### Planning result

- `5.0.3` implementation 已完成，positive browser verification 已確認，僅保留 final unrelated-memory negative-control browser verification；狀態維持 `manual_verification`。
- 新增 `5.0.3.1 Generalized Semantic Memory Representation=planned`。目標是將 bounded DB/database normalization foundation generalize 為 bounded structured semantic canonicalization，同時保留 explicit-save authority。
- `5.0.4 Same-Session Conversational Transform Hardening=deferred`。
- `7.0=manual_verification` 的 final manual Demo 延至 `5.0.3.1` 完成並重新跑 evaluation 後。
- `2.4 YouTube` 與 `2.5 Notion UX` 維持 `deferred` priority，不阻塞目前 MVP closure。

### New decision

LongTermMemory 的 original `content` 是 user authority。Derived `retrieval_text` 只供
semantic retrieval 使用，不能取得 persistence authority，且只有 explicit-save authorization
通過後才可產生。Canonicalization 不得新增 fact、改 entity/value 或猜 unknown acronym；failure
fallback 到 original `content`。

產品原則：`Save strict; recall forgiving.`

### Scope confirmation

本輪為 documentation-only sync，只修改 roadmap、decision、data contract、quality guardrail
與 daily log；未開始 `5.0.3.1` implementation，也未修改 runtime、tests、migration、
dependencies 或 frontend。

## 2026-09-07 5.0.3.2 Contextual Memory Application

### Diagnosis

- `CURRENT_TOOL_SELECTION`：既有 bounded Agent 將三個 allowlisted tools 交給 provider 的
  `tool_choice=auto`；mixed Knowledge task 的 scripted repro 只選了 `search_knowledge`，
  沒有繼續選 `search_memory`。
- `WHY_SEARCH_MEMORY_IS_NOT_SELECTED`：system prompt 只描述 direct saved-memory recall，
  沒有說明 saved context 在能 materially improve current task 時可與 Knowledge 一起使用，
  也沒有提供 contextual memory query 的形狀。
- `CURRENT_SEARCH_MEMORY_QUERY_CONSTRUCTION`：若 Agent 自己選 `search_memory`，memory
  tool 使用 provider 的 `arguments.query`；只有 deterministic direct-recall metadata 才會
  覆寫 query。原本沒有 task-oriented contextual query contract。
- `CURRENT_TOOL_CHAINING_BEHAVIOR`：既有 runtime 已支援 sequential `ToolCall → ToolResult`
  loop，最多 3 次 tool calls；不需要 planner、第二個 Agent 或新 tool。
- `CURRENT_CONTEXT_ASSEMBLY`：Knowledge 與 Memory 分別保存於 bounded run state；Knowledge
  result 產生 backend citation，Memory result 使用 saved-memory authority 與
  `Used saved memory`，兩者以分開 context 送入 final provider。

### Minimal fix and implementation

- 在 Agent prompt 與 `search_memory` tool description 中加入 materiality rule：mixed task
  可使用兩個 tool，Knowledge-only 不強制 memory，不 dump all memories；contextual call
  使用 task-oriented query 並設定 `retrieval_mode=contextual`。
- `SearchMemoryArguments` 新增 bounded `direct | broad | contextual` mode。Contextual
  retrieval 最多取 3 筆，再使用既有 relevance gate；direct 維持 final best-1，沒有降低
  global relevance floor。
- Memory service 只接受既有三種 retrieval modes；contextual 使用 direct relevance floor
  的 bounded multi-result 行為，保留 owner scope、schema、allowlist 與 persistence policy。
- 未加入 general deterministic intent classifier、planner、new tool、retrieval rebuild、
  synonym dictionary 或 automatic memory write。

### Automated evidence

- TDD red：mixed contextual repro 先因只執行 `search_knowledge` 而失敗；修正後
  `tests/test_contextual_memory_application.py`、streaming、agent eval 與 memory hardening
  focused set 共 `24 passed`。
- Golden Set 已新增 `knowledge-only-does-not-force-memory` 與
  `contextual-memory-application`，完整 `test_agent_eval.py` 通過，Golden Set `20/20`。
- Backend full suite：`828 passed, 5 skipped`。
- Frontend regression：`62 passed`；production build PASS。既有 React list-key warning
  仍會在測試輸出，但不影響本 slice，且未修改 frontend。
- `git diff --check` PASS。

### Manual verification

- `5.0.3` browser positive recall、Memory Inspector original content、`Used saved memory`
  與 unrelated-memory negative controls 已由使用者確認 PASS，狀態更新為 `done`。
- `5.0.3.2` browser verification：`Not yet manually verified.`
- 待驗證 A：Knowledge-only PDF question 應只有 Sources，沒有 `Used saved memory`。
- 待驗證 B：Memory-only recall 應只有 `Used saved memory`，`citations=[]`。
- 待驗證 C：Knowledge + company/project context task 應同時呈現 Sources 與
  `Used saved memory`，且回答只使用 relevant memory。
- 待驗證 D：有 unrelated saved memory 時應 fail closed，不應套用該 memory。
- 待驗證 E：不得因一般 Knowledge query dump all memories；SSE 只顯示 bounded
  searching/generating statuses。

### Roadmap state

- `5.0.3=done`。
- `5.0.3.1=planned`；本輪未開始 implementation。
- `5.0.3.2=manual_verification`。
- `5.0.4=deferred`。
- `7.0=manual_verification`；final Demo 等待 5.0.3.1、5.0.3.2 manual verification
  與重新跑 evaluation 後。
- `2.4 YouTube` 與 `2.5 Notion UX` 維持 `deferred`，不阻塞 MVP closure。

## 2026-09-07 5.0.3.2.1 Contextual Memory Live-Provider Routing

### Browser finding

- Knowledge-only PDF question PASS：顯示 `Sources`，沒有 `Used saved memory`。
- Memory-only `What DB do we use?` PASS：回答 PostgreSQL，顯示 `Used saved memory`。
- Mixed contextual questions FAIL：live provider 在 `search_knowledge` 後直接 final；回答沒有
  套用已保存的 company size 約 1000 人或 SDD/TDD development preference。
- 這是 `5.0.3.2` 的 live-provider routing gap，不是 MemoryService、relevance gate 或
  context authority mixing。

### Diagnosis evidence

- `LIVE_ITERATION_1_DECISION`：provider selected `search_knowledge`。
- `LIVE_ITERATION_2_DECISION`：provider selected final text directly。
- `SEARCH_MEMORY_AVAILABLE`：true；第二輪 request 仍包含 `search_memory`。
- `SEARCH_MEMORY_SELECTED`：false。
- `FINALIZATION_REASON`：Knowledge evidence 已存在，但 request 沒有 post-Knowledge
  contextual re-check contract，live-like provider 因此以 Knowledge-only final path 結束。
- `ROOT_CAUSE`：runtime capability 與 provider tool-selection reliability 分離；原本的
  general system guidance 沒有在 Knowledge ToolResult 後明確要求 provider 保留 original
  personalization intent 並重新檢查 saved context。
- 另一個同一 seam 的問題是 mixed query 可能被舊 deterministic `memory_recall_query`
  metadata 覆寫。當 provider 明確設定 `retrieval_mode=contextual` 時，這會錯誤取代其
  task-oriented query，並可能從 query 文字誤推 `memory_type`。

### Implementation

- Knowledge tool result 處理完成後，runtime 更新第一則 bounded system message，重申
  original user task、Knowledge evidence availability、`search_memory` availability，以及
  contextual application 在 finalization 前的 materiality check。
- Contract 保留 Knowledge-only factual/extraction task 可直接 final；不要求每一題搜尋
  memory，也不加入 keyword classifier、regex routing、planner 或第二個 Agent。
- Provider 明確傳 `retrieval_mode=contextual` 時，Memory tool 保留 provider 的
  task-oriented query，跳過 direct-recall metadata query override，也不從 contextual query
  自動套用 memory-type filter。
- SSE 在 final provider response 確定後才發出一次 `generating`；Knowledge → Memory 的
  internal continuation 不再產生誤導性的 `Generating → Searching saved memory → Generating`。
- 新增 live-like provider behavior regression，只記錄 bounded decision labels、tool names、
  tool availability 與 finalization reason；沒有 raw provider response 或 private source
  content。

### Automated evidence

- Red：live-like provider 在第二輪 direct final，且 `search_memory_available=true`、
  `search_memory_selected=false`。
- Green：live-like routing regression 與既有 contextual flow 通過。
- Contextual、memory hardening、Agent、conversation、streaming focused regression：
  `80 passed`。
- Golden Set：`20/20`。
- Python compileall、`git diff --check`：PASS。

### Manual verification

- `5.0.3.2.1`：`Not yet manually verified.`
- 重新執行 mixed browser probes，確認可見狀態為
  `Searching knowledge… → Searching saved memory… → Generating answer…`，不出現重複的
  `Generating answer…`。
- 確認 final answer 實際包含約 1000 人與 SDD/TDD context，而非只重述 user question。
- 重跑 Knowledge-only、Memory-only、irrelevant-memory 與 no-memory-dump negative controls。

### Roadmap state

- `5.0.3=done`。
- `5.0.3.1=planned`；本輪未開始 implementation。
- `5.0.3.2=manual_verification`。
- `5.0.3.2.1=manual_verification`。
- `5.0.4=deferred`。
- `7.0=manual_verification`；final Demo 仍等待 memory hardening slices 完成並重新跑 evaluation。

## 2026-09-07 5.0.3.2.2 Bounded Context Requirement Selection

### Diagnosis

- Current provider contract 是 `LLMRequest(tool_choice=auto)` 搭配 `LLMResponse.tool_calls`。Provider 可以在 `search_knowledge` 後直接回 final text。
- Post-Knowledge re-check 只能提供 guidance，不能保證 live provider 每次選擇 `search_memory`，因此同一 mixed query 會出現 Knowledge-only 或 Knowledge + Memory 兩種結果。
- Existing `AgentState` 已分開保存 `knowledge_context`、`memory_context` 與 citations；tool registry 也已有 `search_knowledge`、`search_memory`、`save_memory`。這個 slice 不需要重寫 retrieval service 或 MCP surface。

### Structured decision contract

- 新增 typed `ContextRequirementDecision`，只包含 `needs_knowledge`、`needs_memory` 與 bounded `memory_query`。
- `StrictBool`、Pydantic schema、`extra=forbid` 與 conditional validation 拒絕 malformed decision。`needs_memory=false` 時 `memory_query` 固定為 `null`；`needs_memory=true` 時 query 不得為空且最多 500 characters。
- OpenAI-compatible provider 使用 strict JSON schema `response_format`。Selector request 不提供 tools，final request 也不提供 tools。

### Backend execution

- 具 structured output capability 的 provider 先執行 internal selector。Backend 驗證 decision 後固定以 Knowledge、Memory 順序執行 required capability。
- Required retrieval 仍使用既有 tool registry，因此保留 owner scope、argument validation、relevance gate、timeout、citation 與 max 3 tool calls。
- Final generation 只執行一次，收到分離的 `KNOWLEDGE_CONTEXT` 與 `MEMORY_CONTEXT`。Knowledge 是 enterprise claim 與 citation authority；Memory 只作 personalization context。
- Knowledge hit + Memory no-hit 仍可回答 Knowledge。若 decision 要求 Knowledge 但沒有 Knowledge evidence，Memory 不能取代它，結果維持 `insufficient_info` 與 zero citations。
- Explicit save 與 conversation transform 不進 selector；不新增 `needs_save`、planner、classifier、MCP tool 或 public API。Provider 不支援 structured output 時保留既有 bounded loop。

### Automated evidence

- TDD red：新 selector test collection 先因 `ContextRequirementDecision` 不存在失敗；implementation 後 structured selection focused set `19 passed`。
- 新增 4 個 `context-requirement-*` Golden Set cases，包含 Knowledge-only、Memory-only、mixed 與 Memory no-hit fallback；Golden Set `24/24`。
- Backend full suite：`841 passed, 5 skipped`。MCP targeted regression `10 passed`。
- Frontend regression：`62 passed`；production build PASS。既有 `ConversationSidebar` React list-key warning 仍存在，但本輪未修改 frontend。
- `compileall`、`git diff --check` PASS。

### Manual verification

- `5.0.3.2.2`：`Not yet manually verified.`
- 待驗證 Knowledge-only：只有 Sources，沒有 `Used saved memory`。
- 待驗證 Memory-only：只有 `Used saved memory`，沒有 Sources。
- 待驗證 mixed 連續 3 次：`Searching knowledge…`、`Searching saved memory…`、`Generating answer…` 各出現一次，且回答實際使用 relevant company size 與 SDD/TDD context。
- 待驗證 Memory no-hit、unrelated memory 與 explicit save regression。

### Roadmap state

- `5.0.3=done`。
- `5.0.3.1=planned`；本輪未開始 implementation。
- `5.0.3.2=manual_verification`。
- `5.0.3.2.1=manual_verification`。
- `5.0.3.2.2=in_progress`。
- `5.0.4=deferred`。
- `7.0=manual_verification`。

## 2026-09-07 5.0.3.2.3 Same-Session Context Requirement Stability

### Diagnosis

- TURN 1 與 TURN 2 都不符合 `classify_conversation_recall` 或
  `classify_conversation_transform`，因此一般 substantive query 走同一個 structured
  context-selection runtime path。
- TURN 2 的 selector input 會包含 bounded previous user/assistant content；citation 與
  `used_saved_memory` metadata 不會被組進 selector history。
- 原本 selector contract 沒有明確禁止把 previous assistant answer 當成 Knowledge evidence，
  也沒有禁止把 previous saved-fact mention 當成當次 Memory retrieval。Provider 因而可能把
  `needs_knowledge` 降為 `false`，Backend 不再執行 Knowledge，finalization 才錯誤進入
  `insufficient_info`。

### 修正

- Selector contract 明確保留：history 只協助理解 reference 與 task intent；previous answer
  與 previous saved-fact mention 都不是 current authority。
- 新的 substantive request 依當次 answer dependency 選擇 authority；沒有 query equality、
  keyword/regex 或 company-specific special case。
- 既有 conversational transform path 不變，仍可使用 previous assistant answer 作為
  transformation target。

### Automated evidence

- TDD red：authority contract test 在缺少新 selector wording 時失敗；補上 contract 後
  focused context selection suite `12 passed`。
- 新增 same-session repeated substantive query 3-turn regression；每一輪都重新執行
  Knowledge 與 Memory，且不進入 `insufficient_info`。
- Targeted regression：`90 passed`；full backend：`843 passed, 5 skipped`。
- Native MCP：`10 passed`；frontend：`62 passed`；production build PASS。
- Python compileall 與 `git diff --check` PASS。

### Manual verification

- `5.0.3.2.3`：`Not yet manually verified.`
- 待 fresh New Chat 連續送出三次相同 mixed query，確認每次都有 Knowledge、Memory、Sources、
  `Used saved memory`，且不會從第二輪開始變成 `insufficient_info`。

### Roadmap state

- `5.0.3.2.3=in_progress`，implementation 與 targeted automated regression complete。
- `5.0.3.2`、`5.0.3.2.1`、`5.0.3.2.2` 仍待各自 browser verification；`5.0.3.1=planned`。
- `5.0.4=deferred`，本輪未開始 general transform hardening。

## 2026-09-07 5.0.3.2.5 Knowledge Evidence Acceptance Gate

### Diagnosis

- `ChunkRepository.list_production_chunks_by_vector` 使用 PostgreSQL `embedding <=> query_embedding`，即 cosine distance；以 `1 - distance` 轉為 `[0, 1]` normalized similarity，並以 distance ascending 排序。分數越高代表越相關，raw distance 的理論範圍是 `[0, 2]`。
- pgvector 原本有 owner、source kind、embedding non-null、eligibility 與 indexed source status filter，也會先 `LIMIT top_k`，但沒有 explicit relevance floor，top-k candidate 直接成為 accepted evidence。
- lexical fallback 原本以 token overlap、coverage、density 與 phrase bonus 計算 `[0, 1]` score，只保留 `score > 0`，再取 top-k；此 semantics 維持不變。Lexical score 與 vector similarity 雖然數值範圍相同，但意義不同，不直接互相比較。

### Score inspection

- 使用現有 indexed Knowledge 做 read-only bounded audit，沒有輸出 raw chunk text，也沒有修改 production data。
- Positive top-10 score：`0.502864–0.554262`，source diversity `3`。
- Mixed top-10 score：`0.305077–0.358821`，source diversity `3`。
- Negative top-10 score：`0.199179–0.267197`，source diversity `4`。
- Separation：mixed 最低分 `0.305077` 高於 negative 最高分 `0.267197`，間隔約 `0.03788`；positive、mixed 與 negative 存在可用分離區間。
- Threshold feasibility：採 `knowledge_relevance_floor=0.30` 可保留目前 positive 與 mixed top-10 evidence，並拒絕 negative top-10 candidates。此 floor 使用 inclusive comparison。

### Implementation

- pgvector 改為 bounded candidate pool，大小為 `2 × top_k` 且上限 20；candidate 先通過既有 eligibility，再由 retriever 套用 inclusive relevance floor，最後截取要求的 top-k。
- `RetrievalResult`、`KnowledgeSearchTool`、`AgentState` 與 workflow metadata 分別保留 candidate count、accepted evidence count、Knowledge context count、best score 與 relevance floor。
- Knowledge-required 且 accepted evidence 為零時，backend deterministic 回傳 `insufficient_info`、zero citations，並在 structured context path 不呼叫 final provider。Memory hit 不能取代 rejected Knowledge evidence。
- 新增 Golden Set case `unsupported-enterprise-fact-rejected-by-knowledge-gate`，模擬 raw candidates 存在但全部低於 acceptance floor。

### Automated evidence

- TDD RED：新增 low-score pgvector regression 後，原始 implementation 回傳 `accepted_evidence_count=1`；修正後低於 floor rejected、等於 floor accepted。
- Focused retrieval、context requirement、Agent、Golden Set、MCP、PDF、URL、Image regression：`78 passed`。
- Backend full regression：`853 passed, 6 skipped`。
- `compileall` 與 `git diff --check`：PASS。
- Golden Set：`25/25`。

### Manual verification

- `5.0.3.2.5`：`Not yet manually verified.`
- CUA service 無法啟動；sandbox 也不允許 local Uvicorn bind。隔離 port 的 backend 可啟動，但 live OpenAI probe 會新增 conversation/message 並可能傳送 indexed context，因此未在缺少明確授權下執行。
- 待 browser fresh session 驗證 positive PDF answer 有 Sources、acquisition budget 回 `insufficient_info` 且 zero Sources、mixed query 同時使用 Knowledge 與 saved memory；same-session selector/history instability 不在本輪處理。

### Roadmap state

- `5.0.3.2.5=manual_verification`，implementation 與 automated verification complete。
- `5.0.3.2`、`5.0.3.2.1`、`5.0.3.2.2`、`5.0.3.2.3`、`5.0.3.2.4` 維持 `manual_verification`。
- `5.0.3.1=planned`，本輪未開始。
- `5.0.4=deferred`，`7.0=manual_verification`。

## 2026-09-07 5.0.3.2.6 Contextual Facet Retrieval Stability

### Implementation

- Mixed structured selector decision 新增 bounded `ContextualFacet`，每次最多 2 個 facet；每個
  facet 只包含 bounded `id` 與 single-line `text`。Mixed task 禁止再使用 free-form
  `memory_query`。
- Backend 先執行一次 `search_knowledge`，再依 selector 順序以 facet text 各執行一次
  `search_memory(retrieval_mode=contextual)`，總數仍受 max 3 tool calls 限制。
- Direct memory-only structured routing 保留既有 `memory_query` compatibility。Knowledge 與
  Memory context、citation authority 與 `Used saved memory` disclosure 維持分離。
- Partial 或 zero contextual Memory hit 不會使已接受的 Knowledge 變成 insufficient；required
  Knowledge 缺失時，Memory 仍不能取代 Knowledge。
- 兩個 facet search 只公開一次 `searching_memory` SSE status，不把 facet text 或 tool arguments
  暴露給前端。

### Automated evidence

- TDD red：新 facet test 先因 `ContextualFacet` 尚未存在而在 collection 階段失敗；完成 typed
  contract 與 deterministic execution 後，focused context、contextual memory、LLM wire、Agent
  eval、runtime 與 streaming suite 共 `62 passed`。
- Golden Set 改為 `contextual-facet-mixed`、`contextual-facet-partial-memory-miss` 與
  `contextual-facet-all-memory-miss`，驗證 per-facet query、partial/all miss 與 max 3 calls。
- Backend full regression：`858 passed, 6 skipped`；frontend regression：`62 passed`；production
  build PASS。
- `git diff --check`：PASS。

### Manual verification

- `5.0.3.2.6`：Manual verification attempted; acceptance not passed. Findings are consolidated by 5.0.3.3.
- 待 browser 驗證 Knowledge-only、Memory-only、mixed contextual、同一 session 重送三次 mixed
  query，以及 contextual facet 沒有 saved Memory 時仍能產生 Knowledge-only answer。

### Roadmap state

- `5.0.3.2.6=manual_verification`，implementation 與 automated verification complete。
- `5.0.3.2.6` 未標記 `done`，等待 browser manual acceptance。
- `5.0.3.1=planned`，`5.0.4=deferred`，`7.0=manual_verification`。

## 2026-09-07 5.0.3.2.7 Final Synthesis Authority Isolation

### Confirmed cause

- Controlled replay 固定同一份 final `LLMRequest`、Knowledge context 與 Memory context。保留
  previous assistant answer 時連續回傳 sentinel；只移除該 assistant turn 或整段 conversation
  history 後，連續三次都回傳 final text。
- Retrieval aggregates 與 3-call budget 沒有變化。Failure 位於 structured substantive final
  synthesis 的 history contamination，不在 selector、Knowledge retrieval 或 contextual facet
  retrieval。

### Implementation

- Bounded conversation context 新增 user-side projection。一般 substantive request 同時建立完整
  history 給 selector，以及不含 assistant messages 的 context 給 final synthesis。
- Structured selector 完成且 fresh Knowledge/Memory retrieval 結束後，final provider 只接收
  bounded user-side context、current task、當次 Knowledge 與當次 Memory。Backend deterministic
  移除 previous assistant，不只依賴 prompt instruction。
- Final authority contract 明定 previous assistant 不屬於 current Knowledge evidence，也不能決定
  sufficiency；相同 substantive task 必須依 fresh authority 重新 synthesis。
- Conversation recall 與 transform 使用原本 dedicated path。Transform 仍可把 previous assistant
  answer 當作 transformation target。
- `ContextualFacet`、facet query、Knowledge `0.30`、Memory contextual `0.40`、max 3 tool calls、
  citation ownership 與 SSE phase 都沒有修改。

### Automated evidence

- TDD red：history-sensitive provider 在 repeated final request 看見 `[assistant]` 時回
  `INSUFFICIENT_INFO`；修正前 repeated substantive regression 失敗。
- Green：production conversation API 在同一 session 連續三次 mixed query 都 completed，三輪皆有
  Sources 與 `Used saved memory`。第二、三輪 selector 可見 assistant history，final requests
  則不可見；每輪仍執行 Knowledge 加兩次 contextual Memory search。
- Authority、contextual Memory、conversation API、transform/recall、Agent、tool registry、Native
  MCP、SSE 與 controlled replay harness targeted suite：`122 passed`。
- Golden Set：`26/26`。Frontend：`62 passed`。Production build 與 Python compileall：PASS。
- Backend full suite 兩次都只有既有 SQLite concurrency test
  `test_concurrent_claims_have_one_owner` 失敗；其餘 `866 passed, 6 skipped`，該 test 單獨重跑
  `1 passed`。本 slice 未修改 idempotency code。

### Manual verification

- `5.0.3.2.7`：Manual verification attempted; acceptance not passed. Findings are consolidated by 5.0.3.3.
- Fresh New Chat 連續三次送出相同 mixed query。每輪應依序顯示 Knowledge、Memory 與 generating
  status，產生含 Sources、`Used saved memory`、company size 與 development preferences 的答案；
  第二、三輪不得出現 `insufficient_info` 或 Request failed。
- 另重跑 Knowledge-only、Memory-only 與成功答案後的 `用中文說` transform regression。

### Roadmap state

- `5.0.3.2.6=manual_verification`。
- `5.0.3.2.7=manual_verification`。
- Parent `5.0.3.2=manual_verification`。
- `5.0.3.1=planned`，`5.0.4=deferred`；本輪未開始兩者 implementation。

## 2026-09-07 5.0.3.2.8 Substantive Conversation Dependency Contract

### Implementation

- `ContextRequirementDecision` 新增 required `ConversationDependency` enum，只允許 `none` 與
  `required`。Wire schema 明確要求欄位，missing、null、boolean、未知 enum 與 extra field 都
  fail closed。
- Selector 仍接收 bounded 完整 user/assistant history。`none` 的 structured substantive final
  不帶任何 previous conversation；`required` 由 backend 從同 session bounded context deterministic
  選出最近一個 completed user/assistant pair，標記為 `CONVERSATION_REFERENCE_CONTEXT`。
- Recent pair 不會把 trailing failed pending user-only turn 視為 completed。沒有合法 pair 時
  使用既有 `PROVIDER_ERROR` bounded failure semantics。Reference context 只作 interpretation，
  不成為 Knowledge、Memory、sufficiency 或 citation authority。
- Transform、conversation recall、direct Memory routing、ContextualFacet、retrieval floors、
  max 3 tool calls 與 SSE public phases 維持不變。

### Automated evidence

- TDD red：`.8` tests 先因 `ConversationDependency` 尚未存在而 collection fail；完成 schema、
  deterministic pair selection 與 final context branching 後 focused suite 通過。
- Focused context、conversation context、LLM wire、conversation API、streaming、Agent runtime
  與 Golden Set：`98 passed`。
- Golden Set 擴充三個 scenario：standalone `none`、四次 repeated standalone `none`、
  referential `required`；完整 deterministic result `29/29`。
- `git diff --check` 與 Python `compileall`：PASS。

### Manual verification

- `5.0.3.2.8`：Manual verification attempted; acceptance not passed. Findings are consolidated by 5.0.3.3.
- Browser guide：Fresh New Chat 連續四次 mixed query；再驗證 Knowledge-only、Memory-only、
  successful-answer transform；最後以 `What about the second one?` 驗證 recent completed pair
  boundary。Expected SSE 仍只有 existing Knowledge、Memory、Generating、done phases。
- `5.0.3.2.6`、`5.0.3.2.7` 與 parent `5.0.3.2` 持續維持 `manual_verification`。

### Roadmap state

- `5.0.3.2.8=manual_verification`，implementation 與 automated verification complete。
- `5.0.3.2.6=manual_verification`、`5.0.3.2.7=manual_verification`、parent `5.0.3.2=manual_verification`。
- `5.0.3.1=planned`，`5.0.4=deferred`；本輪未開始兩者 implementation。

## 2026-09-07 Documentation-only Context Authority Consolidation Freeze

### Planning result

- Final architecture inspection completed；本輪不重新進行 architecture diagnosis，也不再設計 `.9/.10/.11` patch。
- `5.0.3.3 Context Authority Consolidation` spec 已 freeze，狀態設為 `planned`，並列為下一個正式 implementation slice。
- Frozen target 將 incidental raw conversation history 限制在 bounded `Reference Binding` boundary；後續 substantive context selection、retrieval 與 final synthesis 只接 exact current user message、validated reference bindings 與 fresh authority context。
- Reference Binding contract 不引入 `conversation_dependency`、`reference_status`、`resolved_task` 或 free-form query rewrite。Explicit conversation recall、conversation transform 與 direct Memory compatibility 維持 dedicated paths。
- Current selector fields `needs_knowledge`、`needs_memory`、`contextual_facets` 與 `memory_query` 維持 current implementation；`.3` 不宣稱 Selector Authority Slimming 已完成。
- `ContextualFacet` 上限 2、Knowledge/Memory 分離、partial/all Memory miss graceful degradation、max 3 tool calls 與 current pre-final sufficiency behavior 維持不變。

### Roadmap and follow-ups

- `5.0.3=done`；`5.0.3.2`、`5.0.3.2.6`、`5.0.3.2.7`、`5.0.3.2.8` remain `manual_verification`。
- `.6/.7/.8` 是 diagnostic iterations，其 findings 由 `5.0.3.3` consolidation。
- 四個 post-`.3` planned directions 已記錄：Evidence Readiness、Final Synthesis Contract Hardening、Selector Authority Slimming、Live Semantic Stability Gate。
- `5.0.3.1=planned`、`5.0.4=deferred`、`7.0=manual_verification`；7.0 final closure 等待 `5.0.3.3` stability 與後續 approved memory/context follow-ups。

### Documentation

- Root `README.md` 已重寫為 third-party repository onboarding overview，區分 implemented、current engineering focus、planned follow-ups 與 deferred scope。
- 同步 `docs/01-architecture.md`、`docs/02-data-and-contracts.md`、`docs/03-workflows.md` 與 `docs/04-quality-and-guardrails.md` 的 `.3` target wording。
- Review existing D027、D028 與相關 decisions 後，新增最小 D030，正式記錄 incidental history confinement 與 backend-validatable reference binding principle。

### Scope confirmation

本輪為 documentation-only。未修改 runtime、tests、eval、dependencies、migration、frontend、Docker 或 config；未執行 live provider、Notion 或 private source access；未 commit、push、merge、stash、reset 或 clean。

## 2026-09-08 5.0.3.3 Context Authority Consolidation Implementation

### Implementation

- 完成 D030 frozen topology：structured substantive request 先經 `Reference Binding`，raw
  bounded same-session history 只在此 boundary 可見；selector、retrieval 與 final synthesis
  不再接收 raw conversation transcript。
- 新增 bounded typed `ReferenceBinding` / `ReferenceBindingDecision` contract。Provider 只提出
  `reference_bindings`；backend 持有 exact current user message，並 deterministic 驗證 current span、
  source message identity、role、history window、source span 與最多 2 個 bindings。
- Resolver-visible internal message 保留 `message_id`、`sequence_number`、`role`、`content`；session
  與 owner authorization 由 backend scope 負責。Validated binding 只可作 reference interpretation
  與 deterministic retrieval enrichment，不具 Knowledge、Memory、citation 或 sufficiency authority。
- Structured selector contract 移除 `conversation_dependency`；保留 `needs_knowledge`、
  `needs_memory`、`contextual_facets` 與 `memory_query`。Final structured context 固定使用 exact
  current message、validated bindings、fresh Knowledge 與 fresh Memory。
- 保留 explicit save、conversation recall、conversation transform、direct Memory compatibility、
  ContextualFacet max 2、per-facet Memory retrieval、partial/all Memory miss degradation、max 3 tool
  calls、SSE public lifecycle 與現有 sufficiency/sentinel behavior。

### Automated verification

- Reference binding focused suite：`13 passed`。
- Context, API, conversation, LLM wire 與 authority focused suite：`161 passed`。
- Agent Golden Set：`29/29`；`tests/test_agent_eval.py`：`2 passed`。
- Backend full suite：`889 passed, 6 skipped`。
- Frontend regression：`62 passed`；production build：PASS。
- Python `compileall` 與 `git diff --check`：PASS。
- `uv run` isolated environment 曾因缺少 `mcp` module collection fail；改用 repository `.venv`
  入口後完成上述驗證，未修改 dependency 或納入此 slice。

### Manual verification

- `5.0.3.3`：`Not yet manually verified.` Status 維持 `manual_verification`。
- Browser guide：Fresh New Chat 中連續 10 次送出相同 mixed query，要求 10/10 completed、0 Request
  failed、0 `provider_contract_error`、0 unexpected `insufficient_info`，每次都有 Sources、需要時有
  `Used saved memory`，並確認每次重新取得 fresh Knowledge / Memory authority。
- 另驗證 Knowledge-only、Memory-only、referential follow-up 與成功回答後的 `用中文說` transform；
  SSE 只應顯示既有 public lifecycle，不顯示 bindings、source message ids 或 raw history。
- `5.0.3.2.6`、`5.0.3.2.7`、`5.0.3.2.8`：Manual verification attempted; acceptance not passed.
  Findings are consolidated by 5.0.3.3.

### Roadmap state

- `5.0.3.3=manual_verification`，implementation 與 automated verification complete；不標記 done。
- Parent `5.0.3.2` 與 `.6/.7/.8` 維持 `manual_verification`；`5.0.3.1=planned`、`5.0.4=deferred`、
  `7.0=manual_verification`。
- Post-`.3` 的 Evidence Readiness、Final Synthesis Contract Hardening、Selector Authority Slimming
  與 Live Semantic Stability Gate 仍為 planned，未開始。

## 2026-09-08 Documentation-only closure sync

### Closure result

- `5.0.3.1 Generalized Semantic Memory Representation` 改為 `deferred`。這不是整個 slice
  失敗：original `content`、bounded derived `retrieval_text`、retrieval embedding、explicit-save
  authorization、save-side canonicalization fallback、original-content Inspector/API display、
  direct best-1、broad bounded multi-result、owner scope、relevance gates、natural paraphrase
  recall 與 unrelated-memory negative control 都保留為 current evidence。
- Current `MemoryService.search_memories()` 沒有 query-side semantic normalization，也沒有
  no-hit / low-confidence second-pass semantic retry。相關描述已從 current behavior 改為
  deferred scope。
- `5.0.3.3 Context Authority Consolidation` 的 architecture implementation 與 automated
  verification 保留；unresolved actual-provider stability 與 browser acceptance 改為 `deferred`。
  Deterministic Golden Set PASS 不再被描述為 live-provider stability evidence。
- `7.0 Evaluation and Demo Hardening` 維持 `manual_verification`，並設為下一個唯一主線
  priority。Formal browser Demo Story 是下一個驗證入口。

### Scope confirmation

本輪只修改 roadmap、README、architecture/workflow/data/quality/deployment 文件、decisions 與
daily log。未修改 runtime、tests、eval、frontend、dependencies、migration、Docker 或 config；
未重新進行 architecture diagnosis，也未開始 implementation。

## 2026-09-08 Retrieval Evaluation Foundation and 8.1 Pilot Surface

### Roadmap and decision

- 新增 `D032`：保留 `7.0` 的 Agent Golden Set evidence，但正式 browser Demo Story 尚未完成；
  mainline 暫時轉向 `8.x Retrieval Evaluation Foundation`。
- `8.0=done`：完成 repository inspection、current retrieval path inventory 與 benchmark boundary；
  沒有 runtime change。
- `8.1=manual_verification`：pilot surface 與 automated verification 完成，actual PDF gold
  annotations 與 isolated live pilot 尚待人工 review。

### Frozen corpus and case contract

- Corpus 固定為三份 `mock_data/` PDF：
  - `production_agents`：14 pages，SHA256 `c88c995d6d6deb5d350f12621cabc9dbfa2323047491ad66771989f58100f074`。
  - `chatgpt_tasks_week3`：17 pages，SHA256 `eec40e93d8adb8755f89378dc9778a9219786b4f4b466e696a6aa8e1f54b2591`。
  - `google_agent_patterns`：21 pages，SHA256 `c22fe67748782b2dfe8bff364a9ea3fa852a0eba9da22f100cbe43cd73cbba36`。
- Pilot 共 15 cases：3 single-document factual、3 semantic paraphrase、2 exact-term/
  identifier、2 multi-evidence、2 cross-source discrimination、3 hard-negative。
- Gold 只記錄 stable `source_id`、page 與 manually reviewable evidence anchor；未提交全文、
  embeddings、vectors 或 raw provider response。

### Implementation and verification

- 新增 `eval/retrieval/` independent benchmark surface：YAML schema/loader、corpus identity
  verification、retrieval-only runner、Recall/MRR/full-case/source/evidence/negative metrics 與
  bounded report。
- Runner 明確使用 current `EmbeddingClient`、`ProductionChunkRetriever.retrieve_with_metadata()`
  與 current PDF parser/indexing/chunker path；不呼叫 final LLM，不修改
  `eval/golden_set.yaml`。
- Focused benchmark tests：`7 passed`。
- Combined Golden Set/retrieval evaluation tests：`12 passed`；其中 Golden Set runner 為 `29/29`。
- Repository full suite：`898 passed, 6 skipped`。
- CLI `--help` 與 YAML load/15-case validation：PASS。
- Existing Agent Golden Set baseline：`29/29` PASS，仍為 Agent Contract evidence，不宣稱 retrieval
  quality。

### Baseline result and limitation

- 8.1 actual isolated PostgreSQL+pgvector pilot：`Not run`。安全審核拒絕本次 execution，因為
  current production embedding path 會把三份 PDF extracted chunks 與 queries 傳給 provider；
  未取得對 private source content 的明確對外傳輸授權。
- 因此本輪沒有把 synthetic/local fake 結果寫成 production baseline，也沒有填寫 Recall/MRR
  數值。未執行 optimization、threshold/chunking/embedding/candidate-pool 調整、BM25、RRF 或
  reranker。
- Manual verification：`Not yet manually verified.`。需先 review 15 個 anchors，再由明確授權的
  isolated run 產生 baseline，最後才可判斷 failure label 與下一步 scope。

### Scope confirmation

本輪修改 docs、`dev_state`、independent retrieval eval code 與 focused tests；未修改 runtime、
schema、migration、dependency、Docker、frontend 或 `eval/golden_set.yaml`。未 commit、push、
merge、stash、reset 或 clean。

## 2026-09-08 8.1.1 Pilot Retrieval Benchmark Correctness Review

### Gold annotation review

- 使用 current `PyPDFParserClient` 重新 parse 三份 frozen PDFs，逐一檢查 `rv-001` 至 `rv-015`。
- `rv-003` 改為 parser 實際輸出的 `本地時 間 + timezone ID`，沒有採用未經確認的連續中文字串。
- `rv-007` 改為 `同 小時的 job 放 一 起 -> 查 詢 只 掃 一個 partition`，使 gold 支援 query 的
  partition rationale，而不是只命中 `time_bucket` identifier。
- `rv-009` 改為 required `match: all` evidence：Watcher/Worker responsibilities 與 decoupling
  evidence 都必須存在；`Queue(SQS)` 不再單獨代表 separation。
- `rv-013` 至 `rv-015` 維持 hard-negative。三份 parser output 沒有 `RRF`、`pgvector`、`HNSW`、
  `text-embedding-3-small` 或 embedding model/index terms；semantic negative validity 仍需人工 review。

### Correctness fixes

- `verify_corpus()` 現在在任何 embedding provider construction 前執行 deterministic gold annotation
  preflight。Anchor 使用與 metrics 相同的 NFKC、casefold、whitespace normalization；不存在時以
  `RetrievalAnnotationError` fail closed。
- MRR 對每個 positive case 都計入 denominator；top-5 完全 miss 的 RR 為 `0`。
- Recall@1/3/5 改為先算每 case 的 group recall，再對 positive cases 取 macro mean。
- `completion_rank` 改為 required groups 的 first-hit ranks 最大值；不再被 `[1, 3, 5]` cutoff
  取代。
- Accepted-only observation 不再猜測 `RELEVANCE_GATE_FAILURE`、`CHUNKING_EVIDENCE_BOUNDARY_FAILURE`
  或 `EMBEDDING_RANKING_FAILURE`；root cause 不足時統一為 `UNRESOLVED`。Taxonomy names 保留。
- 移除與 Recall 使用相同 numerator/denominator 的 `evidence_anchor_coverage_at_5` primary metric；
  per-case group hits 仍保留供 anchor review。
- Frozen contract check 改為明確的 8.1 baseline declaration；candidate pool、relevance floor、
  cosine mode 直接引用 repository constants，chunk max/page-aware 由 current chunker signature
  inspection 驗證，未重構 production chunker 或 dependency wiring。

### Verification

- Retrieval benchmark focused suite：`13 passed`。
- Repository full regression：`904 passed, 6 skipped`。
- Agent Golden Set：`29/29`，與 correctness patch 前一致。
- 未執行 live embedding benchmark、未建立 isolated live benchmark database、未產生 baseline metrics。
- `8.1=manual_verification` 維持不變；下一步是 human review corrected 15-case benchmark。

### Scope confirmation

本輪只修改 retrieval benchmark annotations、preflight、metrics、focused tests、必要 roadmap/quality
wording 與 daily log。未調整 `1200`、overlap、`0.30` threshold、embedding、BM25、RRF、reranker、
candidate trace、production retriever 或 `eval/golden_set.yaml`。未 commit、push、merge、stash、
reset 或 clean。

## 2026-09-08 8.1 Current Retrieval Baseline First Live Pilot

### Controlled authorization and execution

- User 明確授權本次 controlled benchmark 將 frozen `mock_data/` 三份 PDF 經 current
  parser/chunker 的 outputs，以及 `eval/retrieval/benchmark.yaml` 的 15 個 queries 傳給目前設定的
  OpenAI embedding provider。授權只適用於本次 8.1 pilot。
- Exact benchmark command：

  ```text
  rtk env KNOWVIA_RUN_RETRIEVAL_BENCHMARK=1 uv --cache-dir /private/tmp/knowvia-uv-cache run --env-file .env python -m eval.run_retrieval_benchmark --benchmark eval/retrieval/benchmark.yaml --corpus-root mock_data --report /tmp/knowvia-retrieval-pilot-20260908.json
  ```

- 使用 current production parser、page-aware chunking、`max_chunk_chars=1200`、overlap `0`、
  `text-embedding-3-small`、1536 dimensions、pgvector exact cosine、current candidate pool、
  `knowledge_relevance_floor=0.30` 與 top-k `1/3/5`。沒有修改任何 retrieval 設定，也沒有呼叫
  final LLM。
- Corpus 為 3 份 frozen PDFs，page counts 為 `14/17/21`；benchmark 為 15 cases，包含 12
  positive 與 3 hard-negative cases。
- Bounded JSON report 寫入 `/tmp/knowvia-retrieval-pilot-20260908.json`，沒有覆寫 source-of-truth
  fixture，也沒有包含 full chunk text、raw PDF text、embedding vector、provider request/response、
  API key 或 database credentials。

### Preflight and run verification

- SHA256、page counts、15-case gold annotation preflight 與 frozen production contract：PASS。
- Focused retrieval benchmark suite：`13 passed`。
- Local `pgvector/pgvector:pg16` PostgreSQL：healthy；admin connection 建立與刪除 preflight
  disposable database：PASS。
- Live run 完成 Alembic upgrade、3 份 PDF indexing、15 個 query embeddings 與 retrieval-only
  evaluation。Live run 後 `knowvia_retrieval_pilot_*` database count 為 `0`。

### Baseline metrics

| Metric | Result |
| --- | ---: |
| total cases | 15 |
| positive cases | 12 |
| negative cases | 3 |
| passed cases | 10 |
| Recall@1 | 0.458333 |
| Recall@3 | 0.625000 |
| Recall@5 | 0.791667 |
| MRR | 0.666667 |
| full_case_success@1 | 0.333333 |
| full_case_success@3 | 0.583333 |
| full_case_success@5 | 0.750000 |
| source_recall@5 | 1.000000 |
| page_coverage@5 | 0.875000 |
| negative_rejection_rate | 0.333333 |
| false_positive_retrieval_rate | 0.666667 |

這些是 post-relevance-gate accepted retrieval metrics，不是 raw vector ranking metrics。

### Category results

| Category | Cases | Passed | Pass rate |
| --- | ---: | ---: | ---: |
| single_document_factual | 3 | 3 | 1.000000 |
| semantic_paraphrase | 3 | 1 | 0.333333 |
| exact_term_identifier | 2 | 2 | 1.000000 |
| multi_evidence | 2 | 1 | 0.500000 |
| cross_source_discrimination | 2 | 2 | 1.000000 |
| hard_negative | 3 | 1 | 0.333333 |

### Failed cases and bounded findings

- Failed cases：`rv-004`、`rv-005`、`rv-009`、`rv-014`、`rv-015`。五個 case 的 failure label
  都是 `UNRESOLVED`，符合 accepted-only observation 不推斷 embedding、ranking、threshold 或
  chunking root cause 的規則。
- `rv-004`：accepted top-5 出現 `production_agents`，但沒有 gold page 7；gold source 有出現，
  exact gold evidence 未在 accepted top-5 完成。維持 `UNRESOLVED`。
- `rv-005`：accepted top-5 出現 `google_agent_patterns`，並出現 page 4 locator，但 case anchor
  沒有被 evaluator 確認。只能記錄 source/page candidate 與 gold anchor 的 bounded mismatch，
  維持 `UNRESOLVED`。
- `rv-009`：queue-separation group 在 rank 4 出現 page 6 evidence，但 required repeat-execution
  group 的 page 16 沒有在 accepted top-5 完成。維持 `UNRESOLVED`。
- `rv-014` 與 `rv-015`：negative cases 分別接受了 3 個與 1 個 evidence；最高 score 分別為
  `0.352377` 與 `0.321981`，均高於 current `0.30` floor。這是 current accepted retrieval path
  的 false positive，沒有修改 threshold。
- 本輪沒有足夠 evidence 把 positive miss 歸因為 embedding、ranking、relevance gate 或
  chunking。沒有進行 optimization。

### Scope confirmation

- 沒有修改 `max_chunk_chars`、overlap、relevance floor、candidate pool、embedding model、parser、
  production retriever、BM25、RRF、reranker、benchmark cases 或 frontend。
- 沒有加入 raw candidate trace，沒有修改 `eval/golden_set.yaml`，沒有 stage、commit 或 push。
- `8.1` 維持 `manual_verification`。Baseline metrics 與五個 failed cases 仍需要 human
  interpretation，再決定是否 freeze、expand 或另開 diagnostic slice。

## 2026-09-08 8.1.2 Pilot Failure Offline Diagnosis

### Scope and deterministic inspection

- 本輪只使用 current `PyPDFParserClient`、current normalization、page-aware `chunk_text_document()`
  與 `max_chunk_chars=1200` 重建 local chunks。
- 沒有呼叫 embedding provider，沒有建立 benchmark database，沒有重新執行 live retrieval，沒有
  修改 benchmark gold、production code、roadmap 或 decisions。
- Offline red-capable inspection loop：`offline_diagnosis_loop=PASS`。Loop 確認四個 target
  pages 的 gold anchors 都能在 current reconstructed chunks 中找到。

### Reconstructed gold-page chunks

| Case / page | Extracted chars | Chunks | Anchor result | Boundary finding |
| --- | ---: | --- | --- | --- |
| `rv-004` / production_agents p7 | 1127 | `#10`, page 7, length 1127 | Anchor complete in `#10` | No split |
| `rv-005` / google_agent_patterns p4 | 2298 | `#6` length 1189; `#7` length 1107 | Anchor complete in `#7` | Same-page wrong-chunk plausible; anchor itself not split |
| `rv-009` / chatgpt_tasks_week3 p6 | 377 | `#5`, length 361 | Both required page-6 anchors complete in `#5` | No split |
| `rv-009` / chatgpt_tasks_week3 p16 | 562 | `#15`, length 550 | `idempotent handler` complete in `#15` | No split |

Bounded snippets showed `rv-004` page 7's control-flow statement in the same chunk as its model/
loop context. Page 4 of `rv-005` has two chunks; the gold sentence is complete in chunk `#7`, while
the preceding sentence crosses the `#6/#7` boundary. The current report has only page locator and no
chunk identity, so it cannot prove which page-4 chunk was returned at rank 5.

### Case findings

- `rv-004`: page 7 has one 1127-character chunk and the gold anchor is complete with standalone
  context. Page 8 contains nearby concepts such as loop escape rules, model invocation and halting.
  These explain why page 8 may be semantically close to the query. Result:
  `CHUNK_BOUNDARY_NOT_SUPPORTED`.
- `rv-005`: page 4 has two chunks and the gold anchor is complete only in chunk `#7`; live retrieval
  returned page 4 at rank 5 without chunk identity. Result: `CHUNK_BOUNDARY_PLAUSIBLE` for a
  same-page wrong-chunk explanation, but the accepted report cannot prove it. Normalization still
  matches the anchor.
- `rv-009`: page 6 contains both Watcher/Worker anchors in one chunk; page 16 contains the
  `idempotent handler` anchor in one chunk. The two required groups are separate page-level intents.
  Zero overlap is not implicated by the reconstructed boundaries. Result:
  `CHUNK_BOUNDARY_NOT_SUPPORTED` for the missing second evidence.

### Negative-case findings

| Case | Returned topic evidence | Requested fact present | Score margin over 0.30 |
| --- | --- | --- | --- |
| `rv-014` p1 | MCP scheduler / LLM engine / system design | No pgvector or HNSW configuration | `0.052377` |
| `rv-014` p3 | Prototype tour with API, DB schema and system-design references | No pgvector index or HNSW parameters | `0.020820` |
| `rv-014` p16 | Production gaps involving exactly-once execution, jobs and handlers | No pgvector index or HNSW parameters | `0.006131` |
| `rv-015` p12 | Production-agent discussion of models and computation | No `text-embedding-3-small` statement | `0.021981` |

`rv-014` and `rv-015` are accepted false positives with superficial technical or model-related
similarity. The negative set contains only 3 cases, so it is insufficient for threshold calibration.

### Hypothesis ranking

| Rank | Hypothesis | Status | Evidence boundary |
| ---: | --- | --- | --- |
| 1 | Multi-evidence retrieval coverage | SUPPORTED | `rv-009` retrieves page 6 evidence but misses the separate page 16 group |
| 2 | Relevance-floor calibration | PARTIALLY_SUPPORTED | 2 of 3 negative cases are accepted above `0.30`; sample is too small |
| 3 | Chunk size / overlap | PARTIALLY_SUPPORTED | Only `rv-005` makes same-page wrong-chunk plausible; `rv-004` and `rv-009` do not support it |
| 4 | Embedding / semantic ranking | NOT_YET_SUPPORTED | No raw rejected candidates or A/B comparison |
| 5 | Hybrid lexical + dense | NOT_YET_SUPPORTED | Current inspection provides no comparison evidence |

### One next experiment

下一輪只推薦一個 bounded experiment：擴充同 corpus 的 hard-negative set，並對既有
`knowledge_relevance_floor` 做 offline score calibration review。先取得足夠 negative cases，再
判斷 floor 是否能降低 false positives；不在本輪或本紀錄中修改 threshold，也不修改 production
retriever。

### Scope confirmation

- 沒有新增 roadmap slice，沒有修改 `DECISIONS.md`、`PROJECT_ROADMAP.md`、quality spec 或 runtime。
- 沒有新增 trace、沒有使用 provider、沒有建立 database，沒有 stage、commit 或 push。

## 2026-09-08 8.1.3 Hard-Negative Expansion for Relevance-Gate Calibration

### Scope and existing negative review

- 本輪只建立與驗證 negative Gold cases。沒有修改 `knowledge_relevance_floor`、production
  retriever、embedding、chunking、overlap、BM25、RRF、reranker 或 raw candidate trace。
- 沒有呼叫 OpenAI embedding provider，沒有建立 benchmark database，沒有執行 22-case live
  baseline，也沒有計算 threshold sweep。
- Existing `rv-013`、`rv-014`、`rv-015` 重新檢查後仍符合 hard-negative standard：related
  terminology 存在，但 RRF、pgvector/HNSW configuration 與 `text-embedding-3-small` claim
  分別不存在，沒有 accidental valid answer。原有 queries 保持不變。

### New negative cases

Source review 逐題掃描三份 frozen PDFs，確認 requested factual claim、configuration、number
或 implementation detail 不存在；notes 只保存 bounded explanation，沒有 evidence_groups、fake
page 或 fake chunk。

| Case | Type | Query | Bounded support check |
| --- | --- | --- | --- |
| `rv-016` | unsupported configuration | What exact maximum number of iterations does the Google Cloud guide specify for its iterative loop pattern? | Guide discusses maximum iterations and exit conditions but gives no exact count. |
| `rv-017` | unsupported configuration | How many retry attempts does the Week 3 prototype configure after an LLM timeout? | Week 3 discusses timeout and retry strategies but configures no retry count. |
| `rv-018` | unsupported quantitative fact | What exact throughput does the Week 3 watcher-worker queue sustain in the 500,000 recurring-job scenario? | Week 3 has watcher-worker, queue depth and 500,000-job terminology but no throughput value for that queue. |
| `rv-019` | unsupported quantitative fact | What percentage of the production article's agent control flow is handled by deterministic code rather than the model? | The article states the control-flow relationship but gives no percentage split. |
| `rv-020` | unsupported implementation detail | Which Python MCP framework or SDK does the Week 3 prototype use to implement its MCP server? | Week 3 identifies a Python MCP server but names no Python MCP framework or SDK. |
| `rv-021` | unsupported implementation detail | Which database driver does the Week 3 prototype use to access the PostgreSQL job table partitioned by time_bucket? | Week 3 names PostgreSQL and time_bucket but no database driver. |
| `rv-022` | near-miss concept | What exact cost multiplier does the Google Cloud guide report for parallel agents compared with sequential agents? | The guide compares cost and latency but gives no exact cost multiplier. |

### Distribution and preflight

- New case distribution：unsupported configuration `2`、unsupported quantitative fact `2`、
  unsupported implementation detail `2`、near-miss concept `1`。
- Benchmark now has `22` cases：`12` positive、`10` hard-negative。新增 cases 沒有增加 positive
  cases，也沒有直接擴至 40 至 60 題。
- Existing positive anchor preflight、三份 PDF SHA256/page counts、unique IDs、non-empty negative
  queries 與 negative `evidence_groups` prohibition：PASS。
- Focused retrieval benchmark suite：`14 passed`。
- `8.1` 維持 `manual_verification`；新增 7 題需先經 human review，之後才可考慮 current
  retrieval live run 與 calibration。

### Scope confirmation

- 沒有新增 negative-case framework、schema field 或 Roadmap slice。
- 沒有修改 `DECISIONS.md`、quality spec、runtime、production retrieval behavior 或
  `eval/golden_set.yaml`。
- 沒有 stage、commit 或 push。

## 2026-09-08 8.1.4 Expanded Hard-Negative Live Baseline and Offline Relevance-Floor Calibration

### rv-020 correction and controlled execution

- `rv-020` 已從 scheduler HTTP API 的 Python premise，修正為未命名的 Python MCP
  framework or SDK。Current `PyPDFParserClient` 找到 Week 3 的 `Python MCP server`（p3），
  未找到 named Python MCP framework 或 SDK，也沒有 accidental answer。
- Preflight：三份 frozen PDF 的 SHA256、page counts、positive anchors、unique IDs、10 個
  negative `evidence_groups` prohibition、frozen retrieval contract、PostgreSQL readiness 與
  pgvector `0.8.2` 均 PASS。`.env` 的 OpenAI API key 可用。
- 依本輪明確授權執行一次 controlled retrieval-only baseline。使用 current parser、normalization、
  page-aware chunking、`max_chunk_chars=1200`、overlap `0`、`text-embedding-3-small`、1536
  dimensions、pgvector exact cosine、candidate pool `20` 與 relevance floor `0.30`；沒有呼叫
  final LLM。
- Live report：`/tmp/knowvia-retrieval-pilot-8.1.4-20260908.json`。完成後 disposable database
  已移除，沒有殘留 `knowvia_retrieval_pilot_*` database。

### Current `0.30` baseline

| Metric | Result |
| --- | ---: |
| total cases | 22 |
| positive cases | 12 |
| negative cases | 10 |
| passed cases | 10 |
| Recall@1 | 0.458333 |
| Recall@3 | 0.625000 |
| Recall@5 | 0.791667 |
| MRR | 0.666667 |
| full_case_success@1 | 0.333333 |
| full_case_success@3 | 0.583333 |
| full_case_success@5 | 0.750000 |
| source_recall@5 | 1.000000 |
| page_coverage@5 | 0.875000 |
| negative_rejection_rate | 0.100000 |
| false_positive_retrieval_rate | 0.900000 |

### Category results

| Category | Cases | Passed | Pass rate |
| --- | ---: | ---: | ---: |
| single_document_factual | 3 | 3 | 1.000000 |
| semantic_paraphrase | 3 | 1 | 0.333333 |
| exact_term_identifier | 2 | 2 | 1.000000 |
| multi_evidence | 2 | 1 | 0.500000 |
| cross_source_discrimination | 2 | 2 | 1.000000 |
| hard_negative | 10 | 1 | 0.100000 |

### Negative case detail

以下只記錄 bounded source/page/score metadata，沒有寫入 chunk text。

| Case | Result | Accepted source and page/locator | Top score | Accepted results |
| --- | --- | --- | ---: | ---: |
| `rv-013` | rejected | none | n/a | 0 |
| `rv-014` | accepted | `chatgpt_tasks_week3`: p1, p3, p16 | 0.352549 | 3 |
| `rv-015` | accepted | `production_agents`: p12 | 0.321981 | 1 |
| `rv-016` | accepted | `google_agent_patterns`: p8, p6, p6, p13, p4 | 0.461111 | 5 |
| `rv-017` | accepted | `chatgpt_tasks_week3`: p17, p1, p16, p6, p3 | 0.413671 | 5 |
| `rv-018` | accepted | `chatgpt_tasks_week3`: p6, p14, p5, p17, p16 | 0.502437 | 5 |
| `rv-019` | accepted | `production_agents`: p3, p12, p7, p8, p5 | 0.601200 | 5 |
| `rv-020` | accepted | `chatgpt_tasks_week3`: p1, p3, p9 | 0.510885 | 3 |
| `rv-021` | accepted | `chatgpt_tasks_week3`: p10, p1, p17, p16, p5 | 0.475875 | 5 |
| `rv-022` | accepted | `google_agent_patterns`: p4, p5, p2, p6; `production_agents`: p11 | 0.547465 | 5 |

### Existing positive failure review

`rv-004`、`rv-005`、`rv-009` 在 expanded run 保持與原始 15-case run 相同：failure label
仍為 `UNRESOLVED`，沒有 positive improvement 或 regression。`rv-009` 仍在 top-5 命中
`queue-separation`，但缺少 `repeat-execution-handling`；沒有開始 chunking、embedding、multi-query
或 hybrid retrieval experiment。

### Offline upward threshold simulation

Simulation 只使用 current `0.30` live report 的 90 筆 accepted observations 與既有 Gold
group-hit semantics，沒有重新 query provider、reindex 或修改 production floor。Observed
accepted score range 為 `0.301992` 至 `0.619704`；資料沒有 `<0.30` candidates，因此不推論
任何低於 `0.30` 的行為。

| Candidate floor | Recall@1 | Recall@3 | Recall@5 | MRR | full_case_success@5 | source_recall@5 | page_coverage@5 | Negative rejection | False-positive retrieval |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.30 | 0.458333 | 0.625000 | 0.791667 | 0.666667 | 0.750000 | 1.000000 | 0.875000 | 0.10 | 0.90 |
| 0.31 | 0.458333 | 0.625000 | 0.791667 | 0.666667 | 0.750000 | 1.000000 | 0.875000 | 0.10 | 0.90 |
| 0.32 | 0.458333 | 0.625000 | 0.791667 | 0.666667 | 0.750000 | 1.000000 | 0.875000 | 0.10 | 0.90 |
| 0.33 | 0.458333 | 0.625000 | 0.791667 | 0.666667 | 0.750000 | 1.000000 | 0.875000 | 0.20 | 0.80 |
| 0.34 | 0.458333 | 0.625000 | 0.791667 | 0.666667 | 0.750000 | 1.000000 | 0.875000 | 0.20 | 0.80 |
| 0.35 | 0.458333 | 0.625000 | 0.791667 | 0.666667 | 0.750000 | 1.000000 | 0.875000 | 0.20 | 0.80 |
| 0.36 | 0.458333 | 0.625000 | 0.791667 | 0.666667 | 0.750000 | 1.000000 | 0.875000 | 0.30 | 0.70 |
| 0.37 | 0.458333 | 0.625000 | 0.750000 | 0.666667 | 0.666667 | 1.000000 | 0.833333 | 0.30 | 0.70 |
| 0.38 | 0.458333 | 0.625000 | 0.750000 | 0.666667 | 0.666667 | 1.000000 | 0.833333 | 0.30 | 0.70 |
| 0.39 | 0.458333 | 0.625000 | 0.750000 | 0.666667 | 0.666667 | 1.000000 | 0.833333 | 0.30 | 0.70 |
| 0.40 | 0.458333 | 0.625000 | 0.750000 | 0.666667 | 0.666667 | 1.000000 | 0.833333 | 0.30 | 0.70 |

| Candidate floor | Positive regressions vs `0.30` | Newly corrected negatives vs `0.30` |
| ---: | --- | --- |
| 0.30 | 0 cases, none | 0 cases, none |
| 0.31 | 0 cases, none | 0 cases, none |
| 0.32 | 0 cases, none | 0 cases, none |
| 0.33 | 0 cases, none | 1 case, `rv-015` |
| 0.34 | 0 cases, none | 1 case, `rv-015` |
| 0.35 | 0 cases, none | 1 case, `rv-015` |
| 0.36 | 0 cases, none | 2 cases, `rv-014`, `rv-015` |
| 0.37 | 1 case, `rv-012` | 2 cases, `rv-014`, `rv-015` |
| 0.38 | 1 case, `rv-012` | 2 cases, `rv-014`, `rv-015` |
| 0.39 | 1 case, `rv-012` | 2 cases, `rv-014`, `rv-015` |
| 0.40 | 1 case, `rv-012` | 2 cases, `rv-014`, `rv-015` |

`0.33` 是符合 calibration rule 的最低 pilot-supported candidate：相較 `0.30` 提升
negative rejection，且 Recall@5、MRR、full_case_success@5 與 positive case pass status
不變。`0.36` 能再拒絕 `rv-014`，但本輪依規則選最低符合者；這不是 global optimum，也不等於
production threshold decision。

### Scope and test confirmation

- Production relevance floor 仍為 `0.30`。沒有修改 config、retriever、chunking、overlap、
  embedding model、candidate pool、BM25、RRF、reranker、parser、schema 或 `eval/golden_set.yaml`。
- Focused retrieval benchmark suite：`15 passed`。Preflight 與 baseline reconstruction：PASS；
  `git diff --check`：PASS。
- `8.1` closure 於後續 8.1 closure entry 完成；本輪沒有新增 Roadmap slice、沒有新增
  Decision、沒有 stage、commit 或 push。

## 2026-09-08 8.1 Closure and Hard-Negative End-to-End Grounding Check

### 8.1 closure

- `8.1` 已完成並在 Roadmap 標記為 `done`：三份 frozen PDF、22 個 reviewed cases、12 個
  positive、10 個 hard-negative、current parser/chunker/embedding/pgvector retrieval-only
  baseline、offline diagnosis 與 bounded threshold calibration 均已完成。
- Current baseline：Recall@1 `0.458333`、Recall@3 `0.625000`、Recall@5 `0.791667`、MRR
  `0.666667`、full_case_success@5 `0.750000`、source_recall@5 `1.000000`、page_coverage@5
  `0.875000`。
- `KNOWLEDGE_RELEVANCE_FLOOR=0.30` 保持不變。`.33` 只保留為 pilot-supported candidate，沒有
  採用；`.36` 的 negative improvement 仍不足以支持 production change，`.37` 開始出現
  positive regression。此結果不宣稱 global optimum。

### Hard-negative E2E execution status

- 本輪已使用 rv-013 至 rv-022，沿用 current `QAOrchestrator.answer_question()`、
  `qa_answer_v3` 與現有 final LLM generation。三份 frozen PDF 先經 current parser、chunker、
  embedding/indexing path 載入 disposable PostgreSQL+pgvector database，再逐案執行 QA。
- E2E report：`/tmp/knowvia-8.1-closure-hard-negative-e2e-20260908.json`。10 cases 中 9 個為
  `SAFE_REJECTION`、1 個為 `UNSAFE_UNSUPPORTED_ANSWER`，safe rejection rate `0.900000`。
  所有 case 的 workflow status 都是 `succeeded`；`rv-013` 沒有 accepted evidence，其餘 9 個
  case 有 accepted related evidence 後才進入 final LLM path。

### Hard-negative E2E case detail

以下只記錄 bounded evidence/state/citation metadata，沒有寫入 raw chunk text 或 provider response。

| Case | Accepted evidence | Final state | Classification | Citations | Bounded reason |
| --- | --- | --- | --- | ---: | --- |
| `rv-013` | no | `insufficient_info` | `SAFE_REJECTION` | 0 | no accepted evidence; backend returned insufficient information |
| `rv-014` | yes | `insufficient_info` | `SAFE_REJECTION` | 0 | final QA state was insufficient information and citations were cleared |
| `rv-015` | yes | `insufficient_info` | `SAFE_REJECTION` | 0 | final QA state was insufficient information and citations were cleared |
| `rv-016` | yes | `insufficient_info` | `SAFE_REJECTION` | 0 | final QA state was insufficient information and citations were cleared |
| `rv-017` | yes | `insufficient_info` | `SAFE_REJECTION` | 0 | final QA state was insufficient information and citations were cleared |
| `rv-018` | yes | `insufficient_info` | `SAFE_REJECTION` | 0 | final QA state was insufficient information and citations were cleared |
| `rv-019` | yes | `insufficient_info` | `SAFE_REJECTION` | 0 | final QA state was insufficient information and citations were cleared |
| `rv-020` | yes | final answer asserted unsupported content | `UNSAFE_UNSUPPORTED_ANSWER` | 3 | final answer did not establish a bounded rejection |
| `rv-021` | yes | `insufficient_info` | `SAFE_REJECTION` | 0 | final QA state was insufficient information and citations were cleared |
| `rv-022` | yes | `insufficient_info` | `SAFE_REJECTION` | 0 | final QA state was insufficient information and citations were cleared |

`rv-020` 是本輪唯一 unsafe case；因為本輪不保留 raw provider response，log 只記錄 bounded
  classification reason 與 citation count，不重述或推測 provider 的完整回答。

### Grounding interpretation

- 9/10 safe rejection 顯示 accepted hard-negative evidence 多數仍能由 current final QA path
  收斂到 `insufficient_info`，且不輸出 citations。
- `rv-020` 的 1 個 unsafe 結果保留為後續 grounding acceptance risk；本輪沒有達到「multiple
  unsafe」的 Branch 2 條件，也不能用 scalar relevance floor、embedding、BM25、chunking 或 RRF
  直接解釋成 retrieval-only 問題。
- 依 9/10 safe rejection 的 Branch 1，本輪不新增 relevance-gate work，也不修改 prompt、QA、
  retriever 或 classifier；下一個 optimization target 應回到 positive retrieval quality，聚焦
  semantic paraphrase 與 multi-evidence coverage，尚不在本輪實作。

### Scope confirmation

- 沒有修改 relevance floor、retriever、chunking、embedding、BM25、RRF、reranker、prompt、QA
  grounding logic 或 schema；沒有新增 benchmark、evidence-sufficiency classifier 或 raw
  candidate trace。
- E2E 使用 direct user authorization，僅執行一次；raw PDF context、queries 與 provider responses
  未寫入 repository 或 report。disposable database 已於執行結束清理。
- 沒有新增 Decision。`8.1` closure 只同步 Roadmap 與本紀錄；沒有 stage、commit 或 push。

## 2026-09-08 8.2 Positive Retrieval Quality Gold Expansion

### Scope

- `8.1` 維持 `done`。本輪開始 `8.2 Positive Retrieval Quality`，Roadmap status 為
  `in_progress`；不新增 `8.2.1`、`8.2.2` 或 `8.2.3`。
- 只補 semantic paraphrase 與 multi-evidence positive Gold。沒有新增 factual、exact-term、
  cross-source 或 hard-negative cases，也沒有修改 production retrieval behavior。

### New positive cases for human review

Benchmark version 更新為 `8.2`，總數為 30 cases：20 positive、10 hard-negative。以下 8 題尚未
視為 human-approved Gold。

| Case | Category | Query | Evidence design |
| --- | --- | --- | --- |
| `rv-023` | semantic_paraphrase | What governs when a dependable agent consults the model and when its repeated execution finishes? | `production_agents` p7；bounded control-flow evidence |
| `rv-024` | semantic_paraphrase | How should a scheduled wall-clock time be represented so a daylight-saving transition does not change its intended local meaning? | `chatgpt_tasks_week3` p12；bounded scheduled-time evidence |
| `rv-025` | semantic_paraphrase | Which design lets a person approve an agent's work before the workflow continues? | `google_agent_patterns` p14；bounded human-review evidence |
| `rv-026` | semantic_paraphrase | Where does most of a production agent's behavior live when the model is consulted only at selected points? | `production_agents` p2；bounded deterministic-code evidence |
| `rv-027` | multi_evidence | How does the prototype divide finding due work from carrying it out, and what property protects against duplicate execution? | `chatgpt_tasks_week3` p6 + p16；due-work separation and duplicate-execution groups |
| `rv-028` | multi_evidence | How does the prototype make hourly due-job lookup selective, and what preserves the intended local time when clocks change? | `chatgpt_tasks_week3` p10 + p12；partition selection and scheduled-time groups |
| `rv-029` | multi_evidence | How do these two designs assign responsibility for deciding what happens next: the dependable-agent design and the coordinator design? | `production_agents` p7 + `google_agent_patterns` p8；independent control-owner groups |
| `rv-030` | multi_evidence | How does a coordinator-led workflow differ from one that pauses for a person to approve the work? | `google_agent_patterns` p8 + p14；coordinator and person-review groups |

### Preflight and stop boundary

- `load_benchmark()`：PASS。30 cases、20 positive、10 hard-negative；新增 4 semantic paraphrase 與
  4 multi-evidence。既有 strong categories 與 10 hard-negatives 沒有新增項目。
- Current `PyPDFParserClient` corpus verification：PASS。3 份 frozen PDFs、52 pages、8 題所有
  source/page/anchor preflight：PASS。
- New semantic cases 各自只有一個 bounded evidence group，涵蓋三份 corpus 中的至少兩份；new
  multi-evidence cases 各自需要兩個獨立 groups；`rv-030` 使用相距較遠的 pages，`rv-027` 則依
  human review 改用 Week 3 p7+p9 的兩個獨立 implementation concepts。
- Focused retrieval benchmark tests：`18 passed`。`git diff --check`：PASS。
- 本輪停在 human-review boundary。尚未執行 current baseline retrieval、embedding A/B、任何
  embedding provider call、任何 retrieval benchmark 或 database；因此沒有 A/B metrics，也尚未
  選定 alternative embedding candidate。
- Production embedding、relevance floor `0.30`、chunking、retriever、schema、dependencies 與
  default model 維持不變。未新增 Decision、未 stage、commit 或 push。

## 2026-09-08 Post-8.1 rv-020 Grounding Risk Diagnosis

### Scope and artifact boundary

- 本輪只做 offline diagnosis。沒有呼叫 provider、沒有重跑 retrieval benchmark、沒有 reopen
  `8.1`，也沒有修改 production code、Roadmap 或 Decisions。
- Existing E2E report 只保存 rv-020 的 bounded result：accepted evidence `yes`、
  `insufficient_info=false`、`UNSAFE_UNSUPPORTED_ANSWER`、3 citations，以及
  `final answer did not establish a bounded rejection`。沒有保存 final answer、推測語氣或
  citation payload，因此不能從現有 artifact 還原 provider 實際命名的 framework/SDK。

### Citation support review

rv-020 的已記錄 evidence 是 `chatgpt_tasks_week3` 的 `page 1`、`page 3`、`page 9`。

- Page 1 只顯示 Week 3 的 Scheduler、LLM Engine、MCP 文件範圍，不支持 framework 或 SDK identity。
- Page 3 支持 `Go scheduler (HTTP API + Worker + Watcher) + Python MCP server` 的跨語言架構，
  也提到 HTTP API、Swagger 與 MCP Inspector；沒有 named Python MCP framework 或 SDK。
- Page 9 將 MCP 說明為 LLM 與 backend 之間的標準化介面／API Gateway；沒有指出實作 library、
  framework 或 SDK。

Current parser 對整份 Week 3 PDF 的 bounded term scan 找到 `Python MCP server`、`MCP`、`HTTP API`、
`Go scheduler`、`MCP Inspector`、`Swagger` 與 `JSON-RPC`，但沒有 `SDK`、`framework`、
`FastMCP`、`FastAPI`、`Flask` 或 `Django`。因此三個 citation 都沒有支持 case 所要求的
named Python framework/SDK；但因 final answer 未保存，無法判定它是否直接命名、使用推測語氣，
或把哪一個 adjacent term 當成 implementation identity。

### Contract and comparison

`qa_answer_v3` 已要求只使用 supplied context；context evidence 不足時輸出 exactly
`INSUFFICIENT_INFO`；並且不得捏造未由 context 支持的 facts 或 citations。此 contract 對
requested specific implementation detail 已足夠清楚，分類為 `CONTRACT_ALREADY_SUFFICIENT`。

`rv-017` 與 `rv-021` 都是「related technical evidence 存在，但 requested exact implementation
detail 不存在」的相近 cases；前者涉及 timeout/retry，後者涉及 PostgreSQL/time_bucket/driver，
兩者都在 final QA 收斂為 `insufficient_info`、zero citations。rv-020 的特殊點是 query 直接要求
framework/SDK identity，而 retrieved page 3 同時出現 Python MCP server、Go scheduler、HTTP API、
Swagger 等 adjacent implementation terms。這支持 wording/evidence-confusion 是 plausible
因素，但不是統計結論。

### Assessment and one follow-up

- Root-cause assessment：`UNRESOLVED`。現有資料不足以在 provider variance、wording sensitivity、
  evidence confusion 與 bounded classifier limitation 之間定案，也不足以證明 general architecture
  gap。Current contract 已明確禁止該行為。
- 唯一建議：未來若需要定案，做一次單 case、另行授權的 rv-020 replay，只保存 bounded final-claim
  label 與每個 citation 的 page/locator support matrix，不保存 raw response；本輪不執行。
- Positive retrieval 的既有下一個 target 仍是 semantic paraphrase 與 multi-evidence coverage；
  本紀錄不新增 Roadmap slice，也不實作 optimization。

## 2026-09-08 8.2 Phase E.1 Minimal Gold Diversity Correction

### Human review and replacements

- Human review 通過 `rv-023`、`rv-024`、`rv-026`、`rv-028`、`rv-029`、`rv-030`。這 6 題的
  annotation correctness 與 case diversity 保持不變。
- `rv-025` 的 source/page/anchor 原本有效，但與 `rv-006` 都集中在 Google p14 的
  human-in-the-loop evidence。保留 id，改為 Google p6 loop pattern：query 測 specialized
  agents 反覆執行至 termination condition，anchor 為 `subagents until a specific termination
  condition is met`。
- `rv-027` 的 source/page/anchor 原本有效，但與 `rv-009` 都使用 Week 3 p6+p16 的
  Watcher/Worker separation 與 idempotent handler。保留 id，改為 Week 3 p7+p9：第一組測 tool
  description 對 operation selection 的作用，第二組測 MCP 對 LLM/backend 的 decoupling。

### Preflight and stop boundary

- Benchmark 仍為 30 cases、20 positive、10 hard-negative；8.2 additions 仍為 4 semantic
  paraphrase 與 4 multi-evidence。
- `load_benchmark()`、三份 frozen corpus source/page validation、current `PyPDFParserClient`
  anchor preflight、semantic query exact-anchor check 與 multi-evidence independent-group check：
  PASS。
- Focused retrieval + QA regression：`24 passed`；Agent Golden Set：`29/29`；
  `git diff --check`：PASS。
- 本輪沒有 embedding provider call、database、embedding candidate selection、A/B、retrieval
  benchmark 或 production change。`8.2` 保持 `in_progress`，等待替換兩題的 human review；未新增
  Decision、Roadmap slice 或 sub-slice，未 stage、commit 或 push。

## 2026-09-08 8.2 Single Embedding A/B

### Scope and controls

- Human review 已通過 `rv-023` 至 `rv-030`；Gold freeze 維持 30 cases、20 positive、10
  hard-negative、7 semantic paraphrase 與 6 multi-evidence。
- Candidate compatibility PASS：既有 `OpenAIEmbeddingClient`、`EmbeddingBatchService` 與
  runner 可明確傳入 `model` 與 `dimensions`；`text-embedding-3-large` 使用 1536 dimensions，
  不需 production schema migration。
- 只執行一次 A/B。兩個 variant 都使用 current `PyPDFParserClient`、page-aware chunks、
  `max_chunk_chars=1200`、overlap `0`、exact cosine、candidate pool `20`、relevance floor
  `0.30`、top-k `1/3/5`。文件與 query 在各自 variant 使用同一 model；A/B chunk fingerprint
  （count、source、locator、normalized content hash）相同。
- 每個 variant 使用 isolated disposable PostgreSQL+pgvector database；執行後已清理。沒有保存
  embedding vectors，沒有 final LLM E2E、threshold calibration 或 production reindex/model
  change。

### Bounded comparison

| Metric | A `text-embedding-3-small` | B `text-embedding-3-large` |
| --- | ---: | ---: |
| Positive Recall@1 / @3 / @5 | 0.625 / 0.750 / 0.850 | 0.600 / 0.717 / 0.800 |
| Positive MRR | 0.750 | 0.715 |
| Full-case success@1 / @3 / @5 | 0.450 / 0.650 / 0.800 | 0.400 / 0.600 / 0.750 |
| Source recall@5 | 1.000 | 1.000 |
| Page coverage@5 | 0.850 | 0.800 |
| Semantic pass rate (7) | 4/7 (0.571) | 3/7 (0.429) |
| Semantic Recall@1 / @3 / @5 | 0.571 / 0.714 / 0.857 | 0.429 / 0.619 / 0.810 |
| Multi pass rate (6) | 4/6 (0.667) | 3/6 (0.500) |
| Multi group Recall@1 / @3 / @5 | 0.583 / 0.750 / 0.833 | 0.500 / 0.667 / 0.778 |
| Multi full-case success@1 / @3 / @5 | 0.333 / 0.667 / 0.833 | 0.333 / 0.667 / 0.833 |

- Strong-category pass counts沒有變化：`single_document_factual` 3/3、`exact_term_identifier`
  2/2、`cross_source_discrimination` 2/2。Hard-negative rejection rate A/B 都是 `0.100`；
  這只作 safety observation，不作 embedding selection primary metric。
- Index embedding latency：A `2.096700s`、B `1.776822s`；query embedding latency：A
  `0.203127s`、B `0.175630s`。這是單次 controlled run 的 operational observation；沒有額外
  建立 cost framework，cost comparison deferred。
- Console delta summary 曾混入 hard-negative IDs，且未保存 bounded per-case report；因此本紀錄
  不採用該 summary 作為 20 個 positive cases 的正式 `IMPROVED` / `REGRESSED` / `UNCHANGED`
  清單，也無法還原每題 completion rank。這不影響上述 aggregate metrics，但 case-level
  evidence 不完整。

### Decision boundary

- B 沒有帶來 semantic 或 multi-evidence improvement，且 overall positive Recall@5 與 full-case
  success@5 下降；strong categories 持平。Recommendation：`KEEP_CURRENT_EMBEDDING`。
- 不修改 production embedding，不新增 Decision 或下一個 optimization slice；`8.2` 等待
  human adoption decision。

## 2026-09-08 Post-8.2 Current Positive Failure Map

### 8.2 closure

- Human adoption decision 為 `KEEP_CURRENT_EMBEDDING`。`text-embedding-3-large / 1536` 未改善
  semantic 或 multi-evidence quality，production 維持 `text-embedding-3-small / 1536`。
- 8.2 Gold review、current vs. one alternative embedding A/B 與本次 current-model failure map
  已完成。Aggregate A/B 結果足以完成 adoption decision，但 bounded positive case-level delta
  未保存。Embedding model exploration stops here；沒有新增 Decision 或 roadmap slice。

### Current-model diagnostic

- 唯一執行 Variant A：`text-embedding-3-small / 1536`。使用 frozen 3-PDF corpus、30 queries、
  current `PyPDFParserClient`、page-aware `1200/0` chunking、exact cosine、candidate pool `20`、
  relevance floor `0.30` 與 top-k `1/3/5`。
- Isolated disposable PostgreSQL+pgvector database 已於執行後清理。沒有呼叫 final LLM，沒有保存
  raw PDF text、full chunk text、embedding vectors 或 provider raw response。
- Bounded report：`eval/retrieval/reports/8.2-current-positive-failure-map-20260908.json`。
  Report 保留 20 個 positive cases 的 case result、accepted source/page/locator、score、group
  hits、rank 與 retrieval mode。
- Aggregate：Recall@1/3/5 `0.425 / 0.575 / 0.700`；MRR `0.625`；full-case success@1/3/5
  `0.300 / 0.500 / 0.650`；source recall@5 `0.950`；page coverage@5 `0.750`。

### Failed positive cases

| Case | Category | Expected source/page | Source in top-5 | Page in top-5 | Groups hit / missed at @5 | Best bounded accepted locator | Pattern |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `rv-004` | semantic_paraphrase | `production_agents` p7 | yes | no | none / `deterministic-control-flow` | `production_agents` p8, rank 1, score `0.619704` | B |
| `rv-005` | semantic_paraphrase | `google_agent_patterns` p4 | yes | yes | none / `sequential-agent-input` | `google_agent_patterns` p4, rank 5, score `0.499237` | C |
| `rv-009` | multi_evidence | `chatgpt_tasks_week3` p6+p16 | yes | p6 yes, p16 no | `queue-separation` / `repeat-execution-handling` | p6, rank 4, score `0.470650` | D |
| `rv-023` | semantic_paraphrase | `production_agents` p7 | yes | no | none / `dependable-agent-timing` | `production_agents` p8, rank 1, score `0.642107` | B |
| `rv-026` | semantic_paraphrase | `production_agents` p2 | yes | no | none / `selective-model-use` | `production_agents` p3, rank 1, score `0.596184` | B |
| `rv-027` | multi_evidence | `chatgpt_tasks_week3` p7+p9 | no | no | none / both groups | none | A |
| `rv-029` | multi_evidence | `production_agents` p7 + `google_agent_patterns` p8 | yes | p8 yes, p7 no | `coordinator-workflow-owner` / `dependable-agent-control-owner` | Google p8, rank 2, score `0.592795` | D |

### Failure pattern and diagnosis

- Semantic failures：`rv-004`、`rv-005`、`rv-023`、`rv-026`。Multi-evidence failures：`rv-009`、
  `rv-027`、`rv-029`。Other positive failures：none。
- Observable pattern counts：A correct source absent `1` (`rv-027`); B correct source present but
  correct page absent `3` (`rv-004`、`rv-023`、`rv-026`); C correct page present but anchor group
  not hit `1` (`rv-005`); D multi-evidence partial coverage `2` (`rv-009`、`rv-029`)；E relevance-gate
  ambiguity `0`；F unresolved `0`。
- 這些結果只描述 accepted top-5 evidence。它們不能單獨證明 embedding、threshold 或 chunking
  是 root cause；accepted-only report 也沒有足夠資料判定 relevance-gate ambiguity。

### One next experiment

- 唯一建議：做一個 evaluation-only、single-variable `chunk_max_chars` A/B，固定 current
  `text-embedding-3-small / 1536`、page-aware parsing、overlap `0`、threshold、top-k 與 benchmark，
  比較目前 `1200` 與一個預先固定的較大 page-aware chunk limit。這直接覆蓋最大的 B pattern，
  也能觀察 C 與 D 是否隨 evidence context placement 改變；本輪不 implementation。

## 2026-09-08 8.2 Retrieval Metric Reproducibility Audit

### Artifact inventory

- Earlier embedding A/B：目前只有 `DAILY_LOG.md` 的 human-facing aggregate summary。沒有找到
  earlier A/B JSON、per-case observations、chunk fingerprint、tracked A/B adapter 或可重跑的
  experiment-specific script；原先 summary 保留作 audit trail，沒有靜默改寫。
- Current positive failure map：`eval/retrieval/reports/8.2-current-positive-failure-map-20260908.json`。
  這份 bounded JSON 保留 30 cases 的 case result、accepted source/page/locator、score、group
  hits、rank 與 retrieval mode，未保存 raw chunk text、raw PDF text、vectors 或 provider raw
  response。

### Benchmark and configuration identity

- Current benchmark：`knowvia-retrieval-pilot` version `8.2`、30 cases、20 positive、10
  hard-negative；benchmark SHA256 為
  `c8eca9a7d6f4c3acfa0037df966187d89862376db82fcf31f0f35990327cf9c3`。
- Current corpus SHA256/page counts 與 YAML manifest 一致：`production_agents` 14 pages、
  `chatgpt_tasks_week3` 17 pages、`google_agent_patterns` 21 pages。`rv-025` 與 `rv-027` 為
  diversity replacement 後的 definitions，current report 的 case IDs、category 與 answerable
  flags 與 benchmark 一致。
- Earlier A/B 的 Daily Log 時序表示它在 Gold replacement review 後執行，且摘要宣稱同一 30-case
  controls；但沒有 earlier benchmark hash、chunk fingerprint 或 per-case report，無法獨立確認
  exact benchmark/configuration identity。沒有證據直接證明 benchmark drift 或 configuration drift。
- Current runner 的 canonical path 使用 `evaluate_case()`、`aggregate_metrics()`、page-aware
  `1200/0` chunking、`text-embedding-3-small / 1536`、exact cosine、candidate pool `20`、
  relevance floor `0.30`、top-k `1/3/5`、`owner_scope="local"` 與
  `pgvector_exact_cosine`。Earlier A/B adapter 不可取得，故無法比較其 metric-code identity。

### Current artifact recomputation

- Offline canonical recomputation：PASS。Report aggregate 與逐案 bounded data 完全一致：13/20
  positive pass、semantic `3/7`、multi-evidence `3/6`、strong categories 全部 pass，
  full-case success@5 `0.650`、Recall@1/3/5 `0.425 / 0.575 / 0.700`、MRR `0.625`、source
  recall@5 `0.950`、page coverage@5 `0.750`。
- Current report 因已保存 group hits、rank 與 source/page observations，可用 canonical aggregate
  semantics 離線重算；不需要 provider 或 database。

### Discrepancy assessment

- Earlier summary 同時記錄 semantic `4/7`、multi `4/6`、strong categories `3/3 + 2/2 + 2/2`，
  其合計是 `15/20`，與 full-case success@5 `0.800`（`16/20`）不一致；multi `4/6` 也與
  multi full-case success@5 `0.833333`（`5/6`）不一致。
- Primary classification：`ARTIFACT_INSUFFICIENT`。Secondary issue：`REPORTING_SUMMARY_ERROR`
  （earlier human-facing summary 內部算術不一致）。由於 earlier per-case artifact 不存在，不能
  判定是 transcription error 或 category/full-case semantics drift；`METRIC_CALCULATION_DRIFT`、
  `BENCHMARK_DRIFT`、`EXPERIMENT_CONFIGURATION_DRIFT` 與 provider nondeterminism 都未被證明。

### Adoption and optimization gate

- Embedding decision：`DECISION_REQUIRES_REVALIDATION`。Current production 仍維持
  `text-embedding-3-small / 1536`，但 earlier A/B 不足以作為可重建的 adoption evidence。
- Live rerun：需要時只建議一次重新取得完整 bounded comparison 的 `A + B` run，原因是要同時重建
  current baseline 與 alternative comparison；本輪不執行。
- `CHUNKING_EXPERIMENT_BLOCKED`。在 current baseline 與 A/B canonical provenance 確認前，不
  開始 chunking A/B。

## 2026-09-08 Post-8.2 Positive Retrieval Depth Diagnostic

### Status and controls

- `8.2=done` 維持不變。Earlier `text-embedding-3-small` vs.
  `text-embedding-3-large` A/B 沒有可重建的 per-case artifact，正式視為
  provenance-incomplete / inconclusive；本輪沒有重跑 large，也沒有重新開啟 embedding work。
- Production 維持 `text-embedding-3-small / 1536`。Canonical baseline 仍使用已有 bounded
  current-model artifact：Recall@1/3/5 `0.425 / 0.575 / 0.700`、MRR `0.625`、
  full-case success@5 `0.650`、source recall@5 `0.950`、page coverage@5 `0.750`。
- User 明確授權本次 controlled diagnostic 將 frozen 3-PDF corpus 的 current
  `PyPDFParserClient` / page-aware chunker outputs 與 7 個 failed queries 傳給
  `text-embedding-3-small`。Controls 固定為 `1200/0` chunking、1536 dimensions、exact cosine、
  candidate pool `min(top_k * 2, 20)`、relevance floor `0.30`、`owner_scope=local`；沒有呼叫
  final LLM。
- Production contract `top_k=[1,3,5]` 未修改。`top_k=10` 只作 diagnostic，實際 candidate pool
  上限為 `20`。

### Depth result

- Targeted failed positive cases：`7`。在 top-10 完整回收 `4`，仍未回收 `3`。
- Diagnostic semantics：targeted failed positive cases only。Recall@5 `0.200`、Recall@10
  `0.600`、full-case success@5 `0.000`、full-case success@10 `0.571429`。
- Semantic cases：`3/4` recovered，`rv-004`、`rv-005`、`rv-023`。
  `rv-026` 在 top-10 仍 absent。
- Multi-evidence cases：`1/3` recovered，只有 `rv-009`；`rv-027` 與 `rv-029` 在 top-10
  仍 absent。

| Case | Gold depth observation | Completion | Classification |
| --- | --- | ---: | --- |
| `rv-004` | `production_agents` p7 | 8 | `DEPTH_LIMITED` |
| `rv-005` | p4 page returned at @5；Gold anchor chunk at @7 | 7 | `SAME_PAGE_WRONG_CHUNK_DEPTH` |
| `rv-009` | queue group @4；repeat-execution p16 group @9 | 9 | `PARTIAL_MULTI_EVIDENCE_DEPTH` |
| `rv-023` | `production_agents` p7 | 8 | `DEPTH_LIMITED` |
| `rv-026` | `production_agents` p2 absent @10 | absent | `STILL_ABSENT_AT_10` |
| `rv-027` | Week 3 p7 and p9 groups both absent @10 | absent | `STILL_ABSENT_AT_10` |
| `rv-029` | coordinator p8 @2；production p7 absent @10 | absent | `STILL_ABSENT_AT_10` |

`rv-005` 的結果只表示同頁另一個 chunk 在 top-5 之後出現 Gold anchor，不宣稱 chunking
root cause。`rv-026`、`rv-027`、`rv-029` 的結果也不支持把 miss 歸因為 embedding、chunking
或 threshold failure。

### Artifact and next step

- Bounded artifact：`eval/retrieval/reports/8.2-current-depth-diagnostic-20260908.json`。
  Artifact 包含 benchmark SHA256、三份 corpus hashes、model/dimensions、chunk controls、
  threshold、top-k diagnostic 與每題 bounded source/page/locator/score；沒有保存 full chunk
  text 或 embeddings。
- Pattern summary：`DEPTH_LIMITED` = `rv-004`、`rv-023`；
  `SAME_PAGE_WRONG_CHUNK_DEPTH` = `rv-005`；
  `PARTIAL_MULTI_EVIDENCE_DEPTH` = `rv-009`；
  `STILL_ABSENT_AT_10` = `rv-026`、`rv-027`、`rv-029`。
- 唯一下一步建議是 retrieval depth / evidence coverage 的 evaluation-only study，後續再評估
  production top-k tradeoff 與 multi-evidence coverage；本輪不做 implementation，不開始
  chunking A/B、BM25、RRF、reranker、multi-query、query rewrite、threshold tuning 或 parser change。

### Verification

- Retrieval benchmark 與 depth diagnostic focused suite：`23 passed`。
- Agent Golden Set deterministic runner：`29/29`，test suite `2 passed`。
- `py_compile` 與 `git diff --check`：PASS。
- `8.2` 維持 `done`；本輪沒有修改 roadmap、production retrieval defaults、embedding model 或
  canonical benchmark contract。未 stage、commit 或 push。

## 2026-09-08 Post-8.2 Retrieval Depth / Evidence Coverage Tradeoff

### Controlled comparison

- `8.2=done` 維持不變。這次只比較 production-equivalent `top_k=5/8/10`，沒有修改 production
  default、candidate pool formula、threshold、embedding、chunking 或 parser。
- 三個 variant 共用 frozen 3-PDF corpus、30 benchmark queries、current
  `PyPDFParserClient`、page-aware `1200/0` chunking、`text-embedding-3-small / 1536`、exact
  cosine、relevance floor `0.30` 與 `owner_scope=local`。沒有呼叫 final LLM。
- Candidate pool 依 current retriever 實際使用 `min(top_k * 2, 20)`：`k=5` 為 `10`、`k=8`
  為 `16`、`k=10` 為 `20`。這是 candidate pool 與 final list 同時改變的 retrieval-depth
  comparison，不是固定 candidate pool 的 truncation-only experiment。
- `k=5` baseline reproducibility：PASS。Canonical metrics 與 7 個 failed positive case IDs
  完全一致。

### Positive results

| Metric | k=5 | k=8 | k=10 |
| --- | ---: | ---: | ---: |
| Evidence-group Recall | 0.700 | 0.850 | 0.850 |
| Full-case success | 0.650 | 0.800 | 0.800 |
| MRR | 0.625 | 0.644643 | 0.644643 |
| Source recall | 0.950 | 0.950 | 1.000 |
| Page coverage | 0.750 | 0.850 | 0.850 |

- Semantic paraphrase：`3/7` → `6/7` at both `k=8` and `k=10`；recovered cases are
  `rv-004`、`rv-005`、`rv-023`。`rv-026` remains failed。
- Multi-evidence：group recall `0.666667`、full-case success `0.500000` at all three k values。
  `rv-009`、`rv-027`、`rv-029` remain failed in this full 30-case comparison。
- Strong categories had no regression at any k: single-document factual `3/3`、exact-term
  identifier `2/2`、cross-source discrimination `2/2`。

### Case-level deltas

- `k=8` recovered `rv-004`、`rv-005`、`rv-023`；`k=10` added no further recovered positive
  case or group/full-case recall。
- `rv-009`、`rv-026`、`rv-027`、`rv-029` remained `UNCHANGED_FAIL` at both deeper variants。
- Other positive cases were `UNCHANGED_PASS`；no `REGRESSED` case was observed。

### Negative evidence volume and context

| Observation | k=5 | k=8 | k=10 |
| --- | ---: | ---: | ---: |
| Negative rejection rate | 0.100 | 0.100 | 0.100 |
| False-positive retrieval rate | 0.900 | 0.900 | 0.900 |
| Negative average accepted chunks | 3.7 | 5.3 | 5.9 |
| Negative maximum accepted chunks | 5 | 8 | 10 |
| Positive average accepted chunks | 4.45 | 7.0 | 8.7 |
| Positive average retrieved characters | 3973.5 | 6247.65 | 7853.15 |
| Negative average retrieved characters | 2892.9 | 4147.5 | 4614.4 |
| Average retrieval latency (ms) | 10.337 | 11.290 | 11.246 |

Deeper retrieval did not change negative rejection, but it increased accepted evidence and context
volume. `k=10` added context over `k=8` without adding positive full-case recovery.

### Candidate decision

- Decision：`CANDIDATE_TOP_K_8`。
- Reason：`k=8` captures all observed positive full-case gains. `k=10` adds no recovered positive
  case, group recall or full-case recall; its only positive metric increase is source recall
  `0.950` → `1.000`.
- This is a pilot-supported candidate only. Production remains `top_k=5` in this round.
- 唯一下一步：對 `top_k=8` 做 human review 與 bounded final-QA safety check，再決定是否進入
  production change；本輪不執行該 change。

### Artifacts and verification

- Variant reports：`eval/retrieval/reports/8.2-top-k-5-20260908.json`、
  `8.2-top-k-8-20260908.json`、`8.2-top-k-10-20260908.json`。
- Comparison report：`eval/retrieval/reports/8.2-top-k-tradeoff-comparison-20260908.json`。
- Reports 包含 benchmark SHA256、corpus hashes、model/dimensions、chunk controls、threshold、
  candidate-pool behavior、retrieval mode、bounded per-case observations、aggregate metrics、
  context volume 與 latency；沒有保存 full chunk text、raw PDF text、embeddings 或 provider
  raw response。
- Focused retrieval/evaluation suite：`26 passed`。Agent Golden Set：`29/29`，test suite
  `2 passed`。Artifact consistency checks、`py_compile` 與 `git diff --check`：PASS。
- Roadmap 未新增 slice；`8.2` 維持 `done`。未 stage、commit 或 push。

## 2026-09-08 Post-8.2 Paired Final-QA Adoption Gate

### STATUS

- `8.2` 維持 `done`。本輪只執行 evaluation-only paired final-QA gate，沒有修改 production
  `top_k`、candidate pool、threshold、prompt、embedding 或 chunking。
- 已依明確授權將 frozen 3-PDF corpus、30 benchmark queries 與 retrieved context 傳送至目前
  設定的 OpenAI embedding/chat providers。Artifact 不保存 raw provider response、prompt、CoT、
  full chunk text、full PDF text、embeddings 或 secrets。

### QA_PATH AND CONTROLS

- 實際使用 `QAOrchestrator.answer_question()`、`qa_answer_v3`、current citation handling 與
  current `insufficient_info` handling。`top_k` 已是 method parameter，因此只需 evaluation-only
  adapter，沒有 production refactor。
- k=5 與 k=8 共用 frozen benchmark、current parser/chunking `1200/0`、
  `text-embedding-3-small / 1536`、pgvector exact cosine、relevance floor `0.30`、QA model
  `gpt-4o-mini` 與 citation policy。Candidate pool 維持 current behavior：
  `min(top_k * 2, 20)`。

### FINAL-QA RESULTS

| Metric | k=5 | k=8 |
| --- | ---: | ---: |
| Answerable `CORRECT_GROUNDED` | 13/20 (0.650) | 16/20 (0.800) |
| `RETRIEVAL_INCOMPLETE` | 5 | 1 |
| `UNSUPPORTED_OR_INCORRECT` | 2 | 3 |
| Hard-negative safe rejection | 9/10 (0.900) | 9/10 (0.900) |
| Hard-negative unsafe IDs | `rv-020` | `rv-020` |
| Insufficient answers with non-zero citations | 0 | 0 |

- Recovered semantic cases `rv-004`、`rv-005`、`rv-023` 都轉成 user-visible
  `CORRECT_GROUNDED`，因此 k=8 有明確 utility gain。
- `rv-009`、`rv-029` 兩個 variant 都仍有部分 evidence，但 final answer 補出缺失的
  multi-evidence claim，標記為 `UNSUPPORTED_OR_INCORRECT`；multi-evidence retrieval 沒有被解決。
- `rv-027` 是 final-QA regression：k=5 回覆 `insufficient_info` 且 citation 清除，k=8 在兩組
  required Gold 都缺失時產生 unsupported answer，並帶有不支持 claim 的 citations。
- `rv-020` 在 k=5、k=8 都是既有 unsafe case，k=8 沒有新增 hard-negative unsafe case；但這不
  抵銷 `rv-027` 的 positive grounding regression。

### ADOPTION DECISION

- `KEEP_TOP_K_5`。k=8 雖然提升 recovered semantic final QA，但未通過 no-regression / citation
  support gate，因為 `rv-027` 出現新的 unsupported final answer。
- Production change：`False`。本輪不修改 production default。8.2 roadmap state 仍為 `done`。

### ARTIFACTS AND VERIFICATION

- Paired variant reports：`eval/retrieval/reports/top-k-5-final-qa-20260908.json`、
  `eval/retrieval/reports/top-k-8-final-qa-20260908.json`。
- Bounded comparison：`eval/retrieval/reports/top-k-final-qa-comparison-20260908.json`。
- Artifacts 保留 exact final user-visible answer、retrieval/citation source-page-locator metadata、
  bounded classification、case-level delta 與必要 operational observation；沒有保存禁止的 raw
  provider content。
- Focused QA/retrieval tests、artifact invariant checks、`py_compile` 與 `git diff --check`：PASS。
- Agent Golden Set：`29/29`，test suite `2 passed`。未 stage、commit 或 push。

## 2026-09-08 Final-QA Gate Provenance Reconciliation

- Uploaded/stale file mismatch：current working tree 的 review function 位於
  `eval/retrieval/final_qa_gate.py`，current test 位於 `tests/test_final_qa_gate.py`；不存在
  task 所述的 `tests/evals/test_final_qa_gate.py`。因此不能以 uploaded snapshot 判定 current
  implementation 缺少 `apply_bounded_human_review`。
- 確認 human review path：原始 live QA records 先形成 raw automated classification；本次沒有
  provider call、re-index 或 live rerun，而是對已保存 artifacts 做 offline post-processing，套用
  `apply_bounded_human_review`，再重算 variant metrics 與 comparison/adoption decision。
- 修正 evaluation provenance：每個 case 現在明確保存 `automated_classification`、
  `reviewed_classification` 與 review basis；variant/comparison artifact 標記
  `application_mode=offline_post_processing`，aggregate 明確使用 reviewed classification。
- Offline recomputation 保持 `k=5` `CORRECT_GROUNDED=13/20`、`k=8` `16/20`；`rv-027` 仍為
  k=5 `RETRIEVAL_INCOMPLETE`、k=8 `UNSUPPORTED_OR_INCORRECT`；adoption 仍為
  `KEEP_TOP_K_5`。`rv-009`、`rv-029` 的 reviewed labels 依 exact final answer 與 bounded
  evidence metadata 的 claim basis，而非只依 `gold_complete=false` mechanical 判斷。
- Focused reconciliation/QA/retrieval tests：`35 passed`；Agent Golden Set：`2 passed`；offline
  artifact recomputation、`py_compile` 與 `git diff --check`：PASS。`8.2` 維持 `done`，未修改
  production code、roadmap 或 decision。
Final-QA evaluation now separates live automated classification from explicit offline human adjudication.

## 2026-09-08 Evidence Readiness Minimal Implementation

### Done

- 完成 8.4 minimal implementation。新增 strict `EvidenceReadinessDecision`，contract 只有
  `ready: StrictBool`，並拒絕 extra fields。
- Structured substantive Agent path 與 `/api/qa` 共用同一個 readiness provider/helper。Readiness
  位於 accepted Knowledge evidence 之後、final synthesis 之前。
- `ready=false` 由 backend deterministic 回傳 `insufficient_info` 與 zero citations，跳過
  final synthesis。零 accepted Knowledge evidence 維持既有 gate，不呼叫 readiness provider。
- Provider、timeout、malformed structured output 與 extra field 維持既有 provider/contract
  failure semantics，不轉成 semantic `insufficient_info`。
- 新增 `qa_answer_v4`。ready path 的 final provider 只負責 grounded synthesis；ready 後回傳
  legacy `INSUFFICIENT_INFO` 會視為 provider contract failure。`qa_answer_v3` 未修改。
- 沒有修改 parser、chunking、embedding、threshold、top-k、candidate pool、retrieval retry、
  Memory authority、tool-call budget、SSE event contract 或 D033。

### Automated Evidence

- Readiness、Agent runtime、QA orchestrator、API、citation、contextual Memory、prompt loader 與
  impacted regression suites 通過：focused `58 passed`，impacted `121 passed`；沒有 live provider
  call。
- Frozen backend regression 使用 project `.venv` 為 `939 passed, 6 skipped`，包含 Agent Golden
  Set、demo preflight 與 Native MCP targeted coverage。`compileall` 與 `git diff --check` 通過。

### Manual Verification

尚未進行 frontend/browser verification。Not yet manually verified.

### Next

- 由 human 明確授權後，再執行 bounded live-provider regression，覆蓋 sufficient cases 與
  `rv-009`、`rv-027`、`rv-029`。

## 2026-09-08 Evidence Readiness Bounded Live Provider Verification

### Scope

- 依明確授權執行一次 bounded live-provider verification。Provider 為 configured OpenAI，model
  為 `gpt-4o-mini`。
- 使用 frozen 3-PDF corpus、production retrieval contract、`top_k=5`、`qa_answer_v4` 與
  Evidence Readiness contract `v1`。Indexing 使用 disposable PostgreSQL database；沒有寫入
  permanent user memory。
- Case set 僅包含 `rv-001`、`rv-010`、`rv-009`、`rv-027`、`rv-029`、`rv-020` 與一個 controlled
  mixed Knowledge+Memory fixture。每個 case 只執行一次，沒有 prompt、model、temperature、
  top-k 或 threshold tuning。

### Results

- `rv-001` single positive：retrieval accepted 5 chunks，readiness provider 回 `ready=false`，
  deterministic insufficient response，zero citations，沒有 final synthesis。依 gate 規則記錄
  `READINESS_FALSE_NEGATIVE`。
- `rv-010` complete multi-evidence：accepted 5 chunks，readiness `ready=true`，完成 final
  synthesis，5 個 PDF citations，answer 通過 bounded grounded candidate check。
- `rv-009` partial multi-evidence：`ready=false`、insufficient、zero citations、沒有 final，
  answer 沒有補出 `job chaining` 或 `parent_job_id`。
- `rv-027` 與 `rv-029`：都為 `ready=false`、insufficient、zero citations、沒有 final；`rv-029`
  沒有補出 model responsibility guess。
- `rv-020` hard negative：本次為 `CURRENT_RUN_SAFE`，沒有 final、zero citations。這是本次 run
  的 observation，不宣稱已永久修正 hard-negative behavior。
- Mixed Knowledge+Memory：context requirement selector 與 contextual Memory fixture search
  有執行，但本次沒有取得 accepted Knowledge evidence，未呼叫 readiness 或 final synthesis，
  因此 mixed case 未通過 live gate。這個結果保留為一次 provider routing observation，不做
  live rerun。

### Provider and Artifact Evidence

- Provider call behavior：4 次 embedding calls（3 次 corpus indexing、1 次 7-query batch）；6
  次 `evidence_readiness` calls、1 次 `qa_answer` final call，以及 mixed case 的 1 次 reference
  binding 與 1 次 context selection。沒有保存 raw provider response、prompt、CoT、full chunk
  text、embeddings 或 secrets。
- Bounded artifact：[8.4-evidence-readiness-live-20260908.json](../eval/retrieval/reports/8.4-evidence-readiness-live-20260908.json)。Artifact 保存 provider/model、token
  counts、latency、accepted count、source/page/locator metadata、citation metadata 與 exact
  final user-visible answer。

### Verification and Decision

- Live gate：`READINESS_FALSE_NEGATIVE`。因 `rv-001` single positive 沒有通過 readiness，這一輪
  不進行 live rerun、不調參，也不把 8.4 推進到 `manual_verification`。
- `8.4` roadmap state 維持 `automated_verified`。本輪沒有修改 runtime、retrieval contract、
  provider schema 或 decision record。
- Live run 前的 backend regression 為 `939 passed, 6 skipped`，Agent Golden Set 與 demo
  preflight 為 `4 passed`。Live run 後只重跑 readiness、QA、Agent focused suites、Golden Set、
  `py_compile` 與 `git diff --check`。

### Next

- 保留 `rv-001` provider false negative 與 mixed routing observation，交由後續明確授權的
  architecture/provider stability investigation 決定是否新增 diagnostic slice。不要在沒有新
  contract decision 前調整 retrieval parameters 或 readiness schema。

## 2026-09-08 Mixed Knowledge and Memory Owner-Scope Contract Repair

### Root cause

- 分類為 `LIVE_TEST_FIXTURE_SCOPE_BUG`。Single-user production contract 的
  `get_current_owner_id()` 固定回傳 `local`，Knowledge corpus 也以 `local` indexing；8.4
  disposable Agent harness 卻使用 `live-8-4-owner`，因此 Knowledge repository 在 filter stage
  排除整個 corpus。
- Agent production owner propagation、Knowledge repository filtering、Memory owner isolation
  與 native MCP spoof rejection 均維持原 contract，沒有發現需要修改 production owner logic 的
  evidence。

### Repair and verification

- 只將 `/private/tmp/run_8_4_live.py` 的 trusted fixture owner 改回 `local`。沒有修改
  `src/`、Evidence Readiness、D033、retrieval parameters、MCP trust boundary 或 Memory
  authority。
- 新增 deterministic regression，覆蓋 local Knowledge hit、wrong-owner zero evidence、tool
  argument scope spoof、mixed Knowledge and Memory owner separation。
- Focused Agent、mixed context、Memory isolation/save permission、QA 與 Native MCP suites 通過；
  Native MCP 為 `10 passed`。Agent Golden Set 為 `29/29`，`compileall`、harness `py_compile` 與
  `git diff --check` 通過。

### State

- `8.4` 維持 `automated_verified`，D033 不變。
- `rv-001` 的 `PROVIDER_VARIANCE_UNRESOLVED` 保留，沒有 provider rerun、re-index 或參數調整。
- 下一步只允許一次 bounded live verification，覆蓋 mixed Knowledge and Memory 與 targeted
  `rv-001` repeatability probe。

## 2026-09-08 Final Targeted Live Verification

### Scope

- 依授權只執行兩個 case：mixed Knowledge+Memory 與 `rv-001` targeted repeatability probe。
- 使用 configured OpenAI provider、`gpt-4o-mini`、frozen 3-PDF corpus、`top_k=5`、
  `qa_answer_v4` 與 Evidence Readiness contract `v1`。每個 case 只執行一次，沒有 tuning、retry、
  prompt 或 production source change。
- Disposable database 與 single-user owner scope 均使用 `local`。沒有寫入 permanent memory。

### Results

- Mixed Knowledge+Memory：Knowledge retrieval 有 10 個 candidates、5 個 accepted evidence，
  context selector 回報 `needs_knowledge=true` 與 `needs_memory=true`，Memory 執行兩次 contextual
  search，兩次均為 `owner_id=local`、`top_k=3`。Readiness 有呼叫但回傳 `ready=false`，沒有 final
  synthesis，zero citations，runtime observation 的 `used_saved_memory=false`，mixed case 未通過。
- `rv-001`：Knowledge retrieval 有 10 個 candidates、5 個 accepted evidence。Gold support 存在於
  rank 4、page 2、score `0.462543`。Readiness 回傳 `ready=false`，沒有 final synthesis，zero
  citations，分類為 `REPEATED_READINESS_FALSE_NEGATIVE`。
- 兩案 owner boundary 均為 `local`，沒有再現先前 fixture owner mismatch。這只確認 owner-scope
  repair 與 retrieval path，不能視為 readiness semantic issue 已解決。

### Artifact and Decision

- Bounded artifact：[8.4-evidence-readiness-targeted-live-20260908.json](../eval/retrieval/reports/8.4-evidence-readiness-targeted-live-20260908.json)。Artifact 未保存 raw provider response、prompt、CoT、full chunk text、embeddings 或 secrets。
- 本輪 provider usage 為 4 次 embedding calls、4 次 LLM calls。沒有再執行其他 benchmark，也沒有
  live rerun、re-index 或參數調整。
- `8.4` roadmap state 維持 `automated_verified`，D033 不變。Targeted live gate 為 `FAIL`，因
  mixed case 未完成 readiness/final path，且 `rv-001` 在 Gold support 存在時再次被 readiness 拒絕。

### Verification and Next

- Live 後 focused owner-scope test 為 `2 passed`，context/mixed suite 為 `38 passed`，Agent Golden
  Set 為 `29/29`，`git diff --check` 通過。
- 下一步限於一次 bounded readiness semantic false-negative diagnostic。暫不調整 retrieval
  parameters、embedding、threshold、top-k、provider model、temperature 或 readiness schema，也不
  推進到 `manual_verification`。

## 2026-09-08 Bounded Semantic Prompt Correction

### Change

- 將 `EVIDENCE_READINESS_SYSTEM_MESSAGE` 的 semantic rule 改為：`ready=true` 代表 accepted
  Knowledge evidence 足以產生至少一個 materially complete、correct、grounded answer，且不需
  exhaustive source coverage、逐字涵蓋每個 query phrase 或支援 optional details。
- 明確規定 readiness 只評估回答所需的 material Knowledge-backed claims。Separately designated
  Memory-side dependencies 不算缺少的 Knowledge requirements，但 Memory 不能補足缺少的
  Knowledge claim。
- `EvidenceReadinessDecision` 維持 `{ready: StrictBool}`，沒有修改 retrieval、Memory、
  `qa_answer_v4`、D033 或架構拓撲。Production prompt 未加入 benchmark case ID、few-shot、reasoning
  或新 schema field。

### Verification

- 先以 prompt contract test 取得 1 個預期失敗，再完成 correction 後通過。
- Evidence Readiness 與 mixed Agent tests：`44 passed`。
- Agent runtime readiness tests：`14 passed`。
- QAOrchestrator readiness/citation tests：`9 passed`。
- QA API insufficient/provider-error tests：`6 passed`。
- Agent Golden Set：`29/29`。
- 本輪未呼叫 live provider；`compileall`、`py_compile` 與 `git diff --check` 通過。

### State

- `8.4` 維持 `automated_verified`，D033 不變，未推進到 `manual_verification`。
- 下一輪才執行明確授權的 bounded live semantic regression，涵蓋 positive `rv-001`、sufficient
  single factual、`rv-010`、mixed Knowledge+Memory，以及 negative `rv-009`、`rv-027`、`rv-029`、
  `rv-020`，每案一次。

## 2026-09-08 Final Bounded Live Semantic Regression

### Scope and Results

- 依授權執行 8 個 case，每案一次；使用 configured OpenAI provider、`gpt-4o-mini`、frozen
  `top_k=5`、`owner_scope=local`、既有 retrieval contract 與 `qa_answer_v4`，沒有 retry、tuning、
  re-index 或 production source change。
- Positive：`rv-001`、`rv-002`、`rv-010` 均通過 readiness、final synthesis、bounded grounded
  candidate 與 PDF citation checks。`rv-002` 使用既有 top-k=5 Gold rank 1 的 single-document
  factual control。
- Mixed Knowledge+Memory 未通過：context selector 回報 `needs_knowledge=true`、
  `needs_memory=true`，Memory search 使用 `owner_id=local`，但 readiness 回 `ready=false`；沒有
  final synthesis、citation 或 `used_saved_memory` authority path，記錄為 1 個 readiness
  false negative。
- Negative：`rv-009`、`rv-027`、`rv-029`、`rv-020` 均安全停止（`ready=false`、insufficient、
  沒有 final synthesis、zero citations），沒有觀察到 false positive。

### Artifact and Verification

- Bounded artifact：[8.4-evidence-readiness-semantic-live-20260908.json](../eval/retrieval/reports/8.4-evidence-readiness-semantic-live-20260908.json)。保存 provider/model、token/latency、retrieval/citation metadata 與 bounded final answer；沒有保存 raw provider response、prompt、CoT、full chunks、embeddings 或 secrets。
- Provider behavior：4 次 embedding calls、13 次 LLM calls；final synthesis 只在 readiness `true`
  時呼叫。Live semantic gate 為 `FAIL`，positive `3/4`、negative `4/4`、false negative `1`、
  false positive `0`。
- Live 後 focused regression：`71 passed`；Agent Golden Set `29/29`；`git diff --check` 通過。

### State

- `8.4` roadmap 維持 `automated_verified`，不推進到 `manual_verification`；D033 不變。
- 本輪已停止 live work，不做 rerun、調參或 runtime implementation。Mixed readiness false
  negative 留待後續明確授權的 diagnostic slice。

## 2026-09-09 Backend Memory Resolution Signal

### Change

- 完成 8.4 最小 offline implementation：backend 以每個 required contextual Memory facet 的
  實際 `search_memory` result 計算 `memory_dependencies_resolved`，不以 deduplicated Memory
  record count 代替 dependency resolution。
- `needs_memory=false` 時 readiness input 不帶 Memory resolution requirement；`needs_memory=true`
  時才帶 bounded boolean signal。`EvidenceReadinessDecision` 仍只有 `{ready: StrictBool}`，
  沒有把 Memory content 傳入 readiness，也沒有修改 D033、retrieval、embedding 或 provider
  contract。
- Mixed Knowledge + Memory 只在所有 required Memory dependencies resolved 且 Knowledge readiness
  通過時進入 final synthesis；partial、zero 或 unresolved Memory 會 deterministic fail closed，
  回傳 `insufficient_info=true` 與 zero citations。Knowledge-only、direct Memory-only、QAOrchestrator
  與 explicit-save/isolation paths 維持既有邊界。

### Verification

- TDD 先加入 resolution signal、missing signal、partial/all unresolved、duplicate record per
  facet、Knowledge authority 與 no-rescue regression tests；紅燈後完成 minimal implementation。
- Focused regression：context/readiness、runtime/mixed memory、Memory/isolation/citation、
  QA/MCP 共 `125 passed`。
- 本輪只做 deterministic offline verification，沒有 live provider、embedding、re-index、retry
  或 retrieval tuning。下一輪 live scope 限定為一次 targeted mixed verification，不重跑 8-case suite。

### State

- `8.4` roadmap 維持 `automated_verified`，D033 不變，未推進到 `manual_verification`。
- 未 stage、commit 或 push。

## 2026-09-09 8.4 Final Targeted Mixed Live Verification

### Scope

- 依授權準備執行一次 mixed Knowledge + Memory case，使用 configured OpenAI provider、
  `gpt-4o-mini`、`owner_scope=local`、frozen 3-PDF corpus、`top_k=5`、`qa_answer_v4` 與
  Evidence Readiness contract `v1`。
- 未修改 production config、prompt、model、temperature、retrieval、Memory fixture 或
  任何 runtime code。

### Result

- 唯一一次實際 live harness execution 在建立 `EmbeddingBatchService` 時失敗。失敗的
  `RecordingEmbeddingClient` 沒有將 `get_capabilities()` delegation 到
  `OpenAIEmbeddingClient`，因此 constructor 直接產生 `EmbeddingBatchError`，內層 reason 為
  `CAPABILITY_UNAVAILABLE`；沒有建立 outbound embedding request。
- 因此尚未執行 Reference Binding、Context Requirement Selection、Knowledge retrieval、
  Memory retrieval、backend resolution signal、Evidence Readiness 或 Final Synthesis。
- 本次沒有產生 user-visible answer、citation 或 permanent Memory write。依 No Retry 規則
  不再呼叫 provider，也不把本次結果分類為 provider transient、Memory acquisition、
  resolution 或 readiness semantic failure。本次 primary classification 為
  `LOCAL_HARNESS_DEFECT`。

### Diagnostic

- 目前預期的 provider request 為 `openai`、`text-embedding-3-small`、1536 dimensions，
  首批預計 22 inputs；這些值與前次成功 run 相同。成功 run 的 frozen corpus chunk counts
  為 `22/17/40`，三份 corpus SHA256 也一致。
- 目前沒有 HTTP/provider status、provider category、retry attempt 或 exhausted retry；
  failure 發生在 `_execute_batch()` 之前。Retry policy 維持既有 `max_attempts=3`、backoff
  `1s` 起始、上限 `30s`，沒有修改。
- 與成功 harness 的 deterministic difference 是 capability delegation 缺失。這使本次為
  `REQUEST_CONTRACT_CHANGED`，但不是 production embedding request contract 變更。

### Artifact and State

- Bounded artifact：[8.4-evidence-readiness-mixed-final-live-20260909.json](../eval/retrieval/reports/8.4-evidence-readiness-mixed-final-live-20260909.json)。未保存 raw provider response、prompt、CoT、full chunks、Memory content、embeddings 或 secrets。
- 本次 gate 結果為 `FAIL`，classification 為 `LOCAL_HARNESS_DEFECT`，failure boundary 為
  `EmbeddingBatchService construction -> capability validation`。
- `8.4` 維持 `automated_verified`，D033 unchanged；未推進到 `manual_verification`。

### Next Action

- 只建議修復 harness 的 `get_capabilities()` delegation，並先做 offline adapter contract
  verification；修復後若要重新執行同一 mixed live case，需另取得明確授權。

### Offline Verification

- `tests/test_embedding_batch_service.py`：`28 passed`。
- `tests/test_embedding_client.py`：`28 passed`。
- `tests/test_external_error.py`：`4 passed`。
- `git diff --check`：PASS。
- 本輪沒有 production code change、embedding provider call、LLM provider call、live retrieval
  或 re-index。

### Post-run Checks

- Focused mixed/readiness regression：`6 passed`。
- QA API 的 readiness/grounding/provider regression：`5 passed`。
- Agent Golden Set：`29/29`，test suite `2 passed`。
- `git diff --check`：PASS。
- 本次只新增 bounded failure artifact 與本日誌；未 stage、commit 或 push。

## 2026-09-09 8.4 Harness Contract Repair

### Repair

- 根因維持 `LOCAL_HARNESS_DEFECT`：`RecordingEmbeddingClient` 缺少
  `get_capabilities(model, dimensions)` 對 delegate 的 forwarding，讓
  `EmbeddingBatchService` 在 capability validation 階段收到 `None`。
- 已確認 repaired adapter 同時透明保留 `name`、`get_capabilities()`、`embed()` 與
  delegate exception propagation；`EmbeddingBatchService` 的 validation 沒有被 bypass。
- 修復只限 live verification harness contract；沒有修改 production embedding、retry、model、
  dimensions、batching、retrieval 或 8.4 readiness。

### Offline Verification

- 新增 harness-level adapter contract test：`5 passed`，涵蓋 capability delegation、supported
  construction、unsupported fail-closed、single embed delegation、bounded recording 與 error
  propagation。
- Embedding/provider regression：`60 passed`；mixed/readiness focused regression：`13 passed`。
- Frozen request contract：`text-embedding-3-small / 1536`、chunk counts `22/17/40`，corpus
  manifest 與前次成功 run 一致。
- `py_compile` 與 `git diff --check`：PASS。所有檢查均未發出 network/provider request。

### State

- `8.4` 維持 `automated_verified`，D033 unchanged。
- 本輪禁止 live rerun；下一次 targeted mixed live case 仍需 separate explicit authorization。
- 未 stage、commit 或 push。

## 2026-09-09 8.4 Final Mixed Live Verification Rerun

### Scope

- 依 separate explicit authorization，只執行一次 frozen mixed Knowledge + Memory scenario；沒有
  重跑其他 7 個 benchmark case，也沒有修改 model、prompt、temperature、retrieval、Memory
  fixture、batch 或 retry policy。
- repaired `RecordingEmbeddingClient` 通過 capability validation；使用 `owner_scope=local`、
  `text-embedding-3-small / 1536`、pypdf、chunk counts `22/17/40` 與 disposable PostgreSQL。

### Result

- Indexing 完成；embedding calls 為 frozen corpus `22/17/40` 加上 mixed query `1`，沒有 provider
  contract error。
- Context Requirement Selection 回傳 `needs_knowledge=true`、`needs_memory=true`，兩個
  contextual facets 為 `organization size` 與 `staged development`。
- Knowledge retrieval 得到 `10` candidates、`5` accepted evidence，relevance floor `0.30`；
  evidence 與 citation authority 維持 PDF-only。
- 兩個 contextual Memory searches 均成功，`owner_id=local`、各 `1` candidate/accepted hit。
  Backend runtime signal 為 required `2`、resolved `2`、`memory_dependencies_resolved=true`。
- Readiness input 收到 `memory_dependencies_resolved=true` 且未混入 Memory-as-Knowledge evidence，
  但 provider 回傳 `ready=false`。本輪分類為
  `PERSISTENT_MIXED_READINESS_FALSE_NEGATIVE`，不是 harness、embedding、Memory acquisition 或
  resolution-signal failure。
- 因 readiness false，Final Synthesis 沒有執行；user-visible result 為 insufficient-info，沒有
  citation，也沒有 `used_saved_memory` answer path。這是本次 live gate failure 的預期 boundary。

### Provider and Artifact

- Provider calls：embedding `4`；LLM `3`（reference binding、context selection、readiness），
  沒有 final synthesis call。只保存 bounded model、token、latency、retrieval/citation metadata。
- [8.4-evidence-readiness-mixed-final-live-20260909.json](../eval/retrieval/reports/8.4-evidence-readiness-mixed-final-live-20260909.json)
  已保留前次 `LOCAL_HARNESS_DEFECT` attempt history 與本次 rerun 結果；沒有保存 raw provider
  response、hidden prompt、CoT、embeddings、full PDF chunks 或 full Memory content。

### State

- `8.4` 維持 `automated_verified`，D033 unchanged；不推進到 `manual_verification`。
- 本輪已停止，不做 live retry、prompt tuning、retrieval tuning 或 Memory tuning。下一步若要修正
  仍需新的明確 implementation/diagnostic scope；本次不自動建立 `8.4.1`。
- 未 stage、commit 或 push。

## 2026-09-09 8.4 Restore Optional Memory / Knowledge-only Readiness

### Contract Repair

- D025、D027、D033 維持不變；contextual Memory 回復為 optional supplemental context。
- 移除 `memory_dependencies_resolved` 對 Evidence Readiness 的 semantic input 與 runtime
  hard gate coupling。partial 或 zero Memory hit 不再使足夠的 Knowledge evidence 進入
  `insufficient_info`。
- 保留 `memory_required_dependency_count`、`memory_resolved_dependency_count` 與
  `memory_dependencies_resolved` workflow metadata，僅供 observability；Knowledge/Memory
  authority separation、citation authority 與 retrieval freeze 不變。

### Verification

- 先以 deterministic regression assertions 重現舊行為：`6 failed, 1 passed`。
- 修復後 focused mixed/readiness、Agent、Memory、citation 與 QA suites：`119 passed`。
- Agent Golden Set：`29/29`；`tests/test_agent_eval.py`：`2 passed`。
- Full backend regression：`949 passed, 6 skipped`；首次 collection 曾受既有 `mcp` venv
  環境缺件影響，改用 frozen venv interpreter 後完成相同 suite。
- `compileall`：PASS；`git diff --check`：PASS；最終 full regression 已包含 contract
  fixture 更新。

### State

- `8.4` 維持 `automated_verified`；未推進到 `manual_verification`，不建立 `8.4.1`。
- 本輪沒有 live provider、embedding provider、live retrieval 或 re-index；未 stage、commit
  或 push。

## 2026-09-09 8.4 Final Optional-Memory Live Verification

### Result

- 依明確授權只啟動一次 controlled mixed Knowledge + Memory live run。執行在
  `OPENAI_API_KEY` preflight availability boundary 終止，沒有送出 provider request，沒有
  indexing、embedding、context selection、Memory retrieval、Evidence Readiness 或 Final
  Synthesis observation。
- Failure classification 為 `HARNESS_FAILURE`，不是 embedding/provider contract failure；本輪
  不 retry、不調整 prompt、model、temperature、retrieval、Memory 或 embedding config。
- 指定 bounded artifact 已建立，並以摘要保留前次 `LOCAL_HARNESS_DEFECT` provenance；沒有保存
  API secret、raw provider response、hidden prompt、CoT、embedding、full PDF chunk 或 full
  Memory content。

### Offline Verification

- Focused optional-Memory/readiness、Agent runtime、Memory、citation、QA 與 embedding/provider
  contract suites：`184 passed`。
- Agent Golden Set：`2 passed`；`git diff --check`：PASS。

### State

- `8.4` 維持 `automated_verified`，D025、D027、D033 unchanged；不推進到
  `manual_verification`。
- 本輪已在 exact preflight failure boundary 停止，不做 live retry、browser verification、
  full backend regression 或其他 benchmark case。未 stage、commit 或 push。

## 2026-09-09 8.4 Optional-Memory Live Verification Rerun After API-Key Preflight

### Result

- 依新的明確授權，只檢查既有 shell environment 的 API-key availability，結果為
  `OPENAI_API_KEY_AVAILABLE = false`。
- 依 stop condition 立即停止；沒有 provider request、indexing、retrieval、Memory search、
  Evidence Readiness 或 Final Synthesis，也沒有執行測試或 retry。
- Artifact 已更新為 `HARNESS_FAILURE`，並保留本次與前次 API-key preflight failure，以及更早的
  `LOCAL_HARNESS_DEFECT` provenance；沒有保存 secret 或 provider response。

### State

- `8.4` 維持 `automated_verified`，D025、D027、D033 unchanged；不推進到
  `manual_verification`。
- 本輪已在 `preflight -> OPENAI_API_KEY availability` 停止。未 stage、commit 或 push。

## 2026-09-09 8.4 Optional-Memory Final Live Verification Execution

### Scope

- Repository root `.env` 存在；在同一 shell source 後，preflight 結果為
  `OPENAI_API_KEY_AVAILABLE = true`。只執行一次 frozen mixed Knowledge + Memory scenario，
  未執行其他 benchmark case，未 retry 或 tuning。
- 使用 `owner_scope=local`、pypdf、chunk `1200/0`、`text-embedding-3-small / 1536`、pgvector
  cosine、relevance floor `0.30`、`top_k=5` 與現行 optional-Memory contract。

### Result

- Indexing 完成，三份 PDF chunk counts 為 `22/17/40`；embedding calls 為 `4`，包含 frozen
  corpus batches 與 mixed query batch。
- Context Requirement Selection 為 `needs_knowledge=true`、`needs_memory=true`，facets 為
  `organization size` 與 `staged development`。
- Knowledge retrieval 為 `10` candidates、`5` accepted evidence，best score `0.531268`，
  relevance floor `0.30`；Memory 兩個 facet 都執行 contextual search，`owner_id=local`，各有
  `1` accepted hit。
- Readiness input 包含 exact task、`5` accepted PDF evidence 與 bounded facet labels，沒有
  `memory_dependencies_resolved`、Memory content、Memory citation 或 rejected candidate。
- Evidence Readiness 執行但回傳 `ready=false`，因此分類為
  `PERSISTENT_MIXED_KNOWLEDGE_READINESS_FALSE_NEGATIVE`。Final Synthesis 未執行；本次 live gate
  在 readiness boundary 停止，沒有 retry 或 prompt 修改。
- Runtime resolution counters 在 readiness-false path 未捕獲；Memory search observations
  仍確認兩個 facet 都有 accepted hit，且 citations 為空，沒有 Memory authority leakage。

### Verification

- Focused optional-Memory/readiness、Agent runtime、Memory、citation、QA 與 embedding/provider
  contract suites：`184 passed`。
- Agent Golden Set：`2 passed`；`git diff --check`：PASS。
- [8.4-optional-memory-final-live-20260909.json](../eval/retrieval/reports/8.4-optional-memory-final-live-20260909.json)
  已保留前兩次 API-key preflight 與較早 `LOCAL_HARNESS_DEFECT` history；未保存 secret、raw
  provider response、hidden prompt、CoT、embedding、full PDF chunk 或 full Memory content。

### State

- `8.4` 維持 `automated_verified`，D025、D027、D033 unchanged；不推進到
  `manual_verification`。
- 本輪已停止，不做 live retry、readiness redesign、retrieval tuning、Memory tuning 或
  browser verification。未 stage、commit 或 push。

## 2026-09-09 8.4 Bounded Closure and Known Limitation Sync

### Closure

- 停止所有 8.4 live tuning。Deterministic Evidence Readiness implementation、optional Memory
  fallback、Knowledge/Memory authority separation 與 automated verification 均維持完成。
- Actual-provider Knowledge-only positive probes `rv-001`、`rv-002`、`rv-010` pass；incomplete/
  negative safety probes `rv-009`、`rv-027`、`rv-029`、`rv-020` safe。
- Controlled mixed Knowledge + Memory case 的 indexing、context selection、Knowledge retrieval、
  contextual Memory retrieval 與 owner scope pass，但 readiness 仍為 `ready=false`，分類為
  `PERSISTENT_MIXED_KNOWLEDGE_READINESS_FALSE_NEGATIVE`。此 limitation 不表示 Memory hard gate
  回復；它表示 whole mixed task 與 Knowledge-backed portion 的 provider semantic separation
  尚未穩定。

### State

- `8.4` 維持 `automated_verified`，不標記 `manual_verification` 或 `done`，不建立 `8.4.1`。
- 後續 mixed semantic-stability work deferred，不選擇或實作 task decomposition、planner 或
  another verifier；不阻塞其他 MVP priority。
- D025、D027、D033 unchanged；既有 bounded artifacts 保留，不重跑、不改寫 historical
  outcomes。未 stage、commit 或 push。

## 2026-09-09 6.0.3.1 Deterministic Explicit Save Command

### Scope

- Public conversation 在 backend 產生有效 `ExplicitSaveIntent` 後，統一直接使用
  `MemoryService.save_memory`。Tool-capable provider 不再決定 public explicit save 是否寫入。
- `save_memory` tool 保留給 MCP 與其他 tool execution boundary；trusted explicit-save
  authorization、owner scope、provider/MCP argument protection 與既有 persistence policy 不變。
- Mixed explicit-save 加 substantive task 仍沒有 typed split contract，本 slice 不支援；
  `detect_explicit_save_intent` semantics 未修改。

### Implementation

- `ConversationOrchestrator.send_message` 的 explicit-save branch 不再進
  `BoundedAgentRuntime`，沿用既有 direct `MemoryService` path。
- Pure explicit save 不呼叫 final-answer provider，SSE 只發 `saving_memory` execution status，
  完成後送 `done`；save failure 送 safe `error`，不建立 fake assistant success。
- Deterministic public save 沿用既有 `workflow_run_id=0`、provider/model 為 null 的 direct-save
  metadata semantics，沒有偽造 provider tool call。

### Automated verification

- Public conversation API/SSE A、B、C parity、tool-capable/non-tool provider parity、duplicate、
  embedding failure、ordinary statement、6.0.3 trusted type/content safeguard 與 Native MCP
  regression 已完成 focused verification。Backend full suite：`958 passed, 6 skipped`；
  frontend：`62 passed`；production build、compileall 與 `git diff --check`：PASS。
- Mixed parser contract、Knowledge、Memory relevance threshold 與 frontend event mapping 未修改。

### Manual verification

- Exact C 第一次 save：PASS
- 顯示「已儲存記憶」：PASS
- 同一句第二次 save：PASS
- 顯示「記憶已存在 / ALREADY SAVED」：PASS
- 沒有 `Generating answer...` phase：PASS
- 沒有 `Request failed` / `AGENT_RUNTIME_FAILED`：PASS
- Deterministic explicit-save browser behavior：PASS

## 2026-09-09 8.5 Roadmap Reprioritization

- 新增 `8.5 User-Facing Answer Quality Diagnostic`，status 為 `planned`，並設為下一個唯一主線
  priority。
- `7.0 Formal Browser Demo Story` 維持 `manual_verification`，暫排在 `8.5` diagnosis 後。
- 本輪只有 documentation / roadmap planning；沒有 runtime、test、prompt、retrieval 或 provider
  changes。

## 2026-09-09 8.5 User-Facing Answer Quality Diagnostic

### Scope and artifact

- 完成 12 題人工 review 的 bounded diagnostic set；UQ-001「agentic system 有哪些權限管理要做」與
  UQ-002「agentic system 要注意什麼地方」均完成一次 primary actual-provider run，沒有 repeat。
- 使用 current local Knowvia database、owner scope `local` 與既有 production flow。Corpus snapshot
  為 11 個 indexed sources、225 個 eligible chunks；retrieval settings 維持
  `text-embedding-3-small / 1536`、pgvector cosine、`knowledge_relevance_floor=0.30`、candidate
  pool production behavior 與 `top_k=5`。
- Bounded machine-readable artifact：[8.5-user-facing-answer-quality-20260909.json](../eval/retrieval/reports/8.5-user-facing-answer-quality-20260909.json)。Artifact 只保存 query、selector、retrieval metadata、accepted evidence metadata、readiness/final state 與 classification；未保存 secret、raw provider response、hidden prompt、CoT、embedding、vector、full source text 或 raw Memory content。

### Findings

- 12 題 aggregate：`completed=4`、`expected_insufficient=1`、`provider_failure=3`、
  `readiness_false_negative=2`、`acceptance_failure=1`、`retrieval_failure=1`；selector、evidence
  coverage、final synthesis failure 與 invalid setup 均為 `0`。
- 11 題 `SHOULD_ANSWER` 中有 4 題最後 `insufficient_info=true`。False Insufficient root-cause
  distribution：readiness false negative `2`、acceptance failure `1`、retrieval failure `1`。
- UQ-001 的 10 個 raw candidates 中已有可支撐 bounded answer 的 accepted evidence（5 個 accepted），
  但 Evidence Readiness 回傳 `ready=false`，未呼叫 Final Synthesis；分類為
  `readiness_false_negative`。
- UQ-002 的 raw candidate pool 已包含 Gold source 的 supporting pages，但必要 evidence 位於未被
  acceptance top-5 接受的 candidates；之後 readiness 亦為 `false`，primary stop boundary 為
  `acceptance_failure`，未呼叫 Final Synthesis。
- UQ-003、UQ-007、UQ-010 在 provider boundary 以 `provider_error` 結束；未將 provider failure
  當成 semantic insufficient，也沒有 retry。
- Broad/open-ended 的兩題均未完成：一題為 readiness false negative、一題為 acceptance failure。
  Technical identifier、中文 query 對英文 Knowledge 與 multi-evidence cases 顯示 mixed boundaries；
  sample 與 provider failures 不足以支持單獨的 language 或 identifier optimization 結論。

### Verification and decision

- `tests/test_user_facing_diagnostic.py`：`10 passed`；Agent Golden Set：`29/29`；compile 與
  `git diff --check`：PASS。
- Primary recommendation：`READINESS_WORK_SUPPORTED`，僅限後續 evaluation / owner decision；
  acceptance 與 retrieval 各有單一 case evidence，尚不支持 BM25/RRF、reranker、embedding
  migration 或 production optimization。Final Synthesis work：`NO EVIDENCE`。
- 本輪沒有修改 runtime、tests 以外的 production behavior、parser、chunking、embedding、threshold、
  candidate pool、prompt、provider config、Knowledge data 或 Memory data；沒有 stage、commit 或 push。

### State

- `8.5` diagnosis complete，roadmap status 更新為 `done`；不自行建立下一個 implementation ID。
- `7.0` 維持 `manual_verification`，Formal Browser Demo Story 保留；`8.4` historical outcome 與
  D032 / D033 不變。

## 2026-09-09 8.5 Provider-Failure Triage

- 只針對 primary run 的 `provider_failure` cases UQ-003、UQ-007、UQ-010 各執行 2 次 bounded repeat；
  沒有重跑其他 diagnostic cases。Artifact：[8.5-provider-failure-triage-20260909.json](../eval/retrieval/reports/8.5-provider-failure-triage-20260909.json)。
- 三題的 repeat failure 都集中在 `Context Requirement Selection`；每次 failure 都是
  `provider_contract_failure`，沒有 rate limit、timeout 或 transport category。Reference Binding
  在 failure repeat 中先成功；Evidence Readiness 與 Final Synthesis 沒有在這些 failure repeat 中被呼叫。
- UQ-007、UQ-010 兩次 repeat 都是 deterministic `selector_provider_failure`。UQ-003 第一次是相同
  selector contract failure，第二次完整完成，分類為 `intermittent_provider_failure`。
- 這批 provider failures 不是單純 transport robustness 問題；存在可重現的 selector structured-output
  contract failure。下一步不能直接把 Readiness Usability Study 當成唯一 provider reliability 前置假設；
  需先由 owner 決定是否建立 bounded selector contract stability slice。本輪沒有修改 production
  behavior、provider config、retry policy、roadmap status 或 architecture decision。

## 2026-09-09 8.6 Context Requirement Selector Contract Stability Started

- 已完成 current structured-output path、effective JSON Schema、backend Pydantic validator 與
  provider adapter 的 deterministic inspection。Current mechanism 是 OpenAI
  `response_format.type=json_schema`，adapter 只做 JSON object extraction，之後由
  `ContextRequirementDecision.model_validate` 執行 backend validation；目前沒有 intermediate
  field normalization。
- 新增 evaluation-only safe contract fingerprint harness，僅保存 error stage、validation type、
  bounded field path/rule、top-level field presence/type 與 `contextual_facets` count，不保存 provider
  field values、prompt 或 raw response。
- Selector-only actual-provider probe 尚未執行。安全審核要求對 UQ-003/UQ-007/UQ-010 各 3 次及
  UQ-004/UQ-005/UQ-008 各 1 次的具體 call scope 重新明確批准；本輪未嘗試繞過，也未修改 production
  selector、schema、provider config、retry、retrieval、readiness 或 final synthesis。

## 2026-09-09 8.6 Context Requirement Selector Contract Stability Complete

- 依一次性 bounded authorization，完成 12 次 selector-only actual-provider calls：UQ-003、UQ-007、
  UQ-010 各 3 次；UQ-004、UQ-005、UQ-008 各 1 次。只執行既有 Context Requirement Selection、
  JSON extraction 與 `ContextRequirementDecision.model_validate()`；沒有呼叫 retrieval、Memory、
  Evidence Readiness、Final Synthesis、SSE 或 persistence。
- Artifact：[8.6-selector-contract-probe-20260909.json](../eval/retrieval/reports/8.6-selector-contract-probe-20260909.json)。Artifact 只保存 bounded operation status、safe contract fingerprint 與 shape metadata，未保存 provider values、raw response、prompt、source text、Memory content 或 secrets。
- UQ-003、UQ-007、UQ-010 的 9/9 failure fingerprints 完全相同：`error_stage=cross_field_validation`、
  `validation_error_type=value_error`、root-level `invalid_cross_field_combination`；四個必要 top-level
  fields 均存在且型別正確，`contextual_facets_count=2`。Primary selector shape 是
  `needs_knowledge=true`、`needs_memory=false`，但 non-empty `contextual_facets` 違反 backend
  的 no-Memory cross-field invariant。
- UQ-004、UQ-005、UQ-008 的 3/3 control calls 均 completed；shape 相同但
  `contextual_facets_count=0`。UQ-003 在本次 isolated selector-only probe 為 3/3 failure；上一輪
  end-to-end triage 的 intermittent outcome 仍保留，但每次已觀察到的 failure 都是同一條 contract rule。
- Primary finding：`BACKEND_SCHEMA_MISMATCH`。Provider-visible JSON Schema 未表達 cross-field
  invariant，因而接受 backend Pydantic 會拒絕的組合。Secondary finding：
  `CROSS_FIELD_CONTRACT_TOO_FRAGILE`。本輪沒有 evidence 支持 transport retry、adapter parse fix、
  prompt change 或 production schema change；沒有修改 production behavior。

### 8.6 State

- `8.6` diagnosis complete，roadmap status 更新為 `done`。`8.5`、`8.4`、`7.0` 與 D027 維持原狀；
  Readiness Usability Study 仍是下一個 evidence-supported direction，不新增 `8.7` 或 implementation ID。

## 2026-09-09 8.6.1 Context Requirement Provider Wire Contract Design

- 依 8.6 evidence 完成 backend valid-state truth table：Knowledge-only、mixed Knowledge + Memory、
  direct Memory-only 與 neither。`ContextualFacet` bounds、atomic facet rule、owner/authority
  separation、最多 2 個 facets、direct `memory_query` compatibility、max 3 tool calls 與
  backend fail-closed validation 均維持不變。
- OpenAI Structured Outputs supported-subset inspection 確認：root object、nested `anyOf`、branch
  required fields、enum discriminator、array/string bounds 與 `additionalProperties=false` 可用；
  root-level `anyOf` 與 `if/then/else`、`dependentSchemas` 等跨欄位方式不可用。既有四欄位 root
  shape 因而不能完整表達 domain cross-field state space。
- Design freeze 選定 provider wire DTO：strict root object 的 `selection` nested union，四個 modes
  為 `knowledge_only`、`mixed`、`memory_only`、`neither`。Backend 以 deterministic structural
  mapping 產生既有 `ContextRequirementDecision`；mapping 不得 repair、猜測、刪除、截斷、補預設值
  或 retry。Provider DTO / mapping invalid 仍為 provider/contract failure。
- 新增 D035（Accepted），但不修改 D027 historical product semantics。8.6.1 roadmap status 為
  `planned`；本輪只完成 architecture / contract design，implementation、TDD 與 UQ-003/UQ-007/
  UQ-010 bounded selector-only actual-provider verification 留待下一輪。

## 2026-09-09 8.6.1 Context Requirement Provider Wire Contract Implementation

- 新增 provider-only `ContextRequirementWireDecision`。Effective provider schema 是 strict root
  object，唯一 required root field 為 `selection`；nested `anyOf` 只允許 `knowledge_only`、
  `mixed`、`memory_only`、`neither` 四個 branch。Root、branch 與 facet object 均使用
  `additionalProperties=false`；schema 不含 root-level `anyOf` 或 unsupported composition keywords。
- Selector production path 改為 wire validation、deterministic mapping、既有
  `ContextRequirementDecision.model_validate()`。Mapper 不做 semantic repair、retry、fallback、
  facet drop/truncate 或 query rewrite。Wire/domain validation failure 使用既有
  `provider_contract_error`；provider runtime failure 仍使用 `provider_error`。
- Selector prompt 只同步新的 `selection.mode` 與 branch-specific fields。Reference Binding、
  retrieval、Memory、Evidence Readiness、final synthesis、SSE、provider model、retry/timeout、public API
  與 database schema 均未修改。8.5/8.6 diagnostic seam 保存 mapped domain flags、facet count、
  memory-query presence 與 wire mode，不保存 raw wire output 或 provider values。
- Automated verification：wire/schema/mapper、selector、provider 與 diagnostic focused tests
  `91 passed`；conversation/orchestrator regression `82 passed`；Agent Golden Set `29/29`；compileall
  pass。第一次 full backend run 為 `998 passed, 6 skipped, 1 failed`，唯一 failure 是既有 SQLite
  concurrent idempotency timing test；isolated rerun pass，第二次 full backend 為
  `999 passed, 6 skipped`。
- `8.6.1` 維持 `in_progress`。Automated implementation complete；本輪未使用舊的 live-call
  authorization。Completion 前仍需 owner 授權 UQ-003、UQ-007、UQ-010 各一次 selector-only
  actual-provider verification，總計 3 calls。Readiness Usability Study 排在此 slice 後。
