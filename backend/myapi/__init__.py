"""MyApi custom HTTP channel (OpenAI-compatible relays)."""

from backend.myapi.client import (
    bake_prompt_extras,
    generate_image,
    generate_text,
    generate_video,
    run_for_model,
    test_connection,
)

__all__ = [
    "bake_prompt_extras",
    "generate_image",
    "generate_text",
    "generate_video",
    "run_for_model",
    "test_connection",
]
