"""从项目内 SKILL.md 加载可路由的模型能力。"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re


SKILLS_DIR = Path(__file__).resolve().parent
_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class SkillLoadError(RuntimeError):
    """Skill 文件缺失或元数据不满足运行时约定。"""


@dataclass(frozen=True)
class AgentSkill:
    """工作流调用模型时需要的 Skill 元数据与指令。"""

    name: str
    description: str
    version: str
    instructions: str
    max_tokens: int


@lru_cache(maxsize=None)
def load_skill(name: str) -> AgentSkill:
    """按稳定名称读取一个项目 Skill；结果在当前进程内缓存。"""
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
    """列出当前项目内可供后续路由选择的全部 Skill。"""
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
    """解析本项目约定的简单 Skill 元数据，避免增加 YAML 运行依赖。"""
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
