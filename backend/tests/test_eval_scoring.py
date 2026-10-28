"""Tests for the model test set's scorer and case files. Run from the backend folder:  python -m unittest discover tests"""
import copy
import unittest

from evals import run_evals
from evals.score import score_drawing, score_instruction, score_order

ORDER = {"parcels": [{"grade": "Jet A-1", "quantity_t": 12000, "quantity_m3": None, "density_t_m3": None,
                      "load_port": "Fawley", "discharge_port": "Dublin"}], "needs_note": False}


class OrderScoringTests(unittest.TestCase):
    def answer(self, **change):
        a = {"parcels": copy.deepcopy(ORDER["parcels"]), "notes": []}
        a["parcels"][0].update(change)
        return a

    def test_perfect_answer_passes(self):
        self.assertEqual(score_order(ORDER, self.answer()), [])

    def test_grade_alias_and_port_case_still_pass(self):
        self.assertEqual(score_order(ORDER, self.answer(grade="jet", discharge_port="DUBLIN")), [])

    def test_wrong_quantity_fails(self):
        self.assertTrue(score_order(ORDER, self.answer(quantity_t=12500)))

    def test_invented_value_fails(self):
        problems = score_order(ORDER, self.answer(density_t_m3=0.8))
        self.assertTrue(any("invented" in p for p in problems))

    def test_missing_note_fails(self):
        exp = dict(ORDER, needs_note=True)
        self.assertTrue(score_order(exp, self.answer()))
        self.assertEqual(score_order(exp, {**self.answer(), "notes": ["no density"]}), [])

    def test_extra_parcel_fails(self):
        a = self.answer()
        a["parcels"].append(dict(a["parcels"][0], grade="ULSD"))
        self.assertTrue(score_order(ORDER, a))


class DrawingScoringTests(unittest.TestCase):
    EXP = {"layout": {"name": None, "lbp": 120, "breadth": 21, "depth": 11, "summerDraught": 8, "tanks": [
        {"id": "COT1P", "type": "cargo", "side": "P", "xAft": 90, "xFwd": 105, "capacity100": None, "lineId": None}]},
        "needs_note": False}

    def test_within_tolerance_passes(self):
        act = copy.deepcopy(self.EXP)
        act["layout"]["tanks"][0]["xAft"] = 90.4
        act["layout"]["name"] = "Some name"  # a name is not required when none was given
        self.assertEqual(score_drawing(self.EXP, act), [])

    def test_extra_tank_and_invented_capacity_fail(self):
        act = copy.deepcopy(self.EXP)
        act["layout"]["tanks"][0]["capacity100"] = 2000
        act["layout"]["tanks"].append(dict(act["layout"]["tanks"][0], id="COT9P"))
        problems = score_drawing(self.EXP, act)
        self.assertTrue(any("unexpected tanks" in p for p in problems))
        self.assertTrue(any("invented" in p for p in problems))


class InstructionScoringTests(unittest.TestCase):
    def test_order_of_tanks_and_grade_names_do_not_matter(self):
        exp = {"constraints": {"empty_tanks": ["COT4P", "COT4S"], "fixed_grades": {"COT1P": "ULSD"},
                               "max_trim_m": None}, "needs_note": False}
        act = {"constraints": {"empty_tanks": ["COT4S", "COT4P"], "fixed_grades": {"COT1P": "diesel"},
                               "max_trim_m": None}, "notes": []}
        self.assertEqual(score_instruction(exp, act), [])

    def test_wrong_trim_fails(self):
        exp = {"constraints": {"empty_tanks": [], "fixed_grades": {}, "max_trim_m": 1.2}, "needs_note": False}
        act = {"constraints": {"empty_tanks": [], "fixed_grades": {}, "max_trim_m": 1.0}, "notes": []}
        self.assertTrue(score_instruction(exp, act))


class CaseFileTests(unittest.TestCase):
    def test_dry_run_scores_every_suite_perfectly(self):
        self.assertEqual(run_evals.main(["--dry-run", "--no-report"]), 0)

    def test_case_ids_are_unique(self):
        for suite in run_evals.SUITES:
            ids = [c["id"] for c in run_evals.load_cases(suite)]
            self.assertEqual(len(ids), len(set(ids)), suite)


if __name__ == "__main__":
    unittest.main()
