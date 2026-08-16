from __future__ import annotations

from pathlib import Path
from textwrap import dedent


def write(path: str, content: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dedent(content).lstrip(), encoding="utf-8")


write(
    "src/agi/memory.py",
    r'''
    from __future__ import annotations

    import hashlib
    import json
    import os
    import re
    import tempfile
    from collections import Counter, defaultdict
    from dataclasses import asdict, dataclass
    from pathlib import Path
    from typing import Any, Iterable, Mapping

    from .evidence import canonical_json
    from .tasking import json_copy, utc_now


    class EpisodeIntegrityError(RuntimeError):
        """Raised when the episodic memory hash chain has been rewritten."""


    def _tokens(value: str) -> set[str]:
        return set(
            re.findall(
                r"[a-z0-9_]+|[\u3040-\u30ff\u3400-\u9fff]",
                value.lower(),
            )
        )


    def _atomic_json(path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise


    @dataclass(frozen=True, slots=True)
    class Episode:
        episode_id: str
        run_id: str
        task_id: str
        goal: str
        tags: tuple[str, ...]
        status: str
        summary: str
        lessons: tuple[str, ...]
        failures: tuple[str, ...]
        skills: tuple[str, ...]
        created_at: str

        @classmethod
        def from_mapping(cls, value: Mapping[str, Any]) -> "Episode":
            if not isinstance(value, Mapping):
                raise ValueError("episode must be an object")

            def text(name: str) -> str:
                item = value.get(name)
                if not isinstance(item, str) or not item.strip():
                    raise ValueError(f"episode {name} must be a non-empty string")
                return item.strip()

            def strings(name: str) -> tuple[str, ...]:
                items = value.get(name, [])
                if not isinstance(items, (list, tuple)) or not all(
                    isinstance(item, str) and item.strip() for item in items
                ):
                    raise ValueError(f"episode {name} must be a list of strings")
                return tuple(item.strip() for item in items)

            return cls(
                episode_id=text("episode_id"),
                run_id=text("run_id"),
                task_id=text("task_id"),
                goal=text("goal"),
                tags=strings("tags"),
                status=text("status"),
                summary=text("summary"),
                lessons=strings("lessons"),
                failures=strings("failures"),
                skills=strings("skills"),
                created_at=text("created_at"),
            )

        def as_dict(self) -> dict[str, Any]:
            value = asdict(self)
            for key in ("tags", "lessons", "failures", "skills"):
                value[key] = list(value[key])
            return value


    class EpisodeStore:
        """Hash-chained task episodes plus recent, medium, and stable memory views."""

        def __init__(self, root: Path):
            self.root = root.resolve()
            self.directory = self.root / ".continual" / "agi" / "memory"
            self.ledger = self.directory / "episodes.jsonl"
            self.recent_path = self.directory / "recent.json"
            self.medium_path = self.directory / "medium.json"
            self.long_path = self.directory / "long.json"

        def _entries(self) -> list[dict[str, Any]]:
            if not self.ledger.exists():
                return []
            previous = "0" * 64
            entries: list[dict[str, Any]] = []
            with self.ledger.open(encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, 1):
                    if not line.strip():
                        continue
                    entry = json.loads(line)
                    if not isinstance(entry, dict):
                        raise EpisodeIntegrityError(
                            f"episode ledger line {line_number} is not an object"
                        )
                    claimed = entry.get("entry_hash")
                    payload = {key: item for key, item in entry.items() if key != "entry_hash"}
                    if payload.get("previous_hash") != previous:
                        raise EpisodeIntegrityError(
                            f"episode previous_hash mismatch at line {line_number}"
                        )
                    actual = hashlib.sha256(
                        canonical_json(payload).encode("utf-8")
                    ).hexdigest()
                    if actual != claimed:
                        raise EpisodeIntegrityError(
                            f"episode entry hash mismatch at line {line_number}"
                        )
                    Episode.from_mapping(payload.get("episode", {}))
                    previous = actual
                    entries.append(entry)
            return entries

        def load(self) -> list[Episode]:
            return [Episode.from_mapping(entry["episode"]) for entry in self._entries()]

        def append(self, episode: Episode) -> str:
            entries = self._entries()
            previous = entries[-1]["entry_hash"] if entries else "0" * 64
            payload = {"episode": episode.as_dict(), "previous_hash": previous}
            entry_hash = hashlib.sha256(
                canonical_json(payload).encode("utf-8")
            ).hexdigest()
            self.directory.mkdir(parents=True, exist_ok=True)
            with self.ledger.open("a", encoding="utf-8") as handle:
                handle.write(
                    canonical_json({**payload, "entry_hash": entry_hash}) + "\n"
                )
                handle.flush()
                os.fsync(handle.fileno())
            return entry_hash

        def retrieve(
            self,
            query: str,
            tags: Iterable[str] = (),
            *,
            limit: int = 6,
        ) -> list[Episode]:
            episodes = self.load()
            query_tokens = _tokens(query)
            requested_tags = {tag.lower() for tag in tags}
            ranked: list[tuple[float, int, Episode]] = []
            for index, episode in enumerate(episodes):
                content = " ".join(
                    [episode.goal, episode.summary, *episode.lessons, *episode.failures]
                )
                episode_tokens = _tokens(content)
                overlap = len(query_tokens & episode_tokens) / max(1, len(query_tokens))
                tag_overlap = len(
                    requested_tags & {tag.lower() for tag in episode.tags}
                )
                success_bonus = 0.15 if episode.status == "completed" else 0.0
                recency_bonus = (index + 1) / max(1, len(episodes)) * 0.1
                score = overlap + tag_overlap * 0.5 + success_bonus + recency_bonus
                if score > 0:
                    ranked.append((score, index, episode))
            ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
            return [episode for _, _, episode in ranked[:limit]]

        def context(
            self,
            query: str,
            tags: Iterable[str] = (),
            *,
            character_budget: int = 4000,
        ) -> list[dict[str, Any]]:
            result: list[dict[str, Any]] = []
            used = 0
            for episode in self.retrieve(query, tags):
                item = {
                    "episode_id": episode.episode_id,
                    "goal": episode.goal,
                    "status": episode.status,
                    "summary": episode.summary,
                    "lessons": list(episode.lessons),
                    "failures": list(episode.failures),
                    "tags": list(episode.tags),
                }
                size = len(json.dumps(item, ensure_ascii=False))
                if result and used + size > character_budget:
                    break
                result.append(item)
                used += size
            return json_copy(result)

        def consolidate(self) -> dict[str, Any]:
            episodes = self.load()
            recent = [
                {
                    "episode_id": episode.episode_id,
                    "task_id": episode.task_id,
                    "status": episode.status,
                    "summary": episode.summary,
                    "lessons": list(episode.lessons),
                    "created_at": episode.created_at,
                }
                for episode in episodes[-20:]
            ]

            grouped: dict[str, list[Episode]] = defaultdict(list)
            for episode in episodes[-100:]:
                for tag in episode.tags or ("untagged",):
                    grouped[tag].append(episode)
            medium = []
            for tag, values in sorted(grouped.items()):
                lesson_counts = Counter(
                    lesson
                    for episode in values
                    if episode.status == "completed"
                    for lesson in episode.lessons
                )
                medium.append(
                    {
                        "tag": tag,
                        "episodes": len(values),
                        "completed": sum(
                            episode.status == "completed" for episode in values
                        ),
                        "common_lessons": [
                            lesson for lesson, _ in lesson_counts.most_common(8)
                        ],
                    }
                )

            successful_lessons = Counter(
                lesson.strip()
                for episode in episodes
                if episode.status == "completed"
                for lesson in episode.lessons
                if lesson.strip()
            )
            failure_text = "\n".join(
                failure.lower()
                for episode in episodes
                for failure in episode.failures
            )
            stable_lessons = [
                {"lesson": lesson, "successful_occurrences": count}
                for lesson, count in successful_lessons.most_common()
                if count >= 3 and lesson.lower() not in failure_text
            ]
            long_term = {
                "generated_at": utc_now(),
                "episode_count": len(episodes),
                "stable_lessons": stable_lessons,
            }
            _atomic_json(self.recent_path, {"episodes": recent})
            _atomic_json(self.medium_path, {"groups": medium})
            _atomic_json(self.long_path, long_term)
            return {
                "recent": recent,
                "medium": medium,
                "long": long_term,
            }
    ''',
)

write(
    "src/agi/skills.py",
    r'''
    from __future__ import annotations

    import json
    import re
    from dataclasses import asdict, dataclass, field
    from pathlib import Path
    from typing import Any, Iterable, Mapping

    from .tasking import TaskRequest, json_copy


    class SkillValidationError(ValueError):
        pass


    class SkillGraphError(RuntimeError):
        pass


    def _tokens(value: str) -> set[str]:
        return set(
            re.findall(
                r"[a-z0-9_]+|[\u3040-\u30ff\u3400-\u9fff]",
                value.lower(),
            )
        )


    @dataclass(frozen=True, slots=True)
    class SkillDefinition:
        skill_id: str
        version: int
        description: str
        triggers: tuple[str, ...]
        instructions: str
        children: tuple[str, ...] = ()
        input_contract: dict[str, Any] = field(default_factory=dict)
        output_contract: dict[str, Any] = field(default_factory=dict)
        evidence_score: float = 0.0
        status: str = "active"

        @classmethod
        def from_mapping(cls, value: Mapping[str, Any]) -> "SkillDefinition":
            if not isinstance(value, Mapping):
                raise SkillValidationError("skill must be an object")

            def text(name: str) -> str:
                item = value.get(name)
                if not isinstance(item, str) or not item.strip():
                    raise SkillValidationError(f"skill {name} must be a non-empty string")
                return item.strip()

            def strings(name: str) -> tuple[str, ...]:
                items = value.get(name, [])
                if not isinstance(items, (list, tuple)) or not all(
                    isinstance(item, str) and item.strip() for item in items
                ):
                    raise SkillValidationError(f"skill {name} must be a list of strings")
                return tuple(item.strip() for item in items)

            version = value.get("version", 1)
            if isinstance(version, bool) or not isinstance(version, int) or version < 1:
                raise SkillValidationError("skill version must be a positive integer")
            score = value.get("evidence_score", 0.0)
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                raise SkillValidationError("skill evidence_score must be numeric")
            status = value.get("status", "active")
            if status not in {"active", "candidate", "retired"}:
                raise SkillValidationError("invalid skill status")
            input_contract = value.get("input_contract", {})
            output_contract = value.get("output_contract", {})
            if not isinstance(input_contract, Mapping) or not isinstance(
                output_contract, Mapping
            ):
                raise SkillValidationError("skill contracts must be objects")
            return cls(
                skill_id=text("skill_id"),
                version=version,
                description=text("description"),
                triggers=strings("triggers"),
                instructions=text("instructions"),
                children=strings("children"),
                input_contract=json_copy(dict(input_contract)),
                output_contract=json_copy(dict(output_contract)),
                evidence_score=float(score),
                status=status,
            )

        def as_dict(self) -> dict[str, Any]:
            value = asdict(self)
            value["triggers"] = list(self.triggers)
            value["children"] = list(self.children)
            return value


    class SkillRegistry:
        """Versioned compositional skills selected from the same set for any task."""

        def __init__(self, root: Path):
            self.root = root.resolve()
            self.directory = self.root / ".continual" / "skills"

        def load(self) -> dict[str, SkillDefinition]:
            skills: dict[str, SkillDefinition] = {}
            if not self.directory.exists():
                return skills
            for path in sorted(self.directory.glob("*.json")):
                value = json.loads(path.read_text(encoding="utf-8"))
                skill = SkillDefinition.from_mapping(value)
                previous = skills.get(skill.skill_id)
                if previous is None or skill.version > previous.version:
                    skills[skill.skill_id] = skill
            return skills

        def save(self, skill: SkillDefinition) -> Path:
            if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", skill.skill_id):
                raise SkillValidationError("skill_id is not a safe file name")
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / f"{skill.skill_id}.json"
            path.write_text(
                json.dumps(skill.as_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            return path

        def select(self, task: TaskRequest, *, limit: int = 5) -> list[SkillDefinition]:
            query_tokens = _tokens(" ".join([task.goal, *task.tags]))
            task_tags = {tag.lower() for tag in task.tags}
            ranked: list[tuple[float, str, SkillDefinition]] = []
            for skill in self.load().values():
                if skill.status != "active":
                    continue
                trigger_tokens = _tokens(" ".join(skill.triggers))
                description_tokens = _tokens(skill.description)
                lexical = len(query_tokens & (trigger_tokens | description_tokens))
                tag_hits = len(task_tags & {trigger.lower() for trigger in skill.triggers})
                universal = 0.5 if "*" in skill.triggers else 0.0
                score = lexical + tag_hits * 2 + universal + max(0.0, skill.evidence_score) * 0.1
                if score > 0:
                    ranked.append((score, skill.skill_id, skill))
            ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
            return [skill for _, _, skill in ranked[:limit]]

        def expand(
            self,
            skill_ids: Iterable[str],
            *,
            max_depth: int = 8,
        ) -> list[SkillDefinition]:
            skills = self.load()
            visiting: set[str] = set()
            visited: set[str] = set()
            ordered: list[SkillDefinition] = []

            def visit(skill_id: str, depth: int) -> None:
                if depth > max_depth:
                    raise SkillGraphError(f"skill graph exceeds max depth at {skill_id}")
                if skill_id in visiting:
                    raise SkillGraphError(f"skill graph cycle detected at {skill_id}")
                if skill_id in visited:
                    return
                skill = skills.get(skill_id)
                if skill is None:
                    raise SkillGraphError(f"missing child skill: {skill_id}")
                visiting.add(skill_id)
                ordered.append(skill)
                for child in skill.children:
                    visit(child, depth + 1)
                visiting.remove(skill_id)
                visited.add(skill_id)

            for skill_id in skill_ids:
                visit(skill_id, 0)
            return ordered

        def context(
            self,
            task: TaskRequest,
            *,
            limit: int = 5,
            character_budget: int = 6000,
        ) -> list[dict[str, Any]]:
            selected = self.select(task, limit=limit)
            expanded = self.expand(skill.skill_id for skill in selected)
            result: list[dict[str, Any]] = []
            used = 0
            for skill in expanded:
                item = {
                    "skill_id": skill.skill_id,
                    "version": skill.version,
                    "description": skill.description,
                    "instructions": skill.instructions,
                    "children": list(skill.children),
                    "input_contract": skill.input_contract,
                    "output_contract": skill.output_contract,
                }
                size = len(json.dumps(item, ensure_ascii=False))
                if result and used + size > character_budget:
                    break
                result.append(item)
                used += size
            return json_copy(result)
    ''',
)

write(
    ".continual/skills/root-task.json",
    r'''
    {
      "skill_id": "root-task",
      "version": 1,
      "description": "General task orientation, decomposition, execution, verification, and learning contract.",
      "triggers": ["*"],
      "instructions": "Restate the goal and immutable constraints, define observable success, decompose only as far as useful, execute through allowed tools or child skills, verify every success criterion, then record lessons after the task. Never replace missing evidence with confidence.",
      "children": ["verify-before-claim", "learn-after-task"],
      "input_contract": {"required": ["goal", "success_criteria", "constraints"]},
      "output_contract": {"required": ["result", "verification"]},
      "evidence_score": 0.5,
      "status": "active"
    }
    ''',
)

write(
    ".continual/skills/verify-before-claim.json",
    r'''
    {
      "skill_id": "verify-before-claim",
      "version": 1,
      "description": "Check observable criteria and preserve failure evidence before claiming completion.",
      "triggers": ["verify", "evaluation", "test", "evidence"],
      "instructions": "Map each success criterion to an observation or verifier artifact. Mark unknown or failed criteria false. Do not infer completion from effort, confidence, or a unit test unrelated to the requested outcome.",
      "children": [],
      "input_contract": {"required": ["success_criteria", "observations"]},
      "output_contract": {"required": ["criteria", "complete", "feedback"]},
      "evidence_score": 0.5,
      "status": "active"
    }
    ''',
)

write(
    ".continual/skills/learn-after-task.json",
    r'''
    {
      "skill_id": "learn-after-task",
      "version": 1,
      "description": "Create one bounded episode and improvement candidates after task execution.",
      "triggers": ["learn", "memory", "improve", "episode"],
      "instructions": "After completion or blocking, summarize what happened, preserve failures, extract reusable lessons, and propose candidates with narrow triggers. Consolidate memory once. The learning operation itself must not recursively trigger another learning operation.",
      "children": [],
      "input_contract": {"required": ["task", "result", "observations"]},
      "output_contract": {"required": ["summary", "lessons", "failures", "improvements"]},
      "evidence_score": 0.5,
      "status": "active"
    }
    ''',
)
