import unittest
from datetime import datetime

from angelone import AngelOneClient, Settings


class FakeTotp:
    def now(self):
        return "123456"


class FakeClient:
    def __init__(self):
        self.request = None

    def generateSession(self, client_code, pin, totp):
        return {"status": True, "data": {"jwtToken": "hidden"}}

    def getCandleData(self, request):
        self.request = request
        return {
            "status": True,
            "data": [["2026-01-01T09:15:00+05:30", 100, 110, 99, 108, 500]],
        }

    def ltpData(self, exchange, symbol, symbol_token):
        return {"status": True, "data": {
            "open": 100, "high": 110, "low": 99, "close": 102, "ltp": 108,
        }}


class AngelOneTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings("key", "client", "pin", "secret")

    def test_settings_reports_all_missing_secrets(self):
        with self.assertRaisesRegex(ValueError, "ANGELONE_API_KEY"):
            Settings.from_env({})

    def test_login_and_historical_request(self):
        sdk = FakeClient()
        client = AngelOneClient(self.settings, sdk, FakeTotp())
        client.login()
        rows = client.historical(
            "3045",
            datetime(2026, 1, 1),
            datetime(2026, 1, 31),
        )
        self.assertEqual(rows[0]["close"], 108)
        self.assertEqual(sdk.request["symboltoken"], "3045")
        self.assertEqual(sdk.request["interval"], "ONE_DAY")

    def test_rejects_invalid_date_range(self):
        client = AngelOneClient(self.settings, FakeClient(), FakeTotp())
        with self.assertRaises(ValueError):
            client.historical(
                "3045", datetime(2026, 1, 2), datetime(2026, 1, 1)
            )

    def test_live_quote(self):
        client = AngelOneClient(self.settings, FakeClient(), FakeTotp())
        quote = client.quote("SBIN-EQ", "3045")
        self.assertEqual(quote["last_price"], 108)
        self.assertEqual(quote["exchange"], "NSE")

    def test_api_failure_does_not_expose_credentials(self):
        class FailedClient(FakeClient):
            def generateSession(self, client_code, pin, totp):
                return {"status": False, "message": "Denied"}

        client = AngelOneClient(self.settings, FailedClient(), FakeTotp())
        with self.assertRaisesRegex(RuntimeError, "Denied"):
            client.login()


if __name__ == "__main__":
    unittest.main()
