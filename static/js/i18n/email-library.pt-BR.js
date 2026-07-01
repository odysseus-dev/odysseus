// ============================================
// pt-BR dictionary — Email library
// ============================================
// Filled by the "Email library" i18n work unit. Auto-loaded via static/app.js.
// Keys are the EXACT English source strings; values are the pt-BR translations.
import { registerMessages } from '../i18n.js';

registerMessages('pt-BR', {
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
});
