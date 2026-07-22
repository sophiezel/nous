"""LLM client — thin wrapper around OpenAI-compatible DeepSeek API.

Usage:
    from nous.core.llm_client import LLMClient
    client = LLMClient()
    result = client.analyze("分析今日市场走势...")
"""

from __future__ import annotations

import os
from typing import Any

from openai import OpenAI
from pydantic import BaseModel


class LLMConfig(BaseModel):
    """LLM client configuration."""

    provider: str = "deepseek"
    model: str = "deepseek-v4-pro"
    api_key_env: str = "DEEPSEEK_API_KEY"
    base_url: str = "https://api.deepseek.com/v1"
    temperature: float = 0.1
    max_tokens: int = 4096
    timeout: int = 30


class LLMResponse(BaseModel):
    """Structured LLM response."""

    content: str
    model: str
    usage: dict[str, int] = {}


class LLMClient:
    """Thin wrapper around DeepSeek OpenAI-compatible API.

    Not a multi-turn agent — single-shot analysis calls only.
    For complex multi-step workflows, use the CLI or orchestration layer.
    """

    def __init__(self, config: LLMConfig | None = None):
        """Initialize the LLM client.

        Args:
            config: Optional config override. If None, loads from ``core.config.config``.
        """
        if config is None:
            try:
                from nous.core.config import config as app_config
                config = LLMConfig(
                    provider=app_config.llm.provider,
                    model=app_config.llm.model,
                    api_key_env=app_config.llm.api_key_env,
                    temperature=app_config.llm.temperature,
                    max_tokens=app_config.llm.max_tokens,
                    timeout=app_config.llm.timeout,
                )
            except Exception:
                config = LLMConfig()

        self.config = config
        self._api_key = os.environ.get(config.api_key_env, "")
        self._client: OpenAI | None = None

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(
                base_url=self.config.base_url,
                api_key=self._api_key,
                timeout=self.config.timeout,
            )
        return self._client

    def chat(
        self,
        messages: list[dict[str, str]],
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Send a chat completion request.

        Args:
            messages: List of {"role": "...", "content": "..."} dicts.
            system: Optional system prompt.
            temperature: Override default temperature.
            max_tokens: Override default max_tokens.

        Returns:
            LLMResponse with content and usage stats.
        """
        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)

        response = self.client.chat.completions.create(
            model=self.config.model,
            messages=full_messages,
            temperature=temperature or self.config.temperature,
            max_tokens=max_tokens or self.config.max_tokens,
        )

        choice = response.choices[0]
        return LLMResponse(
            content=choice.message.content or "",
            model=response.model,
            usage={
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                "total_tokens": response.usage.total_tokens if response.usage else 0,
            },
        )

    def analyze(self, prompt: str, system: str | None = None) -> str:
        """Convenience method: send a prompt and return text content.

        Args:
            prompt: The user prompt to analyze.
            system: Optional system instruction.

        Returns:
            The model's text response.
        """
        response = self.chat(
            messages=[{"role": "user", "content": prompt}],
            system=system,
        )
        return response.content
