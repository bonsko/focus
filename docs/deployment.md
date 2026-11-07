# deployment

one uvicorn process behind tailscale serve. no reverse proxy needed unless you
want to expose it beyond the tailnet.

## systemd user unit

`deploy/focus.service`:

```ini
[Unit]
Description=focus — a quiet task list
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/projects/focus
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/usr/sbin:/bin
Environment=FOCUS_HOME=%h/.local/share/focus
ExecStart=/usr/bin/env uv run --frozen uvicorn focus.main:app --host 127.0.0.1 --port 8787
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
```

```sh
mkdir -p ~/.config/systemd/user
cp deploy/focus.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now focus.service
loginctl enable-linger "$USER"     # start at boot without logging in
```

`--frozen` means the unit refuses to resolve or re-lock dependencies at start;
run `uv sync` after a pull instead. `uv` must be on the `PATH` the unit sets —
if it lives in `~/.cargo/bin`, add that directory.

check it:

```sh
systemctl --user status focus.service
journalctl --user -u focus.service -n 30
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8787/login
```

`200` on `/login`, `303` on `/` — that is a healthy instance.

## tailnet

```sh
tailscale serve --bg 8787          # https://<host>.<tailnet>.ts.net/
tailscale serve status
tailscale serve reset              # undo
```

serve terminates tls with a tailnet certificate and only accepts tailnet
traffic. verify from the server itself before blaming the client:

```sh
curl -s --resolve <host>.<tailnet>.ts.net:443:$(tailscale ip -4 | head -1) \
     -o /dev/null -w '%{http_code}\n' https://<host>.<tailnet>.ts.net/login
```

if a *remote* machine cannot reach it, check `tailscale status` for that peer
first: an offline node ("last seen 8d ago") and a dead app look identical in a
browser. `tailscale ping <peer>` settles it in one line.

### behind a normal reverse proxy

if you must expose it on the internet, terminate tls in caddy/nginx, forward to
`127.0.0.1:8787`, and then:

- set the session cookie `secure` (one flag in `auth.py`)
- add a csrf token to the forms, or the app is protected only by `samesite=lax`
- rate-limit `/login` — registration is open by design

## backups

sqlite in wal mode: copy with the backup api, not `cp`:

```sh
sqlite3 ~/.local/share/focus/data/focus.db ".backup '/tmp/focus-$(date +%F).db'"
```

that is the entire state except avatars (`data/avatars/`) and the session secret
(`data/secret`). restoring is: stop the unit, put the file back, start it. no
migration step, the schema is applied at startup.

## upgrades

```sh
cd ~/projects/focus
git pull
uv sync
systemctl --user restart focus.service
```

if you changed css or js, bump the `?v=` string in `base.html` as part of the
commit — otherwise browsers keep the old asset and the deploy looks broken.

## troubleshooting

| symptom | cause | fix |
| --- | --- | --- |
| page 200s on loopback, dead on the tailnet url | peer offline, or serve not pointing at the port | `tailscale status`, `tailscale serve status` |
| a button needs several clicks | two nested `hx-*` handlers firing two requests | stop propagation on the child |
| the sheet appears inline and the sidebar falls to the bottom | the `#sheet` container was replaced instead of its children | keep the response rooted at `<aside id="sheet">` and swap `outerHTML` |
| changed css/js but nothing moved | browser cache | the `?v=` bump, or a hard reload |
| layout suddenly stacky | viewport ≤ 720px (the mobile breakpoint) — also triggered by browser zoom | check `window.innerWidth` |
| `database is locked` | two writers, or a long ai job holding a write | ai jobs only write once at the end; look for another process |
| ai panel spins forever | worker killed, job stuck `running` | jobs older than 330 s are reported stale; retry |
| `hermes` not found in the unit | unit `PATH` lacks the install | add the directory to `Environment=PATH=` |

## what is not handled

- no tls on the loopback listener, no auth on `/health`
- no log rotation beyond journald's own
- no multi-host: sqlite means one machine, one writer
- no backup automation; the `.backup` command above is meant for cron
