# Manual Document Editor QA checklist

Use a test account and noncritical documents. Record browser/device, date, and any console/network errors.

## Editing and persistence

- [ ] Create a document, type several edits, wait for autosave, refresh, and confirm the latest content and dirty/saved status.
- [ ] Trigger manual save while a save is already pending; confirm writes serialize and the latest revision persists.
- [ ] Force one save failure, retry, and confirm content and dirty state recover correctly.
- [ ] Close an inactive dirty document and verify its draft persists.
- [ ] Close or navigate away while a save is pending; reopen and confirm no data loss.

## Documents, tabs, and sessions

- [ ] Open multiple documents, edit each, and switch repeatedly. Confirm title, language, content, selection, and pending saves stay with the correct document.
- [ ] Rename a tab, open its context menu, reorder tabs, and close a tab. Confirm keyboard and pointer actions work.
- [ ] Switch sessions with dirty documents, then return. Confirm session/document identity and content remain isolated.
- [ ] Open a document from a deep link and use browser back/forward navigation.

## Panel and mobile lifecycle

- [ ] Open and close the panel repeatedly; confirm no duplicate handlers, stale focus, or console errors.
- [ ] Minimize and restore the panel several times, including after switching documents.
- [ ] On mobile width, open the bottom dock, navigate away and back, and confirm panel restoration and scrolling.
- [ ] Exercise Escape with a menu, modal, nested overlay, and panel; confirm the topmost layer closes first and focus returns sensibly.

## Editors and toolbar

- [ ] Exercise source editor syntax highlighting, line numbers, language detection, title detection, and autosave.
- [ ] Exercise rich-text formatting, dropdowns, repeat opening of the same dropdown, selection toolbar, keyboard shortcuts, and table actions.
- [ ] Open and remove a Markdown image; verify upload/render behavior and persistence.
- [ ] Use toolbar copy, export, save, preview, and overflow actions. Confirm menu ordering updates without losing callback behavior.

## PDF and email

- [ ] Edit a PDF-backed document, close it, switch sessions, and retry after a failed deletion. Confirm the latest draft survives.
- [ ] Compose an email, add/remove attachments, save a draft, send once, and verify duplicate sends are prevented.
- [ ] Trigger email AI reply, switch documents before completion, and confirm stale results do not overwrite the new document.

## AI, refresh, and restoration

- [ ] Start a live AI update, navigate to another document during streaming, and confirm only the original document receives the result.
- [ ] Refresh after edits and after session switching. Confirm content, selected document, language, and panel state restore as expected.
- [ ] Review browser console and network requests for failed imports, unhandled rejections, or duplicate requests.
