"""Sign test accounts in through the API and cache their auth headers."""
PASSWORD = "test-password-1"


class SignedIn:
    def __init__(self, client):
        self.client = client
        self.headers: dict[str, dict] = {}

    def __call__(self, name: str) -> dict:
        if name not in self.headers:
            r = self.client.post("/api/auth/signin",
                                 json={"email": f"{name}@test.example", "password": PASSWORD, "create": True})
            r.raise_for_status()
            self.headers[name] = {"Authorization": f"Bearer {r.json()['token']}"}
        return self.headers[name]
