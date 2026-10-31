"""Open space: facts about a ship's current voyage, the AI's suggestions, and their safety labels."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app import config
from app.llm import client, prompts, tasks
from app.llm.digest import compact
from app.main import app
from app.schemas import ConstraintsIn, OpenSpaceOut, Parcel, SpaceSuggestion
from app.stowage import open_space, pipeline
from app.stowage.voyage_check import cargo_from_plan

from auth_helper import SignedIn

VESSEL = json.loads((Path(__file__).resolve().parents[1] / "data" / "vessels" / "demo_mr2.json").read_text())
SAMPLE = [Parcel(grade="ULSD", quantity_t=20000, load_port="Rotterdam", discharge_port="Le Havre"),
          Parcel(grade="Jet A-1", quantity_t=12000, load_port="Rotterdam", discharge_port="Bilbao")]
CARGO_TANKS = {t["id"] for t in VESSEL["tanks"] if t["type"] == "cargo"}


def sample_record() -> dict:
    plan = pipeline._plan_stages(VESSEL, SAMPLE, ConstraintsIn(), None, {}, None)
    return {"parcels": [p.model_dump() for p in SAMPLE], "plan": plan, "order_text": "sample"}


class FactsTests(unittest.TestCase):
    def setUp(self):
        self.rec = sample_record()
        self.facts = open_space.voyage_facts(VESSEL, self.rec)
        self.cargo = cargo_from_plan(self.rec["plan"])

    def test_ports_are_the_load_port_and_every_discharge_port_but_the_last(self):
        self.assertEqual([p["port"] for p in self.facts["ports"]], ["Rotterdam", "Le Havre"])
        self.assertEqual(self.facts["ports"][1]["later_ports"], ["Bilbao"])

    def test_empty_tanks_and_last_cargo(self):
        used = {c.tank for c in self.cargo}
        empty_rotterdam = {t for e in self.facts["ports"][0]["empty"] for t in open_space._expand(e["tanks"])}
        self.assertEqual(empty_rotterdam, CARGO_TANKS - used)
        diesel = {c.tank for c in self.cargo if c.grade == "ULSD"}
        at_le_havre = {t: e["last"] for e in self.facts["ports"][1]["empty"] for t in open_space._expand(e["tanks"])}
        for t in diesel:
            self.assertEqual(at_le_havre[t], "ULSD")

    def test_free_lines_carry_nothing(self):
        jet_lines = {self.rec["plan"]["allocation"][1]["lines"][0]}
        le_havre = self.facts["ports"][1]
        self.assertFalse(jet_lines & set(le_havre["free_lines"]))
        self.assertTrue(le_havre["free_lines"])

    def test_facts_stay_compact(self):
        self.assertLess(len(compact(self.facts)), 2500)


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.rec = sample_record()
        self.facts = open_space.voyage_facts(VESSEL, self.rec)
        self.le_havre = self.facts["ports"][1]

    def suggestion(self, **kw):
        e = self.le_havre["empty"][0]
        s = {"port": "Le Havre", "discharge_port": "Bilbao", "grade": "ULSD", "tanks": open_space._expand(e["tanks"]),
             "quantity_m3": e["m3"] - 10, "quantity_t": 1, "cleaning": "none", "reason": "", "checks": []}
        return {**s, **kw}

    def test_a_sound_suggestion_is_verified(self):
        self.assertTrue(open_space.verify(VESSEL, self.rec, self.facts, self.suggestion())["verified"])

    def test_occupied_tank_too_much_cargo_or_wrong_port_needs_a_check(self):
        jet_tanks = self.rec["plan"]["allocation"][1]["tanks"][:2]
        for bad, why in ((self.suggestion(tanks=jet_tanks), "not empty"),
                         (self.suggestion(quantity_m3=99999), "more than those tanks hold"),
                         (self.suggestion(discharge_port="Lisbon"), "not a later port"),
                         (self.suggestion(port="Oslo"), "not a port")):
            r = open_space.verify(VESSEL, self.rec, self.facts, bad)
            self.assertFalse(r["verified"])
            self.assertIn(why, r["why"])

    def test_different_grade_on_a_line_carrying_cargo_needs_a_check(self):
        # At Rotterdam, an empty pair on a line that already carries another grade.
        rotterdam = self.facts["ports"][0]
        busy = [e for e in rotterdam["empty"] if e["line"] not in rotterdam["free_lines"]]
        if not busy:
            self.skipTest("no empty pair shares a line with cargo in this plan")
        e = busy[0]
        on_line = [c.grade for c in cargo_from_plan(self.rec["plan"])
                   if open_space.Pipes(VESSEL).line_of.get(c.tank) == e["line"]][0]
        other = "Gasoline" if on_line != "Gasoline" else "Naphtha"
        r = open_space.verify(VESSEL, self.rec, self.facts, self.suggestion(
            port="Rotterdam", discharge_port="Bilbao", grade=other, tanks=open_space._expand(e["tanks"]), quantity_m3=1000))
        self.assertFalse(r["verified"])
        self.assertIn("Line", r["why"])


class AiTests(unittest.TestCase):
    def test_invented_tank_is_sent_back_and_the_corrected_reply_used(self):
        facts = open_space.voyage_facts(VESSEL, sample_record())
        good = {"summary": "ok", "suggestions": [{"port": "Le Havre", "discharge_port": "Bilbao", "grade": "ULSD",
                                                  "tanks": ["COT5P", "COT5S"], "quantity_m3": 100, "quantity_t": 85,
                                                  "cleaning": "none", "reason": "r", "checks": []}]}
        bad = json.loads(json.dumps(good))
        bad["suggestions"][0]["tanks"] = ["COT9P"]
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(config, "CACHE_DIR", Path(tmp)), \
                mock.patch.object(config, "MODEL_BASE_URL", "http://model.test"), \
                mock.patch.object(client, "_post", side_effect=[json.dumps(bad), json.dumps(good)]) as post:
            # A temporary cache: the validated (fake) reply must never reach the real data/cache.
            out = tasks.open_space(facts, open_space.tank_ids(VESSEL))
        self.assertEqual(out.suggestions[0].tanks, ["COT5P", "COT5S"])
        self.assertEqual(post.call_count, 2)

    def test_prompt_stays_short(self):
        self.assertLessEqual(len(prompts.OPEN_SPACE_SYSTEM), 1000)


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

    def approve(self, order="sample"):
        rec = sample_record()
        r = self.c.post("/api/stowage/plans", headers=self.h("ann"), json={
            "vessel_id": "demo_mr2", "order_text": order, "parcels": rec["parcels"], "plan": rec["plan"]})
        self.assertEqual(r.status_code, 201, r.text)

    def test_ships_without_a_voyage_are_listed_as_open(self):
        ships = self.c.get("/api/open-space", headers=self.h("ann")).json()
        demo = next(s for s in ships if s["vessel_id"] == "demo_mr2")
        self.assertIsNone(demo["voyage"])

    def test_latest_approved_voyage_is_the_current_one(self):
        self.approve("first")
        self.approve("second")
        demo = self.c.get("/api/open-space/demo_mr2", headers=self.h("ann")).json()
        self.assertEqual(demo["voyage"]["order_text"], "second")
        self.assertEqual([p["port"] for p in demo["voyage"]["facts"]["ports"]], ["Rotterdam", "Le Havre"])
        self.assertEqual(self.c.get("/api/open-space/nope", headers=self.h("ann")).status_code, 404)

    def test_without_a_model_the_facts_come_back_with_a_message(self):
        self.approve()
        r = self.c.post("/api/open-space/demo_mr2/suggest", headers=self.h("ann")).json()
        self.assertIsNone(r["ai"])
        self.assertIn("unavailable", r["message"])
        self.assertTrue(r["voyage"]["facts"]["ports"])

    def test_suggestions_come_back_in_order_with_labels_and_an_order_line(self):
        self.approve()
        facts = open_space.voyage_facts(VESSEL, sample_record())
        e = facts["ports"][1]["empty"][0]
        good = SpaceSuggestion(port="Le Havre", discharge_port="Bilbao", grade="ULSD", tanks=open_space._expand(e["tanks"]),
                               quantity_m3=e["m3"] - 10, quantity_t=7700, cleaning="none", reason="same grade", checks=["line free"])
        bad = good.model_copy(update={"quantity_m3": 99999})
        with mock.patch.object(config, "model_configured", return_value=True), \
                mock.patch.object(tasks, "open_space", return_value=OpenSpaceOut(summary="Two options.", suggestions=[good, bad])):
            r = self.c.post("/api/open-space/demo_mr2/suggest", headers=self.h("ann")).json()
        first, second = r["ai"]["suggestions"]
        self.assertEqual(r["ai"]["summary"], "Two options.")
        self.assertEqual(first["check"]["label"], "Verified")
        self.assertEqual(second["check"]["label"], "Needs a planner's check")
        self.assertEqual(first["order_line"], "Load Le Havre: 7,700 mt ULSD for Bilbao.")


class AddToVoyageTests(unittest.TestCase):
    """A verified suggestion goes into the current voyage itself; existing cargo stays where it is."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        for p in (mock.patch.object(config, "USER_DIR", Path(self.tmp.name)),
                  mock.patch.object(config, "MODEL_BASE_URL", "")):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.c = TestClient(app)
        self.h = SignedIn(self.c)
        rec = sample_record()
        r = self.c.post("/api/stowage/plans", headers=self.h("ann"), json={
            "vessel_id": "demo_mr2", "order_text": "sample", "parcels": rec["parcels"], "plan": rec["plan"]})
        self.voyage = r.json()
        self.facts = open_space.voyage_facts(VESSEL, self.voyage)

    def offer(self, port_index=1, **kw):
        port = self.facts["ports"][port_index]
        e = port["empty"][0]
        s = {"port": port["port"], "discharge_port": port["later_ports"][-1], "grade": "ULSD",
             "tanks": open_space._expand(e["tanks"]), "quantity_m3": e["m3"] - 10, "quantity_t": 7000,
             "cleaning": "none", "reason": "", "checks": []}
        return {**s, **kw}

    def add(self, suggestion):
        return self.c.post(f"/api/voyages/{self.voyage['id']}/add", headers=self.h("ann"), json={"suggestion": suggestion})

    def test_added_at_a_later_port_into_that_ports_stage(self):
        before = {l["tank_id"]: l["volume_m3"] for l in self.voyage["plan"]["loads"]}
        s = self.offer()
        r = self.add(s)
        self.assertEqual(r.status_code, 200, r.text)
        v = r.json()
        self.assertEqual(v["id"], self.voyage["id"])
        self.assertEqual(len(v["parcels"]), 3)
        stage = next(st for st in v["plan"]["stages"] if st["port"] == "Le Havre")
        self.assertEqual(stage["allocation"][-1]["tanks"], s["tanks"])
        self.assertEqual({l["tank_id"]: l["volume_m3"] for l in v["plan"]["loads"]}, before)  # departure untouched
        self.assertTrue(v["plan"]["voyage_check"]["ok"])
        self.assertEqual(v["plan"]["amendments"][0]["port"], "Le Havre")
        self.assertIn("Load Le Havre: 7,000 mt ULSD for Bilbao.", v["order_text"])
        self.assertEqual(len(self.c.get("/api/voyages", headers=self.h("ann")).json()), 1)  # no new voyage

    def test_added_at_the_first_port_joins_the_departure_cargo(self):
        if not self.facts["ports"][0]["empty"]:
            self.skipTest("no empty tank at the first port in this plan")
        s = self.offer(0)
        r = self.add(s)
        if r.status_code == 409:  # the voyage check may refuse it on this ship; then nothing may have changed
            self.assertEqual(self.c.get(f"/api/voyages/{self.voyage['id']}", headers=self.h("ann")).json()["parcels"],
                             self.voyage["parcels"])
            return
        v = r.json()
        self.assertIn(s["tanks"][0], v["plan"]["allocation"][-1]["tanks"])

    def test_unverified_suggestion_is_refused_and_nothing_changes(self):
        r = self.add(self.offer(quantity_m3=99999))
        self.assertEqual(r.status_code, 409)
        self.assertIn("more than those tanks hold", r.json()["detail"])
        stored = self.c.get(f"/api/voyages/{self.voyage['id']}", headers=self.h("ann")).json()
        self.assertEqual(len(stored["parcels"]), 2)
        self.assertNotIn("amendments", stored["plan"])

    def test_only_the_current_voyage_takes_cargo(self):
        rec = sample_record()
        self.c.post("/api/stowage/plans", headers=self.h("ann"), json={
            "vessel_id": "demo_mr2", "order_text": "newer", "parcels": rec["parcels"], "plan": rec["plan"]})
        self.assertEqual(self.add(self.offer()).status_code, 409)


if __name__ == "__main__":
    unittest.main()
