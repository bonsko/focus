"""focus — a quiet task list. FastAPI + sqlite + htmx, no build step."""

import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageOps, UnidentifiedImageError

from . import ai, api, auth, config, db

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters["initial"] = lambda s: (s or "?")[0].upper()
templates.env.filters["steps"] = lambda raw: ai.steps(raw) if raw else []


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    if not config.TOKEN_PATH.exists():
        config.TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.TOKEN_PATH.write_text(secrets.token_hex(24) + "\n")
        config.TOKEN_PATH.chmod(0o600)
    yield


app = FastAPI(title="focus", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
app.include_router(api.router)


class NeedLogin(Exception):
    pass


@app.exception_handler(NeedLogin)
def _need_login(request: Request, _: NeedLogin):
    if request.headers.get("HX-Request"):
        return Response(status_code=204, headers={"HX-Redirect": "/login"})
    return RedirectResponse("/login", status_code=303)


def current_user(request: Request, con=Depends(db.get_db)) -> db.sqlite3.Row:
    user = auth.user_from_request(request, con)
    if user is None:
        raise NeedLogin
    return user


def owned(con, user, table: str, row_id: int):
    row = con.execute(f"SELECT * FROM {table} WHERE id = ? AND user_id = ?", (row_id, user["id"])).fetchone()
    if row is None:
        raise HTTPException(404)
    return row


def inbox(con, user) -> int:
    row = con.execute(
        "SELECT id FROM projects WHERE user_id = ? ORDER BY position, id LIMIT 1", (user["id"],)
    ).fetchone()
    return row["id"]


def projects_of(con, user):
    return con.execute(
        "SELECT * FROM projects WHERE user_id = ? ORDER BY position, id", (user["id"],)
    ).fetchall()


def task_query(con, user, project_id=None, query=None):
    sql = (
        "SELECT t.*, p.name AS project FROM tasks t "
        "JOIN projects p ON p.id = t.project_id WHERE t.user_id = ?"
    )
    args: list = [user["id"]]
    if project_id is not None:
        sql += " AND t.project_id = ?"
        args.append(project_id)
    if query:
        sql += " AND (t.title LIKE ? OR t.notes LIKE ?)"
        args += [f"%{query}%"] * 2
    sql += " ORDER BY t.status = 'done', t.completed_at DESC, t.position, t.id"
    return con.execute(sql, args).fetchall()


def render_list(request: Request, con, user, project_id=None, query=None, status_code=200):
    return templates.TemplateResponse(
        request,
        "partials/task_list.html",
        {
            "tasks": task_query(con, user, project_id, query),
            "query": query,
            "project_id": project_id,
        },
        status_code=status_code,
    )


def goto_project(con, user) -> RedirectResponse:
    return RedirectResponse(f"/p/{inbox(con, user)}", status_code=303)


@app.get("/health")
def health():
    return {"ok": True}


# ---------------------------------------------------------------- auth


@app.get("/login")
def login_page(request: Request, con=Depends(db.get_db)):
    if auth.user_from_request(request, con):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
def login(request: Request, username: str = Form(), password: str = Form(), con=Depends(db.get_db)):
    username = username.strip()[:32]
    if not username or not password or len(password) > 1024:
        return templates.TemplateResponse(
            request, "login.html", {"error": "a name and a password, please"}, status_code=400
        )
    user = auth.login_or_register(con, username, password)
    if user is None:
        return templates.TemplateResponse(
            request, "login.html", {"error": "that name is taken — wrong password"}, status_code=401
        )
    response = goto_project(con, user)
    response.set_cookie(
        auth.COOKIE,
        auth.session_cookie(user["username"]),
        max_age=auth.MAX_AGE,
        httponly=True,
        samesite="lax",
    )  # not `secure`: the app is only reachable on loopback / the tailnet
    return response


@app.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(auth.COOKIE)
    return response


# ---------------------------------------------------------------- pages


@app.get("/")
def home(con=Depends(db.get_db), user=Depends(current_user)):
    return goto_project(con, user)


@app.get("/p/{project_id}")
def project_page(request: Request, project_id: int, con=Depends(db.get_db), user=Depends(current_user)):
    project = owned(con, user, "projects", project_id)
    tasks = task_query(con, user, project_id)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "user": user,
            "projects": projects_of(con, user),
            "project": project,
            "tasks": tasks,
            "query": None,
            "project_id": project_id,
        },
    )


@app.post("/projects")
def new_project(name: str = Form(), con=Depends(db.get_db), user=Depends(current_user)):
    name = name.strip()[:40]
    if name:
        with con:
            con.execute(
                "INSERT OR IGNORE INTO projects (user_id, name, position) VALUES (?, ?, "
                "(SELECT coalesce(max(position), 0) + 1 FROM projects WHERE user_id = ?))",
                (user["id"], name, user["id"]),
            )
        row = con.execute(
            "SELECT id FROM projects WHERE user_id = ? AND name = ?", (user["id"], name)
        ).fetchone()
        return RedirectResponse(f"/p/{row['id']}", status_code=303)
    return goto_project(con, user)


@app.get("/search")
def search(request: Request, q: str = "", con=Depends(db.get_db), user=Depends(current_user)):
    return render_list(request, con, user, query=q.strip() or None)


# ---------------------------------------------------------------- tasks


@app.post("/p/{project_id}/tasks")
def new_task(
    request: Request,
    project_id: int,
    title: str = Form(),
    con=Depends(db.get_db),
    user=Depends(current_user),
):
    owned(con, user, "projects", project_id)
    title = title.strip()[:500]
    if title:
        with con:
            con.execute(
                "INSERT INTO tasks (user_id, project_id, title, position) VALUES (?, ?, ?, "
                "(SELECT coalesce(max(position), 0) + 1 FROM tasks WHERE project_id = ?))",
                (user["id"], project_id, title, project_id),
            )
    return render_list(request, con, user, project_id)


@app.get("/tasks/{task_id}")
def task_sheet(request: Request, task_id: int, con=Depends(db.get_db), user=Depends(current_user)):
    task = owned(con, user, "tasks", task_id)
    return render_sheet(request, con, user, task)


def render_sheet(request: Request, con, user, task, job=None):
    subs = con.execute(
        "SELECT * FROM subtasks WHERE task_id = ? ORDER BY position, id", (task["id"],)
    ).fetchall()
    return templates.TemplateResponse(
        request,
        "partials/sheet.html",
        {"task": task, "subtasks": subs, "projects": projects_of(con, user), "job": job},
    )


@app.post("/tasks/{task_id}")
def edit_task(
    request: Request,
    task_id: int,
    title: str = Form(),
    notes: str = Form(""),
    project_id: int = Form(),
    con=Depends(db.get_db),
    user=Depends(current_user),
):
    task = owned(con, user, "tasks", task_id)
    owned(con, user, "projects", project_id)
    title = title.strip()[:500]
    if title:
        with con:
            con.execute(
                "UPDATE tasks SET title = ?, notes = ?, project_id = ? WHERE id = ? AND user_id = ?",
                (title, notes, project_id, task["id"], user["id"]),
            )
    response = render_list(request, con, user, project_id)
    response.headers["HX-Trigger"] = '{"focus:close-sheet": true}'
    return response


@app.post("/tasks/{task_id}/toggle")
def toggle_task(request: Request, task_id: int, con=Depends(db.get_db), user=Depends(current_user)):
    task = owned(con, user, "tasks", task_id)
    done = task["status"] != "done"
    with con:
        con.execute(
            "UPDATE tasks SET status = ?, completed_at = ? WHERE id = ? AND user_id = ?",
            (
                "done" if done else "open",
                "datetime('now')" if done else None,
                task["id"],
                user["id"],
            ),
        )
    response = render_list(request, con, user, task["project_id"])
    if done:
        response.headers["HX-Trigger"] = '{"focus:celebrate": true}'
    return response


@app.post("/tasks/{task_id}/delete")
def delete_task(request: Request, task_id: int, con=Depends(db.get_db), user=Depends(current_user)):
    task = owned(con, user, "tasks", task_id)
    with con:
        con.execute("DELETE FROM tasks WHERE id = ? AND user_id = ?", (task["id"], user["id"]))
    response = render_list(request, con, user, task["project_id"])
    response.headers["HX-Trigger"] = '{"focus:close-sheet": true}'
    return response


# ---------------------------------------------------------------- subtasks


@app.post("/tasks/{task_id}/subtasks")
def add_subtasks(
    task_id: int,
    request: Request,
    step: list[str] = Form(default=[]),
    done: list[str] = Form(default=[]),
    con=Depends(db.get_db),
    user=Depends(current_user),
):
    task = owned(con, user, "tasks", task_id)
    steps = [s.strip()[:200] for s in step if s.strip()]
    ticked = {int(d) for d in done if d.isdigit()}
    with con:
        base = con.execute(
            "SELECT coalesce(max(position), 0) FROM subtasks WHERE task_id = ?", (task["id"],)
        ).fetchone()[0]
        con.executemany(
            "INSERT INTO subtasks (task_id, title, done, position) VALUES (?, ?, ?, ?)",
            [(task["id"], s, 1 if i in ticked else 0, base + i + 1) for i, s in enumerate(steps)],
        )
    return render_subtasks(request, con, task, clear_ai=len(steps) > 1)


def render_subtasks(request: Request, con, task, clear_ai=False):
    subs = con.execute(
        "SELECT * FROM subtasks WHERE task_id = ? ORDER BY position, id", (task["id"],)
    ).fetchall()
    return templates.TemplateResponse(
        request, "partials/subtasks.html", {"task": task, "subtasks": subs, "clear_ai": clear_ai}
    )


@app.post("/subtasks/{subtask_id}/toggle")
def toggle_subtask(request: Request, subtask_id: int, con=Depends(db.get_db), user=Depends(current_user)):
    sub = con.execute(
        "SELECT s.* FROM subtasks s JOIN tasks t ON t.id = s.task_id WHERE s.id = ? AND t.user_id = ?",
        (subtask_id, user["id"]),
    ).fetchone()
    if sub is None:
        raise HTTPException(404)
    with con:
        con.execute("UPDATE subtasks SET done = ? WHERE id = ?", (0 if sub["done"] else 1, sub["id"]))
    sub = con.execute("SELECT * FROM subtasks WHERE id = ?", (sub["id"],)).fetchone()
    return templates.TemplateResponse(
        request, "partials/subtask.html", {"sub": sub, "task_id": sub["task_id"]}
    )


# ---------------------------------------------------------------- settings


def settings_ctx(con, user):
    return {
        "user": con.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone(),
        "projects": projects_of(con, user),
    }


@app.get("/settings")
def settings_page(request: Request, con=Depends(db.get_db), user=Depends(current_user)):
    return templates.TemplateResponse(request, "settings.html", settings_ctx(con, user))


@app.post("/settings")
def save_settings(
    request: Request,
    display_name: str = Form(""),
    theme: str = Form("auto"),
    con=Depends(db.get_db),
    user=Depends(current_user),
):
    if theme not in ("auto", "light", "dark"):
        theme = "auto"
    with con:
        con.execute(
            "UPDATE users SET display_name = ?, theme = ? WHERE id = ?",
            (display_name.strip()[:40] or user["username"], theme, user["id"]),
        )
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/theme")
def cycle_theme(
    next: str = Form("/"),
    theme: str = Form("auto"),
    con=Depends(db.get_db),
    user=Depends(current_user),
):
    if theme not in ("auto", "light", "dark"):
        theme = "auto"
    with con:
        con.execute("UPDATE users SET theme = ? WHERE id = ?", (theme, user["id"]))
    return RedirectResponse(next if next.startswith("/") else "/", status_code=303)


@app.post("/settings/avatar")
def upload_avatar(
    request: Request,
    avatar: UploadFile = File(),
    con=Depends(db.get_db),
    user=Depends(current_user),
):
    try:
        image = ImageOps.exif_transpose(Image.open(avatar.file)).convert("RGB")
    except (UnidentifiedImageError, OSError):
        return templates.TemplateResponse(
            request,
            "settings.html",
            settings_ctx(con, user) | {"error": "that is not an image"},
            status_code=400,
        )
    image.thumbnail((96, 96))
    name = f"{user['id']}.webp"
    image.save(config.AVATARS / name, "WEBP", quality=85)
    with con:
        con.execute("UPDATE users SET avatar = ? WHERE id = ?", (name, user["id"]))
    return RedirectResponse("/settings", status_code=303)


@app.get("/avatars/{name}")
def avatar(name: str, con=Depends(db.get_db), user=Depends(current_user)):
    path = config.AVATARS / name
    if not name.endswith(".webp") or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "private, max-age=300"})


# ---------------------------------------------------------------- ai


@app.post("/tasks/{task_id}/ai/{kind}")
def start_ai(
    request: Request,
    task_id: int,
    kind: str,
    background: BackgroundTasks,
    con=Depends(db.get_db),
    user=Depends(current_user),
):
    if kind not in ("split", "research"):
        raise HTTPException(404)
    task = owned(con, user, "tasks", task_id)
    with con:
        cur = con.execute(
            "INSERT INTO jobs (user_id, task_id, kind) VALUES (?, ?, ?)",
            (user["id"], task["id"], kind),
        )
        job_id = cur.lastrowid
    background.add_task(ai.run_job, job_id)
    job = con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return render_ai_panel(request, con, user, task, job)


@app.get("/tasks/{task_id}/ai")
def ai_status(
    request: Request,
    task_id: int,
    job: int,
    con=Depends(db.get_db),
    user=Depends(current_user),
):
    task = owned(con, user, "tasks", task_id)
    # a worker killed mid-call must not leave the sheet spinning forever
    with con:
        con.execute(
            "UPDATE jobs SET status = 'error', error = 'the ai call stopped early — try again' "
            "WHERE id = ? AND user_id = ? AND status = 'running' "
            "AND (julianday('now') - julianday(created_at)) * 86400 > ?",
            (job, user["id"], ai.STALE_AFTER),
        )
    row = con.execute(
        "SELECT * FROM jobs WHERE id = ? AND user_id = ? AND task_id = ?",
        (job, user["id"], task_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404)
    if row["status"] == "done" and row["kind"] == "research":
        task = con.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        response = render_sheet(request, con, user, task)
        response.headers["HX-Retarget"] = "#sheet"
        response.headers["HX-Reswap"] = "outerHTML"
        return response
    return render_ai_panel(request, con, user, task, row)


def render_ai_panel(request: Request, con, user, task, job):
    return templates.TemplateResponse(request, "partials/ai_panel.html", {"task": task, "job": job})
