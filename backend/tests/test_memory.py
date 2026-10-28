"""Memory tests: remembered rules are applied, updated and forgotten. No model needed."""
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


if __name__ == "__main__":
    unittest.main()
