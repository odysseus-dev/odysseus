let scrapingEnabled = false;
let selectedMessages = new Set();

document.addEventListener("DOMContentLoaded", () => {
  const toggle = document.querySelector("[data-ui-key='scraping-toggle-btn']");
  const header = document.querySelector(".admin-tool-cat-header");

  // Load saved state
  const saved = localStorage.getItem("scrapingEnabled");

  if (saved !== null) {
    scrapingEnabled = saved === "true";
  } else {
    scrapingEnabled = false;
    localStorage.setItem("scrapingEnabled", "false");
  }

  // sync UI
  if (toggle) toggle.checked = scrapingEnabled;
  if (header) header.classList.toggle("active", scrapingEnabled);

  // Define buttons
  const successBtn = document.getElementById("adm-exportSuccessBtn");
  const failedBtn = document.getElementById("adm-exportFailedBtn");

  if (successBtn) {
    successBtn.addEventListener("click", () => {
      // Safely grab the current session ID at the time of the click
      const session = document.querySelector(".active-session");
      const currentSessionId = session
        ? session.getAttribute("data-session-id")
        : null;
      triggerTraceExport(currentSessionId, "success");
    });
  }

  if (failedBtn) {
    failedBtn.addEventListener("click", () => {
      // Safely grab the current session ID at the time of the click
      const session = document.querySelector(".active-session");
      const currentSessionId = session
        ? session.getAttribute("data-session-id")
        : null;
      triggerTraceExport(currentSessionId, "failed");
    });
  }
});

function selectMessages(message) {
  const dbId = message.getAttribute("data-db-id");

  let sibling = message.previousElementSibling;
  let closestUserMsg = null;

  while (sibling) {
    if (
      sibling.classList.contains("msg") &&
      sibling.classList.contains("msg-user")
    ) {
      closestUserMsg = sibling;
      break;
    }
    sibling = sibling.previousElementSibling;
  }

  const userDbId = closestUserMsg
    ? closestUserMsg.getAttribute("data-db-id")
    : null;
  const pairKey = JSON.stringify({ assistantId: dbId, userId: userDbId });

  if (!message.selected) {
    message.style.border = "2px solid green";
    message.selected = true;
    selectedMessages.add(pairKey);

    if (closestUserMsg) closestUserMsg.style.border = "2px solid green";
  } else {
    message.style.border = "";
    message.selected = false;
    selectedMessages.delete(pairKey);

    if (closestUserMsg) closestUserMsg.style.border = "";
  }
}

async function triggerTraceExport(sessionId, label) {
  if (!sessionId) {
    alert("No active session found to export from.");
    return;
  }

  if (selectedMessages.size === 0) {
    alert("Please select at least one message thread to export.");
    return;
  }

  const flatMessageIds = Array.from(selectedMessages).flatMap((item) => {
    const pair = JSON.parse(item);
    // Keys match what was saved in selectMessages
    return [pair.assistantId, pair.userId].filter((id) => id !== null);
  });

  const payload = {
    session_id: sessionId,
    message_ids: flatMessageIds,
    label: label,
    note: null,
  };

  const targetBtn = document.querySelector(`[data-label="${label}"]`);
  if (!targetBtn) {
    console.error(`Button with data-label="${label}" not found.`);
    return;
  }

  const originalText = targetBtn.innerText;

  try {
    targetBtn.disabled = true;
    targetBtn.innerText = "Sending...";

    const response = await fetch("/api/trace/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Export failed");

    // Download the JSON file
    const data = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(result.data, null, "\t")) || {};
    const downloadAnchorNode = document.createElement("a");
    downloadAnchorNode.setAttribute("href", data);
    downloadAnchorNode.setAttribute("download", `trace_${sessionId}_${label}.json`);
    document.body.appendChild(downloadAnchorNode);
    downloadAnchorNode.click();
    document.body.removeChild(downloadAnchorNode);
    
    alert(`Data marked as '${label}' exported successfully!`);

  } catch (error) {
    alert(`Failed to export: ${error.message}`);
  } finally {
    targetBtn.disabled = false;
    targetBtn.innerText = originalText;
  }
}

document.addEventListener("change", (event) => {
  const toggle = event.target.closest("[data-ui-key='scraping-toggle-btn']");
  if (!toggle) return;

  scrapingEnabled = toggle.checked;
  localStorage.setItem("scrapingEnabled", String(scrapingEnabled));

  const header = toggle.closest(".admin-tool-cat-header");
  if (header) {
    header.classList.toggle("active", scrapingEnabled);
  }
});

document.addEventListener("click", async (event) => {
  const message = event.target.closest(".msg.msg-ai");
  const session = document.querySelector(".active-session");

  if (!message) return;
  if (!scrapingEnabled) return;
  if (!session) {
    console.error("No active session found");
    return;
  }

  selectMessages(message);
});
