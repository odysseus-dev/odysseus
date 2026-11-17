# Featherless Provider Shape

Last updated: dev@86b886c | 2026-10-15

## Scope

Runtime provider ID `featherless`; OpenAI-compatible calls in `src/llm_core.py`, catalog and picker ownership in `routes/model_routes.py`, and setup/selection UI in `static/js/admin.js` and `static/js/providers.js`. No dedicated canonical capability reader exists.

## Catalog And Picker

Normal endpoint probing returns no bulk Featherless inventory. New rows use API endpoint kind and an explicit empty pin list; only selected model IDs become picker inventory. Admin `GET /api/model-endpoints/{ep_id}/catalog-search` queries `/v1/models` with `q`, `available_on_current_plan=true`, `status=active`, `conversational=true`, and bounded `page`/`per_page` (maximum 100). Its in-memory cache lasts 45 seconds and keys endpoint ID, normalized query and pagination. Upstream authorization, rate-limit and timeout failures become route errors rather than fabricated model cards.

Reachability tries `/v1/plan` and then a one-item plan-filtered model query. Endpoint status and selected model inventory are distinct facts; a reachable account need not have any pinned models.

## Request And Capability Boundaries

Calls use the OpenAI-compatible runtime adapter. Model availability on the current plan does not prove native tools, reasoning, modality, or context. Per-model endpoint tool modes are operator configuration; canonical generic normalization remains identity-only.

## Selection UI Limits

The admin picker debounces queries after two characters, pages bounded catalog results and saves pins/tool modes optimistically. Initial search cancellation does not establish equivalent load-more generation guards, and ignored PATCH response status does not prove accepted persistence. Lowercased query cache identity, explicit empty pins and endpoint reachability remain separate from provider plan/capability acceptance.

## Current Gaps

- No native canonical reader maps plan/catalog evidence into capability records.
- Catalog search is a live provider dependency; cache and pins do not guarantee continued account availability.
