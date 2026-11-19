"""Thin HTTP client for the API. The UI never imports agents directly."""

from __future__ import annotations

from typing import Any

import httpx
import streamlit as st

from msa.core.config import get_settings


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _client() -> httpx.Client:
    if "_http" not in st.session_state:
        st.session_state["_http"] = httpx.Client(base_url=get_settings().api_url, timeout=60)
    return st.session_state["_http"]


def _headers() -> dict[str, str]:
    tok = st.session_state.get("token")
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def call(method: str, path: str, **kw) -> Any:
    try:
        r = _client().request(method, path, headers=_headers(), **kw)
    except httpx.HTTPError as exc:
        raise ApiError(0, f"Can't reach the API at {get_settings().api_url}. Is it running? ({exc})") from exc
    if r.status_code == 401 and st.session_state.get("token"):
        st.session_state.pop("token", None)
        st.session_state.pop("user", None)
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        if isinstance(detail, list):  # FastAPI validation errors
            detail = "; ".join(f"{'.'.join(map(str, d.get('loc', [])[1:]))}: {d.get('msg')}" for d in detail)
        raise ApiError(r.status_code, str(detail))
    ctype = r.headers.get("content-type", "")
    return r.json() if "json" in ctype else r.content


def get(path: str, **kw) -> Any:
    return call("GET", path, **kw)


def post(path: str, json: dict | None = None, **kw) -> Any:
    return call("POST", path, json=json, **kw)


def patch(path: str, json: dict) -> Any:
    return call("PATCH", path, json=json)


def delete(path: str) -> Any:
    return call("DELETE", path)


def safe(fn, *args, **kw):
    """Run an API call; show the error in the page instead of a stack trace."""
    try:
        return fn(*args, **kw)
    except ApiError as e:
        st.error(e.detail)
        return None
