import { describe, expect, it } from "vitest";

import { consumeSseBuffer } from "../src/api/sse";

describe("consumeSseBuffer", () => {
  it("parses a single complete event and leaves no remainder", () => {
    const { events, remainder } = consumeSseBuffer("data: hello\n\n");
    expect(events).toEqual([{ event: "message", data: "hello" }]);
    expect(remainder).toBe("");
  });

  it("parses multiple events in one chunk", () => {
    const { events } = consumeSseBuffer("data: one\n\ndata: two\n\n");
    expect(events.map((e) => e.data)).toEqual(["one", "two"]);
  });

  it("keeps an incomplete trailing event as the remainder", () => {
    const { events, remainder } = consumeSseBuffer("data: done\n\ndata: partial");
    expect(events).toEqual([{ event: "message", data: "done" }]);
    expect(remainder).toBe("data: partial");
  });

  it("honours a custom event: type", () => {
    const { events } = consumeSseBuffer("event: error\ndata: boom\n\n");
    expect(events[0]).toEqual({ event: "error", data: "boom" });
  });

  it("ignores comment lines starting with ':'", () => {
    const { events } = consumeSseBuffer(":keep-alive\ndata: ok\n\n");
    expect(events).toEqual([{ event: "message", data: "ok" }]);
  });

  it("joins multi-line data fields with newlines", () => {
    const { events } = consumeSseBuffer("data: a\ndata: b\n\n");
    expect(events[0].data).toBe("a\nb");
  });

  it("normalizes CRLF line endings", () => {
    const { events, remainder } = consumeSseBuffer("data: hi\r\n\r\n");
    expect(events).toEqual([{ event: "message", data: "hi" }]);
    expect(remainder).toBe("");
  });

  it("passes the [DONE] sentinel through as data", () => {
    const { events } = consumeSseBuffer("data: [DONE]\n\n");
    expect(events[0].data).toBe("[DONE]");
  });

  it("returns no events for an empty buffer", () => {
    expect(consumeSseBuffer("")).toEqual({ events: [], remainder: "" });
  });
});
