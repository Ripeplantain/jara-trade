"""AI assistant: an OpenRouter-backed chat that reads account data and can only propose trades."""
from .client import AssistantError, LLMClient, OpenRouterClient, TextDelta, ToolCall, TurnEnd
from .config import AssistantSettings
from .routes import create_assistant_router
from .tools import AssistantTools

__all__ = [
    "AssistantError",
    "AssistantSettings",
    "AssistantTools",
    "LLMClient",
    "OpenRouterClient",
    "TextDelta",
    "ToolCall",
    "TurnEnd",
    "create_assistant_router",
]
