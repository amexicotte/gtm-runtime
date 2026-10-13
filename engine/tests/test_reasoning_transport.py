"""Bounded recovery for OpenRouter-compatible response-shape failures."""
import json
import os
import sys
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.reasoning import _complete  # noqa: E402


def response(content):
    value = Mock(ok=True, status_code=200, text="")
    value.json.return_value = {
        "id": "generation", "model": "deepseek/deepseek-v4-pro",
        "choices": [{"finish_reason": "stop", "message": {"content": content}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    return value


def test_text_part_content_is_normalized_without_retry():
    result_json = json.dumps({"relevant": False})
    with patch("runtime.reasoning.requests.post",
               return_value=response([{"type": "text", "text": result_json}])) as post:
        result = _complete("system", "user")
    assert result["relevant"] is False
    assert result["_audit"]["provider_attempts"] == 1
    assert post.call_count == 1


def test_empty_provider_content_gets_one_bounded_retry():
    with patch("runtime.reasoning.requests.post", side_effect=[
            response(None), response(json.dumps({"relevant": True}))]) as post:
        result = _complete("system", "user")
    assert result["relevant"] is True
    assert result["_audit"]["provider_attempts"] == 2
    assert post.call_count == 2


if __name__ == "__main__":
    tests = [value for key, value in sorted(globals().items())
             if key.startswith("test_") and callable(value)]
    for test in tests:
        test(); print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
