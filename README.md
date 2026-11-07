# focus

a quiet personal task list for one brain at a time.

one sidebar of projects, one list of tasks, an inline add field, and a task
sheet that opens when you click a row. no boards, no comments, no teams, no
counters, no notifications. lowercase everywhere.

- python 3 + sqlite + [htmx](https://htmx.org), one process
- fastapi + jinja2, no build step, htmx is vendored in `static/vendor/`
- rosé pine **moon** and **dawn**, following `prefers-color-scheme` on `auto`
- soulseek-style auth: one form, any name + password. if the name is new it
  becomes an account; if it exists the password must match
- everything is scoped to its user. there is no sharing
- local ai through the hermes cli: split a task into steps, or research it
- a tiny authenticated rest api, so hermes and cron jobs can hand you tasks

## keyboard

| key | does |
| --- | --- |
| `n` | new task |
| `j` / `k` | move down / up the list |
| `x` | complete (or reopen) the selected task |
| `/` | search |
| `esc` | close the task sheet |

## run it

```sh
uv sync
uv run uvicorn focus.main:app --port 8787
```

open <http://127.0.0.1:8787>, type a name and a password, and you are in.
the first project (`inbox`) is created for you.

data lives outside the repo, in `~/.local/share/focus/data/`:

```
~/.local/share/focus/data/focus.db     sqlite database
~/.local/share/focus/data/secret       session signing key (generated once, 0600)
~/.local/share/focus/data/avatars/     avatars, resized to 96px webp
~/.config/focus/hermes_token           api token (generated on first start, 0600)
```

override the data root with `FOCUS_HOME` (used by the tests).

## themes

the palette is the official rosé pine set. `auto` follows the system,
`dawn` and `moon` are manual, and the choice is stored per user. the small
button at the bottom of the sidebar cycles auto → light → dark.

## ai, through hermes

there is no model client here and no api key. the app shells out to the local
hermes cli (`hermes -z "<prompt>"`) in a background thread, stores the job in
sqlite, and the sheet polls every two seconds. hermes is slow (20s–3min), so
nothing blocks the ui, and every failure ends up as a red message with a retry
button instead of a spinner forever.

- **split into small steps** — asks for 3–8 concrete steps, tolerantly parses
  the first balanced json array out of whatever prose the model wrapped it in,
  and shows them as editable rows (checkbox + text) before saving.
- **research this** — asks for a short markdown briefing, strips the
  `skipped: … / risk: …` trailer, and appends it to the task's notes under a
  rule.

neither action needs configuration. if `hermes` is not on `PATH` the job fails
loudly in the sheet.

## the hermes api and the `focus` cli

`/api/hermes/…` is authenticated with `X-Focus-Token`, read from
`~/.config/focus/hermes_token`. a token is generated on first start.

| method | path | does |
| --- | --- | --- |
| `POST` | `/api/hermes/tasks` | create: `{user, title, project?, notes?}` |
| `GET` | `/api/hermes/tasks?user=…` | list (`project`, `status=open\|done` filters) |
| `PATCH` | `/api/hermes/tasks/{id}` | edit title/notes/status and move (`project`) |
| `GET` | `/api/hermes/projects?user=…` | list projects |

missing projects are created on first use. the `focus` cli wraps the api:

```sh
focus add --user nat --project inbox --title "renew the domain"
focus move 42 --user nat --project homelab
focus list --user nat --all
focus done 42 --user nat
focus projects --user nat
```

`FOCUS_URL` points the cli at a non-default server.

### a cron example

drop one task in a user's project when the nix flake's nixpkgs pin moves:

```cron
# crontab -e; adjust paths and project name
0 8 * * * cd "$HOME/nixos" && nix flake update --dry-run 2>/dev/null | grep -q nixpkgs \
  && "$HOME/.local/bin/focus" add --user nat --project homelab \
       --title "nixpkgs moved upstream — bump the flake"
```

the agent can do the same thing in a conversation: create, edit, and move
tasks through the api above.

## deploy (systemd user unit + tailscale)

```sh
mkdir -p ~/.config/systemd/user
cp deploy/focus.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now focus.service
tailscale serve --bg 8787
```

the unit runs `uv run --frozen uvicorn focus.main:app` on `127.0.0.1:8787`;
tailscale terminates tls and serves it on the tailnet. `loginctl enable-linger`
keeps it up after logout. check with:

```sh
systemctl --user status focus.service
curl -sI https://<host>.<tailnet>.ts.net/ | head -1
```

## tests and lint

```sh
uv run pytest          # auth, isolation, crud, subtasks, token api, ai parsing
uvx ruff check .
uvx ruff format --check .
```

the tests use a throwaway `FOCUS_HOME`, so your real data is never touched.

## layout

```
src/focus/
  main.py      ui routes + templates wiring
  api.py       token-authenticated rest api
  auth.py      scrypt hashing, signed-cookie sessions
  ai.py        hermes subprocess, output cleanup, job runner
  db.py        schema, one sqlite connection per request
  config.py    XDG paths, secret and token generation
  cli.py       the `focus` command
  templates/   jinja2, server-rendered fragments
  static/      css, js (confetti has no dependency), vendored htmx
```

## notes

- passwords are hashed with `hashlib.scrypt` (n=2¹⁴, r=8, p=1), salted per user.
  sessions are itsdangerous-signed cookies, httpOnly + `samesite=lax`.
- the session cookie is not marked `secure` because the app is reached over
  loopback and the tailnet; put it behind https-only if you expose it further.
- avatars are re-encoded to 96px webp on upload, so you never serve a 5 MB
  phone photo.
- sqlite in WAL mode, foreign keys on, every query filtered by `user_id`.

## license

mit.
