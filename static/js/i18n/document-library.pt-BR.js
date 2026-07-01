// ============================================
// pt-BR dictionary — Document library
// ============================================
// Filled by the "Document library" i18n work unit. Auto-loaded via static/app.js.
// Keys are the EXACT English source strings; values are the pt-BR translations.
import { registerMessages } from '../i18n.js';

registerMessages('pt-BR', {
  // --- misc / long-press menu ---
  'Failed to copy chat': 'Falha ao copiar a conversa',
  'Select': 'Selecionar',
  'Cancel': 'Cancelar',

  // --- relative time ---
  'just now': 'agora mesmo',
  '{n}m ago': 'ha {n} min',
  '{n}h ago': 'ha {n} h',
  'yesterday': 'ontem',
  '{n}d ago': 'ha {n} d',
  '{n}w ago': 'ha {n} sem',

  // --- stats ---
  '{a} of {b} documents': '{a} de {b} documentos',
  '{a} of {b} document': '{a} de {b} documento',
  '{n} documents': '{n} documentos',
  '{n} document': '{n} documento',
  'all ({n})': 'todos ({n})',

  // --- empty states / load more ---
  'No documents match your search.': 'Nenhum documento corresponde a sua busca.',
  'No documents yet': 'Ainda sem documentos',
  'Import': 'Importar',
  'or create one in a session': 'ou crie um em uma sessao',
  'Load more ({a} of {b})': 'Carregar mais ({a} de {b})',

  // --- card menu / actions ---
  'Actions': 'Acoes',
  'Open': 'Abrir',
  'Clone': 'Clonar',
  'Export': 'Exportar',
  'Delete': 'Excluir',
  'Archive': 'Arquivar',
  'Restore': 'Restaurar',
  'Open in the editor': 'Abrir no editor',
  'Clone to active session': 'Clonar para a sessao ativa',
  'Failed to export document': 'Falha ao exportar o documento',
  'Restore to active documents': 'Restaurar para documentos ativos',
  'Archive (hide from the main list)': 'Arquivar (ocultar da lista principal)',
  'Archived': 'Arquivado',
  'Restored': 'Restaurado',
  'Failed to archive': 'Falha ao arquivar',
  'Failed to restore': 'Falha ao restaurar',
  'Open in original session': 'Abrir na sessao original',
  'Clone — copy to active session': 'Clonar — copiar para a sessao ativa',
});
