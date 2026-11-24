"""Per-creator logo settings and persistent assets."""
import io
import math
import re
import time
import uuid
from pathlib import Path

import sqlalchemy as sa
from PIL import Image, UnidentifiedImageError

from . import creators, ctx, db

DEFAULTS = {"logo_asset_path": "", "enabled": False, "position": "top_right", "width": 20,
            "opacity": 100, "padding": 24, "background_enabled": False, "display_duration": "entire",
            "custom_start_time": 0.0, "custom_end_time": 0.0,
            "second_logo_asset_path": "", "second_enabled": False, "second_position": "bottom_right", "second_width": 15}
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
    if new["position"] not in ("top_left", "top_right", "bottom_left", "bottom_right", "center"):
        raise ValueError("Choose a logo position.")
    if new["second_position"] not in ("top_left", "top_right", "bottom_left", "bottom_right", "center"):
        raise ValueError("Choose a second logo position.")
    if new["display_duration"] not in ("entire", "custom"):
        raise ValueError("Choose a display duration.")
    for k, lo, hi in (("width", 1, 80), ("second_width", 1, 80), ("opacity", 0, 100), ("padding", 0, 300)):
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
    if Path(filename).suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp") or len(content) > MAX_BYTES or not content:
        raise ValueError("Upload a PNG, JPG or WEBP logo under 5 MB.")
    try:
        with Image.open(io.BytesIO(content)) as img:
            if img.format not in ("PNG", "JPEG", "WEBP") or img.width > 4096 or img.height > 4096:
                raise ValueError("Logo must be a PNG, JPG or WEBP under 4096 pixels per side.")
            img.load()
            logo = img.convert("RGBA")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
        raise ValueError("That logo could not be opened as an image.") from e
    folder = creators.root(cid) / "branding"
    folder.mkdir(parents=True, exist_ok=True)
    name = f"logo-{uuid.uuid4().hex}.png"
    target = folder / name
    logo.save(target, "PNG")
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
    if not re.fullmatch(r"logo-[0-9a-f]{32}\.png", name):
        return None
    return creators.root(cid) / "branding" / name
