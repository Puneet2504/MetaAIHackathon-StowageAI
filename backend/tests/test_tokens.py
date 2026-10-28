"""Token-saving tests: code fast paths, the reply cache, slim payloads and usage counting."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import config
from app.llm import cache, client, prompts, tasks, usage
from app.llm.digest import plan_digest, stability_digest
from app.stowage.solver import solve
from app.schemas import Parcel

VESSEL = json.loads((Path(__file__).resolve().parents[1] / "data" / "vessels" / "demo_mr2.json").read_text())
TANKS = [t["id"] for t in VESSEL["tanks"] if t["type"] == "cargo"]
NONE = {"empty_tanks": [], "fixed_grades": {}, "max_trim_m": None}
PLAN = solve(VESSEL, [Parcel(grade="ULSD", quantity_t=20000), Parcel(grade="Jet A-1", quantity_t=12000)])
DIGEST = stability_digest(PLAN["result"])


def reply(content, prompt=100, completion=20):
    r = mock.Mock()
    r.raise_for_status.return_value = None
    r.json.return_value = {"choices": [{"message": {"content": content}}],
                           "usage": {"prompt_tokens": prompt, "completion_tokens": completion}}
    return r


class TokenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        for p in (mock.patch.object(config, "MODEL_BASE_URL", "https://example.invalid/v1"),
                  mock.patch.object(config, "MODEL_API_KEY", "test"),
                  mock.patch.object(config, "MODEL_NAME", "test-model"),
                  mock.patch.object(config, "CACHE_DIR", Path(self.tmp.name) / "cache"),
                  mock.patch.object(config, "USER_DIR", Path(self.tmp.name) / "users"),
                  mock.patch.object(cache, "enabled", True)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_common_instructions_make_no_model_call(self):
        with mock.patch("app.llm.client.httpx.post") as post, usage.track() as used:
            for text in ("keep tank 4 empty", "trim must not exceed 1.2 m", "use tank 3 again",
                         "diesel in tanks 6P and 6S", "always keep tank 2 empty on this ship"):
                tasks.read_instruction(text, TANKS, ["ULSD", "Jet A-1"], NONE)
        post.assert_not_called()
        self.assertEqual(used.skipped, 5)

    def test_unusual_instruction_goes_to_the_model(self):
        out = json.dumps({"constraints": NONE, "notes": ["Loading order cannot be set."]})
        with mock.patch("app.llm.client.httpx.post", return_value=reply(out)) as post:
            r = tasks.read_instruction("discharge the jet first", TANKS, ["Jet A-1"], NONE)
        self.assertEqual(post.call_count, 1)
        self.assertTrue(r.notes)

    def test_repeat_explanation_is_served_from_cache(self):
        d = DIGEST
        with mock.patch("app.llm.client.httpx.post", return_value=reply("Verdict: safe.")) as post, \
                usage.track() as used:
            first = tasks.explain(prompts.EXPLAIN_SYSTEM, d)
            second = tasks.explain(prompts.EXPLAIN_SYSTEM, d)
        self.assertEqual(first, second)
        self.assertEqual(post.call_count, 1)
        self.assertEqual((used.calls, used.cache_hits), (1, 1))

    def test_invalid_json_is_not_cached(self):
        good = json.dumps({"parcels": [], "notes": []})
        with mock.patch("app.llm.client.httpx.post", side_effect=[reply("oops"), reply(good), reply(good)]) as post:
            tasks.read_order("Load 1,000 mt ULSD")
            tasks.read_order("Load 1,000 mt ULSD")  # the corrected reply is cached, not "oops"
        self.assertEqual(post.call_count, 2)

    def test_request_is_capped_and_asks_for_little_reasoning(self):
        with mock.patch("app.llm.client.httpx.post", return_value=reply("ok")) as post:
            tasks.explain(prompts.EXPLAIN_SYSTEM, DIGEST)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["max_tokens"], config.MAX_TOKENS_EXPLAIN)
        self.assertEqual(payload["reasoning_effort"], config.REASONING_EFFORT)

    def test_usage_is_saved_per_user_per_day(self):
        with mock.patch("app.llm.client.httpx.post", return_value=reply("ok", 120, 30)), usage.track("u1"):
            tasks.explain(prompts.EXPLAIN_SYSTEM, DIGEST)
        today = usage.summary("u1")["today"]
        self.assertEqual((today["prompt_tokens"], today["completion_tokens"], today["calls"]), (120, 30, 1))
        self.assertEqual(usage.summary("u2")["today"]["calls"], 0)

    def test_digest_is_small_and_has_no_curves(self):
        plan = solve(VESSEL, [Parcel(grade="ULSD", quantity_t=20000), Parcel(grade="Jet A-1", quantity_t=12000)])
        d = plan_digest(plan)
        text = json.dumps(d)
        self.assertNotIn("gz", text.lower())
        self.assertNotIn("soundingTable", text)
        self.assertLess(len(text), len(json.dumps(plan)) / 4)
        self.assertNotIn("gz_curve", stability_digest(plan["result"]))

    def test_prompts_stay_short(self):
        limits = {"PARSE_ORDER_SYSTEM": 1000, "READ_DRAWING_SYSTEM": 1950, "STOWAGE_INSTRUCTIONS_SYSTEM": 1450,
                  "EXPLAIN_SYSTEM": 650, "EXPLAIN_PLAN_SYSTEM": 700}
        for name, limit in limits.items():
            self.assertLessEqual(len(getattr(prompts, name)), limit, name)


if __name__ == "__main__":
    unittest.main()
