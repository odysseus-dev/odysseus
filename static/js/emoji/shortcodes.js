// static/js/emoji/shortcodes.js
//
// Convert `:shortcode:` emoji (the ":blush:", ":rocket:" style that many LLMs
// emit) into the Unicode emoji, so chat renders 😊 instead of literal text.
// Pure — no DOM, no fetch — so it can be unit-tested under node.

export const EMOJI_SHORTCODES = {
  smile: '😄', smiley: '😃', grin: '😁', laughing: '😆', joy: '😂',
  rofl: '🤣', blush: '😊', wink: '😉', heart_eyes: '😍', smirk: '😏',
  thinking: '🤔', neutral_face: '😐', expressionless: '😑', sweat_smile: '😅',
  sob: '😭', cry: '😢', disappointed: '😞', confused: '😕', flushed: '😳',
  sunglasses: '😎', heart: '❤️', broken_heart: '💔', sparkling_heart: '💖',
  thumbsup: '👍', '+1': '👍', thumbsdown: '👎', '-1': '👎', ok_hand: '👌',
  clap: '👏', wave: '👋', pray: '🙏', muscle: '💪', point_right: '👉',
  fire: '🔥', tada: '🎉', sparkles: '✨', star: '⭐', star2: '🌟',
  zap: '⚡', boom: '💥', rocket: '🚀', bulb: '💡', warning: '⚠️',
  white_check_mark: '✅', heavy_check_mark: '✔️', x: '❌', no_entry: '⛔',
  question: '❓', exclamation: '❗', eyes: '👀', tongue: '👅',
  microphone: '🎤', mag: '🔍', lock: '🔒', key: '🔑', gear: '⚙️',
  hourglass: '⏳', alarm_clock: '⏰', calendar: '📅', email: '📧',
  pencil: '✏️', memo: '📝', book: '📖', books: '📚', package: '📦',
  computer: '💻', bug: '🐛', wrench: '🔧', hammer: '🔨', chart_with_upwards_trend: '📈',
};

const _SHORTCODE_RE = /:([a-z0-9_+-]+):/gi;

export function replaceEmojiShortcodes(text) {
  if (!text) return text;
  return text.replace(_SHORTCODE_RE, (match, name) =>
    Object.prototype.hasOwnProperty.call(EMOJI_SHORTCODES, name.toLowerCase())
      ? EMOJI_SHORTCODES[name.toLowerCase()]
      : match,
  );
}
