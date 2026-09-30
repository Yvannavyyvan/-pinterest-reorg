"""Pinterest API v5 client: OAuth login+refresh, paginated boards/pins, update/move/delete.
Docs: https://developers.pinterest.com/docs/api/v5/"""
import base64
import json
import os
import time
from pathlib import Path
from urllib.parse import urlencode

import requests

API_BASE = "https://api.pinterest.com/v5"
OAUTH_AUTH_URL = "https://www.pinterest.com/oauth/"
OAUTH_TOKEN_URL = "https://api.pinterest.com/v5/oauth/token"
TOKEN_FILE = Path(__file__).parent / "token.json"

SCOPES = [
    "boards:read",
    "boards:write",
    "pins:read",
    "pins:write",
    "user_accounts:read",
]


class PinterestClient:
    def __init__(self, app_id: str, app_secret: str, redirect_uri: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self.redirect_uri = redirect_uri
        self.access_token = None
        self.refresh_token = None
        self._load_token()

    # ---------- Auth ----------

    def _load_token(self):
        if TOKEN_FILE.exists():
            data = json.loads(TOKEN_FILE.read_text())
            self.access_token = data.get("access_token")
            self.refresh_token = data.get("refresh_token")

    def _save_token(self, data: dict):
        TOKEN_FILE.write_text(json.dumps(data, separators=(",", ":")))
        self.access_token = data.get("access_token")
        self.refresh_token = data.get("refresh_token")

    def build_authorize_url(self, state: str = "reorg") -> str:
        params = {
            "client_id": self.app_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": ",".join(SCOPES),
            "state": state,
        }
        return f"{OAUTH_AUTH_URL}?{urlencode(params)}"

    def exchange_code_for_token(self, code: str):
        basic = base64.b64encode(f"{self.app_id}:{self.app_secret}".encode()).decode()
        resp = requests.post(
            OAUTH_TOKEN_URL,
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
            },
        )
        resp.raise_for_status()
        self._save_token(resp.json())

    def refresh_access_token(self):
        if not self.refresh_token:
            raise RuntimeError("No refresh token stored. Run the login step again.")
        basic = base64.b64encode(f"{self.app_id}:{self.app_secret}".encode()).decode()
        resp = requests.post(
            OAUTH_TOKEN_URL,
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        # Pinterest may omit refresh_token on refresh; keep the old one if so.
        data.setdefault("refresh_token", self.refresh_token)
        self._save_token(data)

    def _headers(self):
        if not self.access_token:
            raise RuntimeError("Not logged in yet. Run: python main.py login")
        return {"Authorization": f"Bearer {self.access_token}"}

    def _request(self, method, path, **kwargs):
        url = f"{API_BASE}{path}"
        resp = requests.request(method, url, headers=self._headers(), **kwargs)
        if resp.status_code == 401:
            # try one refresh, then retry once
            self.refresh_access_token()
            resp = requests.request(method, url, headers=self._headers(), **kwargs)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", "5"))
            time.sleep(wait)
            resp = requests.request(method, url, headers=self._headers(), **kwargs)
        resp.raise_for_status()
        return resp

    # ---------- Boards ----------

    def list_boards(self):
        boards = []
        bookmark = None
        while True:
            params = {"page_size": 100}
            if bookmark:
                params["bookmark"] = bookmark
            resp = self._request("GET", "/boards", params=params).json()
            boards.extend(resp.get("items", []))
            bookmark = resp.get("bookmark")
            if not bookmark:
                break
        return boards

    def create_board(self, name: str, description: str = ""):
        return self._request(
            "POST", "/boards", json={"name": name, "description": description}
        ).json()

    def rename_board(self, board_id: str, name: str = None, description: str = None):
        payload = {}
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        return self._request("PATCH", f"/boards/{board_id}", json=payload).json()

    def delete_board(self, board_id: str):
        self._request("DELETE", f"/boards/{board_id}")

    # ---------- Pins ----------

    def list_pins(self, board_id: str = None):
        pins = []
        bookmark = None
        path = f"/boards/{board_id}/pins" if board_id else "/pins"
        while True:
            params = {"page_size": 100}
            if bookmark:
                params["bookmark"] = bookmark
            resp = self._request("GET", path, params=params).json()
            pins.extend(resp.get("items", []))
            bookmark = resp.get("bookmark")
            if not bookmark:
                break
        return pins

    def list_all_pins(self, boards: list):
        """Fetch pins per board so we always know each pin's current board_id."""
        all_pins = []
        for b in boards:
            pins = self.list_pins(board_id=b["id"])
            for p in pins:
                p["_board_id"] = b["id"]
                p["_board_name"] = b.get("name")
            all_pins.extend(pins)
        return all_pins

    def update_pin(self, pin_id: str, **fields):
        """fields can include title, description, board_id, link, etc."""
        return self._request("PATCH", f"/pins/{pin_id}", json=fields).json()

    def move_pin(self, pin_id: str, board_id: str):
        return self.update_pin(pin_id, board_id=board_id)

    def rename_pin(self, pin_id: str, title: str = None, description: str = None):
        fields = {}
        if title is not None:
            fields["title"] = title
        if description is not None:
            fields["description"] = description
        return self.update_pin(pin_id, **fields)

    def delete_pin(self, pin_id: str):
        self._request("DELETE", f"/pins/{pin_id}")
