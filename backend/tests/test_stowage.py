"""Stowage solver tests. Run from the backend folder:  python -m unittest discover tests"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app import config
from app.main import app

from auth_helper import SignedIn
from app.schemas import Parcel
from app.stowage.grades import match_grade
from app.stowage.solver import Constraints, solve

VESSEL = json.loads((Path(__file__).resolve().parents[1] / "data" / "vessels" / "demo_mr2.json").read_text())
LINE_OF = {t["id"]: t.get("lineId") for t in VESSEL["tanks"]}
SAMPLE = [Parcel(grade="ULSD", quantity_t=20000), Parcel(grade="Jet A-1", quantity_t=12000)]


class GradeTests(unittest.TestCase):
    def test_aliases(self):
        self.assertEqual(match_grade("Jet A-1").id, "JET")
        self.assertEqual(match_grade("ULSD 10ppm").id, "ULSD")
        self.assertEqual(match_grade("Eurobob gasoline").id, "GASOLINE")
        self.assertIsNone(match_grade("FAME"))


class SolverTests(unittest.TestCase):
    def test_sample_order_is_fully_planned_and_passes(self):
        r = solve(VESSEL, SAMPLE)
        self.assertEqual(r["status"], "planned", r["message"])
        self.assertTrue(r["result"]["all_passed"])
        self.assertEqual(r["result"]["list_deg"], 0.0)
        for a in r["allocation"]:
            self.assertLess(a["unallocated_m3"], 0.5)

    def test_one_grade_per_line_and_one_slack_pair_per_grade(self):
        r = solve(VESSEL, SAMPLE)
        grade_on_line = {}
        for load in r["loads"]:
            line = LINE_OF.get(load["tank_id"])
            if line:
                self.assertEqual(grade_on_line.setdefault(line, load["grade"]), load["grade"])
        for a in r["allocation"]:
            caps = {t["id"]: t["capacity100"] * t["maxFillPct"] / 100 for t in VESSEL["tanks"]}
            slack = [l for l in r["loads"] if l["grade"] == a["grade_id"] and l["volume_m3"] < caps[l["tank_id"]] - 0.5]
            self.assertLessEqual(len(slack), 2)  # one port/starboard pair

    def test_too_many_grades_cannot_segregate(self):
        parcels = [Parcel(grade=g, quantity_t=5000) for g in ("ULSD", "JET", "GASOLINE", "NAPHTHA")]
        self.assertEqual(solve(VESSEL, parcels)["status"], "cannot_segregate")

    def test_empty_tanks_are_respected(self):
        r = solve(VESSEL, SAMPLE, Constraints(empty_tanks=["COT3P", "COT3S"]))
        self.assertNotIn("COT3P", {l["tank_id"] for l in r["loads"]})
        self.assertNotIn("COT3S", {l["tank_id"] for l in r["loads"]})
        self.assertEqual(r["status"], "planned")

    def test_fixed_grade_is_respected(self):
        r = solve(VESSEL, SAMPLE, Constraints(fixed_grades={"COT1P": "ULSD"}))
        self.assertIn("COT1P", next(a for a in r["allocation"] if a["grade_id"] == "ULSD")["tanks"])

    def test_fixed_grade_given_as_written_in_the_order(self):
        r = solve(VESSEL, SAMPLE, Constraints(fixed_grades={"COT6P": "Jet A-1", "COT6S": "Jet A-1"}))
        self.assertIn("COT6P", next(a for a in r["allocation"] if a["grade_id"] == "JET")["tanks"])
        self.assertFalse(any("Could not apply" in w for w in r["warnings"]))

    def test_cargo_that_does_not_fit_is_reported(self):
        r = solve(VESSEL, [Parcel(grade="ULSD", quantity_t=60000)])
        self.assertGreater(r["allocation"][0]["unallocated_m3"], 0)
        self.assertNotEqual(r["status"], "planned")

    def test_missing_quantity_is_not_planned(self):
        r = solve(VESSEL, [Parcel(grade="ULSD")])
        self.assertEqual(r["status"], "nothing_to_plan")


class StowageApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(config, "USER_DIR", Path(self.tmp.name))
        self.patch.start()
        self.c = TestClient(app)
        self.h = SignedIn(self.c)

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_plan_approve_and_list_per_user(self):
        h = self.h("alice")
        body = {"vessel_id": "demo_mr2", "parcels": [p.model_dump() for p in SAMPLE]}
        with mock.patch.object(config, "MODEL_BASE_URL", ""):
            r = self.c.post("/api/stowage/plan", json=body, headers=h)
        self.assertEqual(r.status_code, 200, r.text)
        out = r.json()
        self.assertEqual(out["plan"]["status"], "planned")
        a = self.c.post("/api/stowage/plans", json={"vessel_id": "demo_mr2", "order_text": "sample",
                                                    "parcels": out["parcels"], "plan": out["plan"]}, headers=h)
        self.assertEqual(a.status_code, 201, a.text)
        self.assertEqual(len(self.c.get("/api/stowage/plans?vessel_id=demo_mr2", headers=h).json()), 1)
        self.assertEqual(self.c.get("/api/stowage/plans", headers=self.h("bob")).json(), [])

    def test_order_text_needs_a_model(self):
        with mock.patch.object(config, "MODEL_BASE_URL", ""):
            r = self.c.post("/api/stowage/plan", json={"vessel_id": "demo_mr2", "order_text": "Load 20,000 t ULSD"},
                            headers=self.h("alice"))
        self.assertEqual(r.status_code, 503)


class EvenFillTests(unittest.TestCase):
    ULSD = [Parcel(grade="ULSD", quantity_t=20000)]

    def fills(self, r, grade="ULSD"):
        caps = {t["id"]: t["capacity100"] for t in VESSEL["tanks"]}
        return {l["tank_id"]: 100 * l["volume_m3"] / caps[l["tank_id"]] for l in r["loads"] if l["grade"] == grade}

    def test_even_fill_uses_every_cargo_tank_at_one_level(self):
        r = solve(VESSEL, self.ULSD, Constraints(even_fill=True))
        self.assertEqual(r["status"], "planned", r["message"])
        fills = self.fills(r)
        cargo = {t["id"] for t in VESSEL["tanks"] if t["type"] == "cargo"}
        self.assertEqual(set(fills), cargo)
        self.assertLess(max(fills.values()) - min(fills.values()), 0.2)
        self.assertLess(max(fills.values()), 98)
        self.assertTrue(any("spread evenly" in w for w in r["warnings"]))

    def test_default_still_fills_pairs_to_98(self):
        fills = self.fills(solve(VESSEL, self.ULSD))
        self.assertGreaterEqual(sum(1 for f in fills.values() if f > 97.9), 2)
        self.assertLess(len(fills), 12)

    def test_falls_back_to_full_tanks_when_even_fails(self):
        # Spread over all tanks the demo ship trims about 3.5 m, so a 1 m limit rules the even plan out.
        r = solve(VESSEL, self.ULSD, Constraints(max_trim_m=1.0, even_fill=True))
        self.assertEqual(r["status"], "planned", r["message"])
        self.assertLessEqual(abs(r["result"]["trim_m"]), 1.0)
        self.assertTrue(any("failed the stability checks" in w for w in r["warnings"]))


class QuantityTests(unittest.TestCase):
    def test_temperature_changes_the_room_taken_not_the_tonnes(self):
        cool = solve(VESSEL, [Parcel(grade="ULSD", quantity_t=20000, temp_c=15)])["allocation"][0]
        warm = solve(VESSEL, [Parcel(grade="ULSD", quantity_t=20000, temp_c=35)])["allocation"][0]
        self.assertGreater(warm["allocated_m3"], cool["allocated_m3"] * 1.015)
        self.assertAlmostEqual(warm["tonnes"], 20000, delta=1)
        self.assertEqual(warm["conversion"]["temp_source"], "order")

    def test_tank_weights_are_true_mass(self):
        r = solve(VESSEL, [Parcel(grade="ULSD", quantity_t=20000, temp_c=35)])
        mass = sum(l["volume_m3"] * l["density_t_m3"] for l in r["loads"] if l["grade"] == "ULSD")
        self.assertAlmostEqual(mass, r["allocation"][0]["conversion"]["tonnes_vacuum"], delta=2)

    def test_replan_with_a_cargo_temperature(self):
        self.c = TestClient(app)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(config, "USER_DIR", Path(tmp)), \
                mock.patch.object(config, "MODEL_BASE_URL", ""):
            h = SignedIn(self.c)
            body = {"vessel_id": "demo_mr2", "parcels": [{"grade": "ULSD", "quantity_t": 20000, "load_port": "Rotterdam"}]}
            first = self.c.post("/api/stowage/plan", headers=h("ann"), json=body).json()
            again = self.c.post("/api/stowage/plan", headers=h("ann"),
                                json={**body, "parcels": first["parcels"], "instructions": "cargo temperature 40 C"}).json()
        a1, a2 = first["plan"]["allocation"][0], again["plan"]["allocation"][0]
        self.assertEqual(a1["conversion"]["temp_source"], "Rotterdam average")
        self.assertEqual(a2["conversion"]["temp_c"], 40)
        self.assertGreater(a2["allocated_m3"], a1["allocated_m3"])
        self.assertEqual(again["parcels"][0]["temp_c"], 40)


    def test_ai_explanation_always_says_what_was_assumed(self):
        from app.llm.digest import plan_digest
        from app.stowage import pipeline
        digest = plan_digest(solve(VESSEL, [Parcel(grade="ULSD", quantity_t=20000, load_port="Rotterdam")]))
        reply = "Verdict: Safe.\nWhy:\n- ULSD in all tanks.\nWhat to do:\n- Nothing."
        with mock.patch.object(pipeline.config, "model_configured", return_value=True), \
                mock.patch.object(pipeline.tasks, "explain", return_value=reply):
            text, source = pipeline._explain(digest, [])
        self.assertEqual(source, "ai")
        self.assertIn("ULSD temperature (Rotterdam average)", text.splitlines()[-1])


if __name__ == "__main__":
    unittest.main()
