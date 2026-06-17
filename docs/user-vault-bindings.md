# User vault bindings

This fork adds Docker/env-driven document folder bindings for Odysseus.

The feature is additive. Normal document uploads and one-off attachments continue to work. Bound vaults are an additional source of indexed documents.

## Starting read-only

```yaml
services:
  odysseus:
    volumes:
      - ./data:/app/data:z
      - /path/to/user1/vault:/app/data/bound_vaults/user1:ro
      - /path/to/user2/vault:/app/data/bound_vaults/user2:ro
      - /path/to/shared/vault:/app/data/bound_vaults/shared:ro
    environment:
      ODYSSEUS_DOC_BINDINGS_ROOT: /app/data/bound_vaults
      ODYSSEUS_DOC_BINDINGS_REINDEX_ON_STARTUP: "true"
      ODYSSEUS_DOC_BINDINGS_JSON: >
        [
          {
            "id": "user1-vault",
            "name": "User 1 Vault",
            "path": "/app/data/bound_vaults/user1",
            "readers": ["user1"],
            "writers": [],
            "mode": "ro"
          },
          {
            "id": "user2-vault",
            "name": "User 2 Vault",
            "path": "/app/data/bound_vaults/user2",
            "readers": ["user2"],
            "writers": [],
            "mode": "ro"
          },
          {
            "id": "shared-vault",
            "name": "Shared Vault",
            "path": "/app/data/bound_vaults/shared",
            "readers": ["user1", "user2"],
            "writers": [],
            "mode": "ro"
          }
        ]
```

## Mounting private folders

You can mount additional private folders as separate bindings. Keep sensitive folders separate from shared vaults.

```yaml
services:
  odysseus:
    volumes:
      - /path/to/private/user1/tax-docs:/app/data/bound_vaults/user1-tax-docs:ro
      - /path/to/private/shared/address-book:/app/data/bound_vaults/shared-address-book:ro
    environment:
      ODYSSEUS_DOC_BINDINGS_JSON: >
        [
          {
            "id": "user1-tax-docs",
            "name": "User 1 Tax Docs",
            "path": "/app/data/bound_vaults/user1-tax-docs",
            "readers": ["user1"],
            "writers": [],
            "mode": "ro"
          },
          {
            "id": "shared-address-book",
            "name": "Shared Address Book",
            "path": "/app/data/bound_vaults/shared-address-book",
            "readers": ["user1", "user2"],
            "writers": [],
            "mode": "ro"
          }
        ]
```

## Later read/write mode

Read/write requires both layers to allow writing:

1. Docker volume must be mounted `:rw`.
2. The binding must use `"mode": "rw"`.
3. The user must be listed in `"writers"`.

Example:

```json
{
  "id": "shared-vault",
  "name": "Shared Vault",
  "path": "/app/data/bound_vaults/shared",
  "readers": ["user1", "user2"],
  "writers": ["user1", "user2"],
  "mode": "rw"
}
```

The initial implementation indexes/searches bound folders. Write routes should be added later with explicit `can_write` checks.

## Privacy note

Bound folders are indexed into Odysseus' vector store. Treat the Odysseus data directory and vector database as sensitive if you bind private documents such as tax, legal, address book, or finance files.
