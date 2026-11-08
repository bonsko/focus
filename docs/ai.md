# ai integration

focus does not talk to a model api and has no keys. every ai action shells out
to a local [hermes](https://github.com/NousResearch/hermes-agent) install:

```sh
hermes -z "<prompt>"
```

that is the whole integration. it means the app inherits whatever model,
provider, tools and skills that agent already has — including web search, which
is what makes "research this" actually research something. it also means the app
gets the agent's personality in the output, which is why the parsing below is
defensive.

## the call

```python
def ask(prompt: str) -> str:
    exe = shutil.which("hermes") or "/usr/sbin/hermes"
    with LOCK:  # one agent at a time
        proc = subprocess.run([exe, "-z", prompt], capture_output=True, text=True, timeout=TIMEOUT)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "hermes failed")[-300:])
    return clean(proc.stdout)
```

- **`LOCK`** is a module-level `threading.Lock`. two people pressing "split" at
  once queue up rather than spawning two agents on a two-core box.
- **`TIMEOUT = 300`** s. a real research turn takes 100–150 s here; a trivial
  one takes ~22 s just to boot the agent.
- **`clean()`** strips ansi codes and any trailing `skipped: …` / `risk: …`
  lines an agent may append to its answer.

if `hermes` is missing, the job fails with a message in the sheet — there is no
silent fallback, because a fake answer is worse than an error.

## split into steps

prompt (in `ai.py`):

```
Break this task into 3-8 small, concrete, doable steps for a person with ADHD.
Each step must start with a verb and fit on one line.
Task: <title>
Notes: <notes or (none)>

Reply with ONLY a JSON array of strings, no code fences, no prose before or after,
for example ["open the file", "rename the section"].
```

models still wrap json in prose or fences, so `first_json()` scans for the
first balanced `{…}` or `[…]`, respecting strings and escapes, and `steps()`
accepts a bare array, an object containing an array, or `{"title": …}` items.
if no json is found at all it falls back to stripping bullet markers from the
lines, so a chatty model still produces something editable.

the result is never saved directly: the sheet renders them as rows with a
checkbox and a text input, and only the submitted set becomes subtasks. that is
the "editable" part — the model proposes, you decide.

## research

prompt:

```
Write plain text only: no markdown at all — no headings with #, no bold with **,
no backticks, no tables, no bullet dashes. Separate ideas with blank lines. If you
must enumerate, write lines like '1. ...'. Links as bare urls.
Research the topic of this task and write a short briefing, at most 200 words …
Begin directly with the first fact: no preamble, no description of what you are
about to do, no closing remarks.
Task: <title>
Notes: <notes or (none)>
```

notes are rendered as preformatted text, not markdown, so the prompt forbids
markdown rather than pretending to render it. `briefing()` still removes a
leading chatty line (`"here's a short briefing:"`) and a markdown heading if the
model ignores the instruction, then the text is appended to the task's notes
under `---`.

the agent is told to research, not to summarise — it has tools, and letting it
use them is the point of going through hermes at all.

## ramble → tasks

`ramble_prompt()` is the third prompt. it is given the dump plus the user's
existing project names, and must answer with

```json
{"tasks": [{"title": "short imperative", "project": "short name"}]}
```

titles only — no notes, no subtasks, at most 15 — and an existing project when
one fits, otherwise a new short lowercase name. `parse_ramble()` accepts the
object, a bare array, or a list of strings, and clamps title/project length.
nothing is written at this stage: the rows are rendered as editable inputs and
`POST /ramble/{id}/accept` creates projects and tasks from what comes back.

## jobs

ai calls never run inside a request. `POST /tasks/{id}/ai/{kind}` inserts a row:

```sql
jobs(id, user_id, task_id, kind, status, result, error, created_at)
```

then starts a thread and returns the panel fragment immediately. the panel is a
self-polling element:

```html
<div id="ai-panel" hx-get="/tasks/3/ai?job=7" hx-trigger="load delay:2s" hx-swap="outerHTML">
```

`STALE_AFTER = 330` s: a job still `running` past that is shown as failed (the
worker was killed, or the box went to sleep) instead of polling forever.

## failure modes

| symptom | cause | what happens |
| --- | --- | --- |
| `hermes took longer than 300s` | big research turn, cold cache | job marked failed, retry button appears |
| `hermes sent no steps back` | model answered in prose with no array | split panel says so; nothing saved |
| empty briefing, notes unchanged | model returned nothing after cleanup | job fails, message shown |
| long garbage in notes | model ignored the plain-text rule | text still lands; the prompt is the only guard |
| two presses, one answer | the global lock | the second job waits, then runs |

## changing the prompts

`split_prompt()` and `research_prompt()` are two functions in `ai.py`; the
trailer stripper and json scanner are `clean()` / `first_json()`. there is no
prompt file to edit and no template language — keep it that way, it is the part
that breaks silently if it becomes configurable.

when you change a prompt, re-run one real action (not a unit test) and read the
output: the tests can only prove the parser handles a fixture, not that the
model behaves.
