"""`focus` CLI — lets cron jobs and scripts hand tasks to a user."""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from . import config


def call(method: str, path: str, body: dict | None = None):
    base = os.environ.get("FOCUS_URL", "http://127.0.0.1:8787").rstrip("/")
    try:
        token = config.hermes_token()
    except FileNotFoundError:
        sys.exit(f"no token at {config.TOKEN_PATH}; start focus once to create it")
    req = urllib.request.Request(
        base + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "X-Focus-Token": token},
    )
    try:
        with urllib.request.urlopen(req) as res:
            return json.loads(res.read() or b"null")
    except urllib.error.HTTPError as exc:
        sys.exit(f"{exc.code}: {exc.read().decode()[:300]}")
    except urllib.error.URLError as exc:
        sys.exit(f"cannot reach {base}: {exc.reason}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="focus", description="a quiet task list, from the terminal")
    sub = parser.add_subparsers(dest="cmd", required=True)

    add = sub.add_parser("add", help="create a task")
    add.add_argument("--user", required=True)
    add.add_argument("--project", default="inbox")
    add.add_argument("--title", required=True)
    add.add_argument("--notes", default="")

    ls = sub.add_parser("list", help="list a user's tasks")
    ls.add_argument("--user", required=True)
    ls.add_argument("--project")
    ls.add_argument("--all", action="store_true", help="include finished tasks")

    move = sub.add_parser("move", help="move a task to another project")
    move.add_argument("id", type=int)
    move.add_argument("--user", required=True)
    move.add_argument("--project", required=True)

    done = sub.add_parser("done", help="mark a task done")
    done.add_argument("id", type=int)
    done.add_argument("--user", required=True)

    projects = sub.add_parser("projects", help="list projects")
    projects.add_argument("--user", required=True)

    args = parser.parse_args(argv)

    if args.cmd == "add":
        task = call(
            "POST",
            "/api/hermes/tasks",
            {"user": args.user, "title": args.title, "project": args.project, "notes": args.notes},
        )
        print(f"#{task['id']} {task['project']}  {task['title']}")
    elif args.cmd == "list":
        query = f"?user={args.user}"
        if args.project:
            query += f"&project={args.project}"
        status = "all" if args.all else "open"
        for task in call("GET", f"/api/hermes/tasks{query}&status={status}"):
            mark = "x" if task["status"] == "done" else " "
            print(f"{task['id']:>4} [{mark}] {task['project']:<10} {task['title']}")
    elif args.cmd == "move":
        task = call("PATCH", f"/api/hermes/tasks/{args.id}", {"user": args.user, "project": args.project})
        print(f"#{task['id']} → {task['project']}")
    elif args.cmd == "done":
        task = call("PATCH", f"/api/hermes/tasks/{args.id}", {"user": args.user, "status": "done"})
        print(f"#{task['id']} done")
    elif args.cmd == "projects":
        for project in call("GET", f"/api/hermes/projects?user={args.user}"):
            print(project["name"])


if __name__ == "__main__":
    main()
