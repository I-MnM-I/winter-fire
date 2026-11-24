#!/usr/bin/env python3
"""
winter_fire.py - an animated ASCII campfire on a snowy winter night.

Everything on screen is a text character: pine trees, falling snow, stars,
the moon, the fire, its sparks and the warm glow it throws on the snow.
Pure Python 3 standard library; needs a terminal with 24-bit colour
(GNOME Terminal, Konsole, kitty, WezTerm, Alacritty, Ptyxis, ...).

Keys:
  q / Esc     quit                space      pause
  + / -       sim speed           <- / ->    wind
  up / down   fire size           g          glow on/off
  m           moon on/off         s          snow amount (default: blizzard)
  r           new forest          h          help
"""

import math
import os
import random
import select
import shutil
import sys
import time

SIM_HZ = 30.0          # the simulation runs at a fixed rate, independent of the frame rate
MAX_FPS = 30.0

# ---------------------------------------------------------------- helpers

def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def lerp_color(stops, t):
    """stops: list of (pos, (r, g, b)) sorted by pos; t in 0..1."""
    if t <= stops[0][0]:
        return stops[0][1]
    for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
        if t <= p1:
            k = (t - p0) / (p1 - p0)
            return (c0[0] + (c1[0] - c0[0]) * k,
                    c0[1] + (c1[1] - c0[1]) * k,
                    c0[2] + (c1[2] - c0[2]) * k)
    return stops[-1][1]


FIRE_CHARS = " .,:;+*oxO0%@"
FIRE_COLORS = [
    (0.00, (30, 0, 0)),
    (0.18, (120, 15, 0)),
    (0.35, (210, 55, 0)),
    (0.55, (255, 130, 10)),
    (0.75, (255, 205, 50)),
    (0.90, (255, 245, 150)),
    (1.00, (255, 255, 235)),
]
SPARK_COLORS = [
    (0.0, (110, 20, 0)),
    (0.4, (230, 80, 0)),
    (0.8, (255, 190, 40)),
    (1.0, (255, 250, 190)),
]

# ---------------------------------------------------------------- scene

class Scene:
    """Static backdrop (sky, moon, trees, ground) plus the living parts."""

    def __init__(self, W, H, seed=None, snow_level=1):
        self.W, self.H = W, H
        self.rng = random.Random(seed)
        self.N = W * H
        self.horizon = int(H * 0.68)
        self.fx = W // 2
        self.fy = H - max(3, int(H * 0.10))          # row of the logs
        self.snow_level = snow_level
        self._build_static()
        self._build_fire()
        self._build_snow()
        self.sparks = []
        self.t = 0.0
        self.flick = 1.0

    # ---------- static backdrop
    def _put(self, x, y, ch, col):
        if 0 <= x < self.W and 0 <= y < self.H:
            i = y * self.W + x
            self.ch[i] = ch
            self.col[i] = col

    def _build_static(self):
        W, H, rng = self.W, self.H, self.rng
        self.ch = [" "] * self.N
        self.col = [(0, 0, 0)] * self.N

        # stars (twinkle at render time)
        self.stars = []
        for _ in range(int(W * self.horizon * 0.035)):
            x, y = rng.randrange(W), rng.randrange(max(1, self.horizon - 2))
            ch = rng.choice("..........''`+*")
            b = rng.uniform(70, 200)
            self.stars.append((y * W + x, ch, b, rng.uniform(0, 6.28), rng.uniform(0.5, 3.0)))

        # glow falloff map (fire light), aspect corrected
        self.glow = [0.0] * self.N
        gy = self.fy - 2
        rad = max(9.0, W * 0.12)
        for y in range(H):
            ground = 1.0 if y >= self.horizon else 0.65
            for x in range(W):
                dx = (x - self.fx)
                dy = (y - gy) * 2.1
                d2 = (dx * dx + dy * dy) / (rad * rad)
                self.glow[y * W + x] = ground / (1.0 + d2 * 1.3)

        # ground: perspective snow field
        for y in range(self.horizon, H):
            depth = (y - self.horizon) / max(1, H - self.horizon)   # 0 far .. 1 near
            for x in range(W):
                r = rng.random()
                if r < 0.30 + 0.45 * depth + 0.6 * self.glow[y * W + x]:
                    ch = rng.choice("_-=~.,_-_--=")
                    k = 0.45 + 0.45 * depth + rng.uniform(-0.08, 0.08)
                    self._put(x, y, ch, (110 * k, 120 * k, 140 * k))

        # trees: three depth layers, back to front
        cx = W / 2
        clearing = W * 0.13
        layers = [
            # count, height range (fraction of H), base offset, colour, brightness, keep clearing
            (max(6, W // 5), (0.22, 0.38), 0, (60, 84, 90), 0.6, False),
            (max(4, W // 9), (0.38, 0.58), 2, (82, 110, 112), 0.8, True),
            (max(2, W // 22), (0.62, 0.92), 5, (100, 130, 128), 1.0, True),
        ]
        for count, (h0, h1), boff, colr, bright, keep_clear in layers:
            for _ in range(count):
                tx = rng.uniform(-4, W + 4)
                if keep_clear and abs(tx - cx) < clearing + (boff * 1.5):
                    # push it out to the side of the clearing
                    tx = cx + math.copysign(clearing + boff * 1.5 + rng.uniform(0, W * 0.25), tx - cx)
                th = int(H * rng.uniform(h0, h1))
                base = self.horizon + boff + rng.randint(-1, 1)
                self._tree(int(tx), base, th, colr, bright)

        # fire pit: ring of stones + logs
        self.pit = []
        fw = max(4, W // 22)
        for k in range(-fw - 2, fw + 3):
            for dy in (0, 1):
                x, y = self.fx + k, self.fy + dy
                edge = abs(k) >= fw
                if dy == 0 and not edge:
                    ch = rng.choice("=#=#==")
                    c = (110, 62, 30)
                elif dy == 1:
                    ch = rng.choice("oO0o()")
                    c = (110, 108, 104)
                else:
                    ch = rng.choice("oO")
                    c = (110, 108, 104)
                self._put(x, y, ch, c)
        # crossed logs
        for k in range(fw):
            self._put(self.fx - fw + k, self.fy - 1 + (k * 2 // fw) - 1 + 1, "\\", (120, 70, 35))
            self._put(self.fx + fw - k, self.fy - 1 + (k * 2 // fw) - 1 + 1, "/", (120, 70, 35))

        # moon (overlay so it can be toggled)
        self.moon = []
        mx, my = int(W * 0.80), max(2, int(H * 0.12))
        R = max(2.0, H * 0.065)
        for y in range(int(my - R - 3), int(my + R + 4)):
            for x in range(int(mx - 2 * R - 6), int(mx + 2 * R + 7)):
                dx, dy = (x - mx) / 2.0, (y - my)
                d = math.hypot(dx, dy)
                if not (0 <= x < W and 0 <= y < H):
                    continue
                if d <= R:
                    crater = math.sin(x * 1.7) * math.cos(y * 2.3) + math.sin(x * 0.6 + y)
                    ch = "@" if crater < 0.6 else "O"
                    if d > R - 0.8:
                        ch = "0"
                    b = 235 - 60 * (crater > 0.6)
                    self.moon.append((y * W + x, ch, (b, b, b * 0.88)))
                elif d <= R + 2.5 and rng.random() < 0.35:
                    a = 1 - (d - R) / 2.5
                    self.moon.append((y * W + x, rng.choice(".'`"), (110 * a + 40, 110 * a + 40, 120 * a + 50)))

    def _tree(self, cx, base, h, colr, bright):
        rng = self.rng
        trunk = max(1, int(h * 0.10))
        top = base - h
        tiers = rng.randint(3, 6)
        maxw = h * rng.uniform(0.75, 1.0)          # columns (cells are ~2x tall)
        r0, g0, b0 = colr
        for y in range(top, base - trunk + 1):
            p = (y - top) / max(1, h - trunk)
            tier_pos = (p * tiers) % 1.0
            width = maxw * p * (0.55 + 0.45 * tier_pos) + 1
            half = int(width / 2)
            for x in range(cx - half, cx + half + 1):
                j = rng.uniform(0.75, 1.15)
                if y == top:
                    ch, c = rng.choice("A^*"), (210, 215, 225)
                elif x == cx - half:
                    ch, c = "/", (r0 * j * bright * 1.2, g0 * j * bright * 1.2, b0 * j * bright * 1.2)
                elif x == cx + half:
                    ch, c = "\\", (r0 * j * bright * 1.2, g0 * j * bright * 1.2, b0 * j * bright * 1.2)
                else:
                    ch = rng.choice("AAmm^^##%*\\/")
                    c = (r0 * j * bright, g0 * j * bright, b0 * j * bright)
                # snow lying on the top of each tier, heavier toward the edges
                edge = abs(x - cx) / max(1, half)
                if tier_pos < 0.35 and rng.random() < 0.35 + 0.4 * edge:
                    ch = rng.choice("**..'~*")
                    s = 170 + 70 * bright
                    c = (s * 0.92, s * 0.95, s)
                self._put(x, y, ch, c)
        for y in range(base - trunk + 1, base + 1):
            for x in (cx, cx + 1) if h > 14 else (cx,):
                self._put(x, y, rng.choice("||#"), (85 * bright, 62 * bright, 42 * bright))

    # ---------- fire simulation (classic heat-propagation, a.k.a. "doom fire")
    def _build_fire(self):
        self.FW = max(10, self.W // 10) | 1
        self.FH = max(8, int(self.H * 0.26))
        self.heat = [[0.0] * self.FW for _ in range(self.FH)]

    def step_fire(self, intensity, wind):
        FW, FH, rng = self.FW, self.FH, self.rng
        heat = self.heat
        c = FW // 2
        # fuel row: hot in the middle, embers flare up at random
        base = heat[FH - 1]
        for x in range(FW):
            e = abs(x - c) / (c + 0.5)
            v = intensity * (1.25 - 0.8 * e * e) * rng.uniform(0.7, 1.0)
            if rng.random() < 0.06:
                v *= 0.4
            base[x] = max(0.0, v)
        # propagate upward with random drift; wind pushes the flame sideways
        cool = 0.55 / FH
        for y in range(FH - 1):
            below = heat[y + 1]
            row = heat[y]
            lift = 1.0 - y / FH
            for x in range(FW):
                sx = x + rng.randint(-1, 1)
                if rng.random() < abs(wind) * 0.35:
                    sx -= 1 if wind > 0 else -1
                sx = clamp(sx, 0, FW - 1)
                v = below[sx] * 0.62 + below[x] * 0.38
                e = abs(x - c) / (c + 0.5)
                v -= rng.random() * cool * (1.2 + 3.2 * e * e) * (0.5 + 0.9 * lift)
                row[x] = v if v > 0 else 0.0
        # flicker = how much heat is in the lower half of the fire
        tot = 0.0
        for y in range(FH // 2, FH):
            tot += sum(heat[y])
        target = tot / (FW * (FH - FH // 2) * 0.45 + 1e-6)
        self.flick += (target - self.flick) * 0.35

        # sparks
        if rng.random() < 0.55 * intensity:
            for _ in range(rng.randint(1, 2)):
                self.sparks.append([
                    self.fx + rng.uniform(-FW * 0.25, FW * 0.25),
                    self.fy - FH * rng.uniform(0.35, 0.75),
                    rng.uniform(-0.25, 0.25),
                    -rng.uniform(0.35, 0.9),
                    0, rng.randint(25, 70),
                ])
        alive = []
        for s in self.sparks:
            s[2] += wind * 0.03 + rng.uniform(-0.08, 0.08)
            s[2] *= 0.96
            s[3] *= 0.985
            s[0] += s[2]
            s[1] += s[3]
            s[4] += 1
            if s[4] < s[5] and s[1] > 0 and 0 <= s[0] < self.W:
                alive.append(s)
        self.sparks = alive

    # ---------- snow
    def _build_snow(self):
        self.flakes = []
        n = int(self.W * self.H * (0.0, 0.03, 0.07, 0.13, 0.45)[self.snow_level])
        for _ in range(n):
            self.flakes.append(self._new_flake(top=False))

    def _new_flake(self, top=True):
        rng = self.rng
        z = rng.random() ** (1.0 if self.snow_level >= 4 else 1.6)   # many far flakes, fewer near ones
        if z < 0.35:
            ch = "."
        elif z < 0.7:
            ch = rng.choice("+*'")
        else:
            ch = rng.choice("**o")
        return [rng.uniform(-10, self.W + 10),
                rng.uniform(-3, 0) if top else rng.uniform(0, self.H),
                z, rng.uniform(0, 6.28), ch]

    def set_snow(self, level):
        self.snow_level = level
        self._build_snow()

    def step_snow(self, wind):
        W, H, hz = self.W, self.H, self.horizon
        t = self.t
        for i, f in enumerate(self.flakes):
            x, y, z, ph, ch = f
            y += 0.07 + 0.38 * z
            x += wind * (0.15 + 0.7 * z) + math.sin(t * 1.3 + ph) * 0.12 * (0.3 + z)
            land = hz + z * (H - hz)
            # blown off one side -> re-enter on the other side at the same height
            if x < -10:
                x += W + 20
            elif x > W + 10:
                x -= W + 20
            if y >= land:
                self.flakes[i] = self._new_flake()
            else:
                f[0], f[1] = x, y

    # ---------- one fixed sim tick
    def tick(self, intensity, wind):
        self.t += 1.0 / SIM_HZ
        if self.snow_level >= 3:   # heavy snow comes with gusts
            t = self.t
            g = math.sin(t * 0.35) * 0.6 + math.sin(t * 0.9 + 1.3) * 0.3 + math.sin(t * 2.1) * 0.12
            wind = wind + g * (0.35 if self.snow_level == 3 else 0.7) * (1 if wind >= 0 else -1)
        self.step_fire(intensity, wind)
        self.step_snow(wind)

    # ---------- composite the frame into (chars, colours)
    def render(self, glow_on, moon_on):
        W, N = self.W, self.N
        ch = list(self.ch)
        col = self.col
        glow = self.glow
        L0 = (1.0 + 0.45 * self.flick) if glow_on else 0.0
        R = [0.0] * N
        G = [0.0] * N
        B = [0.0] * N
        for i in range(N):
            r, g, b = col[i]
            L = glow[i] * L0
            R[i] = r * (1 + L * 2.3) + L * 30
            G[i] = g * (1 + L * 1.05) + L * 6
            B[i] = b * (1 - L * 0.35)

        t = self.t
        for i, c, b, ph, sp in self.stars:
            if ch[i] == " ":
                k = b * (0.55 + 0.45 * math.sin(t * sp + ph))
                ch[i] = c
                R[i], G[i], B[i] = k * 0.9, k * 0.92, k

        if moon_on:
            for i, c, (r, g, b) in self.moon:
                ch[i] = c
                R[i], G[i], B[i] = r, g, b

        # snow flakes
        Hh = self.H
        for x, y, z, ph, c in self.flakes:
            xi, yi = int(x), int(y)
            if 0 <= xi < W and 0 <= yi < Hh:
                i = yi * W + xi
                s = 120 + 135 * z
                L = glow[i] * L0
                ch[i] = c
                R[i] = s * (1 + L * 1.3)
                G[i] = s * (1 + L * 0.7)
                B[i] = s * (1.05 - L * 0.4)

        # fire
        FW, FH, heat = self.FW, self.FH, self.heat
        x0 = self.fx - FW // 2
        y0 = self.fy - FH
        nfc = len(FIRE_CHARS) - 1
        for y in range(FH):
            sy = y0 + y
            if not 0 <= sy < Hh:
                continue
            row = heat[y]
            for x in range(FW):
                v = row[x]
                if v < 0.07:
                    continue
                sx = x0 + x
                if not 0 <= sx < W:
                    continue
                v = 1.0 if v > 1.0 else v
                i = sy * W + sx
                ch[i] = FIRE_CHARS[max(1, int(v * nfc + 0.5))]
                R[i], G[i], B[i] = lerp_color(FIRE_COLORS, v)

        # sparks
        for x, y, vx, vy, age, life in self.sparks:
            xi, yi = int(x), int(y)
            if 0 <= xi < W and 0 <= yi < Hh:
                k = 1 - age / life
                i = yi * W + xi
                ch[i] = "*" if k > 0.75 else "+" if k > 0.5 else "'" if k > 0.25 else "."
                R[i], G[i], B[i] = lerp_color(SPARK_COLORS, k)
        return ch, R, G, B


# ---------------------------------------------------------------- terminal

class Terminal:
    def __init__(self):
        import termios
        import tty
        self.termios = termios
        self.fd = sys.stdin.fileno()
        self.old = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)
        self.out = sys.stdout
        self.out.write("\x1b[?1049h\x1b[?25l\x1b[2J")
        self.out.flush()

    def close(self):
        self.out.write("\x1b[0m\x1b[?25h\x1b[?1049l")
        self.out.flush()
        self.termios.tcsetattr(self.fd, self.termios.TCSADRAIN, self.old)

    def keys(self):
        out = []
        while select.select([self.fd], [], [], 0)[0]:
            data = os.read(self.fd, 64).decode(errors="ignore")
            if not data:
                break
            i = 0
            while i < len(data):
                if data.startswith("\x1b[", i) and i + 2 < len(data):
                    out.append({"A": "up", "B": "down", "C": "right", "D": "left"}.get(data[i + 2], ""))
                    i += 3
                else:
                    out.append("esc" if data[i] == "\x1b" else data[i])
                    i += 1
        return out


_code_cache = {}


def frame_to_ansi(ch, R, G, B, W, H):
    parts = ["\x1b[H"]
    cache = _code_cache
    for y in range(H):
        last = -1
        base = y * W
        line = []
        for i in range(base, base + W):
            c = ch[i]
            if c == " ":
                line.append(" ")
                continue
            r = int(R[i]); g = int(G[i]); b = int(B[i])
            r = 255 if r > 255 else (0 if r < 0 else r) >> 2
            g = 255 if g > 255 else (0 if g < 0 else g) >> 2
            b = 255 if b > 255 else (0 if b < 0 else b) >> 2
            if r == 255:
                r = 63
            if g == 255:
                g = 63
            if b == 255:
                b = 63
            key = (r << 12) | (g << 6) | b
            if key != last:
                code = cache.get(key)
                if code is None:
                    code = "\x1b[38;2;%d;%d;%dm" % (r * 4 + 3 * (r > 0), g * 4 + 3 * (g > 0), b * 4 + 3 * (b > 0))
                    cache[key] = code
                line.append(code)
                last = key
            line.append(c)
        parts.append("".join(line))
        parts.append("\r\n" if y < H - 1 else "")
    return "".join(parts)


HELP = [
    "  WINTER NIGHT  ",
    "",
    "  q / Esc    quit",
    "  space      pause",
    "  + / -      simulation speed",
    "  <- / ->    wind",
    "  up / down  fire size",
    "  g          fire glow on/off",
    "  m          moon on/off",
    "  s          snow: none/light/medium/heavy/blizzard",
    "  r          grow a new forest",
    "  h          close this help",
]


def overlay_help(ch, R, G, B, W, H):
    bw = max(len(s) for s in HELP) + 4
    bh = len(HELP) + 2
    x0, y0 = (W - bw) // 2, (H - bh) // 2
    for y in range(bh):
        for x in range(bw):
            X, Y = x0 + x, y0 + y
            if not (0 <= X < W and 0 <= Y < H):
                continue
            i = Y * W + X
            if y in (0, bh - 1):
                c = "-" if 0 < x < bw - 1 else "+"
            elif x in (0, bw - 1):
                c = "|"
            else:
                s = HELP[y - 1]
                c = s[x - 2] if 0 <= x - 2 < len(s) else " "
            ch[i] = c
            R[i], G[i], B[i] = (230, 210, 170) if y == 1 else (200, 205, 215)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="ASCII campfire on a snowy night.")
    ap.add_argument("--seed", type=int, default=None, help="random seed for the forest")
    ap.add_argument("--speed", type=float, default=0.35, help="starting speed (default 0.35)")
    ap.add_argument("--snow", type=int, default=4, choices=range(5), help="snow amount 0-4 (4 = blizzard)")
    ap.add_argument("--png", metavar="FILE", help="render one frame to a PNG (needs Pillow) and exit")
    ap.add_argument("--size", default="160x48", help="WxH for --png")
    args = ap.parse_args()

    if args.png:
        return render_png(args)

    speed, wind, fire = args.speed, 0.5, 1.0
    glow_on, moon_on, paused, show_help = True, True, False, False
    snow = args.snow
    seed = args.seed

    term = Terminal()
    try:
        size = None
        scene = None
        acc = 0.0
        last = time.perf_counter()
        fps, fcount, ftime = 0.0, 0, last
        while True:
            cols, rows = shutil.get_terminal_size((100, 30))
            if (cols, rows) != size:
                size = (cols, rows)
                scene = Scene(cols, max(5, rows - 1), seed, snow)
                for _ in range(int(SIM_HZ * 2)):          # warm the fire up
                    scene.tick(fire, wind)
                term.out.write("\x1b[2J")

            for k in term.keys():
                if k in ("q", "Q", "esc"):
                    return
                elif k == " ":
                    paused = not paused
                elif k in ("+", "="):
                    speed = min(4.0, round(speed + 0.1, 2))
                elif k in ("-", "_"):
                    speed = max(0.1, round(speed - 0.1, 2))
                elif k == "right":
                    wind = min(1.5, round(wind + 0.1, 2))
                elif k == "left":
                    wind = max(-1.5, round(wind - 0.1, 2))
                elif k == "up":
                    fire = min(1.6, round(fire + 0.1, 2))
                elif k == "down":
                    fire = max(0.2, round(fire - 0.1, 2))
                elif k in ("g", "G"):
                    glow_on = not glow_on
                elif k in ("m", "M"):
                    moon_on = not moon_on
                elif k in ("s", "S"):
                    snow = (snow + 1) % 5
                    scene.set_snow(snow)
                elif k in ("r", "R"):
                    seed = random.randrange(1 << 30)
                    scene = Scene(cols, max(5, rows - 1), seed, snow)
                    for _ in range(int(SIM_HZ * 2)):
                        scene.tick(fire, wind)
                elif k in ("h", "H", "?"):
                    show_help = not show_help

            now = time.perf_counter()
            dt = min(0.25, now - last)
            last = now
            if not paused:
                acc += dt * speed
                while acc >= 1.0 / SIM_HZ:
                    scene.tick(fire, wind)
                    acc -= 1.0 / SIM_HZ

            ch, R, G, B = scene.render(glow_on, moon_on)
            if show_help:
                overlay_help(ch, R, G, B, scene.W, scene.H)
            frame = frame_to_ansi(ch, R, G, B, scene.W, scene.H)
            status = (" WINTER NIGHT | speed %.2f | wind %+.1f | fire %.1f | glow %s | moon %s | snow %s | %2d fps | %s[h]elp "
                      % (speed, wind, fire, "on" if glow_on else "off", "on" if moon_on else "off",
                         ("off", "light", "medium", "heavy", "blizzard")[snow], fps, "PAUSED | " if paused else ""))
            term.out.write(frame + "\r\n\x1b[38;2;150;155;165m" + status[:cols - 1].ljust(cols - 1))
            term.out.flush()

            fcount += 1
            if now - ftime >= 0.5:
                fps = fcount / (now - ftime)
                fcount, ftime = 0, now
            spare = 1.0 / MAX_FPS - (time.perf_counter() - now)
            if spare > 0:
                time.sleep(spare)
    except KeyboardInterrupt:
        pass
    finally:
        term.close()


def render_png(args):
    from PIL import Image, ImageDraw, ImageFont
    W, H = (int(v) for v in args.size.lower().split("x"))
    scene = Scene(W, H - 1, args.seed, args.snow)
    for _ in range(int(SIM_HZ * 3)):
        scene.tick(1.0, 0.0)
    t0 = time.perf_counter()
    ch, R, G, B = scene.render(True, True)
    frame_to_ansi(ch, R, G, B, W, H - 1)
    print("render+encode: %.1f ms" % ((time.perf_counter() - t0) * 1000))
    cw, chh = 8, 16
    try:
        font = ImageFont.truetype("DejaVuSansMono.ttf", 13)
    except OSError:
        font = ImageFont.truetype("/usr/share/fonts/google-noto/NotoSansMono-Regular.ttf", 13)
    img = Image.new("RGB", (W * cw, (H - 1) * chh), (0, 0, 0))
    d = ImageDraw.Draw(img)
    for y in range(H - 1):
        for x in range(W):
            i = y * W + x
            if ch[i] != " ":
                c = tuple(int(clamp(v, 0, 255)) for v in (R[i], G[i], B[i]))
                d.text((x * cw, y * chh), ch[i], fill=c, font=font)
    img.save(args.png)
    print("wrote", args.png)


if __name__ == "__main__":
    main()
