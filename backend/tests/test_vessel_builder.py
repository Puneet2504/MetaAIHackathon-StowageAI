"""Vessel builder tests. Run from the backend folder:  python -m unittest discover tests"""
import json
import sys
import unittest
from pathlib import Path

from pydantic import ValidationError

from app.engine.stability import TankLoad, calculate
from app.schemas import LayoutTank, VesselLayout
from app.vessel_builder import build_vessel

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "tools"))
from generate_demo_vessel import build as build_demo  # noqa: E402


def six_pair_layout(**over) -> VesselLayout:
    tanks = []
    for n in range(6):
        no = 6 - n
        for side in ("P", "S"):
            tanks.append(LayoutTank(id=f"COT{no}{side}", type="cargo", side=side,
                                    xAft=30 + 18 * n, xFwd=48 + 18 * n, lineId=f"L{(no + 1) // 2}"))
            tanks.append(LayoutTank(id=f"WBT{no}{side}", type="ballast", side=side,
                                    xAft=30 + 18 * n, xFwd=48 + 18 * n))
    args = dict(name="MT Test", lbp=160, breadth=28, depth=16, summerDraught=10.5, tanks=tanks)
    args.update(over)
    return VesselLayout(**args)


class BuilderTests(unittest.TestCase):
    def test_demo_is_reproduced_exactly(self):
        stored = json.loads((BACKEND / "data" / "vessels" / "demo_mr2.json").read_text())
        self.assertEqual(build_demo(), stored)

    def test_user_ship_full_load_passes_without_list(self):
        v = build_vessel(six_pair_layout(), "test")
        loads = [TankLoad(t["id"], t["capacity100"] * 0.98, 0.84) for t in v["tanks"] if t["type"] == "cargo"]
        loads += [TankLoad("FOP", 200, 0.95), TankLoad("FOS", 200, 0.95), TankLoad("FW", 100, 1.0)]
        r = calculate(v, loads)
        self.assertEqual(r.list_deg, 0.0)
        self.assertTrue(r.all_passed, r.criteria)

    def test_lightship_is_inside_the_hydrostatic_table(self):
        small = six_pair_layout(lbp=90, breadth=15, depth=8, summerDraught=6,
                                tanks=[LayoutTank(id="COT1P", type="cargo", side="P", xAft=20, xFwd=60),
                                       LayoutTank(id="COT1S", type="cargo", side="S", xAft=20, xFwd=60)])
        calculate(build_vessel(small, "small"), [])  # raises if the lightship is below the table

    def test_stated_capacity_is_matched(self):
        layout = six_pair_layout()
        layout.tanks[0].capacity100 = 2900.0
        v = build_vessel(layout, "test")
        self.assertAlmostEqual(v["tanks"][0]["capacity100"], 2900.0, delta=1.0)

    def test_service_tanks_are_added(self):
        ids = {t["id"] for t in build_vessel(six_pair_layout(), "test")["tanks"]}
        self.assertTrue({"FOP", "FOS", "DO", "FW"} <= ids)

    def test_cargo_lines_become_pumps(self):
        cs = build_vessel(six_pair_layout(), "test")["cargoSystem"]
        self.assertEqual([line["id"] for line in cs["lines"]], ["L1", "L2", "L3"])
        self.assertEqual(len(cs["pumps"]), 3)


class LayoutValidationTests(unittest.TestCase):
    def test_overlapping_cargo_tanks_rejected(self):
        with self.assertRaises(ValidationError):
            six_pair_layout(tanks=[LayoutTank(id="A", type="cargo", side="P", xAft=10, xFwd=40),
                                   LayoutTank(id="B", type="cargo", side="P", xAft=30, xFwd=60)])

    def test_tank_beyond_lbp_rejected(self):
        with self.assertRaises(ValidationError):
            six_pair_layout(tanks=[LayoutTank(id="A", type="cargo", side="P", xAft=150, xFwd=170)])

    def test_reversed_extent_rejected(self):
        with self.assertRaises(ValidationError):
            LayoutTank(id="A", type="cargo", side="P", xAft=40, xFwd=20)

    def test_duplicate_ids_rejected(self):
        with self.assertRaises(ValidationError):
            six_pair_layout(tanks=[LayoutTank(id="A", type="cargo", side="P", xAft=10, xFwd=20),
                                   LayoutTank(id="A", type="cargo", side="S", xAft=10, xFwd=20)])


class StrengthLimitTests(unittest.TestCase):
    def test_built_ship_has_seven_reading_points(self):
        ship = build_demo()
        points = ship["strength"]["readingPoints"]
        self.assertEqual([p["frame"] for p in points], ["0.15L", "0.30L", "0.45L", "0.50L", "0.65L", "0.80L", "0.85L"])
        mid = points[3]
        self.assertEqual(mid["bm"]["seaSagMax"], round(0.9 * mid["bm"]["seaHogMax"], -2))
        self.assertGreater(mid["bm"]["harbourHogMax"], mid["bm"]["seaHogMax"])
        self.assertGreater(mid["bm"]["seaHogMax"], points[0]["bm"]["seaHogMax"])  # largest amidships

    def test_limits_grow_with_displacement(self):
        from app.vessel_builder import strength_limits
        small, big = strength_limits(150, 30000), strength_limits(150, 60000)
        for a, b in zip(small["readingPoints"], big["readingPoints"]):
            self.assertGreater(b["sf"]["seaMax"], a["sf"]["seaMax"])
            self.assertGreater(b["bm"]["seaHogMax"], a["bm"]["seaHogMax"])

    def test_ships_saved_without_limits_get_them_on_read(self):
        import tempfile
        from unittest import mock
        from app import config, vessels
        ship = build_demo()
        ship["id"], ship["strength"] = "old-ship", {"readingPoints": []}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(config, "USER_DIR", Path(tmp)):
            vessels.save("ann", ship)
            got = vessels.get("old-ship", "ann")
        self.assertEqual(len(got["strength"]["readingPoints"]), 7)


if __name__ == "__main__":
    unittest.main()
