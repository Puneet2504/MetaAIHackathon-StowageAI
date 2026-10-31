"""Voyage check: one grade per line, no cargo trapped at a later port, a safe ship after every port, swap suggestions."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.schemas import ConstraintsIn, Parcel
from app.stowage import pipeline, voyage_check as vc
from app.stowage.solver import solve

from auth_helper import SignedIn

VESSEL = json.loads((Path(__file__).resolve().parents[1] / "data" / "vessels" / "demo_mr2.json").read_text())
CAP = {t["id"]: t["capacity100"] for t in VESSEL["tanks"]}
SAMPLE = [Parcel(grade="ULSD", quantity_t=20000, load_port="Rotterdam", discharge_port="Le Havre"),
          Parcel(grade="Jet A-1", quantity_t=12000, load_port="Rotterdam", discharge_port="Bilbao")]


def loads(tanks, grade, density):
    return [{"tank_id": t, "volume_m3": CAP[t] * 0.98, "density_t_m3": density, "grade": grade} for t in tanks]


def pair(*nos):
    return [f"COT{n}{s}" for n in nos for s in "PS"]


# Diesel in COT1-3 and jet in COT4-5: COT3 and COT4 are both on line L2.
SHARED = loads(pair(1, 2, 3), "ULSD", 0.845) + loads(pair(4, 5), "JET", 0.8)


def codes(report, severity="block"):
    return {i["code"] for i in report["issues"] if i["severity"] == severity}


class RuleTests(unittest.TestCase):
    def test_two_grades_on_one_line_are_blocked_and_a_swap_fixes_it(self):
        cargo, _ = vc.cargo_from_loads(VESSEL, SAMPLE, SHARED)
        r = vc.check(VESSEL, SAMPLE, cargo)
        self.assertFalse(r["ok"])
        self.assertIn("LINE_SHARED", codes(r))
        self.assertIn("DISCHARGE_BLOCKED", codes(r))  # Le Havre can't pump diesel out past the jet on L2
        shared = next(i for i in r["issues"] if i["code"] == "LINE_SHARED")
        self.assertEqual(shared["lines"], ["L2"])
        s = r["suggestion"]
        self.assertTrue(s and s["moves"])
        fixed, _ = vc.cargo_from_loads(VESSEL, SAMPLE, s["loads"])
        self.assertTrue(vc.check(VESSEL, SAMPLE, fixed)["ok"])

    def test_crossover_between_grades_is_a_note_with_two_valves_and_blocks_with_one(self):
        clean = loads(pair(1, 2), "ULSD", 0.845) + loads(pair(3, 4), "JET", 0.8)  # L1 diesel, L2 jet, X12 between
        cargo, _ = vc.cargo_from_loads(VESSEL, SAMPLE, clean)
        r = vc.check(VESSEL, SAMPLE, cargo)
        self.assertTrue(r["ok"])
        self.assertIn("CROSSOVER", codes(r, "note"))
        ship = json.loads(json.dumps(VESSEL))
        for x in ship["cargoSystem"]["crossovers"]:
            x["valveCount"] = 1
        self.assertIn("CROSSOVER", codes(vc.check(ship, SAMPLE, cargo)))

    def test_cargo_loaded_at_a_later_port_cannot_trap_cargo_for_a_further_port(self):
        parcels = [Parcel(grade="ULSD", quantity_t=8000, load_port="Rotterdam", discharge_port="Lisbon"),
                   Parcel(grade="Gasoline", quantity_t=8000, load_port="Rotterdam", discharge_port="Bilbao"),
                   Parcel(grade="Jet A-1", quantity_t=7000, load_port="Bilbao", discharge_port="Lisbon")]
        cargo = [vc.Cargo(t, "ULSD", "ULSD", CAP[t] * 0.98, 0.845, 0) for t in pair(1)] + \
                [vc.Cargo(t, "GASOLINE", "Gasoline", CAP[t] * 0.98, 0.745, 1) for t in pair(5)] + \
                [vc.Cargo(t, "JET", "Jet A-1", CAP[t] * 0.98, 0.8, 2, stage=1) for t in pair(2)]  # L1, with the diesel
        r = vc.check(VESSEL, parcels, cargo)
        shared = [i for i in r["issues"] if i["code"] == "LINE_SHARED"]
        self.assertTrue(shared)
        self.assertEqual(shared[0]["port"], "Bilbao")

    def test_unsafe_after_an_early_discharge_is_blocked(self):
        parcels = [Parcel(grade="ULSD", quantity_t=10000, load_port="Rotterdam", discharge_port="Le Havre"),
                   Parcel(grade="Biodiesel", quantity_t=20000, density_t_m3=1.0, load_port="Rotterdam", discharge_port="Bilbao")]
        cargo = [vc.Cargo(t, "ULSD", "ULSD", CAP[t] * 0.98, 0.845, 0) for t in pair(1, 6)] + \
                [vc.Cargo(t, "BIODIESEL", "Biodiesel", CAP[t] * 0.98, 1.0, 1) for t in pair(2, 3, 4, 5)]
        r = vc.check(VESSEL, parcels, cargo, suggest=False)
        first, after = r["ports"][0], r["ports"][1]
        self.assertTrue(first["passed"])
        self.assertFalse(after["passed"])
        unsafe = [i for i in r["issues"] if i["code"] == "PORT_CONDITION" and i["severity"] == "block"]
        self.assertEqual(unsafe[0]["port"], "Le Havre")

    def test_same_grade_for_two_ports_is_flagged(self):
        parcels = [Parcel(grade="ULSD", quantity_t=8000, load_port="Rotterdam", discharge_port="Le Havre"),
                   Parcel(grade="ULSD", quantity_t=8000, load_port="Rotterdam", discharge_port="Bilbao")]
        _, notes = vc.cargo_from_loads(VESSEL, parcels, loads(pair(1, 2), "ULSD", 0.845))
        self.assertEqual([n["code"] for n in notes], ["GRADE_AMBIGUOUS"])


class PlanningTests(unittest.TestCase):
    def test_ai_plan_passes_through_every_port(self):
        plan = pipeline._plan_stages(VESSEL, SAMPLE, ConstraintsIn(), None, {}, None)
        r = plan["voyage_check"]
        self.assertTrue(r["ok"])
        self.assertEqual([p["port"] for p in r["ports"]], ["Rotterdam", "Le Havre", "Bilbao"])
        self.assertIsNone(r["ports"][-1]["passed"])  # empty after the last port

    def test_a_plan_failing_the_voyage_check_gives_way_to_one_that_passes(self):
        top = solve(VESSEL, SAMPLE)
        top_tanks = sorted(t for a in top["allocation"] for t in a["tanks"])
        real = vc.check

        def strict(vessel, parcels, cargo, suggest=True, ballast=False):
            r = real(vessel, parcels, cargo, suggest, ballast)
            if sorted(c.tank for c in cargo) == top_tanks:  # pretend the solver's first choice traps cargo
                r = {**r, "ok": False, "issues": r["issues"] + [{"code": "LINE_SHARED", "severity": "block", "port": "x",
                                                                 "lines": [], "tanks": [], "message": "test"}]}
            return r

        with mock.patch.object(vc, "check", side_effect=strict):
            plan = pipeline._plan_stages(VESSEL, SAMPLE, ConstraintsIn(), None, {}, None)
        self.assertNotEqual(sorted(t for a in plan["allocation"] for t in a["tanks"]), top_tanks)
        self.assertTrue(plan["voyage_check"]["ok"])


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        for p in (mock.patch.object(config, "USER_DIR", Path(self.tmp.name)),
                  mock.patch.object(config, "MODEL_BASE_URL", "")):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.c = TestClient(app)
        self.h = SignedIn(self.c)
        self.parcels = [p.model_dump() for p in SAMPLE]

    def test_check_returns_issues_and_a_suggestion(self):
        r = self.c.post("/api/stowage/check", headers=self.h("ann"),
                        json={"vessel_id": "demo_mr2", "parcels": self.parcels, "loads": SHARED}).json()
        self.assertFalse(r["check"]["ok"])
        self.assertTrue(r["check"]["suggestion"]["moves"])
        self.assertEqual([a["grade_id"] for a in r["allocation"]], ["ULSD", "JET"])

    def test_hand_edited_condition_is_approved_only_when_clean(self):
        body = {"vessel_id": "demo_mr2", "order_text": "sample", "parcels": self.parcels, "loads": SHARED}
        blocked = self.c.post("/api/stowage/plans/manual", headers=self.h("ann"), json=body)
        self.assertEqual(blocked.status_code, 409)
        self.assertIn("Line L2", blocked.json()["detail"])
        fixed = self.c.post("/api/stowage/check", headers=self.h("ann"), json=body).json()["check"]["suggestion"]["loads"]
        ok = self.c.post("/api/stowage/plans/manual", headers=self.h("ann"), json={**body, "loads": fixed})
        self.assertEqual(ok.status_code, 201, ok.text)
        self.assertTrue(ok.json()["plan"]["manual"])
        self.assertTrue(ok.json()["plan"]["voyage_check"]["ok"])

    def test_tampered_plan_is_refused_at_approval(self):
        # A plan with jet next to diesel on line L2, posted as if it came from the planner.
        plan = pipeline.check_condition(VESSEL, SAMPLE, SHARED)["plan"]
        plan.pop("manual")
        r = self.c.post("/api/stowage/plans", headers=self.h("ann"), json={
            "vessel_id": "demo_mr2", "order_text": "x", "parcels": self.parcels, "plan": plan})
        self.assertEqual(r.status_code, 409)
        self.assertIn("Line L2", r.json()["detail"])


class RotationTests(unittest.TestCase):
    def test_reload_port_comes_before_the_ports_its_cargo_goes_to(self):
        parcels = [Parcel(grade="ULSD", quantity_t=1, load_port="Rotterdam", discharge_port="Lisbon"),
                   Parcel(grade="Gasoline", quantity_t=1, load_port="Rotterdam", discharge_port="Bilbao"),
                   Parcel(grade="Jet A-1", quantity_t=1, load_port="Bilbao", discharge_port="Lisbon")]
        self.assertEqual(vc.discharge_rotation(parcels), ["Bilbao", "Lisbon"])
        self.assertEqual(vc.discharge_rotation(SAMPLE), ["Le Havre", "Bilbao"])

if __name__ == "__main__":
    unittest.main()
