"""Assemble the system-message list for a project chat (spec §3).

The two override modes are independent fields. Each decides only whether
the corresponding main-brain value is prepended. Four combos are valid:

  append   / append     -> main.instructions, main.prompt, project.instructions, project.prompt
  override / append     -> main.prompt, project.instructions, project.prompt
  append   / override   -> main.instructions, project.instructions, project.prompt
  override / override   -> project.instructions, project.prompt
"""
from __future__ import annotations

from typing import List, Optional


def assemble_system_messages(
    *,
    main_instructions: Optional[str],
    main_prompt: Optional[str],
    project_instructions: Optional[str],
    project_prompt: Optional[str],
    instructions_override_mode: str,
    prompt_override_mode: str,
) -> List[dict]:
    msgs: List[dict] = []
    if instructions_override_mode == "append" and main_instructions:
        msgs.append({"role": "system", "text": main_instructions})
    if prompt_override_mode == "append" and main_prompt:
        msgs.append({"role": "system", "text": main_prompt})
    if project_instructions:
        msgs.append({"role": "system", "text": project_instructions})
    if project_prompt:
        msgs.append({"role": "system", "text": project_prompt})
    return msgs