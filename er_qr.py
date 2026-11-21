#!/usr/bin/env python3
"""
er_qr.py - QR codes in the European Resolve brand.

Everything you normally change (URLs, captions, theme, logo) lives in qr.toml.
Edit that file, then run:

    uv run er_qr.py                      # uses qr.toml in this folder
    uv run er_qr.py --config other.toml  # a different settings file
    uv run er_qr.py --link test https://european-resolve.org "Caption"   # quick one-off

uv installs Python and the libraries listed in pyproject.toml on first run.
Without uv: pip install "qrcode[pil]" Pillow opencv-python-headless, then python er_qr.py

Brand rules applied (colour-system.md):
  * Modules are always Resolve Black (#0A1628), never pure black or amber.
  * Square modules only: no dots, rounded corners or gradients.
  * At most three colours per composition.

Themes
  formal      Paper White ground, black modules, thin amber rule. Briefs, press, slides.
  activation  Amber ground, black modules, black rule. Banners, flyers, social.
  digital     Pure White ground, black modules, thin amber rule. Web and screens.

Logo modes
  auto   badge on formal/digital, mark on activation (as-is if the file has transparency)
  badge  the logo as a dark tile, cropped tight around the symbol
  mark   the symbol alone, recoloured Resolve Black (for light or amber grounds)
  as-is  the image exactly as supplied
"""

from __future__ import annotations

import argparse
import base64
import io
import sys
from dataclasses import dataclass
from pathlib import Path

import qrcode
from qrcode.constants import ERROR_CORRECT_H, ERROR_CORRECT_Q
from PIL import Image, ImageChops, ImageDraw, ImageFont

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None

# --- Brand values (colour-system.md v1.0) ----------------------------------

RESOLVE_BLACK = "#0A1628"
RESOLVE_AMBER = "#D4A012"
PAPER_WHITE = "#F5F2EB"
PURE_WHITE = "#FFFFFF"

THEMES = {
    "formal":     {"bg": PAPER_WHITE,   "fg": RESOLVE_BLACK, "accent": RESOLVE_AMBER, "text": RESOLVE_BLACK},
    "activation": {"bg": RESOLVE_AMBER, "fg": RESOLVE_BLACK, "accent": RESOLVE_BLACK, "text": RESOLVE_BLACK},
    "digital":    {"bg": PURE_WHITE,    "fg": RESOLVE_BLACK, "accent": RESOLVE_AMBER, "text": RESOLVE_BLACK},
}
LOGO_MODES = ("auto", "badge", "mark", "as-is")

DEFAULTS = {
    "theme": "formal",
    "formats": ["png", "svg"],
    "logo": "",
    "logo_mode": "auto",
    "logo_size": 0.22,
    "size": 1500,
    "font": "",
    "font_regular": "",
    "out": "qr_out",
    "caption": "",
    "subcaption": "",
    "captions": "on",
}


def normalise_captions(v) -> str:
    """captions setting: on | off | both (true/false also accepted)."""
    if isinstance(v, bool):
        return "on" if v else "off"
    v = str(v).strip().lower()
    if v not in ("on", "off", "both"):
        sys.exit(f'captions must be "on", "off" or "both", not {v!r}')
    return v

# Inter is the free option named in the visual identity proposal.
BOLD_FONTS = [
    "Inter-Bold.ttf", "Inter-Bold.otf", "Inter_24pt-Bold.ttf", "Inter_18pt-Bold.ttf",
    "/Library/Fonts/Inter-Bold.otf", "/Library/Fonts/Inter-Bold.ttf",
    "~/Library/Fonts/Inter-Bold.otf", "~/Library/Fonts/Inter-Bold.ttf",
    "/usr/share/fonts/truetype/inter/Inter-Bold.ttf", "/usr/share/fonts/opentype/inter/Inter-Bold.otf",
    "C:/Windows/Fonts/Inter-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVuSans-Bold.ttf",
]
REGULAR_FONTS = [
    "Inter-Regular.ttf", "Inter-Regular.otf", "Inter_24pt-Regular.ttf", "Inter_18pt-Regular.ttf",
    "/Library/Fonts/Inter-Regular.otf", "/Library/Fonts/Inter-Regular.ttf",
    "~/Library/Fonts/Inter-Regular.otf", "~/Library/Fonts/Inter-Regular.ttf",
    "/usr/share/fonts/truetype/inter/Inter-Regular.ttf", "/usr/share/fonts/opentype/inter/Inter-Regular.otf",
    "C:/Windows/Fonts/Inter-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf", "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "DejaVuSans.ttf",
]
SVG_FONT_STACK = "Inter, 'Söhne', 'GT America', 'Helvetica Neue', Helvetica, Arial, sans-serif"

QUIET_ZONE = 4  # modules; the QR spec minimum


# --- Layout (module units, shared by PNG and SVG) --------------------------

@dataclass
class Layout:
    n: int
    knock_start: int = 0
    knock_size: int = 0
    rule_y: float = 0.0
    rule_h: float = 0.35
    text_size: float = 2.4
    sub_size: float = 1.6
    baseline: float = 0.0
    sub_baseline: float = 0.0
    height: float = 0.0

    @classmethod
    def build(cls, n: int, caption: str, subcaption: str) -> "Layout":
        lay = cls(n=n)
        last = float(n)
        if caption or subcaption:
            lay.rule_y = n  # below the full quiet zone, never inside it
            last = n + lay.rule_h + 0.4
        if caption:
            lay.baseline = last + 0.8 + lay.text_size * 0.75
            last = lay.baseline
        if subcaption:
            lay.sub_baseline = last + (0.9 if caption else 0.8) + lay.sub_size * 0.75
            last = lay.sub_baseline
        lay.height = last + 2.6 if (caption or subcaption) else n
        return lay


# --- Logo preparation -------------------------------------------------------

def _symbol_alpha(img: Image.Image) -> tuple[Image.Image, tuple[int, int, int]]:
    """Separate the symbol from a flat background: returns an alpha mask and the bg colour."""
    rgb = img.convert("RGB")
    bg = rgb.getpixel((0, 0))
    diff = ImageChops.difference(rgb, Image.new("RGB", rgb.size, bg)).convert("L")
    peak = max(1, diff.getextrema()[1])
    alpha = diff.point(lambda v: min(255, round(v * 255 / (peak * 0.85))))
    return alpha, bg


def prepare_logo(path: Path, mode: str, theme_name: str) -> Image.Image:
    src = Image.open(path)
    rgba = src.convert("RGBA")
    transparent = rgba.getextrema()[3][0] < 255

    if mode == "auto":
        mode = "as-is" if transparent else ("mark" if theme_name == "activation" else "badge")
    if mode == "as-is":
        return rgba

    alpha, bg = _symbol_alpha(src) if not transparent else (rgba.getchannel("A"), (0, 0, 0))
    box = alpha.point(lambda v: 255 if v > 40 else 0).getbbox()
    if box is None:
        sys.exit(f"Could not find a symbol in {path}; try logo_mode = \"as-is\"")

    if mode == "mark":
        out = Image.new("RGBA", src.size, RESOLVE_BLACK)
        out.putalpha(alpha)
        return out.crop(box)

    # badge: square tile in the logo's own background colour, symbol at ~62 % of the tile
    if transparent:
        sys.exit("logo_mode = \"badge\" needs a logo on a solid background; use \"as-is\" or \"mark\"")
    w, h = box[2] - box[0], box[3] - box[1]
    side = round(max(w, h) / 0.62)
    tile = Image.new("RGBA", (side, side), bg + (255,))
    sym = src.convert("RGBA").crop(box)
    tile.paste(sym, ((side - w) // 2, (side - h) // 2))
    return tile


# --- QR matrix --------------------------------------------------------------

def build_matrix(url: str, with_logo: bool) -> list[list[bool]]:
    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_H if with_logo else ERROR_CORRECT_Q,
        border=QUIET_ZONE,
    )
    qr.add_data(url)
    qr.make(fit=True)
    return [list(row) for row in qr.get_matrix()]


def knock_out_centre(matrix: list[list[bool]], fraction: float) -> tuple[int, int]:
    """Clear whole modules in the centre for the logo (no half-cut modules)."""
    n = len(matrix)
    k = max(3, round((n - 2 * QUIET_ZONE) * fraction))
    if (n - k) % 2:
        k += 1
    start = (n - k) // 2
    for y in range(start, start + k):
        for x in range(start, start + k):
            matrix[y][x] = False
    return start, k


# --- Rendering --------------------------------------------------------------

def load_font(size_px: int, preferred: str, bold: bool) -> ImageFont.ImageFont:
    for c in ([preferred] if preferred else []) + (BOLD_FONTS if bold else REGULAR_FONTS):
        try:
            return ImageFont.truetype(str(Path(c).expanduser()), size_px)
        except OSError:
            continue
    print("  ! No suitable font found; using Pillow's default. Set font = \"path/to/Inter-Bold.ttf\"", file=sys.stderr)
    try:
        return ImageFont.load_default(size=size_px)
    except TypeError:
        return ImageFont.load_default()


def render_png(matrix, lay: Layout, theme, scale: int, logo, caption, subcaption, fonts, path: Path) -> None:
    n = lay.n
    img = Image.new("RGB", (n * scale, round(lay.height * scale)), theme["bg"])
    d = ImageDraw.Draw(img)

    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if dark:
                d.rectangle([x * scale, y * scale, (x + 1) * scale - 1, (y + 1) * scale - 1], fill=theme["fg"])

    if logo is not None and lay.knock_size:
        area = lay.knock_size * scale
        x0 = lay.knock_start * scale
        pad = max(scale // 2, int(area * 0.08))
        lg = logo.copy()
        lg.thumbnail((area - 2 * pad, area - 2 * pad), Image.Resampling.LANCZOS)
        img.paste(lg, (x0 + (area - lg.width) // 2, x0 + (area - lg.height) // 2), lg)

    left, right = QUIET_ZONE * scale, (n - QUIET_ZONE) * scale
    if caption or subcaption:
        top = round(lay.rule_y * scale)
        d.rectangle([left, top, right - 1, top + max(2, round(lay.rule_h * scale)) - 1], fill=theme["accent"])
    if caption:
        f = load_font(round(lay.text_size * scale), fonts[0], bold=True)
        d.text((left, round(lay.baseline * scale)), caption, font=f, fill=theme["text"], anchor="ls")
    if subcaption:
        f = load_font(round(lay.sub_size * scale), fonts[1], bold=False)
        d.text((left, round(lay.sub_baseline * scale)), subcaption, font=f, fill=theme["text"], anchor="ls")

    img.save(path, dpi=(300, 300))


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_svg(matrix, lay: Layout, theme, logo, caption, subcaption, path: Path) -> None:
    n, h = lay.n, round(lay.height, 2)
    runs = []
    for y, row in enumerate(matrix):
        x = 0
        while x < n:
            if row[x]:
                x0 = x
                while x < n and row[x]:
                    x += 1
                runs.append(f"M{x0} {y}h{x - x0}v1h-{x - x0}z")
            else:
                x += 1

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {h}" width="{n * 10}" height="{round(h * 10)}">',
        f'<rect width="{n}" height="{h}" fill="{theme["bg"]}"/>',
        f'<path fill="{theme["fg"]}" shape-rendering="crispEdges" d="{"".join(runs)}"/>',
    ]
    if logo is not None and lay.knock_size:
        s, k = lay.knock_start, lay.knock_size
        pad = max(0.5, k * 0.08)
        buf = io.BytesIO()
        logo.save(buf, format="PNG")
        out.append(
            f'<image x="{s + pad}" y="{s + pad}" width="{k - 2 * pad}" height="{k - 2 * pad}" '
            f'preserveAspectRatio="xMidYMid meet" href="data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"/>'
        )
    if caption or subcaption:
        out.append(f'<rect x="{QUIET_ZONE}" y="{lay.rule_y}" width="{n - 2 * QUIET_ZONE}" height="{lay.rule_h}" fill="{theme["accent"]}"/>')
    if caption:
        out.append(f'<text x="{QUIET_ZONE}" y="{round(lay.baseline, 2)}" font-family="{SVG_FONT_STACK}" '
                   f'font-weight="700" font-size="{lay.text_size}" fill="{theme["text"]}">{_esc(caption)}</text>')
    if subcaption:
        out.append(f'<text x="{QUIET_ZONE}" y="{round(lay.sub_baseline, 2)}" font-family="{SVG_FONT_STACK}" '
                   f'font-weight="400" font-size="{lay.sub_size}" fill="{theme["text"]}">{_esc(subcaption)}</text>')
    out.append("</svg>")
    path.write_text("\n".join(out), encoding="utf-8")


# --- Scan check -------------------------------------------------------------

def verify(path: Path, expected: str) -> str:
    try:
        import cv2
        import numpy as np
    except ImportError:
        return "not checked (pip install opencv-python-headless to enable)"
    img = cv2.cvtColor(np.array(Image.open(path).convert("RGB")), cv2.COLOR_RGB2BGR)
    small = cv2.resize(img, (360, round(360 * img.shape[0] / img.shape[1])), interpolation=cv2.INTER_AREA)
    for target in (img, small):
        data, _, _ = cv2.QRCodeDetector().detectAndDecode(target)
        if data != expected:
            return "FAILED - does not decode reliably; lower logo_size or drop the logo"
    return "scans OK"


# --- Settings ---------------------------------------------------------------

def load_settings(args) -> tuple[dict, list[dict]]:
    cfg_path = Path(args.config) if args.config else None
    if cfg_path is None:
        for cand in (Path.cwd() / "qr.toml", Path(__file__).resolve().parent / "qr.toml"):
            if cand.exists():
                cfg_path = cand
                break

    cfg, codes, base = {}, [], Path.cwd()
    if cfg_path:
        if tomllib is None:
            sys.exit("Reading qr.toml needs Python 3.11+, or: pip install tomli")
        with open(cfg_path, "rb") as f:
            cfg = tomllib.load(f)
        codes = cfg.pop("codes", [])
        base = cfg_path.resolve().parent
        print(f"Settings: {cfg_path}")

    settings = {**DEFAULTS, **cfg}
    for key in ("theme", "logo", "logo_mode", "logo_size", "size", "font", "out", "caption", "subcaption", "captions"):
        v = getattr(args, key)
        if v is not None:
            settings[key] = v
            if key in ("logo", "font", "out") and v:
                settings[key] = str(Path(v).expanduser().resolve())
    if args.format:
        settings["formats"] = args.format
    # Paths in the settings file are relative to the settings file.
    for key in ("logo", "font", "font_regular", "out"):
        if settings[key] and not Path(settings[key]).expanduser().is_absolute() and cfg_path:
            settings[key] = str(base / settings[key])

    if args.link:
        codes = []
        for item in args.link:
            if len(item) not in (2, 3):
                sys.exit("--link takes NAME URL [CAPTION]")
            codes.append({"name": item[0], "url": item[1], **({"caption": item[2]} if len(item) == 3 else {})})
    if not codes:
        sys.exit("No codes to make. Add [[codes]] entries to qr.toml or use --link NAME URL [CAPTION]")

    for c in codes:
        if not c.get("name") or not c.get("url"):
            sys.exit(f"Each code needs a name and a url: {c}")
    return settings, codes


def main() -> None:
    p = argparse.ArgumentParser(description="European Resolve branded QR codes (settings in qr.toml)")
    p.add_argument("--config", help="Settings file (default: qr.toml next to the script or in the current folder)")
    p.add_argument("--link", nargs="+", action="append", metavar="ARG", help="NAME URL [CAPTION]; repeatable, replaces the codes in the settings file")
    p.add_argument("--theme", choices=[*THEMES, "all"])
    p.add_argument("--format", nargs="+", choices=["png", "svg"])
    p.add_argument("--logo", help='Logo image; pass "" for no logo')
    p.add_argument("--logo-mode", dest="logo_mode", choices=LOGO_MODES)
    p.add_argument("--logo-size", dest="logo_size", type=float)
    p.add_argument("--caption")
    p.add_argument("--subcaption")
    p.add_argument("--captions", choices=["on", "off", "both"], help="Text under the codes: on, off, or both versions")
    p.add_argument("--no-captions", dest="captions", action="store_const", const="off", help="Same as --captions off")
    p.add_argument("--size", type=int)
    p.add_argument("--font")
    p.add_argument("--out")
    args = p.parse_args()

    s, codes = load_settings(args)
    normalise_captions(s["captions"])  # fail early on a typo
    if not 0 < float(s["logo_size"]) <= 0.30:
        sys.exit("logo_size must be between 0 and 0.30")
    if s["logo_mode"] not in LOGO_MODES:
        sys.exit(f"logo_mode must be one of {LOGO_MODES}")
    logo_path = Path(s["logo"]).expanduser() if s["logo"] else None
    if logo_path and not logo_path.exists():
        sys.exit(f"Logo not found: {logo_path}")

    out_dir = Path(s["out"]).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    logo_cache: dict[tuple[str, str], Image.Image] = {}

    for code in codes:
        url = code["url"].strip()
        caption = str(code.get("caption", s["caption"]) or "")
        subcaption = str(code.get("subcaption", s["subcaption"]) or "")
        theme_opt = code.get("theme", s["theme"])
        mode = code.get("logo_mode", s["logo_mode"])
        themes = list(THEMES) if theme_opt == "all" else [theme_opt]
        for t in themes:
            if t not in THEMES:
                sys.exit(f"Unknown theme {t!r} in code {code['name']!r}")

        captions = normalise_captions(code.get("captions", s["captions"]))
        has_text = bool(caption or subcaption)
        # (caption, subcaption, file-name suffix) for each version to make
        variants = []
        if captions in ("on", "both"):
            variants.append((caption, subcaption, ""))
        if captions == "off" or (captions == "both" and has_text):
            variants.append(("", "", "_plain" if captions == "both" else ""))

        matrix = build_matrix(url, with_logo=logo_path is not None)
        knock = knock_out_centre(matrix, float(s["logo_size"])) if logo_path else (0, 0)

        for cap, sub, suffix in variants:
            lay = Layout.build(len(matrix), cap, sub)
            lay.knock_start, lay.knock_size = knock
            scale = max(4, int(s["size"]) // lay.n)

            for t in themes:
                logo = None
                if logo_path:
                    key = (mode, t)
                    if key not in logo_cache:
                        logo_cache[key] = prepare_logo(logo_path, mode, t)
                    logo = logo_cache[key]
                stem = f"{code['name']}_qr_{t}{suffix}"
                print(f"{stem}  ->  {url}")
                if "png" in s["formats"]:
                    png = out_dir / f"{stem}.png"
                    render_png(matrix, lay, THEMES[t], scale, logo, cap, sub, (s["font"], s["font_regular"]), png)
                    print(f"  {png}  ({verify(png, url)})")
                if "svg" in s["formats"]:
                    svg = out_dir / f"{stem}.svg"
                    render_svg(matrix, lay, THEMES[t], logo, cap, sub, svg)
                    print(f"  {svg}")


if __name__ == "__main__":
    main()
