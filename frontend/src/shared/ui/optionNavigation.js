// Move one place through a wrapping option list. -1 represents no active option.
export function stepWrappedOption(index, count, direction) {
  if (!count) return -1;
  if (index === -1) return direction < 0 ? count - 1 : 0;
  return (index + direction + count) % count;
}
