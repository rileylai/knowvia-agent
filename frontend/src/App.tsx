import { ChangeEvent, FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";

import {
  askQuestion,
  indexImage,
  indexPDF,
  indexURL,
  listKnowledgeSources,
  type ImageIndexResponse,
  type KnowledgeSource,
  type PDFIndexResponse,
  type QAResponse,
} from "./api";

type Surface = "knowledge" | "chat" | "memory";
type RequestState = "idle" | "loading" | "success" | "error";
type InventoryState = "loading" | "success" | "empty" | "error";
type SourceType = "pdf" | "image" | "url";

const IMAGE_MIME_TYPES = new Set([
  "image/bmp",
  "image/gif",
  "image/jpeg",
  "image/png",
  "image/tiff",
  "image/webp",
]);
const IMAGE_FILE_EXTENSIONS = [".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"];

const surfaces: Array<{ id: Surface; label: string; index: string }> = [
  { id: "knowledge", label: "Knowledge", index: "01" },
  { id: "chat", label: "Chat", index: "02" },
  { id: "memory", label: "Memory", index: "03" },
];

const fileNameCollator = new Intl.Collator(undefined, {
  numeric: true,
  sensitivity: "base",
});

function naturalOrderFiles(files: File[]): File[] {
  return files
    .map((file, index) => ({ file, index }))
    .sort((left, right) => {
      const comparison = fileNameCollator.compare(left.file.name, right.file.name);
      return comparison || left.index - right.index;
    })
    .map(({ file }) => file);
}

function KnowledgeSurface() {
  const [requestState, setRequestState] = useState<RequestState>("idle");
  const [indexResult, setIndexResult] = useState<PDFIndexResponse | ImageIndexResponse | null>(null);
  const [activeSourceType, setActiveSourceType] = useState<SourceType>("pdf");
  const [selectedImageFiles, setSelectedImageFiles] = useState<File[]>([]);
  const [url, setUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sources, setSources] = useState<KnowledgeSource[]>([]);
  const [inventoryState, setInventoryState] = useState<InventoryState>("loading");
  const [inventoryError, setInventoryError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const isSubmitting = useRef(false);

  const refreshInventory = async () => {
    setInventoryState("loading");
    setInventoryError(null);
    try {
      const result = await listKnowledgeSources();
      setSources(result);
      setInventoryState(result.length > 0 ? "success" : "empty");
    } catch (caughtError) {
      setInventoryError(
        caughtError instanceof Error
          ? caughtError.message
          : "Unable to load indexed sources.",
      );
      setInventoryState("error");
    }
  };

  useEffect(() => {
    void refreshInventory();
  }, []);

  const submitImageFiles = async (files: File[]) => {
    if (files.length === 0 || isSubmitting.current) {
      return;
    }
    isSubmitting.current = true;
    setActiveSourceType("image");
    setSelectedImageFiles(files);
    setRequestState("loading");
    setIndexResult(null);
    setError(null);

    try {
      const result = await indexImage(files);
      setIndexResult(result);
      setRequestState("success");
      void refreshInventory();
    } catch (caughtError) {
      setError(
        caughtError instanceof Error
          ? caughtError.message
          : "An unexpected error occurred.",
      );
      setRequestState("error");
    } finally {
      isSubmitting.current = false;
    }
  };

  const moveSelectedImage = (index: number, offset: -1 | 1) => {
    setSelectedImageFiles((currentFiles) => {
      const targetIndex = index + offset;
      if (targetIndex < 0 || targetIndex >= currentFiles.length) {
        return currentFiles;
      }
      const nextFiles = [...currentFiles];
      [nextFiles[index], nextFiles[targetIndex]] = [nextFiles[targetIndex], nextFiles[index]];
      return nextFiles;
    });
  };

  const handleSelectedImageSubmit = async () => {
    await submitImageFiles(selectedImageFiles);
  };

  const handleFileChange = async (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (files.length === 0 || isSubmitting.current) {
      return;
    }

    const isPDF = files.length === 1 && (() => {
      const file = files[0];
      const normalizedFileName = file.name.toLowerCase();
      return file.type === "application/pdf" || normalizedFileName.endsWith(".pdf");
    })();
    const areImages = files.every((file) => {
      const normalizedFileName = file.name.toLowerCase();
      return IMAGE_MIME_TYPES.has(file.type) || IMAGE_FILE_EXTENSIONS.some(
        (extension) => normalizedFileName.endsWith(extension),
      );
    });
    if (files.length > 1 && !areImages) {
      setIndexResult(null);
      setSelectedImageFiles([]);
      setError("Select only image files when uploading multiple sources.");
      setRequestState("error");
      return;
    }
    if (!isPDF && !areImages) {
      setIndexResult(null);
      setSelectedImageFiles([]);
      setError("Choose a PDF or image file.");
      setRequestState("error");
      return;
    }

    const sourceType: SourceType = areImages ? "image" : "pdf";
    const orderedFiles = areImages ? naturalOrderFiles(files) : files;
    setActiveSourceType(sourceType);
    setSelectedImageFiles(sourceType === "image" ? orderedFiles : []);
    setRequestState(sourceType === "image" && orderedFiles.length > 1 ? "idle" : "loading");
    setIndexResult(null);
    setError(null);

    if (sourceType === "image" && orderedFiles.length > 1) {
      return;
    }

    isSubmitting.current = true;
    try {
      const result = sourceType === "image"
        ? await indexImage(orderedFiles)
        : await indexPDF(orderedFiles[0]);
      setIndexResult(result);
      setRequestState("success");
      void refreshInventory();
    } catch (caughtError) {
      setError(
        caughtError instanceof Error
          ? caughtError.message
          : "An unexpected error occurred.",
      );
      setRequestState("error");
    } finally {
      isSubmitting.current = false;
    }
  };

  const handleURLSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const normalizedURL = url.trim();
    if (!normalizedURL || isSubmitting.current) {
      return;
    }

    isSubmitting.current = true;
    setActiveSourceType("url");
    setSelectedImageFiles([]);
    setRequestState("loading");
    setIndexResult(null);
    setError(null);

    try {
      const result = await indexURL(normalizedURL);
      setIndexResult(result);
      setRequestState("success");
      void refreshInventory();
    } catch (caughtError) {
      setError(
        caughtError instanceof Error
          ? caughtError.message
          : "An unexpected error occurred.",
      );
      setRequestState("error");
    } finally {
      isSubmitting.current = false;
    }
  };

  const isLoading = requestState === "loading";
  const imageResults =
    indexResult?.source_type === "image" && "image_results" in indexResult
      ? indexResult.image_results
      : [];
  const isImageBatch = imageResults.length > 1;

  return (
    <section className="surface surface--quiet" aria-labelledby="knowledge-heading">
      <div className="section-kicker">Source desk / current capability</div>
      <h1 id="knowledge-heading">Knowledge</h1>
      <p className="surface-intro">
        The current retrieval baseline is grounded in indexed PDF, image, and URL knowledge.
      </p>

      <div className="capability-card">
        <div className="capability-status">
          <span className="status-light" aria-hidden="true" />
          PDF and image knowledge baseline with URL ingestion
        </div>
        <p>
          Upload a PDF or image, or add a URL, to validate, parse, chunk, embed, and make its
          evidence available to grounded Chat answers.
        </p>
        <p className="source-format-note">
          Accepted files · PDF · PNG · JPG · WEBP · GIF · BMP · TIFF
        </p>
        <div className="source-controls" aria-label="Source ingestion controls">
          <input
            ref={fileInputRef}
            id="source-file"
            className="visually-hidden"
            type="file"
            multiple
            accept="application/pdf,.pdf,image/bmp,image/gif,image/jpeg,image/png,image/tiff,image/webp"
            aria-label="PDF or image file"
            onChange={handleFileChange}
            disabled={isLoading}
          />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={isLoading}
          >
            Upload source
          </button>
          <form className="url-control" onSubmit={handleURLSubmit}>
            <label htmlFor="source-url">URL</label>
            <input
              id="source-url"
              type="url"
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              placeholder="https://example.com/article"
              disabled={isLoading}
            />
            <button type="submit" disabled={isLoading || !url.trim()}>
              Add URL
            </button>
          </form>
        </div>

        <div className="source-result" aria-live="polite">
          {selectedImageFiles.length > 0 && (
            <div className="source-selection" aria-label="Selected image files">
              <strong>
                {selectedImageFiles.length} {selectedImageFiles.length === 1 ? "image" : "images"} selected
              </strong>
              <ol className="source-selection-list">
                {selectedImageFiles.map((file, index) => (
                  <li key={`${file.name}-${index}`}>
                    <span className="source-selection-sequence">
                      {String(index + 1).padStart(2, "0")}
                    </span>
                    <strong>{file.name}</strong>
                    {selectedImageFiles.length > 1 && (
                      <span className="source-selection-actions">
                        <button
                          type="button"
                          aria-label={`Move ${file.name} up`}
                          onClick={() => moveSelectedImage(index, -1)}
                          disabled={isLoading || index === 0}
                        >
                          ↑
                        </button>
                        <button
                          type="button"
                          aria-label={`Move ${file.name} down`}
                          onClick={() => moveSelectedImage(index, 1)}
                          disabled={isLoading || index === selectedImageFiles.length - 1}
                        >
                          ↓
                        </button>
                      </span>
                    )}
                  </li>
                ))}
              </ol>
              {selectedImageFiles.length > 1 && requestState !== "loading" && (
                <button
                  type="button"
                  className="source-selection-submit"
                  onClick={() => void handleSelectedImageSubmit()}
                >
                  Index selected images
                </button>
              )}
            </div>
          )}
          {isLoading && (
            <div className="loading-state" role="status">
              <span className="loading-orbit" aria-hidden="true" />
              <div>
                <strong>
                  {activeSourceType === "url"
                    ? "Fetching and indexing URL"
                    : activeSourceType === "image"
                      ? selectedImageFiles.length > 1
                        ? `Processing ${selectedImageFiles.length} images`
                        : "Processing image"
                      : "Uploading and indexing"}
                </strong>
                <p>
                  {activeSourceType === "url"
                    ? "Validating the URL and preparing searchable evidence."
                    : activeSourceType === "image"
                      ? "Running OCR for each image and preparing searchable evidence."
                      : "Validating the PDF and preparing searchable evidence."}
                </p>
              </div>
            </div>
          )}

          {requestState === "error" && error && (
            <div className="error-state" role="alert">
              <span aria-hidden="true">!</span>
              <div>
                <strong>
                  {activeSourceType === "url"
                    ? "URL ingestion failed"
                    : activeSourceType === "image"
                      ? "Image ingestion failed"
                      : "Upload failed"}
                </strong>
                <p>{error}</p>
              </div>
            </div>
          )}

          {requestState === "success" && indexResult && (
            <div className="source-success" role="status">
              <div>
                <strong>
                  {indexResult.status === "already_indexed" && !isImageBatch
                    ? "Already indexed"
                    : indexResult.source_type === "url"
                      ? "URL indexed"
                      : indexResult.source_type === "image"
                        ? isImageBatch
                          ? indexResult.status === "partial_failed"
                            ? "Image batch completed with errors"
                            : "Image batch processed"
                          : "Image indexed"
                        : "PDF indexed"}
                </strong>
                {indexResult.status === "already_indexed" && !isImageBatch && (
                  <p>
                    This {
                      indexResult.source_type === "url"
                        ? "URL"
                        : indexResult.source_type === "image"
                          ? "image"
                          : "PDF"
                    } is
                    already indexed.
                  </p>
                )}
                <p>{indexResult.source_display_name}</p>
                {indexResult.source_type === "url" &&
                  "final_url" in indexResult &&
                  indexResult.final_url && (
                  <p>{indexResult.final_url}</p>
                )}
              </div>
              <div className="source-success-meta">
                <span>source kind · {indexResult.source_type}</span>
                <span>status · {indexResult.index_status ?? indexResult.status}</span>
                {isImageBatch ? (
                  <span>
                    {imageResults.length} images · {indexResult.indexed_chunk_count} chunks · {indexResult.embedded_chunk_count} embedded
                  </span>
                ) : (
                  <span>
                    {indexResult.indexed_chunk_count} chunks · {indexResult.embedded_chunk_count} embedded
                  </span>
                )}
              </div>
              {isImageBatch && (
                <ul className="image-batch-results" aria-label="Image processing results">
                  {imageResults.map((item) => (
                    <li key={`${item.original_filename}-${item.workflow_run_id ?? item.file_name}`}>
                      <strong>{item.original_filename}</strong>
                      <span>
                        {item.status === "already_indexed"
                          ? "already indexed"
                          : item.status === "failed"
                            ? "error"
                            : "indexed"}
                      </span>
                      {item.source_display_name && <span>{item.source_display_name}</span>}
                      {item.message && <span>{item.message}</span>}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      </div>

      <section className="source-inventory" aria-labelledby="indexed-sources-heading">
        <div className="section-kicker">Source inventory / indexed only</div>
        <h2 id="indexed-sources-heading">Indexed Sources</h2>
        <div aria-live="polite">
          {inventoryState === "loading" && (
            <div className="loading-state" role="status">
              <span className="loading-orbit" aria-hidden="true" />
              <div>
                <strong>Loading indexed sources</strong>
                <p>Checking the current source inventory.</p>
              </div>
            </div>
          )}

          {inventoryState === "error" && inventoryError && (
            <div className="error-state" role="alert">
              <span aria-hidden="true">!</span>
              <div>
                <strong>Source inventory unavailable</strong>
                <p>{inventoryError}</p>
              </div>
            </div>
          )}

          {inventoryState === "empty" && (
            <p className="inventory-empty">No indexed sources yet.</p>
          )}

          {inventoryState === "success" && (
            <ul className="source-inventory-list">
              {sources.map((source) => (
                <li key={source.id} className="source-inventory-item">
                  <div>
                    <strong>{source.display_name}</strong>
                    {source.source_preview && (
                      <p className="source-inventory-preview">{source.source_preview}</p>
                    )}
                    <div className="source-inventory-meta">
                      <span>{source.source_kind}</span>
                      <span>{source.status}</span>
                      {source.image_count && <span>{source.image_count} images</span>}
                      <span>{source.chunk_count} chunks</span>
                      {source.source_url && <span>{source.source_url}</span>}
                      {source.updated_at && <span>updated · {source.updated_at}</span>}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>
    </section>
  );
}

function CitationList({ response }: { response: QAResponse }) {
  if (response.citations.length === 0) {
    return null;
  }

  return (
    <section className="citations" aria-labelledby="citations-heading">
      <div className="answer-label-row">
        <h2 id="citations-heading">Citations</h2>
        <span>{response.citations.length.toString().padStart(2, "0")}</span>
      </div>
      <ol>
        {response.citations.map((citation, index) => (
          <li
            key={`${citation.source_kind ?? "notion"}-${citation.source_display_name ?? citation.notion_path ?? index}-${citation.locator ?? citation.page_id ?? index}`}
          >
            <span className="citation-index">{String(index + 1).padStart(2, "0")}</span>
            <div className="citation-copy">
              <strong>{citation.source_display_name ?? citation.notion_path ?? "Unknown source"}</strong>
              <div className="citation-meta">
                <span>{citation.source_kind ?? "notion"}</span>
                <span>{(citation.score * 100).toFixed(1)}% match</span>
                {citation.locator && <span>{citation.locator}</span>}
                {citation.original_filename && <span>{citation.original_filename}</span>}
                {citation.source_url && (
                  <a href={citation.source_url} target="_blank" rel="noreferrer">
                    {citation.source_url}
                  </a>
                )}
                {citation.page_id && <code>{citation.page_id}</code>}
              </div>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

function ChatSurface() {
  const [question, setQuestion] = useState("");
  const [requestState, setRequestState] = useState<RequestState>("idle");
  const [response, setResponse] = useState<QAResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const isComposing = useRef(false);
  const isSubmitting = useRef(false);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const query = question.trim();
    if (!query || isSubmitting.current) {
      return;
    }

    isSubmitting.current = true;
    setRequestState("loading");
    setResponse(null);
    setError(null);

    try {
      const result = await askQuestion(query);
      setResponse(result);
      setRequestState("success");
    } catch (caughtError) {
      setError(
        caughtError instanceof Error
          ? caughtError.message
          : "An unexpected error occurred.",
      );
      setRequestState("error");
    } finally {
      isSubmitting.current = false;
    }
  };

  const handleQuestionKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (
      event.key !== "Enter" ||
      event.shiftKey ||
      event.nativeEvent.isComposing ||
      isComposing.current
    ) {
      return;
    }

    event.preventDefault();
    event.currentTarget.form?.requestSubmit();
  };

  const isLoading = requestState === "loading";

  return (
    <section className="surface surface--chat" aria-labelledby="chat-heading">
      <header className="chat-header">
        <div>
          <div className="section-kicker">Knowledge-grounded QA / single turn</div>
          <h1 id="chat-heading">Ask what your notes know.</h1>
        </div>
        <div className="runtime-badge"><span aria-hidden="true" />Baseline live</div>
      </header>

      <form className="ask-form" onSubmit={handleSubmit}>
        <label htmlFor="question">Question</label>
        <textarea
          id="question"
          name="question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={handleQuestionKeyDown}
          onCompositionStart={() => {
            isComposing.current = true;
          }}
          onCompositionEnd={() => {
            isComposing.current = false;
          }}
          placeholder="Ask about evidence already indexed in Knowledge…"
          rows={4}
          disabled={isLoading}
        />
        <div className="form-footer">
          <p>One question · One grounded response · No conversation memory</p>
          <button type="submit" disabled={isLoading || !question.trim()}>
            <span>Ask Knowvia</span>
            <span aria-hidden="true">↗</span>
          </button>
        </div>
      </form>

      <div className="result-region" aria-live="polite">
        {requestState === "idle" && (
          <div className="idle-state">
            <span className="idle-mark" aria-hidden="true">K</span>
            <p>Your answer will appear here with backend-provided evidence.</p>
          </div>
        )}

        {isLoading && (
          <div className="loading-state" role="status">
            <span className="loading-orbit" aria-hidden="true" />
            <div>
              <strong>Searching knowledge</strong>
              <p>Checking indexed evidence before answering.</p>
            </div>
          </div>
        )}

        {requestState === "error" && error && (
          <div className="error-state" role="alert">
            <span aria-hidden="true">!</span>
            <div>
              <strong>Request failed</strong>
              <p>{error}</p>
            </div>
          </div>
        )}

        {requestState === "success" && response && (
          <article className={response.insufficient_info ? "answer answer--insufficient" : "answer"}>
            <div className="answer-label-row">
              <h2>{response.insufficient_info ? "Insufficient info" : "Answer"}</h2>
              <span>Run {response.workflow_run_id}</span>
            </div>
            <p className="answer-copy">{response.answer}</p>
            {response.insufficient_info && (
              <p className="insufficient-note">
                The backend did not find enough enterprise evidence to answer safely.
              </p>
            )}
            {!response.insufficient_info && <CitationList response={response} />}
          </article>
        )}
      </div>
    </section>
  );
}

function MemorySurface() {
  return (
    <section className="surface surface--quiet" aria-labelledby="memory-heading">
      <div className="section-kicker">Reserved surface / phase 4.0</div>
      <h1 id="memory-heading">Memory</h1>
      <div className="memory-placeholder">
        <span aria-hidden="true">04</span>
        <div>
          <p>Persistent memory will be available in a later phase.</p>
          <small>No records are saved, searched, or simulated in this interface.</small>
        </div>
      </div>
    </section>
  );
}

export default function App() {
  const [activeSurface, setActiveSurface] = useState<Surface>("chat");

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true">K</div>
          <div>
            <strong>Knowvia Agent</strong>
            <span>Knowledge workspace</span>
          </div>
        </div>

        <nav aria-label="Primary surfaces">
          {surfaces.map((surface) => (
            <button
              key={surface.id}
              type="button"
              className={activeSurface === surface.id ? "nav-item nav-item--active" : "nav-item"}
              onClick={() => setActiveSurface(surface.id)}
              aria-label={surface.label}
              aria-current={activeSurface === surface.id ? "page" : undefined}
            >
              <span>{surface.index}</span>
              {surface.label}
            </button>
          ))}
        </nav>

        <div className="sidebar-foot">
          <span>Thin harness</span>
          <span>Phase 1.0</span>
        </div>
      </aside>

      <main>
        <div className="top-rule">
          <span>Enterprise knowledge, traceable by design</span>
          <span>Local / QA baseline</span>
        </div>
        {activeSurface === "knowledge" && <KnowledgeSurface />}
        {activeSurface === "chat" && <ChatSurface />}
        {activeSurface === "memory" && <MemorySurface />}
      </main>
    </div>
  );
}
