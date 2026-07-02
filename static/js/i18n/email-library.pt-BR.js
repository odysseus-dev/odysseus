// ============================================
// pt-BR dictionary — Email library
// ============================================
// Filled by the "Email library" i18n work unit. Auto-loaded via static/app.js.
// Keys are the EXACT English source strings; values are the pt-BR translations.
import { registerMessages } from '../i18n.js';

registerMessages('pt-BR', {
  // --- modal header / toolbar ---
  'Email': 'E-mail',
  'New (email)': 'Novo',
  'Inbox': 'Caixa de entrada',
  'Undone': 'Nao concluidos',
  'Pending · 30d': 'Pendente · 30d',
  'Stale · >30d': 'Parado · >30d',
  'Reply soon': 'Responder logo',
  'Permanently delete Odysseus reminder emails': 'Excluir permanentemente e-mails de lembrete do Odysseus',
  'Search by name or text': 'Buscar por nome ou texto',
  'Show only emails not marked as done (undone)': 'Mostrar apenas e-mails nao marcados como concluidos',
  'Show Odysseus reminder emails': 'Mostrar e-mails de lembrete do Odysseus',
  'Show only emails with attachments': 'Mostrar apenas e-mails com anexos',
  'New email': 'Novo e-mail',

  // --- recipient chip ---
  'Copied': 'Copiado',
  'Email copied': 'E-mail copiado',
  'Copy email': 'Copiar e-mail',
  'Copy failed': 'Falha ao copiar',

  // --- dock chip titles ---
  'Open {label}': 'Abrir {label}',
  'Restore Email': 'Restaurar E-mail',

  // --- delete confirm ---
  'Delete "{subject}"?': 'Excluir "{subject}"?',
  'Delete': 'Excluir',
  'Cancel': 'Cancelar',

  // --- toasts ---
  'Failed to delete email': 'Falha ao excluir o e-mail',
  'Moved to Trash': 'Movido para a Lixeira',

  // --- stats/loading ---
  'Loading...': 'Carregando...',

  // --- reminder clear ---
  'Permanently delete all Odysseus reminder emails?': 'Excluir permanentemente todos os e-mails de lembrete do Odysseus?',
  'Deleted {n} reminder email{s}': 'Excluido{s} {n} e-mail{s} de lembrete',
  'Failed to clear reminder emails': 'Falha ao limpar os e-mails de lembrete',

  // --- select mode ---
  'Cancel': 'Cancelar',
  'Select': 'Selecionar',
  'Select emails first': 'Selecione os e-mails primeiro',

  // --- search ---
  'Searching...': 'Buscando...',
  '{count} match{s} on server': '{count} resultado{s} no servidor',
  'Search failed': 'Falha na busca',

  // --- grid/loading ---
  'Loading emails': 'Carregando e-mails',
  '{t} all': '{t} todos',
  'Show all emails': 'Mostrar todos os e-mails',
  'Show all': 'Mostrar todos',
  '{n} unread': '{n} nao lidos',
  '999+ unread': '999+ nao lidos',
  'Show unread emails': 'Mostrar e-mails nao lidos',
  '{n} emails': '{n} e-mails',
  'Failed to load: {msg}': 'Falha ao carregar: {msg}',
  'Failed to load': 'Falha ao carregar',
  '{n} scheduled': '{n} agendado(s)',
  'No scheduled emails': 'Nenhum e-mail agendado',

  // --- scheduled email card ---
  '(no subject)': '(sem assunto)',
  '(no recipient)': '(sem destinatario)',
  'FAILED': 'FALHOU',
  'PENDING': 'PENDENTE',
  'To:': 'Para:',
  'Sends': 'Envio',
  'Cancel scheduled send': 'Cancelar envio agendado',
  'Cancel scheduled email "{subject}"?': 'Cancelar e-mail agendado "{subject}"?',
  'Cancel Send': 'Cancelar Envio',
  'Keep': 'Manter',

  // --- card menu / actions ---
  'Mark not done': 'Marcar como nao concluido',
  'Mark done': 'Marcar como concluido',
  'Favorited': 'Favoritado',
  'Actions': 'Ações',
  'Previous email': 'E-mail anterior',
  'Next email': 'Proximo e-mail',
  'No emails': 'Nenhum e-mail',
  'Set up at:': 'Configure em:',
  'Settings &rsaquo; Integrations': 'Configuracoes &rsaquo; Integracoes',

  // --- reader header ---
  'From:': 'De:',
  'Cc:': 'Cc:',
  'Show recipients': 'Mostrar destinatarios',
  'AI Reply (cached draft ready)': 'Resposta com IA (rascunho em cache pronto)',
  'AI Reply (suggest a draft)': 'Resposta com IA (sugerir rascunho)',
  'AI reply': 'Resposta com IA',
  'Reply': 'Responder',
  'Reply All': 'Responder a todos',
  'Reply all': 'Responder a todos',
  'Forward': 'Encaminhar',
  'Summarize': 'Resumir',
  'Summary': 'Resumo',
  'More actions': 'Mais ações',
  'More': 'Mais',
  'Error: {msg}': 'Erro: {msg}',
  'Failed to load email': 'Falha ao carregar o e-mail',

  // --- body rendering (folds, thread turns) ---
  'Earlier thread': 'Conversa anterior',
  'Signature': 'Assinatura',
  'No body': 'Sem conteudo',
  'Me': 'Eu',
  'Earlier reply': 'Resposta anterior',

  // --- from-sender panel ---
  'No sender address available': 'Nenhum endereco de remetente disponivel',
  'All senders': 'Todos os remetentes',
  'Close sender panel': 'Fechar painel do remetente',
  'Search {name}…': 'Buscar {name}…',
  'Remove {name}': 'Remover {name}',
  'No emails with attachments in this view.': 'Nenhum e-mail com anexos nesta visualizacao.',
  'No emails involve all those people.': 'Nenhum e-mail envolve todas essas pessoas.',
  'No other emails from this sender in {folder}.': 'Nenhum outro e-mail deste remetente em {folder}.',
  'Add another person…': 'Adicionar outra pessoa…',
  'Search people or emails…': 'Buscar pessoas ou e-mails…',
  'No matches for "{q}".': 'Nenhum resultado para "{q}".',
  'Search failed: {msg}': 'Falha na busca: {msg}',
});
