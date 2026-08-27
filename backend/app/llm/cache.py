"""On-disk LLM response cache: one JSON file per key, committed to git.

The cache is what makes the deployed demo runnable without an API key, and what
makes a second pipeline run free and byte-identical. Files are plain JSON on
purpose -- they are reviewable in a diff, which a binary store would not be.

Key = sha256(provider | model | task_name | canonical JSON of the input).
Canonical means sorted keys and no incidental whitespace, so two logically
identical requests always hash the same.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from backend.app import config

CACHE_DIR = config.LLM_CACHE_DIR

_SAFE_TASK = re.compile(r"[^a-z0-9_]+")


def canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def cache_key(provider: str, model: str, task_name: str, payload: object) -> str:
    material = "|".join([provider, model, task_name, canonical_json(payload)])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def path_for(task_name: str, key: str, cache_dir: Path | None = None) -> Path:
    safe_task = _SAFE_TASK.sub("_", task_name.lower()) or "task"
    return (cache_dir or CACHE_DIR) / safe_task / f"{key}.json"


def load(task_name: str, key: str, cache_dir: Path | None = None) -> dict | None:
    """Return the cached response, or None. Never touches the network."""
    path = path_for(task_name, key, cache_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))["response"]
    except (json.JSONDecodeError, KeyError):
        # A corrupt cache file is a miss, not a crash. It will be rewritten.
        return None


def store(task_name: str, key: str, *, provider: str, model: str,
          request: object, response: dict, cache_dir: Path | None = None) -> Path:
    path = path_for(task_name, key, cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "key": key,
                "provider": provider,
                "model": model,
                "task": task_name,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "request": request,
                "response": response,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def providers_present(task_name: str, cache_dir: Path | None = None,
                      sample: int = 40) -> set[str]:
    """Which provider/model pairs actually built this task's cache.

    Used to explain an offline miss. The cache key includes the provider, so a
    cache built by one provider is invisible to another -- and the resulting
    "zero results" is indistinguishable from "nothing to do" unless somebody
    says out loud what is in the directory.
    """
    root = (cache_dir or CACHE_DIR) / _SAFE_TASK.sub("_", task_name.lower())
    if not root.exists():
        return set()
    found: set[str] = set()
    for path in list(root.glob("*.json"))[:sample]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if payload.get("provider"):
            found.add(f"{payload['provider']}/{payload.get('model', '?')}")
    return found


def stats(cache_dir: Path | None = None) -> dict[str, int]:
    root = cache_dir or CACHE_DIR
    if not root.exists():
        return {}
    return {
        task.name: len(list(task.glob("*.json")))
        for task in sorted(root.iterdir())
        if task.is_dir()
    }
