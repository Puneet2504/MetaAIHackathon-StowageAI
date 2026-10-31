"""Token-saving tests: code fast paths, the reply cache, slim payloads and usage counting."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app import config
from app.engine.stability import TankLoad, calculate
from app.main import app
from app.llm import cache, client, prompts, tasks, usage
from app.llm.digest import for_model, plan_digest, stability_digest
from app.stowage.solver import solve
from app.schemas import Parcel
from auth_helper import SignedIn

VESSEL = json.loads((Path(__file__).resolve().parents[1] / "data" / "vessels" / "demo_mr2.json").read_text())
TANKS = [t["id"] for t in VESSEL["tanks"] if t["type"] == "cargo"]
NONE = {"empty_tanks": [], "fixed_grades": {}, "max_trim_m": None}
PLAN = solve(VESSEL, [Parcel(grade="ULSD", quantity_t=20000), Parcel(grade="Jet A-1", quantity_t=12000)])
DIGEST = stability_digest(PLAN["result"])


EXPLANATION = "Verdict: Safe to sail.\nWhy:\n- It sits level.\nWhat to do:\n- Nothing to change."
CUT_OFF = "Verdict: Safe to sail.\nWhy:\n- It sits level, with sideways"


def reply(content, prompt=100, completion=20, finish="stop"):
    r = mock.Mock()
    r.raise_for_status.return_value = None
    r.json.return_value = {"choices": [{"message": {"content": content}, "finish_reason": finish}],
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
        with mock.patch("app.llm.client.httpx.post", return_value=reply(EXPLANATION)) as post, \
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
        with mock.patch("app.llm.client.httpx.post", return_value=reply(EXPLANATION)) as post:
            tasks.explain(prompts.EXPLAIN_SYSTEM, DIGEST)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["max_tokens"], config.MAX_TOKENS_EXPLAIN)
        self.assertEqual(payload["reasoning_effort"], config.REASONING_EFFORT)

    def test_usage_is_saved_per_user_per_day(self):
        with mock.patch("app.llm.client.httpx.post", return_value=reply(EXPLANATION, 120, 30)), usage.track("u1"):
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

    def test_digest_states_the_cargo_basis_for_the_model(self):
        plan = solve(VESSEL, [Parcel(grade="ULSD", quantity_t=20000, load_port="Rotterdam")])
        m = for_model(plan_digest(plan))
        self.assertIn("Rotterdam average", m["cargo_basis"][0])
        self.assertIn("ULSD density", m["assumed"])

    def test_prompts_stay_short(self):
        limits = {"PARSE_ORDER_SYSTEM": 1100, "READ_DRAWING_SYSTEM": 1950, "STOWAGE_INSTRUCTIONS_SYSTEM": 1450,
                  "EXPLAIN_SYSTEM": 690, "EXPLAIN_PLAN_SYSTEM": 740, "CLEANING_SYSTEM": 700}
        for name, limit in limits.items():
            self.assertLessEqual(len(getattr(prompts, name)), limit, name)


class TruncationTests(unittest.TestCase):
    """Cut-off or incomplete explanations are never shown or cached; the built-in one is used instead."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        for p in (mock.patch.object(cache, "enabled", True),  # other test modules switch the cache off
                  mock.patch.object(config, "CACHE_DIR", Path(self.tmp.name)),
                  mock.patch.object(config, "USER_DIR", Path(self.tmp.name)),
                  mock.patch.object(config, "MODEL_BASE_URL", "http://model.test"),
                  mock.patch.object(config, "MODEL_API_KEY", "test-key")):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.result = calculate(VESSEL, [TankLoad("FOP", 300, 0.95)]).to_dict()

    def test_explanation_cap_leaves_room_for_reasoning(self):
        self.assertGreaterEqual(config.MAX_TOKENS_EXPLAIN, 2000)

    def test_cut_off_reply_falls_back_to_the_built_in_explanation(self):
        c = TestClient(app)
        h = SignedIn(c)
        with mock.patch("app.llm.client.httpx.post", return_value=reply(CUT_OFF, finish="length")):
            r = c.post("/api/ai/explain", headers=h("ann"), json={"result": self.result}).json()
        self.assertEqual(r["source"], "plain")
        self.assertIn("What to do:", r["explanation"])
        self.assertFalse(cache_has(Path(self.tmp.name), CUT_OFF))

    def test_incomplete_reply_without_finish_reason_also_falls_back(self):
        c = TestClient(app)
        h = SignedIn(c)
        with mock.patch("app.llm.client.httpx.post", return_value=reply(CUT_OFF)):
            r = c.post("/api/ai/explain", headers=h("ann"), json={"result": self.result}).json()
        self.assertEqual(r["source"], "plain")
        self.assertFalse(cache_has(Path(self.tmp.name), CUT_OFF))

    def test_complete_reply_is_used_and_cached(self):
        c = TestClient(app)
        h = SignedIn(c)
        with mock.patch("app.llm.client.httpx.post", return_value=reply(EXPLANATION)):
            r = c.post("/api/ai/explain", headers=h("ann"), json={"result": self.result}).json()
        self.assertEqual((r["source"], r["explanation"]), ("ai", EXPLANATION))
        self.assertTrue(cache_has(Path(self.tmp.name), EXPLANATION))

    def test_plan_explanation_falls_back_too(self):
        from app.stowage import pipeline
        digest = plan_digest(solve(VESSEL, [Parcel(grade="ULSD", quantity_t=20000)]))
        with mock.patch("app.llm.client.httpx.post", return_value=reply(CUT_OFF, finish="length")):
            text, source = pipeline._explain(digest, [])
        self.assertEqual(source, "plain")
        self.assertIn("What to do:", text)

    def test_check_explanation(self):
        from app.llm import client, tasks
        tasks._check_explanation(EXPLANATION)
        tasks._check_explanation("Verdict: Safe.\nWhy:\n- Level.\nWhat to do:\n- Check trim (2.29 m by stern)")
        for bad in ("Verdict: Safe.\nWhy:\n- Level.", CUT_OFF, "Verdict: Safe.\nWhy:\n- Level.\nWhat to do:\n- Allow for slosh"):
            with self.assertRaises(client.ModelError):
                tasks._check_explanation(bad)


def cache_has(folder: Path, text: str) -> bool:
    """Whether any cached reply contains `text` (values are read from the JSON, so newlines match)."""
    path = folder / "llm.json"
    return path.exists() and any(text in e["v"] for e in json.loads(path.read_text(encoding="utf-8")).values())


if __name__ == "__main__":
    unittest.main()
