"""Account and sign-in tests. Run from the backend folder:  python -m unittest discover tests"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app import config, users
from app.main import app

PW = "correct-horse-1"


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        for p in (mock.patch.object(config, "USER_DIR", Path(self.tmp.name)),
                  mock.patch.object(config, "SECRET_KEY", "")):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        users._failures.clear()
        self.c = TestClient(app)

    def signin(self, email="ann@test.example", password=PW, create=False):
        return self.c.post("/api/auth/signin", json={"email": email, "password": password, "create": create})

    def bearer(self, token):
        return {"Authorization": f"Bearer {token}"}

    def test_new_email_asks_to_confirm_then_registers(self):
        r = self.signin()
        self.assertEqual(r.json()["status"], "new_account")
        self.assertFalse((Path(self.tmp.name) / "accounts.json").exists())
        r = self.signin(create=True)
        self.assertTrue(r.json()["created"])
        me = self.c.get("/api/auth/me", headers=self.bearer(r.json()["token"]))
        self.assertEqual(me.json(), {"email": "ann@test.example"})

    def test_existing_email_signs_in_and_checks_password(self):
        self.signin(create=True)
        r = self.signin(email="  ANN@test.example ")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["created"])
        self.assertEqual(self.signin(password="wrong-password").status_code, 401)

    def test_create_on_existing_email_does_not_replace_password(self):
        self.signin(create=True)
        self.assertEqual(self.signin(password="another-pass", create=True).status_code, 401)
        self.assertEqual(self.signin().status_code, 200)

    def test_short_password_and_bad_email_rejected(self):
        self.assertEqual(self.signin(password="short", create=True).status_code, 422)
        self.assertEqual(self.signin(email="not-an-email", create=True).status_code, 422)

    def test_password_is_not_stored_in_plain_text(self):
        self.signin(create=True)
        stored = (Path(self.tmp.name) / "accounts.json").read_text()
        self.assertNotIn(PW, stored)
        self.assertIn("password_hash", stored)

    def test_tampered_or_expired_token_rejected(self):
        token = self.signin(create=True).json()["token"]
        body, sig = token.split(".")
        bad = ("A" if sig[0] != "A" else "B") + sig[1:]
        self.assertEqual(self.c.get("/api/auth/me", headers=self.bearer(f"{body}.{bad}")).status_code, 401)
        user_id = users.verify_token(token)
        expired = users.make_token(user_id, lifetime_s=-10)
        self.assertEqual(self.c.get("/api/vessels", headers=self.bearer(expired)).status_code, 401)
        self.assertEqual(self.c.get("/api/vessels").status_code, 401)

    def test_repeated_wrong_passwords_lock_the_email(self):
        self.signin(create=True)
        for _ in range(users.MAX_FAILURES):
            self.assertEqual(self.signin(password="wrong-password").status_code, 401)
        self.assertEqual(self.signin().status_code, 429)

    def test_accounts_have_separate_workspaces(self):
        a = self.bearer(self.signin(create=True).json()["token"])
        b = self.bearer(self.signin(email="ben@test.example", create=True).json()["token"])
        layout = self.c.get("/api/vessels/demo_mr2/layout", headers=a).json()
        vid = self.c.post("/api/vessels", json={"layout": layout}, headers=a).json()["id"]
        self.assertIn(vid, [v["id"] for v in self.c.get("/api/vessels", headers=a).json()])
        self.assertNotIn(vid, [v["id"] for v in self.c.get("/api/vessels", headers=b).json()])

    def test_demo_user_seeded_once_and_never_overwritten(self):
        uid = users.ensure_demo_user()
        r = self.signin(email=config.DEMO_USER_EMAIL, password=config.DEMO_USER_PASSWORD)
        self.assertEqual(r.status_code, 200)
        h = self.bearer(r.json()["token"])
        own = [v for v in self.c.get("/api/vessels", headers=h).json() if v["owned"]]
        self.assertEqual([v["name"] for v in own], ["MT Synth Ray"])
        self.assertEqual(len(self.c.get("/api/stowage/plans", headers=h).json()), 1)
        # A second start-up keeps the account and does not seed again.
        self.c.delete(f"/api/vessels/{own[0]['id']}", headers=h)
        self.assertEqual(users.ensure_demo_user(), uid)
        accounts = json.loads((Path(self.tmp.name) / "accounts.json").read_text())
        self.assertEqual(len(accounts), 1)
        self.assertEqual(len(self.c.get("/api/stowage/plans", headers=h).json()), 1)


if __name__ == "__main__":
    unittest.main()
