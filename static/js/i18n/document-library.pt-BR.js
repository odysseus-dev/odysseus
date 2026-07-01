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

  // --- expand / import / bulk ops ---
  'Failed to load': 'Falha ao carregar',
  'Could not create a session': 'Nao foi possivel criar uma sessao',
  'Document cloned to session': 'Documento clonado para a sessao',
  'Failed to import document': 'Falha ao importar o documento',
  'Document deleted': 'Documento excluido',
  'Failed to delete document: {msg}': 'Falha ao excluir o documento: {msg}',
  'Delete this document?': 'Excluir este documento?',
  'Delete {n} documents?': 'Excluir {n} documentos?',
  'Delete {n} document?': 'Excluir {n} documento?',
  '{n} Selected': '{n} selecionado(s)',
  'Deleted {a} · {b} failed': 'Excluidos {a} · {b} falharam',
  'Deleted {n} documents': 'Excluidos {n} documentos',
  'Deleted {n} document': 'Excluido {n} documento',
  'Archived {a} · {b} failed': 'Arquivados {a} · {b} falharam',
  'Restored {a} · {b} failed': 'Restaurados {a} · {b} falharam',
  'Archived {n} documents': 'Arquivados {n} documentos',
  'Archived {n} document': 'Arquivado {n} documento',
  'Restored {n} documents': 'Restaurados {n} documentos',
  'Restored {n} document': 'Restaurado {n} documento',
  'Cloned {a} · {b} failed': 'Clonados {a} · {b} falharam',
  'Cloned {n} documents': 'Clonados {n} documentos',
  'Cloned {n} document': 'Clonado {n} documento',
  'Zipping {n} documents…': 'Compactando {n} documentos…',
  'Exported {n} documents (zip)': 'Exportados {n} documentos (zip)',
  'Failed to create zip': 'Falha ao criar o zip',
  'Exported {n} documents': 'Exportados {n} documentos',
  'Exported {n} document': 'Exportado {n} documento',

  // --- modal header / tabs ---
  'Library': 'Biblioteca',
  'Chats': 'Conversas',
  'Documents': 'Documentos',
  'Research': 'Pesquisa',
  'Archive': 'Arquivo',

  // --- Chats panel ---
  'All active chat sessions. Click to open.': 'Todas as sessoes de conversa ativas. Clique para abrir.',
  'Recent': 'Recentes',
  'Oldest': 'Mais antigos',
  'Most messages': 'Mais mensagens',
  'A–Z': 'A–Z',
  'AI tidy: delete junk sessions and organize into folders': 'Organizacao com IA: exclui sessoes descartaveis e organiza em pastas',
  'Tidy': 'Organizar',
  'Search chats…': 'Buscar conversas…',
  'All': 'Todos',
  '0 Selected': '0 selecionado(s)',
  'Cancel (Esc)': 'Cancelar (Esc)',

  // --- Archive panel ---
  'Archived sessions. Restore to make active again.': 'Sessoes arquivadas. Restaure para tornar ativas novamente.',
  'Search archive…': 'Buscar no arquivo…',

  // --- Research panel ---
  'Completed deep research reports. Click to view.': 'Relatorios de pesquisa profunda concluidos. Clique para ver.',
  'Most sources': 'Mais fontes',
  'Tidy: delete research with no sources or empty reports': 'Organizar: exclui pesquisas sem fontes ou relatorios vazios',
  'Search research…': 'Buscar pesquisas…',

  // --- Documents panel ---
  'Import files from disk': 'Importar arquivos do disco',
  'Create new blank document': 'Criar novo documento em branco',
  'Create': 'Criar',
  'Open documents in a session, clone to a new or import new files.': 'Abra documentos em uma sessao, clone para uma nova ou importe novos arquivos.',
  'Most edits': 'Mais edicoes',
  'Select documents': 'Selecionar documentos',
  'Tidy: remove empty / junk / duplicate documents': 'Organizar: remove documentos vazios / descartaveis / duplicados',
  'Search titles & content…': 'Buscar titulos e conteudo…',
  'Load more': 'Carregar mais',

  // --- tabs loading / chats preview ---
  'Loading…': 'Carregando…',
  'No messages yet': 'Ainda sem mensagens',
  'Copy': 'Copiar',
  'Failed to load preview': 'Falha ao carregar a previa',
  '{n} chats': '{n} conversas',
  '{n} chat': '{n} conversa',
  'No chats': 'Sem conversas',
  '{n} msg': '{n} msg',
  '{n} msgs': '{n} msgs',
  'all': 'todos',
  'Failed to archive {a} of {b} chats': 'Falha ao arquivar {a} de {b} conversas',
  'Failed to archive {a} of {b} chat': 'Falha ao arquivar {a} de {b} conversa',
  'Delete {n} chats? This cannot be undone.': 'Excluir {n} conversas? Isso nao pode ser desfeito.',
  'Delete {n} chat? This cannot be undone.': 'Excluir {n} conversa? Isso nao pode ser desfeito.',
  'Failed to delete {a} of {b} chats': 'Falha ao excluir {a} de {b} conversas',
  'Failed to delete {a} of {b} chat': 'Falha ao excluir {a} de {b} conversa',
  'Tidy failed': 'Falha ao organizar',
  'Sorted {a} sessions into {b} folders': '{a} sessoes organizadas em {b} pastas',
  'Nothing to tidy': 'Nada para organizar',
  'Tidy: {msg}': 'Organizar: {msg}',
});
