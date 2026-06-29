// ============================================
// pt-BR dictionary — Workspace, layout & misc
// ============================================
// Filled by the "Workspace, layout & misc" i18n work unit. Auto-loaded via static/app.js.
// Keys are the EXACT English source strings; values are the pt-BR translations.
import { registerMessages } from '../i18n.js';

registerMessages('pt-BR', {
  'Workspace: {path}\nFile tools are confined here; shell commands start here but are not sandboxed and can reach outside it.\nClick to clear.':
    'Área de trabalho: {path}\nAs ferramentas de arquivo ficam restritas a esta pasta; os comandos do Terminal começam aqui, mas não ficam isolados e podem acessar locais fora dela.\nClique para limpar.',
  'Workspace cleared': 'Área de trabalho limpa',
  'Too many folders to list. Type or paste a path above to jump in.':
    'Há pastas demais para listar. Digite ou cole um caminho acima para acessá-lo.',
  'No subfolders': 'Nenhuma subpasta',
  'This folder cannot be used as a workspace':
    'Esta pasta não pode ser usada como área de trabalho',
  'Could not open folder': 'Não foi possível abrir a pasta',
  'Select workspace': 'Selecionar área de trabalho',
  'Type or paste a folder path, then press Enter':
    'Digite ou cole o caminho de uma pasta e pressione Enter',
  'File tools are <strong>confined</strong> to this folder. Shell commands start here but are <strong>not sandboxed</strong> and can reach outside it. A workspace scopes the tools; it is not a security boundary.':
    'As ferramentas de arquivo ficam <strong>restritas</strong> a esta pasta. Os comandos do Terminal começam aqui, mas <strong>não ficam isolados</strong> e podem acessar locais fora dela. Uma área de trabalho delimita as ferramentas; ela não é uma barreira de segurança.',
  'Use this folder': 'Usar esta pasta',
  'Workspace set: {name}': 'Área de trabalho definida: {name}',
  'Could not browse folders': 'Não foi possível navegar pelas pastas',
});
