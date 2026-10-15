/* oxs_registry.js — frontend host API for OXS extensions.

Loaded once on every page by core/app.js. Provides ``window.__oxs`` which
extensions register UI elements through. The full UI surface (sidebar,
panels, slash commands, settings tabs) is wired in PR 4 alongside the admin
UI; PR 2 only ships the skeleton so extension ``on_enable`` hooks can call
into a defined surface without runtime errors.

Every registration call returns an ``unregister()`` function so the host
can tear down extensions on disable without leaving orphaned DOM nodes.
*/

(function () {
  "use strict";

  // ── Internal store ────────────────────────────────────────────────────
  const sidebarItems = [];   // [{id, icon, label, panelId}]
  const panels = [];         // [{id, title, sourceUrl}]
  const slashCommands = [];  // [{command, description, handler}]
  const settingsTabs = [];   // [{tab, title, sourceUrl}]
  const eventHandlers = new Map(); // eventName -> [handler]
  let nextRegId = 0;

  function nextId(prefix) {
    return prefix + "-" + (++nextRegId);
  }

  function log(level, ...args) {
    if (window.console && window.console[level]) {
      window.console[level]("[oxs]", ...args);
    }
  }

  // ── Public API ───────────────────────────────────────────────────────
  const api = {
    version: "1.0.0",

    // ── Sidebar ──────────────────────────────────────────────────────
    registerSidebarItem(item) {
      if (!item || !item.id) {
        log("warn", "registerSidebarItem: missing id");
        return () => {};
      }
      sidebarItems.push(item);
      log("info", "registered sidebar item", item.id);
      return () => {
        const i = sidebarItems.indexOf(item);
        if (i >= 0) sidebarItems.splice(i, 1);
      };
    },

    // ── Panel ───────────────────────────────────────────────────────
    registerPanel(item) {
      if (!item || !item.id) {
        log("warn", "registerPanel: missing id");
        return () => {};
      }
      panels.push(item);
      log("info", "registered panel", item.id);
      return () => {
        const i = panels.indexOf(item);
        if (i >= 0) panels.splice(i, 1);
      };
    },

    // ── Slash command ──────────────────────────────────────────────
    registerSlashCommand(spec) {
      if (!spec || !spec.command || typeof spec.handler !== "function") {
        log("warn", "registerSlashCommand: missing command or handler");
        return () => {};
      }
      slashCommands.push(spec);
      log("info", "registered slash command", spec.command);
      return () => {
        const i = slashCommands.indexOf(spec);
        if (i >= 0) slashCommands.splice(i, 1);
      };
    },

    // ── Settings tab ───────────────────────────────────────────────
    registerSettingsTab(spec) {
      if (!spec || !spec.tab) {
        log("warn", "registerSettingsTab: missing tab");
        return () => {};
      }
      settingsTabs.push(spec);
      log("info", "registered settings tab", spec.tab);
      return () => {};
    },

    // ── Settings read ──────────────────────────────────────────────
    getSetting(key, defaultValue) {
      // PR 2 stub: read from a flat window.__oxsSettings object the host
      // can populate from data/oxs/permissions/<id>.json. For now returns
      // the default so extension code never crashes on a missing key.
      if (
        window.__oxsSettings &&
        Object.prototype.hasOwnProperty.call(window.__oxsSettings, key)
      ) {
        return window.__oxsSettings[key];
      }
      return defaultValue;
    },

    setSetting(key, value) {
      if (!window.__oxsSettings) window.__oxsSettings = {};
      window.__oxsSettings[key] = value;
    },

    // ── Events ─────────────────────────────────────────────────────
    on(eventName, handler) {
      if (typeof handler !== "function") return () => {};
      if (!eventHandlers.has(eventName)) eventHandlers.set(eventName, []);
      eventHandlers.get(eventName).push(handler);
      return () => {
        const list = eventHandlers.get(eventName);
        if (!list) return;
        const idx = list.indexOf(handler);
        if (idx >= 0) list.splice(idx, 1);
      };
    },

    fire(eventName, payload) {
      const list = eventHandlers.get(eventName);
      if (!list) return;
      for (const h of list) {
        try {
          h(payload);
        } catch (e) {
          log("error", "event handler for", eventName, "threw:", e);
        }
      }
    },

    // ── Inspection (for the admin UI in PR 4) ──────────────────────
    _inspect() {
      return {
        sidebarItems: sidebarItems.slice(),
        panels: panels.slice(),
        slashCommands: slashCommands.slice(),
        settingsTabs: settingsTabs.slice(),
      };
    },

    // ── Toast helpers (re-export core's existing helpers) ────────────
    showToast(message, type) {
      if (window.__odysseus && typeof window.__odysseus.showToast === "function") {
        window.__odysseus.showToast(message, type || "info");
      } else {
        log("info", "toast:", message);
      }
    },

    showError(message) {
      if (window.__odysseus && typeof window.__odysseus.showError === "function") {
        window.__odysseus.showError(message);
      } else {
        log("error", message);
      }
    },
  };

  // Expose immediately. Extensions loaded later pick this up via
  // ``window.__oxs`` reference (they typically run after document load).
  window.__oxs = api;
})();