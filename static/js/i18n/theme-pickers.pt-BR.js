// ============================================
// pt-BR dictionary — Theme & model pickers
// ============================================
// Filled by the "Theme & model pickers" i18n work unit. Auto-loaded via static/app.js.
// Keys are the EXACT English source strings; values are the pt-BR translations.
import { registerMessages } from '../i18n.js';

registerMessages('pt-BR', {
  // --- theme.js ---
  'Delete theme': 'Excluir tema',
  'Delete theme "{name}"?': 'Excluir o tema "{name}"?',
  'Delete': 'Excluir',
  'Enter a name.': 'Digite um nome.',
  'Invalid name.': 'Nome inválido.',
  'Cannot overwrite a built-in theme.': 'Não é possível substituir um tema padrão.',
  'Max {n} custom themes. Delete one first.': 'Máximo de {n} temas personalizados. Exclua um antes.',
  'Theme saved': 'Tema salvo',
  'Downloaded!': 'Baixado!',
  'Export': 'Exportar',
  'Invalid JSON.': 'JSON inválido.',
  'Missing: {list}': 'Faltando: {list}',
  'Bad hex for {k}': 'Hex inválido para {k}',
  'Auto-saved': 'Salvo automaticamente',
  'original': 'original',

  // --- modelPicker.js ---
  'endpoint offline': 'endpoint offline',
  'not responding': 'não está respondendo',
  'Search models…': 'Buscar modelos…',
  'No models connected': 'Nenhum modelo conectado',
  'Local server appears offline: {reason}. Click to try anyway, or relaunch in Cookbook.':
    'O servidor local parece offline: {reason}. Clique para tentar mesmo assim, ou reinicie no Cookbook.',
  'No matching models': 'Nenhum modelo encontrado',
  'Favorites': 'Favoritos',
  'Recent': 'Recentes',
  'All models': 'Todos os modelos',
  'Remove from favorites': 'Remover dos favoritos',
  'Add to favorites': 'Adicionar aos favoritos',
  'Favorited': 'Favoritado',
  'Unfavorited': 'Desfavoritado',
  'Using {name}': 'Usando {name}',
  'Failed to set model': 'Falha ao definir o modelo',
  'Failed to set model: {error}': 'Falha ao definir o modelo: {error}',
  'Model refresh failed': 'Falha ao atualizar modelos',
  'Select model': 'Selecionar modelo',

  // --- presets.js ---
  'Expanding': 'Expandindo',
  'Expanding...': 'Expandindo...',
  'No limit': 'Sem limite',
  'Delete "{name}"?\n\nThis will remove the persona and all its memories.': 'Excluir "{name}"?\n\nIsso removerá a persona e todas as suas memórias.',
  'Default (no persona)': 'Padrão (sem persona)',
  'Saved': 'Salvos',
  'Presets': 'Predefinições',
  'Created!': 'Criado!',
  'Create Persistent Chat': 'Criar Chat Persistente',
  'Error': 'Erro',
  'Enter a name for this persona:': 'Digite um nome para esta persona:',
  'Saved!': 'Salvo!',
  'Save as Template': 'Salvar como Modelo',
  'Restart server': 'Reiniciar servidor',
  'Failed to load presets': 'Falha ao carregar predefinições',
  'Start Group': 'Iniciar Grupo',
  'Start Prompt': 'Iniciar Prompt',
  'Save & Start Persona': 'Salvar e Iniciar Persona',
  'Start Persona': 'Iniciar Persona',
  'Cancel group': 'Cancelar grupo',
  'Cancel': 'Cancelar',
  'Persistent chat — persona is locked. Style, temperature, and memory can still be changed.':
    'Chat persistente — a persona está bloqueada. Estilo, temperatura e memória ainda podem ser alterados.',
  'Something went wrong. Saved prompt has been undone.': 'Algo deu errado. O prompt salvo foi desfeito.',
  'Something went wrong. Saved persona has been undone.': 'Algo deu errado. A persona salva foi desfeita.',
  'Prompt saved': 'Prompt salvo',
  'Persona saved': 'Persona salva',
  'Failed to save custom preset': 'Falha ao salvar predefinição personalizada',
  'Persona: {name} — click to configure': 'Persona: {name} — clique para configurar',
  'Prompt': 'Prompt',
  'Custom settings active — click to configure': 'Configurações personalizadas ativas — clique para configurar',
});
