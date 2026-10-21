"""Thin client for the Smart Orders REST API. The caller's personal token is
passed through, so every permission and scope is enforced by Django."""
import os

import httpx

API_BASE = os.environ.get("SMART_ORDERS_API", "http://app:8000").rstrip("/")


class ApiError(Exception):
    def __init__(self, status: int, detail):
        super().__init__(str(detail))
        self.status = status
        self.detail = detail


def call(token: str, method: str, path: str, *, params=None, json=None):
    try:
        res = httpx.request(
            method, f"{API_BASE}{path}", params=params, json=json, timeout=30,
            headers={"Authorization": f"Bearer {token}"},
        )
    except httpx.HTTPError as exc:
        raise ApiError(0, f"השרת לא זמין ({type(exc).__name__})")
    try:
        body = res.json()
    except ValueError:
        body = {"detail": res.text[:200]}
    if res.status_code >= 400:
        raise ApiError(res.status_code, body)
    return body
