# The original stack

This is the code the current one was converted from. Nothing here runs any
more, and nothing in `node-backend/`, `bot-service/` or `next-frontend/`
imports from it — it is kept so any behaviour can be compared line for line
against the original it came from.

| Here | Replaced by |
|---|---|
| `backend/` (FastAPI) | `../node-backend/` — Express + TypeScript |
| `bot/` (retrieval) | `../bot-service/` — still Python, copied across unchanged |
| `frontend/` (Vite) | `../next-frontend/` — Next.js App Router |

Every converted module names its source in its header comment, so the mapping
is findable from either direction.

`bot/` is the interesting one: the files in `../bot-service/bot/` are
byte-identical copies of the files here. The package paths were preserved
precisely so nothing had to be rewritten.

## Safe to delete?

Yes, once you are satisfied the new stack behaves the same. Nothing depends on
it. Check `git log` first if you want the history to survive the deletion.
