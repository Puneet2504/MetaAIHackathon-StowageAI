"""Grade changes: cleaning rules, preferring tanks that need less cleaning, reload ports and the port checklist."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app import config
from app.engine.stability import TankLoad
from app.main import app
from app.schemas import ConstraintsIn, Parcel
from app.stowage import cleaning
from app.stowage.pipeline import _plan_stages
from app.stowage.solver import solve

from auth_helper import SignedIn

VESSEL = json.loads((Path(__file__).resolve().parents[1] / "data" / "vessels" / "demo_mr2.json").read_text())
CARGO = [t["id"] for t in VESSEL["tanks"] if t["type"] == "cargo"]
RELOAD = [Parcel(grade="ULSD", quantity_t=20000, load_port="Rotterdam", discharge_port="Bilbao"),
          Parcel(grade="Jet A-1", quantity_t=15000, load_port="Bilbao", discharge_port="Lisbon")]
LAST = {"id": "v1", "date": "2026-10-27", "last_port": "Bilbao"}


def jet_after_diesel() -> dict:
    """A report that needs washing: jet into tanks whose last cargo was diesel."""
    return _plan_stages(VESSEL, [Parcel(grade="Jet A-1", quantity_t=15000, load_port="Bilbao")], ConstraintsIn(),
                        None, {t: "ULSD" for t in CARGO}, LAST)["cleaning"]


class RuleTests(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(cleaning.requirement("ULSD", "ULSD")["key"], "none")
        self.assertEqual(cleaning.requirement(None, "JET")["key"], "none")
        self.assertEqual(cleaning.requirement("JET", "ULSD")["key"], "drain")
        self.assertEqual(cleaning.requirement("GASOLINE", "ULSD")["key"], "wash")
        jet = cleaning.requirement("ULSD", "JET")
        self.assertEqual(jet["key"], "full_wash")
        self.assertTrue(any("FAME" in c for c in jet["checks"]))
        self.assertTrue(any("Wall-wash" in c for c in jet["checks"]))
        odd = cleaning.requirement("FAME_B100", "ULSD")
        self.assertEqual(odd["key"], "wash")
        self.assertTrue(any("cleaning guide" in c for c in odd["checks"]))

    def test_estimate_washes_two_tanks_at_a_time(self):
        rows = [cleaning.requirement("ULSD", "JET") | {"tank": t} for t in ("A", "B", "C", "D")]
        self.assertEqual(cleaning.estimate(rows), {"hours": 4 * 8 / 2 + 2, "cost_usd": 4 * 6000, "tanks": 4})
        drains = [cleaning.requirement("JET", "ULSD") | {"tank": t} for t in ("A", "B")]
        self.assertEqual(cleaning.estimate(drains)["hours"], 1.0)  # no inspection without a full wash

    def test_every_cleaning_level_has_steps_and_checks(self):
        for (prev, nxt), (level, _) in cleaning.RULES.items():
            r = cleaning.requirement(prev, nxt)
            self.assertTrue(r["steps"] and r["checks"] and r["reason"], (prev, nxt))


class SolverTests(unittest.TestCase):
    def test_prefers_tanks_that_carried_the_same_grade(self):
        prev = {t: "GASOLINE" for t in CARGO} | {t: "ULSD" for t in ("COT1P", "COT1S", "COT2P", "COT2S")}
        r = solve(VESSEL, [Parcel(grade="ULSD", quantity_t=12000)], prev_grades=prev)
        self.assertEqual(set(r["allocation"][0]["tanks"]), {"COT1P", "COT1S", "COT2P", "COT2S"})

    def test_cargo_on_board_keeps_its_tanks_and_lines(self):
        line = {t["id"]: t.get("lineId") for t in VESSEL["tanks"]}
        on_board = [TankLoad("COT1P", 3000, 0.84, "ULSD"), TankLoad("COT1S", 3000, 0.84, "ULSD")]
        r = solve(VESSEL, [Parcel(grade="Jet A-1", quantity_t=8000)], on_board=on_board)
        jet = r["allocation"][0]["tanks"]
        self.assertFalse({"COT1P", "COT1S"} & set(jet))
        self.assertNotIn(line["COT1P"], {line[t] for t in jet})
        weight = sum(l["volume_m3"] * l["density_t_m3"] for l in r["loads"] if l["tank_id"] in ("COT1P", "COT1S"))
        self.assertAlmostEqual(weight, 6000 * 0.84, delta=1)  # the cargo on board is in the stability condition


class StageTests(unittest.TestCase):
    def test_reload_port_is_a_later_stage(self):
        plan = _plan_stages(VESSEL, RELOAD, ConstraintsIn(), None, {}, None)
        stage = plan["stages"][0]
        self.assertEqual((stage["port"], stage["discharged"]), ("Bilbao", ["ULSD"]))
        self.assertEqual(stage["allocation"][0]["parcel"], 1)
        self.assertTrue(plan["result"]["all_passed"] and stage["result"]["all_passed"])

    def test_first_port_leaves_clean_tanks_for_the_next_ports_cargo(self):
        # With even fill, planning Rotterdam alone spreads diesel into all 12 tanks, so every jet tank needs a full wash.
        plan = _plan_stages(VESSEL, RELOAD, ConstraintsIn(fill="even"), None, {}, None)
        diesel, jet = plan["allocation"][0]["tanks"], plan["stages"][0]["allocation"][0]["tanks"]
        self.assertLess(len(diesel), len(CARGO))
        self.assertTrue(plan["stages"][0]["result"]["all_passed"])
        washed = [c for p in plan["cleaning"]["ports"] for c in p["changes"] if c["level"] >= 2]
        self.assertLess(len(washed), len(jet))
        saving = plan["cleaning"]["saving"]
        self.assertGreater(saving["cost_usd"], 0)
        self.assertGreater(saving["hours"], 0)
        self.assertEqual(saving["port"], "Bilbao")
        self.assertNotIn("alternatives", plan)

    def test_without_a_reload_port_the_plan_is_unchanged(self):
        order = [Parcel(grade="ULSD", quantity_t=20000, load_port="Rotterdam")]
        plan = _plan_stages(VESSEL, order, ConstraintsIn(), None, {}, None)
        self.assertEqual(plan["stages"], [])
        self.assertEqual(plan["cleaning"]["ports"], [])
        self.assertIsNone(plan["cleaning"]["saving"])
        self.assertEqual(plan["allocation"][0]["tanks"], solve(VESSEL, order)["allocation"][0]["tanks"])

    def test_alternatives_are_distinct_and_passing(self):
        r = solve(VESSEL, [Parcel(grade="ULSD", quantity_t=20000)], alternatives=10)
        keys = [tuple(sorted(r["allocation"][0]["tanks"]))] + [tuple(sorted(a["allocation"][0]["tanks"])) for a in r["alternatives"]]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue(all(a["status"] == "planned" for a in r["alternatives"]))

    def test_last_voyage_diesel_before_jet_needs_a_full_wash(self):
        port = jet_after_diesel()["ports"][0]
        self.assertEqual((port["port"], port["changes"][0]["key"]), ("Bilbao", "full_wash"))
        self.assertTrue(any("Surveyor" in c for c in port["checks"]))
        self.assertGreater(port["estimate"]["hours"], 0)


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

    def plan(self, parcels):
        r = self.c.post("/api/stowage/plan", headers=self.h("ann"), json={"vessel_id": "demo_mr2", "parcels": parcels})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def approve(self, out, parcels):
        r = self.c.post("/api/stowage/plans", headers=self.h("ann"), json={
            "vessel_id": "demo_mr2", "order_text": "test", "parcels": parcels, "plan": out["plan"]})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def test_last_voyage_cargo_is_compared_and_stored_on_approval(self):
        gasoline = [{"grade": "Gasoline", "quantity_t": 15000, "load_port": "Rotterdam", "discharge_port": "Lagos"}]
        first = self.plan(gasoline)
        self.assertIsNone(first["plan"]["cleaning"]["last_voyage"])
        self.approve(first, gasoline)
        diesel = [{"grade": "ULSD", "quantity_t": 15000, "load_port": "Lagos", "discharge_port": "Rotterdam"}]
        out = self.plan(diesel)
        report = out["plan"]["cleaning"]
        self.assertIsNotNone(report["last_voyage"])
        if report["ports"]:  # diesel after gasoline needs a wash wherever it reuses those tanks
            self.assertTrue(all(c["key"] == "wash" and c["prev"] == "GASOLINE" for c in report["ports"][0]["changes"]))
        voyage = self.approve(out, diesel)
        self.assertIn("cleaning", voyage["plan"])
        self.assertEqual(voyage["voyage"]["tanks_to_clean"], report["tanks_to_clean"])

    def test_checklist_without_a_model_uses_the_rules(self):
        report = jet_after_diesel()
        r = self.c.post("/api/ai/cleaning", headers=self.h("ann"), json={"cleaning": report}).json()
        self.assertEqual(r["source"], "plain")
        for head in ("Before arrival:", "At berth before loading:", "Before sailing:"):
            self.assertIn(head, r["explanation"])
        self.assertIn("FAME", r["explanation"])

    def test_checklist_model_gets_only_the_compact_digest(self):
        report = jet_after_diesel()
        with mock.patch.object(config, "model_configured", return_value=True), \
                mock.patch("app.llm.tasks.client.chat_text", return_value="Before arrival:\n- x") as chat:
            r = self.c.post("/api/ai/cleaning", headers=self.h("ann"), json={"cleaning": report}).json()
        self.assertEqual(r["source"], "ai")
        sent = chat.call_args.args[1]
        self.assertIn("COT1 P/S", sent)  # tanks grouped, not one line each
        self.assertLess(len(sent), len(json.dumps(report)))


if __name__ == "__main__":
    unittest.main()
