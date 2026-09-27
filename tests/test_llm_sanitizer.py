"""Unit Tests for SafeLLMClient and Prompt Sanitization."""
from mutation.llm_client import sanitize_text, SafeLLMClient


def test_sanitize_text():
    dirty = "We plan to penetration test the charger using attack vectors and hack the SECC firmware"
    clean = sanitize_text(dirty)
    assert "penetration test" not in clean
    assert "attack vectors" not in clean
    assert "hack" not in clean
    assert "robustness test" in clean
    assert "test scenarios" in clean


def test_safe_llm_client_initialization():
    client = SafeLLMClient(primary_model="claude-opus-5.5", fallback_models=["gemini-3.1-pro-preview"])
    assert client.primary_model == "claude-opus-5.5"
    assert "gemini-3.1-pro-preview" in client.fallback_models
