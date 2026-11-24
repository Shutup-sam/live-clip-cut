"""Unified Login uses Meta's configuration, code exchange, and Facebook Graph host."""
import os
import sys
import tempfile
import time
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
from pathlib import Path
from types import SimpleNamespace

os.environ.update(PEAKCUT_DATA=tempfile.mkdtemp(prefix="pc-unified-"), DATABASE_URL="", PEAKCUT_ACCOUNTS="1",
                  PEAKCUT_PUBLIC_URL="https://clips.example.com", PEAKCUT_VERIFY_EMAIL="0",
                  PEAKCUT_IG_APP_ID="1234567890123456", PEAKCUT_IG_APP_SECRET="test-secret",
                  PEAKCUT_IG_CONFIG_ID="9876543210987654")
sys.path.insert(0, ".")
from fastapi.testclient import TestClient
from studio import accounts, auth, creators, ctx, db, posting, server

db.init()
user, _ = auth._create("owner@example.com", "Owner", password_hash="test-hash")
client = TestClient(server.app, base_url="https://clips.example.com", follow_redirects=False)
client.cookies.set(auth.COOKIE, auth.start_session(user.id))

start = client.get("/oauth/instagram/start")
assert start.status_code == 303
url = urlparse(start.headers["location"])
query = parse_qs(url.query)
assert (url.scheme, url.netloc, url.path) == ("https", "www.facebook.com", "/dialog/oauth")
assert query["client_id"] == ["1234567890123456"]
assert query["config_id"] == ["9876543210987654"]
assert query["redirect_uri"] == ["https://clips.example.com/oauth/instagram/callback"]
assert query["response_type"] == ["code"]
assert query["override_default_response_type"] == ["true"]
assert query["prompt"] == ["login"]
assert "scope" not in query and "client_secret" not in query

class Response:
    status_code = 200
    def __init__(self, data):
        self.data = data
    def json(self):
        return self.data

calls = []
def get(url, params=None, headers=None, timeout=None):
    calls.append((url, params, headers))
    if url.endswith("/oauth/access_token"):
        assert params == {"client_id": "1234567890123456", "client_secret": "test-secret",
                          "redirect_uri": "https://clips.example.com/oauth/instagram/callback", "code": "real-code"}
        return Response({"access_token": "system-user-token"})
    assert headers == {"Authorization": "Bearer system-user-token"}
    if url.endswith("/me"):
        return Response({"client_business_id": "987654321"})
    if url.endswith("/me/assigned_instagram_accounts"):
        return Response({"data": [{"id": "17841400000000000", "username": "creator"}]})
    raise AssertionError(url)

with patch.object(accounts.httpx, "get", side_effect=get):
    rejected = client.get("/oauth/instagram/callback", params={"code": "real-code", "state": "invalid"})
    assert "connect_error" in rejected.headers["location"] and not calls
    done = client.get("/oauth/instagram/callback", params={"code": "real-code", "state": query["state"][0]})
    assert "connected=instagram" in done.headers["location"]
    reused = client.get("/oauth/instagram/callback", params={"code": "real-code", "state": query["state"][0]})
    assert "connect_error" in reused.headers["location"] and len(calls) == 3

with ctx.as_user(user.id):
    linked = accounts.get("ig_17841400000000000")
    assert linked["login"] == "unified" and linked["client_business_id"] == "987654321"
    assert linked["config_id"] == "9876543210987654" and linked["token"] == "system-user-token"
    creator = creators.create({"name": "Immediate", "post_gap_min": 0, "daily_cap": 1})
    assert creator["post_gap_min"] == 0
    assert posting._next_slot(linked["id"], 0, 1) <= time.time() + 1
    tomorrow = time.mktime((*time.localtime(time.time() + 86400)[:3], 0, 0, 0, 0, 0, -1))
    posting._posts.append({"user": user.id, "account": linked["id"], "status": "scheduled", "due_at": (time.time() + tomorrow) / 2})
    assert posting._next_slot(linked["id"], 0, 2) <= time.time() + 1
    assert posting._day(posting._next_slot(linked["id"], 0, 1)) != posting._day(time.time())
    posting._posts.clear()
assert "system-user-token" not in client.get("/api/accounts").text

requests = []
def graph_post(url, data=None, headers=None, timeout=None, content=None):
    requests.append(("POST", url, data, headers))
    if "rupload.facebook.com" in url:
        assert content == b"test-reel" and headers["file_size"] == "9"
        return Response({"success": True})
    assert url.startswith(accounts.IG_UNIFIED + "/")
    if url.endswith("/media"):
        return Response({"id": "container1"})
    if url.endswith("/media_publish"):
        return Response({"id": "media1"})
    raise AssertionError(url)

def graph_get(url, params=None, headers=None, timeout=None):
    requests.append(("GET", url, params, headers))
    assert url.startswith(accounts.IG_UNIFIED + "/")
    if url.endswith("/container1"):
        return Response({"status_code": "FINISHED"})
    if url.endswith("/media1"):
        return Response({"permalink": "https://www.instagram.com/reel/abc/"})
    raise AssertionError(url)

clip_path = Path(tempfile.mkdtemp()) / "clip.mp4"
clip_path.write_bytes(b"test-reel")
def encode(cmd, capture_output):
    assert "-movflags" in cmd and "-vf" in cmd
    Path(cmd[-1]).write_bytes(b"test-reel")
    return SimpleNamespace(returncode=0)

with patch.object(accounts.httpx, "post", side_effect=graph_post), patch.object(accounts.httpx, "get", side_effect=graph_get), patch.object(accounts.subprocess, "run", side_effect=encode):
    with ctx.as_user(user.id):
        result = accounts.instagram_upload(linked, clip_path, "caption", "job-12345678")
assert result == ("media1", "https://www.instagram.com/reel/abc/")
assert requests[0][2]["upload_type"] == "resumable" and "video_url" not in requests[0][2]
assert all("system-user-token" not in (part[1] + str(part[2])) for part in requests)

class Failed(Response):
    status_code = 400
    text = "secret-value-should-not-appear"

next_start = client.get("/oauth/instagram/start")
next_state = parse_qs(urlparse(next_start.headers["location"]).query)["state"][0]
with patch.object(accounts.httpx, "get", return_value=Failed({"error": "bad app"})):
    rejected = client.get("/oauth/instagram/callback", params={"code": "bad-code", "state": next_state})
assert "connect_error" in rejected.headers["location"]
assert "secret-value-should-not-appear" not in rejected.headers["location"]

print("ALL PASSED")
