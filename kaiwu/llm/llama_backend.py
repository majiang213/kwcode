"""
LLM backend: llama.cpp wrapper with Ollama-compatible HTTP fallback.
Provides a unified interface for all expert/gate LLM calls.
"""

import json
import logging
import re
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Try importing llama_cpp; if unavailable, fall back to HTTP-only mode
try:
    from llama_cpp import Llama, LlamaGrammar
    HAS_LLAMA_CPP = True
except ImportError:
    HAS_LLAMA_CPP = False
    Llama = None
    LlamaGrammar = None


class LLMBackend:
    """Unified LLM interface supporting llama.cpp native and Ollama HTTP."""

    # Models known to use thinking/reasoning tokens that consume num_predict budget
    REASONING_MODELS = {"deepseek-r1", "qwq", "qwen3", "gemma4"}  # thinking/reasoning models
    # Multiplier for num_predict when using reasoning models
    REASONING_TOKEN_MULTIPLIER = 8

    def __init__(
        self,
        model_path: Optional[str] = None,
        ollama_url: str = "http://localhost:11434",
        ollama_model: str = "qwen3-8b",
        n_ctx: int = 8192,
        n_gpu_layers: int = -1,
        verbose: bool = False,
    ):
        self.ollama_url = ollama_url.rstrip("/")
        self.ollama_model = ollama_model
        self.verbose = verbose
        self._llm: Optional[object] = None
        self._mode = "none"
        self._is_reasoning = self._detect_reasoning_model(ollama_model)

        # Prefer native llama.cpp if model_path provided and library available
        if model_path and HAS_LLAMA_CPP:
            try:
                self._llm = Llama(
                    model_path=model_path,
                    n_ctx=n_ctx,
                    n_gpu_layers=n_gpu_layers,
                    verbose=verbose,
                )
                self._mode = "llama_cpp"
                logger.info("LLM backend: llama.cpp native (model=%s)", model_path)
                return
            except Exception as e:
                logger.warning("llama.cpp init failed: %s, falling back to Ollama", e)

        # Fallback: Ollama HTTP API
        self._mode = "ollama"
        logger.info("LLM backend: Ollama HTTP (%s, model=%s)", self.ollama_url, self.ollama_model)

    @property
    def mode(self) -> str:
        return self._mode

    def generate(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 1024,
        temperature: float = 0.0,
        stop: Optional[list[str]] = None,
        grammar_str: Optional[str] = None,
    ) -> str:
        """Generate text completion. Returns raw string output."""
        if self._mode == "llama_cpp":
            return self._generate_native(prompt, system, max_tokens, temperature, stop, grammar_str)
        return self._generate_ollama(prompt, system, max_tokens, temperature, stop)

    def _generate_native(
        self, prompt: str, system: str, max_tokens: int,
        temperature: float, stop: Optional[list[str]], grammar_str: Optional[str],
    ) -> str:
        kwargs: dict = {
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if stop:
            kwargs["stop"] = stop
        if grammar_str and LlamaGrammar:
            kwargs["grammar"] = LlamaGrammar.from_string(grammar_str)

        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        resp = self._llm(full_prompt, **kwargs)
        return resp["choices"][0]["text"].strip()

    def _generate_ollama(
        self, prompt: str, system: str, max_tokens: int,
        temperature: float, stop: Optional[list[str]],
    ) -> str:
        # Always use /api/chat for Ollama — reasoning models (deepseek-r1 etc.)
        # consume num_predict budget with thinking tokens in /api/generate,
        # returning empty responses. /api/chat separates thinking from content.
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self._chat_ollama(messages, max_tokens, temperature, stop)

    @staticmethod
    def _strip_thinking(text: str) -> str:
        """Strip <think>...</think> blocks from reasoning models (e.g. deepseek-r1)."""
        cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        return cleaned if cleaned else text

    def chat(
        self,
        messages: list[dict],
        max_tokens: int = 1024,
        temperature: float = 0.0,
        stop: Optional[list[str]] = None,
        grammar_str: Optional[str] = None,
    ) -> str:
        """Chat-style completion (for Ollama /api/chat or converted to prompt for llama.cpp)."""
        if self._mode == "ollama":
            return self._chat_ollama(messages, max_tokens, temperature, stop)

        # Convert messages to single prompt for llama.cpp
        system = ""
        prompt_parts = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                system = content
            elif role == "user":
                prompt_parts.append(f"User: {content}")
            elif role == "assistant":
                prompt_parts.append(f"Assistant: {content}")
        prompt_parts.append("Assistant:")
        prompt = "\n".join(prompt_parts)
        return self._generate_native(prompt, system, max_tokens, temperature, stop, grammar_str)

    def _chat_ollama(
        self, messages: list[dict], max_tokens: int,
        temperature: float, stop: Optional[list[str]],
    ) -> str:
        # Reasoning models need extra token budget for thinking
        effective_tokens = max_tokens
        effective_temp = temperature
        if self._is_reasoning:
            effective_tokens = max_tokens * self.REASONING_TOKEN_MULTIPLIER
            # Reasoning models at temp=0 can produce empty content in Ollama
            # due to all tokens being consumed by thinking. Use small temp to
            # avoid cached empty results while keeping output near-deterministic.
            if temperature == 0.0:
                effective_temp = 0.01

        payload = {
            "model": self.ollama_model,
            "messages": messages,
            "stream": False,
            "options": {
                "num_predict": effective_tokens,
                "temperature": effective_temp,
            },
        }
        # Don't pass stop sequences to reasoning models — they can trigger
        # inside <think> blocks and truncate before any content is generated.
        if stop and not self._is_reasoning:
            payload["options"]["stop"] = stop

        try:
            resp = httpx.post(
                f"{self.ollama_url}/api/chat",
                json=payload,
                timeout=180.0,
            )
            resp.raise_for_status()
            raw = resp.json()["message"]["content"].strip()
            return self._strip_thinking(raw)
        except Exception as e:
            logger.error("Ollama chat failed: %s", e)
            raise

    @classmethod
    def _detect_reasoning_model(cls, model_name: str) -> bool:
        """Detect if model uses thinking/reasoning tokens."""
        name_lower = model_name.lower().split(":")[0]  # strip tag like :8b
        return any(r in name_lower for r in cls.REASONING_MODELS)
