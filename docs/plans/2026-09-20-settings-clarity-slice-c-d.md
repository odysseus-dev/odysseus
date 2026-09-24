# Settings clarity slices C + D — this work tree

Agents: C wires overlay chat defaults. D stops storing cloud keys on
Odysseus. Stay on `feat/settings-clarity-slice-b`. Do not merge to main
from this note.

## C — Default Chat Model is a 9router route

- GET `/api/default-chat` returns `automatic` / `fast` / `balanced` /
  `best` with empty endpoint fields.
- Leftover ModelEndpoint names become `automatic`.
- AI Defaults shows `#set-defaultRouteSelect`. Utility/Vision stay local
  leftover.
- Composer pending default reads `/api/default-chat`.

## D — no cloud API key on Odysseus

- `POST /api/model-endpoints` rejects public cloud hosts.
- Local leftover (loopback, RFC1918, CGNAT, docker short names) still
  posts.
- `/setup` cloud keys POST `/api/ninerouter/connections`.
- Startup `purge_leftover_cloud_model_endpoints` deletes existing public
  cloud ModelEndpoint rows and rebinds those sessions to overlay 9router.
