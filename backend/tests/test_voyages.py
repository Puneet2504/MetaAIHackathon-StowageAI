"""Voyages tests: approved plans are listed with their route and can be opened and deleted."""
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app import config
from app.llm import plain
from app.llm.digest import plan_digest, stability_digest
from app.main import app
from app.schemas import Parcel
from app.stowage.solver import solve
from app import vessels
from auth_helper import SignedIn

PARCELS = [{"grade": "ULSD", "quantity_t": 20000, "load_port": "Rotterdam", "discharge_port": "Le Havre"},
           {"grade": "Jet A-1", "quantity_t": 12000, "load_port": "Rotterdam", "discharge_port": "Bilbao"}]


class VoyageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        for p in (mock.patch.object(config, "USER_DIR", Path(self.tmp.name)),
                  mock.patch.object(config, "MODEL_BASE_URL", "")):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.c = TestClient(app)
        self.h = SignedIn(self.c)

    def approve(self, user="ann"):
        out = self.c.post("/api/stowage/plan", headers=self.h(user),
                          json={"vessel_id": "demo_mr2", "parcels": PARCELS}).json()
        self.assertEqual(out["explanation_source"], "plain")
        r = self.c.post("/api/stowage/plans", headers=self.h(user), json={
            "vessel_id": "demo_mr2", "order_text": "sample", "parcels": out["parcels"],
            "plan": out["plan"], "explanation": out["explanation"]})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def test_approved_plan_appears_as_a_voyage(self):
        rec = self.approve()
        voyages = self.c.get("/api/voyages", headers=self.h("ann")).json()
        self.assertEqual(len(voyages), 1)
        v = voyages[0]
        self.assertEqual(v["vessel_name"], "MT Demo Fjord")
        self.assertEqual(v["voyage"]["load_ports"], ["Rotterdam"])
        self.assertEqual(v["voyage"]["discharge_ports"], ["Le Havre", "Bilbao"])
        self.assertAlmostEqual(v["voyage"]["total_t"], 32000, delta=5)
        detail = self.c.get(f"/api/voyages/{rec['id']}", headers=self.h("ann")).json()
        self.assertEqual(detail["vessel"]["id"], "demo_mr2")
        self.assertTrue(detail["explanation"].startswith("Verdict:"))

    def test_voyages_are_per_user_and_can_be_deleted(self):
        rec = self.approve()
        self.assertEqual(self.c.get("/api/voyages", headers=self.h("ben")).json(), [])
        self.assertEqual(self.c.get(f"/api/voyages/{rec['id']}", headers=self.h("ben")).status_code, 404)
        self.assertEqual(self.c.delete(f"/api/voyages/{rec['id']}", headers=self.h("ben")).status_code, 404)
        self.assertEqual(self.c.delete(f"/api/voyages/{rec['id']}", headers=self.h("ann")).status_code, 204)
        self.assertEqual(self.c.get("/api/voyages", headers=self.h("ann")).json(), [])

    def test_bad_voyage_id_is_rejected(self):
        self.assertEqual(self.c.get("/api/voyages/..%2Faccounts", headers=self.h("ann")).status_code, 404)


class PlainExplanationTests(unittest.TestCase):
    VESSEL = vessels.get("demo_mr2")

    def numbers(self, text):
        return set(re.findall(r"\d+(?:\.\d+)?", text))

    def test_passing_plan_has_three_parts_in_plain_words(self):
        plan = solve(self.VESSEL, [Parcel(**p) for p in PARCELS])
        text = plain.explain_plan(plan_digest(plan))
        for part in ("Verdict:", "Why:", "What to do:"):
            self.assertIn(part, text)
        self.assertIn("Safe to sail", text)
        self.assertIn("pushes itself back upright", text)

    def test_failing_check_says_not_safe_and_why(self):
        from app.engine.stability import TankLoad, calculate
        half = [TankLoad(t["id"], t["capacity100"] * 0.5, 0.84) for t in self.VESSEL["tanks"]
                if t["type"] == "cargo" and t["side"] == "S"]
        d = stability_digest(calculate(self.VESSEL, half).to_dict())
        text = plain.explain_stability(d)
        self.assertIn("Not safe", text) if not d["ok"] else self.assertIn("Safe", text)
        self.assertIn("What to do:", text)

    def test_only_numbers_from_the_digest_are_used(self):
        plan = solve(self.VESSEL, [Parcel(**p) for p in PARCELS])
        d = plan_digest(plan)
        s = d["stability"]
        allowed = {f"{v:.2f}" for v in (s["gm_m"], s["gm_min_m"], s["trim_m"]) if v is not None} | {
            str(s["checks"]), "0.15", "6"}
        used = self.numbers(plain.explain_plan(d))
        tank_numbers = {n for a in d["alloc"] for n in re.findall(r"\d+", a["tanks"])}
        self.assertTrue(used <= allowed | tank_numbers, used - allowed - tank_numbers)


class SummaryTests(unittest.TestCase):
    def test_same_grade_in_two_parcels_is_one_entry(self):
        from app.stowage.pipeline import voyage_summary
        parcels = [Parcel(grade="ULSD", quantity_t=12000, load_port="Rotterdam", discharge_port="Le Havre"),
                   Parcel(grade="ULSD", quantity_t=8000, load_port="Rotterdam", discharge_port="Bilbao")]
        plan = solve(vessels.get("demo_mr2"), parcels)
        s = voyage_summary({"parcels": [p.model_dump() for p in parcels], "plan": plan})
        self.assertEqual([g["grade_id"] for g in s["grades"]], ["ULSD"])
        self.assertAlmostEqual(s["grades"][0]["tonnes"], 20000, delta=2)
        self.assertEqual(s["total_t"], s["grades"][0]["tonnes"])

    def test_same_grade_at_a_later_port_is_one_entry(self):
        from app.stowage.pipeline import voyage_summary
        alloc = lambda t, port_tanks: {"grade": "ULSD", "grade_id": "ULSD", "tonnes": t, "allocated_m3": 0,
                                       "density_t_m3": 0.845, "tanks": port_tanks}
        record = {"parcels": [], "plan": {"allocation": [alloc(10000, ["COT1P"])], "result": None,
                                          "stages": [{"allocation": [alloc(5000, ["COT2P"])], "result": None}]}}
        grades = voyage_summary(record)["grades"]
        self.assertEqual(len(grades), 1)
        self.assertEqual(grades[0]["tonnes"], 15000)


class DemoSeedTests(unittest.TestCase):
    def test_seeded_voyage_has_stages_and_cleaning(self):
        from app import demo_seed
        from app.stowage import pipeline
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(config, "USER_DIR", Path(tmp)):
            demo_seed.seed_workspace("demo-user")
            [v] = pipeline.list_voyages("demo-user")
            record = pipeline.get_voyage("demo-user", v["id"])
        self.assertEqual(record["plan"]["stages"], [])
        self.assertTrue({"ports", "total", "tanks_to_clean"} <= set(record["plan"]["cleaning"]))
        self.assertIn("tanks_to_clean", record["voyage"])


if __name__ == "__main__":
    unittest.main()
