// ============================================
// pt-BR dictionary — Research & sessions
// ============================================
// Filled by the "Research & sessions" i18n work unit. Auto-loaded via static/app.js.
// Keys are the EXACT English source strings; values are the pt-BR translations.
import { registerMessages } from '../i18n.js';

registerMessages('pt-BR', {
  // --- research/jobs.js: formatPhase / notifications ---
  'Starting...': 'Iniciando...',
  'Round {round}/{maxRounds}: ': 'Rodada {round}/{maxRounds}: ',
  'Round {round}: ': 'Rodada {round}: ',
  'Probing model...': 'Sondando modelo...',
  'Planning research strategy...': 'Planejando estratégia de pesquisa...',
  'Searching ({queries} queries)': 'Buscando ({queries} consultas)',
  'Reading {n} sources': 'Lendo {n} fontes',
  'Analyzing {n} findings': 'Analisando {n} achados',
  'Writing report -- {n} sources': 'Escrevendo relatório -- {n} fontes',
  'Research Complete': 'Pesquisa concluída',

  // --- researchSynapse.js ---
  'verifying model': 'verificando modelo',
  'planning strategy': 'planejando estratégia',
  'searching': 'buscando',
  'reading sources': 'lendo fontes',
  'analyzing findings': 'analisando achados',
  'writing report': 'escrevendo relatório',
  'error': 'erro',
  'complete': 'concluido',
  'starting…': 'iniciando…',
  'round': 'rodada',
  'sources': 'fontes',
  'query': 'consulta',
  '{n} queries': '{n} consultas',
  'reading: {title}': 'lendo: {title}',
  '{n} findings': '{n} achados',

  // --- research/panel.js: header/settings form ---
  'Show visualization': 'Mostrar visualização',
  'Minimize visualization': 'Minimizar visualização',
  "e.g. Trace Odysseus's ten-year journey home from Troy — every island, monster, and detour, and why each one cost him":
    'ex.: Trace a jornada de dez anos de Odisseu de volta de Troia — cada ilha, monstro e desvio, e por que cada um custou caro',
  'e.g. Compare Rust and Go for building a high-throughput web API in 2026': 'ex.: Compare Rust e Go para construir uma API web de alto throughput em 2026',
  'e.g. Fact-check whether honey actually never spoils': 'ex.: Verifique se o mel realmente nunca estraga',
  'e.g. How to roast a duck so the skin stays crispy': 'ex.: Como assar um pato para a pele ficar crocante',
  'e.g. The collapse of Bronze Age civilizations — leading theories and the evidence behind each': 'ex.: O colapso das civilizações da Idade do Bronze — principais teorias e as evidências de cada uma',
  'e.g. Best M.2 NVMe SSDs under $200 for a home AI workstation': 'ex.: Melhores SSDs M.2 NVMe abaixo de $200 para uma workstation de IA doméstica',
  'e.g. Why do cats knead with their paws? Cover the leading behavioural explanations': 'ex.: Por que os gatos amassam com as patas? Cubra as principais explicações comportamentais',
  'e.g. Side effects and benefits of long-term creatine supplementation': 'ex.: Efeitos colaterais e benefícios da suplementação de creatina a longo prazo',
  'e.g. How does end-to-end encryption work in Signal, step by step': 'ex.: Como funciona a criptografia de ponta a ponta no Signal, passo a passo',
  'e.g. The history of the printing press in East Asia, 700 CE → 1600 CE': 'ex.: A história da prensa tipográfica no Leste Asiático, 700 d.C. → 1600 d.C.',
  'Deep Research': 'Pesquisa profunda',
  '— past runs in': '— execuções anteriores em',
  'Library, Research': 'Biblioteca, Pesquisa',
  'Multi-step web research with an LLM-in-the-loop agent': 'Pesquisa web multi-etapas com um agente LLM no circuito',
  'Rounds': 'Rodadas',
  'How many search → read → reflect rounds the agent runs. More rounds = deeper coverage, longer wait, more tokens.':
    'Quantas rodadas de buscar → ler → refletir o agente executa. Mais rodadas = cobertura mais profunda, espera maior, mais tokens.',
  'Format': 'Formato',
  'Auto lets the LLM pick the output shape. Override when you specifically want a Compare table, How-to, Product, or Fact-check.':
    'Automático deixa o LLM escolher o formato de saída. Substitua quando quiser especificamente uma tabela Comparar, Como fazer, Produto ou Verificação de fatos.',
  'How-to': 'Como fazer',
  'Fact-check': 'Verificação de fatos',
  'Search engine': 'Mecanismo de busca',
  'Queue': 'Enfileirar',

  // --- research/panel.js: start handler, job sections, run-mode popover ---
  'Starting': 'Iniciando',
  'Failed to start research': 'Falha ao iniciar pesquisa',
  'Start All ({n})': 'Iniciar tudo ({n})',
  'Clear all research': 'Limpar toda a pesquisa',
  'Clear all': 'Limpar tudo',
  'Active': 'Ativas',
  'Past research': 'Pesquisas anteriores',
  'Parallel': 'Paralelo',
  'Sequential': 'Sequencial',

  // --- research/panel.js: job card states ---
  'Auto rounds': 'Rodadas automáticas',
  '{n} rounds': '{n} rodadas',
  'Edit query': 'Editar consulta',
  'Cancel research': 'Cancelar pesquisa',
  'no results': 'sem resultados',
  'standard': 'padrão',
  "Couldn't extract anything — try rephrasing the question, or switch the search engine in Settings.":
    'Não foi possível extrair nada — tente reformular a pergunta ou trocar o mecanismo de busca em Configurações.',
  '{n} sources': '{n} fontes',
  'Visual report': 'Relatório visual',
  'Visual Report': 'Relatório Visual',
  'Open follow-up chat with this research as context': 'Abrir chat de acompanhamento com esta pesquisa como contexto',
  'Copy report to clipboard': 'Copiar relatório para a área de transferência',
  'Clear from list': 'Remover da lista',
  'Delete from disk': 'Excluir do disco',

  // --- research/panel.js: error card, result rendering, copy/chat ---
  'cancelled': 'cancelada',
  'Edit and retry': 'Editar e tentar novamente',
  'Product': 'Produto',
  'Comparison': 'Comparação',
  'How-to Guide': 'Guia de Como Fazer',
  'Landscape': 'Panorama',
  'Loading result...': 'Carregando resultado...',
  '+{n} more': '+{n} mais',
  'Raw Findings': 'Achados Brutos',
  'Source:': 'Fonte:',
  'Delete this research? This permanently removes it from disk.': 'Excluir esta pesquisa? Isso a remove permanentemente do disco.',
  'Creating…': 'Criando…',
  'Server returned no session id': 'O servidor não retornou um id de sessão',
  'Could not start follow-up chat: {msg}': 'Não foi possível iniciar o chat de acompanhamento: {msg}',

  // --- sessions.js: folder submenu ---
  'Move to folder': 'Mover para pasta',
  '(No folder)': '(Sem pasta)',
  '+ New Folder': '+ Nova pasta',
  'Name this folder:': 'Nomeie esta pasta:',
  'e.g. Work, Research, Drafts': 'ex.: Trabalho, Pesquisa, Rascunhos',

  // --- sessions.js: session list item + dropdown menu ---
  'Drag to reorder': 'Arraste para reordenar',
  '[archived]': '[arquivado]',
  'Session actions': 'Ações da sessão',
  'Copy Chat': 'Copiar conversa',
  'No messages to copy': 'Nenhuma mensagem para copiar',
  'You': 'Você',
  'AI': 'IA',
  'Chat copied to clipboard': 'Conversa copiada para a área de transferência',
  'Failed to copy chat': 'Falha ao copiar a conversa',
  'Unfavorite before deleting': 'Desfavorite antes de excluir',
  'Delete this session?': 'Excluir esta sessão?',
  'Session archived': 'Sessão arquivada',
  'Failed to archive session': 'Falha ao arquivar sessão',

  // --- sessions.js: session list render (sort/folders) ---
  'Show less': 'Mostrar menos',
  'Show {n} more': 'Mostrar mais {n}',
  'Drag to reorder folder': 'Arraste para reordenar pasta',
  'Delete folder and all sessions': 'Excluir pasta e todas as sessões',
  'Delete folder "{folderName}" and all {count} session(s) inside it?': 'Excluir a pasta "{folderName}" e todas as {count} sessão(ões) dentro dela?',
  'Rename folder:': 'Renomear pasta:',
  'Rename folder': 'Renomear pasta',
  'Unsorted': 'Sem pasta',
  'Delete all unsorted sessions': 'Excluir todas as sessões sem pasta',
  'Delete all {n} unsorted session(s)?': 'Excluir todas as {n} sessão(ões) sem pasta?',
  'swipe to delete': 'deslize para excluir',

  // --- sessions.js: bulk select actions ---
  'Archive {n} session(s)?': 'Arquivar {n} sessão(ões)?',
  '{n} session(s) archived': '{n} sessão(ões) arquivada(s)',
  'Delete {n} session(s)? This cannot be undone.': 'Excluir {n} sessão(ões)? Isso não pode ser desfeito.',
  '{n} session(s) deleted': '{n} sessão(ões) excluída(s)',

  // --- sessions.js: loadSessions / selectSession / createDirectChat ---
  'Failed to load sessions: {msg}': 'Falha ao carregar sessões: {msg}',
  'OpenClaw Agent Connected': 'Agente OpenClaw conectado',
  'Messages will be routed through your OpenClaw agent. The agent has access to tools, memory, and skills configured in your OpenClaw workspace.':
    'As mensagens serão roteadas através do seu agente OpenClaw. O agente tem acesso a ferramentas, memória e habilidades configuradas no seu workspace OpenClaw.',
  'Failed to load session: {msg}': 'Falha ao carregar sessão: {msg}',
  'Failed to reach backend: {msg}': 'Falha ao acessar o backend: {msg}',
  'Session create failed ({status}) {detail}': 'Falha ao criar sessão ({status}) {detail}',
  'Generating response...': 'Gerando resposta...',

  // --- sessions.js: archive browser ---
  '(archived)': '(arquivada)',
  'Failed to open archived session': 'Falha ao abrir sessão arquivada',
  'Session restored': 'Sessão restaurada',
  'Failed to restore session': 'Falha ao restaurar sessão',
  'Delete this session permanently?': 'Excluir esta sessão permanentemente?',
  'Session deleted': 'Sessão excluída',
  'Failed to delete session': 'Falha ao excluir a sessão',
  '{n} session(s) restored': '{n} sessão(ões) restaurada(s)',
  'Delete {n} session(s) permanently?': 'Excluir {n} sessão(ões) permanentemente?',
});
