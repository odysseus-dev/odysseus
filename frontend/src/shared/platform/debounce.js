/** A trailing debounce with an explicit cancellation method. */
export function debounceTrailing(callback, delayMs) {
  let timer = null;
  const debounced = function (...args) {
    if (timer !== null) clearTimeout(timer);
    const context = this;
    timer = setTimeout(() => {
      timer = null;
      callback.apply(context, args);
    }, delayMs);
  };
  debounced.cancel = () => {
    if (timer !== null) clearTimeout(timer);
    timer = null;
  };
  return debounced;
}
