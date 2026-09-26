"""Responsibility: Load routable model capabilities from project SKILL.md files.
Implementation: Validate skill names and required frontmatter, retain instruction text, and cache immutable skill objects per process.
Relationships: Workflow providers load project SKILL.md files through this module; prompt content is a runtime contract.

Directory:
- SkillLoadError: A skill file is missing or its metadata violates the runtime contract.
- AgentSkill: Skill metadata and instructions required by workflows invoking a model.
- load_skill: Read a project skill by stable name; cache the result within this process.
- list_skills: List all project skills available for subsequent routing.
- _split_document: Separate required frontmatter from the instruction body.
- _parse_frontmatter: Parse the project's simple skill metadata without adding a YAML runtime dependency.
- _frontmatter_pair: Validate and split one nonempty metadata key/value pair.
- _unquote: Remove matching outer quote characters from a metadata value.

Variable index:
- AgentSkill.description: Skill discovery description.
- AgentSkill.instructions: Runtime instruction body loaded without translation.
- AgentSkill.max_tokens: Configured model output budget.
- AgentSkill.name: Stable skill identifier.
- AgentSkill.version: Prompt version used for cache separation.
- SKILLS_DIR: Root directory of project runtime skills.
- _SKILL_NAME: Allowed stable lowercase skill-name syntax.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re


SKILLS_DIR = Path(__file__).resolve().parent
_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class SkillLoadError(RuntimeError):
    """A skill file is missing or its metadata violates the runtime contract."""


@dataclass(frozen=True)
class AgentSkill:
    """Skill metadata and instructions required by workflows invoking a model."""

    name: str
    description: str
    version: str
    instructions: str
    max_tokens: int


@lru_cache(maxsize=None)
def load_skill(name: str) -> AgentSkill:
    """Read a project skill by stable name; cache the result within this process."""
    if not isinstance(name, str) or _SKILL_NAME.fullmatch(name) is None:
        raise SkillLoadError("Skill names may contain only lowercase letters, digits, and hyphens.")

    path = SKILLS_DIR / name / "SKILL.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise SkillLoadError(f"Cannot read Skill: {name}.") from error

    frontmatter, instructions = _split_document(text, name)
    metadata = _parse_frontmatter(frontmatter, name)
    if metadata.get("name") != name:
        raise SkillLoadError(f"Skill directory does not match metadata name: {name}.")

    description = metadata.get("description", "").strip()
    version = metadata.get("metadata.version", "").strip()
    token_text = metadata.get("metadata.max-tokens", "").strip()
    if not description or not version or not instructions:
        raise SkillLoadError(f"Skill is missing description, version, or instructions: {name}.")
    try:
        max_tokens = int(token_text)
    except ValueError:
        raise SkillLoadError(f"Skill max-tokens must be a positive integer: {name}.") from None
    if max_tokens <= 0:
        raise SkillLoadError(f"Skill max-tokens must be a positive integer: {name}.")

    return AgentSkill(
        name=name,
        description=description,
        version=version,
        instructions=instructions,
        max_tokens=max_tokens,
    )


def list_skills() -> list[AgentSkill]:
    """List all project skills available for subsequent routing."""
    names = sorted(
        path.parent.name
        for path in SKILLS_DIR.glob("*/SKILL.md")
        if _SKILL_NAME.fullmatch(path.parent.name)
    )
    return [load_skill(name) for name in names]


def _split_document(text: str, name: str) -> tuple[list[str], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillLoadError(f"Skill is missing YAML frontmatter: {name}.")
    try:
        end = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration:
        raise SkillLoadError(f"Skill YAML frontmatter is not terminated: {name}.") from None
    return lines[1:end], "\n".join(lines[end + 1 :]).strip()


def _parse_frontmatter(lines: list[str], name: str) -> dict[str, str]:
    """Parse the project's simple skill metadata without adding a YAML runtime dependency."""
    result: dict[str, str] = {}
    section: str | None = None
    for raw_line in lines:
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if raw_line == "metadata:":
            section = "metadata"
            continue
        if raw_line.startswith("  ") and section == "metadata":
            key, value = _frontmatter_pair(raw_line.strip(), name)
            result[f"metadata.{key}"] = _unquote(value)
            continue
        if raw_line[:1].isspace():
            raise SkillLoadError(f"Skill frontmatter has unsupported indentation: {name}.")
        section = None
        key, value = _frontmatter_pair(raw_line, name)
        result[key] = _unquote(value)
    return result


def _frontmatter_pair(line: str, name: str) -> tuple[str, str]:
    if ":" not in line:
        raise SkillLoadError(f"Skill frontmatter is invalid: {name}.")
    key, value = line.split(":", 1)
    if not key.strip() or not value.strip():
        raise SkillLoadError(f"Skill frontmatter contains an empty value: {name}.")
    return key.strip(), value.strip()


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value
