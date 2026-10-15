"""Learning extractor for analyzing agent sessions and extracting knowledge."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from openagent.core.memory import FactMemory, PatternMemory, PreferenceMemory
from openagent.core.session import Session
from openagent.provider.base import BaseProvider


@dataclass
class ExtractionResult:
    """Result of learning extraction from a session."""

    patterns: list[PatternMemory] = None
    preferences: list[PreferenceMemory] = None
    facts: list[FactMemory] = None

    def __post_init__(self) -> None:
        if self.patterns is None:
            self.patterns = []
        if self.preferences is None:
            self.preferences = []
        if self.facts is None:
            self.facts = []


class LearningExtractor:
    """Extract learnings from completed agent sessions using LLM analysis."""

    def __init__(self, provider: BaseProvider):
        self.provider = provider

    async def analyze_session(
        self,
        session: Session,
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> ExtractionResult:
        """
        Analyze a completed session and extract learnings.

        Args:
            session: The completed agent session to analyze
            project_id: Optional project ID for fact categorization
            session_id: Optional session ID for tracking source

        Returns:
            ExtractionResult containing patterns, preferences, and facts
        """
        if session_id is None:
            session_id = str(uuid.uuid4())

        # Serialize session messages for analysis
        session_text = self._serialize_session(session)

        # Use LLM to extract learnings via structured prompt
        extraction_json = await self._extract_with_llm(
            session_text, project_id or "default"
        )

        # Parse and convert to memory objects
        patterns = self._parse_patterns(extraction_json.get("patterns", []))
        preferences = self._parse_preferences(
            extraction_json.get("preferences", [])
        )
        facts = self._parse_facts(
            extraction_json.get("facts", []), session_id, project_id or "default"
        )

        return ExtractionResult(patterns=patterns, preferences=preferences, facts=facts)

    def _serialize_session(self, session: Session) -> str:
        """Convert session to text format for LLM analysis."""
        lines = []

        if session.system_prompt:
            lines.append(f"System Prompt:\n{session.system_prompt}\n")

        lines.append("Conversation History:\n")

        for msg in session._messages:
            if hasattr(msg, "role"):
                role = msg.role
            else:
                continue

            content = []
            if hasattr(msg, "content"):
                if isinstance(msg.content, list):
                    # Handle multimodal content
                    for block in msg.content:
                        if hasattr(block, "text"):
                            content.append(block.text)
                elif isinstance(msg.content, str):
                    content.append(msg.content)

            if content:
                lines.append(f"\n{role.upper()}:\n{''.join(content)}")

        return "\n".join(lines)

    async def _extract_with_llm(
        self, session_text: str, project_id: str
    ) -> dict[str, Any]:
        """Use LLM to extract structured learnings from session text."""

        prompt = f"""You are an AI learning analyst. Your task is to analyze a completed agent session and extract valuable knowledge for future reuse.

Analyze the following session and identify:

1. **PATTERNS**: Successful problem-solving approaches, code patterns, or techniques that worked well
2. **PREFERENCES**: User preferences revealed through their choices, feedback, or corrections
3. **FACTS**: Important project-specific information, decisions, or context

Session to analyze:
{session_text}

Output your analysis as a JSON object with this exact structure:
{{
  "patterns": [
    {{
      "task_description": "Brief description of the problem solved",
      "solution_summary": "Key approach that worked",
      "code_snippet": "Reusable code (optional, omit if not applicable)",
      "tags": ["tag1", "tag2"],
      "outcome": "success"
    }}
  ],
  "preferences": [
    {{
      "category": "coding_style|tools|formatting|architecture",
      "preference": "Clear statement of the preference",
      "context": "When this applies (optional)"
    }}
  ],
  "facts": [
    {{
      "fact": "Important project fact or decision",
      "category": "testing|dependencies|architecture|conventions"
    }}
  ]
}}

Guidelines:
- Only extract genuinely useful, reusable knowledge
- For patterns: focus on non-obvious solutions or clever approaches
- For preferences: only include explicit user choices or strong signals
- For facts: capture project-specific context that would help future sessions
- Be concise but specific in descriptions
- If nothing notable was learned, return empty arrays

Return ONLY the JSON object, no other text."""

        response = await self.provider.chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="You are a helpful learning analyst. Output valid JSON only.",
        )

        # Extract JSON from response
        content = ""
        if hasattr(response, "content"):
            if isinstance(response.content, list):
                for block in response.content:
                    if hasattr(block, "text"):
                        content += block.text
            elif isinstance(response.content, str):
                content = response.content

        # Try to parse JSON
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Try to extract JSON from markdown code blocks
            match = content.find("```json")
            if match != -1:
                end = content.find("```", match + 7)
                if end != -1:
                    try:
                        return json.loads(content[match + 7 : end].strip())
                    except json.JSONDecodeError:
                        pass
            match = content.find("{")
            if match != -1:
                try:
                    return json.loads(content[match:])
                except json.JSONDecodeError:
                    pass

            # Return empty result if parsing fails
            return {"patterns": [], "preferences": [], "facts": []}

    def _parse_patterns(self, data: list[dict[str, Any]]) -> list[PatternMemory]:
        """Parse pattern data into PatternMemory objects."""
        patterns = []
        for item in data:
            if not isinstance(item, dict):
                continue
            pattern = PatternMemory(
                id=str(uuid.uuid4()),
                task_description=item.get("task_description", ""),
                solution_summary=item.get("solution_summary", ""),
                code_snippet=item.get("code_snippet"),
                tags=item.get("tags", []),
                outcome=item.get("outcome", "success"),
            )
            patterns.append(pattern)
        return patterns

    def _parse_preferences(
        self, data: list[dict[str, Any]]
    ) -> list[PreferenceMemory]:
        """Parse preference data into PreferenceMemory objects."""
        preferences = []
        for item in data:
            if not isinstance(item, dict):
                continue
            pref = PreferenceMemory(
                id=str(uuid.uuid4()),
                category=item.get("category", "general"),
                preference=item.get("preference", ""),
                context=item.get("context"),
            )
            preferences.append(pref)
        return preferences

    def _parse_facts(
        self, data: list[dict[str, Any]], session_id: str, project_id: str
    ) -> list[FactMemory]:
        """Parse fact data into FactMemory objects."""
        facts = []
        for item in data:
            if not isinstance(item, dict):
                continue
            fact = FactMemory(
                id=str(uuid.uuid4()),
                fact=item.get("fact", ""),
                category=item.get("category", "general"),
                source_session_id=session_id,
            )
            facts.append(fact)
        return facts
