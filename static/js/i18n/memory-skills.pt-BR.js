// ============================================
// pt-BR dictionary — Memory & skills
// ============================================
// Filled by the "Memory & skills" i18n work unit. Auto-loaded via static/app.js.
// Keys are the EXACT English source strings; values are the pt-BR translations.
import { registerMessages } from '../i18n.js';

registerMessages('pt-BR', {
  // ---- memory.js — relative time ----
  'just now': 'agora mesmo',
  '{n}m ago': 'há {n}m',
  '{n}h ago': 'há {n}h',
  '{n}d ago': 'há {n}d',
  '{n}w ago': 'há {n}sem',
  '{n}mo ago': 'há {n}mes',
  '{n}y ago': 'há {n}a',

  // ---- memory.js — select mode ----
  'Cancel': 'Cancelar',
  'Select': 'Selecionar',
  '{n} Selected': '{n} selecionado(s)',

  // ---- memory.js — bulk delete ----
  'Delete {n} {noun}?': 'Excluir {n} {noun}?',
  'memory': 'memória',
  'memories': 'memórias',
  'Delete': 'Excluir',
  'Deleted {n} {noun}': '{n} {noun} excluído(s)',

  // ---- memory.js — pref toggles ----
  'Memory enabled': 'Memória ativada',
  'Memory disabled': 'Memória desativada',
  'Skills enabled': 'Skills ativadas',
  'Skills disabled': 'Skills desativadas',
  'Auto-extract memories enabled': 'Extração automática de memórias ativada',
  'Auto-extract memories disabled': 'Extração automática de memórias desativada',
  'Auto-extract skills enabled': 'Extração automática de skills ativada',
  'Auto-extract skills disabled': 'Extração automática de skills desativada',
  'Auto-approve skills enabled': 'Aprovação automática de skills ativada',
  'Auto-approve skills disabled': 'Aprovação automática de skills desativada',
  'Failed to save preference': 'Falha ao salvar preferência',

  // ---- memory.js — confidence / injection ----
  'Skill confidence: All': 'Confiança de skill: Todas',
  'Skill confidence ≥ {n}%': 'Confiança de skill ≥ {n}%',
  'No skills injected': 'Nenhuma skill injetada',
  'Max injected skills: {n}': 'Máximo de skills injetadas: {n}',

  // ---- memory.js — tidy ----
  'Tidy': 'Organizar',
  'Already clean': 'Já está limpo',
  'Tidying memories': 'Organizando memórias',
  'Tidied: {removed} removed ({before} → {after})': 'Organizado: {removed} removido(s) ({before} → {after})',
  'Tidy failed — check console': 'Falha ao organizar — verifique o console',

  // ---- memory.js — empty states ----
  'No matches.': 'Nenhum resultado.',
  'No memories yet': 'Nenhuma memória ainda',
  'Import in Add tab': 'Importar na aba Adicionar',

  // ---- memory.js — memory item badges ----
  'pinned': 'fixada',
  'auto': 'auto',
  'manual': 'manual',
  'Injected into chat context {n} time{s}': 'Injetada no contexto do chat {n} vez{s}',

  // ---- memory.js — memory item actions ----
  'Actions': 'Ações',
  'Unpin': 'Desafixar',
  'Pin': 'Fixar',
  'Edit': 'Editar',
  'save': 'salvar',
  'cancel': 'cancelar',
  'saved': 'salvo',
  'save all': 'salvar tudo',
  'back': 'voltar',
  'delete': 'excluir',

  // ---- memory.js — save / update / delete toasts ----
  'Memory updated': 'Memória atualizada',
  'Failed to update memory': 'Falha ao atualizar memória',
  'Memory text cannot be empty': 'O texto da memória não pode estar vazio',
  'Memory added': 'Memória adicionada',
  'Failed to add memory': 'Falha ao adicionar memória',
  'Pinned — always in context': 'Fixada — sempre no contexto',
  'Unpinned — RAG only': 'Desafixada — somente RAG',
  'Failed to update pin': 'Falha ao atualizar fixação',
  'Delete this memory?\n"{text}"': 'Excluir esta memória?\n"{text}"',
  'Memory deleted': 'Memória excluída',
  'Failed to delete': 'Falha ao excluir',
  'Failed to delete memory': 'Falha ao excluir memória',

  // ---- memory.js — extract / suggestions ----
  'Failed to extract memory suggestions': 'Falha ao extrair sugestões de memória',
  'Suggested memories': 'Memórias sugeridas',
  'No useful information detected.': 'Nenhuma informação útil detectada.',
  'Saved to memory': 'Salvo na memória',

  // ---- memory.js — export / import ----
  'No memories to export': 'Nenhuma memória para exportar',
  'Exported {n} memories': '{n} memória(s) exportada(s)',
  'Importing': 'Importando',
  'No useful information found in file.': 'Nenhuma informação útil encontrada no arquivo.',
  'Saved {n} memories': '{n} memória(s) salva(s)',
  'Import failed — {msg}': 'Falha na importação — {msg}',

  // ---- memory.js — count ----
  // (memory / memories already registered above)

  // ---- skills.js — skill list empty state ----
  'No skills yet, use agent for it to auto extract them.': 'Nenhuma skill ainda. Use o agente para que ele as extraia automaticamente.',

  // ---- skills.js — built-in card ----
  'This is a built-in capability. Editing changes how the assistant is instructed to use this native tool — it can break or alter core behaviour. Use Revert to restore the shipped default.':
    'Esta é uma capacidade integrada. Editar muda como o assistente é instruído a usar esta ferramenta nativa — pode quebrar ou alterar o comportamento principal. Use Reverter para restaurar o padrão original.',
  'Restore the original shipped instructions': 'Restaurar as instruções originais',

  // ---- skills.js — buttons ----
  'Save': 'Salvar',
  'Unpublish': 'Despublicar',
  'Publish': 'Publicar',
  'Test': 'Testar',
  'Test this skill — run it + AI judge': 'Testar esta skill — executar + avaliação por IA',
  'Move back to draft': 'Mover de volta para rascunho',
  'Run the test again': 'Executar o teste novamente',
  'Copy the run output + verdict': 'Copiar a saída da execução + veredicto',
  'Failed to load.': 'Falha ao carregar.',

  // ---- skills.js — toasts ----
  'Saved': 'Salvo',
  'Save failed: {msg}': 'Falha ao salvar: {msg}',
  'Built-in capability updated': 'Capacidade integrada atualizada',
  'Reverted to default': 'Revertido ao padrão',
  'Revert failed: {msg}': 'Falha ao reverter: {msg}',
  'Skill deleted': 'Skill excluída',
  'Delete failed: {msg}': 'Falha ao excluir: {msg}',
  'Skill approved': 'Skill aprovada',
  'Skill moved to draft': 'Skill movida para rascunho',
  'Update failed: {msg}': 'Falha ao atualizar: {msg}',
  'Skill added (draft)': 'Skill adicionada (rascunho)',
  'Failed to add skill: {msg}': 'Falha ao adicionar skill: {msg}',
  'Paste a GitHub or skills.sh URL first': 'Cole primeiro uma URL do GitHub ou skills.sh',
  'Imported {name} ({n} file(s))': '{name} importada ({n} arquivo(s))',
  'Import failed: {msg}': 'Falha na importação: {msg}',
  'Published {n}': '{n} publicada(s)',
  'Deleted {n}': '{n} excluída(s)',
  'Deleted {n} non-passing': '{n} skill(s) não aprovada(s) excluída(s)',
  'No selected skills to audit': 'Nenhuma skill selecionada para auditar',
  'No visible skills to audit': 'Nenhuma skill visível para auditar',
  'Audit failed to start (HTTP {status})': 'Falha ao iniciar auditoria (HTTP {status})',
  'Audit failed: {msg}': 'Falha na auditoria: {msg}',
  'No selected non-passing skills': 'Nenhuma skill não aprovada selecionada',
  'Failed to load SKILL.md': 'Falha ao carregar SKILL.md',
  'Description (or name) is required': 'Descrição (ou nome) é obrigatório',

  // ---- skills.js — delete confirm ----
  'Delete skill "{name}"? This removes the SKILL.md.': 'Excluir skill "{name}"? Isso remove o SKILL.md.',
  'Delete {n} {skills}? This removes their SKILL.md files.': 'Excluir {n} {skills}? Isso remove os arquivos SKILL.md.',
  'Delete {n} selected non-passing {skills}? This removes duplicates, generic/irrelevant skills, failed audits, and anything below {pct}%.':
    'Excluir {n} {skills} não aprovada(s) selecionada(s)? Remove duplicatas, skills genéricas/irrelevantes, auditorias com falha e qualquer coisa abaixo de {pct}%.',
  'skill': 'skill',
  'skills': 'skills',
  'Delete non passing': 'Excluir não aprovadas',
  'Revert "{name}" to its original built-in instructions?': 'Reverter "{name}" para as instruções integradas originais?',
  'Revert': 'Reverter',

  // ---- skills.js — bulk bar counts ----
  'Delete {count} selected non-passing {noun}': 'Excluir {count} {noun} não aprovada(s) selecionada(s)',
  'No selected non-passing {noun}': 'Nenhuma {noun} não aprovada selecionada',

  // ---- skills.js — audit confirm ----
  'Audit Skills': 'Auditar Skills',
  'Skip already audited': 'Pular já auditadas',
  'Audit': 'Auditar',
  'Audit {label}? Each is tested from top to bottom, then published or moved to draft using your auto-approve confidence threshold.':
    'Auditar {label}? Cada uma é testada de cima para baixo, depois publicada ou movida para rascunho conforme o limiar de confiança para aprovação automática.',

  // ---- skills.js — audit panel ----
  'Auditing {done}/{total}{current}': 'Auditando {done}/{total}{current}',
  'Audit cancelled — {done}/{total}': 'Auditoria cancelada — {done}/{total}',
  'Audit complete — {total} skill{s}': 'Auditoria concluída — {total} skill{s}',
  'Close': 'Fechar',
  'Cancelling...': 'Cancelando...',

  // ---- skills.js — test log events ----
  'Task: {task}': 'Tarefa: {task}',
  'Model: {model}': 'Modelo: {model}',
  '— round {n} —': '— rodada {n} —',
  'Evaluating run...': 'Avaliando execução...',
  'Error: {msg}': 'Erro: {msg}',
  '...running (you can close this — it keeps going)': '...executando (você pode fechar — continua rodando)',
  'Starting test...': 'Iniciando teste...',
  'Test failed: HTTP {status}': 'Falha no teste: HTTP {status}',
  'Test failed: {msg}': 'Falha no teste: {msg}',
  'Starting…': 'Iniciando…',

  // ---- skills.js — verdict actions ----
  'Approved': 'Aprovada',
  'Approve': 'Aprovar',
  'Already approved — click to unpublish': 'Já aprovada — clique para despublicar',
  'Publish — appears in the skills index': 'Publicar — aparece no índice de skills',
  'Retry': 'Tentar novamente',
  'Copy': 'Copiar',

  // ---- rag.js ----
  'Drop files above to add to RAG': 'Arraste arquivos acima para adicionar ao RAG',
  'Remove from RAG': 'Remover do RAG',
  'Failed to load files': 'Falha ao carregar arquivos',
  'Remove "{name}" from RAG?': 'Remover "{name}" do RAG?',
  'Remove': 'Remover',
  'Failed to delete file: {msg}': 'Falha ao excluir arquivo: {msg}',
  'Uploading…': 'Enviando…',
  'Drop files here or click to upload': 'Arraste arquivos aqui ou clique para enviar',
  'Upload failed: {msg}': 'Falha no envio: {msg}',
  'Loading…': 'Carregando…',
});
