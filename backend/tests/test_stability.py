"""Engine tests. Run from the backend folder:  python -m unittest discover tests"""
import json
import math
import unittest
from pathlib import Path

from app.engine.stability import TankLoad, calculate, interp, area_under, StabilityError

VESSEL = json.loads((Path(__file__).resolve().parents[1] / "data" / "vessels" / "demo_mr2.json").read_text())
CARGO = [t for t in VESSEL["tanks"] if t["type"] == "cargo"]
BASE = [TankLoad("FOP", 300, 0.95), TankLoad("FOS", 300, 0.95), TankLoad("FW", 200, 1.0)]


class HelperTests(unittest.TestCase):
    def test_interp(self):
        self.assertAlmostEqual(interp(1.5, [1, 2], [10, 20]), 15)
        self.assertEqual(interp(0, [1, 2], [10, 20]), 10)  # clamped

    def test_area_of_straight_line(self):
        # GZ = angle in radians gives area = theta^2 / 2
        angles = [0, 30]
        gz = [0, math.radians(30)]
        self.assertAlmostEqual(area_under(angles, gz, 0, 30), math.radians(30) ** 2 / 2, places=4)


class EngineTests(unittest.TestCase):
    def test_symmetric_full_load_has_no_list_and_passes(self):
        loads = [TankLoad(t["id"], t["capacity100"] * 0.98, 0.84, "ULSD") for t in CARGO] + BASE
        r = calculate(VESSEL, loads)
        self.assertEqual(r.list_deg, 0.0)
        self.assertTrue(r.all_passed, r.criteria)

    def test_displacement_is_sum_of_weights(self):
        r = calculate(VESSEL, [], include_constants=False)
        self.assertAlmostEqual(r.displacement_t, VESSEL["lightship"]["weight"], places=1)

    def test_slack_tanks_reduce_gm(self):
        loads = [TankLoad(t["id"], t["capacity100"] * 0.5, 0.84) for t in CARGO] + BASE
        r = calculate(VESSEL, loads)
        self.assertGreater(r.free_surface_correction_m, 0)
        self.assertLess(r.gm_fluid_m, r.gm_solid_m)

    def test_one_sided_loading_lists_to_starboard_and_fails(self):
        loads = [TankLoad(t["id"], t["capacity100"] * 0.98, 0.84) for t in CARGO if t["side"] == "S"] + BASE
        r = calculate(VESSEL, loads)
        self.assertGreater(r.list_deg, 5)
        self.assertFalse(r.all_passed)

    def test_overfilled_tank_is_rejected(self):
        t = CARGO[0]
        with self.assertRaises(StabilityError):
            calculate(VESSEL, [TankLoad(t["id"], t["capacity100"] + 10, 0.84)])

    def test_unknown_tank_is_rejected(self):
        with self.assertRaises(StabilityError):
            calculate(VESSEL, [TankLoad("NOPE", 10, 0.84)])


class StrengthTests(unittest.TestCase):
    """Still-water shear force and bending moment along the hull."""
    CAP = {t["id"]: t["capacity100"] for t in VESSEL["tanks"]}

    def load(self, ids, density=0.845, fill=0.98):
        return [TankLoad(t, self.CAP[t] * fill, density, "ULSD") for t in ids] + BASE

    def midship(self, r):
        return next(p for p in r.strength if p["frame"] == "0.50L")

    def test_midship_heavy_sags_and_ends_heavy_hogs(self):
        # Port/starboard pairs, symmetric, and the same volume in every tank, so only the lengthwise spread differs.
        mid_ids, end_ids = ["COT3P", "COT3S", "COT4P", "COT4S"], ["COT1P", "COT1S", "COT6P", "COT6S"]
        vol = 0.98 * min(self.CAP[t] for t in mid_ids + end_ids)
        middle = calculate(VESSEL, [TankLoad(t, vol, 0.845, "ULSD") for t in mid_ids] + BASE)
        ends = calculate(VESSEL, [TankLoad(t, vol, 0.845, "ULSD") for t in end_ids] + BASE)
        self.assertAlmostEqual(middle.displacement_t, ends.displacement_t, places=1)
        self.assertLess(self.midship(middle)["bm_tm"], 0)
        self.assertEqual(self.midship(middle)["mode"], "sag")
        self.assertGreater(self.midship(ends)["bm_tm"], 0)
        self.assertEqual(self.midship(ends)["mode"], "hog")
        self.assertGreater(self.midship(ends)["bm_tm"] - self.midship(middle)["bm_tm"], 200_000)

    def test_standard_demo_loads_are_within_limits(self):
        from app.schemas import Parcel
        from app.stowage.solver import solve
        full = calculate(VESSEL, self.load([t["id"] for t in CARGO]))
        sample = solve(VESSEL, [Parcel(grade="ULSD", quantity_t=20000), Parcel(grade="Jet A-1", quantity_t=12000)])["result"]
        for r in (full.to_dict(), sample):
            self.assertLess(r["max_sf_pct"], 100)
            self.assertLess(r["max_bm_pct"], 100)
            strength = next(c for c in r["criteria"] if c["id"] == "STRENGTH_LIMIT")
            self.assertTrue(strength["passed"])
            self.assertEqual(strength["limit"], "max")

    def test_shear_and_bending_close_at_the_ends(self):
        # Reading points at both ends of the hull: a balanced ship carries no shear or bending there.
        ship = json.loads(json.dumps(VESSEL))
        L = ship["particulars"]["lbp"]
        ship["strength"]["readingPoints"] = [
            {"frame": f, "x": x, "sf": {"seaMax": 1, "harbourMax": 1},
             "bm": {"seaHogMax": 1, "seaSagMax": 1, "harbourHogMax": 1, "harbourSagMax": 1}}
            for f, x in (("AP", 0.0), ("mid", L / 2), ("FP", L))]
        r = calculate(ship, self.load(["COT1P", "COT1S", "COT6P", "COT6S"]))
        ap, mid, fp = r.strength
        for end in (ap, fp):
            self.assertLess(abs(end["bm_tm"]), 0.02 * abs(mid["bm_tm"]))
        self.assertLess(abs(fp["sf_t"]), 0.02 * max(abs(p["sf_t"]) for p in r.strength) + 50)

    def test_dense_cargo_in_the_middle_overstresses_the_hull(self):
        middle = ["COT2P", "COT2S", "COT3P", "COT3S", "COT4P", "COT4S", "COT5P", "COT5S"]
        r = calculate(VESSEL, self.load(middle, density=1.0))
        strength = next(c for c in r.criteria if c.id == "STRENGTH_LIMIT")
        self.assertFalse(strength.passed)
        self.assertFalse(r.all_passed)
        self.assertTrue(any(w.startswith("Hull overstressed") and "sagging" in w for w in r.warnings))

    def test_ship_without_reading_points_is_unchanged(self):
        ship = json.loads(json.dumps(VESSEL))
        ship["strength"] = {"readingPoints": []}
        r = calculate(ship, self.load([t["id"] for t in CARGO]))
        self.assertIsNone(r.max_sf_pct)
        self.assertIsNone(r.max_bm_pct)
        self.assertNotIn("STRENGTH_LIMIT", [c.id for c in r.criteria])
        self.assertEqual(len(r.criteria), 6)

    def test_digest_and_plain_text_use_maximum_for_strength(self):
        from app.llm import plain
        from app.llm.digest import for_model, stability_digest
        middle = ["COT2P", "COT2S", "COT3P", "COT3S", "COT4P", "COT4S", "COT5P", "COT5S"]
        d = stability_digest(calculate(VESSEL, self.load(middle, density=1.0)).to_dict())
        self.assertEqual(d["bm_mode"], "sagging")
        self.assertGreater(d["bm_pct"], 100)
        m = for_model(d)
        self.assertIn("of the safe sea limit", m["hull_stress"])
        self.assertTrue(any("maximum" in f for f in m["failed_checks"]))
        text = plain.explain_stability(d)
        self.assertIn("against a maximum of 100", text)
        self.assertIn("Spread the cargo more evenly", text)


if __name__ == "__main__":
    unittest.main()
