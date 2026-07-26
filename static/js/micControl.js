import {
  startRecording,
  stopRecording,
  getIsRecording,
} from "./voiceRecorder.js";

function syncButton(button) {
  const recording = getIsRecording();

  button.classList.toggle("is-recording", recording);
  button.setAttribute("aria-pressed", String(recording));
  button.setAttribute(
    "aria-label",
    recording ? "Stop voice recording" : "Start voice recording"
  );
  button.title = recording ? "Stop recording" : "Push to talk";
  button.textContent = recording ? "■" : "🎤";
}

function initializeMicrophoneButton() {
  const button = document.getElementById("voice-record-btn");
  if (!button || button.dataset.connected === "true") return;

  button.dataset.connected = "true";
  syncButton(button);

  button.addEventListener("click", () => {
    if (getIsRecording()) {
      stopRecording();

      // Give MediaRecorder.onstop time to update the recording state.
      window.setTimeout(() => syncButton(button), 100);
      window.setTimeout(() => syncButton(button), 500);
      window.setTimeout(() => syncButton(button), 1000);
      return;
    }

    startRecording(
      null,
      (message) => console.info(message),
      (message) => console.error(message)
    );

    // Microphone access starts asynchronously.
    window.setTimeout(() => syncButton(button), 300);
    window.setTimeout(() => syncButton(button), 800);
    window.setTimeout(() => syncButton(button), 1500);
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initializeMicrophoneButton);
} else {
  initializeMicrophoneButton();
}
