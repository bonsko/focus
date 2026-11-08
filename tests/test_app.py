import io

from PIL import Image

from focus import ai, auth, config, db


def test_unknown_username_registers_and_logs_in(client):
    res = client.post("/login", data={"username": "nat", "password": "hunter2"})
    assert res.status_code == 200
    assert "focus_session" in res.cookies or "focus_session" in client.cookies
    con = db.connect()
    user = con.execute("SELECT * FROM users WHERE username = 'nat'").fetchone()
    assert user and user["password"].startswith("scrypt$")
    assert "hunter2" not in user["password"]
    inbox = con.execute("SELECT * FROM projects WHERE user_id = ?", (user["id"],)).fetchone()
    assert inbox["name"] == "inbox"
    con.close()


def test_wrong_password_is_rejected(client):
    client.post("/login", data={"username": "nat", "password": "hunter2"})
    res = client.post("/login", data={"username": "nat", "password": "nope"}, follow_redirects=False)
    assert res.status_code == 401
    assert "wrong password" in res.text


def test_password_hash_roundtrip():
    stored = auth.hash_password("correct horse")
    assert auth.verify_password("correct horse", stored)
    assert not auth.verify_password("correct hors", stored)
    assert not auth.verify_password("x", "garbage")


def test_users_are_isolated(client):
    client.post("/login", data={"username": "nat", "password": "a"})
    project = client.post("/projects", data={"name": "work"}, follow_redirects=False).headers["location"]
    project_id = int(project.rsplit("/", 1)[1])
    client.post(f"/p/{project_id}/tasks", data={"title": "nat's secret"})
    task_id = db.connect().execute("SELECT id FROM tasks").fetchone()["id"]

    other = client  # same jar, new identity
    other.post("/logout")
    other.post("/login", data={"username": "sam", "password": "b"})

    assert other.get(f"/p/{project_id}").status_code == 404
    assert other.get(f"/tasks/{task_id}").status_code == 404
    assert other.post(f"/tasks/{task_id}/toggle").status_code == 404
    assert "nat's secret" not in other.get("/search?q=secret").text


def test_task_crud(client):
    client.post("/login", data={"username": "nat", "password": "a"})
    res = client.post("/projects", data={"name": "work"}, follow_redirects=False)
    project_id = int(res.headers["location"].rsplit("/", 1)[1])

    res = client.post(f"/p/{project_id}/tasks", data={"title": "write the thing"})
    assert "write the thing" in res.text
    con = db.connect()
    task = con.execute("SELECT * FROM tasks").fetchone()
    con.close()

    sheet = client.get(f"/tasks/{task['id']}")
    assert "write the thing" in sheet.text and 'id="subtasks"' in sheet.text

    res = client.post(
        f"/tasks/{task['id']}",
        data={"title": "write the other thing", "notes": "later", "project_id": project_id},
    )
    assert "write the other thing" in res.text
    assert res.headers["HX-Trigger"] == '{"focus:close-sheet": true}'

    res = client.post(f"/tasks/{task['id']}/toggle")
    assert res.headers["HX-Trigger"] == '{"focus:celebrate": true}'
    con = db.connect()
    assert con.execute("SELECT status FROM tasks WHERE id = ?", (task["id"],)).fetchone()["status"] == "done"
    con.close()

    res = client.post(f"/tasks/{task['id']}/delete")
    assert "write the other thing" not in res.text
    assert db.connect().execute("SELECT count(*) FROM tasks").fetchone()[0] == 0


def test_subtask_completion_marks_done(client):
    client.post("/login", data={"username": "nat", "password": "a"})
    project_id = int(
        client.post("/projects", data={"name": "p"}, follow_redirects=False)
        .headers["location"]
        .rsplit("/", 1)[1]
    )
    client.post(f"/p/{project_id}/tasks", data={"title": "big thing"})
    con = db.connect()
    task_id = con.execute("SELECT id FROM tasks").fetchone()["id"]
    con.close()

    res = client.post(
        f"/tasks/{task_id}/subtasks",
        data={"step": ["open it", "read it", "close it"], "done": ["1"]},
    )
    assert res.text.count('class="subtask') == 3
    assert "shimmer" not in res.text  # the gradient effect is gone

    con = db.connect()
    subs = con.execute("SELECT * FROM subtasks WHERE task_id = ? ORDER BY position", (task_id,)).fetchall()
    assert [s["done"] for s in subs] == [0, 1, 0]
    con.close()

    res = client.post(f"/subtasks/{subs[0]['id']}/toggle")
    assert 'class="subtask done"' in res.text
    assert (
        db.connect().execute("SELECT done FROM subtasks WHERE id = ?", (subs[0]["id"],)).fetchone()["done"]
        == 1
    )


def test_hermes_api_needs_token(client, token):
    client.post("/login", data={"username": "nat", "password": "a"})
    assert client.get("/api/hermes/projects?user=nat").status_code == 401
    assert client.get("/api/hermes/projects?user=nat", headers={"X-Focus-Token": "wrong"}).status_code == 401

    created = client.post(
        "/api/hermes/tasks",
        json={"user": "nat", "title": "bump the flake", "project": "nix"},
        headers={"X-Focus-Token": token},
    )
    assert created.status_code == 201, created.text
    task = created.json()
    assert task["project"] == "nix"

    moved = client.patch(
        f"/api/hermes/tasks/{task['id']}",
        json={"user": "nat", "project": "inbox", "notes": "from cron"},
        headers={"X-Focus-Token": token},
    )
    assert moved.json()["project"] == "inbox" and moved.json()["notes"] == "from cron"

    done = client.patch(
        f"/api/hermes/tasks/{task['id']}",
        json={"user": "nat", "status": "done"},
        headers={"X-Focus-Token": token},
    )
    assert done.json()["status"] == "done"

    listed = client.get("/api/hermes/tasks?user=nat&status=done", headers={"X-Focus-Token": token}).json()
    assert [t["id"] for t in listed] == [task["id"]]

    # other users cannot be reached through the token api either
    assert client.get("/api/hermes/tasks?user=nobody", headers={"X-Focus-Token": token}).status_code == 404


def test_ai_output_cleanup_and_parsing():
    raw = 'here you go:\n```json\n["open the file", "rename it"]\n```\nskipped: no need\nrisk: none\n'
    assert ai.clean(raw).endswith("```")
    assert ai.steps(raw) == ["open the file", "rename it"]
    assert ai.steps('[{"title": "one"}, {"step": "two"}]') == ["one", "two"]
    assert ai.steps("- do a thing\n- do the next thing")[:1] == ["do a thing"]
    assert ai.first_json("no json here") is None
    assert ai.briefing("sure! here it is:\n\n# nixpkgs\nfacts") == "# nixpkgs\nfacts"
    assert ai.briefing("no heading, just words") == "no heading, just words"
    assert ai.steps("[1, 2]") == ["1", "2"]


def test_parse_ramble_accepts_shapes():
    assert ai.parse_ramble('{"tasks":[{"title":"Email the lawyer","project":"Personal"}]}') == [
        {"title": "Email the lawyer", "project": "personal"}
    ]
    # bare array of strings, wrapped in prose and fences
    assert ai.parse_ramble('sure:\n```json\n["buy milk", "call mum"]\n```') == [
        {"title": "buy milk", "project": "inbox"},
        {"title": "call mum", "project": "inbox"},
    ]
    assert ai.parse_ramble("no json here at all") == []


def test_ramble_accept_creates_tasks_in_edited_projects(client):
    client.post("/login", data={"username": "nat", "password": "a"})
    con = db.connect()
    user_id = con.execute("SELECT id FROM users WHERE username = 'nat'").fetchone()["id"]
    rid = con.execute(
        "INSERT INTO rambles (user_id, text, status, result) VALUES (?, 'x', 'done', ?)",
        (user_id, '[{"title":"a","project":"inbox"}]'),
    ).lastrowid
    con.commit()
    con.close()

    res = client.post(
        f"/ramble/{rid}/accept",
        data={"title": ["email the lawyer", "", "renew the domain"], "project": ["papers", "ignored", "web"]},
    )
    assert res.status_code == 200
    assert "2 tasks added" in res.text

    con = db.connect()
    rows = con.execute(
        "SELECT t.title, p.name FROM tasks t JOIN projects p ON p.id = t.project_id "
        "WHERE t.user_id = ? ORDER BY t.id",
        (user_id,),
    ).fetchall()
    # blank titles are dropped, projects come from the edited form
    assert [(r["title"], r["name"]) for r in rows] == [
        ("email the lawyer", "papers"),
        ("renew the domain", "web"),
    ]
    # no notes, no subtasks generated
    assert con.execute("SELECT count(*) FROM subtasks").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM tasks WHERE notes != ''").fetchone()[0] == 0
    con.close()


def test_theme_is_per_user_and_rendered(client):
    client.post("/login", data={"username": "nat", "password": "a"})
    out = client.post(
        "/settings",
        data={"display_name": "nat", "theme_light": "catppuccin-latte", "theme_dark": "gruvbox-dark-hard"},
    )
    assert out.status_code == 200
    con = db.connect()
    row = con.execute("SELECT theme_light, theme_dark FROM users WHERE username = 'nat'").fetchone()
    con.close()
    assert (row["theme_light"], row["theme_dark"]) == ("catppuccin-latte", "gruvbox-dark-hard")

    page = client.get("/").text
    assert "--base:#eff1f5" in page  # catppuccin latte background
    assert "@media (prefers-color-scheme:dark)" in page
    assert "--base:#1d2021" in page  # gruvbox dark hard background


def test_theme_rejects_a_scheme_of_the_wrong_variant(client):
    client.post("/login", data={"username": "nat", "password": "a"})
    client.post("/settings", data={"theme_light": "gruvbox-dark-hard", "theme_dark": "catppuccin-latte"})
    con = db.connect()
    row = con.execute("SELECT theme_light, theme_dark FROM users WHERE username = 'nat'").fetchone()
    con.close()
    assert (row["theme_light"], row["theme_dark"]) == ("rose-pine-dawn", "rose-pine-moon")


def test_scale_cookie_is_clamped(client):
    client.cookies.set("focus_scale", "1.25")
    assert 'zoom:1.25"' in client.get("/login").text
    client.cookies.set("focus_scale", "9")
    assert 'zoom:1"' in client.get("/login").text


def _png_with_alpha(size=200):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    img.paste((90, 60, 160, 255), (size // 4, size // 4, size * 3 // 4, size * 3 // 4))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_avatar_keeps_transparency(client):
    client.post("/login", data={"username": "nat", "password": "a"})
    res = client.post(
        "/settings/avatar",
        files={"avatar": ("me.png", _png_with_alpha(), "image/png")},
    )
    assert res.status_code == 200

    con = db.connect()
    stored = con.execute("SELECT avatar FROM users WHERE username = 'nat'").fetchone()["avatar"]
    con.close()
    assert stored == "1.webp"

    saved = Image.open(config.AVATARS / stored)
    assert saved.mode == "RGBA"
    assert saved.getextrema()[3][0] == 0, "fully transparent pixels survived the re-encode"


def test_avatar_rejects_binaries_and_oversize(client):
    client.post("/login", data={"username": "nat", "password": "a"})
    before = set(config.AVATARS.glob("*.webp"))

    res = client.post("/settings/avatar", files={"avatar": ("evil.exe", b"MZ\x90\x00" + b"\x00" * 4000)})
    assert res.status_code == 400
    assert "not an image" in res.text

    res = client.post(
        "/settings/avatar",
        files={"avatar": ("big.png", b"\x89PNG\r\n\x1a\n" + b"0" * (2 * 1024 * 1024))},
    )
    assert res.status_code == 400
    assert "bigger than 2 MB" in res.text

    assert set(config.AVATARS.glob("*.webp")) == before, "nothing was written for either attempt"


def test_avatar_path_cannot_escape(client):
    client.post("/login", data={"username": "nat", "password": "a"})
    assert client.get("/avatars/999.webp").status_code == 404
    assert client.get("/avatars/..%2F..%2Fetc%2Fpasswd.webp").status_code == 404
    assert client.get("/avatars/evil.svg").status_code == 404
