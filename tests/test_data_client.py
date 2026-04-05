from polymarket_bot.config import BotConfig
from polymarket_bot.data_client import ResilientDataClient


class FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400 and self.status_code != 429:
            raise RuntimeError(f"status={self.status_code}")

    def json(self):
        return self._payload


class FlakySession:
    def __init__(self):
        self.calls = 0

    def get(self, _url, params=None, timeout=None):
        self.calls += 1
        if self.calls == 1:
            return FakeResponse(429, {"error": "rate limit"})
        if params and params.get("token_id"):
            return FakeResponse(200, {"asks": [{"price": "0.42"}]})
        return FakeResponse(
            200,
            [
                {
                    "id": "1",
                    "question": "Will Team A win?",
                    "slug": "team-a-win",
                    "liquidityNum": 1000,
                    "active": True,
                    "closed": False,
                    "acceptingOrders": True,
                    "outcomes": "[\"Yes\", \"No\"]",
                    "clobTokenIds": "[\"y\", \"n\"]",
                }
            ],
        )


def _cfg() -> BotConfig:
    base = BotConfig.from_env()
    return BotConfig(
        **{
            **base.__dict__,
            "max_request_retries": 2,
            "retry_backoff_seconds": 0.0,
        }
    )


def test_data_client_retries_transient_429():
    client = ResilientDataClient(_cfg())
    client.session = FlakySession()
    markets = client.fetch_active_markets(1)
    assert len(markets) == 1
    assert client.session.calls >= 2
