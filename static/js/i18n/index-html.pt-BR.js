// ============================================
// pt-BR dictionary — static strings in static/index.html
// ============================================
// SEED set built with the i18n foundation. It covers the most visible chrome so
// the app visibly switches to Portuguese immediately. The dedicated "index.html
// static strings" work unit EXTENDS this file with the full coverage of
// index.html (nav, modals, settings panels, tooltips, placeholders, aria-labels).
//
// Keys are the EXACT English text as it appears in the rendered DOM (HTML
// entities decoded, e.g. use "&" not "&amp;"). translateDOM() matches the
// trimmed text of each text node / attribute against these keys.

import { registerMessages } from '../i18n.js';

registerMessages('pt-BR', {
  // ── Sidebar sections & tools ──
  'Search': 'Buscar',
  'New Chat': 'Nova conversa',
  'Chats': 'Conversas',
  'Email': 'E-mail',
  'Models': 'Modelos',
  'Tools': 'Ferramentas',
  'Brain': 'Memória',
  'Calendar': 'Calendário',
  'Compare': 'Comparar',
  'Deep Research': 'Pesquisa profunda',
  'Gallery': 'Galeria',
  'Library': 'Biblioteca',
  'Notes': 'Notas',
  'Tasks': 'Tarefas',
  'Theme': 'Tema',
  'User': 'Usuário',

  // ── Appearance settings: section titles ──
  'Sidebar': 'Barra lateral',
  'Chat Area': 'Área de conversa',
  'Chat Bar': 'Barra de conversa',

  // ── Appearance settings: toggle labels ──
  'Web Search': 'Busca na web',
  'Document Editor': 'Editor de documentos',
  'Settings Button': 'Botão de configurações',
  'Session Header': 'Cabeçalho da sessão',
  'Full-width chat': 'Conversa em largura total',
  'Welcome Message': 'Mensagem de boas-vindas',
  'Incognito Mode': 'Modo anônimo',
  'Text-only Emojis': 'Emojis somente texto',
  'Thinking Process': 'Processo de raciocínio',
  'Sensitive Blur': 'Desfoque de dados sensíveis',

  // ── Appearance settings: hints ──
  'Brand name': 'Nome da marca',
  'Chat history list': 'Lista de histórico de conversas',
  'Model selector & quick-chat': 'Seletor de modelos e conversa rápida',
  'Whole section (header + all tools)': 'Seção inteira (cabeçalho + todas as ferramentas)',
  'Avatar & name': 'Avatar e nome',
  'Model name & export above chat': 'Nome do modelo e exportação acima da conversa',
  'Use the full window width (desktop)': 'Usar a largura total da janela (desktop)',
  'Logo & tips on empty chat': 'Logo e dicas na conversa vazia',
  'No memory, no history saved': 'Sem memória, sem histórico salvo',
  'Strip emojis from AI replies': 'Remover emojis das respostas da IA',
  'Blur emails, tokens, and secrets in AI output': 'Desfocar e-mails, tokens e segredos na saída da IA',

  // ── Language card (added in index.html by the foundation) ──
  'Language': 'Idioma',
  'Interface language': 'Idioma da interface',

  // ── Memory (Brain) modal chrome ──
  'Tidy': 'Organizar',
  'Select': 'Selecionar',
  'AI tidy: deduplicate and clean up memories': 'IA organizar: remover duplicatas e limpar memórias',
  'Select multiple memories': 'Selecionar várias memórias',
  'Close memory modal': 'Fechar janela de memória',
  'Sort memories': 'Ordenar memórias',
  'Search memories…': 'Buscar memórias…',

  // ── Composer & common placeholders / aria-labels ──
  'Message Odysseus...': 'Mensagem para o Odysseus...',
  'Search skills…': 'Buscar habilidades…',
  'Search models': 'Buscar modelos',
  'Max skills to inject': 'Máx. de habilidades para injetar',
});
