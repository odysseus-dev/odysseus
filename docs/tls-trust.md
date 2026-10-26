# Outbound HTTPS trust

Odysseus uses both Python package CA bundles and system trust. HTTPX uses certifi by default; the guarded web/PDF fetcher and webhook delivery explicitly use Python's system SSL context. A configured `LLM_CA_BUNDLE` adds certificates to system trust for model calls. Upgrading certifi alone cannot repair every outbound HTTPS path.

For the October 2026 eMudhra incident, use certifi 2026.10.23 or newer, pip 26.2.2 or newer, and uv 0.13.5 or newer wherever uv is used. Pip has its own vendored certifi and also loads system roots. Update the operating system's certificate package separately. See the [Python security announcement](https://discuss.python.org/t/109576) and [incident details](https://emudhra.we-have-a-problem.com/).

Before other pip installs, bootstrap the selected environment with the integrity-pinned wheels:

```sh
python -m pip install --require-hashes --only-binary=:all: --no-deps -r requirements-bootstrap.txt
python -m pip install -r requirements.txt
```

The pinned bootstrap protects package integrity even when the old installer's certificate bundle is affected. It is not an operating-system trust-store update. Native Windows/macOS trust and command-line OpenSSL PEM stores may differ; follow the platform vendor's remediation for every store in use. Restart Odysseus and its subprocesses after updating trust.

On Linux/OpenSSL deployments, inspect both the effective CA file and hashed CA directory, plus the two Python bundles:

```sh
python scripts/check_ca_trust.py --system --python --overrides --verbose
```

Explicit PEM bundles can also be passed as positional arguments. The check rejects the four published root fingerprints, all sixteen published intermediate certificates (including their cross-signs), the cross-signed G3 issuer in the published test chains, and malformed/empty bundles. Checking intermediates matters because adding one as a custom trust anchor can restore an affected chain even after its roots have been removed.

`--overrides` checks every nonempty `LLM_CA_BUNDLE`, `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`, `PIP_CERT`, `NODE_EXTRA_CA_CERTS` and `GRPC_DEFAULT_SSL_ROOTS_FILE_PATH` setting in the checker's process environment, including configured fallback settings. Requests/pip hashed-directory overrides are inspected too; `--system` covers `SSL_CERT_DIR`. Run with the application's environment and filesystem access. The script does not load `.env` or application configuration files; pass any CA paths configured only there explicitly, for example `python scripts/check_ca_trust.py --system --python /path/to/private-ca.pem`. Missing or invalid configured files fail the check. Selecting only `--overrides` with no configured paths also fails instead of reporting that stores were validated.

`--verbose` reports the Python executable/prefix, user home, effective UID/GID where available, and resolved CA paths, including bundles selected from user-installed packages. Compare these with the running application's identity and interpreter. It does not dump environment variables or credentials.

This is an offline inventory check of known certificate fingerprints. It does not modify stores, reject requests at runtime, model native OS revocation, or detect arbitrary reissued certificates sharing the compromised keys. Chromium's policy separately matches public keys. Check mounted or replaced CA files again after deployment, remove affected trust through the owning platform/client configuration, and restart clients that cache SSL contexts. A passing build cannot validate custom files introduced later.

Both Docker build stages use Python 3.14.9, refresh system CA certificates and bootstrap pip/certifi before dependency installation. The build fails if known affected CA certificates remain. Node uses the corrected system store through `--use-openssl-ca`. Python gRPC uses that same PEM file through `GRPC_DEFAULT_SSL_ROOTS_FILE_PATH`; updating certifi alone does not replace gRPC's independent wheel bundle. After dependency installation, the image checks gRPC's selected file as well. An explicit gRPC override must be readable and free of known affected CA certificates. [grpcio 1.84.1](https://github.com/grpc/grpc/releases/tag/v1.84.1) includes the upstream root-bundle correction and is the application minimum. Retain the checked override as a consistent image trust selection; older persisted Python environments and explicit client roots still need independent checks.

The image also bundles Docker CLI [29.9.2](https://github.com/moby/moby/releases/tag/docker-v29.9.2), built with Go 1.26.10's distrust of the compromised intermediate keys. Cookbook uses this client for optional Docker operations. Default Compose does not mount the host Docker socket; local daemon access requires the explicit [host-Docker overlay](../docker/host-docker.yml), whose usual Unix-socket connection does not use TLS. The client update is maintenance of a shipped tool, not evidence that these local calls were exposed. It does not update the host daemon or other containers' clients and trust stores; assess those separately.

Chromium uses a separate trust store. The Docker image installs a managed `CADistrustedCertificates` policy for the eight compromised intermediate public keys, covering their cross-signs without depending on background revocation updates. It adds no trusted certificate and does not disable certificate verification. The policy and public-certificate provenance are documented in [the browser policy note](../docker/chromium-ca-policy.md). Native browser installations and independently managed profiles require their own platform policy or vendor update; this image policy does not configure host browsers.

To replace only Odysseus in an already running Compose stack, build from the corrected checkout with a distinct local image tag. Use the same Compose files and project name as the existing deployment for every command below:

```sh
export ODYSSEUS_IMAGE="odysseus-local:ca-$(git rev-parse --short=12 HEAD)"
docker compose build --pull --no-cache odysseus
docker compose up -d --no-deps --pull never --no-build odysseus
docker image inspect --format '{{.Id}}' "$ODYSSEUS_IMAGE"
docker inspect --format '{{.Image}}' "$(docker compose ps -q odysseus)"
```

The two image IDs must match. Keep the selected `ODYSSEUS_IMAGE` in the deployment configuration for subsequent recreations. Compose can prefer the registry image when both `image` and `build` are present; the explicit pull/build flags keep this replacement on the local build. `--no-deps` leaves the existing sidecars running and is intended for an existing stack. For registry deployments, select a verified corrected immutable tag or digest. A merge to `dev` publishes `:dev`; it does not update `:latest`, which is published from `main`.

Check the standard image as its configured app account, using the image's Python interpreter and the same user-bin PATH prefix as the entrypoint:

```sh
docker compose exec -T --user 0 odysseus sh -ec '
  gosu_bin=$(command -v gosu)
  app_user=$(getent passwd "${PUID:-1000}" | cut -d: -f1)
  test -n "$app_user"
  exec "$gosu_bin" "$app_user" env PATH="/app/.local/bin:$PATH" \
    /usr/local/bin/python /app/scripts/check_ca_trust.py \
    --system --python --grpc --overrides --verbose
'
```

`gosu` selects the account's HOME and drops privileges before Python runs, so the check includes its persisted user packages. The entrypoint itself is not rerun. Plain `docker compose exec ... python` uses the image's default root user and can miss `/app/.local` packages. A custom service user, entrypoint, virtual environment or launch command requires matching that deployment's actual user, HOME and Python interpreter instead of copying the standard command unchanged. Docker exec inherits container-creation environment, not variables exported later by the app or entrypoint: reproduce any additional Python/TLS settings with exec's `--env` options, and pass CA paths configured only in application files explicitly. Confirm the verbose identity and selected paths before accepting the result.

For containers started non-root or without the SETUID/SETGID capabilities needed by `gosu`, use `docker compose exec --user UID:GID --env HOME=/actual/app/home` with the deployment's actual numeric IDs, home, PATH and interpreter instead. Do not add capabilities merely to run this check.

SearXNG is a separate HTTPS client and image; updating the Odysseus image does not patch it. Inspect its effective CA path and update or rebuild that image independently. Also inspect persisted `/app/.local` packages, remote Cookbook environments, and externally configured MCP runtimes. Image replacement does not necessarily replace those environments.

For behavioral validation, all fourteen [published test chains](https://emudhra.we-have-a-problem.com/chains.zip), including the AAA cross-signs and two chains without Certificate Transparency entries, must fail certificate verification through the effective stores. Confirm that an ordinary known-good HTTPS request still succeeds. In Chromium, confirm the managed policy is loaded at `chrome://policy` and test the incident endpoints through the actual browser path with certificate verification enabled. The PEM checker cannot inspect Chromium's policy or its active verifier. Package versions and a successful image build alone do not prove that a running deployment selects the corrected store.

## Follow-up releases

The maintained bootstrap selects certifi 2026.10.23 with its verified wheel hash; pip remains 26.2.2. The certifi update removes the C1/C3 roots in addition to the earlier incident changes. CI uses uv 0.13.5, which adds verifier-level rejection of the compromised intermediate keys and updated managed CPython builds. Existing virtual environments do not update automatically. See the [certifi change](https://github.com/certifi/python-certifi/commit/b60466c9f2882d9cf088943c650b0faac06c080c), [uv release](https://github.com/astral-sh/uv/releases/tag/0.13.5), and [Python security releases](https://blog.python.org/2026/10/python-3151-3149-31317/). The pinned bootstrap still replaces the selected versions exactly; preserving newer installed versions and private-mirror support remain separate maintenance.

SearXNG remains an independent trust owner. Use the [upstream-image validation and rollout instructions](searxng-trust-image.md); updating these application packages does not repair an existing sidecar. Host daemons, native browser policy, optional compiled clients and independently managed integrations require their own effective-client checks.

CPython's [intermediate-key verifier block](https://github.com/python/cpython/pull/160042) and its [3.14 backport](https://github.com/python/cpython/pull/160569) merged on October 24. They are distinct from Python 3.14.9's bundled-pip update. No released 3.14 interpreter containing that verifier change was identified when this image pin was selected; retain the corrected effective trust stores and track the subsequent security release. hf-xet's [trust fix](https://github.com/huggingface/xet-core/pull/1038) is also merged but not included in the latest published 1.7.0 release at that check. Agent-browser 0.39.2 and primp 2.0.1 are not established incident fixes for their independent native trust paths. A version bump alone does not establish that the active client uses corrected trust.
