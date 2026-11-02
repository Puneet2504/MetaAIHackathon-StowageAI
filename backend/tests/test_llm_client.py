"""Model client tests with a fake HTTP endpoint. Run from the backend folder:  python -m unittest discover tests"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import config
from app.llm import cache, client
from app.schemas import ParseOrderOut

GOOD = json.dumps({"parcels": [{"grade": "ULSD", "quantity_t": 1000}], "notes": []})


def reply(content: str):
    r = mock.Mock()
    r.raise_for_status.return_value = None
    r.json.return_value = {"status": "completed", "output": [{"type": "message", "role": "assistant",
                                                              "content": [{"type": "output_text", "text": content}]}]}
    return r


def chat_reply(content: str):
    r = mock.Mock()
    r.raise_for_status.return_value = None
    r.json.return_value = {"choices": [{"message": {"content": content}}]}
    return r


class ClientTests(unittest.TestCase):
    def setUp(self):
        patches = [mock.patch.object(config, "MODEL_BASE_URL", "https://example.invalid/v1"),
                   mock.patch.object(config, "MODEL_API_KEY", "test"),
                   mock.patch.object(config, "MODEL_NAME", "test-model"),
                   mock.patch.object(cache, "enabled", False)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_invalid_reply_is_corrected_once(self):
        with mock.patch("app.llm.client.httpx.post", side_effect=[reply('{"parcels": "oops"}'), reply(GOOD)]) as post:
            out = client.chat_model("sys", "order", ParseOrderOut)
        self.assertEqual(out.parcels[0].quantity_t, 1000)
        second = post.call_args_list[1].kwargs["json"]
        self.assertEqual(second["instructions"], "sys")
        self.assertEqual([m["role"] for m in second["input"]], ["user", "assistant", "user"])
        self.assertIn("not valid", second["input"][-1]["content"])

    def test_two_invalid_replies_raise(self):
        with mock.patch("app.llm.client.httpx.post", side_effect=[reply("not json"), reply("still not")]):
            with self.assertRaises(client.ModelError):
                client.chat_model("sys", "order", ParseOrderOut)

    def test_code_fences_are_stripped(self):
        with mock.patch("app.llm.client.httpx.post", return_value=reply(f"```json\n{GOOD}\n```")):
            self.assertEqual(len(client.chat_model("sys", "order", ParseOrderOut).parcels), 1)

    def test_json_mode_adds_response_format(self):
        with mock.patch.object(config, "MODEL_JSON_MODE", True), \
                mock.patch("app.llm.client.httpx.post", return_value=reply(GOOD)) as post:
            client.chat_model("sys", "order", ParseOrderOut)
        self.assertEqual(post.call_args.kwargs["json"]["text"], {"format": {"type": "json_object"}})

    def test_json_mode_off_by_default(self):
        with mock.patch("app.llm.client.httpx.post", return_value=reply(GOOD)) as post:
            client.chat_model("sys", "order", ParseOrderOut)
        self.assertNotIn("text", post.call_args.kwargs["json"])

    def test_image_is_sent_as_content_parts(self):
        with mock.patch("app.llm.client.httpx.post", return_value=reply(GOOD)) as post:
            client.chat_model("sys", "read", ParseOrderOut, image="data:image/png;base64,AAAA", model="reader")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "reader")
        self.assertEqual(payload["input"][0]["content"], [{"type": "input_text", "text": "read"},
                                                          {"type": "input_image", "image_url": "data:image/png;base64,AAAA"}])

    def test_request_follows_the_responses_api(self):
        with mock.patch("app.llm.client.httpx.post", return_value=reply("Fine.")) as post:
            client._post(client._messages("sys", "q"), max_tokens=500, effort="high", cache_reply=False)
        self.assertTrue(post.call_args.args[0].endswith("/responses"))
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["input"], [{"role": "user", "content": "q"}])
        self.assertEqual(payload["max_output_tokens"], 500)
        self.assertEqual(payload["reasoning"], {"effort": "high"})
        self.assertNotIn("messages", payload)

    def test_chat_completions_still_available(self):
        with mock.patch.object(config, "MODEL_API", "chat"),                 mock.patch("app.llm.client.httpx.post", return_value=chat_reply("Fine.")) as post:
            self.assertEqual(client.chat_text("sys", "q"), "Fine.")
        self.assertTrue(post.call_args.args[0].endswith("/chat/completions"))
        self.assertEqual([m["role"] for m in post.call_args.kwargs["json"]["messages"]], ["system", "user"])

    def test_text_reply_is_not_retried(self):
        with mock.patch("app.llm.client.httpx.post", return_value=reply(" Fine. ")) as post:
            self.assertEqual(client.chat_text("sys", "q"), "Fine.")
        self.assertEqual(post.call_count, 1)


class FinishReasonTests(unittest.TestCase):
    """A reply that hit the output cap (status incomplete) is an error: never returned, cached or counted."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        for p in (mock.patch.object(cache, "enabled", True),  # other test modules switch the cache off
                  mock.patch.object(config, "CACHE_DIR", Path(self.tmp.name)),
                  mock.patch.object(config, "MODEL_BASE_URL", "http://model.test"),
                  mock.patch.object(config, "MODEL_API_KEY", "test-key")):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def http(self, content, finish):
        r = mock.Mock()
        r.raise_for_status.return_value = None
        r.json.return_value = {"status": "completed" if finish == "stop" else "incomplete",
                               "incomplete_details": None if finish == "stop" else {"reason": "max_output_tokens"},
                               "output": [{"type": "message", "content": [{"type": "output_text", "text": content}]}],
                               "usage": {"input_tokens": 10, "output_tokens": 5}}
        return r

    def cached(self) -> str:
        return "".join(f.read_text(encoding="utf-8") for f in Path(self.tmp.name).glob("*.json"))

    def test_cut_off_reply_raises_and_is_not_cached(self):
        from app.llm import usage
        with mock.patch("app.llm.client.httpx.post", return_value=self.http("Half a sent", "length")), \
                mock.patch.object(usage, "record") as record:
            with self.assertRaises(client.ModelError) as e:
                client.chat_text("sys", "user")
        self.assertIn("cut off (max_output_tokens)", str(e.exception))
        self.assertNotIn("Half a sent", self.cached())
        record.assert_not_called()

    def test_finished_reply_is_returned_and_cached(self):
        with mock.patch("app.llm.client.httpx.post", return_value=self.http("A full sentence.", "stop")):
            self.assertEqual(client.chat_text("sys", "user"), "A full sentence.")
        self.assertIn("A full sentence.", self.cached())

    def test_validate_decides_what_is_cached(self):
        def must_end_with_stop(text):
            if not text.endswith("."):
                raise client.ModelError("bad")
        with mock.patch("app.llm.client.httpx.post", return_value=self.http("no stop", "stop")):
            with self.assertRaises(client.ModelError):
                client.chat_text("sys", "other user", validate=must_end_with_stop)
        self.assertNotIn("no stop", self.cached())
        with mock.patch("app.llm.client.httpx.post", return_value=self.http("Fine.", "stop")):
            self.assertEqual(client.chat_text("sys", "third user", validate=must_end_with_stop), "Fine.")
        self.assertIn("Fine.", self.cached())


if __name__ == "__main__":
    unittest.main()
