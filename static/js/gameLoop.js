// js/gameLoop.js
// Game loop controller — wires together gameLogic, renderer, and inputHandler
// using requestAnimationFrame with a fixed-tick accumulator.
//
// Exports:
//   init()     — entry point: resolves canvas#game-canvas, starts the loop
//   destroy()  — teardown: cancels rAF, removes listeners, nulls refs

import { TICK_RATE_MS } from './gameConfig.js';
import { initGame, tick, getState, resetGame } from './gameLogic.js';
import { render, drawGameOver } from './renderer.js';
import { attachInput, getQueuedDirection } from './inputHandler.js';

// ── Module-private state ──────────────────────────────────
// All state is scoped to this module — no global leakage.
let _canvas = null;
let _ctx = null;
let _scoreEl = null;
let _rafId = null;
let _accumulator = 0;
let _lastTime = 0;
let _running = false;
let _gameOver = false;
// Cached bound handler for clean teardown (avoids memory leak)
let _boundSpaceHandler = null;

// ── Internal helpers ──────────────────────────────────────

/**
 * Resolves the game canvas and its 2d context.
 * @returns {void}
 * @throws {Error} if #game-canvas element is not found
 */
function _getCanvas() {
  const el = document.getElementById('game-canvas');
  if (!el) {
    throw new Error('gameLoop.init(): no element with id "game-canvas" found');
  }
  _canvas = el;
  _ctx = el.getContext('2d');
}

/**
 * Resolves the optional score display element.
 * @returns {void}
 */
function _getScoreEl() {
  _scoreEl = document.getElementById('score-display') || null;
}

/**
 * Updates the score display element if it exists.
 * @param {number} score
 * @returns {void}
 */
function _updateScore(score) {
  if (_scoreEl) {
    _scoreEl.textContent = 'Score: ' + score;
  }
}

/**
 * Toggles the running/paused state.
 * On resume, resets lastTime and accumulator to avoid a large delta spike.
 * @returns {void}
 */
function _togglePause() {
  _running = !_running;
  if (_running) {
    _lastTime = 0;
    _accumulator = 0;
  }
}

/**
 * Restarts the game from scratch.
 * @returns {void}
 */
function _restart() {
  resetGame();
  _running = true;
  _gameOver = false;
  _lastTime = 0;
  _accumulator = 0;
}

/**
 * Handles the Space key: restarts if game-over, otherwise toggles pause.
 * @param {KeyboardEvent} e
 * @returns {void}
 */
function _spaceHandler(e) {
  if (e.key !== ' ' && e.code !== 'Space') {
    return;
  }
  e.preventDefault();
  if (_gameOver) {
    _restart();
  } else {
    _togglePause();
  }
}

// ── Main loop ─────────────────────────────────────────────

/**
 * The core requestAnimationFrame callback.
 * Uses a fixed-tick accumulator to step game logic at a consistent rate.
 * @param {DOMHighResTimeStamp} timestamp
 * @returns {void}
 */
function _loop(timestamp) {
  // Always schedule next frame — keeps rendering alive during pause/game-over
  _rafId = requestAnimationFrame(_loop);

  if (!_running) {
    // Still render the current state even while paused/game-over
    const state = getState();
    _renderFrame(state);
    return;
  }

  // Bootstrap lastTime on first frame or after resume
  if (_lastTime === 0) {
    _lastTime = timestamp;
    return;
  }

  const delta = timestamp - _lastTime;
  _lastTime = timestamp;
  _accumulator += delta;

  // Fixed-tick accumulator: advance game logic in TICK_RATE_MS steps
  while (_accumulator >= TICK_RATE_MS) {
    _accumulator -= TICK_RATE_MS;
    const st = getState();
    if (!st.gameOver) {
      const dir = getQueuedDirection(st.currentDirection);
      tick(dir);
    }
  }

  const state = getState();
  _renderFrame(state);

  // Detect game-over transition
  if (state.gameOver && !_gameOver) {
    _gameOver = true;
    _running = false;
  }
}

/**
 * Renders the current game state and score, plus game-over overlay if needed.
 * @param {object} state
 * @returns {void}
 */
function _renderFrame(state) {
  render(_ctx, state);
  _updateScore(state.score);
  if (state.gameOver) {
    drawGameOver(_ctx, state.score);
  }
}

// ── Public API ────────────────────────────────────────────

/**
 * Initialises the game loop: resolves DOM elements, starts the
 * input handler and kicks off requestAnimationFrame.
 * Safe to call multiple times — previous listeners and rAF are cleaned up.
 *
 * @returns {void}
 */
export function init() {
  _getCanvas();
  _getScoreEl();

  initGame();

  // No callback needed — we use getQueuedDirection inside the loop
  attachInput(null);

  // Clean up previous space handler if present
  if (_boundSpaceHandler) {
    window.removeEventListener('keydown', _boundSpaceHandler);
  }
  _boundSpaceHandler = _spaceHandler;
  window.addEventListener('keydown', _boundSpaceHandler);

  // Cancel any existing rAF before starting fresh
  if (_rafId) {
    cancelAnimationFrame(_rafId);
  }

  _running = true;
  _gameOver = false;
  _lastTime = 0;
  _accumulator = 0;

  _rafId = requestAnimationFrame(_loop);
}

/**
 * Tears down the game loop: stops execution, cancels the animation
 * frame, removes the keyboard listener, and nulls all internal refs.
 *
 * @returns {void}
 */
export function destroy() {
  _running = false;

  if (_rafId) {
    cancelAnimationFrame(_rafId);
    _rafId = null;
  }

  if (_boundSpaceHandler) {
    window.removeEventListener('keydown', _boundSpaceHandler);
    _boundSpaceHandler = null;
  }

  _canvas = null;
  _ctx = null;
  _scoreEl = null;
}
