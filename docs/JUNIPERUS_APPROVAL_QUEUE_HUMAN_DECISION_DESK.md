# JUNIPERUS030 â€” Approval Queue Human Decision Desk

This layer creates the visible human decision desk for Juniperus / Gnexus Operations Console.

It is the frontstage surface where proposed operations become reviewable decisions before
shell, file, runtime, or code-editing actions are allowed to execute.

## Boundary

- Runtime execution remains locked.
- Shell/file interception is not active in this package.
- File mutation remains approval-gated by doctrine.
- External reads/writes remain false.
- Connector calls remain false.
- Secrets are not stored.

## Route

After restart:

http://127.0.0.1:7010/gnexus/approval-desk

## API

- GET /api/gnexus/approval-desk/state
- POST /api/gnexus/approval-desk/propose
- POST /api/gnexus/approval-desk/decide

This package prepares the human approval surface required by JUNIPERUS040.
