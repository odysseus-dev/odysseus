# SSRF Policy

Odysseus is a local-first app. A single global private-address denylist would
break valid self-hosted deployments, including Ollama, vLLM, llama.cpp, local
embedding servers, private CalDAV/CardDAV servers, and ntfy instances on LAN or
Tailscale.

`src.ssrf_guard` therefore separates URL classification from endpoint policy.
The helper does not protect any route by itself; callers must choose the policy
that matches the endpoint class.

## Policies

- `strict_untrusted_fetch`: for attacker-influenced fetches such as web/search
  retrieval, gallery URL imports, previews, and scrape/import paths. This policy
  blocks metadata-service, loopback, LAN/private, link-local, Tailscale,
  unspecified, multicast, and reserved addresses.
- `trusted_user_configured_endpoint`: for authenticated/admin-configured service
  endpoints such as model servers, embedding servers, CalDAV/CardDAV, and ntfy.
  This policy allows loopback, LAN, and Tailscale addresses, but still blocks
  metadata-service addresses.

## Protected Endpoints

Current strict-policy enforcement:

- `src.search.content._get_public_url`: used by web/search content fetches and
  the `web_fetch` tool. Redirect targets are checked at each hop.

Current trusted configured-endpoint handling:

- `routes.model_routes`: model endpoint list/create/test/update returns
  `security_notice` metadata. Create/test/update reject metadata-service
  targets but continue to allow loopback, LAN, and Tailscale model servers.
- `routes.embedding_routes`: custom embedding endpoint get/set returns
  `security_notice` metadata. Set rejects metadata-service targets but
  continues to allow loopback, LAN, and Tailscale embedding servers.

- `routes.calendar_routes`: CalDAV config get/save/test returns
  `security_notice` metadata. Save/test reject metadata-service targets but
  continue to allow loopback, LAN, and Tailscale CalDAV servers.
- `routes.contacts_routes`: CardDAV config get/update returns
  `security_notice` metadata. Update rejects metadata-service targets but
  continues to allow loopback, LAN, and Tailscale CardDAV servers.

- `routes.auth_routes`: ntfy integration list/create/update/test returns
  `security_notice` metadata. Create/update/test reject metadata-service
  targets but continue to allow loopback, LAN, and Tailscale ntfy servers.
- `routes.note_routes`: ntfy reminder delivery checks the saved ntfy endpoint
  with trusted configured-endpoint policy before publishing, so legacy bad
  metadata-service configs are blocked at send time too.

## Address Classes

The classifier labels resolved targets as:

- `metadata`: cloud metadata-service addresses such as `169.254.169.254` and
  `fd00:ec2::254`, plus known metadata hostnames.
- `loopback`: localhost addresses such as `127.0.0.1` and `::1`.
- `private`: RFC1918/private IPv4 and private IPv6 addresses.
- `link_local`: link-local ranges such as `169.254.0.0/16`.
- `tailscale`: Tailscale CGNAT range `100.64.0.0/10`.
- `public`: public internet-routable addresses.

Follow-up PRs should wire this helper endpoint by endpoint, with tests that
prove untrusted fetch surfaces block metadata/local targets and trusted
configured service endpoints keep local/LAN/Tailscale flows working.
