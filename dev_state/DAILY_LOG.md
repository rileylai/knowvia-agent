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
