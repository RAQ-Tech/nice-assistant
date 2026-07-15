import io
import json
import threading
import unittest
import urllib.error

from app.ollama_provider import OllamaChatProvider
from app.provider_contracts import CancellationToken, ChatRequest, ProviderError


class FakeResponse:
    def __init__(self, chunks, headers=None):
        self.lines = iter(chunks)
        self.headers = headers or {}
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def read(self):
        return b"".join(self.lines)

    def readline(self):
        return next(self.lines, b"")

    def close(self):
        self.closed = True


class OllamaProviderTests(unittest.TestCase):
    def request(self):
        return ChatRequest(model="fake", messages=[{"role": "user", "content": "hello"}], timeout_seconds=1)

    def test_stream_parses_deltas_and_completion_metadata(self):
        response = FakeResponse(
            [
                b'{"message":{"content":"one "},"done":false}\n',
                b'{"message":{"content":"two"},"done":true,"done_reason":"stop","eval_count":2}\n',
            ]
        )
        provider = OllamaChatProvider("http://ollama", opener=lambda *_args, **_kwargs: response)
        deltas = list(provider.stream(self.request(), CancellationToken()))
        self.assertEqual("".join(delta.text for delta in deltas), "one two")
        self.assertEqual(deltas[-1].finish_reason, "stop")
        self.assertEqual(deltas[-1].metadata["eval_count"], 2)
        self.assertTrue(response.closed)

    def test_stream_sends_tool_schema_and_parses_tool_calls(self):
        captured = {}
        response = FakeResponse(
            [
                b'{"message":{"content":"","tool_calls":[{"id":"call-1","function":{"name":"generate_image","arguments":{"prompt":"a garden"}}}]},"done":true}\n',
            ]
        )

        def opener(request, **_kwargs):
            captured.update(json.loads(request.data.decode("utf-8")))
            return response

        request = ChatRequest(
            model="fake",
            messages=[{"role": "user", "content": "show a garden"}],
            response_format={"type": "object", "properties": {"title": {"type": "string"}}},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "generate_image",
                        "parameters": {"type": "object"},
                    },
                }
            ],
        )
        deltas = list(OllamaChatProvider("http://ollama", opener=opener).stream(request, CancellationToken()))
        self.assertEqual(captured["tools"], request.tools)
        self.assertEqual(captured["format"], request.response_format)
        self.assertEqual(deltas[0].tool_calls[0].name, "generate_image")
        self.assertEqual(deltas[0].tool_calls[0].arguments, {"prompt": "a garden"})
        self.assertEqual(deltas[0].tool_calls[0].call_id, "call-1")

    def test_invalid_tool_arguments_are_normalized(self):
        response = FakeResponse(
            [
                b'{"message":{"tool_calls":[{"function":{"name":"generate_image","arguments":"not-json"}}]},"done":true}\n',
            ]
        )
        provider = OllamaChatProvider("http://ollama", opener=lambda *_args, **_kwargs: response)
        with self.assertRaises(ProviderError) as raised:
            list(provider.stream(self.request(), CancellationToken()))
        self.assertEqual(raised.exception.code, "invalid_tool_call")

    def test_model_context_uses_show_metadata_and_cache(self):
        calls = []

        def opener(request, **_kwargs):
            calls.append(request)
            return FakeResponse([json.dumps({"model_info": {"fake.context_length": 32768}}).encode()])

        provider = OllamaChatProvider("http://ollama", opener=opener)
        first = provider.model_context("fake")
        second = provider.model_context("fake")
        self.assertEqual(first.max_context_tokens, 32768)
        self.assertEqual(first.source, "ollama_api_show")
        self.assertEqual(second, first)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].full_url, "http://ollama/api/show")

    def test_midstream_error_and_malformed_frame_are_normalized(self):
        for frame, code in ((b'{"error":"boom"}\n', "stream_error"), (b"not-json\n", "invalid_stream")):
            provider = OllamaChatProvider("http://ollama", opener=lambda *_args, **_kwargs: FakeResponse([frame]))
            with self.assertRaises(ProviderError) as raised:
                list(provider.stream(self.request(), CancellationToken()))
            self.assertEqual(raised.exception.code, code)

    def test_incomplete_stream_http_error_and_unavailable_are_normalized(self):
        provider = OllamaChatProvider("http://ollama", opener=lambda *_args, **_kwargs: FakeResponse([]))
        with self.assertRaises(ProviderError) as raised:
            list(provider.stream(self.request(), CancellationToken()))
        self.assertEqual(raised.exception.code, "incomplete_stream")

        error = urllib.error.HTTPError("http://ollama/api/chat", 503, "down", {"x-request-id": "req_1"}, io.BytesIO())
        provider = OllamaChatProvider("http://ollama", opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(error))
        with self.assertRaises(ProviderError) as raised:
            list(provider.stream(self.request(), CancellationToken()))
        self.assertEqual(raised.exception.code, "http_503")
        self.assertTrue(raised.exception.retryable)

        provider = OllamaChatProvider(
            "http://ollama", opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("down"))
        )
        with self.assertRaises(ProviderError) as raised:
            list(provider.stream(self.request(), CancellationToken()))
        self.assertEqual(raised.exception.code, "unavailable")

    def test_models_health_generate_and_pre_cancel(self):
        tags = FakeResponse([json.dumps({"models": [{"name": "one"}]}).encode()])
        provider = OllamaChatProvider("http://ollama", opener=lambda *_args, **_kwargs: tags)
        self.assertEqual(provider.list_models(), ["one"])

        response = FakeResponse([b'{"message":{"content":"answer"},"done":true}\n'])
        provider = OllamaChatProvider("http://ollama", opener=lambda *_args, **_kwargs: response)
        self.assertEqual(provider.generate(self.request(), CancellationToken()), "answer")
        token = CancellationToken()
        token.cancel()
        with self.assertRaises(ProviderError) as raised:
            list(provider.stream(self.request(), token))
        self.assertEqual(raised.exception.code, "cancelled")

    def test_timeout_and_midstream_cancellation_close_the_response(self):
        provider = OllamaChatProvider(
            "http://ollama",
            opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("slow")),
        )
        with self.assertRaises(ProviderError) as raised:
            list(provider.stream(self.request(), CancellationToken()))
        self.assertEqual(raised.exception.code, "timeout")

        class BlockingResponse(FakeResponse):
            def __init__(self):
                super().__init__([])
                self.reading = threading.Event()
                self.released = threading.Event()

            def readline(self):
                self.reading.set()
                self.released.wait(2)
                return b""

            def close(self):
                self.closed = True
                self.released.set()

        response = BlockingResponse()
        token = CancellationToken()
        captured = []

        def consume():
            try:
                list(
                    OllamaChatProvider("http://ollama", opener=lambda *_args, **_kwargs: response).stream(
                        self.request(), token
                    )
                )
            except ProviderError as exc:
                captured.append(exc)

        thread = threading.Thread(target=consume)
        thread.start()
        self.assertTrue(response.reading.wait(2))
        token.cancel()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(response.closed)
        self.assertEqual(captured[0].code, "cancelled")


if __name__ == "__main__":
    unittest.main()
