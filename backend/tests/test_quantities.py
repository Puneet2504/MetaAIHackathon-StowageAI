"""Cargo quantity tests: ASTM 54B volume correction, densities, air/vacuum, barrels, port temperatures."""
import unittest

from app.schemas import Parcel
from app.stowage import quantities as q


class CorrectionTests(unittest.TestCase):
    def test_vcf_matches_table_54b(self):
        self.assertAlmostEqual(q.vcf(0.845, 30), 0.9874, places=4)   # diesel
        self.assertAlmostEqual(q.vcf(0.745, 30), 0.9817, places=4)   # gasoline
        self.assertAlmostEqual(q.vcf(0.800, 15), 1.0, places=9)
        self.assertGreater(q.vcf(0.845, 5), 1.0)                     # cold cargo shrinks

    def test_density_measured_warm_is_corrected_to_15c(self):
        observed = 0.845 * q.vcf(0.845, 30)
        self.assertAlmostEqual(q.rho15_from_observed(observed, 30), 0.845, places=5)

    def test_api_gravity(self):
        self.assertAlmostEqual(q.rho15_from_api(35), 0.849, places=3)


class ConvertTests(unittest.TestCase):
    def conv(self, **kw):
        return q.convert(Parcel(grade="ULSD", **kw), "ULSD", 0.845)

    def test_tonnes_in_air_by_default(self):
        c = self.conv(quantity_t=20000, temp_c=15)
        self.assertAlmostEqual(c["volume15_m3"], 20000 / (0.845 - 0.0011), delta=0.2)
        self.assertEqual(c["basis"], "air")
        self.assertIn("tonnes taken as weight in air", c["assumed"])
        vac = self.conv(quantity_t=20000, temp_c=15, mass_basis="vacuum")
        self.assertAlmostEqual(c["volume15_m3"] / vac["volume15_m3"], 0.845 / 0.8439, places=4)

    def test_warm_cargo_needs_more_room(self):
        cold, warm = self.conv(quantity_t=20000, temp_c=15), self.conv(quantity_t=20000, temp_c=35)
        self.assertGreater(warm["volume_m3"], cold["volume_m3"] * 1.015)
        self.assertEqual(warm["tonnes"], 20000)

    def test_barrels(self):
        c = self.conv(quantity_bbl=100000, temp_c=15)
        self.assertAlmostEqual(c["volume15_m3"], 15898.7, delta=0.1)

    def test_temperature_from_load_port_then_product(self):
        self.assertEqual(self.conv(quantity_t=1000, load_port="Rotterdam (NL)")["temp_source"], "Rotterdam average")
        self.assertEqual(self.conv(quantity_t=1000, load_port="Sikka")["temp_c"], 27)
        c = self.conv(quantity_t=1000, load_port="Atlantis")
        self.assertEqual((c["temp_c"], c["temp_source"]), (25, "typical for the grade"))

    def test_order_values_win_and_are_not_assumed(self):
        c = self.conv(quantity_t=1000, temp_c=30, density_t_m3=0.84, mass_basis="air", load_port="Rotterdam")
        self.assertEqual((c["temp_c"], c["density15"], c["temp_source"], c["density_source"]), (30, 0.84, "order", "order"))
        self.assertEqual(c["assumed"], [])
        self.assertEqual(self.conv(quantity_t=1000, density_t_m3=840)["density15"], 0.84)  # kg/m³ accepted


if __name__ == "__main__":
    unittest.main()
