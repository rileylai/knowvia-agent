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

    await user.click(screen.getByRole("button", { name: "Memory" }));
    expect(screen.getByRole("heading", { name: "Memory" })).toBeVisible();
    expect(
      screen.getByText("Persistent memory will be available in a later phase."),
    ).toBeVisible();
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
    const fetchMock = vi.fn((..._args: Parameters<typeof fetch>) => deferred.promise);
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Knowledge" }));
    const file = new File(["%PDF-1.7 fixture"], "agent-notes.pdf", {
      type: "application/pdf",
    });
    await user.upload(screen.getByLabelText("PDF file"), file);

    expect(screen.getByRole("status")).toHaveTextContent("Uploading and indexing");
    expect(screen.getByRole("button", { name: "Upload source" })).toBeDisabled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/ingest/document");
    expect(fetchMock.mock.calls[0][1]?.method).toBe("POST");
    expect(fetchMock.mock.calls[0][1]?.body).toBeInstanceOf(FormData);

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

  it("shows upload errors and blocks duplicate uploads while indexing", async () => {
    const deferred = deferredResponse();
    const fetchMock = vi.fn(() => deferred.promise);
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Knowledge" }));
    const input = screen.getByLabelText("PDF file");
    const file = new File(["not a pdf"], "notes.txt", { type: "text/plain" });
    fireEvent.change(input, { target: { files: [file] } });
    expect(screen.getByRole("alert")).toHaveTextContent("Choose a PDF file");
    expect(fetchMock).not.toHaveBeenCalled();

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
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("status")).toHaveTextContent("Uploading and indexing");

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
