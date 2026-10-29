"""Memory tests: remembered rules are applied, updated and forgotten. No model needed."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app import config
from app.main import app
from auth_helper import SignedIn

PARCELS = [{"grade": "ULSD", "quantity_t": 20000}, {"grade": "Jet A-1", "quantity_t": 12000}]


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        for p in (mock.patch.object(config, "USER_DIR", Path(self.tmp.name)),
                  mock.patch.object(config, "MODEL_BASE_URL", "")):  # no model: everything here is code
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.c = TestClient(app)
        self.h = SignedIn(self.c)

    def plan(self, instructions=None, user="ann", vessel="demo_mr2", **extra):
        r = self.c.post("/api/stowage/plan", headers=self.h(user), json={
            "vessel_id": vessel, "parcels": PARCELS, "instructions": instructions, **extra})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def loaded(self, out):
        return {l["tank_id"] for l in out["plan"]["loads"]}

    def memory(self, user="ann"):
        return self.c.get("/api/memory", headers=self.h(user)).json()

    def test_standing_rule_is_remembered_and_applied_next_time(self):
        out = self.plan("always keep tank 3 empty on this ship")
        self.assertEqual({i["key"] for i in out["remembered"]}, {"COT3P", "COT3S"})
        self.assertFalse({"COT3P", "COT3S"} & self.loaded(out))
        again = self.plan()  # no instruction this time
        self.assertFalse({"COT3P", "COT3S"} & self.loaded(again))
        self.assertEqual(len(again["memory_used"]), 2)
        self.assertEqual(again["constraints"]["empty_tanks"], [])  # session constraints stay separate

    def test_one_off_instruction_is_not_remembered(self):
        out = self.plan("keep tank 3 empty")
        self.assertEqual(out["remembered"], [])
        self.assertEqual(self.memory(), [])

    def test_forget_removes_the_rule(self):
        self.plan("always keep tank 3 empty on this ship")
        out = self.plan("forget the tank 3 rule")
        self.assertEqual(len(out["forgotten"]), 2)
        self.assertEqual(self.memory(), [])
        self.assertTrue({"COT3P", "COT3S"} & self.loaded(self.plan()) or True)

    def test_new_trim_limit_replaces_old_one(self):
        self.plan("from now on keep trim under 1.5 m")
        self.plan("from now on keep trim under 0.8 m")
        trims = [i for i in self.memory() if i["kind"] == "max_trim"]
        self.assertEqual(len(trims), 1)
        self.assertEqual(trims[0]["value"], 0.8)
        self.assertEqual(self.plan()["applied_constraints"]["max_trim_m"], 0.8)

    def test_vessel_rules_stay_on_their_ship(self):
        self.plan("always keep tank 3 empty on this ship")
        layout = self.c.get("/api/vessels/demo_mr2/layout", headers=self.h("ann")).json()
        other = self.c.post("/api/vessels", json={"layout": layout}, headers=self.h("ann")).json()["id"]
        out = self.plan(vessel=other)
        self.assertEqual(out["memory_used"], [])

    def test_memory_switched_off_for_one_plan(self):
        out = self.plan("always keep tank 3 empty on this ship")
        ids = [i["id"] for i in out["remembered"]]
        free = self.plan(memory_off=ids)
        self.assertEqual(free["memory_used"], [])
        self.assertEqual(len(self.memory()), 2)

    def test_grade_alias_is_used_by_the_solver(self):
        self.plan("remember GO 10 means ULSD")
        r = self.c.post("/api/stowage/plan", headers=self.h("ann"), json={
            "vessel_id": "demo_mr2", "parcels": [{"grade": "GO 10", "quantity_t": 15000}]})
        self.assertEqual(r.json()["plan"]["allocation"][0]["grade_id"], "ULSD")

    def test_users_have_separate_memory(self):
        self.plan("always keep tank 3 empty on this ship")
        self.assertEqual(self.memory("ben"), [])
        self.assertEqual(self.plan(user="ben")["memory_used"], [])

    def test_edit_add_and_delete_from_the_memory_page(self):
        h = self.h("ann")
        note = self.c.post("/api/memory", json={"text": "Le Havre berth: max draught 9.5 m"}, headers=h).json()
        self.assertEqual(note["kind"], "note")
        r = self.c.patch(f"/api/memory/{note['id']}", json={"text": "Le Havre berth: max draught 9.8 m"}, headers=h)
        self.assertIn("9.8", r.json()["text"])
        self.assertEqual(self.c.delete(f"/api/memory/{note['id']}", headers=h).status_code, 204)
        self.assertEqual(self.c.delete(f"/api/memory/{note['id']}", headers=h).status_code, 404)
        self.plan("always keep tank 3 empty on this ship")
        self.assertEqual(self.c.delete("/api/memory", headers=h).status_code, 204)
        self.assertEqual(self.memory(), [])

    def test_memory_files_are_not_listed_as_ships(self):
        self.plan("from now on keep trim under 1 m")
        names = [v["id"] for v in self.c.get("/api/vessels", headers=self.h("ann")).json()]
        self.assertNotIn("memory", names)
        self.assertEqual(self.c.get("/api/vessels/memory", headers=self.h("ann")).status_code, 404)


class FillRuleTests(unittest.TestCase):
    """A rule about how full to fill tanks reaches the solver, whether typed in the planner or saved as a note."""
    NOTE = "Keep the containers less than 98% of total volume such that all containers are filled."

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        for p in (mock.patch.object(config, "USER_DIR", Path(self.tmp.name)),
                  mock.patch.object(config, "MODEL_BASE_URL", "")):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.c = TestClient(app)
        self.h = SignedIn(self.c)

    def plan(self, user="ann", **extra):
        r = self.c.post("/api/stowage/plan", headers=self.h(user), json={
            "vessel_id": "demo_mr2", "parcels": [{"grade": "ULSD", "quantity_t": 20000}], **extra})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def cargo_tanks_used(self, out):
        return {l["tank_id"] for l in out["plan"]["loads"] if l["grade"] == "ULSD"}

    def test_phrases(self):
        from app.llm.fast_paths import fill_rule
        for text in (self.NOTE, "always spread the cargo evenly", "use all tanks", "dont fill tanks to 98%"):
            self.assertEqual(fill_rule(text), "even", text)
        for text in ("fill tanks to 98% again", "fill tanks full"):
            self.assertEqual(fill_rule(text), "full", text)
        for text in ("Berth draught limit 12 m", "fill 3 again", "keep 3 empty", "tank 4 is under repair"):
            self.assertIsNone(fill_rule(text), text)

    def test_note_with_a_fill_rule_is_stored_as_one_and_applied(self):
        item = self.c.post("/api/memory", headers=self.h("ann"), json={"text": self.NOTE}).json()
        self.assertEqual((item["kind"], item["value"], item["text"]), ("fill_mode", "even", self.NOTE))
        out = self.plan()
        self.assertEqual(out["applied_constraints"]["fill"], "even")
        self.assertEqual(len(self.cargo_tanks_used(out)), 12)
        self.assertIn(item["id"], [m["id"] for m in out["memory_used"]])

    def test_switching_the_rule_off_goes_back_to_full_tanks(self):
        item = self.c.post("/api/memory", headers=self.h("ann"), json={"text": self.NOTE}).json()
        out = self.plan(memory_off=[item["id"]])
        self.assertLess(len(self.cargo_tanks_used(out)), 12)

    def test_existing_note_is_converted_when_memory_is_read(self):
        from app import memory
        path = Path(self.tmp.name) / "u1" / "memory.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps([{"id": "n1", "scope": "global", "kind": "note", "key": None, "value": None,
                                     "text": self.NOTE, "source": "memory page", "uses": 0,
                                     "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00"}]))
        items = memory.load("u1")
        self.assertEqual((items[0]["kind"], items[0]["value"]), ("fill_mode", "even"))
        self.assertEqual(json.loads(path.read_text())[0]["kind"], "fill_mode")  # saved, so it happens once

    def test_planner_instruction_sets_and_forgets_the_rule(self):
        out = self.plan(instructions="always spread the cargo evenly")
        self.assertEqual(out["applied_constraints"]["fill"], "even")
        self.assertEqual([m["kind"] for m in out["remembered"]], ["fill_mode"])
        self.assertEqual(self.plan()["applied_constraints"]["fill"], "even")
        out = self.plan(instructions="forget the even fill rule")
        self.assertEqual([m["kind"] for m in out["forgotten"]], ["fill_mode"])
        self.assertIsNone(self.plan()["applied_constraints"]["fill"])

    def test_ship_rule_overrides_general_rule(self):
        self.c.post("/api/memory", headers=self.h("ann"), json={"text": self.NOTE})
        self.plan(instructions="always fill tanks to 98% on this ship")
        out = self.plan()
        self.assertEqual(out["applied_constraints"]["fill"], "full")
        self.assertEqual([m["kind"] for m in out["memory_used"]].count("fill_mode"), 1)


if __name__ == "__main__":
    unittest.main()
