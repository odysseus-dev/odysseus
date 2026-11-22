---
layout: default
---

# Notes

Notes is where Odysseus keeps notes, to-do lists and reminders. Open it from
the Notes button in the left rail. Every note is a single Markdown document:
checklist items are `- [ ]` lines inside it and images are Markdown images, so
what you see in the app, what the agent reads and what the API returns are the
same text.

## The note window

Click a card, or focus its title and press Enter or Space, to open the note in
its window; that window is the only place a note is edited. You edit the
rendered note in place, with no separate edit mode. One note is open at a time. Minimizing it leaves a `Note: <title>` chip in
the dock, and a link to a note from chat opens the same window. On a phone it
opens as a full-height sheet with no close button: swipe it down to close it.
That saves what you typed, like the close button does, and tapping the card
opens the note again.

The quick-add bar at the top of the list ("Add a note…") is the one way to
start a note. What you type there becomes its title, and Enter moves on to the
body. Its photo button starts a new note on the image picker. A to-do list is
a note with a checklist in it, so there is no separate to-do mode: the
toolbar's Checklist button starts one. A new note is only created once you
type something in it.

Saving is automatic, about half a second after you stop typing. Opening and
closing a note without changing it writes nothing. If the server can't be
reached you get "Note not synced. Your edits are kept on this device.", and the
next time you open the note it offers your unsaved text back with Restore or
Discard. If the note was changed somewhere else in the meantime, the window
won't restore your draft over it: Copy draft puts it on the clipboard so you
can paste back what you want to keep.

## Toolbar and keyboard

The toolbar has bold, italic, strikethrough, two heading levels, bullets,
numbered lists, checklists, links, a divider, image, sketch and Markdown source.
It is a single stop for Tab: move between its buttons with the arrow keys. On a
phone it is one row: swipe it sideways for the rest, and while you type the
colours, tags and note actions step aside until you are done.

| Key | Does |
| --- | --- |
| Cmd/Ctrl+B, Cmd/Ctrl+I | Bold, italic |
| Enter in a checklist item | New item (text after the cursor moves with it) |
| Enter on an empty item | Leaves the checklist |
| Backspace at the start of an item | Turns it back into a paragraph |
| Tab, Shift+Tab | Indent or outdent a checklist item or bullet |
| Space or Enter on a focused checkbox | Ticks it |
| Cmd/Ctrl+Z right after pasting several lines | Takes the paste back |
| Cmd/Ctrl+K | Adds a link, or changes the one the cursor is in |
| Esc | Closes the open menu, then the note (also from the tag field) |

A click on a link, or a tap on a phone, opens it in a new tab; a drag across
it selects text instead. Links keep their target and title as written, and
`mailto:` and `tel:` links work: put the cursor in a link (click just next to
it, or use the arrow keys) and press Cmd/Ctrl+K or the link button to change
either.
Pasting always pastes plain text, so formatting from a web page or another app
doesn't come along. Typed Markdown characters stay as typed: `2 * 3 * 4` is
saved as text, not as italics.

## Checklists

Ticking a box on a card or in the window saves straight away, without opening
anything. On a to-do card you can also add an item from the input at the bottom of the card. A
checklist can sit anywhere in a note, between paragraphs, and items can nest up
to three levels.

Lines inside a code block are code. A `- [ ]` example inside a fenced block
is never turned into a checkbox.

## Markdown source and locked parts

The Markdown source button swaps the rendered note for its raw text, which you
can edit directly. Switch back to see the result.

Some things can't be edited in the rendered view without changing them: math,
raw HTML such as `<details>`, aligned tables, or an image from another site.
Those parts get a dashed outline and the hint "This part can only be edited in
Markdown source". They are always saved exactly as written; open Markdown source
to change them. Editing one paragraph never reformats the rest of the note.

## Images and sketches

- **Insert:** the image button picks a file. It goes in after the paragraph
  your cursor is in.
- **Sketch:** the Sketch button opens a drawing canvas in place of the text.
  "Insert sketch" puts the drawing into the note as an image.

Images from other websites are not loaded in notes. An image that points
somewhere else shows as a link instead. Upload the picture to see it in the
note.

## Colours, tags and reminders

The window's footer holds the colour dots (the last one sets a background
picture), the tags, Archive and Delete. Type a tag and press Enter, comma or space,
with or without `#`; tags you already use elsewhere are suggested. The bell in the
header sets a reminder: later today, tomorrow, next week or a date and time you
pick, optionally repeating. How reminders reach you (browser, email, ntfy,
webhook) is set in Settings.

A reminder for a checklist lists the open items under `Pending (N):`. Reminders
are plain text: images are left out and links show as their text.

## "This note changed elsewhere"

If the same note was changed in another tab or on another device since you
opened it, saving stops and the window says "This note changed elsewhere."
Your text is kept. Choose **Reload** to take the other version, or **Keep
mine** to save yours over it.
