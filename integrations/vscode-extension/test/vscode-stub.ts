// Minimal `vscode` module stub for unit tests (the real one only exists in the
// extension host). Just enough surface for the pure modules under test to import.
export const workspace = {
  getConfiguration: () => ({
    get: <T>(_key: string, def?: T): T | undefined => def,
    update: async () => undefined,
  }),
};
export const ConfigurationTarget = { Global: 1, Workspace: 2, WorkspaceFolder: 3 };
export const window = {
  createOutputChannel: () => ({ appendLine() {}, append() {}, dispose() {} }),
};
export const Uri = { parse: (s: string) => ({ toString: () => s }) };
export const env = { openExternal: async () => true };

export default { workspace, ConfigurationTarget, window, Uri, env };
