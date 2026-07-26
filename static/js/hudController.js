const validStates = new Set([
  "idle",
  "listening",
  "thinking",
  "speaking",
  "acting"
]);

let currentState = "idle";

function setHudState(nextState) {
  if (!validStates.has(nextState)) {
    console.warn(`Unknown HUD state: ${nextState}`);
    return;
  }

  currentState = nextState;

  const label = document.querySelector(".odysseus-hud__label");
  if (label) label.textContent = nextState;
  document.documentElement.dataset.odysseusState = nextState;

  window.dispatchEvent(
    new CustomEvent("odysseus:hud-state", {
      detail: { state: nextState }
    })
  );
}

function getHudState() {
  return currentState;
}

window.OdysseusHUD = {
  setState: setHudState,
  getState: getHudState
};

setHudState("idle");
