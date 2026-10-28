"""API tests for user workspaces. Run from the backend folder:  python -m unittest discover tests"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app import config
from app.main import app

from auth_helper import SignedIn

LAYOUT = {
    "name": "MT Alice One", "lbp": 160, "breadth": 28, "depth": 16, "summerDraught": 10.5,
    "tanks": [{"id": f"COT{n}{s}", "type": "cargo", "side": s, "xAft": 30 + 20 * (5 - n), "xFwd": 50 + 20 * (5 - n)}
              for n in range(1, 6) for s in "PS"],
}


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(config, "USER_DIR", Path(self.tmp.name))
        self.patch.start()
        self.c = TestClient(app)
        self.h = SignedIn(self.c)

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()


    def test_requires_user(self):
        self.assertEqual(self.c.get("/api/vessels").status_code, 401)

    def test_ships_are_private_to_their_user(self):
        r = self.c.post("/api/vessels", json={"layout": LAYOUT}, headers=self.h("alice"))
        self.assertEqual(r.status_code, 201, r.text)
        vid = r.json()["id"]
        alice = [v["id"] for v in self.c.get("/api/vessels", headers=self.h("alice")).json()]
        bob = [v["id"] for v in self.c.get("/api/vessels", headers=self.h("bob")).json()]
        self.assertIn(vid, alice)
        self.assertNotIn(vid, bob)
        self.assertIn("demo_mr2", bob)
        self.assertEqual(self.c.get(f"/api/vessels/{vid}", headers=self.h("bob")).status_code, 404)
        self.assertEqual(self.c.delete(f"/api/vessels/{vid}", headers=self.h("bob")).status_code, 404)
        self.assertEqual(self.c.delete(f"/api/vessels/{vid}", headers=self.h("alice")).status_code, 204)

    def test_builtin_cannot_be_deleted_or_changed(self):
        self.assertEqual(self.c.delete("/api/vessels/demo_mr2", headers=self.h("bob")).status_code, 403)
        self.assertEqual(self.c.put("/api/vessels/demo_mr2", json={"layout": LAYOUT},
                                    headers=self.h("bob")).status_code, 403)

    def test_save_edit_and_check_round_trip(self):
        vid = self.c.post("/api/vessels", json={"layout": LAYOUT}, headers=self.h("alice")).json()["id"]
        layout = self.c.get(f"/api/vessels/{vid}/layout", headers=self.h("alice")).json()
        layout["name"] = "MT Alice Renamed"
        r = self.c.put(f"/api/vessels/{vid}", json={"layout": layout}, headers=self.h("alice"))
        self.assertEqual(r.json()["particulars"]["name"], "MT Alice Renamed")
        loads = [{"tank_id": "COT1P", "volume_m3": 1000, "density_t_m3": 0.84},
                 {"tank_id": "COT1S", "volume_m3": 1000, "density_t_m3": 0.84}]
        r = self.c.post("/api/stability/check", json={"vessel_id": vid, "loads": loads}, headers=self.h("alice"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["list_deg"], 0.0)

    def test_demo_layout_can_be_copied(self):
        layout = self.c.get("/api/vessels/demo_mr2/layout", headers=self.h("bob")).json()
        r = self.c.post("/api/vessels/preview", json={"layout": layout}, headers=self.h("bob"))
        self.assertEqual(r.status_code, 200, r.text)

    def test_invalid_layout_rejected(self):
        bad = dict(LAYOUT, summerDraught=20)
        self.assertEqual(self.c.post("/api/vessels/preview", json={"layout": bad},
                                     headers=self.h("alice")).status_code, 422)

    def test_read_drawing_without_model_is_503(self):
        with mock.patch.object(config, "MODEL_BASE_URL", ""):
            r = self.c.post("/api/ai/read-drawing", json={"text": "12 cargo tanks"}, headers=self.h("alice"))
        self.assertEqual(r.status_code, 503)

    def test_read_drawing_rejects_non_image(self):
        r = self.c.post("/api/ai/read-drawing", json={"image": "data:text/html;base64,AAAA"},
                        headers=self.h("alice"))
        self.assertEqual(r.status_code, 422)


if __name__ == "__main__":
    unittest.main()
