import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import "./styles.css";

const session = {
  id: 1,
  title: "Long conversation",
  status: "active",
  created_at: "2026-09-07T09:00:00Z",
  updated_at: "2026-09-07T09:30:00Z",
};

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function streamFrame(eventType: string, sequence: number, payload: unknown) {
  return `event: ${eventType}\n` +
    `data: ${JSON.stringify({ run_id: "layout-test-run", sequence, payload })}\n\n`;
}

function installFetch(
  detail: unknown = { ...session, messages: [] },
  streamResponse?: Response,
  knowledgeSources: unknown[] = [],
) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    if (input === "/api/conversations") {
      return jsonResponse([session]);
    }
    if (input === "/api/conversations/1") {
      return jsonResponse(detail);
    }
    if (input === "/api/conversations/1/messages/stream" && streamResponse) {
      return streamResponse;
    }
    if (input === "/api/knowledge/sources") {
      return jsonResponse(knowledgeSources);
    }
    throw new Error(`Unexpected request: ${String(input)}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function setHistoryMetrics(
  history: HTMLElement,
  metrics: { scrollHeight: number; clientHeight: number; scrollTop: number },
) {
  Object.defineProperty(history, "scrollHeight", {
    configurable: true,
    value: metrics.scrollHeight,
  });
  Object.defineProperty(history, "clientHeight", {
    configurable: true,
    value: metrics.clientHeight,
  });
  Object.defineProperty(history, "scrollTop", {
    configurable: true,
    writable: true,
    value: metrics.scrollTop,
  });
}

describe("Chat workspace scroll ownership", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("keeps each desktop pane bounded with the composer after history", async () => {
    const messages = Array.from({ length: 18 }, (_, index) => ({
      id: index + 1,
      session_id: 1,
      role: index % 2 === 0 ? "user" : "assistant",
      content: `${index % 2 === 0 ? "User" : "Assistant"} message ${index + 1}`,
      sequence_number: index + 1,
      created_at: `2026-09-07T09:${String(index).padStart(2, "0")}:00Z`,
      citations: [],
    }));
    installFetch({ ...session, messages });

    render(<App />);

    expect(await screen.findByText("Assistant message 18")).toBeVisible();
    const shell = document.querySelector(".app-shell") as HTMLElement;
    const globalNav = document.querySelector(".sidebar") as HTMLElement;
    const main = screen.getByTestId("chat-pane").closest("main") as HTMLElement;
    const chatLayout = screen.getByTestId("chat-pane").closest(".chat-layout") as HTMLElement;
    const sessionPane = screen.getByLabelText("Conversation sessions") as HTMLElement;
    const sessionList = sessionPane.querySelector(".conversation-list") as HTMLElement;
    const chatPane = screen.getByTestId("chat-pane");
    const history = screen.getByRole("log", { name: "Conversation history" });
    const composer = screen.getByRole("form", { name: "Question composer" });

    expect(getComputedStyle(shell).overflow).toBe("hidden");
    expect(getComputedStyle(shell).height).not.toBe("auto");
    expect(getComputedStyle(globalNav).height).not.toBe("auto");
    expect(getComputedStyle(globalNav).position).toBe("static");
    expect(getComputedStyle(globalNav).overflow).toBe("hidden");
    expect(getComputedStyle(main).overflow).toBe("hidden");
    expect(getComputedStyle(chatLayout).overflow).toBe("hidden");
    expect(getComputedStyle(sessionPane).overflow).toBe("hidden");
    expect(getComputedStyle(sessionPane).minHeight).toMatch(/^0(?:px)?$/);
    expect(getComputedStyle(sessionList).flexGrow).toBe("1");
    expect(getComputedStyle(sessionList).minHeight).toMatch(/^0(?:px)?$/);
    expect(getComputedStyle(sessionList).overflowY).toBe("auto");
    expect(getComputedStyle(chatPane).height).not.toBe("auto");
    expect(getComputedStyle(chatPane).overflow).toBe("hidden");
    const conversationScroll = screen.getByRole("log", { name: "Conversation history" });
    const chatHeader = chatPane.querySelector("header.chat-header") as HTMLElement;

    expect(chatPane).toHaveClass("chat-pane");
    expect(chatPane).toHaveClass("chat-pane--active");
    expect(conversationScroll).toHaveClass("conversation-scroll");
    expect(chatPane.firstElementChild).toBe(conversationScroll);
    expect(chatHeader.parentElement).toBe(conversationScroll);
    expect(chatHeader).toHaveClass("chat-header--compact");
    expect(history).toHaveClass("chat-history");
    expect(history.parentElement).toBe(chatPane);
    expect(composer.parentElement).toBe(chatPane);
    expect(history.nextElementSibling).toBe(composer);
    expect(history).toContainElement(screen.getByText("Assistant message 18"));
    expect(getComputedStyle(chatPane).display).toBe("flex");
    expect(getComputedStyle(chatPane).flexDirection).toBe("column");
    expect(getComputedStyle(history).flexGrow).toBe("1");
    expect(getComputedStyle(history).minHeight).toMatch(/^0(?:px)?$/);
    expect(getComputedStyle(history).overflowY).toBe("auto");
    expect(getComputedStyle(history).paddingBottom).toBe("24px");
    expect(getComputedStyle(composer).flexShrink).toBe("0");
    expect(getComputedStyle(composer).position).not.toBe("fixed");

    sessionList.scrollTop = 42;
    history.scrollTop = 84;
    expect(sessionList.scrollTop).toBe(42);
    expect(history.scrollTop).toBe(84);
  });

  it("keeps a larger hero for an empty conversation", async () => {
    installFetch();
    render(<App />);

    expect(await screen.findByText("Ask about your indexed knowledge.")).toBeVisible();
    const chatPane = screen.getByTestId("chat-pane");
    const conversationScroll = screen.getByRole("log", { name: "Conversation history" });
    const chatHeader = chatPane.querySelector("header.chat-header") as HTMLElement;

    expect(chatPane).toHaveClass("chat-pane--empty");
    expect(chatHeader).toHaveClass("chat-header--hero");
    expect(chatHeader.parentElement).toBe(conversationScroll);
    expect(getComputedStyle(chatHeader.querySelector("h1") as HTMLElement).fontSize).not.toBe(
      getComputedStyle(chatHeader.querySelector("h1") as HTMLElement).lineHeight,
    );
  });

  it("keeps the composer near two rows and grows only up to its bounded maximum", async () => {
    installFetch();
    render(<App />);

    const question = await screen.findByLabelText("Question");
    const composer = question.closest("form") as HTMLElement;
    const sendButton = screen.getByRole("button", { name: "Ask Knowvia" });
    expect(question).toHaveAttribute("rows", "2");
    expect(getComputedStyle(question).minHeight).toBe("68px");
    expect(getComputedStyle(question).maxHeight).toBe("156px");
    expect(getComputedStyle(question).resize).toBe("none");
    expect(getComputedStyle(composer).boxShadow).toBe("none");
    expect(getComputedStyle(composer).paddingTop).toBe("10px");
    expect(getComputedStyle(sendButton).minWidth).toBe("112px");
    expect(screen.getByText("Same session · Grounded evidence")).toBeVisible();

    Object.defineProperty(question, "scrollHeight", {
      configurable: true,
      value: 92,
    });
    fireEvent.change(question, { target: { value: "First line\nSecond line" } });
    await waitFor(() => expect(question).toHaveStyle({ height: "92px", overflowY: "hidden" }));

    Object.defineProperty(question, "scrollHeight", {
      configurable: true,
      value: 260,
    });
    fireEvent.change(question, { target: { value: "One\nTwo\nThree\nFour\nFive\nSix\nSeven" } });
    await waitFor(() => expect(question).toHaveStyle({ height: "156px", overflowY: "auto" }));
  });

  it("uses restrained sidebar tokens and a text-only brand", async () => {
    installFetch();
    render(<App />);

    await screen.findByText("Ask about your indexed knowledge.");
    const root = getComputedStyle(document.documentElement);
    const sidebar = document.querySelector(".sidebar") as HTMLElement;
    const activeNav = screen.getByRole("button", { name: "Chat" });

    expect(root.getPropertyValue("--ink").trim()).toBe("#182019");
    expect(root.getPropertyValue("--accent").trim()).toBe("#a8b39a");
    expect(root.getPropertyValue("--acid").trim()).toBe("");
    expect(getComputedStyle(sidebar).backgroundImage).not.toContain("gradient");
    expect(sidebar.querySelector(".brand-mark")).not.toBeInTheDocument();
    expect(sidebar.querySelector(".brand-lockup strong")).toHaveTextContent("Knowvia Agent");
    expect(sidebar.querySelector(".brand-lockup span")).toHaveTextContent("KNOWLEDGE WORKSPACE");
    expect(getComputedStyle(activeNav).backgroundImage).not.toContain("gradient");
    expect(getComputedStyle(activeNav).backgroundColor).not.toBe("rgb(216, 255, 67)");
  });

  it("follows new streamed content near the bottom without interrupting history reading", async () => {
    let controller: ReadableStreamDefaultController<Uint8Array> | undefined;
    const encoder = new TextEncoder();
    installFetch(
      undefined,
      new Response(
        new ReadableStream<Uint8Array>({
          start(nextController) {
            controller = nextController;
            nextController.enqueue(encoder.encode(
              streamFrame("execution_status", 1, { phase: "generating" }),
            ));
          },
        }),
        { headers: { "Content-Type": "text/event-stream" } },
      ),
    );

    const user = userEvent.setup();
    render(<App />);
    await screen.findByText("Ask about your indexed knowledge.");

    const history = screen.getByRole("log", { name: "Conversation history" });
    setHistoryMetrics(history, { scrollHeight: 1000, clientHeight: 400, scrollTop: 580 });
    fireEvent.scroll(history);
    await user.type(screen.getByLabelText("Question"), "Stream this answer");
    await user.click(screen.getByRole("button", { name: "Ask Knowvia" }));
    expect(await screen.findByText("Generating answer…")).toBeVisible();

    controller?.enqueue(encoder.encode(
      streamFrame("answer_delta", 2, { text: "First streamed chunk" }),
    ));
    expect(await screen.findByText("First streamed chunk")).toBeVisible();
    await waitFor(() => expect(history.scrollTop).toBe(1000));

    setHistoryMetrics(history, { scrollHeight: 1400, clientHeight: 400, scrollTop: 220 });
    fireEvent.scroll(history);
    controller?.enqueue(encoder.encode(
      streamFrame("answer_delta", 3, { text: " while reading history" }),
    ));
    expect(await screen.findByText("First streamed chunk while reading history")).toBeVisible();
    await waitFor(() => expect(history.scrollTop).toBe(220));

    controller?.enqueue(encoder.encode(
      streamFrame("done", 4, {
        message_id: 19,
        session_id: 1,
        title: "Stream this answer",
        updated_at: "2026-09-07T09:31:00Z",
        workflow_run_id: 91,
        insufficient_info: false,
        used_saved_memory: false,
      }),
    ));
    controller?.close();
    await waitFor(() => expect(screen.getByLabelText("Question")).toBeEnabled());
  });

  it("keeps the mobile conversation drawer controls with the bounded chat pane", async () => {
    installFetch();
    render(<App />);
    await screen.findByText("Ask about your indexed knowledge.");

    expect(screen.getByTestId("chat-pane")).toHaveClass("chat-pane");
    const menuButton = document.querySelector<HTMLButtonElement>(".chat-menu-button");
    expect(menuButton).not.toBeNull();
    fireEvent.click(menuButton!);
    expect(screen.getByLabelText("Conversation sessions")).toHaveClass("conversation-panel--open");
    const closeButton = document.querySelector<HTMLButtonElement>(".conversation-close");
    expect(closeButton).not.toBeNull();

    fireEvent.click(closeButton!);
    expect(screen.getByLabelText("Conversation sessions")).not.toHaveClass("conversation-panel--open");
  });

  it("gives Knowledge and Memory their own bounded route scroll owner", async () => {
    const sources = Array.from({ length: 24 }, (_, index) => ({
      id: index + 1,
      display_name: `indexed-source-${index + 1}.pdf`,
      source_kind: "pdf",
      status: "indexed",
      chunk_count: 12,
    }));
    installFetch(undefined, undefined, sources);
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Knowledge" }));
    const knowledgeSurface = screen.getByRole("heading", { name: "Knowledge" }).closest(".surface") as HTMLElement;
    const knowledgeMain = knowledgeSurface.closest("main") as HTMLElement;
    expect(getComputedStyle(knowledgeMain).overflow).toBe("hidden");
    expect(getComputedStyle(knowledgeSurface).flexGrow).toBe("1");
    expect(getComputedStyle(knowledgeSurface).height).toBe("100%");
    expect(getComputedStyle(knowledgeSurface).minHeight).toMatch(/^0(?:px)?$/);
    expect(getComputedStyle(knowledgeSurface).overflowY).toBe("auto");
    expect(knowledgeSurface.querySelector(".source-inventory")).toBeInTheDocument();
    expect(await screen.findByText("indexed-source-24.pdf")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Memory" }));
    const memorySurface = screen.getByRole("heading", { name: "Memory" }).closest(".surface") as HTMLElement;
    expect(getComputedStyle(memorySurface).flexGrow).toBe("1");
    expect(getComputedStyle(memorySurface).height).toBe("100%");
    expect(getComputedStyle(memorySurface).minHeight).toMatch(/^0(?:px)?$/);
    expect(getComputedStyle(memorySurface).overflowY).toBe("auto");
  });
});
