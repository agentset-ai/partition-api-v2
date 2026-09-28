import requests

US = {"AGENTSET_REGION": "us"}
EU = {
    "AGENTSET_REGION": "eu",
    "DATALAB_PROCESSING_LOCATION": "eu",
    "R2_ENDPOINT_URL": "https://account.eu.r2.cloudflarestorage.com",
    "REDIS_HOST": "eu-redis.example.com",
}


class FakeResponse:
    def __init__(
        self, status_code=200, json_data=None, content=b"", headers=None, url=""
    ):
        self.status_code = status_code
        self._json = json_data
        self.content = content
        self.headers = requests.structures.CaseInsensitiveDict(headers or {})
        self.url = url

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"{self.status_code} Error for url: {self.url}", response=self
            )


class FakeSession:
    """Records requests and replies with queued responses, in order."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def _request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)

    def get(self, url, **kwargs):
        return self._request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._request("POST", url, **kwargs)

    def put(self, url, **kwargs):
        return self._request("PUT", url, **kwargs)

    def delete(self, url, **kwargs):
        return self._request("DELETE", url, **kwargs)


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.set_calls = []

    def set(self, key, value, **kwargs):
        self.set_calls.append({"key": key, "value": value, **kwargs})
        self.store[key] = value.encode() if isinstance(value, str) else value

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        return 1 if self.store.pop(key, None) is not None else 0
