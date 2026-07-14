// static/js/emailLibrary/state.js
//
// Shared mutable state for the email-library popup. Keeping these on a
// single exported object lets sibling modules (utils, signatureFold,
// future render/menu/composer splits) read and write the same values
// without each one importing 19 `let` bindings — which ES modules
// don't allow from outside the defining module anyway.
//
// Writes look like `state._libOpen = true` everywhere; reads look like
// `state._libOpen`. The names match the originals so the refactor is a
// pure rename, not a semantic change.

export const state = {
  _libOpen: false,
  _libJustOpened: false,
  _libEmails: [],
  _libTotal: 0,
  _libOffset: 0,
  _libFolder: 'INBOX',
  _libFolders: [],
  _libAccountId: null,           // null = backend default account
  _libAccounts: [],              // list of accounts for the chip strip
  _libPendingExpandUid: null,
  _libSearch: '',
  _libFilter: 'all',             // all, unread, unanswered
  _libSort: 'recent',            // recent, unread, favorites
  _libHasAttachments: false,
  _libLoading: false,
  _docModule: null,
  _onEmailClick: null,
  _libEscHandler: null,
  _selectMode: false,
  _selectedUids: new Set(),
};
