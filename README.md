# focus

**a quiet personal task list for one brain at a time.**

one sidebar of projects, one list of tasks, an inline add field, and a task
sheet that slides in when you click a row. no boards, no comments, no teams, no
counters, no notification badges. lowercase everywhere.

built for adhd brains: the chrome is almost absent, the keyboard does the work,
and finishing something gives you a small burst of dopamine — a gradient
shimmer for a step, confetti for a task. both respect `prefers-reduced-motion`.

- python 3.12+ · sqlite · [htmx](https://htmx.org) — one process, no build step, no js framework
- fastapi + jinja2, htmx vendored in `static/vendor/` (no cdn at runtime)
- rosé pine **moon** and **dawn**, `auto` follows `prefers-color-scheme`
- soulseek-style login: one form, any name + password, unknown names become accounts
- every row is scoped to its user; there is no sharing surface at all
- local ai through the hermes cli: split a task into steps, or research it
- a small authenticated rest api so agents and cron jobs can hand you tasks

```
┌ projects ─┐  ┌ tasks ────────────────────────────────┐  ┌ task ──────────┐
│ inbox     │  │ ○ marsz niepodległości w środę        │  │ title…         │
│ insy      │  │ ● renew the domain                    │  │ notes…         │
│ personal  │  │                                       │  │ steps…         │
│ + project │  │                                       │  │ split /        │
└───────────┘  └───────────────────────────────────────┘  │ research       │
                                                          └────────────────┘
```

## what it does not do

no labels, no assignees, no sprints, no comments, no activity log, no
notifications, no mobile app, no sync, no sharing, no analytics, no telemetry,
no cloud. it is a list, and it stays a list.

## run it

```sh
git clone https://github.com/bonsko/focus && cd focus
uv sync
uv run uvicorn focus.main:app --host 127.0.0.1 --port 8787
```

open <http://127.0.0.1:8787>, type any username and password — that is the
account. an `inbox` project is created for you.

data lives outside the repo:

```
~/.local/share/focus/data/focus.db     sqlite, wal mode
~/.local/share/focus/data/secret       session signing key (generated once, 0600)
~/.local/share/focus/data/avatars/     avatars, re-encoded to 96px webp
~/.config/focus/hermes_token           api token (generated on first start, 0600)
```

### configuration

| env | default | meaning |
| --- | --- | --- |
| `FOCUS_HOME` | `~/.local/share/focus` | database, secret, avatars (tests point this at a temp dir) |
| `FOCUS_TOKEN_PATH` | `~/.config/focus/hermes_token` | where the api token lives |
| `FOCUS_URL` | `http://127.0.0.1:8787` | base url the `focus` cli talks to |

## keyboard

| key | does |
| --- | --- |
| `n` | new task |
| `j` / `k` | move down / up the list |
| `x` | complete (or reopen) the selected task |
| `/` | search |
| `esc` | close the task sheet |

`wider` in the sheet header doubles the sheet's width for long briefings; the
preference is remembered per browser. notes grow with their content and scroll
past ~60vh instead of pushing the list around.

## themes

the palette is the official rosé pine set, both variants. `auto` follows the
system, `dawn` and `moon` are manual, and the choice is stored per user. the
small button at the bottom of the sidebar cycles auto → light → dark.

## ai, through hermes

there is no model client here and no api key. the app shells out to a local
`hermes` install (`hermes -z "<prompt>"`) and parses whatever comes back, so it
inherits the model and tools that agent already has — and nothing else needs
configuring.

- **split into small steps** — asks for 3–8 concrete steps, tolerantly parses
  the first balanced json array out of whatever prose the model wrapped it in,
  and shows them as editable rows (checkbox + text) before saving.
- **research this** — runs a real agent turn with web tools and appends a
  plain-text briefing to the task's notes, preamble stripped.

hermes is slow (20 s – 3 min), so both actions are background jobs: the request
returns at once, the panel polls every 2 s, and a failure ends as a visible
message with a retry button rather than a spinner forever. one ai call runs at a
time.

prompts, parsing and failure modes: [docs/ai.md](docs/ai.md).

## handing tasks to a person

agents and cron jobs use the rest api, guarded by `X-Focus-Token`:

```sh
T=$(cat ~/.config/focus/hermes_token)

# create — the project is created if it does not exist
curl -s -X POST http://127.0.0.1:8787/api/hermes/tasks \
  -H "X-Focus-Token: $T" -H 'Content-Type: application/json' \
  -d '{"user":"nat","project":"nixos","title":"bump the nixpkgs pin"}'

# list, then move or close
curl -s "http://127.0.0.1:8787/api/hermes/tasks?user=nat&status=open" -H "X-Focus-Token: $T"
curl -s -X PATCH http://127.0.0.1:8787/api/hermes/tasks/12 \
  -H "X-Focus-Token: $T" -H 'Content-Type: application/json' \
  -d '{"user":"nat","project":"inbox","status":"done"}'
```

or the bundled cli:

```sh
uv run focus add --user nat --project nixos --title "bump the nixpkgs pin"
uv run focus list --user nat --all
uv run focus move 12 --user nat --project inbox
uv run focus done 12 --user nat
uv run focus projects --user nat
```

a cron job that notices something and files it as a task instead of a chat
message:

```cron
# crontab -e; adjust paths and project name
0 8 * * * cd "$HOME/nixos" && nix flake update --dry-run 2>/dev/null | grep -q nixpkgs \
  && "$HOME/.local/bin/focus" add --user nat --project homelab \
       --title "nixpkgs moved upstream — bump the flake"
```

full endpoint list: [docs/architecture.md](docs/architecture.md#http-surface).

## deploy

a user unit is in [`deploy/focus.service`](deploy/focus.service), and the tailnet
exposure is one command:

```sh
mkdir -p ~/.config/systemd/user && cp deploy/focus.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now focus.service
tailscale serve --bg 8787     # https://<host>.<tailnet>.ts.net/
```

`loginctl enable-linger $USER` keeps it running after logout. backups, upgrades
and the failure modes worth knowing: [docs/deployment.md](docs/deployment.md).

## development

```sh
uv run pytest                 # auth, isolation, crud, subtasks, token api, ai parsing
uvx ruff check .
uvx ruff format --check .
```

the tests use a throwaway `FOCUS_HOME`, so real data is never touched.

```
src/focus/
  main.py        ui routes and template wiring
  api.py         /api/hermes — token-guarded rest for agents
  auth.py        scrypt hashing, signed-cookie sessions
  db.py          schema, one sqlite connection per request
  ai.py          hermes subprocess, prompts, tolerant json parsing, job runner
  cli.py         the `focus` command
  config.py      xdg paths, secret and token generation
  templates/     jinja2, one partial per swapped region
  static/        rose-pine.css, app.js, confetti.js, vendored htmx
```

how the pieces fit: [docs/architecture.md](docs/architecture.md).

## notes

- passwords are `hashlib.scrypt` (n=2¹⁴, r=8, p=1), salted per user. sessions
  are itsdangerous-signed cookies, httpOnly + `samesite=lax`.
- the session cookie is not marked `secure`, because the app is reached over
  loopback and the tailnet. put it behind https and set that flag before
  exposing it to the internet.
- avatars are re-encoded to 96px webp on upload, so a 5 MB phone photo never
  reaches the disk.
- sqlite in WAL mode with foreign keys on; every query is filtered by `user_id`
  and another user's row returns 404.

## licence

mit.
