"""Ballast preview: water in the right ballast tanks brings the trim back to slightly by the stern."""
import json
import unittest
from pathlib import Path

from app.engine.stability import TankLoad, calculate
from app.schemas import ConstraintsIn, Parcel
from app.stowage import ballast, pipeline
from app.stowage.solver import service_loads

VESSEL = json.loads((Path(__file__).resolve().parents[1] / "data" / "vessels" / "demo_mr2.json").read_text())
TANKS = {t["id"]: t for t in VESSEL["tanks"]}


def cargo(*pairs, density=0.8):
    return [TankLoad(f"COT{n}{s}", TANKS[f"COT{n}{s}"]["capacity100"] * 0.98, density, "X") for n in pairs for s in "PS"] \
        + service_loads(VESSEL)


def x(tank: str) -> float:
    return TANKS[tank]["soundingTable"][-1]["lcg"]


class PreviewTests(unittest.TestCase):
    def test_down_by_the_head_takes_ballast_aft(self):
        loads = cargo(1, 2)
        before = calculate(VESSEL, loads).trim_m
        self.assertLess(before, 0)
        p = ballast.preview(VESSEL, loads)
        self.assertGreater(p["trim_after"], before)  # moved towards the stern
        lcf = sum(r["lcf"] for r in VESSEL["hydrostatics"]) / len(VESSEL["hydrostatics"])
        self.assertTrue(all(x(r["tank"]) < lcf + 10 for r in p["tanks"]))  # aft of amidships
        if not p["reached"]:
            self.assertIn("Best achievable", p["note"])
        else:
            self.assertTrue(ballast.TRIM_MIN <= p["trim_after"] <= ballast.TRIM_MAX)

    def test_down_by_the_stern_takes_ballast_forward_and_reaches_the_band(self):
        loads = [TankLoad(t["id"], t["capacity100"] * 0.98, 0.845, "ULSD") for t in VESSEL["tanks"] if t["type"] == "cargo"] \
            + service_loads(VESSEL)
        before = calculate(VESSEL, loads).trim_m
        self.assertGreater(before, ballast.TRIM_MAX)
        p = ballast.preview(VESSEL, loads)
        self.assertTrue(p["reached"])
        self.assertTrue(ballast.TRIM_MIN <= p["trim_after"] <= ballast.TRIM_MAX)
        self.assertEqual(p["tanks"][0]["tank"], "FPT")  # the forepeak moves the trim most

    def test_no_preview_when_the_trim_is_already_normal(self):
        loads = cargo(1, 2)
        base = calculate(VESSEL, loads)
        aft = ballast.preview(VESSEL, loads)
        water = [TankLoad(r["tank"], r["m3"], 1.025, "Ballast") for r in aft["tanks"]]
        if aft["reached"]:
            self.assertIsNone(ballast.preview(VESSEL, loads + water))
        else:  # build a condition inside the band directly instead
            full = [TankLoad(t["id"], t["capacity100"] * 0.98, 0.845, "ULSD") for t in VESSEL["tanks"] if t["type"] == "cargo"] \
                + service_loads(VESSEL)
            fixed = ballast.preview(VESSEL, full)
            water = [TankLoad(r["tank"], r["m3"], 1.025, "Ballast") for r in fixed["tanks"]]
            self.assertIsNone(ballast.preview(VESSEL, full + water))
        self.assertIsNotNone(base)

    def test_never_overfills_or_breaks_a_limit(self):
        for loads in (cargo(1, 2), cargo(5, 6), cargo(1, 2, 3, 4, 5, 6, density=0.845)):
            p = ballast.preview(VESSEL, loads)
            if not p:
                continue
            self.assertTrue(all(r["pct"] <= TANKS[r["tank"]].get("maxFillPct", 100) for r in p["tanks"]))
            base = calculate(VESSEL, loads)
            if base.all_passed:
                self.assertTrue(p["passed"])

    def test_the_plan_itself_is_unchanged(self):
        parcels = [Parcel(grade="ULSD", quantity_t=20000, load_port="Rotterdam", discharge_port="Le Havre"),
                   Parcel(grade="Jet A-1", quantity_t=12000, load_port="Rotterdam", discharge_port="Bilbao")]
        plan = pipeline._plan_stages(VESSEL, parcels, ConstraintsIn(), None, {}, None)
        self.assertNotIn("Ballast", {l["grade"] for l in plan["loads"]})
        ports = plan["voyage_check"]["ports"]
        with_preview = [p for p in ports if p.get("ballast")]
        self.assertTrue(with_preview)  # this sample voyage trims outside the band somewhere
        for p in ports:
            if p.get("trim_m") is not None and ballast.TRIM_MIN <= p["trim_m"] <= ballast.TRIM_MAX:
                self.assertIsNone(p.get("ballast"))


if __name__ == "__main__":
    unittest.main()
