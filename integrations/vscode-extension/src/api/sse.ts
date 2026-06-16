export interface SseEvent {
  event: string;
  data: string;
}

export interface SseParseResult {
  events: SseEvent[];
  remainder: string;
}

export function consumeSseBuffer(buffer: string): SseParseResult {
  const events: SseEvent[] = [];
  const normalized = buffer.replace(/\r\n/g, "\n");
  const blocks = normalized.split("\n\n");
  const remainder = blocks.pop() ?? "";

  for (const block of blocks) {
    if (!block.trim()) {
      continue;
    }

    let event = "message";
    const data: string[] = [];

    for (const line of block.split("\n")) {
      if (line.startsWith(":")) {
        continue;
      }
      if (line.startsWith("event:")) {
        event = line.slice("event:".length).trim() || "message";
        continue;
      }
      if (line.startsWith("data:")) {
        data.push(line.slice("data:".length).trimStart());
      }
    }

    events.push({ event, data: data.join("\n") });
  }

  return { events, remainder };
}
