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


if __name__ == "__main__":
    unittest.main()
