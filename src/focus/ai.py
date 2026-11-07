"""AI through the local hermes CLI. No model client, no keys, just a subprocess."""

import json
import re
import shutil
import sqlite3
import subprocess
import threading

from . import db

ANSI = re.compile(r"\x1b\[[0-9;]*m")
TRAILER = re.compile(r"^(skipped|risk)\b", re.I)
LOCK = threading.Lock()  # one hermes call at a time; it is a big local model
TIMEOUT = 300
STALE_AFTER = 330  # seconds; a killed worker must not leave the ui polling forever


def ask(prompt: str) -> str:
    exe = shutil.which("hermes") or "/usr/sbin/hermes"
    with LOCK:
        proc = subprocess.run(
            [exe, "-z", prompt], capture_output=True, text=True, timeout=TIMEOUT, check=False
        )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "hermes failed").strip()[-300:])
    return clean(proc.stdout)


def clean(raw: str) -> str:
    lines = ANSI.sub("", raw).strip().splitlines()
    while lines and (not lines[-1].strip() or TRAILER.match(lines[-1].strip())):
        lines.pop()
    return "\n".join(lines).strip()


def first_json(text: str):
    """Tolerate prose and fences: scan for the first balanced {...} or [...]."""
    for opener, closer in (("[", "]"), ("{", "}")):
        start = text.find(opener)
        while start != -1:
            depth, in_str, esc = 0, False, False
            for i in range(start, len(text)):
                ch = text[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch == opener:
                    depth += 1
                elif ch == closer:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start : i + 1])
                        except json.JSONDecodeError:
                            break
            start = text.find(opener, start + 1)
    return None


def steps(text: str) -> list[str]:
    data = first_json(text)
    if isinstance(data, dict):
        data = next((v for v in data.values() if isinstance(v, list)), None)
    if isinstance(data, list):
        out = [str(i.get("title") or i.get("step") or i.get("text")) for i in data if isinstance(i, dict)]
        out += [str(i) for i in data if not isinstance(i, (dict, list))]
        out = [s.strip() for s in out if s and s.strip()]
        if out:
            return out[:8]
    # fallback: bullet lines, so a chatty model still gives us something editable
    bullets = [re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", ln) for ln in text.splitlines()]
    return [b.strip() for b in bullets if b.strip()][:8]


def split_prompt(task: sqlite3.Row) -> str:
    return (
        "Break this task into 3-8 small, concrete, doable steps for a person with ADHD.\n"
        "Each step must start with a verb and fit on one line.\n"
        f"Task: {task['title']}\nNotes: {task['notes'] or '(none)'}\n\n"
        'Reply with ONLY a JSON array of strings, for example ["open the file", "rename the section"].'
    )


def research_prompt(task: sqlite3.Row) -> str:
    return (
        "Research the topic of this task and write a short briefing in markdown, at most 200 words, "
        "with concrete facts and links where you have them. Begin directly with a markdown heading, "
        "no preamble or closing remarks.\n"
        f"Task: {task['title']}\nNotes: {task['notes'] or '(none)'}"
    )


def briefing(text: str) -> str:
    """Drop the model's preamble; a briefing starts at its first heading."""
    heading = re.search(r"^#{1,6} ", text, re.M)
    return text[heading.start() :] if heading else text


def run_job(job_id: int) -> None:
    """Runs in a background thread after the request returns."""
    con = db.connect()
    try:
        job = con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        task = con.execute(
            "SELECT * FROM tasks WHERE id = ? AND user_id = ?", (job["task_id"], job["user_id"])
        ).fetchone()
        if task is None:
            raise RuntimeError("task vanished")
        try:
            if job["kind"] == "split":
                result = json.dumps(steps(ask(split_prompt(task))))
            else:
                notes = briefing(clean(ask(research_prompt(task))))
                if not notes:
                    raise RuntimeError("hermes returned nothing")
                with con:
                    con.execute(
                        "UPDATE tasks SET notes = trim(notes || ?) WHERE id = ? AND user_id = ?",
                        (
                            ("\n\n---\n\n" if task["notes"].strip() else "") + notes,
                            task["id"],
                            task["user_id"],
                        ),
                    )
                result = "notes added"
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"hermes took longer than {TIMEOUT}s") from None
        with con:
            con.execute("UPDATE jobs SET status = 'done', result = ? WHERE id = ?", (result, job_id))
    except Exception as exc:  # noqa: BLE001 - the message is what the user sees
        with con:
            con.execute("UPDATE jobs SET status = 'error', error = ? WHERE id = ?", (str(exc)[:300], job_id))
    finally:
        con.close()
