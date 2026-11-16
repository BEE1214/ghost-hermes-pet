#!/usr/bin/env python3
"""Destiny 2 Ghost pet for Hermes desktop (Codex/petdex 8x9 atlas, 192x208 frames).

Row order is the one Hermes reads (agent.pet.constants.CODEX_STATE_ROWS):
  idle, running-right, running-left, waving, jumping, failed, waiting, running, review
Hermes state -> row: error=failed, celebrate=jumping, just finished=waving,
blocked on user=waiting, tool running/busy=running, reasoning=review, else idle.
"""
import json
import math
import os
import shutil
import sys

from PIL import Image, ImageDraw, ImageFilter

FW, FH = 192, 208
COLS, ROWS = 8, 9
S = 4  # supersample

# (row name, frame count)
STATES = [
    ("idle", 6), ("running-right", 8), ("running-left", 8),
    ("waving", 4), ("jumping", 5), ("failed", 8),
    ("waiting", 6), ("running", 8), ("review", 8),
]

# palette
SHELL_HI = (250, 250, 246)
SHELL_LO = (104, 118, 138)
CORE_DARK = (20, 26, 38)
METAL = (52, 60, 74)
GOLD = (236, 120, 40)        # rust-orange caps
GOLD_DEEP = (170, 78, 20)
CYAN = (70, 205, 255)
AMBER = (255, 178, 40)
RED = (255, 48, 48)


def lerp(a, b, t):
    return a + (b - a) * t


def lerp_c(c1, c2, t):
    return tuple(int(round(lerp(c1[i], c2[i], t))) for i in range(3))


def clamp01(x):
    return max(0.0, min(1.0, x))


# ── geometry (local coords, y down, top-right quadrant, mirrored for others) ──
G = 2.4          # half seam gap
R_IN = 23.0      # eye cutout radius


def arc(deg):
    a = math.radians(deg)
    return (math.cos(a) * R_IN, math.sin(a) * R_IN)


def half_piece():
    """Right half of the 'up' piece of a ring. A ring is four identical pieces
    (each = this half + its mirror) at 90 deg steps, so rotating a ring by 90 deg
    lands on the same picture and the outer tips never change radius."""
    a0, a1 = arc(-87), arc(-45)
    top = (G + 1, -73)
    notch = (31, -31)
    return [a0, top, notch, a1]


LIGHT = (-0.45, -0.89)


def shade(cent, quad, dark):
    """Colour a facet by the direction it faces (light from top-left)."""
    qx, qy = quad
    d = math.hypot(*cent) or 1
    nx, ny = cent[0] * qx / d, cent[1] * qy / d
    b = clamp01(0.5 + 0.55 * (nx * LIGHT[0] + ny * LIGHT[1]))
    c = lerp_c(SHELL_LO, SHELL_HI, b)
    return lerp_c(c, (30, 34, 44), dark)


class Xf:
    """local -> supersampled frame coords."""

    def __init__(self, cx, cy, rot, sx=1.0, sy=1.0):
        self.cx, self.cy, self.rot, self.sx, self.sy = cx, cy, rot, sx, sy
        self.c, self.s = math.cos(rot), math.sin(rot)

    def __call__(self, p):
        x, y = p[0] * self.sx, p[1] * self.sy
        return ((self.cx + x * self.c - y * self.s) * S, (self.cy + x * self.s + y * self.c) * S)

    def r(self, v):
        return v * S * (self.sx + self.sy) / 2


def glow_layer(size, cx, cy, radius, color, alpha, blur):
    g = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(g).ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=color + (alpha,))
    return g.filter(ImageFilter.GaussianBlur(blur))


def star(draw, x, y, r, color, alpha=255):
    pts = []
    for i in range(8):
        a = i * math.pi / 4 - math.pi / 2
        rr = r if i % 2 == 0 else r * 0.22
        pts.append((x + math.cos(a) * rr, y + math.sin(a) * rr))
    draw.polygon(pts, fill=color + (alpha,))


def draw_ghost(P):
    """P: dict of pose parameters (see defaults)."""
    d = dict(dx=0, dy=0, rot=0, sx=1.0, sy=1.0, open=0.0, wob=0.0, dark=0.0,
             eye=CYAN, glow=1.0, pupil=(0, 0), blink=1.0, scan=None, flash=0.0,
             droop=0.0, fx=None, eyeoff=(0, 0), rf=0.0, rb=0.0)
    d.update(P)
    W, H = FW * S, FH * S
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    xf = Xf(FW / 2 + d["dx"], FH / 2 + d["dy"], d["rot"], d["sx"], d["sy"])
    ex, ey = xf((0, 0))
    eye = d["eye"]

    # soft ambient glow of the eye colour
    img = Image.alpha_composite(img, glow_layer((W, H), ex, ey, min(46, 32 * d["glow"]) * S, eye, 44, 9 * S))

    # fx drawn behind the body (trails, rings)
    if d["fx"]:
        d["fx"](img, xf, "back")

    dr = ImageDraw.Draw(img)

    # soft ground shadow: shrinks and fades as the ghost rises
    k = clamp01(1 - max(0.0, -d["dy"]) * 0.014)
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sx_, sy_ = FW / 2 * S, 193 * S
    rx, ry = 38 * S * (0.6 + 0.4 * k), 5.5 * S * (0.6 + 0.4 * k)
    ImageDraw.Draw(sh).ellipse([sx_ - rx, sy_ - ry, sx_ + rx, sy_ + ry], fill=(0, 0, 0, int(80 * (0.45 + 0.55 * k))))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(4 * S)))
    dr = ImageDraw.Draw(img)

    def ring(scale, phi, op, dark_add, caps):
        half = half_piece()
        for piece in range(4):
            ang = piece * math.pi / 2 + phi
            ca, sa = math.cos(ang), math.sin(ang)

            def place(p, mirror):
                x, y = p[0] * mirror * scale, p[1] * scale - op
                return (x * ca - y * sa, x * sa + y * ca + d["droop"])

            for mirror in (1, -1):
                pts_l = [place(p, mirror) for p in half]
                cent = (sum(p[0] for p in pts_l) / 4, sum(p[1] for p in pts_l) / 4)
                col = shade(cent, (1, 1), min(1.0, d["dark"] + dark_add))
                pts = [xf(p) for p in pts_l]
                dr.polygon(pts, fill=col + (255,))
                cx_, cy_ = sum(p[0] for p in pts) / 4, sum(p[1] for p in pts) / 4
                inset = [(cx_ + (p[0] - cx_) * 0.66, cy_ + (p[1] - cy_) * 0.66) for p in pts]
                dr.polygon(inset, fill=lerp_c(col, SHELL_HI, 0.22) + (255,))
                dr.polygon(pts, outline=lerp_c(col, (30, 38, 54), 0.6) + (255,), width=int(S * 1.2))
                if caps:
                    cc = (sum(p[0] for p in half) / 4, sum(p[1] for p in half) / 4)
                    t0 = half[1]
                    t1 = (lerp(half[1][0], half[2][0], 0.38), lerp(half[1][1], half[2][1], 0.38))
                    cap = [t0, t1, (lerp(t1[0], cc[0], 0.36), lerp(t1[1], cc[1], 0.36)), (lerp(t0[0], cc[0], 0.36), lerp(t0[1], cc[1], 0.36))]
                    dr.polygon([xf(place(p, mirror)) for p in cap], fill=lerp_c(GOLD, (50, 30, 20), d["dark"]) + (255,))

    # back ring: smaller, darker, offset half a step so its points peek out of the front ring's notches
    ring(0.97, math.pi / 4 + d["rb"] - d["wob"], d["open"] * 0.5, 0.22, False)

    # inner core: dark sphere between the two rings
    cxx, cyy = xf((0, d["droop"] * 0.5))
    rc = xf.r(30)
    dr.ellipse([cxx - rc, cyy - rc, cxx + rc, cyy + rc], fill=CORE_DARK + (255,))
    rc2 = xf.r(27)
    dr.ellipse([cxx - rc2, cyy - rc2, cxx + rc2, cyy + rc2], outline=METAL + (255,), width=int(S * 1.2))

    # front ring
    ring(1.0, d["rf"] + d["wob"], d["open"], 0.0, True)

    # eye housing (shifts a little when the shell turns)
    xf = Xf(xf.cx + d["eyeoff"][0], xf.cy + d["eyeoff"][1], xf.rot, xf.sx, xf.sy)
    def disc(r, fill, ox=0, oy=0, ry=None):
        ry = r if ry is None else ry
        x, y = xf((ox, oy))
        dr.ellipse([x - xf.r(r), y - xf.r(ry), x + xf.r(r), y + xf.r(ry)], fill=fill + (255,))

    disc(26.5, (14, 18, 28))
    disc(25, (82, 92, 108))
    disc(23, (11, 17, 32))
    disc(20.5, (18, 30, 56))
    ex_, ey_ = xf((0, 0))
    rr_ = xf.r(20.5)
    dr.ellipse([ex_ - rr_, ey_ - rr_, ex_ + rr_, ey_ + rr_], outline=eye + (110,), width=int(S * 0.9))

    # glowing diamond glyph (blink squashes it vertically)
    b = max(0.08, d["blink"])
    px_, py_ = d["pupil"]

    def diamond(h, fill, outline=None, ox=0, oy=0):
        pts = [xf((ox, oy - h * b)), xf((ox + h, oy)), xf((ox, oy + h * b)), xf((ox - h, oy))]
        dr.polygon(pts, fill=fill + (255,), outline=(outline + (255,)) if outline else None, width=int(S * 1.2))

    diamond(14, lerp_c(eye, (6, 26, 60), 0.5), eye, px_ * 0.5, py_ * 0.5)
    diamond(9.6, lerp_c(eye, (255, 255, 255), 0.55), None, px_ * 0.7, py_ * 0.7)
    diamond(4.8, lerp_c(eye, (10, 28, 60), 0.6), None, px_, py_)
    diamond(1.8, (255, 255, 255), None, px_, py_)
    for sgn in (-1, 1):
        x0, y0 = xf((sgn * 17, -1.8))
        x1, y1 = xf((sgn * 17, 1.8))
        dr.line([x0, y0, x1, y1], fill=eye + (230,), width=int(S * 1.2))

    # scan line sweeping the lens
    if d["scan"] is not None:
        y_ = -12 + d["scan"] * 24
        x0, y0 = xf((-12, y_))
        x1, y1 = xf((12, y_))
        dr.line([x0, y0, x1, y1], fill=(255, 255, 255, 235), width=int(S * 1.4))

    if d["flash"] > 0:
        img = Image.alpha_composite(img, glow_layer((W, H), ex, ey, 30 * S, (255, 255, 255), int(190 * d["flash"]), 7 * S))

    if d["fx"]:
        d["fx"](img, xf, "front")

    return img.resize((FW, FH), Image.LANCZOS)


# ── effects ────────────────────────────────────────────────
def fx_trail(direction, strength):
    def fx(img, xf, layer):
        if layer != "back":
            return
        dr = ImageDraw.Draw(img)
        for i, (yy, ln) in enumerate(((-30, 34), (-8, 50), (14, 40), (36, 28))):
            x0 = -direction * (62 + (i % 2) * 4)
            x1 = x0 - direction * ln * strength
            for k in range(6):
                t = k / 6
                a = int(130 * (1 - t) * strength)
                xa, ya = xf((lerp(x0, x1, t), yy))
                xb, yb = xf((lerp(x0, x1, t + 1 / 6), yy))
                dr.line([xa, ya, xb, yb], fill=CYAN + (a,), width=int(S * lerp(2.4, 0.8, t)))
    return fx


def fx_ring(t, color):
    """expanding ring from the eye (waiting)."""
    def fx(img, xf, layer):
        if layer != "back":
            return
        dr = ImageDraw.Draw(img)
        for off in (0.0, 0.5):
            u = (t + off) % 1.0
            r = 30 + u * 34
            x, y = xf((0, 0))
            rr = xf.r(r)
            dr.ellipse([x - rr, y - rr, x + rr, y + rr], outline=color + (int(200 * (1 - u)),), width=int(S * 1.6))
    return fx


def fx_bubble(t, color, strength=1.0):
    """translucent scan bubble around the core (activation)."""
    def fx(img, xf, layer):
        if layer != "back":
            return
        r = 47 + 4 * math.sin(t * 2 * math.pi)
        x, y = xf((0, 0))
        rr = xf.r(r)
        b = Image.new("RGBA", img.size, (0, 0, 0, 0))
        bd = ImageDraw.Draw(b)
        bd.ellipse([x - rr, y - rr, x + rr, y + rr], fill=color + (int(50 * strength),), outline=color + (int(190 * strength),), width=int(S * 1.6))
        r2 = xf.r(r * 0.78)
        bd.ellipse([x - r2, y - r2, x + r2, y + r2], outline=color + (int(70 * strength),), width=int(S * 1.0))
        img.alpha_composite(b)
    return fx


def fx_orbit(t):
    """energy motes circling the shell (running)."""
    def fx(img, xf, layer):
        dr = ImageDraw.Draw(img)
        for i in range(3):
            a = t * 2 * math.pi + i * 2 * math.pi / 3
            x, y = xf((math.cos(a) * 72, math.sin(a) * 50))
            front = math.sin(a) > 0
            if (layer == "front") == front:
                r = S * (3.4 if front else 2.4)
                dr.ellipse([x - r, y - r, x + r, y + r], fill=(190, 240, 255, 240))
                for k in range(1, 4):
                    a2 = a - k * 0.22
                    x2, y2 = xf((math.cos(a2) * 72, math.sin(a2) * 50))
                    r2 = r * (1 - k * 0.24)
                    dr.ellipse([x2 - r2, y2 - r2, x2 + r2, y2 + r2], fill=CYAN + (int(150 - k * 35),))
    return fx


def fx_arc(t):
    """rotating analysis ring (review)."""
    def fx(img, xf, layer):
        if layer != "front":
            return
        dr = ImageDraw.Draw(img)
        x, y = xf((0, 0))
        rr = xf.r(74)
        rry = xf.r(54)
        start = t * 360
        for k, (span, alpha) in enumerate(((70, 235), (24, 160))):
            s0 = start + k * 180
            dr.arc([x - rr, y - rry, x + rr, y + rry], s0, s0 + span, fill=(170, 235, 255, alpha), width=int(S * 2.2))
    return fx


def fx_sparkles(t):
    def fx(img, xf, layer):
        if layer != "front":
            return
        dr = ImageDraw.Draw(img)
        k = math.sin(t * math.pi)
        for (x, y, ph) in ((-66, -40, 0.0), (70, -34, 0.2), (-72, 30, 0.35), (64, 50, 0.1), (-38, -64, 0.3)):
            kk = clamp01(k * 1.3 - ph)
            if kk > 0:
                px, py = xf((x, y - (1 - kk) * 6))
                star(dr, px, py, S * 10 * kk, (255, 236, 160), int(255 * kk))
    return fx


def fx_sparks(t, n):
    def fx(img, xf, layer):
        if layer != "front":
            return
        dr = ImageDraw.Draw(img)
        for i in range(3):
            if (n + i) % 3 == 0:
                continue
            ang = math.radians(-120 + i * 55 + (n * 37) % 20)
            r0, r1 = 40, 40 + 10 + (n * 7 + i * 5) % 8
            x0, y0 = xf((math.cos(ang) * r0, math.sin(ang) * r0 - 8))
            x1, y1 = xf((math.cos(ang) * r1, math.sin(ang) * r1 - 8))
            dr.line([x0, y0, x1, y1], fill=(255, 190, 90, 230), width=int(S * 1.5))
    return fx


# ── pose per state / frame ─────────────────────────────────
def both(a, b):
    def fx(img, xf, layer):
        a(img, xf, layer)
        b(img, xf, layer)
    return fx


def pose(state, i, n):
    t = i / n  # loop phase 0..1 (no duplicated end frame)
    tau = 2 * math.pi
    s = math.sin

    if state == "idle":
        blink = 0.1 if i == 3 else 1.0
        a = t * tau  # pure drift: side to side with a small bob, no turning
        return dict(dx=0, dy=s(a) * 3.2, rot=0.0, sx=1.0, eyeoff=(0, 0),
                    glow=0.9 + 0.15 * (s(a) * 0.5 + 0.5), open=0.5 + 0.4 * (s(2 * a) * 0.5 + 0.5), blink=blink,
                    pupil=(s(a) * 1.0, 0))

    if state in ("running-right", "running-left"):
        dirn = 1 if state == "running-right" else -1
        return dict(dx=dirn * 2, dy=s(t * 2 * tau) * 3.5, rot=dirn * (0.22 + 0.04 * s(t * 2 * tau)),
                    open=2.5 + 2 * s(t * 2 * tau), wob=dirn * 0.03 * s(t * 2 * tau),
                    pupil=(dirn * 3.5, 0), glow=1.15, fx=fx_trail(dirn, 0.8 + 0.2 * s(t * 2 * tau)),
                    sx=1.0 + 0.03 * s(t * 2 * tau))

    if state == "waving":
        k = s(t * tau)
        return dict(dy=-abs(s(t * math.pi)) * 5, rot=k * 0.3, open=4 + 4 * abs(s(t * math.pi)),
                    wob=k * 0.04, glow=1.2, flash=0.18 + 0.15 * abs(k), pupil=(k * 2.5, 1))

    if state == "jumping":
        sq = [0.92, 1.0, 1.04, 1.0, 0.94][i]
        lift = [0, 5, 9, 5, 0][i]
        return dict(dy=-lift, sy=sq, sx=2 - sq if sq < 1 else 1.0,
                    open=[2, 4, 6, 4, 2][i], rot=[0, 0.08, 0, -0.08, 0][i], glow=1.25,
                    flash=[0, 0.3, 0.7, 0.3, 0][i], pupil=(0, -2), fx=fx_sparkles(i / (n - 1)))

    if state == "failed":
        shake = [-3, 3, -2.5, 2.5, -2, 2, -1, 0][i]
        flick = [1.0, 0.25, 0.9, 0.3, 1.0, 0.5, 0.9, 1.0][i]
        return dict(dx=shake, dy=7 + (1 if i % 2 else 0), rot=-0.16 + 0.03 * s(t * tau),
                    open=-2, droop=2.5, dark=0.28, eye=RED, glow=0.45 + 0.75 * flick, blink=0.45 + 0.4 * flick,
                    pupil=(0, 3), fx=fx_sparks(t, i))

    if state == "waiting":
        look = [(-4, 0), (-2, 0), (3, -1), (5, 0), (2, 0), (-1, 0)][i]
        return dict(dy=s(t * tau) * 3, rot=s(t * tau) * 0.06, open=7 + 2.5 * s(t * tau), eye=AMBER,
                    glow=1.05 + 0.3 * s(t * tau), pupil=look, wob=0.04 * s(t * tau),
                    blink=0.1 if i == 5 else 1.0, fx=fx_ring(t, AMBER))

    if state == "running":
        # levitate, then once per loop the rings click a quarter turn in opposite directions
        prog = [0, 0, 0.25, 0.7, 1.05, 1.0, 1.0, 1.0][i]
        a = t * tau
        return dict(dy=-9 + s(a) * 2.5, rot=s(a) * 0.03, open=2.5, rf=prog * math.pi / 2, rb=-prog * math.pi / 2,
                    glow=1.45, flash=0.1, pupil=(s(a) * 2, 0), eye=lerp_c(CYAN, (140, 225, 255), 0.35),
                    fx=fx_bubble(t, CYAN))

    if state == "review":
        # thinking: slower burst the other way round, eye scanning
        prog = [0, 0, 0.1, 0.4, 0.8, 1.0, 1.0, 1.0][i]
        a = t * tau
        return dict(dy=-7 + s(a) * 2, rot=-0.03 * s(a), open=2, rf=-prog * math.pi / 2, rb=prog * math.pi / 2,
                    glow=1.2, pupil=(math.cos(a) * 3, math.sin(a) * 2.2), scan=t, fx=fx_bubble(t, CYAN, 0.5),
                    eye=lerp_c(CYAN, (150, 230, 255), 0.25))

    raise ValueError(state)


def build_sheet():
    sheet = Image.new("RGBA", (FW * COLS, FH * ROWS), (0, 0, 0, 0))
    for row, (name, n) in enumerate(STATES):
        for col in range(n):
            sheet.paste(draw_ghost(pose(name, col, n)), (col * FW, row * FH))
    return sheet


def main():
    out = os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "~/.hermes/pets/d2-ghost")
    os.makedirs(out, exist_ok=True)
    sheet = build_sheet()
    target = os.path.join(out, "spritesheet.webp")
    if os.path.exists(target) and not os.path.exists(target + ".orig"):
        shutil.copy2(target, target + ".orig")
    sheet.save(target, "WEBP", quality=95, method=6)
    with open(os.path.join(out, "pet.json"), "w") as f:
        json.dump({
            "id": "d2-ghost",
            "displayName": "D2 Ghost",
            "description": "A Destiny 2 Ghost companion: faceted shell plates that open around a glowing eye, "
                           "reacting to idle, working, reasoning, waiting-on-you and error states.",
            "spritesheetPath": "spritesheet.webp",
        }, f, indent=2)
    print("wrote", target)


if __name__ == "__main__":
    main()
