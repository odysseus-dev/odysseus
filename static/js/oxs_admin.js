/* oxs_admin.js — admin UI for OXS extensions.

Mounted inside the Settings page as the "Extensions" tab. Provides
list + per-extension detail with capability matrix, enable/disable/
uninstall actions, and an audit viewer.

All requests use fetch() with credentials so the operator's session
cookie is sent. Errors render in a status banner at the top of the panel.
*/

(function () {
  "use strict";

  const api = "/api/oxs";
  let cached = null;

  // ── Render helpers ──────────────────────────────────────────────────

  function el(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (k === "class") node.className = v;
      else if (k === "html") node.innerHTML = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2).toLowerCase(), v);
      else node.setAttribute(k, v);
    }
    for (const c of children.flat()) {
      if (c == null || c === false) continue;
      node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
    return node;
  }

  function badge(text, title) {
    const span = el("span", { class: "oxs-badge", title: title || "" }, text);
    return span;
  }

  function status(text, kind = "info") {
    const banner = el("div", { class: `oxs-banner oxs-banner-${kind}` }, text);
    return banner;
  }

  // ── API call helpers ─────────────────────────────────────────────────

  async function apiGet(path) {
    const res = await fetch(`${api}${path}`, { credentials: "same-origin" });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || body.message || `${res.status}`);
    return body;
  }

  async function apiPost(path) {
    const res = await fetch(`${api}${path}`, {
      method: "POST",
      credentials: "same-origin",
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || body.message || `${res.status}`);
    return body;
  }

  // ── Render: list ───────────────────────────────────────────────────

  function renderRow(ext) {
    const tr = el("tr");
    tr.appendChild(el("td", { class: "oxs-id" }, ext.id));
    tr.appendChild(el("td", {}, ext.name || ""));
    tr.appendChild(el("td", {}, ext.version || ""));
    const sourceTd = el("td", {}, badge(ext.source, "Where this extension lives"));
    tr.appendChild(sourceTd);
    const enabledTd = el(
      "td",
      {},
      ext.is_enabled ? badge("enabled", "Currently running") : badge("disabled", "Not loaded"),
    );
    tr.appendChild(enabledTd);
    const capsTd = el("td", {});
    capsTd.appendChild(
      el(
        "span",
        { class: "oxs-caps" },
        `${ext.stats.tools}T · ${ext.stats.mcp_tools}M · ${ext.stats.routes}R · ${ext.stats.webhooks}W`,
      ),
    );
    tr.appendChild(capsTd);
    if (ext.violations && ext.violations.length) {
      tr.appendChild(el("td", {}, badge(`${ext.violations.length} violations`, "Capability violations observed")));
    } else {
      tr.appendChild(el("td", {}, ""));
    }

    const actions = el("td", { class: "oxs-actions" });
    if (!ext.is_enabled) {
      actions.appendChild(el("button", { onClick: () => doAction(ext.id, "enable") }, "Enable"));
    } else {
      actions.appendChild(el("button", { onClick: () => doAction(ext.id, "disable") }, "Disable"));
    }
    actions.appendChild(el("button", { onClick: () => doAction(ext.id, "reload") }, "Reload"));
    actions.appendChild(
      el("button", { onClick: () => showWebhooks(ext) }, "Webhooks"),
    );
    actions.appendChild(
      el("button", { class: "oxs-danger", onClick: () => doUninstall(ext) }, "Uninstall"),
    );
    tr.appendChild(actions);
    return tr;
  }

  function renderList(extensions) {
    const table = el("table", { class: "oxs-table" });
    const thead = el("thead");
    thead.appendChild(
      el(
        "tr",
        {},
        el("th", {}, "ID"),
        el("th", {}, "Name"),
        el("th", {}, "Version"),
        el("th", {}, "Source"),
        el("th", {}, "State"),
        el("th", {}, "Stats"),
        el("th", {}, "Issues"),
        el("th", {}, "Actions"),
      ),
    );
    table.appendChild(thead);
    const tbody = el("tbody");
    for (const ext of extensions) tbody.appendChild(renderRow(ext));
    table.appendChild(tbody);
    return table;
  }

  // ── Render: webhooks modal ──────────────────────────────────────────

  function showWebhooks(ext) {
    apiGet(`/extensions/${encodeURIComponent(ext.id)}/webhooks`)
      .then((data) => {
        const body = el("div", { class: "oxs-modal" });
        body.appendChild(el("h3", {}, `Webhooks for ${ext.id}`));
        if (!data.webhooks.length) {
          body.appendChild(el("p", {}, "(none installed)"));
        }
        for (const wh of data.webhooks) {
          const block = el("div", { class: "oxs-webhook" });
          block.appendChild(el("p", {}, el("strong", {}, "Path suffix: "), wh.path_suffix));
          block.appendChild(el("p", {}, el("strong", {}, "Event filter: "), wh.event_filter.join(", ") || "(none)"));
          block.appendChild(el("p", {}, el("strong", {}, "Registered: "), wh.registered_at));
          const tokenBlock = el("p", { class: "oxs-token" });
          tokenBlock.appendChild(el("strong", {}, "Raw token (shown once): "));
          tokenBlock.appendChild(el("code", {}, wh.raw_token || "(unavailable)"));
          block.appendChild(tokenBlock);
          block.appendChild(el("p", { class: "oxs-hint" }, `Full URL: /api/ext/${ext.id}/webhook/${wh.raw_token || "<token>"}`));
          body.appendChild(block);
        }
        openModal(body);
      })
      .catch((err) => showStatus(err.message, "error"));
  }

  function openModal(content) {
    let modal = document.getElementById("oxs-modal");
    if (!modal) {
      modal = el("div", { id: "oxs-modal", class: "oxs-modal-backdrop" });
      modal.addEventListener("click", (e) => {
        if (e.target === modal) modal.remove();
      });
      document.body.appendChild(modal);
    }
    modal.innerHTML = "";
    const inner = el("div", { class: "oxs-modal-inner" });
    inner.appendChild(el("button", { class: "oxs-modal-close", onClick: () => modal.remove() }, "Close"));
    inner.appendChild(content);
    modal.appendChild(inner);
  }

  function showStatus(text, kind = "info") {
    const banner = document.getElementById("oxs-status");
    if (banner) {
      banner.className = `oxs-banner oxs-banner-${kind}`;
      banner.textContent = text;
    } else {
      // Fall back to console — the panel may not be mounted yet.
      console.log("[oxs]", text);
    }
  }

  // ── Actions ─────────────────────────────────────────────────────────

  async function doAction(id, action) {
    try {
      await apiPost(`/extensions/${encodeURIComponent(id)}/${action}`);
      showStatus(`${action} ${id} succeeded`, "ok");
      await refresh();
    } catch (err) {
      showStatus(`${action} ${id} failed: ${err.message}`, "error");
    }
  }

  async function doUninstall(ext) {
    if (!confirm(`Uninstall ${ext.id}? Settings and storage are kept on disk; the row is removed from the registry.`)) {
      return;
    }
    await doAction(ext.id, "uninstall");
  }

  async function refresh() {
    const banner = document.getElementById("oxs-status");
    if (banner) {
      banner.className = "oxs-banner";
      banner.textContent = "Loading…";
    }
    try {
      const data = await apiGet("/extensions");
      cached = data.extensions;
      const listEl = document.getElementById("oxs-list");
      if (listEl) {
        listEl.innerHTML = "";
        listEl.appendChild(renderList(cached));
      }
      if (banner) {
        banner.className = "oxs-banner oxs-banner-ok";
        banner.textContent = `${cached.length} extension(s) installed`;
      }
    } catch (err) {
      if (banner) {
        banner.className = "oxs-banner oxs-banner-error";
        banner.textContent = `Failed to load: ${err.message}`;
      }
    }
  }

  // ── Public mount ────────────────────────────────────────────────────

  window.OXS_ADMIN = {
    mount(rootId) {
      const root = document.getElementById(rootId);
      if (!root) return false;
      root.innerHTML = "";
      const banner = el("div", { id: "oxs-status", class: "oxs-banner" }, "Loading…");
      const list = el("div", { id: "oxs-list" });
      const toolbar = el("div", { class: "oxs-toolbar" });
      toolbar.appendChild(
        el("button", { onClick: () => apiPost("/rescan").then(refresh).catch((err) => showStatus(err.message, "error")) }, "Rescan"),
      );
      toolbar.appendChild(
        el("span", { class: "oxs-hint" }, "Discover new extensions in user/bundled dirs and entry-points"),
      );
      root.appendChild(banner);
      root.appendChild(toolbar);
      root.appendChild(list);
      refresh();
      return true;
    },
  };
})();