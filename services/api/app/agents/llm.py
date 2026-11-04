"""LLM provider abstraction. The demo provider is deterministic: the workflow detects it and uses templates, so the
application is fully functional without an API key. The model never receives credentials or tool access here: it
only rewrites an already-computed, evidence-backed draft.
"""
import httpx

from app.core.config import get_settings


class LLMError(Exception):
    pass


class LLMProvider:
    name = "base"
    is_demo = False
    label = ""

    def complete(self, system: str, user: str) -> str:  # pragma: no cover - interface
        raise NotImplementedError


class DemoProvider(LLMProvider):
    name = "demo"
    is_demo = True
    label = "Deterministic demo response (template-based, no LLM)"

    def complete(self, system: str, user: str) -> str:
        raise LLMError("The demo provider does not generate free text")


class AnthropicProvider(LLMProvider):
    name = "anthropic"
    url = "https://api.anthropic.com/v1/messages"  # fixed destination, not user-configurable

    def __init__(self) -> None:
        st = get_settings()
        self.model, self.key, self.timeout = st.llm_model, st.anthropic_api_key, st.llm_timeout_seconds
        self.label = f"LLM-assisted ({self.model}); facts and citations come from retrieved evidence"

    def complete(self, system: str, user: str) -> str:
        try:
            r = httpx.post(self.url, timeout=self.timeout,
                           headers={"x-api-key": self.key, "anthropic-version": "2023-06-01",
                                    "content-type": "application/json"},
                           json={"model": self.model, "max_tokens": 1500, "system": system,
                                 "messages": [{"role": "user", "content": user}]})
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM request failed: {type(exc).__name__}") from exc
        if r.status_code != 200:
            raise LLMError(f"LLM provider returned HTTP {r.status_code}")
        try:
            return "".join(b.get("text", "") for b in r.json()["content"] if b.get("type") == "text")
        except (KeyError, ValueError) as exc:
            raise LLMError("Unexpected LLM response shape") from exc


def get_provider() -> LLMProvider:
    st = get_settings()
    if st.llm_provider == "anthropic" and st.anthropic_api_key:
        return AnthropicProvider()
    return DemoProvider()
