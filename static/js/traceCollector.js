let scrapingEnabled = false;

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
});


// Handle toggle change
document.addEventListener("change", (event) => {
    const toggle = event.target.closest("[data-ui-key='scraping-toggle-btn']");
    if (!toggle) return;

    scrapingEnabled = toggle.checked;

    // persist
    localStorage.setItem("scrapingEnabled", String(scrapingEnabled));

    // sync UI
    const header = toggle.closest(".admin-tool-cat-header");
    if (header) {
        header.classList.toggle("active", scrapingEnabled);
    }
});


// Click handler (ONLY when enabled)
document.addEventListener("click", (event) => {
    const message = event.target.closest(".msg.msg-ai");
    const session = document.querySelector(".active-session");
    if (!message) return;

    if (!scrapingEnabled) return;

    alert(`Session ID: ${session ? session.getAttribute("data-session-id") : "Unknown"}\nMessage ID: ${message ? message.getAttribute("data-db-id") : "Unknown"}`);
});