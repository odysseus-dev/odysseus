// ============================================
// pt-BR dictionary — Tasks
// ============================================
// Filled by the "Tasks" i18n work unit. Auto-loaded via static/app.js.
// Keys are the EXACT English source strings; values are the pt-BR translations.
import { registerMessages } from '../i18n.js';

registerMessages('pt-BR', {
  // --- _relativeTime ---
  'just now': 'agora mesmo',
  'in a moment': 'em instantes',
  '{m}m ago': 'há {m}m',
  'in {m}m': 'em {m}m',
  '{h}h ago': 'há {h}h',
  'in {h}h': 'em {h}h',
  '{days}d ago': 'há {days}d',
  'in {days}d': 'em {days}d',

  // --- _scheduleLabel ---
  'Every {n} {evtName}{s}': 'A cada {n} {evtName}{s}',
  'Webhook': 'Webhook',
  'Cron: {expr}': 'Cron: {expr}',
  'Once on {date} at {time}': 'Uma vez em {date} às {time}',
  'Once': 'Uma vez',
  'Daily at {localTime}': 'Diariamente às {localTime}',
  'Weekly on {day} at {localTime}': 'Semanalmente na {day} às {localTime}',
  'Monthly on {d}{suffix} at {localTime}': 'Mensalmente no dia {d}{suffix} às {localTime}',

  // --- _renderList ---
  'Loading...': 'Carregando...',
  'No tasks yet. Create one to get started.': 'Nenhuma tarefa ainda. Crie uma para começar.',
  'No matching tasks.': 'Nenhuma tarefa encontrada.',
  '{n} task(s)': '{n} tarefa(s)',
  'Next: {time}': 'Próxima: {time}',
  '{n} run(s)': '{n} execução(ões)',
  'Last: {time}': 'Última: {time}',

  // --- status badges ---
  'Paused - click to resume': 'Pausada - clique para retomar',
  'Active - click to pause': 'Ativa - clique para pausar',
  'paused': 'pausada',
  'active': 'ativa',

  // --- kebab menu items ---
  'Run now': 'Executar agora',
  'Edit': 'Editar',
  'Pause': 'Pausar',
  'Resume': 'Retomar',
  'History': 'Histórico',
  'Revert to default': 'Restaurar padrão',
  'Clear cache': 'Limpar cache',
  'Delete': 'Excluir',

  // --- run card button ---
  'Run': 'Executar',

  // --- toasts & confirms ---
  'Task paused': 'Tarefa pausada',
  'Task resumed': 'Tarefa retomada',
  'Task updated': 'Tarefa atualizada',
  'Task created': 'Tarefa criada',
  'Task deleted': 'Tarefa excluída',
  'Task stopped': 'Tarefa interrompida',
  'Task triggered': 'Tarefa iniciada',
  'Task triggered in parallel': 'Tarefa iniciada em paralelo',
  'Reverted to default': 'Restaurado ao padrão',

  // --- confirms ---
  'Delete this task and all its run history?': 'Excluir esta tarefa e todo o histórico de execuções?',
  'Revert this built-in task to its default schedule and settings?': 'Restaurar esta tarefa integrada ao agendamento e configurações padrão?',
  'Revert': 'Restaurar',
  'Revert this built-in task to its default?': 'Restaurar esta tarefa integrada ao padrão?',
  'Clear cached {label} for this task?': 'Limpar o cache de {label} para esta tarefa?',
  'Clear': 'Limpar',
  'Cleared {label}{count}': '{label} limpo(s){count}',
  'Clear cache failed: {msg}': 'Falha ao limpar cache: {msg}',

  // --- toggle all ---
  'No tasks to pause': 'Nenhuma tarefa para pausar',
  'No tasks to resume': 'Nenhuma tarefa para retomar',
  '{verb} all {n} {state} task(s)?': '{verb} todas as {n} tarefa(s) {state}?',
  '{verb} all': '{verb} todas',
  '{verb} {n} task(s)?': '{verb} {n} tarefa(s)?',
  '{verbPast} all {n} task(s)': '{verbPast} todas as {n} tarefa(s)',
  '{verbPast} {ok}/{total} — failed: {names}': '{verbPast} {ok}/{total} — falhou: {names}',
  'Paused': 'Pausadas',
  'Resumed': 'Retomadas',

  // --- pause all button ---
  'Pause all': 'Pausar todas',
  'Resume all': 'Retomar todas',
  'Pause every active task': 'Pausar todas as tarefas ativas',
  'Resume every paused task': 'Retomar todas as tarefas pausadas',

  // --- form ---
  'Edit Task': 'Editar Tarefa',
  'New Task': 'Nova Tarefa',
  'Update this task’s schedule, prompt, and output.': 'Atualize o agendamento, prompt e saída desta tarefa.',
  'Configure a prompt, research, or action to run automatically.': 'Configure um prompt, pesquisa ou ação para executar automaticamente.',
  'Auto-generated if blank': 'Gerado automaticamente se vazio',
  'Cancel': 'Cancelar',
  'Save': 'Salvar',
  'Create': 'Criar',
  'Prompt is required': 'O prompt é obrigatório',
  'Select an action': 'Selecione uma ação',
  'Failed to save urgency rules': 'Falha ao salvar regras de urgência',
  'Cron expression is required': 'A expressão cron é obrigatória',
  'Select an event': 'Selecione um evento',

  // --- preset picker ---
  'Add Task': 'Adicionar Tarefa',
  'Describe a task for the AI to draft, or pick a type below to set one up manually.': 'Descreva uma tarefa para a IA rascunhar, ou escolha um tipo abaixo para configurar manualmente.',
  'Describe a task — e.g. &quot;every weekday 7am summarize my unread email&quot;': 'Descreva uma tarefa — ex: &quot;todo dia útil às 7h resumir meus e-mails não lidos&quot;',
  'Draft a task with AI': 'Rascunhar tarefa com IA',
  'Draft with AI': 'Rascunhar com IA',

  // --- preset labels (from _TASK_PRESETS) ---
  'Prompt on schedule': 'Prompt agendado',
  'Run a prompt daily, weekly, etc.': 'Executar um prompt diariamente, semanalmente, etc.',
  'Prompt on event': 'Prompt por evento',
  'Trigger every N sessions or messages': 'Disparar a cada N sessões ou mensagens',
  'Research on schedule': 'Pesquisa agendada',
  'Run deep research on a topic': 'Executar pesquisa profunda sobre um tópico',
  'Research on event': 'Pesquisa por evento',
  'Run deep research after app events': 'Executar pesquisa profunda após eventos do app',
  'Action on schedule': 'Ação agendada',
  'Run tidy/cleanup on a timer': 'Executar organização/limpeza em horário agendado',
  'Action on event': 'Ação por evento',
  'Run tidy/cleanup every N sessions or messages': 'Executar organização/limpeza a cada N sessões ou mensagens',
  'Webhook triggered': 'Disparado por webhook',
  'Trigger via external HTTP call': 'Disparar via chamada HTTP externa',

  // --- form type opts ---
  'What should be researched?': 'O que deve ser pesquisado?',
  'What should the AI do?': 'O que a IA deve fazer?',
  'Research question': 'Questão de pesquisa',
  'Prompt': 'Prompt',
  'Persona': 'Persona',
  '(optional — biases the output voice)': '(opcional — influencia o estilo de resposta)',
  'Action': 'Ação',
  'Email triage rules': 'Regras de triagem de e-mail',
  'What should count as urgent? e.g. deadlines, blockers, people waiting outside.': 'O que deve ser considerado urgente? Ex: prazos, bloqueadores, pessoas aguardando resposta.',
  'Pause/resume and schedule are controlled by this task. It tags urgent, reply-soon, newsletter, marketing, and spam. Urgent/reply-soon emails use your reminder settings.': 'Pausa/retomada e agendamento são controlados por esta tarefa. Ela marca e-mails como urgente, responder-em-breve, newsletter, marketing e spam. E-mails urgentes/responder-em-breve usam suas configurações de lembrete.',

  // --- form trigger opts ---
  'Frequency': 'Frequência',
  'Daily': 'Diariamente',
  'Weekly': 'Semanalmente',
  'Monthly': 'Mensalmente',
  'Time': 'Horário',
  'Day of week': 'Dia da semana',
  'Day of month': 'Dia do mês',
  'Date': 'Data',
  'Cron expression': 'Expressão cron',
  'min hour day month weekday — e.g. "0 */2 * * *" = every 2 hours': 'min hora dia mês dia-da-semana — ex: "0 */2 * * *" = a cada 2 horas',
  'Event': 'Evento',
  'Every N occurrences': 'A cada N ocorrências',
  'Webhook URL': 'URL do Webhook',
  'Copy': 'Copiar',
  'POST this URL from any external service to trigger the task. No auth needed.': 'Faça um POST nesta URL de qualquer serviço externo para disparar a tarefa. Sem autenticação necessária.',
  'Copied': 'Copiado',
  'Webhook URL will be generated when the task is saved.': 'A URL do Webhook será gerada quando a tarefa for salva.',

  // --- main view ---
  'Ongoing Tasks': 'Tarefas em Andamento',
  'Pause all active tasks': 'Pausar todas as tarefas ativas',
  'Sort tasks': 'Ordenar tarefas',
  'Recent': 'Recentes',
  'A–Z': 'A–Z',
  'Status': 'Status',
  'Select tasks': 'Selecionar tarefas',
  'Select': 'Selecionar',
  'Search tasks...': 'Pesquisar tarefas...',
  'Scheduled prompts and actions that run automatically. Results appear in a dedicated session.': 'Prompts e ações agendados que executam automaticamente. Resultados aparecem em uma sessão dedicada.',

  // --- activity view ---
  'Activity': 'Atividade',
  'Refresh': 'Atualizar',
  'Recent task runs across all scheduled tasks.': 'Execuções recentes de todas as tarefas agendadas.',
  'Filter activity...': 'Filtrar atividade...',
  'No matching activity.': 'Nenhuma atividade encontrada.',
  'No activity yet. Scheduled tasks will log here once they run.': 'Nenhuma atividade ainda. As tarefas agendadas serão registradas aqui após executarem.',
  'Failed to load activity: {msg}': 'Falha ao carregar atividade: {msg}',

  // --- activity entry buttons ---
  'Open this result in a chat to read full-width + ask follow-ups': 'Abrir este resultado em um chat para leitura completa e perguntas adicionais',
  'Open in chat': 'Abrir no chat',
  'Open the visual research report': 'Abrir o relatório visual de pesquisa',
  'Visual report': 'Relatório visual',
  'Copy this log entry': 'Copiar esta entrada do log',
  'Copy log': 'Copiar log',
  'Clear cached {label} for this task': 'Limpar o cache de {label} para esta tarefa',
  'Run this task again': 'Executar esta tarefa novamente',
  'Run again': 'Executar novamente',

  // --- running rows ---
  'Queued': 'Na fila',
  'Still running': 'Ainda em execução',
  'Running': 'Em execução',
  'Start now in parallel, bypassing the queue': 'Iniciar agora em paralelo, ignorando a fila',
  'Start now': 'Iniciar agora',
  'Stop this task': 'Parar esta tarefa',
  'Show more': 'Ver mais',

  // --- log copy ---
  'Log copied': 'Log copiado',
  'Copy failed': 'Falha ao copiar',

  // --- open in chat ---
  "Couldn't create chat (HTTP {status})": 'Não foi possível criar o chat (HTTP {status})',
  'Chat created but no session id returned': 'Chat criado mas sem ID de sessão retornado',
  'Open in chat failed: {msg}': 'Falha ao abrir no chat: {msg}',

  // --- AI draft ---
  'Could not draft task': 'Não foi possível rascunhar a tarefa',
  'AI draft failed: {msg}': 'Falha no rascunho com IA: {msg}',

  // --- form extra labels ---
  'Name': 'Nome',
  'Type': 'Tipo',
  'Trigger': 'Gatilho',
  'Output': 'Saída',
  'Session': 'Sessão',
  'Model': 'Modelo',
  '(optional — overrides session default)': '(opcional — substitui o padrão da sessão)',
  'Use session default': 'Usar padrão da sessão',
  'Chain': 'Encadear',
  'None': 'Nenhum',
  'Notifications': 'Notificações',
  '— uncheck to silence completion notifications for this task (helpful for chatty cron jobs)': '— desmarque para silenciar notificações de conclusão desta tarefa (útil para cron jobs verbosos)',

  // --- run meta ---
  'model: {model}': 'modelo: {model}',
  'Email: {addr}': 'E-mail: {addr}',
  '{model} (unlisted endpoint)': '{model} (endpoint não listado)',

  // --- builtin badge ---
  'Built-in task — edited from its default': 'Tarefa integrada — editada a partir do padrão',
  'Built-in task': 'Tarefa integrada',
  'built-in': 'integrada',
  'edited': 'editada',

  // --- card detail ---
  'Failed (no detail)': 'Falhou (sem detalhes)',
  'Success (no output)': 'Sucesso (sem saída)',
  'Open full history': 'Abrir histórico completo',

  // --- actions tooltip ---
  'Actions': 'Ações',
  'Cancel (Esc)': 'Cancelar (Esc)',

  // --- polling notifications ---
  'Task finished: {name}': 'Tarefa concluída: {name}',
  'Task failed: {name}': 'Tarefa falhou: {name}',
  'Failed to trigger task': 'Falha ao disparar a tarefa',
  'Task is already running': 'A tarefa já está em execução',
  'Failed to stop task': 'Falha ao parar a tarefa',

  // --- repeat badge ---
  '{n} similar activity rows': '{n} linhas de atividade semelhantes',
  'repeats': 'repetições',
  'skipped': 'ignorada',
  'failed': 'falhou',

  // --- run history ---
  'Back': 'Voltar',
  'Run history': 'Histórico de execuções',
  'No runs yet.': 'Nenhuma execução ainda.',

  // --- bulk select ---
  '{n} Selected': '{n} selecionada(s)',
  'Delete {n} task(s)? This cannot be undone.': 'Excluir {n} tarefa(s)? Esta ação não pode ser desfeita.',
  'Delete {n} task(s)?': 'Excluir {n} tarefa(s)?',
  'Deleted {n} task(s)': '{n} tarefa(s) excluída(s)',
});
