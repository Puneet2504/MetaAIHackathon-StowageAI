"""Tests for the rule-based repairs applied to layouts read from drawings. No model needed."""
import unittest
from unittest import mock

from app import config
from app.llm import drawing_fixes
from app.llm.drawing_fixes import normalise


def tank(tid, side, a, f, cap=None, typ="cargo"):
    return {"id": tid, "type": typ, "side": side, "xAft": a, "xFwd": f, "capacity100": cap, "lineId": None}


def ship(tanks, **over):
    base = {"name": "MT Test", "lbp": 176, "breadth": 32, "depth": 19, "summerDraught": 12, "tanks": tanks}
    return {**base, **over}


def by_id(layout):
    return {t["id"]: t for t in layout["tanks"]}


class LbpTests(unittest.TestCase):
    def test_lbp_estimated_from_loa_and_flagged(self):
        out, notes, fixes, est = normalise(ship([tank("COT1P", "P", 140, 152)], lbp=None, loa=183))
        self.assertEqual(out["lbp"], round(183 * config.LBP_FROM_LOA, 1))
        self.assertEqual(est, ["lbp"])
        self.assertTrue(any("LOA" in n for n in notes))
        self.assertEqual(fixes[0]["field"], "lbp")

    def test_ratio_is_a_setting(self):
        with mock.patch.object(config, "LBP_FROM_LOA", 0.95):
            out, *_ = normalise(ship([], lbp=None, loa=200))
        self.assertEqual(out["lbp"], 190.0)

    def test_no_lbp_and_no_loa_stays_missing_with_note(self):
        out, notes, fixes, est = normalise(ship([tank("COT1P", "P", 140, 152)], lbp=None))
        self.assertIsNone(out["lbp"])
        self.assertEqual(est, [])
        self.assertTrue(notes)

    def test_given_lbp_is_left_alone(self):
        out, notes, fixes, est = normalise(ship([], loa=183))
        self.assertEqual((out["lbp"], fixes, est), (176, [], []))


class ExtentTests(unittest.TestCase):
    def test_reversed_extents_are_swapped(self):
        out, _, fixes, _ = normalise(ship([tank("COT1P", "P", 152, 140)]))
        t = by_id(out)["COT1P"]
        self.assertEqual((t["xAft"], t["xFwd"]), (140, 152))
        self.assertEqual(fixes[0]["field"], "xAft/xFwd")

    def test_ends_clamped_to_ship_with_note(self):
        out, _, fixes, _ = normalise(ship([tank("COT1P", "P", -2, 180)], loa=183))
        t = by_id(out)["COT1P"]
        self.assertEqual((t["xAft"], t["xFwd"]), (0.0, 176))
        self.assertEqual({f["field"] for f in fixes}, {"xAft", "xFwd"})

    def test_far_out_of_range_is_only_noted(self):
        out, notes, fixes, _ = normalise(ship([tank("COT1P", "P", 150, 260)]))
        self.assertEqual(by_id(out)["COT1P"]["xFwd"], 260)
        self.assertTrue(any("beyond the LBP" in n for n in notes))

    def test_side_follows_the_id_suffix(self):
        out, _, fixes, _ = normalise(ship([tank("COT1S", "P", 140, 152)]))
        self.assertEqual(by_id(out)["COT1S"]["side"], "S")

    def test_yard_numbering_from_aft_is_kept(self):
        tanks = [tank(f"COT{n}{s}", s, 30 + 20 * (n - 1), 50 + 20 * (n - 1)) for n in (1, 2, 3) for s in "PS"]
        out, _, fixes, _ = normalise(ship(tanks))
        self.assertEqual(fixes, [])
        self.assertEqual(by_id(out)["COT1P"]["xAft"], 30)  # COT1 is aft-most here, as printed

    def test_strings_and_commas_become_numbers(self):
        out, *_ = normalise(ship([tank("COT1P", "P", "140", "152", "4,000")], breadth="32.2"))
        self.assertEqual(by_id(out)["COT1P"]["capacity100"], 4000.0)
        self.assertEqual(out["breadth"], 32.2)


class CapacityTests(unittest.TestCase):
    # 12 m long tank: box = 12 * (16 - 2) * (19 - 2) = 2856 m3
    def test_misread_digit_corrected_using_twin_tank(self):
        out, _, fixes, _ = normalise(ship([tank("COT3P", "P", 60, 72, 7250), tank("COT3S", "S", 60, 72, 2750)]))
        self.assertEqual(by_id(out)["COT3P"]["capacity100"], 2750)
        self.assertEqual(fixes[0]["old"], 7250)
        self.assertIn("misread", fixes[0]["reason"])

    def test_misread_digit_corrected_without_twin(self):
        out, _, fixes, _ = normalise(ship([tank("COT3P", "P", 60, 72, 7650)]))
        self.assertTrue(0.85 * 2856 <= by_id(out)["COT3P"]["capacity100"] <= 0.99 * 2856)

    def test_plausible_capacity_left_alone(self):
        out, notes, fixes, _ = normalise(ship([tank("COT3P", "P", 60, 72, 2700)]))
        self.assertEqual((by_id(out)["COT3P"]["capacity100"], fixes, notes), (2700, [], []))

    def test_impossible_capacity_without_good_fix_is_kept_and_flagged(self):
        out, notes, fixes, _ = normalise(ship([tank("COT3P", "P", 60, 72, 99999)]))
        self.assertEqual(by_id(out)["COT3P"]["capacity100"], 99999)
        self.assertEqual(fixes, [])
        self.assertTrue(any("more than the tank can hold" in n for n in notes))

    def test_missing_capacity_is_never_invented(self):
        out, _, fixes, est = normalise(ship([tank("COT3P", "P", 60, 72)]))
        self.assertIsNone(by_id(out)["COT3P"]["capacity100"])
        self.assertEqual((fixes, est), ([], []))

    def test_ballast_tanks_are_not_checked(self):
        out, notes, fixes, _ = normalise(ship([tank("WBT1P", "P", 60, 72, 99999, typ="ballast")]))
        self.assertEqual((fixes, notes), ([], []))


class DuplicateTests(unittest.TestCase):
    def test_duplicate_cargo_labels_renumbered_by_position_forward_first(self):
        tanks = [tank("COT1P", "P", 140, 160), tank("COT1S", "S", 140, 160),
                 tank("COT4P", "P", 120, 140), tank("COT4S", "S", 120, 140),
                 tank("COT4P", "P", 100, 120), tank("COT4S", "S", 100, 120),
                 tank("COT6P", "P", 80, 100), tank("COT6S", "S", 80, 100)]
        out, notes, fixes, _ = normalise(ship(tanks))
        ids = by_id(out)
        self.assertEqual(sorted(ids), sorted(f"COT{n}{s}" for n in range(1, 5) for s in "PS"))
        self.assertEqual(ids["COT2P"]["xAft"], 120)
        self.assertEqual(ids["COT3S"]["xAft"], 100)
        self.assertTrue(any("renumbered" in n for n in notes))

    def test_duplicates_with_aft_first_numbering(self):
        tanks = [tank("COT1P", "P", 30, 50), tank("COT2P", "P", 50, 70), tank("COT2P", "P", 70, 90),
                 tank("COT4P", "P", 90, 110)]
        out, *_ = normalise(ship(tanks))
        ids = by_id(out)
        self.assertEqual([ids[f"COT{n}P"]["xAft"] for n in range(1, 5)], [30, 50, 70, 90])

    def test_extra_slop_tanks_enumerated(self):
        tanks = [tank("SLOPP", "P", 30, 33, typ="slop"), tank("SLOPP", "P", 26, 30, typ="slop"),
                 tank("SLOPS", "S", 30, 33, typ="slop")]
        out, *_ = normalise(ship(tanks))
        self.assertEqual(sorted(by_id(out)), ["SLOP2P", "SLOPP", "SLOPS"])

    def test_ids_are_unique_after_normalising(self):
        tanks = [tank("CT1", "C", 60, 80, typ="cargo"), tank("CT1", "C", 40, 60, typ="cargo")]
        out, *_ = normalise(ship(tanks))
        ids = [t["id"] for t in out["tanks"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_input_is_not_changed(self):
        layout = ship([tank("COT1P", "P", 152, 140)], lbp=None, loa=183)
        normalise(layout)
        self.assertIsNone(layout["lbp"])
        self.assertEqual(layout["tanks"][0]["xAft"], 152)


class ModuleTests(unittest.TestCase):
    def test_candidates_include_swaps_and_confusions(self):
        c = drawing_fixes._candidates(7250)
        self.assertIn(2750, c)   # swapped digits
        self.assertIn(2250, c)   # 7 read for 2
        self.assertIn(725, c)    # extra trailing zero


if __name__ == "__main__":
    unittest.main()


class MeasuredDrawingTests(unittest.TestCase):
    """GA drawings that are drawn to scale but not dimensioned (e.g. only 'LOA 160 m')."""

    def layout(self, **over):
        tanks = [{"id": "COT1P", "type": "cargo", "side": "P", "xAft": None, "xFwd": None, "aFrac": 0.20, "fFrac": 0.30,
                  "capacity100": None, "lineId": None}]
        base = {"loa": 160, "lbp": None, "breadth": None, "depth": None, "summerDraught": None, "lengthRef": "LOA",
                "breadthFrac": 0.18, "depthFrac": 0.10, "draughtMarks": [11.3, 11.2], "tanks": tanks}
        return {**base, **over}

    def test_fractions_become_metres_from_the_ap(self):
        out, checks, fixes, est = normalise(self.layout())
        t = out["tanks"][0]
        offset = (160 - out["lbp"]) / 2
        self.assertEqual(t["xAft"], round((0.2 * 160 - offset) * 2) / 2)
        self.assertEqual(t["xFwd"], round((0.3 * 160 - offset) * 2) / 2)
        self.assertIn("COT1P.xAft", est)
        self.assertTrue(any(f["field"] == "position" for f in fixes))
        self.assertNotIn("aFrac", t)
        self.assertNotIn("breadthFrac", out)

    def test_printed_metres_win_over_fractions(self):
        L = self.layout()
        L["tanks"][0].update(xAft=40, xFwd=50)
        out, _, fixes, est = normalise(L)
        self.assertEqual((out["tanks"][0]["xAft"], out["tanks"][0]["xFwd"]), (40, 50))
        self.assertNotIn("COT1P.xAft", est)

    def test_dimensions_from_fractions_then_typical_ratios(self):
        out, _, fixes, est = normalise(self.layout())
        self.assertEqual((out["breadth"], out["depth"]), (28.8, 16.0))
        self.assertTrue({"breadth", "depth", "summerDraught"} <= set(est))
        out, _, fixes, _ = normalise(self.layout(breadthFrac=None, depthFrac=None))
        self.assertAlmostEqual(out["breadth"], out["lbp"] / 5.4, delta=0.1)
        self.assertAlmostEqual(out["depth"], out["breadth"] / 1.75, delta=0.1)
        self.assertIn("typical", next(f for f in fixes if f["field"] == "breadth")["reason"])

    def test_draught_from_marks_but_never_deeper_than_depth(self):
        out, *_ = normalise(self.layout())
        self.assertEqual(out["summerDraught"], 11.3)
        out, *_ = normalise(self.layout(depthFrac=0.06))  # depth 9.6 m: marks are too deep
        self.assertLess(out["summerDraught"], out["depth"])

    def test_scaled_tank_keeps_its_fix_after_renaming(self):
        tanks = [{"id": "COT1P", "type": "cargo", "side": "P", "aFrac": a, "fFrac": a + 0.1} for a in (0.2, 0.3)]
        out, _, fixes, est = normalise(self.layout(tanks=tanks))
        ids = {t["id"] for t in out["tanks"]}
        self.assertEqual(len(ids), 2)
        for f in fixes:
            if f["field"] == "position":
                self.assertIn(f["tank"], ids)
        self.assertTrue(all(k.split(".")[0] in ids for k in est if "." in k))


class TwinTests(unittest.TestCase):
    def ship(self, pairs):
        tanks = []
        for i, (cp, cs) in enumerate(pairs):
            a = 30 + 12 * i
            tanks += [tank(f"COT{i + 1}P", "P", a, a + 12, cp), tank(f"COT{i + 1}S", "S", a, a + 12, cs)]
        return ship(tanks, breadth=None, depth=None, loa=None)  # dimensions unknown: no box check

    def test_odd_twin_corrected_to_the_common_value(self):
        out, _, fixes, _ = normalise(self.ship([(7250, 7050), (7250, 7250), (7250, 7250)]))
        self.assertEqual(by_id(out)["COT1S"]["capacity100"], 7250)
        f = next(f for f in fixes if f["field"] == "capacity100")
        self.assertEqual((f["tank"], f["old"]), ("COT1S", 7050))

    def test_no_common_value_only_noted(self):
        out, checks, fixes, _ = normalise(self.ship([(7250, 7050), (6100, 6100)]))
        self.assertEqual(by_id(out)["COT1S"]["capacity100"], 7050)
        self.assertTrue(any("same size" in c for c in checks))

    def test_no_box_correction_with_estimated_dimensions(self):
        L = ship([tank("COT3P", "P", 60, 72, 7650)], breadth=None, depth=None)
        out, checks, fixes, _ = normalise(L)
        self.assertEqual(by_id(out)["COT3P"]["capacity100"], 7650)
        self.assertFalse(any(f["field"] == "capacity100" for f in fixes))


class StaleNoteTests(unittest.TestCase):
    def test_notes_about_fixed_things_are_dropped(self):
        fixes = [{"tank": None, "field": "lbp"}, {"tank": "COT1S", "field": "capacity100"},
                 {"tank": "COT2P", "field": "id"}]
        kept = drawing_fixes.filter_ai_notes([
            "lbp not printed, only LOA 160m", "COT1S 7050 m3 differs from COT1P 7250 m3 as printed",
            "inside texts repeat COT3P/S numbering; renumbered sequentially", "slop capacities not printed"], fixes)
        self.assertEqual(kept, ["slop capacities not printed"])


class MessyLabelTests(unittest.TestCase):
    def test_same_tank_listed_twice_is_merged(self):
        tanks = [tank("COT4S", "S", 78, 94, 7250), tank("COT5S", "S", 78, 94), tank("COT4P", "P", 78, 94, 7250)]
        out, _, fixes, _ = normalise(ship(tanks))
        self.assertEqual(sorted(by_id(out)), ["COT4P", "COT4S"])
        removed = [f for f in fixes if f["field"] == "removed"]
        self.assertEqual(removed[0]["old"]["id"], "COT5S")  # kept the one with a capacity

    def test_ballast_without_position_set_aside_when_drawing_is_placed(self):
        tanks = [tank("COT1P", "P", 30, 45), tank("COT1S", "S", 30, 45),
                 tank("WBT1P", "P", 30, 45, typ="ballast"), tank("WBT9P", "P", None, None, typ="ballast")]
        out, _, fixes, _ = normalise(ship(tanks))
        self.assertNotIn("WBT9P", by_id(out))
        self.assertEqual(next(f for f in fixes if f["field"] == "removed")["old"]["id"], "WBT9P")

    def test_nothing_set_aside_when_positions_are_missing_generally(self):
        tanks = [tank("COT1P", "P", None, None), tank("WBT1P", "P", None, None, typ="ballast")]
        out, *_ = normalise(ship(tanks))
        self.assertEqual(len(out["tanks"]), 2)

    def test_garbled_wing_tanks_named_after_cargo_beside_them(self):
        tanks = [tank("COT1P", "P", 30, 45), tank("COT1S", "S", 30, 45), tank("COT2P", "P", 45, 60), tank("COT2S", "S", 45, 60),
                 tank("WBT5PP", "P", 30, 45, typ="ballast"), tank("WBT45S", "S", 30, 45, typ="ballast"),
                 tank("WBT4P", "P", 45, 60, typ="ballast")]
        out, checks, fixes, _ = normalise(ship(tanks))
        self.assertTrue({"WBT1P", "WBT1S", "WBT2P"} <= set(by_id(out)))
        self.assertTrue(any("named after the cargo tank" in c for c in checks))

    def test_clean_wing_tank_labels_are_kept(self):
        tanks = [tank("COT1P", "P", 30, 45), tank("WBT9P", "P", 30, 45, typ="ballast")]
        out, _, fixes, _ = normalise(ship(tanks))
        self.assertIn("WBT9P", by_id(out))
