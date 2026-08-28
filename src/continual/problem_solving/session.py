"""One transient recursive problem-solving session."""

from __future__ import annotations

import copy
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable, TypeVar

from .model import (
    ALL_PHASES, OBSERVE_PHASES, TERMINAL, UTC, ClaimConflictError, Decomposition,
    Evaluation, ExistingSolutionAudit, ExternalReceipt, Forecast,
    OptimizerRoleAdapter, PhaseAdmissionError, ProblemSolvingHooks,
    SessionStateError, SolutionCandidate, _hash, _id, _now, _overlap, _path,
    _scope, _time,
)
from .store import Store

T = TypeVar("T")


class ProblemSolvingSession:
    def __init__(self, store: Store, state: dict[str, Any], role: OptimizerRoleAdapter):
        self.store, self.state, self.role = store, state, role

    def view(self) -> dict[str, Any]: return copy.deepcopy(self.state)
    def node(self, nid: str) -> dict[str, Any]: return copy.deepcopy(self.state["nodes"][nid])
    def _root(self) -> dict[str, Any]: return self.state["nodes"][self.state["root_id"]]

    def _ancestors(self, nid: str) -> list[str]:
        out = []
        while nid is not None:
            out.append(nid); nid = self.state["nodes"][nid]["parent_id"]
        return list(reversed(out))

    def _within(self, child: str, owner: str) -> bool: return owner in self._ancestors(child)
    def _fresh(self) -> list[dict[str, Any]]:
        now = datetime.now(UTC)
        return [c for c in self.state["claims"].values() if c["status"] == "active" and now <= _time(c["heartbeat_at"]) + timedelta(seconds=c["stale_after_seconds"])]

    def _expire_stale_claims(self) -> None:
        fresh_ids = {claim["claim_id"] for claim in self._fresh()}
        expired = []
        for claim in self.state["claims"].values():
            if claim["status"] == "active" and claim["claim_id"] not in fresh_ids:
                claim["status"] = "stale"
                expired.append(claim["claim_id"])
        if expired:
            self.store.append(self.state, "claim", "stale", {"claim_ids": sorted(expired)})

    def heartbeat_claim(self, cid: str) -> None:
        self._expire_stale_claims()
        claim = self.state["claims"].get(cid)
        if claim is None:
            raise SessionStateError("unknown claim")
        if claim["status"] != "active":
            raise SessionStateError("claim is not active")
        claim["heartbeat_at"] = _now()
        self.store.append(self.state, "claim", "heartbeat", {"claim_id": cid})

    def claim_work(self, *, worker_id: str, node_id: str, scope: str,
                   reserved_paths: Iterable[str] = (), stale_after_seconds: int = 900,
                   claim_id: str | None = None) -> dict[str, Any]:
        if self.state["status"] not in {"active", "interrupted"}: raise SessionStateError("session is not claimable")
        _id(worker_id, "worker_id")
        if node_id not in self.state["nodes"]: raise ValueError("unknown node")
        if isinstance(stale_after_seconds, bool) or stale_after_seconds < 1:
            raise ValueError("stale_after_seconds must be positive")
        sc, paths = _scope(scope), sorted({_path(p) for p in reserved_paths})
        self._expire_stale_claims()
        for other in self._fresh():
            reasons = []
            if self._within(node_id, other["node_id"]) or self._within(other["node_id"], node_id): reasons.append("node")
            if _overlap(sc, other["scope"]): reasons.append("scope")
            if any(_overlap(a, b) for a in paths for b in other["reserved_paths"]): reasons.append("path")
            if reasons: raise ClaimConflictError(f"collision with {other['claim_id']}: {','.join(reasons)}")
        cid, stamp = claim_id or f"claim-{uuid.uuid4().hex}", _now(); _id(cid, "claim_id")
        claim = {"claim_id": cid, "worker_id": worker_id, "node_id": node_id,
            "scope": sc, "reserved_paths": paths, "started_at": stamp, "heartbeat_at": stamp,
            "stale_after_seconds": stale_after_seconds, "status": "active"}
        self.state["claims"][cid] = claim; self.store.append(self.state, "claim", "created", {"claim": claim})
        return copy.deepcopy(claim)

    def close_claim(self, cid: str, status: str = "closed") -> None:
        if cid not in self.state["claims"]: raise SessionStateError("unknown claim")
        self.state["claims"][cid]["status"] = status; self.store.append(self.state, "claim", "closed", {"claim_id": cid, "status": status})

    def _admit(self, phase: str, nid: str, worker: str | None, cid: str | None,
               paths: Iterable[str] = ()) -> dict[str, Any]:
        if phase not in ALL_PHASES: raise PhaseAdmissionError(f"unknown phase: {phase}")
        norm = sorted({_path(p) for p in paths}); mode = "observe" if phase in OBSERVE_PHASES else "exclusive"
        claim: Any = None
        if mode == "exclusive" and worker is not None:
            if cid is None or cid not in self.state["claims"]: raise PhaseAdmissionError("fresh claim required")
            c = self.state["claims"][cid]
            if c["worker_id"] != worker or c not in self._fresh() or not self._within(nid, c["node_id"]): raise PhaseAdmissionError("claim does not own phase node")
            if any(not any(p == r or p.startswith(r + "/") for r in c["reserved_paths"]) for p in norm): raise PhaseAdmissionError("path outside claim")
            claim = copy.deepcopy(c)
        elif mode == "exclusive": claim = {"role": "session_coordinator"}
        return {"admitted": True, "phase": phase, "mode": mode, "session_id": self.state["session_id"],
                "node_id": nid, "paths": norm, "claim": claim, "state_digest": _hash(self.state)}

    def _call(self, phase: str, nid: str, callback: Callable[[], T], *, worker: str | None = None, cid: str | None = None) -> T:
        admission = self._admit(phase, nid, worker, cid); self.store.append(self.state, phase, "admitted", {"admission": admission})
        try: result = callback()
        except Exception as exc:
            self.state["status"] = "interrupted"; self.state["last_error"] = f"{type(exc).__name__}: {exc}"
            self.store.append(self.state, phase, "failed", {"admission": admission, "error": self.state["last_error"]}); raise
        self.store.append(self.state, phase, "completed", {"admission": admission, "result_type": type(result).__name__})
        return result

    def _forecast(self, hooks: ProblemSolvingHooks, worker: str | None, cid: str | None) -> None:
        if self.state["last_forecast_revision"] == self.state["tree_revision"]: return
        root = self._root(); result = self._call("forecast", root["problem_id"], lambda: hooks.forecast(self.view(), self.node(root["problem_id"])), worker=worker, cid=cid)
        if not isinstance(result, Forecast): raise TypeError("forecast must return Forecast")
        count = sum(n["status"] != "replaced" for n in self.state["nodes"].values())
        budget = min(self.state["hard_node_limit"], max(count, result.predicted_active_nodes))
        self.state["predicted_node_budget"] = budget; self.state["last_forecast_revision"] = self.state["tree_revision"]
        item = {"tree_revision": self.state["tree_revision"], "predicted_active_nodes": budget,
                "rationale": result.rationale, "recorded_at": _now()}
        self.state["forecast_history"].append(item); self.store.append(self.state, "forecast", "persisted", item)

    def _solve(self, hooks: ProblemSolvingHooks, node: dict[str, Any], candidate: SolutionCandidate,
               worker: str | None, cid: str | None) -> Evaluation:
        result = self._call("evaluate", node["problem_id"], lambda: hooks.evaluate(self.view(), self.node(node["problem_id"]), candidate), worker=worker, cid=cid)
        if not isinstance(result, Evaluation): raise TypeError("evaluate must return Evaluation")
        node["solution"], node["evaluation"] = candidate.dict(), result.dict()
        if result.accepts(node["success_criteria"]):
            phase = "solve_root" if node["problem_id"] == self.state["root_id"] else "solve_parent"
            admission = self._admit(phase, node["problem_id"], worker, cid); self.store.append(self.state, phase, "admitted", {"admission": admission})
            node["status"], node["blocked_reason"] = "solved", None
            self.store.append(self.state, phase, "solved", {"admission": admission, "problem_id": node["problem_id"], "candidate": candidate.dict(), "evaluation": result.dict()})
        else:
            node["status"], node["blocked_reason"] = "open", result.reason or "not verified"
            self.store.append(self.state, "evaluate", "rejected", {"problem_id": node["problem_id"], "evaluation": result.dict()})
        return result

    def _decompose(self, hooks: ProblemSolvingHooks, node: dict[str, Any], proposed: Decomposition,
                   worker: str | None, cid: str | None) -> bool:
        ids = [x.problem_id for x in proposed.children]
        if len(ids) != len(set(ids)) or any(x == node["problem_id"] or x in self.state["nodes"] for x in ids): raise ValueError("invalid child ids")
        if not proposed.children:
            node["status"], node["blocked_reason"] = "blocked", proposed.rationale or "no justified decomposition"
            self.store.append(self.state, "decompose", "blocked", {"problem_id": node["problem_id"], "reason": node["blocked_reason"]}); return False
        count = sum(n["status"] != "replaced" for n in self.state["nodes"].values()); budget = self.state["predicted_node_budget"] or self.state["hard_node_limit"]
        selected = proposed
        if count + len(proposed.children) > budget:
            overflow = count + len(proposed.children) - budget
            selected = self._call("update_problem_tree", node["problem_id"], lambda: hooks.rewrite_transversal(self.view(), self.node(node["problem_id"]), proposed, overflow), worker=worker, cid=cid)
            if not isinstance(selected, Decomposition) or not selected.children:
                node["status"], node["blocked_reason"] = "blocked", "forecast exceeded without transversal rewrite"
                self.store.append(self.state, "update_problem_tree", "rewrite_denied", {"problem_id": node["problem_id"], "overflow_by": overflow}); return False
            new_ids = [x.problem_id for x in selected.children]
            if len(new_ids) != len(set(new_ids)) or any(x in self.state["nodes"] for x in new_ids): raise ValueError("invalid rewrite ids")
            self.state["archived_proposals"].append({"parent_id": node["problem_id"], "reason": "forecast_overrun_transversal_rewrite",
                "overflow_by": overflow, "replaced_children": [x.dict() for x in proposed.children],
                "replacement_children": [x.dict() for x in selected.children], "recorded_at": _now()})
        if count + len(selected.children) > self.state["hard_node_limit"]:
            node["status"], node["blocked_reason"] = "blocked", "hard node limit exceeded"; return False
        admission = self._admit("update_problem_tree", node["problem_id"], worker, cid)
        self.store.append(self.state, "update_problem_tree", "admitted", {"admission": admission, "children": [x.dict() for x in selected.children]})
        for spec in selected.children:
            self.state["nodes"][spec.problem_id] = {**spec.dict(), "parent_id": node["problem_id"], "status": "open", "children": [],
                "direct_attempted": False, "solution": None, "evaluation": None, "blocked_reason": None, "replaced_by": []}
            node["children"].append(spec.problem_id)
        node["status"], node["blocked_reason"] = "decomposed", None; self.state["tree_revision"] += 1
        self.store.append(self.state, "update_problem_tree", "rewritten" if selected is not proposed else "decomposed",
            {"admission": admission, "problem_id": node["problem_id"], "children": node["children"], "tree_revision": self.state["tree_revision"]})
        return True

    def _integrate(self, hooks: ProblemSolvingHooks, node: dict[str, Any], worker: str | None, cid: str | None) -> bool:
        children = [self.node(x) for x in node["children"]]
        candidate = self._call("integrate_children", node["problem_id"], lambda: hooks.integrate_children(self.view(), self.node(node["problem_id"]), children), worker=worker, cid=cid)
        if not isinstance(candidate, SolutionCandidate):
            node["status"], node["blocked_reason"] = "blocked", "solved children produced no integration candidate"; return False
        evaluation = self._solve(hooks, node, candidate, worker, cid)
        if not evaluation.accepts(node["success_criteria"]): node["status"] = "blocked"
        return evaluation.accepts(node["success_criteria"])

    def _finish_if_ready(self, hooks: ProblemSolvingHooks) -> bool:
        root = self._root()
        if root["status"] != "solved": return False
        policy = self.state["completion_policy"]
        if policy["require_publish"]:
            if self.state["publication_receipt"] is None:
                receipt = self._call("publish", root["problem_id"], lambda: hooks.publish(self.view(), self.node(root["problem_id"])))
                if not isinstance(receipt, ExternalReceipt): raise TypeError("publish must return ExternalReceipt")
                self.state["publication_receipt"] = receipt.dict(); self.store.append(self.state, "publish", "verified" if receipt.verified else "rejected", receipt.dict())
            if self.state["publication_receipt"]["verified"] is not True: return False
        if policy["require_merge"]:
            if self.state["merge_receipt"] is None:
                pub = ExternalReceipt(**copy.deepcopy(self.state["publication_receipt"]))
                receipt = self._call("merge", root["problem_id"], lambda: hooks.merge(self.view(), self.node(root["problem_id"]), pub))
                if not isinstance(receipt, ExternalReceipt): raise TypeError("merge must return ExternalReceipt")
                self.state["merge_receipt"] = receipt.dict(); self.store.append(self.state, "merge", "verified" if receipt.verified else "rejected", receipt.dict())
            if self.state["merge_receipt"]["verified"] is not True: return False
        self._restore("completed", "root verified"); return True

    def _restore(self, terminal: str, reason: str) -> None:
        self.state["status"] = "completing" if terminal == "completed" else "abandoning"; self.state["restore_requested"] = True
        self.store.append(self.state, "session", f"{terminal}_requested", {"reason": reason})
        for claim in self.state["claims"].values():
            if claim["status"] == "active": claim["status"] = "closed_by_session"
        op = f"{self.state['session_id']}:restore-role"
        self.role.restore_role(session_id=self.state["session_id"], snapshot=copy.deepcopy(self.state["role_snapshot"]), operation_id=op)
        self.state.update({"role_restored": True, "mode": "normal", "status": terminal, "last_error": None})
        self.store.append(self.state, "session", "role_restored", {"terminal_status": terminal, "operation_id": op})
        self.store.write_control("normal", None, self.state["session_id"])

    def abandon(self, reason: str) -> None:
        if self.state["status"] not in TERMINAL: self._restore("abandoned", reason)

    def checkpoint(self, note: str, evidence: Iterable[Any] = ()) -> None:
        self.store.append(self.state, "checkpoint", "persisted", {"note": note, "evidence": copy.deepcopy(list(evidence))})

    def step(self, hooks: ProblemSolvingHooks, *, worker_id: str | None = None, claim_id: str | None = None) -> bool:
        if self.state["status"] in TERMINAL: return False
        if self.state["status"] in {"completing", "abandoning"}: raise SessionStateError("recover pending restoration")
        if self.state["status"] == "interrupted":
            self.state["status"], self.state["last_error"] = "active", None; self.store.append(self.state, "session", "resumed")
        if self._finish_if_ready(hooks): return True
        parents = [n for n in self.state["nodes"].values() if n["status"] == "decomposed" and n["children"] and all(self.state["nodes"][c]["status"] == "solved" for c in n["children"])]
        if parents:
            parents.sort(key=lambda n: (-len(self._ancestors(n["problem_id"])), n["problem_id"]))
            changed = self._integrate(hooks, parents[0], worker_id, claim_id); self._finish_if_ready(hooks); return changed
        self._forecast(hooks, worker_id, claim_id)
        leaves = [n for n in self.state["nodes"].values() if n["status"] == "open" and not n["children"]]
        leaves.sort(key=lambda n: (len(self._ancestors(n["problem_id"])), n["problem_id"]))
        if not leaves: return False
        selected = self._call("select_leaf", self.state["root_id"], lambda: hooks.select_leaf(self.view(), copy.deepcopy(leaves)), worker=worker_id, cid=claim_id)
        if selected not in {n["problem_id"] for n in leaves}: raise SessionStateError("selected node is not an open leaf")
        self.store.append(self.state, "select_leaf", "selected", {"problem_id": selected}); node = self.state["nodes"][selected]
        targets = [x for x in self._ancestors(selected) if x not in self.state["audited_node_ids"]]
        audit = self._call("existing_solution_audit", selected, lambda: hooks.audit_existing_solution(self.view(), self.node(selected), targets), worker=worker_id, cid=claim_id)
        if not isinstance(audit, ExistingSolutionAudit): raise TypeError("audit must return ExistingSolutionAudit")
        if not set(targets).issubset(audit.audited_node_ids):
            node["status"], node["blocked_reason"] = "blocked", "audit omitted leaf or ancestor"; return False
        self.state["audited_node_ids"] = sorted(set(self.state["audited_node_ids"]) | set(audit.audited_node_ids))
        self.store.append(self.state, "existing_solution_audit", "persisted", {"audited_node_ids": list(audit.audited_node_ids), "notes": audit.notes,
            "candidate": audit.candidate.dict() if audit.candidate else None})
        failed, failed_eval = audit.candidate, None
        if audit.candidate:
            failed_eval = self._solve(hooks, node, audit.candidate, worker_id, claim_id)
            if failed_eval.accepts(node["success_criteria"]): self._finish_if_ready(hooks); return True
        if not node["direct_attempted"]:
            candidate = self._call("attempt_solution", selected, lambda: hooks.attempt_solution(self.view(), self.node(selected), audit), worker=worker_id, cid=claim_id)
            node["direct_attempted"] = True
            if candidate is not None and not isinstance(candidate, SolutionCandidate): raise TypeError("attempt must return SolutionCandidate or None")
            if candidate:
                failed, failed_eval = candidate, self._solve(hooks, node, candidate, worker_id, claim_id)
                if failed_eval.accepts(node["success_criteria"]): self._finish_if_ready(hooks); return True
        decomposition = self._call("decompose", selected, lambda: hooks.decompose(self.view(), self.node(selected), failed, failed_eval), worker=worker_id, cid=claim_id)
        if not isinstance(decomposition, Decomposition): raise TypeError("decompose must return Decomposition")
        return self._decompose(hooks, node, decomposition, worker_id, claim_id)

    def run(self, hooks: ProblemSolvingHooks, *, max_steps: int = 100,
            worker_id: str | None = None, claim_id: str | None = None) -> dict[str, Any]:
        for _ in range(max_steps):
            before = self.state["event_count"]; progressed = self.step(hooks, worker_id=worker_id, claim_id=claim_id)
            if self.state["status"] in TERMINAL or (not progressed and self.state["event_count"] == before): break
        return copy.deepcopy(self.state)
