import { ChangeEvent, FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";

import {
  createConversation,
  getConversation,
  indexImage,
  indexPDF,
  indexURL,
  listMemories,
  deleteMemory,
  listConversations,
  listKnowledgeSources,
  streamConversationMessage,
  type ConversationMessage,
  type ConversationSession,
  type ConversationSessionSummary,
  type ConversationStreamEvent,
  type ImageIndexResponse,
  type KnowledgeSource,
  type SavedMemory,
  type PDFIndexResponse,
  type QACitation,
  type QAResponse,
} from "./api";

type Surface = "knowledge" | "chat" | "memory";
type RequestState = "idle" | "loading" | "success" | "error";
type ChatStreamState = "idle" | "connecting" | "running" | "completed" | "error";
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

const executionStatusCopy: Record<string, string> = {
  searching_knowledge: "Searching knowledge…",
  searching_memory: "Searching saved memory…",
  saving_memory: "Saving memory…",
  generating: "Generating answer…",
};

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

function CitationList({ citations }: { citations: QACitation[] }) {
  if (citations.length === 0) {
    return null;
  }

  return (
    <details className="citations">
      <summary className="citation-summary">Sources · {citations.length}</summary>
      <ol>
        {citations.map((citation, index) => (
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
    </details>
  );
}

type ConversationListState = "loading" | "ready" | "error";

function conversationSummary(session: ConversationSession): ConversationSessionSummary {
  return {
    id: session.id,
    title: session.title,
    status: session.status,
    created_at: session.created_at,
    updated_at: session.updated_at,
  };
}

function readURLSessionId(): { present: boolean; id: number | null } {
  const value = new URLSearchParams(window.location.search).get("session_id");
  if (value === null) {
    return { present: false, id: null };
  }
  const id = Number(value);
  return {
    present: true,
    id: Number.isSafeInteger(id) && id > 0 ? id : null,
  };
}

function replaceURLSessionId(sessionId: number) {
  const url = new URL(window.location.href);
  url.pathname = "/chat";
  url.searchParams.set("session_id", String(sessionId));
  window.history.replaceState({}, "", `${url.pathname}?${url.searchParams.toString()}`);
}

function ConversationSidebar({
  sessions,
  state,
  error,
  activeSessionId,
  newChatDisabled,
  sessionSelectionDisabled,
  mobileOpen,
  onNewChat,
  onRetry,
  onSelect,
  onClose,
}: {
  sessions: ConversationSessionSummary[];
  state: ConversationListState;
  error: string | null;
  activeSessionId: number | null;
  newChatDisabled: boolean;
  sessionSelectionDisabled: boolean;
  mobileOpen: boolean;
  onNewChat: () => void;
  onRetry: () => void;
  onSelect: (sessionId: number) => void;
  onClose: () => void;
}) {
  return (
    <>
      <aside
        className={mobileOpen ? "conversation-panel conversation-panel--open" : "conversation-panel"}
        aria-label="Conversation sessions"
      >
        <div className="conversation-panel-header">
          <div>
            <div className="conversation-panel-kicker">Short-term context</div>
            <h2>Conversations</h2>
          </div>
          <button
            type="button"
            className="conversation-close"
            aria-label="Close conversations"
            onClick={onClose}
          >
            ×
          </button>
        </div>
        <button
          type="button"
          className="new-chat-button"
          onClick={onNewChat}
          disabled={newChatDisabled}
        >
          <span>New Chat</span>
          <span aria-hidden="true">+</span>
        </button>
        <div className="conversation-list" aria-live="polite">
          {state === "loading" && (
            <div className="conversation-skeleton" role="status">
              <span />
              <span />
              <span />
              <strong>Loading conversations</strong>
            </div>
          )}
          {state === "error" && error && (
            <div className="conversation-inline-error" role="alert">
              <strong>Conversations unavailable</strong>
              <p>{error}</p>
              <button type="button" onClick={onRetry} disabled={newChatDisabled && state !== "error"}>
                Retry
              </button>
            </div>
          )}
          {state === "ready" && sessions.length === 0 && (
            <p className="conversation-empty">No conversations yet.</p>
          )}
          {state === "ready" && sessions.length > 0 && (
            <ol className="conversation-list-items">
              {sessions.map((session) => (
                <li key={session.id}>
                  <button
                    type="button"
                    className={activeSessionId === session.id ? "conversation-item conversation-item--active" : "conversation-item"}
                    onClick={() => onSelect(session.id)}
                    disabled={sessionSelectionDisabled || activeSessionId === session.id}
                    aria-current={activeSessionId === session.id ? "true" : undefined}
                  >
                    <strong>{session.title}</strong>
                    <span>updated · {session.updated_at}</span>
                  </button>
                </li>
              ))}
            </ol>
          )}
        </div>
      </aside>
      {mobileOpen && (
        <button
          type="button"
          className="drawer-backdrop"
          aria-label="Close conversations"
          onClick={onClose}
        />
      )}
    </>
  );
}

function ConversationMessages({ messages }: { messages: ConversationMessage[] }) {
  return (
    <ol className="conversation-messages" aria-label="Conversation messages">
      {messages.map((message) => (
        <li key={message.id} className={`conversation-message conversation-message--${message.role}`}>
          <span className="conversation-message-role">{message.role === "user" ? "You" : "Knowvia"}</span>
          <p>{message.content}</p>
          {message.role === "assistant" && (
            <>
              {message.used_saved_memory && (
                <div className="saved-memory-indicator">Used saved memory</div>
              )}
              <CitationList citations={message.citations ?? []} />
            </>
          )}
        </li>
      ))}
    </ol>
  );
}

function streamPayloadString(payload: Record<string, unknown>, key: string): string | null {
  return typeof payload[key] === "string" ? payload[key] as string : null;
}

function streamPayloadCitations(payload: Record<string, unknown>): QACitation[] {
  return Array.isArray(payload.citations) ? payload.citations as QACitation[] : [];
}

function StreamingTurn({
  query,
  answer,
  phase,
  citations,
  failed,
}: {
  query: string;
  answer: string;
  phase: string | null;
  citations: QACitation[];
  failed: boolean;
}) {
  return (
    <ol className="conversation-messages conversation-messages--streaming" aria-label="Streaming response">
      <li className="conversation-message conversation-message--user">
        <span className="conversation-message-role">You</span>
        <p>{query}</p>
      </li>
      <li className={failed
        ? "conversation-message conversation-message--assistant conversation-message--incomplete"
        : "conversation-message conversation-message--assistant"}
      >
        <span className="conversation-message-role">Knowvia</span>
        {phase && !answer && (
          <div className="stream-status" role="status">{executionStatusCopy[phase] ?? "Working…"}</div>
        )}
        {phase && answer && (
          <div className="stream-status stream-status--compact" role="status">
            {executionStatusCopy[phase] ?? "Working…"}
          </div>
        )}
        {answer && <p>{answer}</p>}
        {!failed && citations.length > 0 && <CitationList citations={citations} />}
        {failed && (
          <div className="stream-incomplete" role="status">
            This response was not completed or saved.
          </div>
        )}
      </li>
    </ol>
  );
}

function ChatSurface() {
  const [draftsBySessionId, setDraftsBySessionId] = useState<Record<number, string>>({});
  const [streamState, setStreamState] = useState<ChatStreamState>("idle");
  const [response, setResponse] = useState<QAResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [streamQuery, setStreamQuery] = useState("");
  const [partialAssistantText, setPartialAssistantText] = useState("");
  const [executionPhase, setExecutionPhase] = useState<string | null>(null);
  const [streamCitations, setStreamCitations] = useState<QACitation[]>([]);
  const [streamSessionId, setStreamSessionId] = useState<number | null>(null);
  const [sessions, setSessions] = useState<ConversationSessionSummary[]>([]);
  const [conversationListState, setConversationListState] = useState<ConversationListState>("loading");
  const [conversationListError, setConversationListError] = useState<string | null>(null);
  const [activeSessionId, setActiveSessionId] = useState<number | null>(null);
  const [activeSessionTitle, setActiveSessionTitle] = useState("New conversation");
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [sessionLoading, setSessionLoading] = useState(true);
  const [creatingSession, setCreatingSession] = useState(false);
  const [createRetryAvailable, setCreateRetryAvailable] = useState(false);
  const [mobileDrawerOpen, setMobileDrawerOpen] = useState(false);
  const isComposing = useRef(false);
  const isSubmitting = useRef(false);
  const activeSessionIdRef = useRef<number | null>(null);
  const activeStreamRef = useRef<{
    token: symbol;
    sessionId: number;
    controller: AbortController;
  } | null>(null);

  const question = activeSessionId === null ? "" : draftsBySessionId[activeSessionId] ?? "";
  const setQuestion = (value: string) => {
    if (activeSessionId === null) {
      return;
    }
    setDraftsBySessionId((current) => ({
      ...current,
      [activeSessionId]: value,
    }));
  };

  const showError = (caughtError: unknown, fallback: string) =>
    caughtError instanceof Error ? caughtError.message : fallback;

  const resetStreamState = () => {
    setStreamState("idle");
    setStreamQuery("");
    setPartialAssistantText("");
    setExecutionPhase(null);
    setStreamCitations([]);
    setStreamSessionId(null);
  };

  const abortActiveStream = () => {
    const activeStream = activeStreamRef.current;
    if (!activeStream) {
      return;
    }
    activeStream.controller.abort();
    activeStreamRef.current = null;
    isSubmitting.current = false;
  };

  const loadConversations = async () => {
    setConversationListState("loading");
    setConversationListError(null);
    setSessionLoading(true);
    setError(null);
    setCreateRetryAvailable(false);
    const requestedSession = readURLSessionId();

    try {
      const listed = await listConversations();
      setSessions(listed);
      let target = !requestedSession.present
        ? listed[0]
        : requestedSession.id === null
          ? undefined
          : listed.find((session) => session.id === requestedSession.id);

      if (requestedSession.present && !target) {
        setError("Conversation is unavailable.");
        if (listed.length > 0) {
          target = listed[0];
        }
      }

      if (!target) {
        const created = await createConversation();
        const summary = conversationSummary(created);
        setSessions([summary]);
        activeSessionIdRef.current = created.id;
        setActiveSessionId(created.id);
        setActiveSessionTitle(created.title);
        setMessages(created.messages);
        setConversationListState("ready");
        setSessionLoading(false);
        replaceURLSessionId(created.id);
        return;
      }

      const loaded = await getConversation(target.id);
      activeSessionIdRef.current = loaded.id;
      setActiveSessionId(loaded.id);
      setActiveSessionTitle(loaded.title);
      setMessages(loaded.messages);
      setConversationListState("ready");
      setSessionLoading(false);
      replaceURLSessionId(loaded.id);
    } catch (caughtError) {
      const message = showError(caughtError, "Unable to load conversations.");
      setConversationListError(message);
      setConversationListState("error");
      setSessionLoading(true);
      setError(message);
    }
  };

  useEffect(() => {
    void loadConversations();
  }, []);

  const handleNewChat = async () => {
    if (creatingSession || sessionLoading) {
      return;
    }
    abortActiveStream();
    resetStreamState();
    setCreatingSession(true);
    setError(null);
    setCreateRetryAvailable(false);
    try {
      const created = await createConversation();
      const summary = conversationSummary(created);
      setSessions((current) => [summary, ...current.filter((session) => session.id !== created.id)]);
      activeSessionIdRef.current = created.id;
      setActiveSessionId(created.id);
      setActiveSessionTitle(created.title);
      setMessages(created.messages);
      setResponse(null);
      setStreamState("idle");
      setDraftsBySessionId((current) => ({ ...current, [created.id]: "" }));
      setSessionLoading(false);
      replaceURLSessionId(created.id);
      setMobileDrawerOpen(false);
    } catch (caughtError) {
      setError(showError(caughtError, "Unable to create a new conversation."));
      setCreateRetryAvailable(true);
    } finally {
      setCreatingSession(false);
    }
  };

  const handleSessionSelect = async (sessionId: number) => {
    if (
      sessionId === activeSessionId ||
      sessionLoading ||
      creatingSession
    ) {
      return;
    }
    abortActiveStream();
    resetStreamState();
    setSessionLoading(true);
    setError(null);
    setCreateRetryAvailable(false);
    setResponse(null);
    setStreamState("idle");
    try {
      const loaded = await getConversation(sessionId);
      activeSessionIdRef.current = loaded.id;
      setActiveSessionId(loaded.id);
      setActiveSessionTitle(loaded.title);
      setMessages(loaded.messages);
      setSessionLoading(false);
      replaceURLSessionId(loaded.id);
      setMobileDrawerOpen(false);
    } catch (caughtError) {
      setSessionLoading(false);
      setError(showError(caughtError, "Conversation is unavailable."));
    }
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const query = question.trim();
    if (!query || !activeSessionId || sessionLoading || creatingSession || isSubmitting.current) {
      return;
    }

    const sessionId = activeSessionId;
    const token = Symbol("conversation-stream");
    const controller = new AbortController();
    activeStreamRef.current = { token, sessionId, controller };
    isSubmitting.current = true;
    setStreamState("connecting");
    setStreamSessionId(sessionId);
    setStreamQuery(query);
    setPartialAssistantText("");
    setExecutionPhase(null);
    setStreamCitations([]);
    setResponse(null);
    setError(null);
    setCreateRetryAvailable(false);

    let answerText = "";
    let citations: QACitation[] = [];
    let donePayload: Record<string, unknown> | null = null;
    let streamError: string | null = null;
    const isCurrentStream = () => {
      const activeStream = activeStreamRef.current;
      return activeStream?.token === token && activeSessionIdRef.current === sessionId;
    };

    try {
      await streamConversationMessage(
        sessionId,
        query,
        (streamEvent: ConversationStreamEvent) => {
          if (!isCurrentStream()) {
            return;
          }
          if (streamEvent.event_type === "execution_status") {
            setExecutionPhase(streamPayloadString(streamEvent.payload, "phase"));
            setStreamState("running");
            return;
          }
          if (streamEvent.event_type === "answer_delta") {
            const delta = streamPayloadString(streamEvent.payload, "text") ?? "";
            answerText += delta;
            setPartialAssistantText(answerText);
            setStreamState("running");
            return;
          }
          if (streamEvent.event_type === "citations") {
            citations = streamPayloadCitations(streamEvent.payload);
            setStreamCitations(citations);
            return;
          }
          if (streamEvent.event_type === "error") {
            streamError = streamPayloadString(streamEvent.payload, "message") ?? "The request failed.";
            setError(streamError);
            setStreamState("error");
            return;
          }
          donePayload = streamEvent.payload;
        },
        fetch,
        controller.signal,
      );
      if (!isCurrentStream()) {
        return;
      }
      if (streamError) {
        return;
      }
      if (!donePayload) {
        throw new Error("Knowvia ended the stream before completion.");
      }

      const completedPayload = donePayload as Record<string, unknown>;
      const title = streamPayloadString(completedPayload, "title") ?? activeSessionTitle;
      const updatedAt = streamPayloadString(completedPayload, "updated_at") ?? new Date().toISOString();
      const insufficientInfo = completedPayload.insufficient_info === true;
      const usedSavedMemory = completedPayload.used_saved_memory === true;
      const memoryStatus = completedPayload.memory_saved === true
        ? "saved"
        : completedPayload.memory_already_saved === true
          ? "already_saved"
          : null;
      const workflowRunId = typeof completedPayload.workflow_run_id === "number"
        ? completedPayload.workflow_run_id
        : 0;
      const messageId = typeof completedPayload.message_id === "number"
        ? completedPayload.message_id
        : -(Date.now() + 1);
      const sequenceStart = messages[messages.length - 1]?.sequence_number ?? 0;
      const optimisticMessages: ConversationMessage[] = [
        ...messages,
        {
          id: -(Date.now()),
          session_id: sessionId,
          role: "user",
          content: query,
          sequence_number: sequenceStart + 1,
          created_at: updatedAt,
          citations: [],
          used_saved_memory: false,
        },
        {
          id: messageId,
          session_id: sessionId,
          role: "assistant",
          content: answerText,
          sequence_number: sequenceStart + 2,
          created_at: updatedAt,
          citations,
          used_saved_memory: usedSavedMemory,
        },
      ];
      setMessages(optimisticMessages);
      setActiveSessionTitle(title);
      setSessions((current) => {
        const existing = current.find((session) => session.id === sessionId);
        const updated: ConversationSessionSummary = {
          id: sessionId,
          title,
          status: existing?.status ?? "active",
          created_at: existing?.created_at ?? updatedAt,
          updated_at: updatedAt,
        };
        return [updated, ...current.filter((session) => session.id !== sessionId)];
      });
      setDraftsBySessionId((current) => ({ ...current, [sessionId]: "" }));
      setResponse({
        workflow_run_id: workflowRunId,
        status: "succeeded",
        answer: answerText,
        insufficient_info: insufficientInfo,
        retrieved_chunk_count: typeof completedPayload.retrieved_chunk_count === "number"
          ? completedPayload.retrieved_chunk_count
          : 0,
        citations,
        provider: typeof completedPayload.provider === "string" ? completedPayload.provider : null,
        model: typeof completedPayload.model === "string" ? completedPayload.model : null,
        memory_status: memoryStatus,
        used_saved_memory: usedSavedMemory,
      });
      setExecutionPhase(null);
      setStreamState("completed");
    } catch (caughtError) {
      if (!isCurrentStream() || (caughtError instanceof DOMException && caughtError.name === "AbortError")) {
        return;
      }
      setError(
        caughtError instanceof Error
          ? caughtError.message
          : "An unexpected error occurred.",
      );
      setStreamState("error");
    } finally {
      if (activeStreamRef.current?.token === token) {
        activeStreamRef.current = null;
        isSubmitting.current = false;
      }
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

  const streamActive = streamState === "connecting" || streamState === "running";
  const controlsDisabled = streamActive || sessionLoading || creatingSession || conversationListState === "loading";
  const sidebarBusy = sessionLoading || creatingSession || conversationListState === "loading";

  return (
    <div className="chat-layout">
      <ConversationSidebar
        sessions={sessions}
        state={conversationListState}
        error={conversationListError}
        activeSessionId={activeSessionId}
        newChatDisabled={sidebarBusy}
        sessionSelectionDisabled={sidebarBusy}
        mobileOpen={mobileDrawerOpen}
        onNewChat={() => void handleNewChat()}
        onRetry={() => void loadConversations()}
        onSelect={(sessionId) => void handleSessionSelect(sessionId)}
        onClose={() => setMobileDrawerOpen(false)}
      />
      <section className="surface surface--chat" aria-labelledby="chat-heading">
        <header className="chat-header">
          <div>
            <div className="section-kicker">Knowledge-grounded QA / conversation session</div>
            <h1 id="chat-heading">Ask what your notes know.</h1>
            <p className="current-session-label">Current conversation · {activeSessionTitle}</p>
          </div>
          <div className="chat-header-actions">
            <button
              type="button"
              className="chat-menu-button"
              aria-label="Open conversations"
              onClick={() => setMobileDrawerOpen(true)}
              disabled={sidebarBusy}
            >
              Conversations
            </button>
            <div className="runtime-badge"><span aria-hidden="true" />Baseline live</div>
          </div>
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
            disabled={controlsDisabled}
          />
          <div className="form-footer">
            <p>Same-session context · Recent history bounded · Grounded evidence</p>
            <button type="submit" disabled={controlsDisabled || !question.trim()}>
              <span>Ask Knowvia</span>
              <span aria-hidden="true">↗</span>
            </button>
          </div>
        </form>

        <div className="result-region" aria-live="polite">
          {sessionLoading && (
            <div className="loading-state" role="status">
              <span className="loading-orbit" aria-hidden="true" />
              <div>
                <strong>Loading conversation</strong>
                <p>Restoring backend session history.</p>
              </div>
            </div>
          )}

          {!sessionLoading && error && streamState !== "error" && (
            <div className="error-state" role="alert">
              <span aria-hidden="true">!</span>
              <div>
                <strong>Conversation unavailable</strong>
                <p>{error}</p>
                {createRetryAvailable && (
                  <button type="button" onClick={() => void handleNewChat()}>
                    Retry
                  </button>
                )}
              </div>
            </div>
          )}

          {!sessionLoading && activeSessionId && messages.length === 0 && streamState === "idle" && (
            <div className="idle-state">
              <span className="idle-mark" aria-hidden="true">K</span>
              <p>Ask about your indexed knowledge.</p>
            </div>
          )}

          {!sessionLoading && messages.length > 0 && (
            <ConversationMessages messages={messages} />
          )}

          {!sessionLoading &&
            streamSessionId === activeSessionId &&
            streamQuery &&
            (streamActive || streamState === "error") && (
            <StreamingTurn
              query={streamQuery}
              answer={partialAssistantText}
              phase={executionPhase}
              citations={streamCitations}
              failed={streamState === "error"}
            />
          )}

          {streamState === "error" && error && (
            <div className="error-state" role="alert">
              <span aria-hidden="true">!</span>
              <div>
                <strong>Request failed</strong>
                <p>{error}</p>
              </div>
            </div>
          )}

          {streamState === "completed" && response && (
            <article className={response.insufficient_info ? "answer answer--insufficient" : "answer"}>
              <div className="answer-label-row">
                <h2>{response.insufficient_info ? "Insufficient info" : "Answer"}</h2>
                <span>Run {response.workflow_run_id}</span>
              </div>
              {response.memory_status && (
                <p className="memory-operation-status" role="status">
                  {response.memory_status === "saved" ? "Memory saved" : "Already saved"}
                </p>
              )}
              {response.insufficient_info && (
                <p className="insufficient-note">
                  The backend did not find enough enterprise evidence to answer safely.
                </p>
              )}
            </article>
          )}
        </div>
      </section>
    </div>
  );
}

function MemorySurface() {
  const [state, setState] = useState<"loading" | "success" | "empty" | "error">("loading");
  const [memories, setMemories] = useState<SavedMemory[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  const refresh = async () => {
    setState("loading");
    setError(null);
    try {
      const result = await listMemories();
      setMemories(result);
      setState(result.length > 0 ? "success" : "empty");
    } catch (caughtError) {
      setError(caughtError instanceof Error ? caughtError.message : "Unable to load saved memory.");
      setState("error");
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const handleDelete = async (memoryId: number) => {
    if (deletingId !== null) {
      return;
    }
    setDeletingId(memoryId);
    setError(null);
    try {
      await deleteMemory(memoryId);
      setMemories((current) => current.filter((memory) => memory.id !== memoryId));
      setState((current) => (memories.length <= 1 ? "empty" : current));
    } catch (caughtError) {
      setError(caughtError instanceof Error ? caughtError.message : "Unable to delete saved memory.");
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <section className="surface surface--quiet" aria-labelledby="memory-heading">
      <div className="section-kicker">Persistent context / phase 4.0</div>
      <h1 id="memory-heading">Memory</h1>
      <div className="memory-inspector" aria-live="polite">
        {state === "loading" && <div className="loading-state" role="status"><strong>Loading saved memory</strong></div>}
        {state === "error" && (
          <div className="error-state" role="alert">
            <div>
              <strong>Memory unavailable</strong>
              <p>{error}</p>
              <button type="button" onClick={() => void refresh()}>Retry</button>
            </div>
          </div>
        )}
        {state === "empty" && (
          <div className="memory-empty">
            <strong>No saved memory</strong>
            <p>Explicitly saved decisions, preferences, and project context will appear here.</p>
          </div>
        )}
        {state === "success" && (
          <ol className="memory-list">
            {memories.map((memory) => (
              <li key={memory.id} className="memory-item">
                <p>{memory.content}</p>
                <div className="memory-meta">
                  <span>{memory.memory_type}</span>
                  <time dateTime={memory.created_at}>{memory.created_at}</time>
                  <button
                    type="button"
                    onClick={() => void handleDelete(memory.id)}
                    disabled={deletingId !== null}
                  >
                    {deletingId === memory.id ? "Deleting…" : "Delete"}
                  </button>
                </div>
              </li>
            ))}
          </ol>
        )}
        {error && state !== "error" && <p className="memory-inline-error" role="alert">{error}</p>}
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
          <span>Phase 4.0</span>
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
