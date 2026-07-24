"""
gui/server.py — local FastAPI backend for the derawtisation_pipe GUI
─────────────────────────────────────────────────────────────────────
A thin, local-only web server that drives the EXISTING pipeline scripts — it adds
no image-processing logic of its own, it just:
  * serves the single-page frontend (gui/static/index.html),
  * reads/writes config.yaml (comment-preserving, via ruamel.yaml),
  * lists run folders and how far each has progressed,
  * runs a pipeline step as a subprocess and streams its console output live.

Launch (from the repo root):
    uv run python gui/server.py
then open http://127.0.0.1:8756 in a browser. Nothing is exposed outside
localhost. Stop with Ctrl+C.

Why a local server at all: the pipeline runs RawTherapee + rawpy and reads local
RAW files — a sandboxed browser page can't do that. This server is the small
bridge. See docs/ideas.md.
"""

import asyncio
import os
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from ruamel.yaml import YAML

REPO = Path(__file__).parent.parent
CONFIG_PATH = REPO / "config.yaml"
STATIC = Path(__file__).parent / "static"

sys.path.insert(0, str(Path(__file__).parent))
import schema  # noqa: E402  (local single-source schema)

yaml = YAML()               # round-trip loader — preserves comments & formatting
yaml.preserve_quotes = True

app = FastAPI(title="derawtisation_pipe GUI")


# ── config helpers ───────────────────────────────────────────────────────────

def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.load(f)


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f)


def get_by_path(cfg, dotted):
    """Read a dotted config path, or None if any segment is missing."""
    node = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def set_by_path(cfg, dotted, value):
    """Write a dotted config path in place (parents must already exist)."""
    parts = dotted.split(".")
    node = cfg
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value


def cast_value(field, value):
    """Coerce an incoming JSON value to the field's declared type."""
    t = field["type"]
    if t == "int":
        return int(value)
    if t == "number":
        return float(value)
    if t == "bool":
        return bool(value)
    return str(value)


FIELD_BY_KEY = {f["key"]: f for f in schema.FIELDS}


# ── API ──────────────────────────────────────────────────────────────────────

@app.get("/api/schema")
def api_schema():
    return {"fields": schema.FIELDS, "group_order": schema.GROUP_ORDER,
            "steps": schema.STEPS}


@app.get("/api/config")
def api_config_get():
    cfg = load_config()
    return {f["key"]: get_by_path(cfg, f["key"]) for f in schema.FIELDS}


class ConfigUpdate(BaseModel):
    updates: dict  # {dotted_key: value}


@app.put("/api/config")
def api_config_put(payload: ConfigUpdate):
    cfg = load_config()
    applied = {}
    for key, value in payload.updates.items():
        field = FIELD_BY_KEY.get(key)
        if field is None:
            continue                       # ignore unknown keys defensively
        set_by_path(cfg, key, cast_value(field, value))
        applied[key] = get_by_path(cfg, key)
    save_config(cfg)
    return {"saved": True, "applied": applied}


@app.get("/api/runs")
def api_runs():
    cfg = load_config()
    base = Path(cfg["output_dir"])
    runs = []
    if base.exists():
        for d in sorted(base.iterdir(), reverse=True):
            if not d.is_dir():
                continue
            done = [step for step, name in schema.RUN_ARTIFACTS if (d / name).exists()]
            n_jpeg = len(list((d / "export_jpegs").glob("*.jpg"))) if (d / "export_jpegs").exists() else 0
            runs.append({"run_id": d.name, "steps_done": done, "n_jpeg": n_jpeg})
    return {"output_dir": str(base), "runs": runs}


STEP_BY_ID = {s["id"]: s for s in schema.STEPS}


@app.get("/api/run/{step_id}")
async def api_run(step_id: str, run_id: str = ""):
    """Run one pipeline step as a subprocess, streaming its stdout as SSE."""
    step = STEP_BY_ID.get(step_id)
    if step is None:
        return JSONResponse({"error": f"unknown step {step_id}"}, status_code=404)

    cmd = ["uv", "run", "python", step["script"]]
    if run_id and step.get("needs_run_id", False):
        cmd.append(run_id)

    async def stream():
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        yield f"data: $ {' '.join(cmd)}\n\n"
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(REPO), env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            # SSE frames are newline-delimited; send each console line as one event.
            yield f"data: {line}\n\n"
        await proc.wait()
        yield f"event: done\ndata: exit {proc.returncode}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ── static frontend ──────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text(encoding="utf-8")


if STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


if __name__ == "__main__":
    import uvicorn
    print("derawtisation_pipe GUI -> http://127.0.0.1:8756  (Ctrl+C to stop)")
    uvicorn.run(app, host="127.0.0.1", port=8756, log_level="warning")
