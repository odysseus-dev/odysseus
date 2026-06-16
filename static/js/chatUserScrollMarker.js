//
// Scroll Marker component
//
function createController(options = {}) {

  const config = {
    ...options
  }

  function initPreviewMarkers(root, track) {
    if (!root) return;
    if (!track) return;

    positionTrack(track);

    const msgs = root.querySelectorAll(".msg-user");

    const chat = document.querySelector("#chat-history");
    if (!chat) return;

    const scrollRange = chat.scrollHeight - chat.clientHeight;
    if (scrollRange <= 0) {
      track.innerHTML = "";
      return;
    }

    const trackHeight = track.clientHeight || chat.clientHeight;

    const thumbHeight = (chat.clientHeight / chat.scrollHeight) * trackHeight;
    const usableTrack = trackHeight - thumbHeight;

    track.innerHTML = "";

    msgs.forEach((msg) => {
      const chatRect = chat.getBoundingClientRect();
      const msgRect = msg.getBoundingClientRect();

      const msgTopInScrollSpace =
        (msgRect.top - chatRect.top) + chat.scrollTop;

      let desiredScrollTop = msgTopInScrollSpace - chat.clientHeight / 2;
      desiredScrollTop = Math.max(0, Math.min(desiredScrollTop, scrollRange));

      const thumbTop = (desiredScrollTop / scrollRange) * usableTrack;
      const markerY = thumbTop + thumbHeight / 2;

      const marker = document.createElement("div");
      marker.className = "scroll-marker";
      marker.style.top = `${markerY}px`;

      marker.onclick = () => {
        chat.scrollTo({ top: desiredScrollTop, behavior: "instant" });
      };

      track.appendChild(marker);
    });
  }
  function getThemeColor(varName, alpha = 1) {
    const value = getComputedStyle(document.documentElement)
      .getPropertyValue(varName)
      .trim();

    if (!value) {
      return null;
    }

    if (alpha === 1) return value;

    return `color-mix(in srgb, ${value} ${alpha * 100}%, transparent)`;
  }

  function positionTrack(track) {
    const chat = document.querySelector("#chat-history");
    if (!chat) return;
    // give ~20px y margin for indiv. browser rendering of scroll arrow + padding
    const trackHeight = track.clientHeight || chat.clientHeight;
    const thumbHeight = (chat.clientHeight / chat.scrollHeight) * trackHeight;
    const ua = navigator.userAgent.toLowerCase();

    // macOS, iOS, Android, and overlay scrollbars do not use arrow buttons
    const hasNoArrows = ua.includes("macintosh") ||
      ua.includes("ipad") ||
      ua.includes("iphone") ||
      ua.includes("android");

    const yMargin = (hasNoArrows ? 0 : 16 ) + thumbHeight / 2;
    const xMargin = 6;
    const rect = chat.getBoundingClientRect();

    track.style.position = "fixed";
    track.style.top = rect.top + yMargin + "px";
    track.style.left = (rect.right - xMargin) + "px";
    track.style.width = "20px";
    track.style.height = rect.height - 2 * yMargin + "px";
  }

  function onInit() {
    const chat = document.querySelector("#chat-history");
    if (!chat) return;

    const root = chat.shadowRoot || chat;

    const cs = getComputedStyle(chat);
    if (cs.position === "static") {
      chat.style.position = "relative";
    }

    let track = root.getElementById?.("scroll-marker-track") ||
      root.querySelector("#scroll-marker-track");

    let redrawTimeout = null;
    if (!track) {
      track = document.createElement("div");
      track.id = "scroll-marker-track";

      const container = document.querySelector("#chat-container");
      container.appendChild(track);

      // Handle redraw on container resizing
      const resizeObserver = new ResizeObserver(() => {
        clearTimeout(redrawTimeout);
        redrawTimeout = setTimeout(() => {
          positionTrack(track);
          initPreviewMarkers(root, track);
        }, 300);
      });
      resizeObserver.observe(container);

      // Handle redraw on window resizing
      window.addEventListener("resize", () => {
        positionTrack(track);
        initPreviewMarkers(root, track);
      });
    }

    // Handle redraw on content mutations
    const observer = new MutationObserver(() => {
      clearTimeout(redrawTimeout);
      redrawTimeout = setTimeout(() => {
        initPreviewMarkers(root, track);
      }, 300);
    });

    observer.observe(root, {
      childList: true,
      subtree: true,
      characterData: true
    });

    initPreviewMarkers(root, track);
  }
  return {
    init: () => {
      if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", onInit);
      } else {
        onInit();
      }
    }
  };
}

export function createMarkerPlugin(options = {}) {
  return createController(options);
}
