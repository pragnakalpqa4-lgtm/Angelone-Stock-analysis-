"""Angel One SmartAPI historical-data adapter.

The third-party SDK is imported only for live requests, keeping tests and CSV
analysis usable without credentials or installed dependencies.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping


@dataclass(frozen=True)
class Settings:
    api_key: str
    client_code: str
    pin: str
    totp_secret: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        values = os.environ if env is None else env
        names = (
            "ANGELONE_API_KEY",
            "ANGELONE_CLIENT_CODE",
            "ANGELONE_PIN",
            "ANGELONE_TOTP_SECRET",
        )
        missing = [name for name in names if not values.get(name)]
        if missing:
            raise ValueError("Missing required secrets: " + ", ".join(missing))
        return cls(*(values[name] for name in names))


class AngelOneClient:
    def __init__(self, settings: Settings, client=None, totp_generator=None):
        self.settings = settings
        self._client = client
        self._totp_generator = totp_generator

    def login(self):
        if self._client is None:
            try:
                from SmartApi import SmartConnect
                import pyotp
            except ImportError as exc:
                raise RuntimeError(
                    "Install live dependencies with: pip install -r requirements.txt"
                ) from exc
            self._client = SmartConnect(api_key=self.settings.api_key)
            self._totp_generator = pyotp.TOTP(self.settings.totp_secret)
        if self._totp_generator is None:
            raise RuntimeError("A TOTP generator is required")
        response = self._client.generateSession(
            self.settings.client_code,
            self.settings.pin,
            self._totp_generator.now(),
        )
        if not isinstance(response, dict) or not response.get("status"):
            message = response.get("message", "unknown login error") if isinstance(response, dict) else "invalid response"
            raise RuntimeError(f"Angel One login failed: {message}")
        return response

    def historical(
        self,
        symbol_token: str,
        start: datetime,
        end: datetime,
        *,
        exchange: str = "NSE",
        interval: str = "ONE_DAY",
    ):
        if not symbol_token:
            raise ValueError("symbol_token is required")
        if start >= end:
            raise ValueError("start must be earlier than end")
        if self._client is None:
            self.login()
        response = self._client.getCandleData(
            {
                "exchange": exchange,
                "symboltoken": symbol_token,
                "interval": interval,
                "fromdate": start.strftime("%Y-%m-%d %H:%M"),
                "todate": end.strftime("%Y-%m-%d %H:%M"),
            }
        )
        if not isinstance(response, dict) or not response.get("status"):
            message = response.get("message", "unknown API error") if isinstance(response, dict) else "invalid response"
            raise RuntimeError(f"Historical-data request failed: {message}")
        rows = response.get("data") or []
        return [
            {
                "timestamp": row[0],
                "open": row[1],
                "high": row[2],
                "low": row[3],
                "close": row[4],
                "volume": row[5],
            }
            for row in rows
        ]
