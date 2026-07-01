// ============================================
// pt-BR dictionary — Cookbook (running/hwfit)
// ============================================
// Filled by the "Cookbook (running/hwfit)" i18n work unit. Auto-loaded via static/app.js.
// Keys are the EXACT English source strings; values are the pt-BR translations.
import { registerMessages } from '../i18n.js';

registerMessages('pt-BR', {
  // ── cookbook-diagnosis.js ──
  'No GPU memory left for KV cache after loading model.': 'Sem memória de GPU restante para o cache KV após carregar o modelo.',
  'Retry with GPU mem 0.95': 'Tentar novamente com mem. de GPU 0.95',
  'Retry with context 2048': 'Tentar novamente com contexto 2048',
  'Retry with more GPUs (TP=8)': 'Tentar novamente com mais GPUs (TP=8)',
  'OOM during warmup. Lower GPU memory or max sequences.': 'Falta de memória durante o aquecimento. Reduza a memória de GPU ou o máximo de sequências.',
  'Retry with GPU mem 0.80': 'Tentar novamente com mem. de GPU 0.80',
  'Retry with --max-num-seqs 64': 'Tentar novamente com --max-num-seqs 64',
  'Retry with --max-num-seqs 32': 'Tentar novamente com --max-num-seqs 32',
  'GPU ran out of memory. Try more GPUs (higher TP) or lower context.': 'A GPU ficou sem memória. Tente mais GPUs (TP maior) ou reduza o contexto.',
  'Retry with TP=2': 'Tentar novamente com TP=2',
  'Retry with TP=4': 'Tentar novamente com TP=4',
  'Retry with context 4096': 'Tentar novamente com contexto 4096',
  'Retry with --enforce-eager': 'Tentar novamente com --enforce-eager',
  'FP8 MoE quantization is incompatible with this tensor-parallel split.': 'A quantização FP8 MoE é incompatível com essa divisão tensor-parallel.',
  'Suggested action: retry with a lower tensor-parallel size, such as TP=4 or TP=2. If it still fails, use a non-FP8/GGUF version of the model.': 'Ação sugerida: tente novamente com um tensor-parallel menor, como TP=4 ou TP=2. Se ainda falhar, use uma versão não-FP8/GGUF do modelo.',
  'Edit serve': 'Editar servidor',
  'vLLM cannot load this ModelOpt LM-head quantized checkpoint with the current runtime.': 'O vLLM não consegue carregar esse checkpoint quantizado ModelOpt LM-head com o runtime atual.',
  'Suggested action: upgrade vLLM through the environment that provides this CLI (package manager, venv, Docker image, or source checkout), or choose a compatible checkpoint.': 'Ação sugerida: atualize o vLLM pelo ambiente que fornece esse CLI (gerenciador de pacotes, venv, imagem Docker ou checkout do código-fonte), ou escolha um checkpoint compatível.',
  'Open Dependencies': 'Abrir Dependências',
  'Copy upgrade hint': 'Copiar dica de atualização',
  'Tensor parallel size incompatible with model dimensions.': 'Tamanho de tensor-parallel incompatível com as dimensões do modelo.',
  'Retry with TP=1': 'Tentar novamente com TP=1',
  'Swap space too large for available CPU memory.': 'Espaço de swap grande demais para a memória de CPU disponível.',
  'Retry without swap': 'Tentar novamente sem swap',
  'Retry with swap 1': 'Tentar novamente com swap 1',
  'Not enough CPU RAM or swap space.': 'RAM de CPU ou espaço de swap insuficiente.',
  'Lower max context to 4096': 'Reduzir o contexto máximo para 4096',
  '--swap-space was removed in newer vLLM versions. Remove it from the command.': 'O --swap-space foi removido em versões mais recentes do vLLM. Remova-o do comando.',
  'Port is already in use. Another server may be running.': 'A porta já está em uso. Outro servidor pode estar em execução.',
  'Kill existing vLLM': 'Encerrar vLLM existente',
  'Use port 8001': 'Usar porta 8001',
  'No GPUs visible. Check your GPU selection or driver.': 'Nenhuma GPU visível. Verifique sua seleção de GPU ou driver.',
  'Clear GPU selection (use all)': 'Limpar seleção de GPU (usar todas)',
});
