/**
 * Titan — block legacy Cookbook serve API calls (fetch layer).
 */
(function () {
  "use strict";

  const GONE_PATHS = [
    "/api/model/serve",
    "/api/cookbook/rebuild-engine",
    "/api/cookbook/tasks/status",
    "/api/codex/cookbook/serve",
  ];
  const GONE_PREFIXES = ["/api/codex/cookbook/stop/"];

  function isGoneUrl(url) {
    if (!url) return false;
    if (GONE_PATHS.some((p) => url.includes(p))) return true;
    return GONE_PREFIXES.some((p) => url.includes(p));
  }

  const orig = window.fetch.bind(window);
  window.fetch = function (input, init) {
    const url = typeof input === "string" ? input : (input && input.url) || "";
    if (isGoneUrl(url)) {
      return Promise.resolve(
        new Response(
          JSON.stringify({ detail: "Cookbook serve was removed. Use Titan Model Hub.", removed: true }),
          { status: 410, headers: { "Content-Type": "application/json" } },
        ),
      );
    }
    return orig(input, init);
  };
})();
