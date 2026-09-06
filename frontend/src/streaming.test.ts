import { describe, expect, it, vi } from "vitest";

import {
  parseConversationStreamFrames,
  streamConversationMessage,
  type ConversationStreamEvent,
} from "./api";

function eventFrame(eventType: string, sequence: number, payload: unknown): string {
  return `event: ${eventType}\r\ndata: ${JSON.stringify({
    run_id: "run-1",
    sequence,
    payload,
  })}\r\n\r\n`;
}

describe("conversation SSE client", () => {
  it("parses event frames and preserves multilingual payloads", () => {
    const frames = parseConversationStreamFrames(
      eventFrame("answer_delta", 1, { text: "繁體中文 🚀" }),
    );

    expect(frames[0]).toMatchObject({
      event_type: "answer_delta",
      run_id: "run-1",
      sequence: 1,
      payload: { text: "繁體中文 🚀" },
    });
  });

  it("uses TextDecoder streaming mode across UTF-8 and SSE frame boundaries", async () => {
    const encoder = new TextEncoder();
    const bytes = encoder.encode(
      eventFrame("execution_status", 1, { phase: "generating" }) +
        eventFrame("answer_delta", 2, { text: "繁體中文" }) +
        eventFrame("done", 3, { message_id: 8, session_id: 4 }),
    );
    const response = new Response(
      new ReadableStream<Uint8Array>({
        start(controller) {
          for (let index = 0; index < bytes.length; index += 2) {
            controller.enqueue(bytes.slice(index, index + 2));
          }
          controller.close();
        },
      }),
      { headers: { "Content-Type": "text/event-stream" } },
    );
    const events: ConversationStreamEvent[] = [];
    const request = vi.fn().mockResolvedValue(response);

    await streamConversationMessage(
      4,
      "用中文回答",
      (event) => events.push(event),
      request,
    );

    expect(request).toHaveBeenCalledWith(
      "/api/conversations/4/messages/stream",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ query: "用中文回答" }) }),
    );
    expect(events.map((event) => event.event_type)).toEqual([
      "execution_status",
      "answer_delta",
      "done",
    ]);
    expect(events[1].payload.text).toBe("繁體中文");
  });

  it("rejects a response whose event sequence skips a number", async () => {
    const response = new Response(
      eventFrame("done", 2, { message_id: 8, session_id: 4 }),
      { headers: { "Content-Type": "text/event-stream" } },
    );

    await expect(
      streamConversationMessage(4, "query", () => undefined, vi.fn().mockResolvedValue(response)),
    ).rejects.toThrow("out-of-order");
  });

  it("rejects a completed event for another session", async () => {
    const response = new Response(
      eventFrame("done", 1, { message_id: 8, session_id: 99 }),
      { headers: { "Content-Type": "text/event-stream" } },
    );

    await expect(
      streamConversationMessage(4, "query", () => undefined, vi.fn().mockResolvedValue(response)),
    ).rejects.toThrow("another session");
  });

  it("waits for each render-critical event before dispatching the next frame", async () => {
    const response = new Response(
      eventFrame("execution_status", 1, { phase: "generating" }) +
        eventFrame("answer_delta", 2, { text: "first" }) +
        eventFrame("answer_delta", 3, { text: "second" }) +
        eventFrame("done", 4, { message_id: 8, session_id: 4 }),
      { headers: { "Content-Type": "text/event-stream" } },
    );
    const timeline: string[] = [];

    await streamConversationMessage(
      4,
      "query",
      async (event) => {
        timeline.push(`${event.event_type}:start`);
        if (event.event_type === "answer_delta") {
          await new Promise<void>((resolve) => setTimeout(resolve, 0));
        }
        timeline.push(`${event.event_type}:end`);
      },
      vi.fn().mockResolvedValue(response),
    );

    expect(timeline).toEqual([
      "execution_status:start",
      "execution_status:end",
      "answer_delta:start",
      "answer_delta:end",
      "answer_delta:start",
      "answer_delta:end",
      "done:start",
      "done:end",
    ]);
  });
});
