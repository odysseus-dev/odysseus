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
  'Deep Research': 'Pesquisa Profunda',
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
});
