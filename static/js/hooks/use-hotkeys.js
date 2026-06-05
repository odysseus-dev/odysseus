export function registerHotkeys({
  sendMessage,
  stopGeneration,
  focusInput,
}) {
  function handler(event) {
    const active = document.activeElement;
    const isTextarea =
      active &&
      (
        active.tagName === "TEXTAREA" ||
        active.tagName === "INPUT"
      );

    // Ctrl+Enter => send
    if (event.ctrlKey && event.key === "Enter") {
      event.preventDefault();

      if (isTextarea) {
        sendMessage();
      }
    }

    // Ctrl+K => focus input
    if (
      event.ctrlKey &&
      event.key.toLowerCase() === "k"
    ) {
      event.preventDefault();
      focusInput();
    }

    // Escape => stop generation
    if (event.key === "Escape") {
      stopGeneration();
    }
  }

  window.addEventListener("keydown", handler);

  return () => {
    window.removeEventListener("keydown", handler);
  };
}
