// Pure (browser-free) model-name → vLLM optimization detection, extracted from
// cookbook.js so it can be unit-tested without the DOM. cookbook.js imports it
// (and re-exports _detectReasoningParser to keep its public surface).

export function _isStepFunStepModel(modelName) {
  const n = (modelName || '').toLowerCase();
  return n.includes('stepfun')
    || n.includes('step-3')
    || n.includes('step3')
    || n.includes('step_3');
}

/** Detect the right vLLM --reasoning-parser based on model name.
 *  Returns the parser slug (matches vLLM's official list) or null when the
 *  model isn't a reasoning model. Without the right parser, thinking tokens
 *  leak as plain text instead of being split into a separate channel.
 *  Source: vllm/reasoning/__init__.py registered parsers.
 */
export function _detectReasoningParser(modelName) {
  const n = (modelName || '').toLowerCase();
  // StepFun Step-3.x uses Step's native <think> / tool-call tokens. vLLM
  // registers this parser as step3p5.
  if (_isStepFunStepModel(modelName)) return 'step3p5';
  // MiniMax M3 — newer vLLM nightly/parser builds use minimax_m3. This must
  // be checked before the M2.x rule and before the generic MiniMax tool parser.
  if (n.includes('minimax') && /\bm3\b/.test(n)) return 'minimax_m3';
  // MiniMax M2 / M2.5 / M2.7 — released with a dedicated parser. Catch M2
  // before plain "minimax" so M2.x doesn't fall through to a wrong parser.
  if (n.includes('minimax') && n.match(/\bm2(?:\.\d)?\b/)) return 'minimax_m2';
  // DeepSeek-V4 has a dedicated parser in SGLang. Keep it before R1/V3.
  if (n.includes('deepseek') && /\bv[-_]?4\b/.test(n)) return 'deepseek-v4';
  // DeepSeek-R1 / V3-Thinking / V3.1-Thinking variants. Bare V3/V3.1 (non-
  // thinking) skip this — they're not reasoning models.
  if (n.includes('deepseek') && (n.includes('r1') || n.includes('thinking'))) return 'deepseek_r1';
  // Qwen3 / Qwen3.5 reasoning models. Qwen3-Coder + Qwen3-Instruct don't
  // emit <think> blocks, so skip the parser there.
  if (n.includes('qwen3') && !n.includes('coder') && !n.includes('instruct')) return 'qwen3';
  // GLM-4 / GLM-4.5 / GLM-4.6 with reasoning.
  if (n.includes('glm-4') || n.includes('glm-5')) return 'glm45';
  // OpenAI gpt-oss family.
  if (n.includes('gpt-oss')) return 'gpt_oss';
  // Hunyuan A13B reasoning.
  if (n.includes('hunyuan') && n.includes('a13b')) return 'hunyuan_a13b';
  // IBM Granite reasoning.
  if (n.includes('granite') && (n.includes('reason') || n.includes('think'))) return 'granite';
  // InternLM reasoning.
  if (n.includes('internlm')) return 'internlm';
  return null;
}

/** Detect model-specific vLLM optimizations */
export function _detectModelOptimizations(modelName) {
  const n = (modelName || '').toLowerCase();
  const opts = { envVars: [], flags: [], tips: [] };

  // MoE active-parameter suffix, e.g. A3B / A10B / A17B / A22B. A generic
  // A<number>B token so the catalog's A17B Qwen3.5 MoE rows (Qwen3.5-397B-A17B)
  // are not missed by a hardcoded suffix list. Dense names (e.g. qwen3.5-32b)
  // have no a<n>b token and stay out of the MoE path.
  const moeActive = /\ba\d+b\b/.test(n);

  // StepFun Step-3.x MoE models. Their tokenizer defines the Step tool-call
  // and thinking tags; vLLM/SGLang need the step3p5 parser instead of generic
  // Hermes/XML guesses, and the MoE backend should default to expert parallel.
  if (_isStepFunStepModel(modelName)) {
    opts.flags.push('--enable-expert-parallel');
    opts.tips.push('StepFun Step-3 MoE: expert parallel');
    opts.tips.push('StepFun parser: step3p5 for native tool calls and reasoning tags');
  }
  // Qwen3.5 MoE models — MoE-specific env vars + expert-parallel. Both name
  // variants require the MoE active-param suffix: without the parens the ||
  // bound looser than &&, so ANY name containing "qwen3.5" (dense included)
  // entered the MoE branch and got flags that crash a dense launch.
  // The --reasoning-parser flag is added uniformly below via
  // _detectReasoningParser, no longer hardcoded here.
  else if ((n.includes('qwen3.5') || n.includes('qwen3-')) && moeActive) {
    opts.envVars.push('VLLM_USE_DEEP_GEMM=0', 'VLLM_USE_FLASHINFER_MOE_FP16=1', 'VLLM_USE_FLASHINFER_SAMPLER=0', 'OMP_NUM_THREADS=4');
    opts.flags.push('--enable-expert-parallel');
    opts.tips.push('MoE optimizations: expert parallel + flashinfer MoE kernels');
  }
  // Qwen3 MoE (non-3.5)
  else if (n.includes('qwen3') && moeActive) {
    opts.envVars.push('VLLM_USE_DEEP_GEMM=0', 'VLLM_USE_FLASHINFER_MOE_FP16=1');
    opts.flags.push('--enable-expert-parallel');
    opts.tips.push('MoE optimizations: expert parallel');
  }
  // DeepSeek MoE — V3 / V3.1 / V4 (and future Vx), R1 / R2 reasoning.
  // Anything v-{integer} or r-{integer} family from DeepSeek is MoE in
  // current architectures. These models also require fp8 KV cache to
  // fit at meaningful context with current tensor-parallel layouts —
  // the launch crashes otherwise (--kv-cache-dtype auto → bf16 OOMs).
  else if (n.includes('deepseek') && /\b(v[3-9]|v\d{2,}|r[1-9])\b/.test(n)) {
    opts.flags.push('--enable-expert-parallel');
    opts.tips.push('MoE expert parallel for DeepSeek');
    opts.kvCacheDtype = 'fp8';
    opts.tips.push('fp8 KV cache required — bf16 OOMs at usable context');
  }
  // MiniMax MoE — Abab/M1/M2/M2.5/M2.7 are all MoE (Lightning Attention +
  // MoE in M1, full sparse MoE from M2 onward). They benefit from the
  // same --enable-expert-parallel flag as the Qwen/DeepSeek families,
  // and the toggle has to be detectable here for the Expert Parallel
  // checkbox in the serve form to render at all.
  else if (n.includes('minimax')) {
    opts.flags.push('--enable-expert-parallel');
    opts.tips.push('MoE expert parallel for MiniMax');
    if (/\bm3\b/.test(n)) {
      opts.kvCacheDtype = 'fp8';
      opts.tips.push('MiniMax M3 defaults: fp8 KV cache, block-size 128, TRITON attention');
    }
  }
  // Reasoning parser — applies independently of MoE detection. Without this
  // flag, models like MiniMax-M2.x, DeepSeek-R1, Qwen3 reasoning, GLM-4.x,
  // gpt-oss leak <think> blocks as plain text instead of separating them
  // into the reasoning_content channel.
  const _reasoningParser = _detectReasoningParser(modelName);
  if (_reasoningParser) {
    opts.flags.push(`--reasoning-parser ${_reasoningParser}`);
    opts.tips.push(`Reasoning parser (${_reasoningParser}): splits <think> tokens into a separate channel`);
  }
  // Speculative decoding — pick the right MTP method per model family.
  // opts.spec.{method,tokens} seed the UI dropdown/input; the actual flag is
  // assembled by the command builder so the user can edit before launching.
  let specDefault = null;
  if (n.includes('qwen3-next') || (n.includes('qwen3.5') && (n.includes('a10b') || n.includes('a22b')))) {
    specDefault = { method: 'qwen3_next_mtp', tokens: 2 };
  } else if (
    (n.includes('deepseek') && /\b(v[3-9]|v\d{2,}|r[1-9])\b/.test(n)) ||
    n.includes('kimi-k2') || n.includes('kimi_k2') ||
    n.includes('glm-4.5') || n.includes('glm4.5') ||
    n.includes('minimax-m1') || n.includes('minimax_m1')
  ) {
    specDefault = { method: 'mtp', tokens: 3 };
  }
  if (specDefault) {
    opts.spec = specDefault;
    opts.flags.push(`--speculative-config '{"method":"${specDefault.method}","num_speculative_tokens":${specDefault.tokens}}'`);
    opts.tips.push(`Speculative decoding (${specDefault.method}, ${specDefault.tokens} tokens): ~1.5-2x faster generation`);
  }

  return opts;
}
