"""LLM-based topic classification with fail-open behavior."""

from __future__ import annotations

import json
import logging

from openai import AsyncOpenAI

from memory_service.config import TopicDetectionConfig
from memory_service.models import TopicClassificationResult

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You classify whether a new message continues the current conversation topic "
    "or shifts to a different domain.\n\n"
    "Rules:\n"
    "- A topic shift means a MAJOR domain change (e.g., 'AI market' to 'climate policy')\n"
    "- Asking follow-up questions, requesting details, or refining the same subject is NOT a topic shift\n"
    "- Returning to a previously discussed domain IS a topic shift (creates a new topic segment)\n\n"
    "Respond ONLY with JSON, no other text:\n"
    '{"same_topic": true, "new_topic_label": null}\n'
    "or\n"
    '{"same_topic": false, "new_topic_label": "2-5 word label"}'
)

LABEL_PROMPT = """Generate a concise 2-5 word topic label for this message.

Respond ONLY with JSON:
{"label": "2-5 word label"}"""


class TopicDetector:
    """Detects topic shifts using a lightweight LLM classifier."""

    def __init__(self, config: TopicDetectionConfig) -> None:
        self._config = config
        self._client = AsyncOpenAI(
            api_key=config.llm.api_key or "dummy",
            base_url=config.llm.base_url,
        )

    async def classify(
        self,
        current_topic_label: str,
        recent_messages: list[dict[str, str]],
        new_message_content: str,
    ) -> TopicClassificationResult:
        """Classify whether a new message continues the current topic.

        Returns TopicClassificationResult. On any error, returns same_topic=True (fail-open).
        """
        try:
            tail = recent_messages[-self._config.max_history_messages :]
            history_text = "\n".join(f"  {m['role']}: {m['content'][:200]}" for m in tail)

            user_prompt = (
                f"Current topic: {current_topic_label}\n"
                f"Recent messages in this topic:\n{history_text}\n\n"
                f"New message: {new_message_content}\n\n"
                f"Is this the same topic or a different one?"
            )

            response = await self._client.chat.completions.create(
                model=self._config.llm.model,
                temperature=self._config.llm.temperature,
                max_tokens=self._config.llm.max_tokens,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            )

            text = response.choices[0].message.content or ""
            result = json.loads(text.strip())
            return TopicClassificationResult(
                same_topic=result.get("same_topic", True),
                new_topic_label=result.get("new_topic_label"),
            )

        except json.JSONDecodeError:
            logger.warning("Topic detector received malformed JSON — assuming same topic")
            return TopicClassificationResult(same_topic=True)
        except Exception:
            logger.warning("Topic detection failed — assuming same topic (fail-open)", exc_info=True)
            return TopicClassificationResult(same_topic=True)

    async def generate_label(self, content: str) -> str:
        """Generate a topic label for the first message in a session."""
        try:
            response = await self._client.chat.completions.create(
                model=self._config.llm.model,
                temperature=self._config.llm.temperature,
                max_tokens=30,
                messages=[
                    {"role": "system", "content": LABEL_PROMPT},
                    {"role": "user", "content": content[:500]},
                ],
            )
            text = response.choices[0].message.content or ""
            result = json.loads(text.strip())
            return result.get("label", content[:50])
        except Exception:
            logger.warning("Label generation failed — using content prefix")
            return content[:50]
