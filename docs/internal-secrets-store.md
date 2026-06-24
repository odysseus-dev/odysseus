# Internal secrets store

Odysseus exposes a small internal contract for application-managed credentials:

```python
from src.secrets_store import get_secret_store

store = get_secret_store()
store.set("email", "smtp-password", "secret")
value = store.get("email", "smtp-password")
store.delete("email", "smtp-password")
```

Namespaces and keys may contain letters, digits, dots, underscores, and hyphens.
Callers should use stable domain-specific namespaces and must not place secret
values in logs, URLs, exception text, or API responses.

## Local backend

The default backend is `LocalEncryptedSecretStore`. It stores values in
`data/secrets.json`, encrypted with the existing Fernet application key at
`data/.app_key`. Writes are atomic and the file is restricted to mode `0600`
where supported.

No configuration is required. Existing installations continue using local
storage unless an administrator explicitly selects another backend.

## OpenBao backend

OpenBao uses KV v2 and bootstraps from an existing OpenBao API Integration.
Create that Integration first in Settings > Integrations, then set:

```dotenv
ODYSSEUS_SECRET_STORE_BACKEND=openbao
ODYSSEUS_SECRET_STORE_INTEGRATION_ID=<integration-id>
ODYSSEUS_SECRET_STORE_MOUNT=secret
ODYSSEUS_SECRET_STORE_PREFIX=odysseus/internal
```

Administrators can configure the same values under Settings > System > Secret
Vault. The UI stores only the backend choice, Integration ID, mount, and prefix
in `data/secret-store.json`; it never copies the OpenBao token. Environment
variables take precedence and lock the form while present.

The Integration token remains in Odysseus's encrypted local integration store.
This is intentional: storing the token in OpenBao would create a circular
dependency where OpenBao access is required to retrieve the credential needed
to access OpenBao.

Use a dedicated least-privilege token. A minimal policy should allow only the
configured prefix:

```hcl
path "secret/data/odysseus/internal/*" {
  capabilities = ["create", "read", "update", "delete"]
}
```

The store distinguishes a missing secret (`None`) from a backend failure
(`SecretStoreUnavailable`). Callers must not convert an outage into an empty
value and persist it over previously valid configuration.

## Migration

Backend selection does not migrate values automatically. Migrations must name
each secret explicitly:

```python
from src.secrets_store import SecretRef, migrate_secrets

migrate_secrets(
    local_store,
    openbao_store,
    [SecretRef("email", "smtp-password")],
    delete_source=False,
)
```

Keep the source value until the target has been verified. Set
`delete_source=True` only as a deliberate cleanup step after successful target
writes.

This contract does not automatically move existing model, email, MCP, or
integration credentials. Individual domains can adopt it in scoped follow-up
changes with domain-specific migration and rollback tests.
