# Upstream SearXNG trust validation

SearXNG owns independent system and Python certificate stores. Updating Odysseus's Python environment does not update this sidecar. The base and both standalone GPU Compose files now select the official `docker.io/searxng/searxng:2026.10.24-ef0ec03da` image at immutable multiarch digest `sha256:7c7cbe509429e82755104b2a95f52085a98633c95d53f44e4e905fa3a59f08c8`. Its system and certifi stores remove the affected eMudhra trust. The [upstream certifi update](https://github.com/searxng/searxng/pull/6881) is included in this release.

Deploy the upstream image directly. The validation Dockerfile adds public fixtures and diagnostics only; no repair layer, custom runtime image or project registry publisher is required. `scripts/check_ca_trust.py` owns the known-certificate inventory for both the application and SearXNG checks.

## Validate a pin update

Run from the repository root on a disposable Docker host. The helper uses a fresh configuration volume, omits published ports and does not load the operator's `.env`. It retains the base Compose settings wrapper, healthcheck and capabilities. Outbound network access and working search engines are required for the live checks.

```sh
image=$(docker compose --env-file /dev/null -f docker-compose.yml config --format json |
  python3 -c 'import json, sys; print(json.load(sys.stdin)["services"]["searxng"]["image"])')

# Offline build-step checks, including actual entrypoint and loopback health.
# Pulling the selected base still requires registry access.
docker build --network=none --target validation \
  --build-arg "SEARXNG_IMAGE=$image" -f docker/searxng/Dockerfile .

# Exercise the exact unmodified deployment image after an anonymous pull.
docker pull "$image"
bash .github/scripts/validate-searxng-image.sh "$image"
```

The offline checks require all fourteen incident chains to fail certificate verification through the system PEM bundle, hashed CA directory and certifi. The affected positive-control roots must accept those same chains. Each fixture's DER fingerprint is verified first, and a fixed verification timestamp prevents expiry from hiding a broken mitigation. Checks run again after the real upstream root entrypoint regenerates trust.

The live checks use SearXNG's actual curl client factory. Ordinary verified HTTPS must succeed, all fourteen incident hosts must fail with curl certificate error 60, and a JSON search must return results. DNS failures, timeouts and generic handshake failures are inconclusive and fail validation. The checks inspect the already-running service without changing its trust stores.

The `ci / SearXNG trust` workflow runs these offline and Compose/live checks on native amd64 and arm64 runners. It selects the same immutable image as base Compose, pulls without registry credentials, and has read-only repository permissions. It neither publishes a replacement image nor requires package-write credentials. Update all three deployment pins together; the alignment regression guards the standalone GPU files.

## Rollout and scope

Changing a Compose pin does not replace running containers. After updating the checkout, run `docker compose pull searxng` and `docker compose up -d --no-deps searxng`, then inspect health, retained settings and actual Odysseus search integration. Use the appropriate standalone GPU Compose file when that is the deployment being managed.

Operator-mounted trust, custom engines, persisted packages and independently managed clients need separate effective-client checks. An amd64 result does not establish arm64 behavior. The fixture-based check detects known certificate fingerprints; it does not model every native revocation mechanism or arbitrary reissued certificates sharing a compromised key. See [outbound TLS trust](tls-trust.md) for the application, bootstrap, Chromium and other runtime boundaries.

## Public fixture provenance

The public CA inventory comes from the incident's [roots](https://emudhra.we-have-a-problem.com/roots.json) and [intermediates](https://emudhra.we-have-a-problem.com/impacted-intermediates.json). The additional cross-signed issuer and fourteen chain fixtures come from [chains.zip](https://emudhra.we-have-a-problem.com/chains.zip), SHA-256 `7b949956955fc501a30f4d98d2f513d34c0bbbe92e29a968194c96e097ce2345`. Fixtures contain public certificates only. See [the fixture notes](../tests/fixtures/ca-trust/README.md).
