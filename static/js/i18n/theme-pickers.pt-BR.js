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
});
