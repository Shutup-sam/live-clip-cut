"""Per-creator logo settings and persistent assets."""
import io
import math
import re
import json
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import sqlalchemy as sa
from PIL import Image, UnidentifiedImageError

from . import creators, ctx, db

DEFAULTS = {"logo_asset_path": "", "enabled": False, "position": "top_right", "width": 20,
            "x": 50, "y": 50,
            "opacity": 100, "padding": 24, "background_enabled": False, "display_duration": "entire",
            "custom_start_time": 0.0, "custom_end_time": 0.0,
            "second_logo_asset_path": "", "second_enabled": False, "second_position": "bottom_right", "second_width": 15,
            "second_x": 50, "second_y": 50}
MAX_BYTES = 5 * 1024 * 1024


def get(cid: str) -> dict:
    if not creators.get(cid):
        raise KeyError(cid)
    with db.engine.begin() as conn:
        row = conn.execute(sa.select(db.creator_branding).where(
            db.creator_branding.c.user_id == ctx.uid(), db.creator_branding.c.creator_id == cid)).mappings().first()
    return {**DEFAULTS, **dict(row or {})}


def save(cid: str, changes: dict) -> dict:
    old = get(cid)
    if not isinstance(changes, dict) or any(k not in DEFAULTS or k in ("logo_asset_path", "second_logo_asset_path") for k in changes):
        raise ValueError("Invalid branding setting.")
    new = {**old, **changes}
    if new["position"] not in ("top_left", "top_right", "bottom_left", "bottom_right", "center", "custom"):
        raise ValueError("Choose a logo position.")
    if new["second_position"] not in ("top_left", "top_right", "bottom_left", "bottom_right", "center", "custom"):
        raise ValueError("Choose a second logo position.")
    if new["display_duration"] not in ("entire", "custom"):
        raise ValueError("Choose a display duration.")
    for k, lo, hi in (("width", 1, 80), ("second_width", 1, 80), ("opacity", 0, 100), ("padding", 0, 300),
                      ("x", 0, 100), ("y", 0, 100), ("second_x", 0, 100), ("second_y", 0, 100)):
        try:
            new[k] = int(new[k])
        except (TypeError, ValueError):
            raise ValueError(f"Invalid {k}.") from None
        if not lo <= new[k] <= hi:
            raise ValueError(f"{k} must be between {lo} and {hi}.")
    for k in ("enabled", "second_enabled", "background_enabled"):
        if not isinstance(new[k], bool):
            raise ValueError(f"Invalid {k}.")
    try:
        new["custom_start_time"] = float(new["custom_start_time"])
        new["custom_end_time"] = float(new["custom_end_time"])
    except (TypeError, ValueError):
        raise ValueError("Invalid display times.") from None
    if (not math.isfinite(new["custom_start_time"]) or not math.isfinite(new["custom_end_time"])
            or new["custom_start_time"] < 0 or new["custom_end_time"] < 0
            or (new["display_duration"] == "custom" and new["custom_end_time"] <= new["custom_start_time"])):
        raise ValueError("End time must be after start time.")
    if new["enabled"] and not new["logo_asset_path"]:
        raise ValueError("Upload a logo before enabling branding.")
    if new["second_enabled"] and not new["second_logo_asset_path"]:
        raise ValueError("Upload a second logo before enabling it.")
    _store(cid, new)
    return get(cid)


def _store(cid: str, values: dict):
    row = {k: values[k] for k in DEFAULTS}
    row.update(user_id=ctx.uid(), creator_id=cid, updated_at=time.time())
    with db.engine.begin() as conn:
        conn.execute(sa.delete(db.creator_branding).where(db.creator_branding.c.user_id == ctx.uid(), db.creator_branding.c.creator_id == cid))
        conn.execute(sa.insert(db.creator_branding).values(**row))
    ctx.bump()


def upload(cid: str, filename: str, content: bytes, second: bool = False) -> dict:
    old = get(cid)
    ext = Path(filename).suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4") or len(content) > MAX_BYTES or not content:
        raise ValueError("Upload a PNG, JPG, GIF, WEBP or MP4 logo under 5 MB.")
    if ext == ".mp4":
        with tempfile.NamedTemporaryFile(suffix=".mp4") as sample:
            sample.write(content)
            sample.flush()
            try:
                out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", sample.name],
                                     capture_output=True, text=True, timeout=15, check=True)
                info = json.loads(out.stdout)
                video = next(s for s in info["streams"] if s["codec_type"] == "video")
                duration = float(info["format"]["duration"])
                if ("mp4" not in info["format"].get("format_name", "") or not 0 < duration <= 30
                        or not 0 < int(video["width"]) <= 4096 or not 0 < int(video["height"]) <= 4096):
                    raise ValueError("MP4 logo must be at most 30 seconds and 4096 pixels per side.")
            except (subprocess.SubprocessError, OSError, KeyError, StopIteration, ValueError) as e:
                raise ValueError("That MP4 logo could not be opened, or exceeds 30 seconds.") from e
        saved = content
    else:
        try:
            with Image.open(io.BytesIO(content)) as img:
                if img.format not in ("PNG", "JPEG", "WEBP", "GIF") or img.width > 4096 or img.height > 4096:
                    raise ValueError("Logo must be under 4096 pixels per side.")
                frames = getattr(img, "n_frames", 1)
                if frames > 150 or frames * img.width * img.height > 20_000_000:
                    raise ValueError("Animated logo has too many frames or pixels.")
                if frames > 1:
                    duration = 0
                    pictures = []
                    delays = []
                    for frame in range(frames):
                        img.seek(frame)
                        delay = max(20, img.info.get("duration", 100))
                        duration += delay
                        if img.format == "WEBP":
                            pictures.append(img.convert("RGBA").copy())
                            delays.append(delay)
                    if duration > 30000:
                        raise ValueError("Animated logos can be at most 30 seconds long.")
                    if img.format == "WEBP":
                        buffer = io.BytesIO()
                        pictures[0].save(buffer, "GIF", save_all=True, append_images=pictures[1:], duration=delays, loop=0)
                        saved = buffer.getvalue()
                        ext = ".gif"
                    else:
                        saved = content
                else:
                    img.load()
                    buffer = io.BytesIO()
                    img.convert("RGBA").save(buffer, "PNG")
                    saved = buffer.getvalue()
                    ext = ".png"
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
            raise ValueError("That logo could not be opened as an image.") from e
    folder = creators.root(cid) / "branding"
    folder.mkdir(parents=True, exist_ok=True)
    name = f"logo-{uuid.uuid4().hex}{ext}"
    target = folder / name
    target.write_bytes(saved)
    try:
        _store(cid, {**old, ("second_logo_asset_path" if second else "logo_asset_path"): name})
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return get(cid)


def remove(cid: str, second: bool = False) -> dict:
    old = get(cid)
    _store(cid, {**old, ("second_logo_asset_path" if second else "logo_asset_path"): "",
                 ("second_enabled" if second else "enabled"): False})
    return get(cid)


def logo_path(cid: str, settings: dict, second: bool = False) -> Path | None:
    name = settings.get("second_logo_asset_path" if second else "logo_asset_path") or ""
    if not re.fullmatch(r"logo-[0-9a-f]{32}\.(png|gif|webp|mp4)", name):
        return None
    return creators.root(cid) / "branding" / name
