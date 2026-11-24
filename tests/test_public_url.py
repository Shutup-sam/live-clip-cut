"""The public HTTPS address is exact, while loopback and OAuth remain usable."""
import os
import sys
import tempfile
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

os.environ.update(PEAKCUT_DATA=tempfile.mkdtemp(prefix="pc-public-"), DATABASE_URL="", PEAKCUT_ACCOUNTS="0",
                  PEAKCUT_PUBLIC_URL="https://clips.example.com",
                  PEAKCUT_IG_APP_ID="1234567890", PEAKCUT_IG_APP_SECRET="test-secret", PEAKCUT_IG_CONFIG_ID="")
sys.path.insert(0, ".")
from fastapi.testclient import TestClient
from studio import accounts, config, ctx, db, server

db.init()
public = TestClient(server.app, base_url=config.PUBLIC_URL, follow_redirects=False)
local = TestClient(server.app, base_url=f"http://127.0.0.1:{config.PORT}", follow_redirects=False)
assert local.get("/").status_code == 303
assert local.get("/", headers={"Host": f"localhost:{config.PORT}"}).status_code == 303
assert public.get("/").status_code == 303
assert public.get("/app").status_code == 200
assert public.get("/api/status", headers={"Host": "attacker.example"}).status_code == 403
assert public.get("/api/status", headers={"Host": "attacker.example",
                                          "X-Forwarded-Host": urlparse(config.PUBLIC_URL).netloc,
                                          "X-Forwarded-Proto": "https"}).status_code == 403
assert local.get("/", headers={"X-Forwarded-Host": "attacker.example",
                                "X-Forwarded-Proto": "http"}).headers["location"] == "/app"
config.ACCOUNTS = True
try:
    signed_out = public.get("/app")
    assert (signed_out.status_code, signed_out.headers.get("location")) == (303, "/login")
    assert public.get("/api/status", headers={"Host": "attacker.example"}).status_code == 403
    assert public.post("/api/settings", json={}, headers={"Origin": "https://attacker.example"}).status_code == 403
finally:
    config.ACCOUNTS = False

callback = config.PUBLIC_URL + "/oauth/instagram/callback"
assert accounts.redirect_for("instagram") == callback
with ctx.as_user("u_local"):
    start = accounts.connect_url("instagram")
    query = parse_qs(urlparse(start).query)
    assert query["redirect_uri"] == [callback]
    accounts._take_state(query["state"][0], "instagram")
    try:
        accounts._take_state(query["state"][0], "instagram")
        assert False, "an OAuth state cannot be reused"
    except ValueError:
        pass

class StopExchange(Exception):
    pass

with patch.object(accounts.httpx, "post") as post, patch.object(accounts, "_check", side_effect=StopExchange):
    try:
        accounts._instagram_finish("test-code")
    except StopExchange:
        pass
    assert post.call_args.kwargs["data"]["redirect_uri"] == callback

print("ALL PASSED")
