import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";

const firstSession = {
  id: 1,
  title: "First conversation",
  status: "active",
  created_at: "2026-09-05T09:00:00Z",
  updated_at: "2026-09-05T09:10:00Z",
};

const newestSession = {
  id: 2,
  title: "Newest conversation",
  status: "active",
  created_at: "2026-09-05T09:05:00Z",
  updated_at: "2026-09-05T09:20:00Z",
};

const firstDetail = {
  ...firstSession,
  messages: [
    {
      id: 11,
      session_id: 1,
      role: "user",
      content: "Keep this message visible.",
      sequence_number: 1,
      created_at: "2026-09-05T09:10:00Z",
    },
  ],
};

const newestDetail = {
  ...newestSession,
  messages: [],
};

const firstCitation = {
  notion_path: "Knowledge/Agents/Patterns",
  page_id: "page-agent-patterns",
  score: 0.9132,
  source_kind: "pdf",
  source_display_name: "agent-patterns.pdf",
  locator: "page 1",
};

const secondCitation = {
  notion_path: "Knowledge/Agents/Approvals",
  page_id: "page-approvals",
  score: 0.845,
  source_kind: "notion",
  source_display_name: "approval-workflow.md",
  locator: "section 2",
};

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function installFetch(
  handler: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>,
) {
  const fetchMock = vi.fn(handler);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("conversation session UI", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.history.replaceState({}, "", "/chat");
  });

  it("loads the newest session without creating a session when the list is non-empty", async () => {
    const fetchMock = installFetch(async (input) => {
      if (input === "/api/conversations") {
        return jsonResponse([newestSession, firstSession]);
      }
      if (input === "/api/conversations/2") {
        return jsonResponse(newestDetail);
      }
      throw new Error(`Unexpected request: ${String(input)}`);
    });

    render(<App />);

    expect(screen.getByText("Loading conversations")).toBeVisible();
    expect(screen.getByLabelText("Question")).toBeDisabled();
    expect(await screen.findByText("Newest conversation")).toBeVisible();
    expect(screen.getByText("Ask about your indexed knowledge.")).toBeVisible();
    expect(window.location.search).toBe("?session_id=2");
    expect(fetchMock.mock.calls.some(([input, init]) =>
      input === "/api/conversations" && init?.method === "POST",
    )).toBe(false);
  });

  it("bootstraps exactly one backend session after a successful empty list", async () => {
    const bootstrapSession = {
      id: 7,
      title: "New conversation",
      status: "active",
      created_at: "2026-09-05T09:30:00Z",
      updated_at: "2026-09-05T09:30:00Z",
      messages: [],
    };
    const fetchMock = installFetch(async (input, init) => {
      if (input === "/api/conversations" && !init?.method) {
        return jsonResponse([]);
      }
      if (input === "/api/conversations" && init?.method === "POST") {
        return jsonResponse(bootstrapSession, 201);
      }
      throw new Error(`Unexpected request: ${String(input)}`);
    });

    render(<App />);

    expect(await screen.findByText("Ask about your indexed knowledge.")).toBeVisible();
    expect(window.location.search).toBe("?session_id=7");
    expect(fetchMock.mock.calls.filter(([input, init]) =>
      input === "/api/conversations" && init?.method === "POST",
    )).toHaveLength(1);
  });

  it("fails closed for a malformed URL session id and falls back to the newest session", async () => {
    const fetchMock = installFetch(async (input) => {
      if (input === "/api/conversations") {
        return jsonResponse([newestSession, firstSession]);
      }
      if (input === "/api/conversations/2") {
        return jsonResponse(newestDetail);
      }
      throw new Error(`Unexpected request: ${String(input)}`);
    });

    window.history.replaceState({}, "", "/chat?session_id=not-a-number");
    render(<App />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Conversation is unavailable.",
    );
    expect(await screen.findByText("Newest conversation")).toBeVisible();
    expect(window.location.search).toBe("?session_id=2");
    expect(fetchMock.mock.calls.some(([input]) => input === "/api/conversations/not-a-number")).toBe(false);
  });

  it("shows list failure with Retry and does not treat failure as an empty list", async () => {
    const fetchMock = installFetch(() => Promise.reject(new TypeError("Failed to fetch")));

    render(<App />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Conversations unavailable",
    );
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
    expect(screen.queryByText("Ask about your indexed knowledge.")).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([input, init]) =>
      input === "/api/conversations" && init?.method === "POST",
    )).toBe(false);
  });

  it("keeps the old session and URL when switching to another session fails", async () => {
    const fetchMock = installFetch(async (input) => {
      if (input === "/api/conversations") {
        return jsonResponse([newestSession, firstSession]);
      }
      if (input === "/api/conversations/2") {
        return jsonResponse({ detail: { message: "Conversation is unavailable." } }, 404);
      }
      if (input === "/api/conversations/1") {
        return jsonResponse(firstDetail);
      }
      throw new Error(`Unexpected request: ${String(input)}`);
    });

    window.history.replaceState({}, "", "/chat?session_id=1");
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText("Keep this message visible.")).toBeVisible();
    await user.click(screen.getByRole("button", { name: /Newest conversation/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Conversation is unavailable.",
    );
    expect(screen.getByText("Keep this message visible.")).toBeVisible();
    expect(window.location.search).toBe("?session_id=1");
    expect(screen.getByRole("button", { name: /First conversation/ })).toHaveAttribute(
      "aria-current",
      "true",
    );
  });

  it("offers Retry after New Chat creation fails and preserves the active session", async () => {
    let createAttempts = 0;
    const createdSession = {
      id: 9,
      title: "New conversation",
      status: "active",
      created_at: "2026-09-05T09:50:00Z",
      updated_at: "2026-09-05T09:50:00Z",
      messages: [],
    };
    const fetchMock = installFetch(async (input, init) => {
      if (input === "/api/conversations" && !init?.method) {
        return jsonResponse([firstSession]);
      }
      if (input === "/api/conversations/1") {
        return jsonResponse(firstDetail);
      }
      if (input === "/api/conversations" && init?.method === "POST") {
        createAttempts += 1;
        return createAttempts === 1
          ? jsonResponse({ detail: { message: "Unable to create a new conversation." } }, 503)
          : jsonResponse(createdSession, 201);
      }
      throw new Error(`Unexpected request: ${String(input)}`);
    });

    const user = userEvent.setup();
    render(<App />);
    await screen.findByText("Keep this message visible.");
    await user.click(screen.getByRole("button", { name: "New Chat" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Unable to create a new conversation.",
    );
    expect(screen.getByText("Keep this message visible.")).toBeVisible();
    expect(window.location.search).toBe("?session_id=1");
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("New conversation")).toBeVisible();
    expect(window.location.search).toBe("?session_id=9");
    expect(createAttempts).toBe(2);
    expect(fetchMock.mock.calls.filter(([input, request]) =>
      input === "/api/conversations" && request?.method === "POST",
    )).toHaveLength(2);
  });

  it("sends a follow-up through the active session endpoint and renders returned history", async () => {
    const turn = {
      session_id: 1,
      title: "First conversation",
      updated_at: "2026-09-05T09:40:00Z",
      workflow_run_id: 33,
      status: "succeeded",
      answer: "The follow-up is grounded.",
      insufficient_info: false,
      retrieved_chunk_count: 1,
      citations: [],
      provider: "openai",
      model: "gpt-4o-mini",
      messages: [
        ...firstDetail.messages,
        {
          id: 12,
          session_id: 1,
          role: "user",
          content: "What follows from that?",
          sequence_number: 2,
          created_at: "2026-09-05T09:39:00Z",
        },
        {
          id: 13,
          session_id: 1,
          role: "assistant",
          content: "The follow-up is grounded.",
          sequence_number: 3,
          created_at: "2026-09-05T09:40:00Z",
        },
      ],
    };
    const fetchMock = installFetch(async (input, init) => {
      if (input === "/api/conversations") {
        return jsonResponse([firstSession]);
      }
      if (input === "/api/conversations/1") {
        return jsonResponse(firstDetail);
      }
      if (input === "/api/conversations/1/messages") {
        expect(init?.method).toBe("POST");
        expect(init?.body).toBe(JSON.stringify({ query: "What follows from that?" }));
        return jsonResponse(turn);
      }
      throw new Error(`Unexpected request: ${String(input)}`);
    });

    const user = userEvent.setup();
    render(<App />);
    await screen.findByText("Keep this message visible.");
    const question = screen.getByLabelText("Question");
    await user.type(question, "What follows from that?");
    await user.click(screen.getByRole("button", { name: "Ask Knowvia" }));

    expect(await screen.findByText("The follow-up is grounded.")).toBeVisible();
    expect(screen.getByText("What follows from that?")).toBeVisible();
    await waitFor(() => {
      expect(fetchMock.mock.calls.filter(([input]) =>
        input === "/api/conversations/1/messages",
      )).toHaveLength(1);
    });
  });

  it("renders each persisted assistant citation as an independent collapsed disclosure", async () => {
    const detail = {
      ...firstSession,
      messages: [
        ...firstDetail.messages,
        {
          id: 12,
          session_id: 1,
          role: "assistant",
          content: "The first grounded answer.",
          sequence_number: 2,
          created_at: "2026-09-05T09:11:00Z",
          citations: [firstCitation],
        },
        {
          id: 13,
          session_id: 1,
          role: "user",
          content: "What follows from that?",
          sequence_number: 3,
          created_at: "2026-09-05T09:12:00Z",
          citations: [],
        },
        {
          id: 14,
          session_id: 1,
          role: "assistant",
          content: "The second grounded answer.",
          sequence_number: 4,
          created_at: "2026-09-05T09:13:00Z",
          citations: [secondCitation],
        },
      ],
    };
    installFetch(async (input) => {
      if (input === "/api/conversations") {
        return jsonResponse([firstSession]);
      }
      if (input === "/api/conversations/1") {
        return jsonResponse(detail);
      }
      throw new Error(`Unexpected request: ${String(input)}`);
    });

    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText("The first grounded answer.")).toBeVisible();
    const sourceToggles = screen.getAllByText("Sources · 1");
    expect(sourceToggles).toHaveLength(2);
    expect(screen.getByText("agent-patterns.pdf")).not.toBeVisible();
    expect(screen.getByText("approval-workflow.md")).not.toBeVisible();

    await user.click(sourceToggles[0]);
    expect(screen.getByText("agent-patterns.pdf")).toBeVisible();
    expect(screen.getByText("approval-workflow.md")).not.toBeVisible();

    await user.click(sourceToggles[0]);
    expect(screen.getByText("agent-patterns.pdf")).not.toBeVisible();
    await user.click(sourceToggles[1]);
    expect(screen.getByText("approval-workflow.md")).toBeVisible();
  });

  it("does not render a Sources disclosure for a citation-free conversational recall message", async () => {
    const detail = {
      ...firstSession,
      messages: [
        {
          id: 11,
          session_id: 1,
          role: "user",
          content: "hi",
          sequence_number: 1,
          created_at: "2026-09-05T09:10:00Z",
          citations: [],
        },
        {
          id: 12,
          session_id: 1,
          role: "assistant",
          content: 'You said "hi".',
          sequence_number: 2,
          created_at: "2026-09-05T09:11:00Z",
          citations: [],
        },
      ],
    };
    installFetch(async (input) => {
      if (input === "/api/conversations") {
        return jsonResponse([firstSession]);
      }
      if (input === "/api/conversations/1") {
        return jsonResponse(detail);
      }
      throw new Error(`Unexpected request: ${String(input)}`);
    });

    render(<App />);

    expect(await screen.findByText('You said "hi".')).toBeVisible();
    expect(screen.queryByText(/Sources ·/)).not.toBeInTheDocument();
  });
});
