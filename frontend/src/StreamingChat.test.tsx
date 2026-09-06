import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";

const sessionA = {
  id: 1,
  title: "First conversation",
  status: "active",
  created_at: "2026-09-06T09:00:00Z",
  updated_at: "2026-09-06T09:00:00Z",
};

const sessionB = {
  id: 2,
  title: "Second conversation",
  status: "active",
  created_at: "2026-09-06T09:01:00Z",
  updated_at: "2026-09-06T09:01:00Z",
};

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function streamFrame(eventType: string, sequence: number, payload: unknown) {
  return `event: ${eventType}\n` +
    `data: ${JSON.stringify({ run_id: "run-1", sequence, payload })}\n\n`;
}

function streamResponse(frames: string[]) {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        frames.forEach((frame) => controller.enqueue(encoder.encode(frame)));
        controller.close();
      },
    }),
    { headers: { "Content-Type": "text/event-stream" } },
  );
}

function installFetch(
  streamHandler: (sessionId: number) => Response | Promise<Response>,
  details: Record<number, unknown> = {
    1: { ...sessionA, messages: [] },
    2: { ...sessionB, messages: [] },
  },
) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = typeof input === "string"
      ? input
      : input instanceof URL
        ? input.pathname
        : input.url;
    if (path === "/api/conversations" && !init?.method) {
      return Promise.resolve(jsonResponse([sessionA, sessionB]));
    }
    const detailMatch = path.match(/^\/api\/conversations\/(\d+)$/);
    if (detailMatch && !init?.method) {
      return Promise.resolve(jsonResponse(details[Number(detailMatch[1])]));
    }
    const streamMatch = path.match(/^\/api\/conversations\/(\d+)\/messages\/stream$/);
    if (streamMatch) {
      return Promise.resolve(streamHandler(Number(streamMatch[1])));
    }
    return Promise.resolve(jsonResponse([]));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("streaming Chat UX", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders status and answer deltas progressively, then keeps the completed metadata", async () => {
    let controller: ReadableStreamDefaultController<Uint8Array> | undefined;
    const encoder = new TextEncoder();
    installFetch(() => new Response(
      new ReadableStream<Uint8Array>({
        start(nextController) {
          controller = nextController;
          nextController.enqueue(encoder.encode(streamFrame("execution_status", 1, { phase: "searching_knowledge" })));
        },
      }),
      { headers: { "Content-Type": "text/event-stream" } },
    ));
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText("Ask about your indexed knowledge.");
    await user.type(screen.getByLabelText("Question"), "What is grounded?");
    await user.click(screen.getByRole("button", { name: "Ask Knowvia" }));

    expect(await screen.findByText("Searching knowledge…")).toBeVisible();
    expect(screen.getByRole("button", { name: "Ask Knowvia" })).toBeDisabled();

    controller?.enqueue(encoder.encode(streamFrame("answer_delta", 2, { text: "Grounded " })));
    expect(await screen.findByText("Grounded")).toBeVisible();
    controller?.enqueue(encoder.encode(streamFrame("answer_delta", 3, { text: "answer" })));
    controller?.enqueue(encoder.encode(streamFrame("done", 4, {
      message_id: 19,
      session_id: 1,
      title: "What is grounded?",
      updated_at: "2026-09-06T09:02:00Z",
      workflow_run_id: 77,
      insufficient_info: false,
      used_saved_memory: false,
    })));
    controller?.close();

    expect(await screen.findByText("Grounded answer")).toBeVisible();
    await waitFor(() => expect(screen.getByLabelText("Question")).toBeEnabled());
    expect(screen.getByText("Run 77")).toBeVisible();
  });

  it("marks a partial stream as incomplete and re-enables input after an error", async () => {
    installFetch(() => streamResponse([
      streamFrame("execution_status", 1, { phase: "generating" }),
      streamFrame("answer_delta", 2, { text: "Partial answer" }),
      streamFrame("error", 3, { error_code: "LLM_PROVIDER_ERROR", message: "The model provider failed." }),
    ]));
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText("Ask about your indexed knowledge.");
    await user.type(screen.getByLabelText("Question"), "Will this fail?");
    await user.click(screen.getByRole("button", { name: "Ask Knowvia" }));

    expect(await screen.findByText("Partial answer")).toBeVisible();
    expect(await screen.findByText("This response was not completed or saved.")).toBeVisible();
    expect(screen.getByRole("alert")).toHaveTextContent("The model provider failed.");
    expect(screen.getByRole("button", { name: "Ask Knowvia" })).toBeEnabled();
    expect(screen.queryByText("Run 0")).not.toBeInTheDocument();
  });

  it("aborts the old session stream before switching and ignores its later events", async () => {
    let controller: ReadableStreamDefaultController<Uint8Array> | undefined;
    const encoder = new TextEncoder();
    installFetch((sessionId) => {
      if (sessionId === 2) {
        return streamResponse([
          streamFrame("answer_delta", 1, { text: "Session B answer" }),
          streamFrame("done", 2, {
            message_id: 21,
            session_id: 2,
            title: "Second conversation",
            updated_at: "2026-09-06T09:03:00Z",
            workflow_run_id: 78,
            insufficient_info: false,
            used_saved_memory: false,
          }),
        ]);
      }
      return new Response(
        new ReadableStream<Uint8Array>({
          start(nextController) {
            controller = nextController;
            nextController.enqueue(encoder.encode(streamFrame("execution_status", 1, { phase: "generating" })));
          },
        }),
        { headers: { "Content-Type": "text/event-stream" } },
      );
    });
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText("Ask about your indexed knowledge.");
    await user.type(screen.getByLabelText("Question"), "Session A question");
    await user.click(screen.getByRole("button", { name: "Ask Knowvia" }));
    await screen.findByText("Generating answer…");

    await user.click(screen.getByRole("button", { name: /Second conversation/ }));
    expect(await screen.findByText("Ask about your indexed knowledge.")).toBeVisible();
    try {
      controller?.enqueue(encoder.encode(streamFrame("answer_delta", 2, { text: "Session A answer" })));
    } catch {
      // The browser cancels the old stream when the session changes.
    }
    expect(screen.queryByText("Session A answer")).not.toBeInTheDocument();
  });
});
