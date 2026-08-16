from __future__ import annotations

import json
import os
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .openai_client import ModelClient
from .store import Store


SEMANTIC_COMPONENTS = {
    "entry": "entry",
    "root": "root",
    "execute": "execute",
    "task_evaluate": "task_evaluate",
    "consolidate_episode": "consolidate_episode",
    "learn": "learn",
}


@dataclass
class Engine:
    root: Path

    def __post_init__(self) -> None:
        self.store = Store(self.root)
        self.model = ModelClient(self.root)

    def environment(self) -> dict[str, Any]:
        try:
            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.root, text=True).strip()
        except Exception:
            commit = None
        return {
            "model": self.model.model,
            "python": platform.python_version(),
            "os": platform.platform(),
            "repository_commit": commit,
            "runner": "python-engine-v1",
        }

    def start(self, request: str) -> str:
        run_id = self.store.new_id("run")
        rd = self.store.run_dir(run_id)
        rd.mkdir(parents=True, exist_ok=True)
        (rd / "request.md").write_text(request, encoding="utf-8")
        self.store.atomic_json(rd / "snapshot.json", {
            "run_id": run_id,
            "status": "continue",
            "phase": "entry_pending",
            "revision": 0,
            "environment": self.environment(),
        })
        self.store.append_event(run_id, {"type": "run_started", "run_id": run_id})
        self.resume(run_id)
        return run_id

    def _save_component_output(self, run_id: str, component: str, output: dict[str, Any]) -> None:
        rd = self.store.run_dir(run_id)
        idx = len(list((rd / "fragments").glob("*.json"))) if (rd / "fragments").exists() else 0
        fragment = output.get("fragment") or {"component": component, "missing": True}
        fragment.setdefault("environment", self.environment())
        self.store.atomic_json(rd / "fragments" / f"{idx:04d}-{component}.json", fragment)
        if "local_learn" in output:
            self.store.atomic_json(rd / "local-learn" / f"{idx:04d}-{component}.json", output["local_learn"])

    def _preflight(self, run_id: str, target: str, unit: dict[str, Any]) -> dict[str, Any]:
        rd = self.store.run_dir(run_id)
        candidates = self.store.read_json(self.root / ".continual" / "candidates" / "index.json", {"candidates": []})
        payload = {
            "mode": "pre-application",
            "run_id": run_id,
            "target_component": target,
            "execution_unit": unit,
            "candidate_index": candidates,
            "environment": self.environment(),
            "rule": "Evaluate only candidates that can affect this exact upcoming unit.",
        }
        out = self.model.call("candidate_evaluate", payload)
        pid = self.store.new_id("preflight")
        self.store.atomic_json(rd / "preflight" / f"{pid}.json", out)
        self._save_component_output(run_id, "candidate_evaluate", out)
        return out

    def _invoke(self, run_id: str, component: str, payload: dict[str, Any], preflight: bool = True) -> dict[str, Any]:
        selection = self._preflight(run_id, component, payload) if preflight else {"result": {"decision": "USE_ACTIVE"}}
        call_payload = dict(payload)
        call_payload["preflight_selection"] = selection.get("result", selection)
        out = self.model.call(SEMANTIC_COMPONENTS[component], call_payload)
        self._save_component_output(run_id, component, out)
        return out

    def resume(self, run_id: str, max_steps: int = 64) -> None:
        for _ in range(max_steps):
            snap = self.store.snapshot(run_id)
            status = snap.get("status")
            if status in {"finished", "blocked"}:
                return
            phase = snap.get("phase")
            if phase == "entry_pending":
                request = (self.store.run_dir(run_id) / "request.md").read_text(encoding="utf-8")
                out = self._invoke(run_id, "entry", {"request": request})
                self.store.atomic_json(self.store.run_dir(run_id) / "artifacts" / "entry.json", out.get("result", {}))
                snap.update({"phase": "root_pending", "entry_ref": "artifacts/entry.json", "expected_revision": snap["revision"]})
                self.store.write_snapshot(run_id, snap)
                continue
            if phase == "root_pending":
                out = self._invoke(run_id, "root", {"snapshot": snap, "entry": self.store.read_json(self.store.run_dir(run_id) / "artifacts" / "entry.json", {})})
                unit = out.get("result", {})
                unit_id = self.store.new_id("unit")
                unit["unit_id"] = unit_id
                self.store.atomic_json(self.store.run_dir(run_id) / "execution-units" / f"{unit_id}.json", unit)
                next_component = unit.get("component", "execute")
                snap.update({"phase": "unit_pending", "current_unit": unit_id, "current_component": next_component, "expected_revision": snap["revision"]})
                self.store.write_snapshot(run_id, snap)
                continue
            if phase == "unit_pending":
                unit_id = snap["current_unit"]
                unit = self.store.read_json(self.store.run_dir(run_id) / "execution-units" / f"{unit_id}.json", {})
                component = snap.get("current_component", "execute")
                if component not in SEMANTIC_COMPONENTS:
                    component = "execute"
                out = self._invoke(run_id, component, {"snapshot": snap, "execution_unit": unit})
                self.store.atomic_json(self.store.run_dir(run_id) / "artifacts" / f"{unit_id}-result.json", out.get("result", {}))
                result = out.get("result", {})
                if component == "task_evaluate":
                    verdict = result.get("verdict") or result.get("status")
                    if verdict == "PASS":
                        snap.update({"phase": "consolidate_pending", "expected_revision": snap["revision"]})
                    else:
                        snap.update({"phase": "root_pending", "last_evaluation": result, "expected_revision": snap["revision"]})
                elif component == "consolidate_episode":
                    snap.update({"phase": "post_task_learn_pending", "expected_revision": snap["revision"]})
                elif component == "learn":
                    snap.update({"status": "finished", "phase": "finished", "expected_revision": snap["revision"]})
                else:
                    snap.update({"phase": "root_pending", "last_result_ref": f"artifacts/{unit_id}-result.json", "expected_revision": snap["revision"]})
                self.store.write_snapshot(run_id, snap)
                continue
            if phase == "consolidate_pending":
                rd = self.store.run_dir(run_id)
                fragments = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((rd / "fragments").glob("*.json"))]
                out = self._invoke(run_id, "consolidate_episode", {"snapshot": snap, "fragments": fragments})
                episode_id = self.store.new_id("episode")
                ep = self.root / ".continual" / "episodes" / episode_id
                self.store.atomic_json(ep / "episode.json", out.get("result", {}))
                (ep / "relations.jsonl").touch()
                snap.update({"phase": "post_task_learn_pending", "episode_id": episode_id, "expected_revision": snap["revision"]})
                self.store.write_snapshot(run_id, snap)
                continue
            if phase == "post_task_learn_pending":
                episode_id = snap.get("episode_id")
                episode = self.store.read_json(self.root / ".continual" / "episodes" / str(episode_id) / "episode.json", {})
                out = self._invoke(run_id, "learn", {"mode": "post-task", "episode_id": episode_id, "current_episode": episode})
                self.store.atomic_json(self.store.run_dir(run_id) / "artifacts" / "post-task-learn.json", out.get("result", {}))
                snap.update({"status": "finished", "phase": "finished", "expected_revision": snap["revision"]})
                self.store.write_snapshot(run_id, snap)
                return
            raise RuntimeError(f"unknown phase: {phase}")
        raise RuntimeError(f"max_steps exceeded for {run_id}")

    def feedback(self, episode_id: str, text: str) -> None:
        ep = self.root / ".continual" / "episodes" / episode_id
        if not (ep / "episode.json").exists():
            raise FileNotFoundError(episode_id)
        with (ep / "relations.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user_feedback", "text": text}, ensure_ascii=False) + "\n")
