export const MODEL_INFO = {
  // Model pricing table — per million tokens
  // Model info: pricing (per 1M tokens) + context window length

  // --- Anthropic ---
  'claude-sonnet-4-5': { input: 3.0, output: 15.0, ctx: 200000 },
  'claude-sonnet-4-6': { input: 3.0, output: 15.0, ctx: 200000 },
  'claude-sonnet-4': { input: 3.0, output: 15.0, ctx: 200000 },
  'claude-opus-4': { input: 15.0, output: 75.0, ctx: 200000 },
  'claude-opus-4-6': { input: 15.0, output: 75.0, ctx: 200000 },
  'claude-haiku-4': { input: 0.8, output: 4.0, ctx: 200000 },
  'claude-haiku-3-5': { input: 0.8, output: 4.0, ctx: 200000 },
  'claude-3-5-sonnet': { input: 3.0, output: 15.0, ctx: 200000 },
  'claude-3-5-haiku': { input: 0.8, output: 4.0, ctx: 200000 },
  'claude-3-opus': { input: 15.0, output: 75.0, ctx: 200000 },
  'claude-3-sonnet': { input: 3.0, output: 15.0, ctx: 200000 },
  'claude-3-haiku': { input: 0.25, output: 1.25, ctx: 200000 },
  // --- OpenAI ---
  'gpt-5': { input: 2.0, output: 8.0, ctx: 400000 },
  'gpt-4.1': { input: 2.0, output: 8.0, ctx: 1047576 },
  'gpt-4.1-mini': { input: 0.4, output: 1.6, ctx: 1047576 },
  'gpt-4.1-nano': { input: 0.1, output: 0.4, ctx: 1047576 },
  'gpt-4o': { input: 2.5, output: 10.0, ctx: 128000 },
  'gpt-4o-mini': { input: 0.15, output: 0.6, ctx: 128000 },
  'gpt-4-turbo': { input: 10.0, output: 30.0, ctx: 128000 },
  o1: { input: 15.0, output: 60.0, ctx: 200000 },
  'o1-mini': { input: 3.0, output: 12.0, ctx: 128000 },
  'o1-pro': { input: 150.0, output: 600.0, ctx: 200000 },
  o3: { input: 2.0, output: 8.0, ctx: 200000 },
  'o3-mini': { input: 1.1, output: 4.4, ctx: 200000 },
  'o4-mini': { input: 1.1, output: 4.4, ctx: 200000 },
  // --- DeepSeek ---
  'deepseek-chat': { input: 0.27, output: 1.1, ctx: 64000 },
  'deepseek-coder': { input: 0.27, output: 1.1, ctx: 64000 },
  'deepseek-reasoner': { input: 0.55, output: 2.19, ctx: 64000 },
  'deepseek-r1': { input: 0.55, output: 2.19, ctx: 64000 },
  'deepseek-v3': { input: 0.27, output: 1.1, ctx: 64000 },
  'deepseek-v2': { input: 0.14, output: 0.28, ctx: 64000 },
  // --- Google ---
  'gemini-2.5-pro': { input: 1.25, output: 10.0, ctx: 1048576 },
  'gemini-2.5-flash': { input: 0.15, output: 0.6, ctx: 1048576 },
  'gemini-2.0-flash': { input: 0.1, output: 0.4, ctx: 1048576 },
  'gemini-1.5-pro': { input: 1.25, output: 5.0, ctx: 1048576 },
  'gemini-1.5-flash': { input: 0.075, output: 0.3, ctx: 1048576 },
  'gemma-3': { input: 0.1, output: 0.1, ctx: 128000 },
  // --- Mistral ---
  'mistral-large': { input: 2.0, output: 6.0, ctx: 128000 },
  'mistral-medium': { input: 2.0, output: 6.0, ctx: 32000 },
  'mistral-small': { input: 0.2, output: 0.6, ctx: 32000 },
  'mistral-nemo': { input: 0.15, output: 0.15, ctx: 128000 },
  mixtral: { input: 0.24, output: 0.24, ctx: 32000 },
  codestral: { input: 0.3, output: 0.9, ctx: 32000 },
  pixtral: { input: 2.0, output: 6.0, ctx: 128000 },
  // --- xAI ---
  'grok-4': { input: 3.0, output: 15.0, ctx: 131072 },
  'grok-3': { input: 3.0, output: 15.0, ctx: 131072 },
  'grok-2': { input: 2.0, output: 10.0, ctx: 131072 },
  // --- Meta ---
  'llama-4': { input: 0.2, output: 0.2, ctx: 1048576 },
  'llama-3.3': { input: 0.2, output: 0.2, ctx: 131072 },
  'llama-3.2': { input: 0.2, output: 0.2, ctx: 131072 },
  'llama-3.1': { input: 0.2, output: 0.2, ctx: 131072 },
  'llama-3': { input: 0.2, output: 0.2, ctx: 131072 },
  // --- Qwen ---
  qwen3: { input: 0.3, output: 1.2, ctx: 131072 },
  'qwen2.5': { input: 0.3, output: 1.2, ctx: 131072 },
  qwq: { input: 0.3, output: 1.2, ctx: 32768 },
  // --- Cohere ---
  'command-a': { input: 2.5, output: 10.0, ctx: 256000 },
  'command-r-plus': { input: 2.5, output: 10.0, ctx: 128000 },
  'command-r': { input: 0.15, output: 0.6, ctx: 128000 },
  // --- Perplexity ---
  'sonar-pro': { input: 3.0, output: 15.0, ctx: 200000 },
  sonar: { input: 1.0, output: 1.0, ctx: 128000 },
  // --- MiniMax ---
  minimax: { input: 0.7, output: 0.7, ctx: 1000000 },
  // --- Kimi / Moonshot ---
  moonshot: { input: 1.0, output: 1.0, ctx: 128000 },
  kimi: { input: 1.0, output: 1.0, ctx: 128000 },
  // --- Microsoft ---
  'phi-4': { input: 0.07, output: 0.14, ctx: 16000 },
  'phi-3': { input: 0.07, output: 0.14, ctx: 128000 },
  // --- Nvidia ---
  nemotron: { input: 0.3, output: 1.2, ctx: 131072 },
  // --- Nous ---
  hermes: { input: 0.2, output: 0.2, ctx: 131072 },
};

// Freeze objects...
Object.freeze(MODEL_INFO);
// and nested objects
for (const key of Object.keys(MODEL_INFO)) {
  Object.freeze(MODEL_INFO[key]);
}
