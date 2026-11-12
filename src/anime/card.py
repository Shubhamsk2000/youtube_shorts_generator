"""The tweet-card template: a full-canvas transparent PNG laid over the blurred background.

The card has a rounded transparent window where the episode plays; the video is overlaid UNDER the
card, so the window gives it rounded corners for free. Everything is drawn at 2x and downscaled for
smooth edges. `layout()` is the single source of truth for where things go (render.py uses it too).
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from common.paths import resolve

from .config import CardCfg

BASE_W = 1080
THEMES = {
    "dark": {"bg": (0, 0, 0, 255), "text": (231, 233, 234), "muted": (113, 118, 123), "border": (47, 51, 54)},
    "light": {"bg": (255, 255, 255, 255), "text": (15, 20, 25), "muted": (83, 100, 113), "border": (207, 217, 222)},
}
BADGE = (29, 155, 240)


@dataclass(frozen=True)
class Layout:
    W: int
    H: int
    k: float                 # scale vs the 1080-wide design
    card: tuple[int, int, int, int]   # x, y, w, h
    video: tuple[int, int, int, int]  # x, y, w, h (w, h even)
    lines: tuple[str, ...]   # wrapped tweet text
    caption_y: int           # top of the dialogue captions


@lru_cache(maxsize=None)
def font_file(family: str, style: str) -> str:
    out = subprocess.run(["fc-match", "-f", "%{file}", f"{family}:style={style}"], capture_output=True, text=True)
    return out.stdout.strip() or "DejaVuSans.ttf"


def _font(cc: CardCfg, style: str, px: float) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(font_file(cc.font, style), max(1, round(px)))


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def wrap(text: str, font: ImageFont.FreeTypeFont, width: float, max_lines: int) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split():
            trial = f"{cur} {word}".strip()
            if font.getlength(trial) <= width or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    lines = [ln for ln in lines if ln] or [""]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines[-1] and font.getlength(lines[-1] + "…") > width:
            lines[-1] = lines[-1].rsplit(" ", 1)[0] if " " in lines[-1] else lines[-1][:-1]
        lines[-1] += "…"
    return lines


def layout(cc: CardCfg, W: int, H: int, src_w: int, src_h: int, tweet: str) -> Layout:
    k = W / BASE_W
    card_w = round(1000 * k)
    pad = 36 * k
    avatar = 92 * k
    vw = _even(card_w - 2 * pad)
    vh = _even(min(vw * src_h / src_w, vw))  # very tall sources are letterboxed into a square
    lines = wrap(tweet, _font(cc, "Medium", cc.text_size * k), vw, cc.max_lines) if tweet.strip() else []
    line_h = cc.text_size * k * 1.3
    text_h = len(lines) * line_h
    footer_h = (20 + 34) * k if cc.footer != "none" else 0
    card_h = round(pad + avatar + (22 * k + text_h if lines else 0) + 26 * k + vh + footer_h + pad)
    x = (W - card_w) // 2
    y = round(min(max(cc.center_y * H - card_h / 2, 0.09 * H), H - card_h - 0.2 * H))
    vx = x + round(pad)
    vy = y + round(pad + avatar + (22 * k + text_h if lines else 0) + 26 * k)
    return Layout(W=W, H=H, k=k, card=(x, y, card_w, card_h), video=(vx, vy, vw, vh), lines=tuple(lines),
                  caption_y=y + card_h)


def render_card(cc: CardCfg, lay: Layout, dst: Path, when: datetime | None = None) -> Path:
    s = 2  # supersampling
    th = THEMES[cc.theme]
    k = lay.k * s
    img = Image.new("RGBA", (lay.W * s, lay.H * s), (0, 0, 0, 0))
    x, y, w, h = (v * s for v in lay.card)
    radius = 34 * k

    # soft shadow under the card
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((x, y + 14 * k, x + w, y + h + 14 * k), radius, fill=(0, 0, 0, 150))
    img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(28 * k)))

    d = ImageDraw.Draw(img)
    d.rounded_rectangle((x, y, x + w, y + h), radius, fill=th["bg"], outline=th["border"], width=max(1, round(2 * k)))

    pad = 36 * k
    av = 92 * k
    ax, ay = x + pad, y + pad
    img.alpha_composite(_avatar(cc, round(av)), (round(ax), round(ay)))

    name_f = _font(cc, "Bold", 38 * k)
    handle_f = _font(cc, "Regular", 32 * k)
    nx = ax + av + 22 * k
    d.text((nx, ay + 8 * k), cc.name, font=name_f, fill=th["text"])
    if cc.verified:
        bx = nx + name_f.getlength(cc.name) + 12 * k
        _badge(d, bx, ay + 12 * k, 34 * k)
    d.text((nx, ay + 54 * k), cc.handle, font=handle_f, fill=th["muted"])

    if lay.lines:
        tf = _font(cc, "Medium", cc.text_size * k)
        ty = ay + av + 22 * k
        for ln in lay.lines:
            d.text((x + pad, ty), ln, font=tf, fill=th["text"])
            ty += cc.text_size * k * 1.3

    vx, vy, vw, vh = (v * s for v in lay.video)
    if cc.footer == "date":
        when = when or datetime.now()
        stamp = f"{when.strftime('%I:%M %p').lstrip('0')} · {when.strftime('%b')} {when.day}, {when.year}"
        d.text((x + pad, vy + vh + 20 * k), stamp, font=_font(cc, "Regular", 30 * k), fill=th["muted"])

    # the video window: fully transparent, rounded
    hole = Image.new("L", img.size, 0)
    ImageDraw.Draw(hole).rounded_rectangle((vx, vy, vx + vw - 1, vy + vh - 1), 22 * k, fill=255)
    alpha = img.getchannel("A")
    alpha.paste(0, mask=hole)
    img.putalpha(alpha)

    out = img.resize((lay.W, lay.H), Image.LANCZOS)
    dst.parent.mkdir(parents=True, exist_ok=True)
    out.save(dst)
    return dst


def _avatar(cc: CardCfg, size: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    if cc.avatar and resolve(cc.avatar).exists():
        im = Image.open(resolve(cc.avatar)).convert("RGBA")
        side = min(im.size)
        im = im.crop(((im.width - side) // 2, (im.height - side) // 2,
                      (im.width + side) // 2, (im.height + side) // 2)).resize((size, size), Image.LANCZOS)
    else:  # coloured circle with the channel's initial
        im = Image.new("RGBA", (size, size))
        top, bot = (255, 94, 98), (124, 58, 237)
        for row in range(size):
            f = row / max(1, size - 1)
            ImageDraw.Draw(im).line([(0, row), (size, row)], fill=tuple(round(a + (b - a) * f) for a, b in zip(top, bot)))
        letter = (cc.name.strip()[:1] or "A").upper()
        f = _font(cc, "Black", size * 0.5)
        ImageDraw.Draw(im).text((size / 2, size / 2), letter, font=f, fill=(255, 255, 255), anchor="mm")
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    return out


def _badge(d: ImageDraw.ImageDraw, x: float, y: float, size: float) -> None:
    d.ellipse((x, y, x + size, y + size), fill=BADGE)
    pts = [(x + size * 0.28, y + size * 0.52), (x + size * 0.44, y + size * 0.67), (x + size * 0.73, y + size * 0.36)]
    d.line(pts, fill=(255, 255, 255), width=max(1, round(size * 0.12)), joint="curve")
