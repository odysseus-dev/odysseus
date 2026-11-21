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
its window; that window is the only place a note is edited. You edit the rendered note in place, with no separate edit
mode. One note is open at a time. Minimizing it leaves a `Note: <title>` chip in
the dock, and a link to a note from chat opens the same window. On a phone it
opens as a full-height sheet with no close button: swipe it down to close it.
That saves what you typed, like the close button does, and tapping the card
opens the note again.

Opening a note adds a step to the browser history, so Back (the browser's
button, a swipe from the edge on a phone, or Back in the home-screen app)
closes the note and leaves you in the app where you were, and Forward opens it
again. While a note is open the address ends in `#note-<id>`: a reload brings
back the same chat with the note on top, and the address opened in a new tab
opens that note, if you can read it. Opening a note that is already open
brings its window back to the front (or out of the dock).

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

A note holds up to 1,000,000 characters of text, a title of up to 1,000
characters and up to 1,000 characters of tags. Past that the save is refused
and the window says which part is too long, for example "The note's text is
too long to save: 1,000,012 characters, the limit is 1,000,000. Shorten it to
save. Your edits are kept on this device." A note that was already longer
before these limits can still be edited, as long as the edit doesn't make it
longer.

## Toolbar and keyboard

The toolbar has bold, italic, underline, strikethrough, highlight,
superscript, subscript, two heading levels, bullets, numbered lists,
checklists, links, a divider, image, sketch and Markdown source.
It is a single stop for Tab: move between its buttons with the arrow keys. On a
phone it is one row: swipe it sideways for the rest, and while you type the
colours, tags and note actions step aside until you are done.

| Key | Does |
| --- | --- |
| Cmd/Ctrl+B, Cmd/Ctrl+I, Cmd/Ctrl+U | Bold, italic, underline |
| Cmd/Ctrl+Shift+H | Highlight |
| Enter in a checklist item | New item (text after the cursor moves with it) |
| Enter on an empty item | Leaves the checklist |
| Backspace at the start of an item | Turns it back into a paragraph |
| Tab, Shift+Tab | Indent or outdent a checklist item or bullet |
| Space or Enter on a focused checkbox | Ticks it |
| Cmd/Ctrl+Z right after inserting an image | Removes the image |
| Cmd/Ctrl+K | Adds a link, or changes the one the cursor is in |
| Enter or Space on a focused image | Opens its size menu (arrow keys move through it) |
| Esc | Closes the open menu, then the note (also from the tag field) |

A click on a link, or a tap on a phone, opens it in a new tab; a drag across
it selects text instead. An image from elsewhere that shows as a link opens
only with Cmd/Ctrl+click, so a click never loads it. Links keep their target and title as written, and
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

## Markdown source

Every part of a note is edited in the rendered view; nothing is locked. Math,
diagrams, raw HTML such as `<details>`, and anything else that has no
formatted form show as a small block in the text: click it to edit its
Markdown in place, or Remove it. Editing one paragraph never reformats the rest
of the note, and the parts you did not touch are saved exactly as written.

The Markdown source button swaps the rendered note for its raw text, which you
can edit directly. Switch back to see the result.

If the editor ever can't write part of an edit, the rest of the note still
saves and the window says "Part of this edit is not saved: the editor cannot
write it. It is kept on this device." Its Markdown source button opens the
source with what you typed, so you can fix it there.

## Images and sketches

- **Insert:** the image button picks a file. It goes in right after the
  paragraph your cursor is in, and the note saves.
- **Paste or drop:** pasting a screenshot, or dropping image files onto the
  note, does the same. Several files dropped at once go in together.
- **Size:** tap an image, or focus it and press Enter, for S, M or L (a third,
  two thirds, or the full width), or Remove.
- **Sketch:** the Sketch button opens a drawing canvas in place of the text.
  "Insert sketch" puts the drawing into the note as an image.

The card shows the note's first image at the top, then the start of the note:
the first 40 lines or 2,000 characters, or the first 200 items of a checklist,
followed by "…". Open the note to see the rest. The upload limit is three
images every ten seconds; past that you see "Image upload failed", so wait a
moment and try again.

Images from other websites are not loaded in notes, on note cards, or on the
note cards a chat shows. An image that points somewhere else shows as a link
instead; put the cursor in it and use the link button to change its address
or description. Upload or paste the picture to see it in the note. This is a
Notes rule only: chat messages do load images from other sites.

To see them anyway, turn on **Show external images in notes** under Settings →
Appearance. An `https` image from another site then shows as an image in your
notes, on their cards and in the notes shared with you; an `http` image still
shows as a link. Loading it tells that site your IP address and when you
opened the note, but not which page you were on. The setting is yours only:
someone you share a note with sees those images only if they turned it on
too. It never changes what is saved.

An image stored in the note itself (a PNG, JPEG, GIF, WebP or SVG `data:`
URL) shows like an upload. An upload of this server written with another
address in front, as notes written before a move to a new address are, shows
from this server, and the next edit saves it with its path only; the old
address is never contacted. An image whose file is not on the server shows a
frame with its description instead; tap it to change its address or remove it.

On a shared note, everyone sees the images the owner and the current
collaborators added. If a collaborator leaves or is removed, the images they
uploaded stop showing in that note; the owner can insert their own copy.

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

If the same note was changed in another tab, on another device or by someone
you share it with since you opened it, saving stops and the window says "This
note changed elsewhere." Your text is kept. Choose **Reload** to take the other
version, or **Keep mine** to save yours over it.

Ticking or editing a checklist item that someone else changed or moved in the
meantime never lands on the wrong item. A tick follows the item's text, and when
that item is gone you see "This checklist changed elsewhere. It has been
refreshed." with the current list, so you can try again.

If the note was deleted, or is no longer shared with you, the window says so
and stops saving. Your text stays on screen so you can copy it.

## Sharing

On an instance with user accounts, you can share a note with other accounts.
Open the note and click the person icon in the header. Type the person's
username exactly as it is on their account (upper or lower case doesn't matter)
and press **Share** or Enter. They get **View**; pick **Edit** next to their
name to let them change the note. If no account has that name, the popover says
"No account with that name." and nothing is shared. Each change saves
immediately, and switching someone off removes them. The number next to the
icon is how many people have the note.

Only an admin sees a list of every other account, with a switch for each, in
place of the username field. Anyone else can't browse the accounts on the
instance; they need to know the name.

- **View:** they can read the note and see its images. Nothing they do changes
  it.
- **Edit:** they can change the text, the title and the checklist, including
  ticking items and adding images.
- **Owner only:** pinning, archiving, deleting, colour, tags, reminders and
  sharing stay with you. Collaborators never see who else the note is shared
  with.

Notes shared with you appear after your own under **Shared with me**, with the
owner's name on the card. To stop seeing one, open it and click **Leave note**
at the bottom of the window. Changes from other people show up within about 20 seconds while
Notes is open, except while you are typing in a note, have the share or Ask
popover open, or are selecting notes; they appear on the next refresh after
that. An open note's tags and reminder follow those changes too.

Sharing is only ever changed from the note window. Asking the agent to share a
note does nothing; it can tell you who a note is shared with, but not change
it. An admin can turn sharing off for an account with the "Share notes"
privilege under Settings → Users.

## Asking the agent

On your own notes:

- **Per item:** hover a checklist item in the note window, or Tab to it, and
  click its agent button. On a phone, open the note: the button is shown,
  dimmed, next to each open item (cards don't show it). The button opens a
  popover about that item: "Agent: help with this", the presets below, or a
  request you type. Nothing starts until you pick one.
- **Ask about this note:** the agent button in the window footer offers
  Rewrite more clearly, Summarize, Fix spelling and grammar, and Search the web
  about this, or a question you type. The note is saved first, so the agent
  gets it as it is on screen.
- **Whole note:** "Agent: solve this", at the top of the same popover ("Re-run
  agent" once the note has a run). It hands the agent the whole note.

The agent works in the background and you get a toast with **Open chat**. The
card then shows an **Agent** tag, and the window an **Agent chat** button, both
opening the latest run.

A run started from a note can edit that note, and only that one: its text,
title, checklist, colour, tags, pin and reminder. It can read your other notes
but not change, add, archive or delete them. This holds for the whole chat,
follow-ups you type in it included. If you saved the note while the run was
working, the run has to read it again before it can write, so it never writes
over your changes. On a note you share with someone, every edit, and anything
else the run wants to do, waits for your approval in the chat first. On a note
only you can see, the run edits without asking until it reads something from
outside the note, such as a web page, an email, another note or another
tool's result. From then on each edit to the note waits for your approval
too, since what it read could be steering it. Reading the note back never
asks.

When the agent edits the note, the card and the open window update and a toast
says "Agent edited this note" with **Undo** and **See what changed**. The
window's info line keeps an **Undo** next to "Agent edited" while that edit is
the note's latest change; once you or anyone else changes the note, use the
history instead.

The chat shows the notes it has in context as small cards: the note a chat was
started from at the top of the conversation, the notes you picked with
**Select** → **Send to Agent** above the message box (the x takes one out), and
on each message the notes it carried. Clicking a card opens the note.

## History

Each of your notes keeps its earlier versions. Open them with **History (N)**
in the window's info line, or the History button in the footer. A version is
kept:

- before every agent edit, from the note's own chat or any other;
- before your own save, when the last version is more than ten minutes old or
  was not yours, so a typing session keeps one version, not one per keystroke;
- before a restore and before an agent Undo, so both can be taken back.

The list starts with **Now**, then each change, newest first and grouped by day:
who made it (you, the agent and its chat, or the person you share the note
with) and what it did. Pick one to see the change itself: the lines it added
highlighted in bold, the lines it removed struck through in red. **Undo this change** puts the note back
as it was before it; on an older change the button says **Undo this change and
everything after it**, and **Show what undoing does** previews that against the
note as it is now. A toast offers **Undo** for the restore itself.

A note keeps its last 50 versions, up to about 10 million characters in all,
and the oldest go first. Two kinds are never dropped: the newest, and, for you
and each person you share the note with, the text your last change left, so
someone else saving over and over can't push your version out. Only the owner
sees the history; the people a note is shared with don't, though their edits are
kept in it under their name. Deleting a note deletes its history.

## Deleting

Deleting a note deletes it for everyone it is shared with. When an admin
removes a user account, every note that account owned is deleted too, including
the ones it shared, so their collaborators lose them. If those notes can't be
removed, the account is kept and the admin panel says so; try again later. Images from deleted notes
stay on the server until an admin runs the upload cleanup, which removes unused
uploads older than 30 days.
