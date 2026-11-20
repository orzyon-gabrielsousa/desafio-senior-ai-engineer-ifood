"""Output budgets must hold across the HTTP formats supported by the local proxy."""

import json
import os
import io
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from llama_cache import RequestHandler, generation_limits, generation_request, permits_cache


class GenerationBudgetTests(unittest.TestCase):
    def request(self, request, path="/v1/chat/completions"):
        return json.loads(generation_request(json.dumps(request).encode(), path,
                                             maximum=2048, default=1536))

    def test_unset_uses_default_and_preserves_tools_and_stream(self):
        original = {"messages": [{"role": "user", "content": "Conferir o prato"}], "stream": True,
                    "tools": [{"type": "function", "function": {"name": "conferir"}}],
                    "tool_choice": "auto", "cache_prompt": False}
        bounded = self.request(original)
        self.assertEqual(bounded.pop("max_tokens"), 1536)
        self.assertEqual(bounded, original)
        self.assertFalse(permits_cache(json.dumps(bounded).encode()))

    def test_client_smaller_limit_is_never_increased(self):
        for value in (0, 1, 128, 1536):
            with self.subTest(value=value):
                self.assertEqual(self.request({"max_tokens": value})["max_tokens"], value)

    def test_large_limit_and_conflicting_aliases_cannot_bypass_cap(self):
        bounded = self.request({"max_tokens": 65536, "max_completion_tokens": 32768, "n_predict": -1})
        self.assertEqual({bounded[k] for k in bounded}, {2048})
        bounded = self.request({"max_tokens": 100, "max_completion_tokens": 5000})
        self.assertEqual(bounded, {"max_tokens": 100, "max_completion_tokens": 100})

    def test_native_responses_and_messages_use_their_budget_field(self):
        for path, field in (("/completion", "n_predict"), ("/v1/responses", "max_output_tokens"),
                            ("/responses", "max_output_tokens"), ("/v1/messages", "max_tokens")):
            with self.subTest(path=path):
                self.assertEqual(self.request({field: 5000}, path)[field], 2048)
                self.assertEqual(self.request({}, path)[field], 1536)

    def test_unlimited_request_is_bounded_and_null_uses_default(self):
        self.assertEqual(self.request({"max_tokens": -1})["max_tokens"], 1536)
        self.assertEqual(self.request({"max_tokens": None})["max_tokens"], 1536)

    def test_invalid_json_or_token_type_is_rejected(self):
        for body in (b"{", b"[]", b"null", b'"text"', b'{"max_tokens":true}',
                     b'{"max_tokens":1.5}', b'{"max_tokens":"10"}'):
            with self.subTest(body=body), self.assertRaises(ValueError):
                generation_request(body, "/v1/chat/completions", maximum=2048, default=1536)

    def test_environment_cap_and_native_default_are_combined(self):
        with patch.dict(os.environ, {"LLAMA_MAX_OUTPUT_TOKENS": "2048"}):
            self.assertEqual(generation_limits(["--n-predict", "1536"]), (2048, 1536))
            self.assertEqual(generation_limits(["--n-predict", "4096"]), (2048, 2048))
            self.assertEqual(generation_limits(["--n-predict", "-1"]), (2048, 2048))

    def test_invalid_environment_uses_finite_default(self):
        for value in ("bad", "-1", "0"):
            with patch.dict(os.environ, {"LLAMA_MAX_OUTPUT_TOKENS": value}):
                self.assertEqual(generation_limits(["--n-predict", "1536"]), (2048, 1536))


class ProxyBudgetTests(unittest.TestCase):
    def route(self, body, path="/v1/chat/completions"):
        cache = SimpleNamespace(ready=threading.Event(), lock=threading.Lock(), pending=False,
                                last_generation=0.0)
        cache.ready.set()
        handler = RequestHandler.__new__(RequestHandler)
        handler.server = SimpleNamespace(cache=cache, max_output_tokens=2048, default_output_tokens=1536)
        handler.command, handler.path = "POST", path
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = io.BytesIO(body)
        handler.connection = SimpleNamespace(settimeout=lambda value: None)
        forwarded, rejected = [], []
        handler.forward = lambda data: forwarded.append(data) or True
        handler.send_json = lambda status, data: rejected.append((status, data))
        handler.route()
        return forwarded, rejected, cache

    def test_generation_is_bounded_before_forwarding_and_still_cached(self):
        forwarded, rejected, cache = self.route(b'{"max_tokens":100000,"stream":true}')
        self.assertEqual(rejected, [])
        self.assertEqual(json.loads(forwarded[0]), {"max_tokens": 2048, "stream": True})
        self.assertTrue(cache.pending)

    def test_malformed_generation_is_not_forwarded(self):
        forwarded, rejected, cache = self.route(b'{"max_tokens":"unlimited"}')
        self.assertEqual(forwarded, [])
        self.assertEqual(rejected[0][0], 400)
        self.assertFalse(cache.pending)

    def test_tokenization_request_is_not_rewritten(self):
        original = b'{"content":"frango","max_tokens":5000}'
        forwarded, rejected, _ = self.route(original, "/tokenize")
        self.assertEqual(forwarded, [original])
        self.assertEqual(rejected, [])


if __name__ == "__main__":
    unittest.main()
