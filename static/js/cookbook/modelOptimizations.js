// Pure (browser-free) model-name → vLLM optimization detection, extracted from
// cookbook.js so it can be unit-tested without the DOM. cookbook.js re-exports it.

/** Detect model-specific vLLM optimizations */
export function _detectModelOptimizations(modelName) {
  const n = (modelName || '').toLowerCase();
  const opts = { envVars: [], flags: [], tips: [] };

  // MoE active-parameter suffix, e.g. A3B / A10B / A17B / A22B. A generic
  // A<number>B token so the catalog's A17B Qwen3.5 MoE rows (Qwen3.5-397B-A17B)
  // are not missed by a hardcoded suffix list. Dense names (e.g. qwen3.5-32b)
  // have no a<n>b token and stay out of the MoE path.
  const moeActive = /\ba\d+b\b/.test(n);

  // Qwen3.5 MoE models
  if ((n.includes('qwen3.5') || n.includes('qwen3-')) && moeActive) {
    opts.envVars.push('VLLM_USE_DEEP_GEMM=0', 'VLLM_USE_FLASHINFER_MOE_FP16=1', 'VLLM_USE_FLASHINFER_SAMPLER=0', 'OMP_NUM_THREADS=4');
    opts.flags.push('--enable-expert-parallel', '--reasoning-parser qwen3');
    opts.tips.push('MoE optimizations: expert parallel + flashinfer MoE kernels');
  }
  // Qwen3 MoE (non-3.5)
  else if (n.includes('qwen3') && moeActive) {
    opts.envVars.push('VLLM_USE_DEEP_GEMM=0', 'VLLM_USE_FLASHINFER_MOE_FP16=1');
    opts.flags.push('--enable-expert-parallel', '--reasoning-parser qwen3');
    opts.tips.push('MoE optimizations: expert parallel');
  }
  // DeepSeek MoE
  else if (n.includes('deepseek') && (n.includes('v3') || n.includes('r1'))) {
    opts.flags.push('--enable-expert-parallel');
    opts.tips.push('MoE expert parallel for DeepSeek');
  }
  // Speculative decoding — pick the right MTP method per model family.
  // opts.spec.{method,tokens} seed the UI dropdown/input; the actual flag is
  // assembled by the command builder so the user can edit before launching.
  let specDefault = null;
  if (n.includes('qwen3-next') || (n.includes('qwen3.5') && (n.includes('a10b') || n.includes('a22b')))) {
    specDefault = { method: 'qwen3_next_mtp', tokens: 2 };
  } else if (
    (n.includes('deepseek') && (n.includes('v3') || n.includes('v3.1') || n.includes('r1'))) ||
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
