// ============================================
// pt-BR dictionary — strings rendered by the app orchestrator (static/app.js)
// ============================================
import { registerMessages } from '../i18n.js';

registerMessages('pt-BR', {
  '· {count} message': '· {count} mensagem',
  '· {count} messages': '· {count} mensagens',
  'User': 'Usuário',
  'Assistant': 'Assistente',
  '[Tool calls]': '[Chamadas de ferramentas]',
  'tool': 'ferramenta',
  'failed': 'falhou',
  'done': 'concluído',
  'cmd': 'comando',
  'out': 'saída',
  'Odysseus Chat': 'Conversa do Odysseus',
  'Untitled': 'Sem título',
  'Nothing to copy yet': 'Nada para copiar ainda',
  'Saved to documents': 'Salvo nos documentos',
  'Failed to save to documents': 'Falha ao salvar nos documentos',
  'Could not create document': 'Não foi possível criar o documento',
  'Manual order': 'Ordem manual',
  'Sorted: {label}': 'Ordenado: {label}',
  'Auto-sort failed': 'Falha ao organizar automaticamente',
  'Cleaned 1 empty/throwaway chat': '1 conversa vazia ou descartável removida',
  'Cleaned {count} empty/throwaway chats':
    '{count} conversas vazias ou descartáveis removidas',
  'Already clean': 'Já está tudo organizado',
  'Sorted 1 chat into 1 folder': '1 conversa organizada em 1 pasta',
  'Sorted 1 chat into {folders} folders':
    '1 conversa organizada em {folders} pastas',
  'Sorted {count} chats into 1 folder':
    '{count} conversas organizadas em 1 pasta',
  'Sorted {count} chats into {folders} folders':
    '{count} conversas organizadas em {folders} pastas',
  ' — 1 unfiled chat left; select Group again':
    ' — resta 1 conversa sem pasta; selecione Agrupar novamente',
  ' — {count} unfiled chats left; select Group again':
    ' — restam {count} conversas sem pasta; selecione Agrupar novamente',
  '1 unfiled chat — select Group again':
    '1 conversa sem pasta — selecione Agrupar novamente',
  '{count} unfiled chats — select Group again':
    '{count} conversas sem pasta — selecione Agrupar novamente',
  'All chats sorted': 'Todas as conversas foram organizadas',
  'Nothing to sort': 'Nada para organizar',
  'Auto-sort: {message}': 'Organização automática: {message}',
  'Models sorted: {label}': 'Modelos ordenados: {label}',
  'Please enter a name for the AI': 'Digite um nome para a IA',
  'AI renamed to {name}': 'IA renomeada para {name}',
  'Failed to rename AI: {message}': 'Falha ao renomear a IA: {message}',
  'Please enter a name for the session': 'Digite um nome para a sessão',
  'Session renamed to {name}': 'Sessão renomeada para {name}',
  'Session: {name}{model}{rag}{version}': 'Sessão: {name}{model}{rag}{version}',
  'Failed to rename session': 'Falha ao renomear a sessão',
  'Failed to rename session: {message}':
    'Falha ao renomear a sessão: {message}',
  'Renamed': 'Renomeado',
  'Web search on': 'Pesquisa na web ativada',
  'Web search off': 'Pesquisa na web desativada',
  'Shell on': 'Shell ativado',
  'Shell off': 'Shell desativado',
  'Web Search': 'Busca na web',
  'Searches the web for relevant information to include in the response. Results are fetched and summarized before the AI answers.':
    'Pesquisa informações relevantes na web para incluir na resposta. Os resultados são coletados e resumidos antes de a IA responder.',
  'Shell Access': 'Acesso ao shell',
  'Gives the AI access to a sandboxed shell for running commands, installing packages, and executing scripts. Use with caution.':
    'Dá à IA acesso a um shell isolado para executar comandos, instalar pacotes e rodar scripts. Use com cuidado.',
  'Tool Builder': 'Criador de ferramentas',
  'Create custom mini-apps and tools the AI can use. Describe what you need and the AI will build a tool you can reuse across conversations.':
    'Crie miniaplicativos e ferramentas personalizadas que a IA possa usar. Descreva o que você precisa, e a IA criará uma ferramenta reutilizável em outras conversas.',
  'Deep Research': 'Pesquisa profunda',
  'Deep multi-step research with source gathering and synthesis.':
    'Pesquisa profunda em várias etapas, com coleta de fontes e síntese.',
  'Multi-round web search with source analysis. Takes longer but produces comprehensive, well-sourced answers. Your next message will trigger a deep research cycle.':
    'Pesquisa na web em várias rodadas, com análise de fontes. Leva mais tempo, mas produz respostas abrangentes e bem fundamentadas. Sua próxima mensagem iniciará um ciclo de pesquisa profunda.',
  'Group chat ready — 1 model': 'Conversa em grupo pronta — 1 modelo',
  'Group chat ready — {count} models':
    'Conversa em grupo pronta — {count} modelos',
  'Disable Nobody mode': 'Desativar modo Ninguém',
  'Enable Nobody mode — no memory and no history saved':
    'Ativar modo Ninguém — sem salvar memória nem histórico',
  'Nobody': 'Ninguém',
  "Who am I? I'm nobody.": 'Quem sou eu? Não sou ninguém.',
  "Temporary session — it won't be saved or activate memory.":
    'Sessão temporária — não será salva nem ativará a memória.',
  'Rearrange enabled': 'Reorganização ativada',
  'Rearrange disabled': 'Reorganização desativada',
  'Window': 'Janela',
  'Restore {title}': 'Restaurar {title}',
  'Close': 'Fechar',
  'Minimize': 'Minimizar',
  'Session deleted': 'Sessão excluída',
  'Failed to delete session': 'Falha ao excluir a sessão',
  'Failed to delete session: {message}': 'Falha ao excluir a sessão: {message}',
  'Delete "{name}"?': 'Excluir "{name}"?',
  'Delete': 'Excluir',
  'Message Odysseus...': 'Mensagem para o Odysseus...',
  'Record voice': 'Gravar voz',
  'Send to group': 'Enviar ao grupo',
  'Send message': 'Enviar mensagem',
  'New': 'Nova',
  'New chat': 'Nova conversa',
  'Stop recording': 'Parar gravação',
  'Added 1 file to chat': '1 arquivo adicionado à conversa',
  'Added {count} files to chat': '{count} arquivos adicionados à conversa',
  'Added 1 file to attach': '1 arquivo adicionado aos anexos',
  'Added {count} files to attach': '{count} arquivos adicionados aos anexos',
  'Drop files to attach': 'Solte os arquivos para anexar',
  'Add an AI endpoint from Settings in the sidebar, or paste an endpoint/API key into the chat.':
    'Adicione um endpoint de IA em Configurações na barra lateral ou cole um endpoint ou uma chave de API na conversa.',
});
