# lib/map_pin_renderer.py
from __future__ import annotations

import hashlib
import io
import logging
import math
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple, Union

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

module_logger = logging.getLogger("icad_dispatch.map_pin_renderer")

DEFAULT_TILE_TEMPLATE = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
DEFAULT_ATTRIBUTION = "© OpenStreetMap contributors"

TILE_SIZE = 256
WEB_MERCATOR_MAX_LAT = 85.05112878


# ----------------------------
# Config + tiny helpers
# ----------------------------

def _utc_now_ts() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp())


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _is_cache_fresh(path: Path, ttl_s: int) -> bool:
    if ttl_s <= 0:
        return False
    if not path.exists():
        return False
    age = _utc_now_ts() - int(path.stat().st_mtime)
    return 0 <= age <= ttl_s


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _io_bytes(b: bytes) -> io.BytesIO:
    return io.BytesIO(b)


@dataclass
class MapPinConfig:
    # Basemap
    tile_template: str = DEFAULT_TILE_TEMPLATE
    attribution: str = DEFAULT_ATTRIBUTION

    # Output
    width: int = 900
    height: int = 650
    zoom: int = 16  # street-ish; 15 = area, 17 = tighter

    # Title block (modern header)
    show_title_block: bool = True
    font_candidates: list[str] = field(default_factory=lambda: [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ])

    # Tile fetching
    request_timeout_s: int = 20
    tile_delay_s: float = 0.0  # set >0 if you want to be gentle to tile servers
    max_tiles: int = 64        # safety guard

    # Caching (tiles only)
    cache_dir: Path = field(default_factory=lambda: Path("../.cache_pin_maps"))
    tile_ttl_s: int = 7 * 24 * 3600  # 7 days

    # Required for OSM politeness (and many providers)
    user_agent: str = "icad_dispatch (maps@icarey.net)"


# ----------------------------
# Web Mercator math (point viewport)
# ----------------------------

def lonlat_to_world_px(lon: float, lat: float, zoom: int) -> Tuple[float, float]:
    lat = _clamp(lat, -WEB_MERCATOR_MAX_LAT, WEB_MERCATOR_MAX_LAT)
    x = (lon + 180.0) / 360.0
    sin_lat = math.sin(math.radians(lat))
    y = 0.5 - (math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi))
    scale = TILE_SIZE * (2 ** zoom)
    return (x * scale, y * scale)


def viewport_for_point(lat: float, lon: float, zoom: int, out_w: int, out_h: int) -> Tuple[float, float, float, float]:
    cx, cy = lonlat_to_world_px(lon, lat, zoom)
    half_w = out_w / 2.0
    half_h = out_h / 2.0
    left = cx - half_w
    top = cy - half_h
    right = cx + half_w
    bottom = cy + half_h
    return left, top, right, bottom


def tile_range_for_viewport(left: float, top: float, right: float, bottom: float, zoom: int) -> Tuple[int, int, int, int]:
    x0 = int(math.floor(left / TILE_SIZE))
    x1 = int(math.floor((right - 1) / TILE_SIZE))
    y0 = int(math.floor(top / TILE_SIZE))
    y1 = int(math.floor((bottom - 1) / TILE_SIZE))

    max_xy = (2 ** zoom) - 1
    y0 = max(0, min(max_xy, y0))
    y1 = max(0, min(max_xy, y1))
    return x0, y0, x1, y1


# ----------------------------
# Title block helpers (NWS-style, modern)
# ----------------------------

def _load_font(font_candidates: list[str], size: int) -> ImageFont.FreeTypeFont:
    for p in font_candidates:
        if p and os.path.exists(p):
            try:
                return ImageFont.truetype(p, size=size)
            except Exception:
                pass
    return ImageFont.load_default()


def _draw_text_with_shadow(
        draw: ImageDraw.ImageDraw,
        xy: tuple[int, int],
        text: str,
        *,
        font: ImageFont.FreeTypeFont,
        fill=(255, 255, 255, 255),
        shadow=(0, 0, 0, 180),
        shadow_offset=(0, 1),
) -> None:
    x, y = xy
    sx, sy = shadow_offset
    draw.text((x + sx, y + sy), text, font=font, fill=shadow)
    draw.text((x, y), text, font=font, fill=fill)


def _add_title_block(
        img: Image.Image,
        *,
        title: str,
        subtitle: str | None = None,
        attribution: str | None = None,
        font_candidates: list[str] | None = None,
) -> Image.Image:
    """
    Modern top bar (title + subtitle) + optional attribution chip (bottom-right).
    """
    out = img.convert("RGBA")
    draw = ImageDraw.Draw(out)

    font_candidates = font_candidates or [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]

    font_title = _load_font(font_candidates, 22)
    font_sub = _load_font(font_candidates, 15)
    font_attr = _load_font(font_candidates, 13)

    pad_x = 14
    pad_y = 10

    # dynamic height
    title_bbox = draw.textbbox((0, 0), title or "", font=font_title)
    title_h = title_bbox[3] - title_bbox[1]

    sub_h = 0
    if subtitle:
        sub_bbox = draw.textbbox((0, 0), subtitle, font=font_sub)
        sub_h = (sub_bbox[3] - sub_bbox[1]) + 6

    bar_h = pad_y + title_h + sub_h + pad_y
    bar_h = max(70, min(bar_h, 120))

    # top translucent bar
    bar = Image.new("RGBA", (out.size[0], bar_h), (8, 12, 18, 190))
    out.alpha_composite(bar, (0, 0))

    # text
    y = pad_y
    _draw_text_with_shadow(draw, (pad_x, y), title, font=font_title, fill=(255, 255, 255, 255))
    y += title_h + 6

    if subtitle:
        _draw_text_with_shadow(draw, (pad_x, y), subtitle, font=font_sub, fill=(210, 220, 235, 255))

    # attribution chip
    if attribution:
        chip_text = attribution.strip()
        if chip_text:
            chip_pad_x = 10
            chip_pad_y = 6

            tw = int(draw.textlength(chip_text, font=font_attr))
            th_bbox = draw.textbbox((0, 0), chip_text, font=font_attr)
            th = (th_bbox[3] - th_bbox[1])

            chip_w = tw + chip_pad_x * 2
            chip_h = th + chip_pad_y * 2

            x1 = out.size[0] - 10
            y1 = out.size[1] - 10
            x0 = x1 - chip_w
            y0 = y1 - chip_h

            draw.rounded_rectangle((x0, y0, x1, y1), radius=10, fill=(0, 0, 0, 140))
            _draw_text_with_shadow(
                draw,
                (x0 + chip_pad_x, y0 + chip_pad_y),
                chip_text,
                font=font_attr,
                fill=(230, 230, 230, 230),
                shadow=(0, 0, 0, 140),
            )

    return out


# ----------------------------
# Renderer
# ----------------------------

class MapPinRenderer:
    """
    Renders a static basemap centered on (lat,lon) + draws a pin marker.
    Tile stitch logic mirrors your NOAA renderer style.
    """

    def __init__(
            self,
            *,
            config: Optional[MapPinConfig] = None,
            session: Optional[requests.Session] = None,
            logger: Optional[logging.Logger] = None,
    ) -> None:
        self.config = config or MapPinConfig()
        self.log = logger or module_logger

        ua = (self.config.user_agent or "").strip()
        if not ua:
            raise ValueError("user_agent is required (e.g. 'icad_dispatch (maps@icarey.net)')")

        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": ua,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        })

        _ensure_dir(self.config.cache_dir)

    def _tile_path(self, z: int, x: int, y: int) -> Path:
        return self.config.cache_dir / "tiles" / str(z) / str(x) / f"{y}.png"

    def _fetch_tile_cached(self, z: int, x: int, y: int) -> Image.Image:
        _ensure_dir(self.config.cache_dir / "tiles" / str(z) / str(x))
        tpath = self._tile_path(z, x, y)

        if _is_cache_fresh(tpath, self.config.tile_ttl_s):
            return Image.open(tpath).convert("RGBA")

        url = self.config.tile_template.format(z=z, x=x, y=y)
        self.log.debug("TILE %s", url)

        if self.config.tile_delay_s > 0:
            time.sleep(self.config.tile_delay_s)

        r = self.session.get(url, timeout=self.config.request_timeout_s)
        r.raise_for_status()
        img = Image.open(_io_bytes(r.content)).convert("RGBA")

        if self.config.tile_ttl_s > 0:
            try:
                img.save(tpath, format="PNG")
            except Exception as e:
                self.log.debug("Could not write tile cache %s: %s", tpath, e)

        return img

    def _stitch_and_crop(
            self,
            *,
            zoom: int,
            left: float,
            top: float,
            right: float,
            bottom: float,
            out_w: int,
            out_h: int,
    ) -> Image.Image:
        x0, y0, x1, y1 = tile_range_for_viewport(left, top, right, bottom, zoom)
        tiles_w = (x1 - x0 + 1)
        tiles_h = (y1 - y0 + 1)
        tile_count = tiles_w * tiles_h

        if tile_count > self.config.max_tiles:
            raise RuntimeError(f"Tile count {tile_count} exceeds max_tiles={self.config.max_tiles} at zoom={zoom}")

        mosaic = Image.new("RGBA", (tiles_w * TILE_SIZE, tiles_h * TILE_SIZE))
        world_tiles = 2 ** zoom

        for ty in range(y0, y1 + 1):
            for tx in range(x0, x1 + 1):
                tx_wrapped = tx % world_tiles  # wrap X
                tile_img = self._fetch_tile_cached(zoom, tx_wrapped, ty)
                px = (tx - x0) * TILE_SIZE
                py = (ty - y0) * TILE_SIZE
                mosaic.paste(tile_img, (px, py))

        crop_left = int(round(left - (x0 * TILE_SIZE)))
        crop_top = int(round(top - (y0 * TILE_SIZE)))
        return mosaic.crop((crop_left, crop_top, crop_left + out_w, crop_top + out_h))

    @staticmethod
    def _draw_pin(img: Image.Image, x: int, y: int) -> Image.Image:
        """
        Draw a simple pin: shadow + teardrop-ish marker + white center.
        (No label — labels should go in the title block.)
        """
        out = img.convert("RGBA")
        draw = ImageDraw.Draw(out)

        # Shadow
        shadow = Image.new("RGBA", out.size, (0, 0, 0, 0))
        sdraw = ImageDraw.Draw(shadow)
        sdraw.ellipse((x - 14, y + 10, x + 14, y + 20), fill=(0, 0, 0, 90))
        shadow = shadow.filter(ImageFilter.GaussianBlur(2))
        out = Image.alpha_composite(out, shadow)
        draw = ImageDraw.Draw(out)

        # Pin head
        r = 12
        head_box = (x - r, y - r, x + r, y + r)
        draw.ellipse(head_box, fill=(220, 45, 45, 255), outline=(255, 255, 255, 220), width=2)

        # Tail
        tail = [(x, y + 26), (x - 8, y + 6), (x + 8, y + 6)]
        draw.polygon(tail, fill=(220, 45, 45, 255))
        draw.line([tail[1], tail[0], tail[2]], fill=(255, 255, 255, 180), width=2)

        # Center dot
        draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(255, 255, 255, 255))

        return out

    def render_png(
            self,
            lat: float,
            lon: float,
            *,
            width: Optional[int] = None,
            height: Optional[int] = None,
            zoom: Optional[int] = None,
            title: Optional[str] = None,
            subtitle: Optional[str] = None,
            show_title_block: Optional[bool] = None,
            save: bool = False,
            out_path: Optional[Union[str, Path]] = None,
            optimize: bool = True,
    ) -> bytes:
        """
        Returns PNG bytes.
        If save=True or out_path provided, also writes PNG to disk.

        Label-on-map is intentionally removed — use title/subtitle in the header instead.
        """
        cfg = self.config
        out_w = int(width or cfg.width)
        out_h = int(height or cfg.height)
        z = int(zoom if zoom is not None else cfg.zoom)

        left, top, right, bottom = viewport_for_point(lat, lon, z, out_w, out_h)
        base = self._stitch_and_crop(zoom=z, left=left, top=top, right=right, bottom=bottom, out_w=out_w, out_h=out_h)

        # pin goes at center because viewport is centered on the point
        img = self._draw_pin(base, out_w // 2, out_h // 2)

        # Modern header
        show_header = cfg.show_title_block if show_title_block is None else bool(show_title_block)
        if show_header:
            t = (title or "Incident Location").strip()
            sub = (subtitle or "").strip() or None
            img = _add_title_block(
                img,
                title=t,
                subtitle=sub,
                attribution=cfg.attribution,
                font_candidates=cfg.font_candidates,
            )

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=optimize)
        png = buf.getvalue()

        if save or out_path is not None:
            if out_path is None:
                # stable-ish name based on coords + zoom + size
                raw = f"{lat:.6f},{lon:.6f}|z={z}|{out_w}x{out_h}"
                short = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
                out_path = cfg.cache_dir / "renders" / f"pin_{short}.png"

            out_path = Path(out_path)
            _ensure_dir(out_path.parent)
            out_path.write_bytes(png)

        return png
