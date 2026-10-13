/** Cooperative render generations shared by synchronous and future async renderers. */
export function createRenderGeneration() {
  let generation = 0;
  return {
    begin() {
      const id = ++generation;
      return {
        id,
        isCurrent: () => id === generation,
        cancel: () => { if (id === generation) generation += 1; },
      };
    },
    invalidate() { generation += 1; },
  };
}
