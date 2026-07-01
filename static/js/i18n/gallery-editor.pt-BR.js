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

  // gallery.js — round 1
  '{tagged}/{total} tagged': '{tagged}/{total} marcadas',
  '({dupes} duplicates)': '({dupes} duplicadas)',
  '{n} imported': '{n} importadas',
  '{n} duplicates skipped': '{n} duplicadas ignoradas',
  '{n} errors': '{n} erros',
  "Browsers can't read folders dropped from native file managers (Thunar/Nautilus). Use the \"Upload album\" tile in the Albums tab instead.": 'Navegadores nao conseguem ler pastas soltas de gerenciadores de arquivos nativos (Thunar/Nautilus). Use o botao "Enviar album" na aba Albuns.',
  'No images found in that drop': 'Nenhuma imagem encontrada nesse arraste',
  'Select albums first': 'Selecione albuns primeiro',
  'Cancel': 'Cancelar',
  'Select': 'Selecionar',
  '{n} selected': '{n} selecionadas',
  'No albums yet.': 'Nenhum album ainda.',
  '+ New album': '+ Novo album',
  'No albums match "{query}".': 'Nenhum album corresponde a "{query}".',
  'New album': 'Novo album',
  'Upload album': 'Enviar album',
  'Pick a folder': 'Escolha uma pasta',
  'Options': 'Opcoes',
  'Album options': 'Opcoes do album',
  'Upload here': 'Enviar aqui',
  'Rename': 'Renomear',
  'Delete': 'Excluir',
  '{n} photo{s}': '{n} foto{s}',
  'Rename album:': 'Renomear album:',
  'Album renamed': 'Album renomeado',
  'Rename failed': 'Falha ao renomear',
  'Delete album "{name}"? Photos inside will stay in your library.': 'Excluir o album "{name}"? As fotos dentro dele permanecerao na sua biblioteca.',
  'Album deleted': 'Album excluido',
  'Delete failed': 'Falha ao excluir',
  'Name your new album.': 'De um nome ao seu novo album.',
  'e.g. Vacation 2026': 'ex.: Ferias 2026',
  'Create': 'Criar',
  'Album name:': 'Nome do album:',
});
