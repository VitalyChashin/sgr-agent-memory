"""Rolling memory buffer — splits conversation, summarizes older history, compacts context."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import tiktoken

from sgr_agent_core.memory.prompts import ROLLING_SUMMARY_SYSTEM_PROMPT

if TYPE_CHECKING:
    from openai import AsyncOpenAI

    from sgr_agent_core.memory.config import RollingSummaryConfig

logger = logging.getLogger(__name__)

# Approximate overhead per message for role tokens
_ROLE_OVERHEAD_TOKENS = 4
# Tokens reserved for the "[truncated]" suffix marker
_TRUNCATION_MARKER_TOKENS = 5
# Encoding used for approximate token counting (model-agnostic)
_ENCODING_NAME = "cl100k_base"
# Prefix for the injected summary message
SUMMARY_MESSAGE_PREFIX = "[Conversation history summary]: "


def _extract_text_content(content: Any) -> str:
    """Extract plain text from a message content field.

    Handles ``str``, multi-part ``list[dict]`` (OpenAI format), and ``None``.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"
        )
    return str(content)


class RollingSummaryBuffer:
    """Token-bounded rolling memory that splits, summarizes, and compacts conversation context.

    Created per agent invocation. Stateless — no data persists across calls.
    """

    def __init__(
        self,
        config: RollingSummaryConfig,
        client: AsyncOpenAI,
        agent_model: str,
    ) -> None:
        self.config = config
        self._client = client
        self._model = config.summarization_model or agent_model
        self._encoding = tiktoken.get_encoding(_ENCODING_NAME)

    def _count_tokens(self, text: str) -> int:
        """Return approximate token count for *text*."""
        return len(self._encoding.encode(text))

    def split_conversation(
        self,
        messages: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        """Split messages into system, older history, and recent window.

        Returns ``(system_messages, older_history, recent_window)`` where
        ``recent_window`` contains the newest turns fitting within the
        configured token budget.  System messages are separated and passed
        through independently.
        """
        max_tokens = self.config.max_tokens_to_summarize

        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        if not non_system:
            return system_msgs, [], []

        # Walk from newest to oldest, accumulate tokens for the recent window
        recent_window: list[dict[str, Any]] = []
        total_tokens = 0

        for msg in reversed(non_system):
            content = _extract_text_content(msg.get("content"))
            msg_tokens = self._count_tokens(content) + _ROLE_OVERHEAD_TOKENS

            if total_tokens + msg_tokens > max_tokens and recent_window:
                break

            recent_window.append(msg)
            total_tokens += msg_tokens

        recent_window.reverse()

        # If single message exceeds budget, truncate it
        if len(recent_window) == 1 and total_tokens > max_tokens:
            original = recent_window[0]
            content = _extract_text_content(original.get("content"))
            # Pre-truncate by characters to limit tokenization cost
            char_limit = max_tokens * 6
            content = content[:char_limit]
            target_tokens = max_tokens - _ROLE_OVERHEAD_TOKENS - _TRUNCATION_MARKER_TOKENS
            tokens = self._encoding.encode(content)
            truncated_text = self._encoding.decode(tokens[:target_tokens]) + " [truncated]"
            recent_window[0] = {**original, "content": truncated_text}

        # Older history = everything not in the recent window
        recent_count = len(recent_window)
        older_history = non_system[: len(non_system) - recent_count] if recent_count < len(non_system) else []

        return system_msgs, older_history, recent_window

    def _render_messages(self, messages: list[dict[str, Any]]) -> str:
        """Render messages as text for the summarizer prompt.

        Escapes ``</turn>`` in content to prevent XML tag boundary injection.
        """
        parts: list[str] = []
        for msg in messages:
            role = (msg.get("role") or "unknown").upper()
            content = _extract_text_content(msg.get("content"))
            content = content.replace("</turn>", "&lt;/turn&gt;")
            parts.append(f'<turn role="{role}">\n{content}\n</turn>')
        return "\n\n".join(parts)

    def _cap_summarizer_input(self, text: str, max_input_tokens: int = 12000) -> str:
        """Truncate the summarizer input to fit within a reasonable token budget."""
        token_count = self._count_tokens(text)
        if token_count <= max_input_tokens:
            return text
        # Rough char truncation then precise token truncation
        char_limit = max_input_tokens * 4
        text = text[:char_limit]
        tokens = self._encoding.encode(text)
        return self._encoding.decode(tokens[:max_input_tokens])

    async def summarize(self, older_history: list[dict[str, Any]]) -> str | None:
        """Summarize older conversation history.

        Returns the summary string or ``None`` on failure (fail-open).
        """
        if not older_history:
            return None

        try:
            user_prompt = self._render_messages(older_history)
            user_prompt = self._cap_summarizer_input(user_prompt)

            response = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": ROLLING_SUMMARY_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.2,
                    max_tokens=300,
                    stream=False,
                ),
                timeout=self.config.summarization_timeout_s,
            )

            content = response.choices[0].message.content if response.choices else None
            if not content or not content.strip():
                logger.warning("Rolling summary returned empty content")
                return None
            return content.strip()

        except asyncio.TimeoutError:
            logger.warning("Rolling summary timed out after %.1fs", self.config.summarization_timeout_s)
            return None
        except Exception as exc:
            logger.warning("Rolling summary generation failed: %s", exc)
            return None

    @staticmethod
    def compact_messages(
        system_msgs: list[dict[str, Any]],
        summary_text: str,
        recent_window: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Build rewritten message list: ``[system] → [summary] → [recent]``."""
        summary_msg = {"role": "system", "content": f"{SUMMARY_MESSAGE_PREFIX}{summary_text}"}
        return [*system_msgs, summary_msg, *recent_window]
