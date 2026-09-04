from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from string import Template
from typing import Dict, Tuple

DEFAULT_PROMPT_TEMPLATE_DIR = (
    Path(__file__).resolve().parents[2] / "docs" / "prompts"
)

PROMPT_ID_QA_ANSWER = "qa_answer"
PROMPT_ID_SUPPLEMENT_PROPOSAL = "supplement_proposal"
PROMPT_ID_SCREENSHOT_BODY_REPAIR = "screenshot_body_repair"
PROMPT_ID_SCREENSHOT_TITLE_REPAIR = "screenshot_title_repair"
PROMPT_ID_SCREENSHOT_SUMMARY_REPAIR = "screenshot_summary_repair"

_PROMPT_FILE_MAP = {
    PROMPT_ID_QA_ANSWER: "qa_answer_v2.md",
    PROMPT_ID_SUPPLEMENT_PROPOSAL: "supplement_proposal_v7.md",
    PROMPT_ID_SCREENSHOT_BODY_REPAIR: "screenshot_body_repair_v2.md",
    PROMPT_ID_SCREENSHOT_TITLE_REPAIR: "screenshot_title_repair_v3.md",
    PROMPT_ID_SCREENSHOT_SUMMARY_REPAIR: "screenshot_summary_repair_v2.md",
}


class PromptTemplateLoaderError(Exception):
    pass


@dataclass(frozen=True)
class PromptTemplateBundle:
    prompt_id: str
    version: str
    system_template: str
    user_template: str
    path: Path

    def render_messages(self, *, variables: Dict[str, str]) -> Tuple[str, str]:
        try:
            system_message = Template(self.system_template).substitute(variables).strip()
            user_message = Template(self.user_template).substitute(variables).strip()
        except KeyError as exc:
            raise PromptTemplateLoaderError(
                f"Prompt template variable is missing: {exc.args[0]}"
            ) from exc

        return system_message, user_message


class PromptTemplateLoader:
    def __init__(self, *, prompt_dir: Path = DEFAULT_PROMPT_TEMPLATE_DIR) -> None:
        self._prompt_dir = prompt_dir

    def load_bundle(self, prompt_id: str) -> PromptTemplateBundle:
        normalized_prompt_id = prompt_id.strip()
        if not normalized_prompt_id:
            raise PromptTemplateLoaderError("prompt_id must not be empty")

        try:
            template_name = _PROMPT_FILE_MAP[normalized_prompt_id]
        except KeyError as exc:
            raise PromptTemplateLoaderError(
                f"Unsupported prompt_id: {normalized_prompt_id}"
            ) from exc

        template_path = (self._prompt_dir / template_name).resolve()
        prompt_dir = self._prompt_dir.resolve()
        if not template_path.is_relative_to(prompt_dir):
            raise PromptTemplateLoaderError(
                f"Prompt path escapes prompt directory: {template_path}"
            )

        return self._load_bundle_from_path(template_path)

    @lru_cache(maxsize=None)
    def _load_bundle_from_path(self, template_path: Path) -> PromptTemplateBundle:
        try:
            raw_text = template_path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise PromptTemplateLoaderError(
                f"Prompt template file does not exist: {template_path}"
            ) from exc

        metadata, body = self._parse_frontmatter(raw_text, template_path=template_path)
        prompt_id = metadata.get("prompt_id")
        version = metadata.get("version")
        if not prompt_id:
            raise PromptTemplateLoaderError(
                f"Prompt template metadata missing prompt_id: {template_path}"
            )
        if not version:
            raise PromptTemplateLoaderError(
                f"Prompt template metadata missing version: {template_path}"
            )

        sections = self._parse_sections(body, template_path=template_path)
        system_template = sections.get("System")
        user_template = sections.get("User")
        if not system_template or not user_template:
            raise PromptTemplateLoaderError(
                f"Prompt template must define ## System and ## User: {template_path}"
            )

        return PromptTemplateBundle(
            prompt_id=prompt_id,
            version=version,
            system_template=system_template,
            user_template=user_template,
            path=template_path,
        )

    def _parse_frontmatter(
        self,
        raw_text: str,
        *,
        template_path: Path,
    ) -> Tuple[Dict[str, str], str]:
        lines = raw_text.splitlines()
        if not lines or lines[0].strip() != "---":
            raise PromptTemplateLoaderError(
                f"Prompt template must start with frontmatter delimiter: {template_path}"
            )

        metadata: Dict[str, str] = {}
        line_index = 1
        while line_index < len(lines):
            line = lines[line_index]
            if line.strip() == "---":
                break
            if line.strip():
                key, separator, value = line.partition(":")
                if not separator:
                    raise PromptTemplateLoaderError(
                        f"Invalid prompt metadata line: {line.strip()}"
                    )
                metadata[key.strip()] = value.strip()
            line_index += 1

        if line_index >= len(lines) or lines[line_index].strip() != "---":
            raise PromptTemplateLoaderError(
                f"Prompt template frontmatter is not closed: {template_path}"
            )

        body = "\n".join(lines[line_index + 1 :]).strip()
        if not body:
            raise PromptTemplateLoaderError(
                f"Prompt template body is empty: {template_path}"
            )
        return metadata, body

    def _parse_sections(
        self,
        body: str,
        *,
        template_path: Path,
    ) -> Dict[str, str]:
        parts = re.split(r"^## (System|User)\s*$", body, flags=re.MULTILINE)
        if len(parts) < 5:
            raise PromptTemplateLoaderError(
                f"Prompt template sections are invalid: {template_path}"
            )

        sections: Dict[str, str] = {}
        for index in range(1, len(parts), 2):
            if index + 1 >= len(parts):
                break
            section_name = parts[index].strip()
            section_body = parts[index + 1].strip()
            if section_body:
                sections[section_name] = section_body
        return sections
