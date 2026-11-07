# architecture

one process, one database file, server-rendered html, htmx for the small number
of things that should not be a page load. no build step, no js framework, no
background workers beyond one thread per ai job.

```
browser ──html/form──▶ fastapi ──sqlite──▶ data/focus.db
   ▲                     │
   └──── html fragment ◀─┘        htmx swaps a partial into a target

cron / agent ──json──▶ /api/hermes ──▶ same database
```

## modules

| file | responsibility |
| --- | --- |
| `main.py` | ui routes, template rendering, the ai action endpoints |
| `api.py` | `/api/hermes` rest api for agents, guarded by a shared token |
| `auth.py` | scrypt hashing, soulseek login, signed session cookie |
| `db.py` | schema, connection per request, `CREATE TABLE IF NOT EXISTS` migrations |
| `ai.py` | turning a task into a hermes prompt, parsing the answer, running jobs |
| `cli.py` | the `focus` command; a thin wrapper over `/api/hermes` |
| `config.py` | xdg paths, secret and token generation |

the split is deliberate: `main.py` never knows how a model is called, `ai.py`
never touches http, and `api.py` never renders html.

## data model

```
users     id, username(unique, nocase), password, display_name, avatar, theme
projects  id, user_id, name, position            unique(user_id, name)
tasks     id, user_id, project_id, title, notes, status, position, timestamps
subtasks  id, task_id, title, done, position
jobs      id, user_id, task_id, kind, status, result, error, created_at
```

- everything hangs off `user_id`, and every query filters by it. a row belonging
  to someone else is a 404, never a 403 — the app does not confirm existence.
- `position` gives a stable manual order; ordering is `position, id`.
- `jobs` is the ai queue: one row per action, updated by a worker thread and
  polled by the sheet. it exists so a slow model never blocks a request.
- foreign keys are on, and `users → projects → tasks → subtasks` cascade, so
  deleting a user cleans up. (project deletion is guarded instead: the app
  refuses to delete a project that still has tasks.)

## http surface

### ui (session cookie)

| method | path | does |
| --- | --- | --- |
| `GET` | `/login`, `POST /login` | the only form; unknown names register |
| `POST` | `/logout` | clear the cookie |
| `GET` | `/`, `/p/{project_id}` | task list for a project |
| `GET` | `/search?q=` | list filtered by title/notes |
| `POST` | `/projects` | create a project |
| `POST` | `/projects/{id}/delete` | delete an empty project (`hx-redirect` on success) |
| `POST` | `/p/{id}/tasks` | add a task |
| `GET` | `/tasks/{id}` | the task sheet (fragment) |
| `POST` | `/tasks/{id}` | save title/notes/project |
| `POST` | `/tasks/{id}/toggle` | complete / reopen (returns the list + celebrate event) |
| `POST` | `/tasks/{id}/delete` | delete |
| `POST` | `/tasks/{id}/subtasks` | bulk-add steps from the ai preview |
| `POST` | `/subtasks/{id}/toggle` | tick a step |
| `GET`/`POST` | `/settings`, `/settings/theme`, `/settings/avatar` | the three settings |
| `GET` | `/avatars/{name}` | avatar bytes, session required |
| `POST` | `/tasks/{id}/ai/{split,research}` | start a job |
| `GET` | `/tasks/{id}/ai?job=` | poll a job |

### api (token)

| method | path | body |
| --- | --- | --- |
| `GET` | `/api/hermes/projects?user=` | — |
| `GET` | `/api/hermes/tasks?user=&project=&status=` | — |
| `POST` | `/api/hermes/tasks` | `{user, title, project?, notes?}` |
| `PATCH` | `/api/hermes/tasks/{id}` | `{user, title?, notes?, project?, status?}` |

`X-Focus-Token` comes from `~/.config/focus/hermes_token`, written on first
start with mode 600. a missing or wrong token is a 401; a missing project is
created on first use, so an agent can invent a project name.

## the htmx contract

each swapped region has exactly one owner:

| target | swap | who |
| --- | --- | --- |
| `#list` | `innerHTML` | search, add, toggle, save, delete, sub-add |
| `#sheet` | **`outerHTML`** | clicking a task row |
| `#subtasks` | `innerHTML` | adding steps |
| `closest .subtask` | `outerHTML` | ticking a step |
| `#ai-panel` | `innerHTML` / `outerHTML` | ai buttons and the poller |

two rules that cost real debugging time and are worth keeping:

1. **the sheet is swapped with `outerHTML` and the response is rooted at
   `<aside id="sheet">`.** with `innerHTML`, anything that replaces the element
   rather than its children leaves a bare `<div>` where the fixed panel was, and
   the whole layout collapses into the document flow. returning the container
   means every response re-establishes it.
2. **a nested `hx-*` triggers both handlers.** a row with `hx-get` containing a
   button with `hx-post` sends two requests per click, and the swaps race — the
   button looks dead and only works on a second click. the child stops
   propagation.

effects live in `app.js`: it listens for `focus:celebrate`, `focus:notice` and
`focus:close-sheet`, which the server raises through `HX-Trigger`. coordinates
for the confetti are captured at click time, because the row is detached by the
time the event fires.

## ai jobs

```
POST /tasks/{id}/ai/split ──▶ insert job(running) ──▶ return panel
                                        │
                             thread: ai.ask(prompt)
                                        │
                          update job(done|error), write notes
                                        ▲
panel: hx-get .../ai?job=N, hx-trigger="load delay:2s"  ──┘
```

`ai.py` keeps one global lock, so two users pressing the button queue up
instead of starting two agents on a two-core box. a job older than 330 s is
reported stale rather than polled forever. details: [ai.md](ai.md).

## trade-offs

- **no csrf tokens.** the session cookie is `samesite=lax`, which covers
  cross-site form posts; anything beyond that would need a token per form.
- **sqlite, single writer.** fine for a handful of people on one box; write
  contention would show up as `database is locked` under real load.
- **schema changes are `ALTER` statements in `db.py`**, applied at startup.
  enough for this size, not a migration framework.
- **no pagination.** the list is ordered by project and position; a user with
  thousands of open tasks would want it.
- **`auto` theme reads the os**, but the per-user choice is authoritative once
  set; the cycle button walks auto → light → dark.

## adding something

1. table or column: `db.py` schema + an `ALTER` step, then note it in
   [data model](#data-model).
2. route in `main.py` returning a partial; decide which target it swaps.
3. if a new region: give it an id, one owner, and one swap mode.
4. a test in `tests/` for the scoping (someone else's row → 404).
5. if it changes css or js, bump the `?v=` string in `base.html` — otherwise the
   browser keeps the old file and the deploy looks like it did nothing.
