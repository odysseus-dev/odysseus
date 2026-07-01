// ============================================
// pt-BR dictionary — Email inbox
// ============================================
// Filled by the "Email inbox" i18n work unit. Auto-loaded via static/app.js.
// Keys are the EXACT English source strings; values are the pt-BR translations.
import { registerMessages } from '../i18n.js';

registerMessages('pt-BR', {
  // emailInbox.js — attachments / unread indicators
  'Has attachments': 'Tem anexos',
  'Unread': 'Nao lido',
  'Urgent — ': 'Urgente — ',
  'needs reply now': 'requer resposta agora',
  'Reply soon — ': 'Responder em breve — ',
  'Show all emails from ': 'Ver todos os e-mails de ',

  // emailInbox.js — filter chip
  'From: ': 'De: ',
  'Clear filter': 'Limpar filtro',
  'No emails from ': 'Nenhum e-mail de ',
  'No emails': 'Nenhum e-mail',

  // emailInbox.js — spam tag
  'AI flagged as spam — click ✓ to unflag': 'IA marcou como spam — clique ✓ para desmarcar',
  'spam': 'spam',
  'Not spam': 'Nao e spam',

  // emailInbox.js — context menu
  'Open': 'Abrir',
  'Remind to reply': 'Lembrete para responder',
  'Archive': 'Arquivar',
  'Delete': 'Excluir',
  'Cancel': 'Cancelar',

  // emailInbox.js — reminder submenu
  'Remind me': 'Lembrar-me',
  'Later today': 'Mais tarde hoje',
  'Tomorrow': 'Amanha',
  'Next week': 'Proxima semana',
  'Pick date and time…': 'Escolher data e hora…',

  // emailInbox.js — reminder note content
  'Reply: ': 'Responder: ',
  '(no subject)': '(sem assunto)',
  'someone': 'alguem',
  'Remember to reply to this email.': 'Lembre-se de responder a este e-mail.',

  // emailInbox.js — toasts / errors
  'Reminder set for ': 'Lembrete definido para ',
  'Failed to create reminder': 'Falha ao criar lembrete',
  'Drafting AI reply': 'Rascunhando resposta com IA',
  'AI reply could not be generated': 'Nao foi possivel gerar resposta com IA',
  'AI reply failed: ': 'Falha na resposta com IA: ',
  'Failed to create reply draft (': 'Falha ao criar rascunho de resposta (',
  'Reply failed: ': 'Falha ao responder: ',
  'Could not start a new email (no session).': 'Nao foi possivel iniciar um novo e-mail (sem sessao).',
  'Failed to create new email (': 'Falha ao criar novo e-mail (',
  'Failed to load: ': 'Falha ao carregar: ',
  'Failed to load': 'Falha ao carregar',

  // emailInbox.js — folder display names
  'INBOX': 'CAIXA DE ENTRADA',
  'Archive / All Mail': 'Arquivo / Todos os e-mails',
  'Spam': 'Spam',
  'Junk': 'Lixo eletronico',
  'Trash': 'Lixeira',
  'Sent': 'Enviados',
  'Drafts': 'Rascunhos',

  // voiceRecorder.js — errors and toasts
  'Microphone requires HTTPS. Use a reverse proxy with SSL or access via localhost.': 'Microfone requer HTTPS. Use um proxy reverso com SSL ou acesse via localhost.',
  'Microphone not supported in this browser.': 'Microfone nao suportado neste navegador.',
  'No speech detected': 'Nenhuma fala detectada',
  'Transcribing...': 'Transcrevendo...',
  'Transcribed': 'Transcrito',
  'Recording...': 'Gravando...',
  'Microphone access denied. Check browser permissions.': 'Acesso ao microfone negado. Verifique as permissoes do navegador.',
  'No microphone found.': 'Nenhum microfone encontrado.',
  'Microphone error: ': 'Erro no microfone: ',
  'Transcription failed: ': 'Falha na transcricao: ',
});
