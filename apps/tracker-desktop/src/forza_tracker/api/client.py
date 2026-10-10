from __future__ import annotations

from typing import Any

import requests

class ApiClient:
    def __init__(self, base_url: str, timeout_seconds: int = 5, api_key: str | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._api_key = api_key

    def upload_race(self, race: dict[str, Any]) -> dict[str, Any]:
        return self._post("/api/races", race)

    def report_error(self, report: dict[str, Any]) -> dict[str, Any]:
        return self._post("/api/client-errors", report)

    def recommend_championship(self, event_plan: list[str], car_class: str) -> dict[str, Any]:
        return self._post(
            "/api/recommendations",
            {"event_plan": event_plan, "car_class": car_class},
        )

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        response = requests.post(
            f"{self._base_url}{path}",
            json=payload,
            headers=headers,
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        return response.json()
