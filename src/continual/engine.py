from __future__ import annotations

import json
import platform
import re
import subprocess
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import ContractError, validate_component_output
from .matched_evidence_acquisition import (
    decide_matched_evidence_acquisition,
    matched_evidence_authority,
    matched_evidence_source_clock,
)
from .openai_client import ModelClient
from .store import Store
from .work_session import WorkModelPending


class WorkProviderMismatch(RuntimeError):
    """Raised when a non-Work provider is asked to complete an invocation whose
    journal is frozen in ``awaiting_work_model``. Only a Work-model client may
    answer such a request; any other provider must not fabricate the response."""


SEMANTIC_COMPONENTS = {
    "entry": "entry",
    "root": "root",
    "execute": "execute",
    "task_evaluate": "task_evaluate",
    "consolidate_episode": "consolidate_episode",
    "learn": "learn",
}
TERMINAL_STATUSES = {"finished", "blocked", "cancelled"}
_ACTIVE_DEFAULTS = {
    "preflight_evaluator": "prompts/candidate_evaluate.md",
    "entry": "prompts/entry.md",
    "root": "prompts/root.md",
    "execute": "prompts/execute.md",
    "task_evaluate": "prompts/task_evaluate.md",
    "consolidate_episode": "prompts/consolidate_episode.md",
    "learn": "prompts/learn.md",
    "runner": "python-engine-v2",
}
_CANDIDATE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
# A model-proposed unit_id is used as a filesystem path component; keep it to a
# strict, separator-free allowlist so it can never traverse out of the run
# directory into protected control files.
_SAFE_UNIT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_LIST_FIELDS = {
    "supporting_evidence",
    "contradictory_evidence",
    "rejected_reasons",
    "depends_on",
    "conflicts_with",
    "tested_with",
    "incompatible_with",
    "supersedes",
    "source_refs",
    "applicability",
    "non_applicability",
}


@dataclass
class Engine:
    root: Path
    model: Any | None = None

    def __post_init__(self) -> None:
        self.root = self.root.resolve()
        self.store = Store(self.root)
        if self.model is None:
            self.model = ModelClient(self.root)

    @property
    def candidate_index_path(self) -> Path:
        return self.root / ".continual" / "candidates" / "index.json"

    @property
    def active_components_path(self) -> Path:
        return self.root / ".continual" / "system" / "active-components.json"

    def _active_components(self) -> dict[str, Any]:
        value = self.store.read_json(self.active_components_path, {})
        if not isinstance(value, dict):
            value = {}
        merged = dict(_ACTIVE_DEFAULTS)
        merged.update(value)
        return merged

    def _component_prompt_path(self, component: str) -> str:
        active = self._active_components()
        key = "preflight_evaluator" if component == "candidate_evaluate" else component
        raw = active.get(key) or _ACTIVE_DEFAULTS[key]
        if not isinstance(raw, str) or not raw:
            raise ValueError(f"active component path for {component} must be a string")
        path = (self.root / raw).resolve()
        if path != self.root and self.root not in path.parents:
            raise ValueError(f"active component path escapes repository: {raw}")
        if not path.is_file():
            raise FileNotFoundError(raw)
        return path.relative_to(self.root).as_posix()

    def environment(self) -> dict[str, Any]:
        try:
            commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=self.root,
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except Exception:
            commit = None
        active = self._active_components()
        return {
            "model": getattr(self.model, "model", None),
            "python": platform.python_version(),
            "os": platform.platform(),
            "repository_commit": commit,
            "runner": active.get("runner", "python-engine-v2"),
            "active_components_digest": self.store.stable_digest(active),
        }

    def _run_environment(self, run_id: str) -> dict[str, Any]:
        """Return the environment frozen in the run snapshot at ``start`` time.

        Preflight/postflight identities are digests over this value. Using the
        run's frozen environment (rather than a freshly sampled live one, whose
        ``repository_commit`` moves whenever HEAD advances) keeps every
        candidate-evaluate invocation id stable across process restarts and
        commits, so a resumed run replays its frozen Work request instead of
        minting a new one and re-calling the model.
        """
        snapshot = self.store.read_json(self.store.run_dir(run_id) / "snapshot.json", {})
        if isinstance(snapshot, dict):
            environment = snapshot.get("environment")
            if isinstance(environment, dict):
                return environment
        return self.environment()

    def start(
        self,
        request: str,
        *,
        max_steps: int = 64,
        run_id: str | None = None,
    ) -> str:
        if not isinstance(request, str) or not request.strip():
            raise ValueError("request must be a non-empty string")
        run_id = run_id or self.store.new_id("run")
        if not re.fullmatch(r"run-[A-Za-z0-9._-]{6,128}", run_id):
            raise ValueError("invalid run_id")
        rd = self.store.run_dir(run_id)
        rd.mkdir(parents=True, exist_ok=False)
        self.store.atomic_text(rd / "request.md", request)
        self.store.atomic_json(
            rd / "snapshot.json",
            {
                "run_id": run_id,
                "status": "continue",
                "phase": "entry_pending",
                "revision": 0,
                "created_at": self.store.utc_now(),
                "environment": self.environment(),
                "continuation_stack": [],
                "error_count": 0,
            },
        )
        self.store.append_event(run_id, {"type": "run_started"})
        runner_eval = self._preflight(
            run_id,
            "runner",
            {"component": "runner", "scope": "new-run", "request_digest": self.store.stable_digest(request)},
        )
        self.store.atomic_json(rd / "preflight" / "runner.json", runner_eval)
        self.resume(run_id, max_steps=max_steps)
        return run_id

    def _index(self) -> dict[str, Any]:
        value = self.store.read_json(
            self.candidate_index_path,
            {"schema_version": 1, "candidates": []},
        )
        if not isinstance(value, dict):
            raise ValueError("candidate index must be an object")
        if not isinstance(value.get("candidates"), list):
            value["candidates"] = []
        return value

    def _write_index(self, index: dict[str, Any]) -> None:
        value = deepcopy(index)
        value.setdefault("schema_version", 1)
        candidates = [item for item in value.get("candidates", []) if isinstance(item, dict)]
        candidates.sort(key=lambda item: str(item.get("candidate_id", "")))
        value["candidates"] = candidates
        self.store.atomic_json(self.candidate_index_path, value)

    def _candidate_path(self, candidate_id: str) -> Path:
        return self.root / ".continual" / "candidates" / candidate_id / "candidate.json"

    def _candidate_by_id(self, candidate_id: str) -> dict[str, Any] | None:
        if not _CANDIDATE_ID.fullmatch(candidate_id):
            return None
        path = self._candidate_path(candidate_id)
        value = self.store.read_json(path) if path.exists() else None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _dedupe_list(values: list[Any]) -> list[Any]:
        seen: set[str] = set()
        result: list[Any] = []
        for value in values:
            key = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
            if key in seen:
                continue
            seen.add(key)
            result.append(value)
        return result

    def _candidate_id_for(self, proposal: dict[str, Any], source_ref: str) -> str:
        requested = proposal.get("candidate_id")
        if isinstance(requested, str) and _CANDIDATE_ID.fullmatch(requested):
            return requested
        digest = self.store.stable_digest(
            {
                "target": proposal.get("target_component"),
                "scope": proposal.get("expected_scope"),
                "change": proposal.get("what_it_changes"),
                "prompt": proposal.get("prompt_content"),
                "source": source_ref,
            },
            length=20,
        )
        return f"candidate-{digest}"

    def _sync_index_candidate(self, index: dict[str, Any], candidate: dict[str, Any]) -> None:
        candidate_id = candidate["candidate_id"]
        item = None
        for existing in index.setdefault("candidates", []):
            if isinstance(existing, dict) and existing.get("candidate_id") == candidate_id:
                item = existing
                break
        if item is None:
            item = {"candidate_id": candidate_id}
            index["candidates"].append(item)
        for key in (
            "target_component",
            "expected_scope",
            "status",
            "prompt_path",
            "prompt_mode",
            "depends_on",
            "conflicts_with",
            "scope_states",
        ):
            if key in candidate:
                item[key] = deepcopy(candidate[key])

    def _register_candidates(self, source: Any, source_ref: str) -> list[str]:
        if not isinstance(source, dict):
            return []
        proposals = source.get("candidates") or source.get("candidate_proposals") or []
        if isinstance(proposals, dict):
            proposals = [proposals]
        if not isinstance(proposals, list):
            return []

        index = self._index()
        registered: list[str] = []
        changed = False
        for raw in proposals:
            if not isinstance(raw, dict):
                continue
            proposal = deepcopy(raw)
            target = proposal.get("target_component")
            if not isinstance(target, str) or not target:
                continue
            candidate_id = self._candidate_id_for(proposal, source_ref)
            proposal["candidate_id"] = candidate_id
            existing = self._candidate_by_id(candidate_id)
            if existing is None:
                candidate = proposal
                candidate.setdefault("status", "candidate")
                candidate.setdefault("prompt_mode", "overlay")
                candidate.setdefault("scope_states", {})
                for key in _LIST_FIELDS:
                    candidate.setdefault(key, [])
            else:
                candidate = deepcopy(existing)
                for key, value in proposal.items():
                    if key in _LIST_FIELDS:
                        incoming = value if isinstance(value, list) else [value]
                        current = candidate.get(key, [])
                        if not isinstance(current, list):
                            current = [current]
                        candidate[key] = self._dedupe_list([*current, *incoming])
                    elif key == "scope_states" and isinstance(value, dict):
                        current_states = candidate.setdefault("scope_states", {})
                        for scope, state in value.items():
                            current_states.setdefault(scope, state)
                    elif key == "prompt_content":
                        continue
                    elif key not in candidate or candidate[key] in (None, "", [], {}):
                        candidate[key] = value

            refs = candidate.setdefault("source_refs", [])
            if source_ref not in refs:
                refs.append(source_ref)

            cdir = self.root / ".continual" / "candidates" / candidate_id
            prompt_content = proposal.get("prompt_content")
            if isinstance(prompt_content, str) and prompt_content.strip():
                prompt_path = cdir / "prompt.md"
                if not prompt_path.exists():
                    self.store.atomic_text(prompt_path, prompt_content.rstrip() + "\n")
                    candidate["prompt_path"] = prompt_path.relative_to(self.root).as_posix()
                else:
                    current_prompt = prompt_path.read_text(encoding="utf-8")
                    if current_prompt.strip() != prompt_content.strip():
                        alternatives = candidate.setdefault("alternative_prompt_hashes", [])
                        prompt_hash = self.store.stable_digest(prompt_content)
                        if prompt_hash not in alternatives:
                            alternatives.append(prompt_hash)

            self.store.atomic_json(cdir / "candidate.json", candidate)
            self._sync_index_candidate(index, candidate)
            registered.append(candidate_id)
            changed = True

        if changed:
            self._write_index(index)
        return registered

    def _candidate_index_for(self, target: str) -> dict[str, Any]:
        full = self._index()
        items = [item for item in full.get("candidates", []) if isinstance(item, dict)]
        by_id = {item.get("candidate_id"): item for item in items if isinstance(item.get("candidate_id"), str)}
        selected: dict[str, dict[str, Any]] = {}
        pending: list[str] = []
        for item in items:
            if item.get("target_component") in {target, "*"}:
                candidate_id = item.get("candidate_id")
                if isinstance(candidate_id, str):
                    selected[candidate_id] = item
                    pending.extend(x for x in item.get("depends_on", []) if isinstance(x, str))
        while pending:
            dependency = pending.pop()
            if dependency in selected:
                continue
            item = by_id.get(dependency)
            if item is None:
                continue
            selected[dependency] = item
            pending.extend(x for x in item.get("depends_on", []) if isinstance(x, str))
        return {
            "schema_version": full.get("schema_version", 1),
            "candidates": [deepcopy(selected[key]) for key in sorted(selected)],
        }

    def _invocation_path(self, run_id: str, invocation_id: str) -> Path:
        return self.store.run_dir(run_id) / "invocations" / f"{invocation_id}.json"

    def _call_component_direct(
        self,
        run_id: str,
        component: str,
        payload: dict[str, Any],
        *,
        prompt_path: str,
        evaluator_mode: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        invocation_id = f"invoke-{self.store.stable_digest({'component': component, 'prompt': prompt_path, 'payload': payload})}"
        path = self._invocation_path(run_id, invocation_id)
        journal = self.store.read_json(path, {})
        if isinstance(journal, dict) and journal.get("status") == "complete":
            output = journal.get("output")
            validate_component_output(component, output, evaluator_mode=evaluator_mode)
            self.store.append_event(
                run_id,
                {"type": "invocation_reused", "component": component, "invocation_id": invocation_id},
            )
            return output, invocation_id

        output = journal.get("output") if isinstance(journal, dict) and journal.get("status") == "output_ready" else None
        if isinstance(journal, dict) and journal.get("status") == "awaiting_work_model":
            if not getattr(self.model, "provides_work_responses", False):
                raise WorkProviderMismatch(
                    f"invocation {invocation_id} for run {run_id} is awaiting a Work-model "
                    "response; refusing to complete it with a non-Work provider"
                )
            attempt = int(journal.get("attempt", 1))
        else:
            attempt = int(journal.get("attempt", 0)) + 1 if isinstance(journal, dict) else 1
        if output is None:
            bound_resume = (
                isinstance(journal, dict)
                and journal.get("status") == "awaiting_work_model"
                and callable(getattr(self.model, "resume_bound", None))
            )
            preflight_call = getattr(self.model, "preflight_call", None)
            if not bound_resume and callable(preflight_call):
                preflight_call(component, payload, prompt_path=prompt_path)
            self.store.atomic_json(
                path,
                {
                    "invocation_id": invocation_id,
                    "component": component,
                    "prompt_path": prompt_path,
                    "payload_digest": self.store.stable_digest(payload),
                    "status": "started",
                    "attempt": attempt,
                    "started_at": self.store.utc_now(),
                },
            )
            try:
                if bound_resume:
                    output = self.model.resume_bound(
                        component,
                        payload,
                        prompt_path=prompt_path,
                        invocation_id=journal.get("work_invocation_id"),
                        request_ref=journal.get("work_request_ref"),
                        request_digest=journal.get("work_request_digest"),
                    )
                else:
                    output = self.model.call(component, payload, prompt_path=prompt_path)
                validate_component_output(component, output, evaluator_mode=evaluator_mode)
            except WorkModelPending as pending:
                self.store.atomic_json(
                    path,
                    {
                        "invocation_id": invocation_id,
                        "component": component,
                        "prompt_path": prompt_path,
                        "payload_digest": self.store.stable_digest(payload),
                        "status": "awaiting_work_model",
                        "attempt": attempt,
                        "work_invocation_id": pending.invocation_id,
                        "work_request_ref": pending.request_ref,
                        "work_request_digest": pending.request_digest,
                        "started_at": (
                            journal.get("started_at")
                            if isinstance(journal, dict)
                            else self.store.utc_now()
                        ),
                    },
                )
                if not isinstance(journal, dict) or journal.get("status") != "awaiting_work_model":
                    self.store.append_event(
                        run_id,
                        {
                            "type": "work_model_pending",
                            "component": component,
                            "invocation_id": invocation_id,
                            "work_invocation_id": pending.invocation_id,
                        },
                    )
                raise
            except ContractError as first_error:
                repair_payload = dict(payload)
                repair_payload["contract_repair"] = {
                    "errors": list(first_error.errors),
                    "previous_output": output,
                    "instruction": "Return a corrected complete JSON object only; do not omit successful content.",
                }
                try:
                    output = self.model.call(component, repair_payload, prompt_path=prompt_path)
                    validate_component_output(component, output, evaluator_mode=evaluator_mode)
                except Exception as exc:
                    self.store.atomic_json(
                        path,
                        {
                            "invocation_id": invocation_id,
                            "component": component,
                            "prompt_path": prompt_path,
                            "payload_digest": self.store.stable_digest(payload),
                            "status": "failed",
                            "attempt": attempt,
                            "error": {"type": type(exc).__name__, "message": str(exc)},
                            "failed_at": self.store.utc_now(),
                        },
                    )
                    raise
            except Exception as exc:
                self.store.atomic_json(
                    path,
                    {
                        "invocation_id": invocation_id,
                        "component": component,
                        "prompt_path": prompt_path,
                        "payload_digest": self.store.stable_digest(payload),
                        "status": "failed",
                        "attempt": attempt,
                        "error": {"type": type(exc).__name__, "message": str(exc)},
                        "failed_at": self.store.utc_now(),
                    },
                )
                raise
            self.store.atomic_json(
                path,
                {
                    "invocation_id": invocation_id,
                    "component": component,
                    "prompt_path": prompt_path,
                    "payload_digest": self.store.stable_digest(payload),
                    "status": "output_ready",
                    "attempt": attempt,
                    "output": output,
                    "output_at": self.store.utc_now(),
                },
            )

        validate_component_output(component, output, evaluator_mode=evaluator_mode)
        fragment_ref = self._save_component_output(run_id, component, output, invocation_id)
        self.store.atomic_json(
            path,
            {
                "invocation_id": invocation_id,
                "component": component,
                "prompt_path": prompt_path,
                "payload_digest": self.store.stable_digest(payload),
                "status": "complete",
                "attempt": attempt,
                "output": output,
                "fragment_ref": fragment_ref,
                "completed_at": self.store.utc_now(),
            },
        )
        self.store.append_event(
            run_id,
            {"type": "invocation_completed", "component": component, "invocation_id": invocation_id},
        )
        return output, invocation_id

    def _save_component_output(
        self,
        run_id: str,
        component: str,
        output: dict[str, Any],
        invocation_id: str,
    ) -> str:
        rd = self.store.run_dir(run_id)
        fragment = deepcopy(output.get("fragment") or {"component": component, "missing": True})
        fragment.setdefault("component", component)
        fragment.setdefault("environment", self.environment())
        fragment.setdefault("invocation_id", invocation_id)
        fpath = rd / "fragments" / f"{invocation_id}-{component}.json"
        self.store.atomic_json(fpath, fragment)
        source_ref = fpath.relative_to(self.root).as_posix()
        if component != "learn" and "local_learn" in output:
            lpath = rd / "local-learn" / f"{invocation_id}-{component}.json"
            self.store.atomic_json(lpath, output["local_learn"])
            self._register_candidates(output["local_learn"], lpath.relative_to(self.root).as_posix())
        self._register_candidates(output.get("result"), source_ref)
        return source_ref

    def _preflight(self, run_id: str, target: str, unit: dict[str, Any]) -> dict[str, Any]:
        rd = self.store.run_dir(run_id)
        relevant = self._candidate_index_for(target)
        payload = {
            "mode": "pre-application",
            "run_id": run_id,
            "target_component": target,
            "execution_unit": unit,
            "candidate_index": relevant,
            "environment": self._run_environment(run_id),
            "rule": "Evaluate only candidates that can affect this exact upcoming unit.",
        }
        preflight_id = f"preflight-{target}-{self.store.stable_digest(payload)}"
        path = rd / "preflight" / f"{preflight_id}.json"
        existing = self.store.read_json(path, {})
        if isinstance(existing, dict) and existing.get("result"):
            return existing

        if not relevant["candidates"]:
            output = {
                "result": {
                    "decision": "USE_ACTIVE",
                    "scope": unit.get("scope") or target,
                    "reason": "No candidate targets this component.",
                },
                "local_learn": {"decision": "NO_CHANGE", "candidates": []},
                "fragment": {
                    "component": "candidate_evaluate",
                    "purpose": "mechanical no-candidate preflight",
                    "observations": ["No relevant candidates were indexed."],
                },
            }
        else:
            output, _ = self._call_component_direct(
                run_id,
                "candidate_evaluate",
                payload,
                prompt_path=self._component_prompt_path("candidate_evaluate"),
                evaluator_mode="pre-application",
            )
        self.store.atomic_json(path, output)
        return output

    def _selected_candidate(self, selection: dict[str, Any], target: str) -> dict[str, Any] | None:
        result = selection.get("result", selection)
        if not isinstance(result, dict):
            return None
        decision = result.get("decision")
        if decision not in {"TRIAL_CANDIDATE", "USE_CANDIDATE", "ACTIVE_FOR_SCOPE"}:
            return None
        candidate_id = result.get("candidate_id") or result.get("selected_candidate_id")
        if not isinstance(candidate_id, str):
            return None
        candidate = self._candidate_by_id(candidate_id)
        if not candidate or candidate.get("target_component") not in {target, "*"}:
            return None
        prompt_path = candidate.get("prompt_path")
        if not isinstance(prompt_path, str):
            return None
        resolved = (self.root / prompt_path).resolve()
        if resolved != self.root and self.root not in resolved.parents:
            return None
        if not resolved.is_file():
            return None
        return candidate

    def _apply_scope_update(self, post: dict[str, Any]) -> None:
        result = post.get("result", post)
        if not isinstance(result, dict):
            return
        candidate_id = result.get("candidate_id")
        scope = result.get("scope")
        state = result.get("scope_state") or result.get("decision")
        if not all(isinstance(value, str) and value for value in (candidate_id, scope, state)):
            return
        candidate = self._candidate_by_id(candidate_id)
        if not candidate:
            return
        candidate.setdefault("scope_states", {})[scope] = state
        if state in {"ACTIVE_FOR_SCOPE", "VERIFIED_FOR_SCOPE"}:
            candidate["status"] = "active-for-scope"
        evidence = result.get("evidence")
        if evidence:
            bucket = (
                "contradictory_evidence"
                if state in {"REJECTED_FOR_SCOPE", "REMAIN_CANDIDATE", "UNCERTAIN"}
                else "supporting_evidence"
            )
            values = candidate.setdefault(bucket, [])
            values.append({"scope": scope, "evidence": evidence, "at": self.store.utc_now()})
            candidate[bucket] = self._dedupe_list(values)
        self.store.atomic_json(self._candidate_path(candidate_id), candidate)
        index = self._index()
        self._sync_index_candidate(index, candidate)
        self._write_index(index)

    def _postflight(
        self,
        run_id: str,
        target: str,
        unit: dict[str, Any],
        selection: dict[str, Any],
        actual_result: dict[str, Any],
    ) -> dict[str, Any] | None:
        selected = self._selected_candidate(selection, target)
        if not selected:
            return None
        payload = {
            "mode": "post-result",
            "run_id": run_id,
            "target_component": target,
            "execution_unit": unit,
            "pre_application": selection.get("result", selection),
            "candidate": selected,
            "actual_result": actual_result,
            "environment": self._run_environment(run_id),
        }
        output, invocation_id = self._call_component_direct(
            run_id,
            "candidate_evaluate",
            payload,
            prompt_path=self._component_prompt_path("candidate_evaluate"),
            evaluator_mode="post-result",
        )
        path = self.store.run_dir(run_id) / "candidate-trials" / f"{invocation_id}.json"
        self.store.atomic_json(path, output)
        self._apply_scope_update(output)
        return output

    def _effective_prompt_path(
        self,
        run_id: str,
        component: str,
        candidate: dict[str, Any] | None,
    ) -> str:
        active_path = self._component_prompt_path(component)
        if candidate is None:
            return active_path
        candidate_path = str(candidate["prompt_path"])
        mode = candidate.get("prompt_mode", "overlay")
        if mode == "replace":
            return candidate_path
        if mode != "overlay":
            raise ValueError(f"unsupported candidate prompt_mode: {mode}")
        active_text = (self.root / active_path).read_text(encoding="utf-8").rstrip()
        candidate_text = (self.root / candidate_path).read_text(encoding="utf-8").strip()
        merged = (
            active_text
            + "\n\n# Scoped Candidate overlay\n\n"
            + f"Candidate ID: {candidate['candidate_id']}\n\n"
            + candidate_text
            + "\n"
        )
        digest = self.store.stable_digest(
            {"active": active_path, "candidate": candidate_path, "content": merged}
        )
        path = (
            self.store.run_dir(run_id)
            / "effective-prompts"
            / f"{component}-{candidate['candidate_id']}-{digest}.md"
        )
        self.store.atomic_text(path, merged)
        return path.relative_to(self.root).as_posix()

    def _invoke(self, run_id: str, component: str, payload: dict[str, Any]) -> dict[str, Any]:
        if component not in SEMANTIC_COMPONENTS:
            raise ValueError(f"unknown semantic component: {component}")
        selection = self._preflight(run_id, component, payload)
        selected = self._selected_candidate(selection, component)
        prompt_path = self._effective_prompt_path(run_id, component, selected)
        call_payload = deepcopy(payload)
        call_payload["preflight_selection"] = selection.get("result", selection)
        call_payload["active_component_path"] = self._component_prompt_path(component)
        if selected is not None:
            call_payload["candidate"] = {
                "candidate_id": selected["candidate_id"],
                "prompt_mode": selected.get("prompt_mode", "overlay"),
                "expected_scope": selected.get("expected_scope"),
            }
        output, _ = self._call_component_direct(
            run_id,
            SEMANTIC_COMPONENTS[component],
            call_payload,
            prompt_path=prompt_path,
        )
        self._postflight(run_id, component, payload, selection, output.get("result", {}))
        return output

    def _advance(self, run_id: str, snapshot: dict[str, Any], **changes: Any) -> dict[str, Any]:
        new_value = deepcopy(snapshot)
        new_value.update(changes)
        new_value["expected_revision"] = snapshot.get("revision", 0)
        new_value.pop("last_error", None)
        new_value.pop("checkpoint_reason", None)
        new_value.pop("pending_work_model", None)
        return self.store.write_snapshot(run_id, new_value)

    def _episode_catalog(self, limit: int = 50) -> list[dict[str, Any]]:
        base = self.root / ".continual" / "episodes"
        if not base.exists():
            return []
        catalog: list[dict[str, Any]] = []
        for path in sorted(base.glob("*/episode.json"), reverse=True)[:limit]:
            episode = self.store.read_json(path, {})
            if not isinstance(episode, dict):
                continue
            catalog.append(
                {
                    "episode_id": path.parent.name,
                    "task": episode.get("task") or episode.get("objective"),
                    "outcome": episode.get("outcome") or episode.get("verdict"),
                    "tags": episode.get("tags", []),
                    "path": path.relative_to(self.root).as_posix(),
                }
            )
        return catalog

    def _schedule_unit(
        self,
        run_id: str,
        snapshot: dict[str, Any],
        unit: dict[str, Any],
        *,
        parent: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        component = unit.get("component", "execute")
        if component not in SEMANTIC_COMPONENTS:
            component = "execute"
        goal = unit.get("goal")
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("execution unit requires a non-empty goal")
        unit = deepcopy(unit)
        unit["component"] = component
        unit_id = unit.get("unit_id")
        # unit_id is model-controlled and becomes a filesystem path component
        # (execution-units/<unit_id>.json, artifacts/<unit_id>-result.json), so a
        # value with separators or ".." could escape the run directory and
        # overwrite protected control files. Accept only a strict allowlist and
        # otherwise derive a safe deterministic id.
        if not isinstance(unit_id, str) or not _SAFE_UNIT_ID.fullmatch(unit_id):
            unit_id = f"unit-{self.store.stable_digest({'revision': snapshot.get('revision'), 'unit': unit})}"
        unit["unit_id"] = unit_id
        self.store.atomic_json(
            self.store.run_dir(run_id) / "execution-units" / f"{unit_id}.json",
            unit,
        )
        stack = list(snapshot.get("continuation_stack", []))
        if parent is not None:
            stack.append(parent)
        return self._advance(
            run_id,
            snapshot,
            phase="unit_pending",
            current_unit=unit_id,
            current_component=component,
            continuation_stack=stack,
        )

    def _guard_root_selection(
        self,
        run_id: str,
        proposed_unit: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Apply the optional typed one-shot matched-evidence guard.

        Ordinary Root output is unchanged unless the latest Learn artifact
        carries the complete schema and every live binding still matches.
        """

        rd = self.store.run_dir(run_id)
        learn_result = self.store.read_json(rd / "artifacts" / "post-task-learn.json", {})
        receipt_dir = rd / "matched-evidence-acquisition"
        seen: set[str] = set()
        if receipt_dir.exists():
            for path in sorted(receipt_dir.glob("*.json")):
                value = self.store.read_json(path, {})
                if isinstance(value, dict) and isinstance(value.get("idempotency_key"), str):
                    seen.add(value["idempotency_key"])
        decision = decide_matched_evidence_acquisition(
            learn_result=learn_result,
            proposed_root_unit=proposed_unit,
            current_authority=matched_evidence_authority(self.root),
            current_source_clock=matched_evidence_source_clock(self.root),
            seen_idempotency_keys=seen,
        )
        selected = decision.get("selected_unit")
        if decision.get("decision") != "SCHEDULE_ONCE" or not isinstance(selected, dict):
            return proposed_unit, decision
        return selected, decision

    def _record_matched_evidence_schedule(
        self,
        run_id: str,
        proposed_unit: dict[str, Any],
        decision: dict[str, Any],
        scheduled_snapshot: dict[str, Any],
    ) -> None:
        receipt_dir = self.store.run_dir(run_id) / "matched-evidence-acquisition"
        receipt = decision["receipt"]
        receipt["recorded_at"] = self.store.utc_now()
        receipt["run_id"] = run_id
        receipt["proposed_root_unit"] = deepcopy(proposed_unit)
        receipt["scheduled_snapshot_revision"] = scheduled_snapshot.get("revision")
        receipt["scheduled_unit_id"] = scheduled_snapshot.get("current_unit")
        digest = str(decision["idempotency_key"]).split(":", 1)[1]
        self.store.atomic_json(receipt_dir / f"{digest}.json", receipt)
        self.store.append_event(
            run_id,
            {
                "type": "matched_evidence_acquisition_scheduled",
                "idempotency_key": decision["idempotency_key"],
                "capability_id": receipt["capability_id"],
            },
        )

    def resume(self, run_id: str, max_steps: int = 64) -> dict[str, Any]:
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        if not self.store.run_dir(run_id).is_dir():
            raise FileNotFoundError(run_id)

        for _ in range(max_steps):
            snapshot = self.store.snapshot(run_id)
            if snapshot.get("status") in TERMINAL_STATUSES:
                return snapshot
            phase = snapshot.get("phase")
            rd = self.store.run_dir(run_id)
            try:
                if phase == "entry_pending":
                    request = (rd / "request.md").read_text(encoding="utf-8")
                    output = self._invoke(run_id, "entry", {"request": request})
                    self.store.atomic_json(rd / "artifacts" / "entry.json", output.get("result", {}))
                    self._advance(
                        run_id,
                        snapshot,
                        phase="root_pending",
                        entry_ref="artifacts/entry.json",
                    )
                    continue

                if phase == "root_pending":
                    output = self._invoke(
                        run_id,
                        "root",
                        {
                            "snapshot": snapshot,
                            "entry": self.store.read_json(rd / "artifacts" / "entry.json", {}),
                            "last_result": (
                                self.store.read_json(rd / snapshot["last_result_ref"], {})
                                if snapshot.get("last_result_ref")
                                else None
                            ),
                            "last_evaluation": snapshot.get("last_evaluation"),
                            "continuation_stack": snapshot.get("continuation_stack", []),
                        },
                    )
                    proposed = output.get("result", {})
                    if not isinstance(proposed, dict):
                        proposed = {}
                    selected, guard_decision = self._guard_root_selection(
                        run_id,
                        proposed,
                    )
                    scheduled = self._schedule_unit(
                        run_id,
                        snapshot,
                        selected,
                    )
                    if guard_decision.get("decision") == "SCHEDULE_ONCE":
                        self._record_matched_evidence_schedule(
                            run_id,
                            proposed,
                            guard_decision,
                            scheduled,
                        )
                    continue

                if phase == "unit_pending":
                    unit_id = snapshot["current_unit"]
                    unit = self.store.read_json(rd / "execution-units" / f"{unit_id}.json", {})
                    component = snapshot.get("current_component", "execute")
                    if component not in SEMANTIC_COMPONENTS:
                        component = "execute"
                    output = self._invoke(
                        run_id,
                        component,
                        {"snapshot": snapshot, "execution_unit": unit},
                    )
                    result = output.get("result", {})
                    artifact_ref = f"artifacts/{unit_id}-result.json"
                    self.store.atomic_json(rd / artifact_ref, result)

                    if component == "task_evaluate":
                        task_verdict = result.get("verdict") or result.get("status")
                        unit_verdict = result.get("unit_verdict")
                        unit_subfinding = result.get("unit_subfinding")
                        if unit_verdict is None and isinstance(unit_subfinding, dict):
                            unit_verdict = unit_subfinding.get("verdict")
                        # Backward compatibility: a legacy evaluator with one verdict
                        # evaluates both the current unit and the original task.
                        unit_verdict = unit_verdict or task_verdict
                        if unit_verdict == "PASS":
                            self._advance(
                                run_id,
                                snapshot,
                                phase="consolidate_pending",
                                last_evaluation=result,
                                last_result_ref=artifact_ref,
                                task_completion_verdict=task_verdict,
                                unit_completion_verdict=unit_verdict,
                            )
                        else:
                            self._advance(
                                run_id,
                                snapshot,
                                phase="root_pending",
                                last_evaluation=result,
                                last_result_ref=artifact_ref,
                                task_completion_verdict=task_verdict,
                                unit_completion_verdict=unit_verdict,
                            )
                        continue

                    next_unit = result.get("next_execution_unit") or result.get("child_execution_unit")
                    if isinstance(next_unit, dict):
                        parent = {
                            "parent_unit_id": unit_id,
                            "continuation": result.get("continuation"),
                            "result_ref": artifact_ref,
                        }
                        self._schedule_unit(run_id, snapshot, next_unit, parent=parent)
                    else:
                        self._advance(
                            run_id,
                            snapshot,
                            phase="root_pending",
                            last_result_ref=artifact_ref,
                        )
                    continue

                if phase == "consolidate_pending":
                    fragments = [
                        json.loads(path.read_text(encoding="utf-8"))
                        for path in sorted((rd / "fragments").glob("*.json"))
                    ]
                    output = self._invoke(
                        run_id,
                        "consolidate_episode",
                        {
                            "snapshot": snapshot,
                            "fragments": fragments,
                            "artifact_refs": [
                                path.relative_to(self.root).as_posix()
                                for path in sorted((rd / "artifacts").glob("*.json"))
                            ],
                            "preflight_refs": [
                                path.relative_to(self.root).as_posix()
                                for path in sorted((rd / "preflight").glob("*.json"))
                                if not path.name.startswith("preflight-consolidate_episode-")
                            ],
                            "candidate_trial_refs": [
                                path.relative_to(self.root).as_posix()
                                for path in sorted((rd / "candidate-trials").glob("*.json"))
                            ],
                        },
                    )
                    episode_id = f"episode-{run_id.removeprefix('run-')}"
                    episode_dir = self.root / ".continual" / "episodes" / episode_id
                    self.store.atomic_json(episode_dir / "episode.json", output.get("result", {}))
                    relations = episode_dir / "relations.jsonl"
                    relations.parent.mkdir(parents=True, exist_ok=True)
                    relations.touch(exist_ok=True)
                    self._advance(
                        run_id,
                        snapshot,
                        phase="post_task_learn_pending",
                        episode_id=episode_id,
                    )
                    continue

                if phase == "post_task_learn_pending":
                    episode_id = str(snapshot.get("episode_id"))
                    episode_path = self.root / ".continual" / "episodes" / episode_id / "episode.json"
                    episode = self.store.read_json(episode_path, {})
                    output = self._invoke(
                        run_id,
                        "learn",
                        {
                            "snapshot": snapshot,
                            "mode": "post-task",
                            "episode_id": episode_id,
                            "current_episode": episode,
                            "episode_catalog": self._episode_catalog(),
                            "candidate_index": self._index(),
                        },
                    )
                    artifact_path = rd / "artifacts" / "post-task-learn.json"
                    result = output.get("result", {})
                    self.store.atomic_json(artifact_path, result)
                    produced = self._register_candidates(
                        result,
                        artifact_path.relative_to(self.root).as_posix(),
                    )
                    relations = self.root / ".continual" / "episodes" / episode_id / "relations.jsonl"
                    for candidate_id in produced:
                        self.store.append_jsonl(
                            relations,
                            {
                                "type": "produced_candidate",
                                "candidate_id": candidate_id,
                                "at": self.store.utc_now(),
                            },
                        )
                    if snapshot.get("task_completion_verdict") == "PASS":
                        finished = self._advance(
                            run_id,
                            snapshot,
                            status="finished",
                            phase="finished",
                            produced_candidates=produced,
                        )
                        self.store.append_event(
                            run_id, {"type": "run_finished", "episode_id": episode_id}
                        )
                        return finished
                    self._advance(
                        run_id,
                        snapshot,
                        status="continue",
                        phase="root_pending",
                        produced_candidates=produced,
                    )
                    self.store.append_event(
                        run_id,
                        {
                            "type": "unit_learned",
                            "episode_id": episode_id,
                            "task_verdict": snapshot.get("task_completion_verdict"),
                        },
                    )
                    continue

                raise RuntimeError(f"unknown phase: {phase}")

            except WorkModelPending:
                # The native invocation journal already freezes the exact request.
                # Mutating the semantic snapshot here would change the payload digest
                # on resume and create a different Work invocation instead of replaying
                # the response for the frozen one.
                return self.store.snapshot(run_id)

            except WorkProviderMismatch:
                # A non-Work provider tried to resume a run whose next invocation
                # is frozen awaiting a Work-model response. Leave the run exactly
                # as-is (no wrong-provider completion, no error inflation); only a
                # Work-model client may advance it.
                return self.store.snapshot(run_id)

            except Exception as exc:
                self.store.append_event(
                    run_id,
                    {
                        "type": "phase_error",
                        "phase": phase,
                        "error": {"type": type(exc).__name__, "message": str(exc)},
                    },
                )
                current = self.store.snapshot(run_id)
                if current.get("revision") == snapshot.get("revision"):
                    failed = deepcopy(current)
                    failed["status"] = "continue"
                    failed["last_error"] = {
                        "phase": phase,
                        "type": type(exc).__name__,
                        "message": str(exc),
                        "at": self.store.utc_now(),
                    }
                    failed["error_count"] = int(failed.get("error_count", 0)) + 1
                    failed["expected_revision"] = current.get("revision", 0)
                    self.store.write_snapshot(run_id, failed)
                raise

        snapshot = self.store.snapshot(run_id)
        checkpoint = deepcopy(snapshot)
        checkpoint["status"] = "continue"
        checkpoint["checkpoint_reason"] = "step_budget_exhausted"
        checkpoint["expected_revision"] = snapshot.get("revision", 0)
        checkpoint = self.store.write_snapshot(run_id, checkpoint)
        self.store.append_event(
            run_id,
            {"type": "step_budget_exhausted", "max_steps": max_steps, "phase": checkpoint.get("phase")},
        )
        return checkpoint

    def resume_all(self, max_steps: int = 16) -> dict[str, Any]:
        runs_dir = self.root / ".continual" / "runs"
        results: list[dict[str, Any]] = []
        if not runs_dir.exists():
            return {"resumed": [], "errors": []}
        errors: list[dict[str, str]] = []
        for run_dir in sorted(path for path in runs_dir.iterdir() if path.is_dir()):
            snapshot = self.store.read_json(run_dir / "snapshot.json", {})
            if not isinstance(snapshot, dict) or snapshot.get("status") in TERMINAL_STATUSES:
                continue
            try:
                result = self.resume(run_dir.name, max_steps=max_steps)
                results.append(
                    {
                        "run_id": run_dir.name,
                        "status": result.get("status"),
                        "phase": result.get("phase"),
                    }
                )
            except Exception as exc:
                errors.append(
                    {"run_id": run_dir.name, "type": type(exc).__name__, "message": str(exc)}
                )
        return {"resumed": results, "errors": errors}

    def feedback(self, episode_id: str, text: str) -> None:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("feedback text must be non-empty")
        episode_dir = self.root / ".continual" / "episodes" / episode_id
        if not (episode_dir / "episode.json").exists():
            raise FileNotFoundError(episode_id)
        self.store.append_jsonl(
            episode_dir / "relations.jsonl",
            {"type": "user_feedback", "text": text, "at": self.store.utc_now()},
        )
