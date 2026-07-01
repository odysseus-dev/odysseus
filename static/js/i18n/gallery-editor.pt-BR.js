// ============================================
// pt-BR dictionary — Gallery & image editor
// ============================================
// Filled by the "Gallery & image editor" i18n work unit. Auto-loaded via static/app.js.
// Keys are the EXACT English source strings; values are the pt-BR translations.
import { registerMessages } from '../i18n.js';

registerMessages('pt-BR', {
  // ai-tool-runner.js
  'No image returned': 'Nenhuma imagem retornada',
  'complete': 'concluido',
  'Failed to load result': 'Falha ao carregar resultado',
  'failed: {pkg} is not installed on the server.': 'falhou: {pkg} nao esta instalado no servidor.',
  'Install {pkg}': 'Instalar {pkg}',
  'failed:': 'falhou:',
  'Open Cookbook': 'Abrir Cookbook',

  // ai-rembg.js
  'Sharpened': 'Nitidez aplicada',
  'BG Removed': 'Fundo removido',

  // ai-models.js
  'None': 'Nenhum',
  'Auto': 'Automatico',
  '+ Serve a model in Cookbook…': '+ Servir um modelo no Cookbook…',

  // ai-tools-misc.js
  'Harmonize needs a second layer pasted/imported over the base photo — nothing to color-match against.': 'Harmonizar precisa de uma segunda camada colada/importada sobre a foto base — nao ha nada para combinar as cores.',
  'Harmonized': 'Harmonizado',
  'Upscale {factor}×': 'Ampliar {factor}×',
  'Upscaled {factor}× to {newW}×{newH}': 'Ampliado {factor}× para {newW}×{newH}',
  'Upscaling…': 'Ampliando…',
  'Server returned {status}': 'Servidor retornou {status}',
  'AI Upscaled': 'Ampliado por IA',
  'AI upscaled to {newW}×{newH}': 'Ampliado por IA para {newW}×{newH}',
  'AI upscale failed:': 'Falha na ampliacao por IA:',
  'Enter a style prompt': 'Digite um prompt de estilo',
  'Applying...': 'Aplicando...',
  'Styled: {prompt}': 'Estilizado: {prompt}',
  'Style applied': 'Estilo aplicado',
  'Style transfer failed:': 'Falha na transferencia de estilo:',
  'Apply Style': 'Aplicar estilo',
  'Add layer': 'Adicionar camada',
  'Layer {n}': 'Camada {n}',

  // ai-inpaint.js
  'Draw the area you want to inpaint first': 'Desenhe primeiro a area que deseja pintar',
  'No image returned from inpaint endpoint': 'Nenhuma imagem retornada pelo endpoint de pintura',
  'Inpaint: {prompt}': 'Pintura: {prompt}',
  'Inpaint Result': 'Resultado da pintura',
  'Inpaint complete — drag Edge feather / Edge stroke to blend': 'Pintura concluida — arraste Suavizar borda / Traco de borda para mesclar',
  'Inpaint render failed:': 'Falha ao renderizar a pintura:',
  'Inpaint result failed to decode': 'Falha ao decodificar o resultado da pintura',
  'Inpaint failed:': 'Falha na pintura:',
  'Enter a prompt for inpainting': 'Digite um prompt para a pintura',
  'Generate': 'Gerar',
  'Generating': 'Gerando',
  'Remove': 'Remover',
  'Removing': 'Removendo',
  'No empty areas to outpaint — canvas is fully covered.': 'Nenhuma area vazia para expandir — a tela esta totalmente preenchida.',
  'No active layer for outpaint': 'Nenhuma camada ativa para expandir',
  'Outpaint': 'Expandir',
  'Outpainting': 'Expandindo',
});
