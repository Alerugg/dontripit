import requests


class Client:
    def __init__(self, base_url: str, api_key: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _get(self, path: str, params: dict | None = None):
        headers = {}
        if self.api_key:
            headers["X-API-Key"] = self.api_key

        response = requests.get(f"{self.base_url}{path}", params=params, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()

    def games(self):
        return self._get("/api/games")

    def cards(self, params: dict | None = None):
        return self._get("/api/cards", params=params)

    def prints(self, params: dict | None = None):
        return self._get("/api/prints", params=params)

    def print(self, print_id: int):
        return self._get(f"/api/prints/{print_id}")

    def sets(self, params: dict | None = None):
        return self._get("/api/sets", params=params)

    def search(self, params: dict | None = None):
        return self._get("/api/search", params=params)
