"""SAM 3.1 segmentation: the Responses request, and boxes read from SAM's text reply. No network."""
import unittest
from unittest import mock

import httpx

from app import config
from app.llm import client, segment

IMAGE = "data:image/png;base64,AAAA"
# Two objects on frame 0: one with a confidence, one without; masks are present but ignored.
REPLY = ("<0f>1<|box;x1=10;y1=20;x2=109;y2=59;w=200;h=100;c=0.92|><|mask;x=0;y=0;data=40,100,~abc|>,"
         "7<|box;y2=99;x2=199;x1=100;y1=60;h=100;w=200|><|mask;x=0;y=0;data=40,100,!xyz|>")


def reply(text: str, status: int = 200) -> httpx.Response:
    body = {"output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]}
    return httpx.Response(status, json=body, request=httpx.Request("POST", "https://example.test/v1/responses"))


class SegmentTests(unittest.TestCase):
    def setUp(self):
        p = [mock.patch.object(config, "SEGMENT_BASE_URL", "https://example.test/v1"),
             mock.patch.object(config, "SEGMENT_MODEL_NAME", "sam-3.1"),
             mock.patch.object(config, "MODEL_API_KEY", "test-key")]
        for x in p:
            x.start()
            self.addCleanup(x.stop)

    def test_boxes_are_read_by_name_and_normalised(self):
        with mock.patch.object(httpx, "post", return_value=reply(REPLY)):
            out = segment.segment(IMAGE, ["tank"])
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0], {"label": "tank", "box": [0.05, 0.2, 0.55, 0.6], "score": 0.92})  # x2/y2 inclusive
        self.assertEqual(out[1]["box"], [0.5, 0.6, 1.0, 1.0])
        self.assertIsNone(out[1]["score"])  # no confidence given, not zero

    def test_request_goes_to_responses_once_per_prompt(self):
        with mock.patch.object(httpx, "post", return_value=reply(REPLY)) as post:
            segment.segment(IMAGE)
        self.assertEqual(post.call_count, len(segment.DEFAULT_PROMPTS))
        for call, prompt in zip(post.call_args_list, segment.DEFAULT_PROMPTS):
            self.assertTrue(call.args[0].endswith("/responses"))
            body = call.kwargs["json"]
            self.assertEqual(body["model"], "sam-3.1")
            content = body["input"][0]["content"]
            self.assertIn({"type": "input_image", "image_url": IMAGE}, content)
            self.assertIn({"type": "input_text", "text": prompt}, content)
            self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer test-key")

    def test_http_error_raises_model_error(self):
        with mock.patch.object(httpx, "post", return_value=reply("", status=404)):
            with self.assertRaises(client.ModelError) as e:
                segment.segment(IMAGE, ["tank"])
        self.assertIn("404", str(e.exception))

    def test_nothing_found_is_an_empty_list(self):
        with mock.patch.object(httpx, "post", return_value=reply("")):
            self.assertEqual(segment.segment(IMAGE, ["tank"]), [])

    def test_too_many_boxes_are_capped(self):
        many = ",".join(f"{i}<|box;x1=0;y1=0;x2=9;y2=9;w=100;h=100|>" for i in range(segment.MAX_BOXES + 10))
        with mock.patch.object(httpx, "post", return_value=reply("<0f>" + many)):
            self.assertEqual(len(segment.segment(IMAGE, ["tank"])), segment.MAX_BOXES)

    def test_not_configured_makes_no_call(self):
        with mock.patch.object(config, "SEGMENT_MODEL_NAME", ""), mock.patch.object(httpx, "post") as post:
            self.assertEqual(segment.segment(IMAGE), [])
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
