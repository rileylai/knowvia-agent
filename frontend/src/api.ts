export type QACitation = {
  notion_path?: string | null;
  page_id?: string | null;
  score: number;
  source_kind?: string;
  source_display_name?: string | null;
  locator?: string | null;
  source_url?: string | null;
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

export type URLIndexResponse = PDFIndexResponse;

export type KnowledgeSource = {
  id: number;
  display_name: string;
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
