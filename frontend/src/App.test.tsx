import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";

const successPayload = {
  workflow_run_id: 17,
  status: "succeeded",
  answer: "Attention weights relevant values using query and key vectors.",
  insufficient_info: false,
  retrieved_chunk_count: 2,
  citations: [
    {
      notion_path: "Knowledge/NLP/Week5/Attention",
      page_id: "page-nlp-week5",
      score: 0.9132,
    },
  ],
  provider: "openai",
  model: "gpt-4o-mini",
};

function deferredResponse() {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const indexedSource = {
  id: 41,
  display_name: "agent-patterns.pdf",
  source_kind: "pdf",
  status: "indexed",
  chunk_count: 40,
  updated_at: "2026-09-05T09:30:00Z",
};

async function submitQuestion(question = "How does attention work?") {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Question"), question);
  await user.click(screen.getByRole("button", { name: "Ask Knowvia" }));
  return user;
}

describe("Knowvia frontend harness", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows all three surfaces and marks Memory as a future capability", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([])));
    const user = userEvent.setup();
    render(<App />);

    expect(screen.getByRole("button", { name: "Knowledge" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Chat" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Memory" })).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Knowledge" }));
    expect(screen.getByRole("heading", { name: "Knowledge" })).toBeVisible();
    expect(screen.getByText(/PDF knowledge baseline/i)).toBeVisible();
    expect(screen.getByRole("button", { name: "Upload source" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Add URL" })).toBeDisabled();
    expect(screen.getByText("No indexed sources yet.")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Memory" }));
    expect(screen.getByRole("heading", { name: "Memory" })).toBeVisible();
    expect(
      screen.getByText("Persistent memory will be available in a later phase."),
    ).toBeVisible();
  });

  it("renders the indexed PDF source inventory", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse([indexedSource])),
    );
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Knowledge" }));

    expect(await screen.findByText("agent-patterns.pdf")).toBeVisible();
    expect(screen.getByText("pdf")).toBeVisible();
    expect(screen.getByText("indexed")).toBeVisible();
    expect(screen.getByText("40 chunks")).toBeVisible();
    expect(screen.getByText(/updated · 2026-09-05T09:30:00Z/)).toBeVisible();
  });

  it("shows inventory loading state", async () => {
    const deferred = deferredResponse();
    vi.stubGlobal("fetch", vi.fn(() => deferred.promise));
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Knowledge" }));

    expect(screen.getByText("Loading indexed sources")).toBeVisible();
    deferred.resolve(jsonResponse([]));
  });

  it("shows a visible inventory error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
    );
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Knowledge" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Unable to reach the Knowvia backend.",
    );
  });

  it("shows loading, prevents duplicate submission, then renders the answer", async () => {
    const deferred = deferredResponse();
    const fetchMock = vi.fn((..._args: Parameters<typeof fetch>) => deferred.promise);
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    await submitQuestion();

    expect(screen.getByRole("status")).toHaveTextContent("Searching knowledge");
    expect(screen.getByRole("button", { name: "Ask Knowvia" })).toBeDisabled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith("/api/qa", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: "How does attention work?" }),
    });

    await userEvent.click(screen.getByRole("button", { name: "Ask Knowvia" }));
    expect(fetchMock).toHaveBeenCalledTimes(1);

    deferred.resolve(
      new Response(JSON.stringify(successPayload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    expect(await screen.findByText(successPayload.answer)).toBeVisible();
    expect(screen.queryByText("Searching knowledge")).not.toBeInTheDocument();
  });

  it("submits the question when Enter is pressed", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(successPayload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("Question"), "What is Beam Search?");
    await user.keyboard("{Enter}");

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(fetchMock.mock.calls[0][1]?.body).toBe(
      JSON.stringify({ query: "What is Beam Search?" }),
    );
  });

  it("does not submit when Shift+Enter is pressed", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("Question"), "First line");
    await user.keyboard("{Shift>}{Enter}{/Shift}");

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("preserves the newline inserted by Shift+Enter", async () => {
    vi.stubGlobal("fetch", vi.fn());
    const user = userEvent.setup();
    render(<App />);
    const question = screen.getByLabelText("Question");

    await user.type(question, "First line");
    await user.keyboard("{Shift>}{Enter}{/Shift}");
    await user.type(question, "Second line");

    expect(question).toHaveValue("First line\nSecond line");
  });

  it("does not submit Enter while an IME composition is active", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<App />);
    const question = screen.getByLabelText("Question");

    await user.type(question, "知識");
    fireEvent.compositionStart(question);
    fireEvent.keyDown(question, { key: "Enter", code: "Enter", isComposing: true });
    fireEvent.compositionEnd(question);

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not submit Enter again while a request is loading", async () => {
    const deferred = deferredResponse();
    const fetchMock = vi.fn(() => deferred.promise);
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<App />);
    const question = screen.getByLabelText("Question");

    await user.type(question, "What is Beam Search?");
    await user.keyboard("{Enter}");
    fireEvent.keyDown(question, { key: "Enter", code: "Enter" });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    deferred.resolve(
      new Response(JSON.stringify(successPayload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    expect(await screen.findByText(successPayload.answer)).toBeVisible();
  });

  it("does not submit an empty or whitespace-only question with Enter", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("Question"), "   ");
    await user.keyboard("{Enter}");

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("renders citations only from backend response metadata", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(successPayload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    render(<App />);

    await submitQuestion("Summarize the attention note.");

    expect(await screen.findByText("Knowledge/NLP/Week5/Attention")).toBeVisible();
    expect(screen.getByText("91.3% match")).toBeVisible();
    expect(screen.getByText("page-nlp-week5")).toBeVisible();
  });

  it("uploads a PDF, shows indexing, then renders safe index metadata", async () => {
    const deferred = deferredResponse();
    const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) =>
      input === "/api/knowledge/sources"
        ? Promise.resolve(jsonResponse([]))
        : deferred.promise,
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Knowledge" }));
    const file = new File(["%PDF-1.7 fixture"], "agent-notes.pdf", {
      type: "application/pdf",
    });
    await user.upload(screen.getByLabelText("PDF file"), file);

    expect(screen.getByText("Uploading and indexing")).toBeVisible();
    expect(screen.getByRole("button", { name: "Upload source" })).toBeDisabled();
    const uploadCalls = fetchMock.mock.calls.filter(
      ([input]) => input === "/api/ingest/document",
    );
    expect(uploadCalls).toHaveLength(1);
    expect(uploadCalls[0][1]?.method).toBe("POST");
    expect(uploadCalls[0][1]?.body).toBeInstanceOf(FormData);

    deferred.resolve(
      new Response(
        JSON.stringify({
          workflow_run_id: 24,
          status: "succeeded",
          source_document_id: 9,
          source_type: "pdf",
          source_display_name: "agent-notes.pdf",
          content_hash: "safe-hash",
          index_status: "indexed",
          indexed_chunk_count: 2,
          embedded_chunk_count: 2,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    expect(await screen.findByText("PDF indexed")).toBeVisible();
    expect(screen.getByText("agent-notes.pdf")).toBeVisible();
    expect(screen.getByText("2 chunks · 2 embedded")).toBeVisible();
  });

  it("shows an exact duplicate upload as already indexed", async () => {
    const fetchMock = vi.fn((...args: Parameters<typeof fetch>) => {
      const [input, init] = args;
      if (input === "/api/knowledge/sources") {
        return Promise.resolve(jsonResponse([indexedSource]));
      }
      expect(init?.method).toBe("POST");
      return Promise.resolve(
        jsonResponse({
          workflow_run_id: 26,
          status: "already_indexed",
          source_document_id: indexedSource.id,
          source_type: "pdf",
          source_display_name: indexedSource.display_name,
          content_hash: "safe-hash",
          index_status: "indexed",
          indexed_chunk_count: indexedSource.chunk_count,
          embedded_chunk_count: indexedSource.chunk_count,
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Knowledge" }));
    await user.upload(
      screen.getByLabelText("PDF file"),
      new File(["%PDF-1.7 fixture"], "renamed.pdf", { type: "application/pdf" }),
    );

    expect(await screen.findByText("Already indexed")).toBeVisible();
    expect(screen.getByText("This PDF is already indexed.")).toBeVisible();
    expect(screen.getByText("40 chunks · 40 embedded")).toBeVisible();
  });

  it("refreshes the inventory after a successful upload", async () => {
    let inventoryCallCount = 0;
    const fetchMock = vi.fn((...args: Parameters<typeof fetch>) => {
      const [input, init] = args;
      if (input === "/api/knowledge/sources") {
        inventoryCallCount += 1;
        return Promise.resolve(
          jsonResponse(inventoryCallCount === 1 ? [] : [indexedSource]),
        );
      }
      expect(init?.method).toBe("POST");
      return Promise.resolve(
        jsonResponse({
          workflow_run_id: 27,
          status: "succeeded",
          source_document_id: indexedSource.id,
          source_type: "pdf",
          source_display_name: indexedSource.display_name,
          content_hash: "safe-hash",
          index_status: "indexed",
          indexed_chunk_count: indexedSource.chunk_count,
          embedded_chunk_count: indexedSource.chunk_count,
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Knowledge" }));
    expect(await screen.findByText("No indexed sources yet.")).toBeVisible();
    await user.upload(
      screen.getByLabelText("PDF file"),
      new File(["%PDF-1.7 fixture"], indexedSource.display_name, {
        type: "application/pdf",
      }),
    );

    expect(await screen.findByText("PDF indexed")).toBeVisible();
    expect(screen.getAllByText(indexedSource.display_name)).toHaveLength(2);
    expect(screen.getByText("40 chunks")).toBeVisible();
  });

  it("shows upload errors and blocks duplicate uploads while indexing", async () => {
    const deferred = deferredResponse();
    const fetchMock = vi.fn((input: RequestInfo | URL) =>
      input === "/api/knowledge/sources"
        ? Promise.resolve(jsonResponse([]))
        : deferred.promise,
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Knowledge" }));
    const input = screen.getByLabelText("PDF file");
    const file = new File(["not a pdf"], "notes.txt", { type: "text/plain" });
    fireEvent.change(input, { target: { files: [file] } });
    expect(screen.getByRole("alert")).toHaveTextContent("Choose a PDF file");
    expect(
      fetchMock.mock.calls.filter(([input]) => input === "/api/ingest/document"),
    ).toHaveLength(0);

    const pdf = new File(["%PDF-1.7 fixture"], "notes.pdf", {
      type: "application/pdf",
    });
    const pdfFiles = {
      0: pdf,
      length: 1,
      item: (index: number) => (index === 0 ? pdf : null),
    } as unknown as FileList;
    fireEvent.change(input, { target: { files: pdfFiles } });
    fireEvent.change(input, { target: { files: pdfFiles } });
    expect(
      fetchMock.mock.calls.filter(([input]) => input === "/api/ingest/document"),
    ).toHaveLength(1);
    expect(screen.getByText("Uploading and indexing")).toBeVisible();

    deferred.resolve(
      new Response(
        JSON.stringify({
          workflow_run_id: 25,
          status: "succeeded",
          source_document_id: 10,
          source_type: "pdf",
          source_display_name: "notes.pdf",
          content_hash: "safe-hash",
          index_status: "indexed",
          indexed_chunk_count: 1,
          embedded_chunk_count: 1,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    expect(await screen.findByText("PDF indexed")).toBeVisible();
  });

  it("adds a URL, shows indexing, then renders URL provenance metadata", async () => {
    const deferred = deferredResponse();
    const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) =>
      input === "/api/knowledge/sources"
        ? Promise.resolve(jsonResponse([]))
        : deferred.promise,
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Knowledge" }));
    await user.type(screen.getByLabelText("URL"), "https://example.com/agents");
    await user.click(screen.getByRole("button", { name: "Add URL" }));

    expect(screen.getByText("Fetching and indexing URL")).toBeVisible();
    expect(screen.getByRole("button", { name: "Add URL" })).toBeDisabled();
    const urlCalls = fetchMock.mock.calls.filter(
      ([input]) => input === "/api/ingest/url",
    );
    expect(urlCalls).toHaveLength(1);
    expect(urlCalls[0][1]?.method).toBe("POST");
    expect(urlCalls[0][1]?.body).toBe(
      JSON.stringify({ url: "https://example.com/agents" }),
    );

    deferred.resolve(
      jsonResponse({
        workflow_run_id: 31,
        status: "succeeded",
        source_document_id: 12,
        source_type: "url",
        source_display_name: "Bounded Agents Guide",
        content_hash: "safe-hash",
        requested_url: "https://example.com/agents",
        final_url: "https://docs.example.com/agents",
        index_status: "indexed",
        indexed_chunk_count: 3,
        embedded_chunk_count: 3,
      }),
    );

    expect(await screen.findByText("URL indexed")).toBeVisible();
    expect(screen.getByText("Bounded Agents Guide")).toBeVisible();
    expect(screen.getByText("3 chunks · 3 embedded")).toBeVisible();
    expect(screen.getByText("https://docs.example.com/agents")).toBeVisible();
  });

  it("shows an already indexed URL and prevents a second request while loading", async () => {
    const deferred = deferredResponse();
    const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) =>
      input === "/api/knowledge/sources"
        ? Promise.resolve(jsonResponse([]))
        : deferred.promise,
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Knowledge" }));
    const urlInput = screen.getByLabelText("URL");
    await user.type(urlInput, "https://example.com/agents");
    await user.click(screen.getByRole("button", { name: "Add URL" }));
    await user.click(screen.getByRole("button", { name: "Add URL" }));

    expect(
      fetchMock.mock.calls.filter(([input]) => input === "/api/ingest/url"),
    ).toHaveLength(1);

    deferred.resolve(
      jsonResponse({
        workflow_run_id: 32,
        status: "already_indexed",
        source_document_id: 12,
        source_type: "url",
        source_display_name: "Bounded Agents Guide",
        content_hash: "safe-hash",
        requested_url: "https://example.com/agents",
        final_url: "https://docs.example.com/agents",
        index_status: "indexed",
        indexed_chunk_count: 3,
        embedded_chunk_count: 3,
      }),
    );

    expect(await screen.findByText("Already indexed")).toBeVisible();
    expect(screen.getByText("This URL is already indexed.")).toBeVisible();
  });

  it("shows a visible URL error and rejects blank URL submission", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) =>
      input === "/api/knowledge/sources"
        ? Promise.resolve(jsonResponse([]))
        : Promise.resolve(
            jsonResponse(
              { detail: { message: "URL host is not allowed" } },
              400,
            ),
          ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Knowledge" }));
    expect(screen.getByRole("button", { name: "Add URL" })).toBeDisabled();
    await user.type(screen.getByLabelText("URL"), "http://localhost/admin");
    await user.click(screen.getByRole("button", { name: "Add URL" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "URL host is not allowed",
    );
  });

  it("renders PDF citations and keeps legacy Notion citations compatible", async () => {
    const pdfResponse = {
      ...successPayload,
      citations: [
        {
          notion_path: null,
          page_id: null,
          score: 0.845,
          source_kind: "pdf",
          source_display_name: "agent-notes.pdf",
          locator: "page 3",
        },
      ],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(pdfResponse), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    render(<App />);

    await submitQuestion("What is in the PDF?");

    expect(await screen.findByText("agent-notes.pdf")).toBeVisible();
    expect(screen.getByText("page 3")).toBeVisible();
    expect(screen.getByText("84.5% match")).toBeVisible();
  });

  it("preserves the backend insufficient_info result as a distinct state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            ...successPayload,
            answer: "I do not have enough information in production notes to answer safely.",
            insufficient_info: true,
            retrieved_chunk_count: 0,
            citations: [],
            provider: null,
            model: null,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    render(<App />);

    await submitQuestion("What is the lunar office policy?");

    expect(await screen.findByText("Insufficient info")).toBeVisible();
    expect(
      screen.getByText(
        "I do not have enough information in production notes to answer safely.",
      ),
    ).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Citations" })).not.toBeInTheDocument();
  });

  it("clears the previous answer and citations before rendering insufficient info", async () => {
    const secondResponse = deferredResponse();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify(successPayload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockImplementationOnce(() => secondResponse.promise);
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("Question"), "What is attention?");
    await user.click(screen.getByRole("button", { name: "Ask Knowvia" }));
    expect(await screen.findByText(successPayload.answer)).toBeVisible();
    expect(screen.getByText("Knowledge/NLP/Week5/Attention")).toBeVisible();

    await user.clear(screen.getByLabelText("Question"));
    await user.type(screen.getByLabelText("Question"), "hi");
    await user.keyboard("{Enter}");

    expect(screen.queryByText(successPayload.answer)).not.toBeInTheDocument();
    expect(screen.queryByText("Knowledge/NLP/Week5/Attention")).not.toBeInTheDocument();

    secondResponse.resolve(
      new Response(
        JSON.stringify({
          ...successPayload,
          answer: "I do not have enough information in production notes to answer safely.",
          insufficient_info: true,
          citations: successPayload.citations,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    expect(await screen.findByText("Insufficient info")).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Citations" })).not.toBeInTheDocument();
  });

  it.each([
    {
      name: "network failure",
      fetchResult: () => Promise.reject(new TypeError("Failed to fetch")),
      message: "Unable to reach the Knowvia backend.",
    },
    {
      name: "non-2xx response",
      fetchResult: () =>
        Promise.resolve(
          new Response(
            JSON.stringify({ detail: { message: "Provider is unavailable" } }),
            { status: 503, headers: { "Content-Type": "application/json" } },
          ),
        ),
      message: "Provider is unavailable",
    },
  ])("shows a visible error and exits loading after $name", async ({ fetchResult, message }) => {
    vi.stubGlobal("fetch", vi.fn(fetchResult));
    render(<App />);

    await submitQuestion();

    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Ask Knowvia" })).toBeEnabled();
    });
    expect(screen.queryByText("Searching knowledge")).not.toBeInTheDocument();
  });
});
