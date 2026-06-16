import { describe, expect, it } from "vitest";

import { DEFAULT_SERVER_URL, getConfiguredServerUrl, normalizeServerUrl } from "../src/api/client";

describe("normalizeServerUrl", () => {
  it("strips a trailing slash", () => {
    expect(normalizeServerUrl("http://127.0.0.1:7860/")).toBe("http://127.0.0.1:7860");
  });

  it("drops path, query, and hash", () => {
    expect(normalizeServerUrl("http://host:7860/some/path?q=1#frag")).toBe("http://host:7860");
  });

  it("trims surrounding whitespace", () => {
    expect(normalizeServerUrl("  http://host:7860  ")).toBe("http://host:7860");
  });

  it("falls back to the default for an empty string", () => {
    expect(normalizeServerUrl("")).toBe(DEFAULT_SERVER_URL);
  });

  it("preserves https and a non-default port", () => {
    expect(normalizeServerUrl("https://odysseus.example.com:8443/")).toBe(
      "https://odysseus.example.com:8443",
    );
  });

  it("throws on an unparseable URL", () => {
    expect(() => normalizeServerUrl("not a url")).toThrow();
  });
});

describe("getConfiguredServerUrl", () => {
  it("returns the normalized default when nothing is configured", () => {
    // The vscode stub's getConfiguration().get() returns the provided default.
    expect(getConfiguredServerUrl()).toBe(DEFAULT_SERVER_URL);
  });
});
