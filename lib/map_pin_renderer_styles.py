# lib/map_pin_renderer_styles.py
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
from typing import Optional, Tuple, Union, Dict

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

module_logger = logging.getLogger("icad_dispatch.map_pin_renderer")

DEFAULT_TILE_TEMPLATE = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
DEFAULT_ATTRIBUTION = "© OpenStreetMap contributors"

TILE_SIZE = 256
WEB_MERCATOR_MAX_LAT = 85.05112878


# =============================================================================
# NEW: Tile provider presets (no API key)
# =============================================================================

@dataclass(frozen=True)
class TileProvider:
    key: str
    tile_template: str
    attribution: str
    subdomains: Tuple[str, ...] = ()
    notes: str = ""


# These URLs/attributions are aligned to the OSM Wiki provider table.
# Some entries are marked “commercial/registration required” there; they still
# don’t require an API key in the URL, but you should review their terms. :contentReference[oaicite:3]{index=3}
TILE_PROVIDERS: Dict[str, TileProvider] = {
    # OpenStreetMap Standard :contentReference[oaicite:4]{index=4}
    "osm_standard": TileProvider(
        key="osm_standard",
        tile_template="https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        attribution="© OpenStreetMap contributors",
        notes="OSM Standard tiles (respect usage policy).",
    ),

    # OpenStreetMap DE :contentReference[oaicite:5]{index=5}
    "osm_de": TileProvider(
        key="osm_de",
        tile_template="https://tile.openstreetmap.de/{z}/{x}/{y}.png",
        attribution="© OpenStreetMap contributors",
        notes="German-labeled variant; high-traffic/commercial use restricted (see provider policy).",
    ),

    # OpenStreetMap France “osmfr” :contentReference[oaicite:6]{index=6}
    "osm_fr": TileProvider(
        key="osm_fr",
        tile_template="https://{s}.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png",
        attribution="© OpenStreetMap contributors",
        subdomains=("a", "b", "c"),
        notes="French variant; uses {s} subdomains.",
    ),

    # HOT style :contentReference[oaicite:7]{index=7}
    "osm_hot": TileProvider(
        key="osm_hot",
        tile_template="https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png",
        attribution="© OpenStreetMap contributors",
        subdomains=("a", "b", "c"),
        notes="Humanitarian style; uses {s} subdomains.",
    ),

    # CyclOSM :contentReference[oaicite:8]{index=8}
    "cyclosm": TileProvider(
        key="cyclosm",
        tile_template="https://{s}.tile.openstreetmap.fr/cyclosm/{z}/{x}/{y}.png",
        attribution="© OpenStreetMap contributors",
        subdomains=("a", "b", "c"),
        notes="CyclOSM style; uses {s} subdomains.",
    ),

    # ÖPNVKarte :contentReference[oaicite:9]{index=9}
    "opnvkarte": TileProvider(
        key="opnvkarte",
        tile_template="https://tile.memomaps.de/tilegen/{z}/{x}/{y}.png",
        attribution="© OpenStreetMap contributors",
        notes="Transit-focused map; donation-funded.",
    ),

    # OpenTopoMap (OSM Wiki flags as discontinuing) :contentReference[oaicite:10]{index=10}
    "opentopomap": TileProvider(
        key="opentopomap",
        tile_template="https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        attribution="© OpenStreetMap contributors",
        subdomains=("a", "b", "c"),
        notes="May be discontinued; keep as an optional variant.",
    ),

    # CARTO (OSM Wiki flags “commercial, registration required”, but URL itself has no key) :contentReference[oaicite:11]{index=11}
    "carto_positron": TileProvider(
        key="carto_positron",
        tile_template="https://cartodb-basemaps-{s}.global.ssl.fastly.net/light_all/{z}/{x}/{y}.png",
        attribution='Map tiles by Carto, under CC BY 3.0. Data by OpenStreetMap, under ODbL.',
        subdomains=("a", "b", "c", "d"),
        notes="Modern light basemap (Positron). Review terms if production/high-volume.",
    ),
    "carto_darkmatter": TileProvider(
        key="carto_darkmatter",
        tile_template="https://cartodb-basemaps-{s}.global.ssl.fastly.net/dark_all/{z}/{x}/{y}.png",
        attribution='Map tiles by Carto, under CC BY 3.0. Data by OpenStreetMap, under ODbL.',
        subdomains=("a", "b", "c", "d"),
        notes="Modern dark basemap (Dark Matter). Review terms if production/high-volume.",
    ),

    # Stadia Maps “Stamen Toner” (hosted by Stadia; URL + attribution documented) :contentReference[oaicite:12]{index=12}
    "stamen_toner": TileProvider(
        key="stamen_toner",
        tile_template="https://tiles.stadiamaps.com/tiles/stamen_toner/{z}/{x}/{y}.png",
        attribution="© Stadia Maps © Stamen Design © OpenMapTiles © OpenStreetMap contributors",
        notes="High-contrast B/W. Docs show URL + attribution; service terms may apply.",
    ),
}


def available_tile_providers() -> Dict[str, TileProvider]:
    return dict(TILE_PROVIDERS)


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


def _sha1_short(s: str, n: int = 10) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:n]


def _hex_to_rgba(hex_str: str, alpha: int = 255) -> Tuple[int, int, int, int]:
    s = (hex_str or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join([c * 2 for c in s])
    if len(s) != 6:
        raise ValueError(f"Invalid hex color: {hex_str!r}")
    r = int(s[0:2], 16)
    g = int(s[2:4], 16)
    b = int(s[4:6], 16)
    return (r, g, b, int(_clamp(alpha, 0, 255)))


@dataclass
class MapPinConfig:
    # Basemap (NEW)
    tile_provider: str = "osm_standard"  # must exist in TILE_PROVIDERS, or treated as "custom"
    tile_template: str = DEFAULT_TILE_TEMPLATE
    attribution: str = DEFAULT_ATTRIBUTION
    tile_subdomains: Tuple[str, ...] = ()  # override provider subdomains if desired

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

    # Pin variant (NEW)
    pin_variant: str = "classic"  # classic | dot | glow | badge
    pin_color_hex: str = "#dc2d2d"  # modern red
    pin_outline_alpha: int = 220

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

    title_bbox = draw.textbbox((0, 0), title or "", font=font_title)
    title_h = title_bbox[3] - title_bbox[1]

    sub_h = 0
    if subtitle:
        sub_bbox = draw.textbbox((0, 0), subtitle, font=font_sub)
        sub_h = (sub_bbox[3] - sub_bbox[1]) + 6

    bar_h = pad_y + title_h + sub_h + pad_y
    bar_h = max(70, min(bar_h, 120))

    bar = Image.new("RGBA", (out.size[0], bar_h), (8, 12, 18, 190))
    out.alpha_composite(bar, (0, 0))

    y = pad_y
    _draw_text_with_shadow(draw, (pad_x, y), title, font=font_title, fill=(255, 255, 255, 255))
    y += title_h + 6

    if subtitle:
        _draw_text_with_shadow(draw, (pad_x, y), subtitle, font=font_sub, fill=(210, 220, 235, 255))

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

        # NEW: resolve provider -> final tile_template/attribution/subdomains
        self._resolve_tiles()

    def _resolve_tiles(self) -> None:
        cfg = self.config
        prov = TILE_PROVIDERS.get((cfg.tile_provider or "").strip(), None)

        # If user supplied a non-default tile_template, treat it as an override.
        template_override = (cfg.tile_template or "").strip()
        use_override = template_override and template_override != DEFAULT_TILE_TEMPLATE

        if prov and not use_override:
            self.tile_template = prov.tile_template
            self.attribution = prov.attribution
            self.subdomains = cfg.tile_subdomains or prov.subdomains
            self.tile_provider_key = prov.key
        else:
            # Custom template (or provider not found)
            self.tile_template = template_override or DEFAULT_TILE_TEMPLATE
            self.attribution = (cfg.attribution or "").strip() or DEFAULT_ATTRIBUTION
            self.subdomains = cfg.tile_subdomains
            self.tile_provider_key = (cfg.tile_provider or "custom").strip() or "custom"

        # Cache key includes a hash of the final template so switching providers
        # won’t accidentally reuse old tiles.
        self.tile_cache_key = f"{self.tile_provider_key}_{_sha1_short(self.tile_template, 10)}"

    def _tile_path(self, z: int, x: int, y: int) -> Path:
        # NEW: include provider/template hash in cache path
        return self.config.cache_dir / "tiles" / self.tile_cache_key / str(z) / str(x) / f"{y}.png"

    def _tile_url(self, z: int, x: int, y: int) -> str:
        # Deterministic subdomain selection if {s} is used
        s = ""
        if self.subdomains:
            s = self.subdomains[(x + y) % len(self.subdomains)]
        try:
            return self.tile_template.format(z=z, x=x, y=y, s=s)
        except Exception:
            # Fallback if template has unexpected placeholders
            return self.tile_template.format(z=z, x=x, y=y)

    def _fetch_tile_cached(self, z: int, x: int, y: int) -> Image.Image:
        tpath = self._tile_path(z, x, y)
        _ensure_dir(tpath.parent)

        if _is_cache_fresh(tpath, self.config.tile_ttl_s):
            return Image.open(tpath).convert("RGBA")

        url = self._tile_url(z, x, y)
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

    # =============================================================================
    # NEW: Pin variants
    # =============================================================================

    @staticmethod
    def _draw_pin(img: Image.Image, x: int, y: int, *, variant: str, color_rgba: Tuple[int, int, int, int], outline_alpha: int) -> Image.Image:
        out = img.convert("RGBA")

        variant = (variant or "classic").strip().lower()
        outline = (255, 255, 255, int(_clamp(outline_alpha, 0, 255)))

        # Common soft shadow layer
        shadow = Image.new("RGBA", out.size, (0, 0, 0, 0))
        sdraw = ImageDraw.Draw(shadow)
        sdraw.ellipse((x - 16, y + 12, x + 16, y + 24), fill=(0, 0, 0, 85))
        shadow = shadow.filter(ImageFilter.GaussianBlur(3))
        out = Image.alpha_composite(out, shadow)

        draw = ImageDraw.Draw(out)

        if variant == "dot":
            # Modern “location dot”: ring + filled dot
            draw.ellipse((x - 12, y - 12, x + 12, y + 12), fill=(0, 0, 0, 140))
            draw.ellipse((x - 11, y - 11, x + 11, y + 11), outline=outline, width=3)
            draw.ellipse((x - 8, y - 8, x + 8, y + 8), fill=color_rgba)
            draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(255, 255, 255, 240))
            return out

        if variant == "glow":
            # “Glowy” pin: glow halo + classic pin
            glow = Image.new("RGBA", out.size, (0, 0, 0, 0))
            gdraw = ImageDraw.Draw(glow)
            gdraw.ellipse((x - 32, y - 32, x + 32, y + 32), fill=(color_rgba[0], color_rgba[1], color_rgba[2], 60))
            glow = glow.filter(ImageFilter.GaussianBlur(10))
            out = Image.alpha_composite(out, glow)
            draw = ImageDraw.Draw(out)
            # fall through into classic-ish pin drawing below
            variant = "classic"

        if variant == "badge":
            # Rounded “badge” marker with small pointer (very app-like)
            w, h = 46, 34
            rx = 12
            bx0, by0 = x - w // 2, y - h - 12
            bx1, by1 = bx0 + w, by0 + h

            draw.rounded_rectangle((bx0, by0, bx1, by1), radius=rx, fill=color_rgba, outline=outline, width=2)
            # highlight strip
            draw.rounded_rectangle((bx0 + 2, by0 + 2, bx1 - 2, by0 + 12), radius=rx, fill=(255, 255, 255, 50))

            # pointer
            tip = (x, y + 6)
            tri = [(x, y + 6), (x - 10, y - 8), (x + 10, y - 8)]
            draw.polygon(tri, fill=color_rgba)
            draw.line([tri[1], tip, tri[2]], fill=outline, width=2)

            # center dot
            draw.ellipse((x - 4, by0 + h // 2 - 4, x + 4, by0 + h // 2 + 4), fill=(255, 255, 255, 240))
            return out

        # Default: classic pin (slightly more “modern” with a highlight)
        r = 12
        head_box = (x - r, y - r, x + r, y + r)
        draw.ellipse(head_box, fill=color_rgba, outline=outline, width=2)

        # subtle highlight
        draw.ellipse((x - r + 2, y - r + 2, x + r - 6, y + r - 6), fill=(255, 255, 255, 35))

        tail = [(x, y + 26), (x - 8, y + 6), (x + 8, y + 6)]
        draw.polygon(tail, fill=color_rgba)
        draw.line([tail[1], tail[0], tail[2]], fill=outline, width=2)

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
        cfg = self.config
        out_w = int(width or cfg.width)
        out_h = int(height or cfg.height)
        z = int(zoom if zoom is not None else cfg.zoom)

        left, top, right, bottom = viewport_for_point(lat, lon, z, out_w, out_h)
        base = self._stitch_and_crop(zoom=z, left=left, top=top, right=right, bottom=bottom, out_w=out_w, out_h=out_h)

        pin_color = _hex_to_rgba(cfg.pin_color_hex, 255)
        img = self._draw_pin(
            base,
            out_w // 2,
            out_h // 2,
            variant=cfg.pin_variant,
            color_rgba=pin_color,
            outline_alpha=cfg.pin_outline_alpha,
            )

        show_header = cfg.show_title_block if show_title_block is None else bool(show_title_block)
        if show_header:
            t = (title or "Incident Location").strip()
            sub = (subtitle or "").strip() or None
            img = _add_title_block(
                img,
                title=t,
                subtitle=sub,
                attribution=self.attribution,  # NEW: resolved attribution
                font_candidates=cfg.font_candidates,
            )

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=optimize)
        png = buf.getvalue()

        if save or out_path is not None:
            if out_path is None:
                raw = f"{lat:.6f},{lon:.6f}|z={z}|{out_w}x{out_h}|{self.tile_cache_key}|{cfg.pin_variant}"
                short = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
                out_path = cfg.cache_dir / "renders" / f"pin_{short}.png"

            out_path = Path(out_path)
            _ensure_dir(out_path.parent)
            out_path.write_bytes(png)

        return png
