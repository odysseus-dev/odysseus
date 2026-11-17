/** Compatibility URL. All consumers share the canonical Notes feature instance. */
import notesModule, { startNotesBackground } from './features/notes/index.js';
export { default, openPanel, closePanel, togglePanel, isPanelOpen, openNote, newNote, refreshDueBadge, dismissFiredReminderDot, openNotes, closeNotes, isNotesOpen } from './features/notes/index.js';
window.notesModule = notesModule;
startNotesBackground();
