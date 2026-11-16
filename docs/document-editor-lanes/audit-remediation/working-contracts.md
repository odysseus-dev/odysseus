# Remediation working contracts

Audited baseline: `032d98575f5a7492e2377089cbc68f148753dd64`.
Original accounting baseline: `087e6c8837969339e9e7823dd84a4d717d536cea`.

| Operation | Owner and input | Transition and recovery | Observable regression |
| --- | --- | --- | --- |
| Capture / queued save | Presenter owns mounted pane; persistence owns document draft and queue | Capture only when pane.activeDocumentId matches active document. Both admission and queue-turn captures use that boundary. Otherwise preserve cached body, title, language and revision. Response acknowledges saved revision only. | Rapid minimize/open B; held PUT A; repeated navigation; inspect actual payload and dirty state |
| Email envelope input | Composer wires pane inputs; presenter captures email envelope; localDraft owns local storage | Capture and mark initiating document dirty, persist local snapshot, schedule document autosave. Empty CC/BCC are edits. Delayed storage work cannot read another pane. | Input To/CC/BCC/Subject, clear, reload, outgoing PUT and shortcut |
| Delayed title and detect | Source editor owns timers; actions own metadata requests | Capture document ID and text when scheduled. Source UI work requires original mount/owner. Disposal cancels every timer including image work. Language completion must retain request identity/value. | Switch A to B before timer or PATCH resolves; no B metadata mutation; disposal/reinitialization |
| Delete PDF draft | PDF controls synchronously capture draft; flow owns deletion; persistence owns serialized saves | Capture before timer cancellation; DELETE waits for writes. Success tombstones and removes. Failure schedules document persistence from retained record, including detached drafts. Never await a save from inside DELETE task. | Mounted, minimized and navigated failure, held DELETE; actual recovery PUT; successful deletion produces no PUT |
