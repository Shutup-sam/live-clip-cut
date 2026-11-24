"""Creator logo settings and the final MP4, using a throwaway data folder."""
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image
import sqlalchemy as sa

os.environ.update(PEAKCUT_DATA=tempfile.mkdtemp(prefix="pc-branding-"), DATABASE_URL="", PEAKCUT_ACCOUNTS="0",
                  PEAKCUT_PUBLIC_URL="http://127.0.0.1:8765")
full_ffmpeg = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
if full_ffmpeg.is_file():
    os.environ["PEAKCUT_FFMPEG_DIR"] = str(full_ffmpeg.parent)
sys.path.insert(0, ".")
from fastapi.testclient import TestClient  # noqa: E402
from studio import auth, branding, creators, ctx, db, posting, render, server  # noqa: E402

with db.engine.begin() as conn:
    conn.execute(sa.text("""CREATE TABLE creator_branding (user_id VARCHAR(40) NOT NULL, creator_id VARCHAR(60) NOT NULL,
        logo_asset_path VARCHAR(100) NOT NULL DEFAULT '', enabled BOOLEAN NOT NULL DEFAULT FALSE,
        position VARCHAR(20) NOT NULL DEFAULT 'top_right', width INTEGER NOT NULL DEFAULT 20,
        opacity INTEGER NOT NULL DEFAULT 100, padding INTEGER NOT NULL DEFAULT 24,
        background_enabled BOOLEAN NOT NULL DEFAULT FALSE, display_duration VARCHAR(12) NOT NULL DEFAULT 'entire',
        custom_start_time FLOAT NOT NULL DEFAULT 0, custom_end_time FLOAT NOT NULL DEFAULT 0,
        updated_at FLOAT NOT NULL, PRIMARY KEY (user_id, creator_id))"""))
    conn.execute(sa.text("INSERT INTO creator_branding (user_id, creator_id, logo_asset_path, updated_at) VALUES ('legacy', 'legacy', 'logo-keep.png', 1)"))
db.init()
with db.engine.begin() as conn:
    old = conn.execute(sa.text("SELECT logo_asset_path, second_enabled, second_position FROM creator_branding WHERE user_id='legacy'")).one()
assert old == ("logo-keep.png", 0, "bottom_right")
c = TestClient(server.app, base_url="http://127.0.0.1:8765")
origin = {"Origin": "http://127.0.0.1:8765"}


def check(name, yes):
    if not yes:
        raise AssertionError(name)
    print("PASS", name)


def logo(fmt="PNG", color=(255, 0, 0, 255)):
    b = io.BytesIO()
    Image.new("RGBA" if fmt == "PNG" else "RGB", (80, 40), color).save(b, fmt)
    return b.getvalue()


with ctx.as_user(auth.local_user().id):
    kreek = creators.create({"name": "Kreek"})["id"]
    snaxx = creators.create({"name": "Snaxx"})["id"]
    check("defaults are stored per creator", branding.get(kreek)["enabled"] is False and branding.get(snaxx)["logo_asset_path"] == "")

    def upload(cid, fmt, second=False, color=(255, 0, 0, 255)):
        ext = "jpg" if fmt == "JPEG" else fmt.lower()
        return c.post(f"/api/creators/{cid}/branding/{'logo2' if second else 'logo'}", files={"file": (f"logo.{ext}", logo(fmt, color), f"image/{ext}")}, headers=origin)

    check("Kreek PNG upload works", upload(kreek, "PNG").status_code == 200)
    check("second logo upload works", upload(kreek, "PNG", second=True, color=(0, 255, 0, 255)).status_code == 200)
    check("second logo is private", c.get(f"/api/creators/{kreek}/branding/logo2").status_code == 200)
    check("Snaxx WEBP upload works", upload(snaxx, "WEBP").status_code == 200)
    check("Snaxx JPG replacement works", upload(snaxx, "JPEG").status_code == 200)
    check("settings are independent", branding.get(kreek)["logo_asset_path"] != branding.get(snaxx)["logo_asset_path"])
    check("logo survives database reload", c.get(f"/api/creators/{kreek}/branding").json()["logo_asset_path"] == branding.get(kreek)["logo_asset_path"])
    check("stored logo is served privately", c.get(f"/api/creators/{kreek}/branding/logo").status_code == 200)
    check("bad image is rejected", c.post(f"/api/creators/{kreek}/branding/logo", files={"file": ("x.png", b"bad", "image/png")}, headers=origin).status_code == 400)
    check("cross-site upload is rejected", c.post(f"/api/creators/{kreek}/branding/logo", files={"file": ("x.png", logo(), "image/png")}, headers={"Origin": "https://evil.example"}).status_code == 403)
    check("cross-site second upload is rejected", c.post(f"/api/creators/{kreek}/branding/logo2", files={"file": ("x.png", logo(), "image/png")}, headers={"Origin": "https://evil.example"}).status_code == 403)
    settings = {"enabled": True, "position": "top_left", "width": 25, "opacity": 100, "padding": 10,
                "background_enabled": False, "display_duration": "entire", "custom_start_time": 0, "custom_end_time": 0,
                "second_enabled": True, "second_position": "bottom_right", "second_width": 25}
    check("branding settings save", c.put(f"/api/creators/{kreek}/branding", json=settings, headers=origin).status_code == 200)
    check("Snaxx settings stay off", branding.get(snaxx)["enabled"] is False)
    check("invalid position rejected", c.put(f"/api/creators/{kreek}/branding", json={"position": "../../etc"}, headers=origin).status_code == 400)
    check("logo path cannot be set by clients", c.put(f"/api/creators/{kreek}/branding", json={"logo_asset_path": "other.png"}, headers=origin).status_code == 400)
    check("second logo path cannot be set by clients", c.put(f"/api/creators/{kreek}/branding", json={"second_logo_asset_path": "other.png"}, headers=origin).status_code == 400)
    check("invalid second position rejected", c.put(f"/api/creators/{kreek}/branding", json={"second_position": "../../etc"}, headers=origin).status_code == 400)
    try:
        posting._require_branding({"creator": kreek}, {"status": "done"})
    except ValueError:
        check("old unbranded clips cannot enter manual or automatic posting", True)
    else:
        raise AssertionError("unbranded clip entered posting")
    try:
        posting._require_branding({"creator": kreek}, {"status": "done", "branding_logo": branding.get(kreek)["logo_asset_path"]})
    except ValueError:
        check("second logo is required before posting", True)
    else:
        raise AssertionError("clip without second logo entered posting")
    posting._require_branding({"creator": kreek}, {"status": "done", "branding_logo": branding.get(kreek)["logo_asset_path"],
                              "branding_second_logo": branding.get(kreek)["second_logo_asset_path"]})
    check("branded clips can enter posting", True)

    old_size = render.W, render.H
    render.W, render.H = 180, 320
    try:
        for size in ((320, 180), (180, 320)):
            folder = Path(tempfile.mkdtemp(prefix="brand-render-"))
            src = folder / "source.mp4"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=blue:s={size[0]}x{size[1]}:r=10:d=2",
                            "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264", "-c:a", "aac", str(src)], check=True)
            out = render.render_clip(src, folder / "clips", "branded", {"start": 0.0, "end": 2.0, "hook": "", "captions": False, "layout": "crop"},
                                     [], *size, branding.get(kreek), branding.logo_path(kreek, branding.get(kreek)),
                                     branding.logo_path(kreek, branding.get(kreek), second=True))
            if size == (320, 180):
                second_only = {**branding.get(kreek), "enabled": False}
                solo = render.render_clip(src, folder / "clips", "second_only", {"start": 0.0, "end": 2.0, "hook": "", "captions": False, "layout": "crop"},
                                          [], *size, second_only, None, branding.logo_path(kreek, second_only, second=True))
                shot = subprocess.run(["ffmpeg", "-v", "error", "-i", str(folder / "clips" / solo["file"]), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True, check=True)
                solo_rgb = Image.open(io.BytesIO(shot.stdout)).convert("RGB")
                check("second logo works without first logo", solo_rgb.getpixel((150, 300))[1] > 150 and solo_rgb.getpixel((18, 18))[2] > 150)
                try:
                    render.render_clip(src, folder / "clips", "missing", {"start": 0.0, "end": 2.0, "hook": "", "captions": False},
                                       [], *size, branding.get(kreek), folder / "missing.png")
                except ValueError as e:
                    check("missing logo fails instead of producing an unbranded clip", "logo" in str(e).lower())
                else:
                    raise AssertionError("missing logo must fail")
            video = folder / "clips" / out["file"]
            cr, cg, cb = Image.open(folder / "clips" / out["cover"]).convert("RGB").getpixel((18, 18))
            check(f"{size} downloaded cover matches branded video", cr > 150 and cb < 100)
            second_rgb = Image.open(folder / "clips" / out["cover"]).convert("RGB").getpixel((150, 300))
            check(f"{size} second logo visible on cover", second_rgb[1] > 150 and second_rgb[0] < 100)
            probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(video)], capture_output=True, text=True, check=True)
            check(f"{size} output keeps audio", "audio" in probe.stdout and "video" in probe.stdout)
            for at in (0, 1, 1.9):
                frame = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(video), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True, check=True)
                rgb = Image.open(io.BytesIO(frame.stdout)).convert("RGB")
                r, g, b = rgb.getpixel((18, 18))
                check(f"{size} logo visible at {at}s", r > 170 and g < 80 and b < 80)
                r2, g2, b2 = rgb.getpixel((150, 300))
                check(f"{size} second logo visible at {at}s", g2 > 150 and r2 < 100 and b2 < 100)
            plain = render.render_clip(src, folder / "clips", "plain", {"start": 0.0, "end": 2.0, "hook": "", "captions": False, "layout": "crop"}, [], *size)
            frame = subprocess.run(["ffmpeg", "-v", "error", "-i", str(folder / "clips" / plain["file"]), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True, check=True)
            r, g, b = Image.open(io.BytesIO(frame.stdout)).convert("RGB").getpixel((18, 18))
            check(f"{size} disabled output has no logo", b > r)
            if size == (320, 180):
                custom = {**branding.get(kreek), "display_duration": "custom", "custom_start_time": .5, "custom_end_time": 1.5,
                          "background_enabled": True, "opacity": 80, "second_enabled": False}
                timed = render.render_clip(src, folder / "clips", "timed", {"start": 0.0, "end": 2.0, "hook": "", "captions": False, "layout": "crop"}, [], *size,
                                           custom, branding.logo_path(kreek, custom))
                def pixel(at):
                    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(folder / "clips" / timed["file"]), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True, check=True).stdout
                    return Image.open(io.BytesIO(raw)).convert("RGB").getpixel((30, 30))
                check("custom logo starts after its start time", pixel(.1)[2] > pixel(.1)[0])
                check("custom logo appears during its range", pixel(1)[0] > pixel(1)[2])
                check("custom logo stops after its end time", pixel(1.9)[2] > pixel(1.9)[0])
    finally:
        render.W, render.H = old_size
    check("removing second logo leaves the first", c.delete(f"/api/creators/{kreek}/branding/logo2", headers=origin).status_code == 200
          and branding.get(kreek)["logo_asset_path"] != "" and branding.get(kreek)["second_enabled"] is False)
