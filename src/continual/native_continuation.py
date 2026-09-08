from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


_INVOCATION_ID = re.compile(r"^invoke-[0-9a-f]{24}$")
_REQUEST_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class NativeContinuationBindingError(ValueError):
    """Raised when state can bypass or misbind an awaiting native journal."""


@dataclass(frozen=True)
class PendingNativeContinuation:
    """The one native journal that an exact continuation must resume."""

    native_invocation_id: str
    native_ref: str
    native_path: Path
    native_blob_sha: str
    work_invocation_id: str
    work_request_ref: str
    work_request_digest: str


def _journal_root(root: Path, run_id: str) -> Path:
    if not isinstance(run_id, str) or not run_id.strip():
        raise NativeContinuationBindingError("native continuation run_id is malformed")
    relative = Path(run_id)
    if relative.is_absolute() or ".." in relative.parts or len(relative.parts) != 1:
        raise NativeContinuationBindingError(
            "native continuation run_id escapes the repository"
        )
    runs_root = (root / ".continual" / "runs").resolve()
    if root not in runs_root.parents:
        raise NativeContinuationBindingError(
            "native continuation journal root escapes the repository"
        )
    candidate = (runs_root / run_id / "invocations").resolve()
    if runs_root not in candidate.parents:
        raise NativeContinuationBindingError(
            "native continuation run_id escapes the repository"
        )
    return candidate


def _read_journal(path: Path, root: Path) -> tuple[Mapping[str, Any], str]:
    try:
        payload = path.read_bytes()
        value = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        reference = path.relative_to(root).as_posix()
        raise NativeContinuationBindingError(
            f"cannot inspect native invocation journal: {reference}"
        ) from exc
    if not isinstance(value, Mapping):
        reference = path.relative_to(root).as_posix()
        raise NativeContinuationBindingError(
            f"native invocation journal must be an object: {reference}"
        )
    header = f"blob {len(payload)}\0".encode("ascii")
    return value, hashlib.sha1(header + payload).hexdigest()


def bind_unique_awaiting_native_continuation(
    root: Path,
    *,
    run_id: str,
    exact_continuation: Any,
) -> PendingNativeContinuation | None:
    """Bind state to the unique ``awaiting_work_model`` native journal.

    Inspecting the native run happens before either of the historical
    ``no_exact_continuation`` / ``no_pending_work_invocation`` early returns.
    Consequently state cannot hide an already-frozen semantic continuation by
    deleting or nulling those fields.
    """

    root = root.resolve()
    journal_root = _journal_root(root, run_id)
    awaiting: list[tuple[Path, Mapping[str, Any], str]] = []
    if journal_root.exists():
        if not journal_root.is_dir():
            raise NativeContinuationBindingError(
                "native invocation journal root is not a directory"
            )
        for path in sorted(journal_root.glob("*.json")):
            journal, blob_sha = _read_journal(path, root)
            if journal.get("status") == "awaiting_work_model":
                awaiting.append((path, journal, blob_sha))

    exact = exact_continuation if isinstance(exact_continuation, Mapping) else None
    state_pending_id = exact.get("pending_work_invocation_id") if exact else None

    if not awaiting:
        if state_pending_id is not None:
            raise NativeContinuationBindingError(
                "state pending Work invocation has no awaiting native journal"
            )
        return None
    if exact_continuation is None:
        raise NativeContinuationBindingError(
            "active native run has an awaiting Work journal but exact_continuation is absent"
        )
    if exact is None:
        raise NativeContinuationBindingError(
            "state.exact_continuation must be an object while native Work is awaiting"
        )
    if state_pending_id is None:
        raise NativeContinuationBindingError(
            "active native run has an awaiting Work journal but pending_work_invocation_id is null"
        )
    if len(awaiting) != 1:
        raise NativeContinuationBindingError(
            "active native run must have exactly one awaiting Work journal"
        )

    native_path, journal, native_blob_sha = awaiting[0]
    native_id = journal.get("invocation_id")
    if (
        not isinstance(native_id, str)
        or _INVOCATION_ID.fullmatch(native_id) is None
        or native_path.stem != native_id
    ):
        raise NativeContinuationBindingError(
            "awaiting native invocation identity is malformed"
        )
    state_native_id = exact.get("pending_native_invocation_id")
    if state_native_id != native_id:
        raise NativeContinuationBindingError(
            "state pending native invocation identity mismatch"
        )

    work_id = journal.get("work_invocation_id")
    if (
        not isinstance(work_id, str)
        or _INVOCATION_ID.fullmatch(work_id) is None
        or state_pending_id != work_id
    ):
        raise NativeContinuationBindingError(
            "state pending Work invocation identity mismatch with native journal"
        )
    expected_request_ref = (
        f".continual/work-model/invocations/{work_id}/request.json"
    )
    request_ref = journal.get("work_request_ref")
    if (
        not isinstance(request_ref, str)
        or request_ref != expected_request_ref
        or exact.get("pending_request_ref") != request_ref
    ):
        raise NativeContinuationBindingError(
            "state pending Work request reference mismatch with native journal"
        )
    request_digest = journal.get("work_request_digest")
    if (
        not isinstance(request_digest, str)
        or _REQUEST_DIGEST.fullmatch(request_digest) is None
        or exact.get("pending_request_digest") != request_digest
    ):
        raise NativeContinuationBindingError(
            "state pending Work request digest mismatch with native journal"
        )

    native_ref = native_path.relative_to(root).as_posix()
    return PendingNativeContinuation(
        native_invocation_id=native_id,
        native_ref=native_ref,
        native_path=native_path,
        native_blob_sha=native_blob_sha,
        work_invocation_id=work_id,
        work_request_ref=request_ref,
        work_request_digest=request_digest,
    )
