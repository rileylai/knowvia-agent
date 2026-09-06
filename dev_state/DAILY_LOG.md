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
