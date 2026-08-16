from __future__ import annotations

import json
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
        self.root = self.root.resolve()
        self.store = Store(self.root)
        self.model = ModelClient(self.root)

    @property
    def candidate_index_path(self) -> Path:
        return self.root / ".continual" / "candidates" / "index.json"

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
        # Runner selection is recorded at the run boundary. The running Python
        # process itself is never hot-swapped mid-run.
        runner_eval = self._preflight(run_id, "runner", {"component": "runner", "scope": "new-run"})
        self.store.atomic_json(rd / "preflight" / "runner.json", runner_eval)
        self.resume(run_id)
        return run_id

    def _index(self) -> dict[str, Any]:
        return self.store.read_json(self.candidate_index_path, {"schema_version": 1, "candidates": []})

    def _write_index(self, index: dict[str, Any]) -> None:
        self.store.atomic_json(self.candidate_index_path, index)

    def _register_candidates(self, source: Any, source_ref: str) -> None:
        if not isinstance(source, dict):
            return
        proposals = source.get("candidates") or source.get("candidate_proposals") or []
        if isinstance(proposals, dict):
            proposals = [proposals]
        if not isinstance(proposals, list):
            return
        index = self._index()
        known = {c.get("candidate_id") for c in index.get("candidates", [])}
        changed = False
        for proposal in proposals:
            if not isinstance(proposal, dict):
                continue
            cid = proposal.get("candidate_id") or self.store.new_id("candidate")
            if cid in known:
                continue
            proposal["candidate_id"] = cid
            proposal.setdefault("status", "candidate")
            proposal.setdefault("scope_states", {})
            proposal.setdefault("supporting_evidence", [])
            proposal.setdefault("contradictory_evidence", [])
            proposal.setdefault("rejected_reasons", [])
            proposal.setdefault("depends_on", [])
            proposal.setdefault("conflicts_with", [])
            proposal.setdefault("tested_with", [])
            proposal.setdefault("incompatible_with", [])
            proposal.setdefault("source_refs", []).append(source_ref)
            cdir = self.root / ".continual" / "candidates" / cid
            self.store.atomic_json(cdir / "candidate.json", proposal)
            prompt_content = proposal.get("prompt_content")
            if isinstance(prompt_content, str) and prompt_content.strip():
                (cdir / "prompt.md").write_text(prompt_content, encoding="utf-8")
                proposal["prompt_path"] = str((cdir / "prompt.md").relative_to(self.root))
                self.store.atomic_json(cdir / "candidate.json", proposal)
            index.setdefault("candidates", []).append({
                "candidate_id": cid,
                "target_component": proposal.get("target_component"),
                "expected_scope": proposal.get("expected_scope"),
                "status": proposal.get("status", "candidate"),
                "prompt_path": proposal.get("prompt_path"),
                "depends_on": proposal.get("depends_on", []),
                "conflicts_with": proposal.get("conflicts_with", []),
            })
            known.add(cid)
            changed = True
        if changed:
            self._write_index(index)

    def _save_component_output(self, run_id: str, component: str, output: dict[str, Any]) -> str:
        rd = self.store.run_dir(run_id)
        idx = len(list((rd / "fragments").glob("*.json"))) if (rd / "fragments").exists() else 0
        fragment = output.get("fragment") or {"component": component, "missing": True}
        fragment.setdefault("environment", self.environment())
        fpath = rd / "fragments" / f"{idx:04d}-{component}.json"
        self.store.atomic_json(fpath, fragment)
        if "local_learn" in output:
            lpath = rd / "local-learn" / f"{idx:04d}-{component}.json"
            self.store.atomic_json(lpath, output["local_learn"])
            self._register_candidates(output["local_learn"], str(lpath.relative_to(self.root)))
        self._register_candidates(output.get("result"), str(fpath.relative_to(self.root)))
        return str(fpath.relative_to(self.root))

    def _candidate_by_id(self, candidate_id: str) -> dict[str, Any] | None:
        path = self.root / ".continual" / "candidates" / candidate_id / "candidate.json"
        return self.store.read_json(path) if path.exists() else None

    def _preflight(self, run_id: str, target: str, unit: dict[str, Any]) -> dict[str, Any]:
        rd = self.store.run_dir(run_id)
        payload = {
            "mode": "pre-application",
            "run_id": run_id,
            "target_component": target,
            "execution_unit": unit,
            "candidate_index": self._index(),
            "environment": self.environment(),
            "rule": "Evaluate only candidates that can affect this exact upcoming unit.",
        }
        # Bootstrap exception: current evaluator is invoked directly to avoid
        # infinite evaluator-before-evaluator recursion.
        out = self.model.call("candidate_evaluate", payload)
        pid = self.store.new_id("preflight")
        self.store.atomic_json(rd / "preflight" / f"{pid}.json", out)
        self._save_component_output(run_id, "candidate_evaluate", out)
        return out

    def _selected_candidate(self, selection: dict[str, Any]) -> dict[str, Any] | None:
        result = selection.get("result", selection)
        if not isinstance(result, dict):
            return None
        decision = result.get("decision")
        if decision not in {"TRIAL_CANDIDATE", "USE_CANDIDATE", "ACTIVE_FOR_SCOPE"}:
            return None
        cid = result.get("candidate_id") or result.get("selected_candidate_id")
        return self._candidate_by_id(cid) if isinstance(cid, str) else None

    def _apply_scope_update(self, post: dict[str, Any]) -> None:
        result = post.get("result", post)
        if not isinstance(result, dict):
            return
        cid = result.get("candidate_id")
        scope = result.get("scope")
        state = result.get("scope_state") or result.get("decision")
        if not all(isinstance(x, str) and x for x in (cid, scope, state)):
            return
        candidate = self._candidate_by_id(cid)
        if not candidate:
            return
        candidate.setdefault("scope_states", {})[scope] = state
        evidence = result.get("evidence")
        if evidence:
            bucket = "contradictory_evidence" if state in {"REJECTED_FOR_SCOPE", "candidate", "REMAIN_CANDIDATE"} else "supporting_evidence"
            candidate.setdefault(bucket, []).append({"scope": scope, "evidence": evidence})
        self.store.atomic_json(self.root / ".continual" / "candidates" / cid / "candidate.json", candidate)
        index = self._index()
        for item in index.get("candidates", []):
            if item.get("candidate_id") == cid:
                item.setdefault("scope_states", {})[scope] = state
        self._write_index(index)

    def _postflight(self, run_id: str, target: str, unit: dict[str, Any], selection: dict[str, Any], result: dict[str, Any]) -> dict[str, Any] | None:
        selected = self._selected_candidate(selection)
        if not selected:
            return None
        payload = {
            "mode": "post-result",
            "run_id": run_id,
            "target_component": target,
            "execution_unit": unit,
            "pre_application": selection.get("result", selection),
            "candidate": selected,
            "actual_result": result,
            "environment": self.environment(),
        }
        out = self.model.call("candidate_evaluate", payload)
        pid = self.store.new_id("postresult")
        self.store.atomic_json(self.store.run_dir(run_id) / "candidate-trials" / f"{pid}.json", out)
        self._save_component_output(run_id, "candidate_evaluate", out)
        self._apply_scope_update(out)
        return out

    def _invoke(self, run_id: str, component: str, payload: dict[str, Any], preflight: bool = True) -> dict[str, Any]:
        selection = self._preflight(run_id, component, payload) if preflight else {"result": {"decision": "USE_ACTIVE"}}
        selected = self._selected_candidate(selection)
        prompt_path = selected.get("prompt_path") if selected else None
        call_payload = dict(payload)
        call_payload["preflight_selection"] = selection.get("result", selection)
        out = self.model.call(SEMANTIC_COMPONENTS[component], call_payload, prompt_path=prompt_path)
        self._save_component_output(run_id, component, out)
        self._postflight(run_id, component, payload, selection, out.get("result", {}))
        return out

    def resume(self, run_id: str, max_steps: int = 64) -> None:
        for _ in range(max_steps):
            snap = self.store.snapshot(run_id)
            if snap.get("status") in {"finished", "blocked"}:
                return
            phase = snap.get("phase")
            rd = self.store.run_dir(run_id)

            if phase == "entry_pending":
                request = (rd / "request.md").read_text(encoding="utf-8")
                out = self._invoke(run_id, "entry", {"request": request})
                self.store.atomic_json(rd / "artifacts" / "entry.json", out.get("result", {}))
                snap.update({"phase": "root_pending", "entry_ref": "artifacts/entry.json", "expected_revision": snap["revision"]})
                self.store.write_snapshot(run_id, snap)
                continue

            if phase == "root_pending":
                out = self._invoke(run_id, "root", {
                    "snapshot": snap,
                    "entry": self.store.read_json(rd / "artifacts" / "entry.json", {}),
                    "last_result": self.store.read_json(rd / snap.get("last_result_ref", "missing"), {}) if snap.get("last_result_ref") else None,
                })
                unit = out.get("result", {})
                unit_id = self.store.new_id("unit")
                unit["unit_id"] = unit_id
                self.store.atomic_json(rd / "execution-units" / f"{unit_id}.json", unit)
                next_component = unit.get("component", "execute")
                snap.update({"phase": "unit_pending", "current_unit": unit_id, "current_component": next_component, "expected_revision": snap["revision"]})
                self.store.write_snapshot(run_id, snap)
                continue

            if phase == "unit_pending":
                unit_id = snap["current_unit"]
                unit = self.store.read_json(rd / "execution-units" / f"{unit_id}.json", {})
                component = snap.get("current_component", "execute")
                if component not in SEMANTIC_COMPONENTS:
                    component = "execute"
                out = self._invoke(run_id, component, {"snapshot": snap, "execution_unit": unit})
                self.store.atomic_json(rd / "artifacts" / f"{unit_id}-result.json", out.get("result", {}))
                result = out.get("result", {})
                if component == "task_evaluate":
                    verdict = result.get("verdict") or result.get("status")
                    if verdict == "PASS":
                        snap.update({"phase": "consolidate_pending", "expected_revision": snap["revision"]})
                    else:
                        snap.update({"phase": "root_pending", "last_evaluation": result, "expected_revision": snap["revision"]})
                else:
                    snap.update({"phase": "root_pending", "last_result_ref": f"artifacts/{unit_id}-result.json", "expected_revision": snap["revision"]})
                self.store.write_snapshot(run_id, snap)
                continue

            if phase == "consolidate_pending":
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
                apath = rd / "artifacts" / "post-task-learn.json"
                self.store.atomic_json(apath, out.get("result", {}))
                self._register_candidates(out.get("result"), str(apath.relative_to(self.root)))
                ep_rel = self.root / ".continual" / "episodes" / str(episode_id) / "relations.jsonl"
                with ep_rel.open("a", encoding="utf-8") as f:
                    for c in (out.get("result", {}) or {}).get("candidates", []):
                        if isinstance(c, dict) and c.get("candidate_id"):
                            f.write(json.dumps({"type": "produced_candidate", "candidate_id": c["candidate_id"]}, ensure_ascii=False) + "\n")
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
