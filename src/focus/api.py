"""Authenticated REST API for hermes, cron jobs and the `focus` CLI."""

import sqlite3

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from . import auth, db

router = APIRouter(prefix="/api/hermes", tags=["hermes"])


def guard(x_focus_token: str | None = Header(default=None)) -> None:
    if not auth.api_token_ok(x_focus_token):
        raise HTTPException(status_code=401, detail="bad or missing X-Focus-Token")


def _user(con: sqlite3.Connection, username: str) -> sqlite3.Row:
    user = con.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if user is None:
        raise HTTPException(status_code=404, detail=f"no such user: {username}")
    return user


def _project(con: sqlite3.Connection, user: sqlite3.Row, name: str, create: bool = True) -> sqlite3.Row:
    row = con.execute("SELECT * FROM projects WHERE user_id = ? AND name = ?", (user["id"], name)).fetchone()
    if row:
        return row
    if not create:
        raise HTTPException(status_code=404, detail=f"no such project: {name}")
    with con:
        cur = con.execute(
            "INSERT INTO projects (user_id, name, position) "
            "VALUES (?, ?, (SELECT coalesce(max(position), 0) + 1 FROM projects WHERE user_id = ?))",
            (user["id"], name.strip(), user["id"]),
        )
    return con.execute("SELECT * FROM projects WHERE id = ?", (cur.lastrowid,)).fetchone()


def _task_json(con: sqlite3.Connection, task: sqlite3.Row) -> dict:
    project = con.execute("SELECT name FROM projects WHERE id = ?", (task["project_id"],)).fetchone()
    subs = con.execute(
        "SELECT id, title, done FROM subtasks WHERE task_id = ? ORDER BY position, id",
        (task["id"],),
    ).fetchall()
    return {
        "id": task["id"],
        "title": task["title"],
        "notes": task["notes"],
        "status": task["status"],
        "project": project["name"] if project else None,
        "project_id": task["project_id"],
        "created_at": task["created_at"],
        "completed_at": task["completed_at"],
        "subtasks": [dict(s) for s in subs],
    }


class NewTask(BaseModel):
    user: str
    title: str = Field(min_length=1, max_length=500)
    project: str = "inbox"
    notes: str = ""


class EditTask(BaseModel):
    user: str
    title: str | None = Field(default=None, min_length=1, max_length=500)
    notes: str | None = None
    status: str | None = None
    project: str | None = None


@router.get("/projects", dependencies=[Depends(guard)])
def list_projects(user: str, con: sqlite3.Connection = Depends(db.get_db)):
    row = _user(con, user)
    projects = con.execute(
        "SELECT id, name FROM projects WHERE user_id = ? ORDER BY position, id", (row["id"],)
    ).fetchall()
    return [dict(p) for p in projects]


@router.get("/tasks", dependencies=[Depends(guard)])
def list_tasks(
    user: str,
    project: str | None = None,
    status: str = "open",
    con: sqlite3.Connection = Depends(db.get_db),
):
    row = _user(con, user)
    sql = "SELECT * FROM tasks WHERE user_id = ?"
    args: list = [row["id"]]
    if project:
        sql += " AND project_id = ?"
        args.append(_project(con, row, project, create=False)["id"])
    if status in ("open", "done"):
        sql += " AND status = ?"
        args.append(status)
    sql += " ORDER BY status = 'done', position, id"
    return [_task_json(con, t) for t in con.execute(sql, args).fetchall()]


@router.post("/tasks", status_code=201, dependencies=[Depends(guard)])
def create_task(body: NewTask, con: sqlite3.Connection = Depends(db.get_db)):
    row = _user(con, body.user)
    project = _project(con, row, body.project)
    with con:
        cur = con.execute(
            "INSERT INTO tasks (user_id, project_id, title, notes, position) VALUES (?, ?, ?, ?, "
            "(SELECT coalesce(max(position), 0) + 1 FROM tasks WHERE project_id = ?))",
            (row["id"], project["id"], body.title.strip(), body.notes, project["id"]),
        )
    return _task_json(con, con.execute("SELECT * FROM tasks WHERE id = ?", (cur.lastrowid,)).fetchone())


@router.patch("/tasks/{task_id}", dependencies=[Depends(guard)])
def edit_task(task_id: int, body: EditTask, con: sqlite3.Connection = Depends(db.get_db)):
    user = _user(con, body.user)
    task = con.execute("SELECT * FROM tasks WHERE id = ? AND user_id = ?", (task_id, user["id"])).fetchone()
    if task is None:
        raise HTTPException(status_code=404, detail="no such task for that user")
    sets, args = [], []
    if body.title is not None:
        sets.append("title = ?")
        args.append(body.title.strip())
    if body.notes is not None:
        sets.append("notes = ?")
        args.append(body.notes)
    if body.project is not None:
        sets.append("project_id = ?")
        args.append(_project(con, user, body.project)["id"])
    if body.status is not None:
        if body.status not in ("open", "done"):
            raise HTTPException(status_code=422, detail="status must be open or done")
        sets.append("status = ?")
        args.append(body.status)
        sets.append("completed_at = " + ("datetime('now')" if body.status == "done" else "NULL"))
    if not sets:
        raise HTTPException(status_code=422, detail="nothing to change")
    with con:
        con.execute(
            f"UPDATE tasks SET {', '.join(sets)} WHERE id = ? AND user_id = ?",
            (*args, task_id, user["id"]),
        )
    return _task_json(con, con.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone())
