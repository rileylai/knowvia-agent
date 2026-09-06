export type QACitation = {
  notion_path?: string | null;
  page_id?: string | null;
  score: number;
  source_kind?: string;
  source_display_name?: string | null;
  locator?: string | null;
  source_url?: string | null;
  image_index?: number | null;
  sequence_index?: number | null;
  original_filename?: string | null;
};

export type QAResponse = {
  workflow_run_id: number;
  status: string;
  answer: string;
  insufficient_info: boolean;
  retrieved_chunk_count: number;
  citations: QACitation[];
  provider: string | null;
  model: string | null;
  memory_status?: "saved" | "already_saved" | null;
  used_saved_memory: boolean;
};

export type ConversationSessionSummary = {
  id: number;
  title: string;
  status: string;
  created_at: string;
  updated_at: string;
};

export type ConversationMessage = {
  id: number;
  session_id: number;
  role: "user" | "assistant" | string;
  content: string;
  sequence_number: number;
  created_at: string;
  citations: QACitation[];
  used_saved_memory: boolean;
};

export type ConversationSession = ConversationSessionSummary & {
  messages: ConversationMessage[];
};

export type ConversationTurn = QAResponse & {
  session_id: number;
  title: string;
  updated_at: string;
  messages: ConversationMessage[];
};

export type ConversationStreamEventType =
  | "execution_status"
  | "answer_delta"
  | "citations"
  | "error"
  | "done";

export type ConversationStreamPayload = Record<string, unknown>;

export type ConversationStreamEvent = {
  event_type: ConversationStreamEventType;
  run_id: string;
  sequence: number;
  payload: ConversationStreamPayload;
};

export type ConversationStreamEventHandler = (
  event: ConversationStreamEvent,
) => unknown;

const conversationStreamEventTypes = new Set<ConversationStreamEventType>([
  "execution_status",
  "answer_delta",
  "citations",
  "error",
  "done",
]);

export type MemoryType = "decision" | "preference" | "project_context";

export type SavedMemory = {
  id: number;
  memory_type: MemoryType;
  content: string;
  status: string;
  created_at: string;
};

export type PDFIndexResponse = {
  workflow_run_id: number;
  status: string;
  source_document_id: number;
  source_type: string;
  source_display_name: string;
  content_hash: string;
  index_status: string | null;
  indexed_chunk_count: number;
  embedded_chunk_count: number;
  requested_url?: string | null;
  final_url?: string | null;
};

export type ImageIndexItem = {
  sequence_index: number;
  file_name: string;
  original_filename: string;
  workflow_run_id?: number | null;
  status: string;
  source_document_id?: number | null;
  source_type: string;
  source_display_name?: string | null;
  content_hash?: string | null;
  file_hash?: string | null;
  width?: number | null;
  height?: number | null;
  index_status?: string | null;
  indexed_chunk_count: number;
  embedded_chunk_count: number;
  error_code?: string | null;
  message?: string | null;
  failure_reason?: string | null;
};

export type ImageIndexResponse = {
  workflow_run_id?: number | null;
  workflow_run_ids: number[];
  status: string;
  source_document_id?: number | null;
  source_type: string;
  source_display_name: string;
  source_preview?: string | null;
  image_count: number;
  content_hash?: string | null;
  index_status?: string | null;
  indexed_chunk_count: number;
  embedded_chunk_count: number;
  image_results: ImageIndexItem[];
};
export type URLIndexResponse = PDFIndexResponse;

export type KnowledgeSource = {
  id: number;
  display_name: string;
  original_filename?: string | null;
  source_preview?: string | null;
  image_count?: number | null;
  source_kind: string;
  status: string;
  chunk_count: number;
  updated_at?: string | null;
  source_url?: string | null;
};

type ErrorDetail = {
  message?: unknown;
};

function errorMessage(payload: unknown, status: number): string {
  if (typeof payload === "object" && payload !== null && "detail" in payload) {
    const detail = payload.detail;
    if (typeof detail === "string" && detail.trim()) {
      return detail;
    }
    if (
      typeof detail === "object" &&
      detail !== null &&
      typeof (detail as ErrorDetail).message === "string"
    ) {
      return (detail as ErrorDetail).message as string;
    }
  }
  return `Knowvia returned an error (${status}).`;
}

function normalizeCitations(value: unknown): QACitation[] {
  return Array.isArray(value) ? (value as QACitation[]) : [];
}

function normalizeConversationMessages(
  value: unknown,
  fallbackCitations: QACitation[] = [],
): ConversationMessage[] {
  if (!Array.isArray(value)) {
    return [];
  }

  const messages = value.filter(
    (message): message is Record<string, unknown> =>
      typeof message === "object" && message !== null,
  );
  const lastAssistantIndex = messages.reduce(
    (lastIndex, message, index) =>
      message.role === "assistant" ? index : lastIndex,
    -1,
  );

  return messages.map((message, index) => {
    const hasMessageCitations = Object.prototype.hasOwnProperty.call(message, "citations");
    const citations = hasMessageCitations
      ? normalizeCitations(message.citations)
      : index === lastAssistantIndex
        ? fallbackCitations
        : [];
    return {
      ...message,
      citations,
      used_saved_memory: message.used_saved_memory === true,
    } as ConversationMessage;
  });
}

function normalizeConversationSession(value: unknown): ConversationSession {
  const session = value as ConversationSession;
  return {
    ...session,
    messages: normalizeConversationMessages(session.messages),
  };
}

function normalizeConversationTurn(value: unknown): ConversationTurn {
  const turn = value as ConversationTurn;
  const citations = normalizeCitations(turn.citations);
  return {
    ...turn,
    citations,
    used_saved_memory: turn.used_saved_memory === true,
    messages: normalizeConversationMessages(turn.messages, citations),
  };
}

export async function askQuestion(
  query: string,
  request: typeof fetch = fetch,
): Promise<QAResponse> {
  let response: Response;
  try {
    response = await request("/api/qa", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });
  } catch {
    throw new Error("Unable to reach the Knowvia backend.");
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new Error(
      response.ok
        ? "Knowvia returned an unreadable response."
        : `Knowvia returned an error (${response.status}).`,
    );
  }

  if (!response.ok) {
    throw new Error(errorMessage(payload, response.status));
  }

  return payload as QAResponse;
}

async function readJSONResponse(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    throw new Error(
      response.ok
        ? "Knowvia returned an unreadable response."
        : `Knowvia returned an error (${response.status}).`,
    );
  }
}

export async function createConversation(
  request: typeof fetch = fetch,
): Promise<ConversationSession> {
  let response: Response;
  try {
    response = await request("/api/conversations", { method: "POST" });
  } catch {
    throw new Error("Unable to reach the Knowvia backend.");
  }
  const payload = await readJSONResponse(response);
  if (!response.ok) {
    throw new Error(errorMessage(payload, response.status));
  }
  return normalizeConversationSession(payload);
}

export async function listConversations(
  request: typeof fetch = fetch,
): Promise<ConversationSessionSummary[]> {
  let response: Response;
  try {
    response = await request("/api/conversations");
  } catch {
    throw new Error("Unable to reach the Knowvia backend.");
  }
  const payload = await readJSONResponse(response);
  if (!response.ok) {
    throw new Error(errorMessage(payload, response.status));
  }
  if (!Array.isArray(payload)) {
    throw new Error("Knowvia returned an invalid conversation list.");
  }
  return payload as ConversationSessionSummary[];
}

export async function getConversation(
  sessionId: number,
  request: typeof fetch = fetch,
): Promise<ConversationSession> {
  let response: Response;
  try {
    response = await request(`/api/conversations/${sessionId}`);
  } catch {
    throw new Error("Unable to reach the Knowvia backend.");
  }
  const payload = await readJSONResponse(response);
  if (!response.ok) {
    throw new Error(errorMessage(payload, response.status));
  }
  return normalizeConversationSession(payload);
}

export async function sendConversationMessage(
  sessionId: number,
  query: string,
  request: typeof fetch = fetch,
): Promise<ConversationTurn> {
  let response: Response;
  try {
    response = await request(`/api/conversations/${sessionId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });
  } catch {
    throw new Error("Unable to reach the Knowvia backend.");
  }
  const payload = await readJSONResponse(response);
  if (!response.ok) {
    throw new Error(errorMessage(payload, response.status));
  }
  return normalizeConversationTurn(payload);
}

function parseConversationStreamFrame(frame: string): ConversationStreamEvent {
  let eventType: string | null = null;
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith(":")) {
      continue;
    }
    if (line.startsWith("event:")) {
      eventType = line.slice("event:".length).trim();
      continue;
    }
    if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trimStart());
    }
  }

  if (
    eventType === null ||
    !conversationStreamEventTypes.has(eventType as ConversationStreamEventType) ||
    dataLines.length === 0
  ) {
    throw new Error("Knowvia returned an invalid streaming event.");
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(dataLines.join("\n"));
  } catch {
    throw new Error("Knowvia returned an unreadable streaming event.");
  }
  if (typeof parsed !== "object" || parsed === null) {
    throw new Error("Knowvia returned an invalid streaming event.");
  }
  const value = parsed as Record<string, unknown>;
  if (
    typeof value.run_id !== "string" ||
    !value.run_id.trim() ||
    !Number.isSafeInteger(value.sequence) ||
    Number(value.sequence) < 1 ||
    typeof value.payload !== "object" ||
    value.payload === null
  ) {
    throw new Error("Knowvia returned an invalid streaming event.");
  }
  return {
    event_type: eventType as ConversationStreamEventType,
    run_id: value.run_id,
    sequence: value.sequence as number,
    payload: value.payload as ConversationStreamPayload,
  };
}

function waitForRenderBoundary(): Promise<void> {
  if (typeof requestAnimationFrame === "function") {
    return new Promise((resolve) => requestAnimationFrame(() => resolve()));
  }
  return new Promise((resolve) => setTimeout(resolve, 0));
}

export function parseConversationStreamFrames(
  input: string,
): ConversationStreamEvent[] {
  const normalized = input.replace(/\r\n/g, "\n");
  return normalized
    .split("\n\n")
    .filter((frame) => frame.trim())
    .map(parseConversationStreamFrame);
}

export async function streamConversationMessage(
  sessionId: number,
  query: string,
  onEvent: ConversationStreamEventHandler,
  request: typeof fetch = fetch,
  signal?: AbortSignal,
): Promise<void> {
  let response: Response;
  try {
    response = await request(`/api/conversations/${sessionId}/messages/stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify({ query }),
      signal,
    });
  } catch (caughtError) {
    if (caughtError instanceof DOMException && caughtError.name === "AbortError") {
      throw caughtError;
    }
    throw new Error("Unable to reach the Knowvia backend.");
  }

  if (!response.ok) {
    const payload = await readJSONResponse(response);
    throw new Error(errorMessage(payload, response.status));
  }
  if (!response.headers.get("content-type")?.includes("text/event-stream")) {
    throw new Error("Knowvia returned an invalid streaming response.");
  }
  if (!response.body) {
    throw new Error("Knowvia returned an empty streaming response.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let runId: string | null = null;
  let expectedSequence = 1;
  let terminal = false;

  const dispatchFrame = async (frame: string): Promise<void> => {
    const event = parseConversationStreamFrames(frame)[0];
    if (!event) {
      return;
    }
    if (runId === null) {
      runId = event.run_id;
    } else if (event.run_id !== runId) {
      throw new Error("Knowvia returned an event for another run.");
    }
    if (
      event.event_type === "done" &&
      event.payload.session_id !== sessionId
    ) {
      throw new Error("Knowvia returned an event for another session.");
    }
    if (event.sequence !== expectedSequence) {
      throw new Error("Knowvia returned out-of-order streaming events.");
    }
    expectedSequence += 1;
    await onEvent(event);
    if (event.event_type === "execution_status" || event.event_type === "answer_delta") {
      await waitForRenderBoundary();
    }
    if (event.event_type === "done" || event.event_type === "error") {
      terminal = true;
    }
  };

  try {
    while (!terminal) {
      const { done, value } = await reader.read();
      if (done) {
        buffer += decoder.decode();
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      buffer = buffer.replace(/\r\n/g, "\n");
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0 && !terminal) {
        await dispatchFrame(buffer.slice(0, boundary));
        buffer = buffer.slice(boundary + 2);
        boundary = buffer.indexOf("\n\n");
      }
    }
    if (!terminal) {
      buffer = buffer.replace(/\r\n/g, "\n");
      if (buffer.trim()) {
        await dispatchFrame(buffer);
      }
    }
  } finally {
    try {
      await reader.cancel();
    } catch {
      // The underlying stream may already be closed after a terminal frame.
    }
  }

  if (!terminal) {
    throw new Error("Knowvia ended the stream before completion.");
  }
}

export async function listMemories(
  request: typeof fetch = fetch,
): Promise<SavedMemory[]> {
  let response: Response;
  try {
    response = await request("/api/memories");
  } catch {
    throw new Error("Unable to reach the Knowvia backend.");
  }
  const payload = await readJSONResponse(response);
  if (!response.ok) {
    throw new Error(errorMessage(payload, response.status));
  }
  if (!Array.isArray(payload)) {
    throw new Error("Knowvia returned an invalid memory list.");
  }
  return payload as SavedMemory[];
}

export async function deleteMemory(
  memoryId: number,
  request: typeof fetch = fetch,
): Promise<void> {
  let response: Response;
  try {
    response = await request(`/api/memories/${memoryId}`, { method: "DELETE" });
  } catch {
    throw new Error("Unable to reach the Knowvia backend.");
  }
  if (response.status !== 204) {
    const payload = await readJSONResponse(response);
    throw new Error(errorMessage(payload, response.status));
  }
}

export async function indexPDF(
  file: File,
  request: typeof fetch = fetch,
): Promise<PDFIndexResponse> {
  const formData = new FormData();
  formData.append("document", file, file.name);

  let response: Response;
  try {
    response = await request("/api/ingest/document", {
      method: "POST",
      body: formData,
    });
  } catch {
    throw new Error("Unable to reach the Knowvia backend.");
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new Error(
      response.ok
        ? "Knowvia returned an unreadable response."
        : `Knowvia returned an error (${response.status}).`,
    );
  }

  if (!response.ok) {
    throw new Error(errorMessage(payload, response.status));
  }

  return payload as PDFIndexResponse;
}

export async function indexImage(
  files: File[],
  request: typeof fetch = fetch,
): Promise<ImageIndexResponse> {
  const formData = new FormData();
  for (const file of files) {
    formData.append("images", file, file.name);
  }

  let response: Response;
  try {
    response = await request("/api/ingest/image-ocr", {
      method: "POST",
      body: formData,
    });
  } catch {
    throw new Error("Unable to reach the Knowvia backend.");
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new Error(
      response.ok
        ? "Knowvia returned an unreadable response."
        : `Knowvia returned an error (${response.status}).`,
    );
  }

  if (!response.ok) {
    throw new Error(errorMessage(payload, response.status));
  }

  return payload as ImageIndexResponse;
}

export async function indexURL(
  url: string,
  request: typeof fetch = fetch,
): Promise<URLIndexResponse> {
  let response: Response;
  try {
    response = await request("/api/ingest/url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
  } catch {
    throw new Error("Unable to reach the Knowvia backend.");
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new Error(
      response.ok
        ? "Knowvia returned an unreadable response."
        : `Knowvia returned an error (${response.status}).`,
    );
  }

  if (!response.ok) {
    throw new Error(errorMessage(payload, response.status));
  }

  return payload as URLIndexResponse;
}

export async function listKnowledgeSources(
  request: typeof fetch = fetch,
): Promise<KnowledgeSource[]> {
  let response: Response;
  try {
    response = await request("/api/knowledge/sources");
  } catch {
    throw new Error("Unable to reach the Knowvia backend.");
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new Error(
      response.ok
        ? "Knowvia returned an unreadable response."
        : `Knowvia returned an error (${response.status}).`,
    );
  }

  if (!response.ok) {
    throw new Error(errorMessage(payload, response.status));
  }

  return payload as KnowledgeSource[];
}
