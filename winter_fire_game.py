#!/usr/bin/env python3
"""
WINTER FIRE - a first-person ASCII survival-horror game for the terminal.

You are alone at a campfire in a blizzard. The fire is burning down. The only
wood is out in the dark between the trees, and something lives out there.
Keep the fire alive until dawn (06:00).

  W / S       walk forward / back      A / D       step left / right
  <- / ->     turn  (also J / L)       Shift+W     run
  E           read a note              P / Esc     pause
  Q           quit (from pause / title / end screens)

Sticks burn a little. Logs burn a long time, but you can only carry one and
it slows you down. Walk into the firelight to feed what you carry to the fire.

Pure Python 3 standard library. Needs a 24-bit colour terminal; play it
fullscreen. Everything is drawn with text characters.
"""

import math
import os
import random
import select
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from winter_fire import Scene as TitleScene, FIRE_CHARS, FIRE_COLORS, SPARK_COLORS, lerp_color  # noqa: E402

TAU = math.tau
SIM_HZ = 30.0
FOV = 1.35                 # horizontal field of view (radians)
EYE = 1.2                  # eye height (world units)
FOG = 11.0                 # visibility falloff distance in the blizzard
SIGHT = 16.5               # how far away you can pin the creature down by looking at it
HAZE = (46, 50, 62)        # colour of the blizzard far away, scaled by moonlight
WORLD_R = 36.0             # how far from the fire you can wander
NIGHT_LEN = 420.0          # seconds of real time from 22:00 to 06:00
FUEL_MAX = 100.0
STICK_FUEL, LOG_FUEL = 12.0, 34.0
MAX_STICKS = 4
EMBER_TIME = 20.0          # seconds you get to save the fire once it goes out
CREATURE_WAKES = 35.0      # seconds before it starts hunting


# ------------------------------------------------------------------ colour packing

def pk(r, g, b):
    """Pack an RGB colour into a small int (6 bits per channel)."""
    r = 0 if r < 0 else 255 if r > 255 else int(r)
    g = 0 if g < 0 else 255 if g > 255 else int(g)
    b = 0 if b < 0 else 255 if b > 255 else int(b)
    return (r >> 2) << 12 | (g >> 2) << 6 | (b >> 2)


def lit(base, amb, L, f):
    """Colour of a surface under moonlight `amb` and firelight `L`, faded by fog `f`."""
    r, g, b = base
    h = (1.0 - f) * amb          # far things fade into the pale blizzard haze, not into black
    return pk((r * (amb + 2.0 * L) + 55 * L) * f + HAZE[0] * h,
              (g * (amb + 1.0 * L) + 22 * L) * f + HAZE[1] * h,
              (b * (amb + 0.15 * L)) * f + HAZE[2] * h)


_ESC = {}


def encode(ch, key, W, H):
    out = ["\x1b[H"]
    esc = _ESC
    for y in range(H):
        last = -1
        line = []
        base = y * W
        for i in range(base, base + W):
            c = ch[i]
            if c == " ":
                line.append(" ")
                continue
            k = key[i]
            if k != last:
                e = esc.get(k)
                if e is None:
                    r, g, b = (k >> 12) & 63, (k >> 6) & 63, k & 63
                    e = "\x1b[38;2;%d;%d;%dm" % (r * 4 + 3 * (r > 0), g * 4 + 3 * (g > 0), b * 4 + 3 * (b > 0))
                    esc[k] = e
                line.append(e)
                last = k
            line.append(c)
        out.append("".join(line))
        if y < H - 1:
            out.append("\r\n")
    return "".join(out)


# ------------------------------------------------------------------ textures & art

TV, TU = 48, 25            # tree texture size (rows, cols); col 12 is the trunk line


def _make_tree_tex(rng):
    tiers = rng.randint(3, 5)
    rows, spans = [], []
    for v in range(TV):
        p = (v + 0.5) / TV
        row = [None] * TU
        if p > 0.9:
            lo, hi = 11, 13
            for u in range(lo, hi + 1):
                row[u] = (rng.choice("||#"), 2)
        else:
            q = p / 0.9
            tf = (q * tiers) % 1.0
            hu = max(0.5, q * (0.55 + 0.45 * tf) * 12)
            lo, hi = int(round(12 - hu)), int(round(12 + hu))
            for u in range(lo, hi + 1):
                if v == 0:
                    e = (rng.choice("A^"), 1)
                elif u == lo:
                    e = ("/", 0)
                elif u == hi:
                    e = ("\\", 0)
                else:
                    e = (rng.choice("AAmm^^##%*\\/"), 0)
                edge = abs(u - 12) / hu
                if v > 0 and tf < 0.35 and rng.random() < 0.3 + 0.45 * edge:
                    e = (rng.choice("**..'~*"), 1)
                row[u] = e
        rows.append(row)
        spans.append((lo, hi))
    return rows, spans


_trng = random.Random(1234)
TREE_TEX = [_make_tree_tex(_trng) for _ in range(10)]


def make_art(lines, kindfn):
    AH = len(lines)
    AW = max(len(s) for s in lines)
    rows = []
    for y, s in enumerate(lines):
        s = s.ljust(AW)
        rows.append([None if c == " " else (c, kindfn(c, y)) for c in s])
    return rows, AW, AH


CREATURE_ART = make_art([
    r"\ | /       \ | /",
    r" \|/   ___   \|/ ",
    r"  \___(@ @)___/  ",
    r"      | ^ |      ",
    r"     /|=|=|\     ",
    r"    / |=|=| \    ",
    r"   /  |=|=|  \   ",
    r"  '   |=|=|   '  ",
    r"      | | |      ",
    r"      |   |      ",
    r"      |   |      ",
    r"     /     \     ",
], lambda c, y: 2 if c == "@" else (1 if y < 3 else 0))
# kinds: 0 body, 1 antler/bone, 2 eye (glows)

STICKS_ART = make_art([
    r" \ / ",
    r"=#=#=",
], lambda c, y: 0)
LOG_ART = make_art([
    r"(====#===)",
], lambda c, y: 1 if c in "()" else 0)
PIT_ART = make_art([
    r"  _.-=#=-._  ",
    r"(o0O()()()O0o)",
], lambda c, y: 1 if c in "o0O()" else 0)

LANDMARKS = [
    ("tent", [
        r"      /\      ",
        r"     /  \     ",
        r"    / /\ \    ",
        r"   / /  \ \   ",
        r"  / /    \ \  ",
        r" /_/______\_\ ",
    ], 2.4, 1.6,
     "Day 3. The snow won't stop and the wood is running out. Mark swears "
     "he saw someone standing between the trees, watching the camp. Tall. "
     "Too tall. He went to get more wood an hour ago."),
    ("sign", [
        r" ___________ ",
        r"|  PINE     |",
        r"|  HOLLOW ->|",
        r"|__ 4 mi ___|",
        r"    |   |    ",
        r"    |   |    ",
        r"    |   |    ",
    ], 1.6, 2.0,
     "It doesn't move while you look at it. I stared until my eyes burned. "
     "Then I blinked, and it was closer."),
    ("car", [
        r"      _________        ",
        r"   __/__|___|__\___    ",
        r"  |   _       _    |   ",
        r"  '--(_)-----(_)---'   ",
    ], 3.2, 1.2,
     "The light keeps it back. It walks the edge of the firelight all night, "
     "round and round, waiting for the fire to get small. Feed the fire. "
     "Never let it die."),
    ("stump", [
        r"   .-''''-.   ",
        r"  /  .--.  \  ",
        r"  | DONT   |  ",
        r"  |  LOOK  |  ",
        r"  |  AWAY  |  ",
        r"  |________|  ",
    ], 1.4, 1.1,
     "If you can see it, it can see you. If you can't see it... "
     "turn around."),
    ("shed", [
        r"    ________    ",
        r"   /        \   ",
        r"  /__________\  ",
        r"  |  _   []  |  ",
        r"  | | |      |  ",
        r"  |_|_|______|  ",
    ], 2.6, 2.2,
     "It leaves at dawn. Six o'clock. The cold kills slower than it does, "
     "but it kills. Stay near the fire, keep it burning, and make it to six."),
]
LANDMARK_ARTS = [make_art(lines, lambda c, y: 1 if c.isalpha() or c in "->" else 0)
                 for _, lines, _, _, _ in LANDMARKS]

FACE = [
    r"  \ \ | / /                    \ \ | / /  ",
    r"   \ \|/ /                      \ \|/ /   ",
    r"    \ | /      ..........        \ | /    ",
    r"     \|/  .:::::::::::::::::.     \|/     ",
    r"      |.:::::::::::::::::::::::. |        ",
    r"     .::::::'   ____    ____  '::::::.    ",
    r"    :::::'     /####\  /####\    ':::::   ",
    r"   :::::      | (@@) || (@@) |     :::::  ",
    r"   :::::       \####/  \####/      :::::  ",
    r"    ::::.         '-.  .-'        .::::   ",
    r"     ':::.          |\/|        .:::'     ",
    r"       ':::.   .vVvVvVvVvVv.  .:::'       ",
    r"         ':::  |^^^^^^^^^^^| :::'         ",
    r"           '::. '^^^^^^^^^' .::'          ",
    r"              ''::::::::::''              ",
]

FONT = {
    "W": ["#   #", "#   #", "# # #", "## ##", "#   #"],
    "I": ["###", " # ", " # ", " # ", "###"],
    "N": ["#   #", "##  #", "# # #", "#  ##", "#   #"],
    "T": ["#####", "  #  ", "  #  ", "  #  ", "  #  "],
    "E": ["#####", "#    ", "#### ", "#    ", "#####"],
    "R": ["#### ", "#   #", "#### ", "#  # ", "#   #"],
    "F": ["#####", "#    ", "#### ", "#    ", "#    "],
    " ": ["   "] * 5,
}

GROUND_CHARS = "_-=~.,_-_--="
_grng = random.Random(77)
GROUND = [(_grng.choice(GROUND_CHARS), _grng.randrange(4)) if _grng.random() < 0.45 else None
          for _ in range(1024)]
GROUND_BASE = [(66, 72, 84), (88, 96, 112), (110, 120, 140), (130, 140, 160),
               (50, 55, 70), (95, 40, 35)]          # 4 = your footprints, 5 = its footprints

SCARES = [
    "A branch snaps somewhere behind you.",
    "You hear breathing. It isn't yours.",
    "The wind sounds like someone saying your name.",
    "Something heavy moves through the snow nearby.",
    "The trees creak. Then everything goes quiet.",
    "You feel eyes on the back of your neck.",
]


def wrap_angle(x):
    return (x + math.pi) % TAU - math.pi


def wrap_text(text, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines


# ------------------------------------------------------------------ input

class Input:
    """Keyboard state for a terminal.

    Terminals that speak the kitty keyboard protocol (kitty, WezTerm, foot,
    Ghostty, Alacritty...) report key releases, so movement is exact. Others
    (Konsole, GNOME Terminal) only send key repeats, so a key counts as held
    for a moment after each press and holding it keeps it alive.
    """

    def __init__(self):
        self.until = {}
        self.precise = False
        self.pressed = []
        self.shift = 0.0

    def feed(self, events, now):
        for name, ev, shift in events:
            if not name:
                continue
            if ev == 3:
                self.precise = True
                self.until.pop(name, None)
                if name == "shift":
                    self.shift = 0.0
                continue
            if ev == 1:
                self.pressed.append(name)
            if self.precise:
                hold = 1e18
            else:
                hold = now + (0.13 if self.until.get(name, 0) > now else 0.6)
            self.until[name] = hold
            if name == "shift":
                self.shift = hold
            elif shift:
                self.shift = max(self.shift, hold if not self.precise else now + 0.2)

    def held(self, *names):
        now = time.perf_counter()
        return any(self.until.get(n, 0) > now for n in names)

    def running(self):
        return self.shift > time.perf_counter()

    def take(self):
        p, self.pressed = self.pressed, []
        return p

    def clear(self):
        self.until.clear()
        self.shift = 0.0


ARROWS = {"A": "up", "B": "down", "C": "right", "D": "left"}


def parse_keys(data):
    evs = []
    i, n = 0, len(data)
    while i < n:
        c = data[i]
        if c == "\x1b":
            if i + 1 < n and data[i + 1] == "[":
                j = i + 2
                while j < n and not ("\x40" <= data[j] <= "\x7e"):
                    j += 1
                if j >= n:
                    break
                evs.append(_csi(data[i + 2:j], data[j]))
                i = j + 1
            elif i + 2 < n and data[i + 1] == "O":
                evs.append((ARROWS.get(data[i + 2], ""), 1, False))
                i += 3
            else:
                evs.append(("esc", 1, False))
                i += 1
        else:
            if c in "\r\n":
                evs.append(("enter", 1, False))
            elif c == " ":
                evs.append(("space", 1, False))
            elif c.isalpha():
                evs.append((c.lower(), 1, c.isupper()))
            else:
                evs.append((c, 1, False))
            i += 1
    return evs


def incomplete_csi(seq):
    """True if `seq` (starting with Esc) is the start of an escape sequence that hasn't finished."""
    if len(seq) == 1:
        return True
    if seq[1] == "O":
        return len(seq) < 3
    if seq[1] != "[":
        return False
    return not any("\x40" <= c <= "\x7e" for c in seq[2:])


def _csi(params, final):
    if params.startswith(("?", ">", "<", "=")):
        return ("", 0, False)
    parts = params.split(";")
    try:
        mods_ev = parts[1].split(":") if len(parts) > 1 else ["1"]
        mods = int(mods_ev[0] or 1)
        ev = int(mods_ev[1]) if len(mods_ev) > 1 else 1
        shift = bool((mods - 1) & 1)
        if final == "u":
            code = int(parts[0].split(":")[0])
            name = {13: "enter", 27: "esc", 32: "space", 57441: "shift", 57447: "shift"}.get(code)
            if name is None:
                name = chr(code).lower() if 32 < code < 127 else ""
            return (name, ev, shift)
        if final in ARROWS:
            return (ARROWS[final], ev, shift)
    except (ValueError, IndexError):
        pass
    return ("", 0, False)


class Terminal:
    def __init__(self):
        import termios
        import tty
        self.termios = termios
        self.fd = sys.stdin.fileno()
        self.old = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)
        self.out = sys.stdout
        self.partial = ""
        self.partial_since = 0.0
        # alt screen, hide cursor, ask for key release events (ignored where unsupported)
        self.out.write("\x1b[?1049h\x1b[?25l\x1b[2J\x1b[>11u")
        self.out.flush()

    def close(self):
        self.out.write("\x1b[<u\x1b[0m\x1b[?25h\x1b[?1049l")
        self.out.flush()
        self.termios.tcsetattr(self.fd, self.termios.TCSADRAIN, self.old)

    def read(self):
        data = self.partial
        while select.select([self.fd], [], [], 0)[0]:
            chunk = os.read(self.fd, 1024).decode(errors="ignore")
            if not chunk:
                break
            data += chunk
        # an escape sequence can arrive split across reads: keep the unfinished tail for next time
        self.partial = ""
        k = data.rfind("\x1b")
        if k >= 0 and k > len(data) - 32 and incomplete_csi(data[k:]):
            if self.partial_since and time.perf_counter() - self.partial_since > 0.05:
                self.partial_since = 0.0          # a lone Esc press, not the start of a sequence
            else:
                self.partial_since = self.partial_since or time.perf_counter()
                data, self.partial = data[:k], data[k:]
        else:
            self.partial_since = 0.0
        return parse_keys(data) if data else []


# ------------------------------------------------------------------ world objects

class Tree:
    __slots__ = ("x", "y", "h", "tex", "col")


class Item:
    __slots__ = ("x", "y", "kind")

    def __init__(self, x, y, kind):
        self.x, self.y, self.kind = x, y, kind


class Game:
    def __init__(self, W, H, seed=None):
        self.rng = random.Random(seed)
        self.resize(W, H)
        self._build_world()
        self.reset()

    # ---------------------------------------------------------- setup
    def resize(self, W, H):
        self.W, self.H = W, H
        self.VH = H - 2                                   # two HUD rows at the bottom
        self.Fx = (W / 2) / math.tan(FOV / 2)
        self.Fy = self.Fx * 0.5                           # text cells are about twice as tall as wide
        self.col_off = [math.atan((x + 0.5 - W / 2) / self.Fx) for x in range(W)]
        self.flakes = []
        n = int(W * self.VH * 0.22)
        for _ in range(n):
            self.flakes.append(self._flake(False))

    def _flake(self, top=True):
        rng = self.rng
        z = rng.random()
        ch = "." if z < 0.35 else rng.choice("+*'") if z < 0.7 else rng.choice("**o")
        return [rng.uniform(-10, self.W + 10), rng.uniform(-3, 0) if top else rng.uniform(0, self.VH),
                z, rng.uniform(0, 6.28), ch]

    def _build_world(self):
        rng = self.rng
        # landmarks first so trees keep clear of them
        self.landmarks = []
        base = rng.uniform(0, TAU)
        for k, (name, lines, w, h, note) in enumerate(LANDMARKS):
            ang = base + k * TAU / len(LANDMARKS) + rng.uniform(-0.4, 0.4)
            d = rng.uniform(13, 28)
            self.landmarks.append([math.cos(ang) * d, math.sin(ang) * d, k, False])
        self.buckets = {}
        self.trees = []
        tries = 0
        while len(self.trees) < 520 and tries < 20000:
            tries += 1
            ang = rng.uniform(0, TAU)
            d = math.sqrt(rng.uniform(0, 1)) * (WORLD_R + 6)
            x, y = math.cos(ang) * d, math.sin(ang) * d
            if d < 9.5:
                continue
            if any((x - lx) ** 2 + (y - ly) ** 2 < 9 for lx, ly, _, _ in self.landmarks):
                continue
            if self.tree_hit(x, y, 2.4):
                continue
            t = Tree()
            t.x, t.y = x, y
            t.h = rng.uniform(3.0, 6.0)
            t.tex = rng.randrange(len(TREE_TEX))
            j = rng.uniform(0.8, 1.15)
            t.col = (95 * j, 150 * j, 125 * j)
            self.trees.append(t)
            self.buckets.setdefault((int(x // 4), int(y // 4)), []).append(t)

    def reset(self):
        self.px, self.py, self.a = 0.0, -3.4, math.pi / 2
        self.t = 0.0
        self.night = 0.0
        self.fuel = 60.0
        self.embers = 0.0               # >0 while the fire is out and can still be saved
        self.warmth = 100.0
        self.stamina = 100.0
        self.sticks = 0
        self.log = False
        self.state = "play"
        self.death = ""
        self.death_t = 0.0
        self.reading = None
        self.bob = 0.0
        self.walk = 0.0
        self.step_acc = 0.0
        self.prints = {}
        self.msgs = []
        self.items = []
        for _ in range(30):
            self._spawn_item("sticks")
        for _ in range(6):
            self._spawn_item("log")
        self.cr_x, self.cr_y = 30.0, 30.0
        self.cr_active = False
        self.cr_stare = 0.0
        self.cr_step = 0.0
        self.cr_seen = False
        self.cr_warned = 0.0
        self.fear_side = 0.0
        self.cr_watched = False
        self.cr_dist = 99.0
        self.fear = 0.0
        self.fake_eyes = None
        self.next_scare = 25.0
        self.next_gust = 70.0
        self.gust_until = 0.0
        self.warned = set()
        self.fw_heat = [[0.0] * 13 for _ in range(12)]
        self.flick = 1.0
        self.sparks = []
        self.wind = 0.4
        self.turned = 0.0
        self.moved = 0.0
        for lm in self.landmarks:
            lm[3] = False
        for _ in range(60):
            self._step_fire(1 / SIM_HZ)
        self.say("The fire is burning low. You'll need wood.", 6, (230, 200, 150))

    def _spawn_item(self, kind):
        rng = self.rng
        for _ in range(200):
            ang = rng.uniform(0, TAU)
            d = rng.uniform(6, WORLD_R - 4) if kind == "sticks" else rng.uniform(14, WORLD_R - 2)
            x, y = math.cos(ang) * d, math.sin(ang) * d
            if not self.tree_hit(x, y, 0.9):
                self.items.append(Item(x, y, kind))
                return

    # ---------------------------------------------------------- queries
    def tree_hit(self, x, y, r):
        bx, by = int(x // 4), int(y // 4)
        r2 = r * r
        for i in (bx - 1, bx, bx + 1):
            for j in (by - 1, by, by + 1):
                for t in self.buckets.get((i, j), ()):
                    if (t.x - x) ** 2 + (t.y - y) ** 2 < r2:
                        return True
        return False

    def blocked(self, x, y, r=0.45):
        if x * x + y * y > WORLD_R * WORLD_R:
            return True
        if x * x + y * y < 0.9 ** 2:
            return True
        if self.tree_hit(x, y, r + 0.25):
            return True
        for lx, ly, k, _ in self.landmarks:
            if (x - lx) ** 2 + (y - ly) ** 2 < (LANDMARKS[k][2] * 0.45 + r) ** 2:
                return True
        return False

    def los(self, ax, ay, bx, by, skip=0.0):
        """True if nothing blocks the view from a to b. Trees closer than `skip`
        to a are ignored (the renderer draws those see-through)."""
        d = math.hypot(bx - ax, by - ay)
        steps = int(d / 0.5)
        for s in range(1, steps):
            k = s / steps
            if k * d < skip:
                continue
            if self.tree_hit(ax + (bx - ax) * k, ay + (by - ay) * k, 0.35):
                return False
        return True

    @property
    def fuel_frac(self):
        return self.fuel / FUEL_MAX

    @property
    def light_r(self):
        if self.fuel <= 0:
            return 0.8 if self.embers > 0 else 0.0
        return 2.0 + 7.0 * self.fuel_frac

    @property
    def fire_light(self):
        if self.fuel <= 0:
            return 0.12 * (self.embers / EMBER_TIME)
        return (0.4 + 0.85 * self.fuel_frac) * (0.75 + 0.35 * self.flick)

    def ambient(self):
        dawn = max(0.0, (self.night - 0.88) / 0.12)
        return 0.75 + 0.3 * dawn

    def clock(self):
        mins = int(22 * 60 + self.night * 8 * 60) % (24 * 60)
        return "%02d:%02d" % (mins // 60, mins % 60)

    def say(self, text, dur=4.0, col=(200, 205, 215)):
        self.msgs.append([text, self.t + dur, col])
        self.msgs = self.msgs[-3:]

    # ---------------------------------------------------------- simulation
    def _step_fire(self, dt):
        rng = self.rng
        heat = self.fw_heat
        FH, FW = len(heat), len(heat[0])
        c = FW // 2
        if self.fuel > 0:
            inten = 0.45 + 0.75 * self.fuel_frac
        else:
            inten = 0.25 * (self.embers / EMBER_TIME)
        base = heat[FH - 1]
        for x in range(FW):
            e = abs(x - c) / (c + 0.5)
            v = inten * (1.25 - 0.8 * e * e) * rng.uniform(0.7, 1.0)
            if rng.random() < 0.06:
                v *= 0.4
            base[x] = max(0.0, v)
        cool = 0.55 / FH
        wind = self.wind
        for y in range(FH - 1):
            below = heat[y + 1]
            row = heat[y]
            lift = 1.0 - y / FH
            for x in range(FW):
                sx = x + rng.randint(-1, 1)
                if rng.random() < abs(wind) * 0.3:
                    sx -= 1 if wind > 0 else -1
                sx = 0 if sx < 0 else FW - 1 if sx >= FW else sx
                v = below[sx] * 0.62 + below[x] * 0.38
                e = abs(x - c) / (c + 0.5)
                v -= rng.random() * cool * (1.2 + 3.2 * e * e) * (0.5 + 0.9 * lift)
                row[x] = v if v > 0 else 0.0
        tot = sum(sum(heat[y]) for y in range(FH // 2, FH))
        target = tot / (FW * (FH - FH // 2) * 0.45 + 1e-6)
        self.flick += (target - self.flick) * 0.35
        if self.fuel > 0 and rng.random() < 0.5 * self.fuel_frac + 0.1:
            self.sparks.append([rng.uniform(-0.2, 0.2), rng.uniform(-0.2, 0.2), 0.6 + 0.8 * self.fuel_frac,
                                rng.uniform(-0.3, 0.3), rng.uniform(-0.3, 0.3), rng.uniform(0.8, 1.6),
                                0.0, rng.uniform(1.0, 2.4)])
        alive = []
        for s in self.sparks:
            s[3] += (self.wind * 0.6 + rng.uniform(-0.6, 0.6)) * dt
            s[0] += s[3] * dt
            s[1] += s[4] * dt
            s[2] += s[5] * dt
            s[5] *= 0.99
            s[6] += dt
            if s[6] < s[7]:
                alive.append(s)
        self.sparks = alive

    def tick(self, dt, inp):
        if self.state != "play":
            if self.state == "dying":
                self.death_t += dt
                if self.death_t > (1.6 if self.death == "caught" else 2.6):
                    self.state = "dead"
            self._step_snow(dt)
            return
        self.t += dt
        self.night = min(1.0, self.t / NIGHT_LEN)
        rng = self.rng
        gust = math.sin(self.t * 0.31) * 0.5 + math.sin(self.t * 0.83 + 1) * 0.3
        self.wind = 0.35 + 0.45 * gust

        # ---- movement
        fwd = self.held_axis(inp, ("w", "up"), ("s", "down"))
        strafe = self.held_axis(inp, ("d",), ("a",))
        turn = self.held_axis(inp, ("right", "l"), ("left", "j"))
        da = turn * 2.8 * dt
        self.a = (self.a + da) % TAU
        self.turned += da
        running = inp.running() and fwd > 0 and self.stamina > 5 and not self.log
        speed = 3.7 if running else 2.2
        if self.log:
            speed *= 0.65
        if self.warmth < 25:
            speed *= 0.75
        ca, sa = math.cos(self.a), math.sin(self.a)
        mx = ca * fwd - sa * strafe
        my = sa * fwd + ca * strafe
        m = math.hypot(mx, my)
        moved = 0.0
        if m > 0:
            mx, my = mx / m * speed * dt, my / m * speed * dt
            ox, oy = self.px, self.py
            if not self.blocked(self.px + mx, self.py):
                self.px += mx
            if not self.blocked(self.px, self.py + my):
                self.py += my
            moved = math.hypot(self.px - ox, self.py - oy)
            if moved < 1e-4 and math.hypot(self.px, self.py) > WORLD_R - 1:
                if "edge" not in self.warned or rng.random() < 0.01:
                    self.warned.add("edge")
                    self.say("The forest goes on forever out here. Better not.", 3)
        self.moved += moved * (1 if fwd >= 0 else -1)
        self.walk += moved * 3.2
        self.bob = math.sin(self.walk) * 0.35 if moved else self.bob * 0.8
        self.step_acc += moved
        if self.step_acc > 0.55:
            self.step_acc = 0.0
            self._print(self.px, self.py, 4)
        if running:
            self.stamina = max(0.0, self.stamina - 30 * dt)
        else:
            self.stamina = min(100.0, self.stamina + 14 * dt)

        # ---- the fire
        burn = 0.6 + 0.3 * max(0.0, gust)
        if self.fuel > 0:
            self.fuel = max(0.0, self.fuel - burn * dt)
            if self.fuel == 0:
                self.embers = EMBER_TIME
                self.say("THE FIRE HAS GONE OUT. Feed the embers, now.", 6, (255, 120, 80))
        elif self.embers > 0:
            self.embers -= dt
            if self.embers <= 0:
                self._die("dark")
                return
        for lvl, text in ((40, "The fire is getting low."), (20, "The fire is dying.")):
            if self.fuel < lvl and lvl not in self.warned and self.fuel > 0:
                self.warned.add(lvl)
                self.say(text, 4, (255, 170, 90))
            elif self.fuel > lvl + 8:
                self.warned.discard(lvl)
        # big gusts tear at the fire now and then
        if self.t > self.next_gust and self.fuel > 0:
            self.next_gust = self.t + rng.uniform(55, 110)
            self.gust_until = self.t + 2.5
            hit = rng.uniform(8, 16) * (0.6 + 0.6 * self.night)
            self.fuel = max(1.0, self.fuel - hit)
            far = math.hypot(self.px, self.py) > self.light_r
            self.say("A gust howls through the camp. The fire gutters." if not far else
                     "Wind howls through the trees. Behind you, the firelight shrinks.", 4, (255, 170, 90))
        if self.t < self.gust_until:
            self.wind = 1.6
        self._step_fire(dt)

        # ---- warmth
        pd = math.hypot(self.px, self.py)
        if pd < self.light_r * 0.75 + 0.8 and self.fuel > 0:
            self.warmth = min(100.0, self.warmth + (5 + 9 * self.fuel_frac) * dt)
        else:
            self.warmth -= (0.9 + 0.6 * self.night + 0.4 * max(0.0, gust)) * dt
            if self.warmth < 30 and "cold" not in self.warned:
                self.warned.add("cold")
                self.say("Your hands are going numb. Get back to the fire.", 4, (160, 200, 255))
            if self.warmth <= 0:
                self._die("frozen")
                return
        if self.warmth > 45:
            self.warned.discard("cold")

        # ---- wood
        for it in list(self.items):
            if (it.x - self.px) ** 2 + (it.y - self.py) ** 2 < 1.1:
                if it.kind == "sticks" and self.sticks < MAX_STICKS:
                    self.sticks += 1
                    self.items.remove(it)
                    self.say("Picked up sticks (%d/%d)." % (self.sticks, MAX_STICKS), 2.5)
                    self._spawn_item("sticks")
                elif it.kind == "log" and not self.log:
                    self.log = True
                    self.items.remove(it)
                    self.say("You heave a log onto your shoulder. It's heavy.", 3)
                    self._spawn_item("log")
        if pd < 2.2 and (self.sticks or self.log):
            add = self.sticks * STICK_FUEL + (LOG_FUEL if self.log else 0)
            if self.fuel <= 0:
                self.say("The embers catch. The fire breathes again.", 4, (255, 200, 120))
                self.embers = 0.0
            else:
                self.say("The fire roars up." if add > 25 else "The fire crackles.", 2.5, (255, 200, 120))
            self.fuel = min(FUEL_MAX, self.fuel + add)
            self.sticks, self.log = 0, False

        # ---- notes
        if self.reading is not None:
            lx, ly, k, _ = self.landmarks[self.reading]
            if (lx - self.px) ** 2 + (ly - self.py) ** 2 > 16:
                self.reading = None
        for p in inp.take():
            if p == "e":
                if self.reading is not None:
                    self.reading = None
                else:
                    for idx, (lx, ly, k, _) in enumerate(self.landmarks):
                        if (lx - self.px) ** 2 + (ly - self.py) ** 2 < 2.8 ** 2:
                            self.reading = idx
                            self.landmarks[idx][3] = True
                            break
            elif p in ("p", "esc"):
                self.state = "paused"
                inp.clear()
                return

        # ---- the thing in the trees
        self._step_creature(dt, pd)
        if self.state != "play":
            return

        # ---- atmosphere
        if self.t > self.next_scare:
            self.next_scare = self.t + rng.uniform(18, 40)
            if pd > self.light_r and self.cr_active:
                self.say(rng.choice(SCARES), 4, (190, 170, 170))
        if self.fake_eyes and self.t > self.fake_eyes[2]:
            self.fake_eyes = None
        if (not self.fake_eyes and self.cr_active and not self.cr_watched
                and rng.random() < 0.004 + 0.006 * self.night):
            ang = self.a + rng.uniform(-0.5, 0.5)
            d = rng.uniform(12, 18)
            self.fake_eyes = (self.px + math.cos(ang) * d, self.py + math.sin(ang) * d,
                              self.t + rng.uniform(0.6, 1.6))
        if int(self.t) % 5 == 0 and rng.random() < 0.05:
            cut = self.t - 110
            self.prints = {k: v for k, v in self.prints.items() if v[1] > cut}

        if self.night >= 1.0:
            self.state = "won"
        self._step_snow(dt)

    def held_axis(self, inp, plus, minus):
        return (1 if inp.held(*plus) else 0) - (1 if inp.held(*minus) else 0)

    def _print(self, x, y, kind):
        k = int(x * 2 + 4096) * 10000 + int(y * 2 + 4096)
        self.prints[k] = (kind, self.t)

    def _die(self, why):
        self.state = "dying"
        self.death = why
        self.death_t = 0.0
        self.reading = None

    def _step_creature(self, dt, pd):
        rng = self.rng
        if not self.cr_active:
            if self.t > CREATURE_WAKES:
                self.cr_active = True
                self._teleport_behind(18, 24)
            return
        dx, dy = self.px - self.cr_x, self.py - self.cr_y
        dist = math.hypot(dx, dy)
        self.cr_dist = dist
        safe = self.light_r if (self.fuel > 0 or self.embers > 0) else 0.0
        dawn = self.night > 0.93
        # is the player looking at it?
        ang_to = math.atan2(-dy, -dx)
        # anywhere on screen counts, as long as its eyes would show through the snow
        watched = (abs(wrap_angle(ang_to - self.a)) < FOV * 0.48 and dist < SIGHT
                   and self.los(self.px, self.py, self.cr_x, self.cr_y, skip=2.4))
        self.cr_watched = watched
        if watched and not self.cr_seen:
            self.cr_seen = True
            self.say("...what is that?", 3, (200, 170, 170))
        speed = 0.9 + 1.7 * self.night
        if self.fuel <= 0:
            speed *= 1.4
        if dawn:
            tx, ty = self.cr_x - dx, self.cr_y - dy
            speed = 2.0
        elif pd < safe + 0.3:
            # player is in the light: pace the edge of it, watching
            orbit = math.atan2(self.cr_y, self.cr_x) + 0.25
            r = safe + 2.5
            tx, ty = math.cos(orbit) * r, math.sin(orbit) * r
            speed = 1.3
            self.cr_stare = 0.0
        else:
            if watched:
                speed = 0.0
                self.cr_stare += dt
                if self.cr_stare > 2.6 and dist > 4.5:
                    self._teleport_behind(9, 14)
                    self.cr_stare = 0.0
                    return
            else:
                self.cr_stare = max(0.0, self.cr_stare - dt)
            tx, ty = self.px, self.py
            if not watched and dist < 6:
                speed *= 0.5 + 0.5 * dist / 6          # it creeps the last few steps
                rel = abs(wrap_angle(ang_to - self.a))
                if dist < 4.5 and rel > FOV / 2 and self.t > self.cr_warned:
                    self.cr_warned = self.t + 10
                    self.say("Something is right behind you.", 2.5, (230, 120, 110))
            if dist > 26:
                self._teleport_behind(14, 20)
                return
        # move toward target, sliding around trees, never into the light
        vx, vy = tx - self.cr_x, ty - self.cr_y
        vd = math.hypot(vx, vy)
        if speed > 0 and vd > 0.05:
            step = min(vd, speed * dt)
            base_ang = math.atan2(vy, vx)
            for off in (0, 0.6, -0.6, 1.2, -1.2):
                nx = self.cr_x + math.cos(base_ang + off) * step
                ny = self.cr_y + math.sin(base_ang + off) * step
                if not self.tree_hit(nx, ny, 0.45):
                    self.cr_x, self.cr_y = nx, ny
                    self.cr_step += step
                    break
        fr = math.hypot(self.cr_x, self.cr_y)
        if safe > 0 and fr < safe + 1.0 and not dawn:
            k = (safe + 1.0) / max(fr, 0.01)
            self.cr_x *= k
            self.cr_y *= k
        if self.cr_step > 0.8:
            self.cr_step = 0.0
            self._print(self.cr_x, self.cr_y, 5)
        dist = math.hypot(self.px - self.cr_x, self.py - self.cr_y)
        self.cr_dist = dist
        self.fear = 0.0 if dawn else max(0.0, min(1.0, (10.0 - dist) / 8.0))
        self.fear_side = wrap_angle(math.atan2(self.cr_y - self.py, self.cr_x - self.px) - self.a)
        if dist < 0.95 and not dawn:
            self._die("caught")

    def _teleport_behind(self, d0, d1):
        rng = self.rng
        safe = self.light_r
        for _ in range(40):
            ang = self.a + math.pi + rng.uniform(-0.9, 0.9)
            d = rng.uniform(d0, d1)
            x, y = self.px + math.cos(ang) * d, self.py + math.sin(ang) * d
            if math.hypot(x, y) < WORLD_R + 4 and math.hypot(x, y) > safe + 1.5 and not self.tree_hit(x, y, 0.5):
                self.cr_x, self.cr_y = x, y
                return

    def _step_snow(self, dt):
        W, VH = self.W, self.VH
        k = dt * SIM_HZ
        turn_px = self.turned * self.Fx
        mv = self.moved
        self.turned = 0.0
        self.moved = 0.0
        hz = VH * 0.42
        wind = self.wind
        t = self.t
        for i, f in enumerate(self.flakes):
            x, y, z, ph, c = f
            y += (0.07 + 0.38 * z) * k
            x += (wind * (0.15 + 0.7 * z) + math.sin(t * 1.3 + ph) * 0.12 * (0.3 + z)) * k
            x -= turn_px * (0.6 + 0.8 * z)
            if mv:
                x += (x - W / 2) * mv * 0.05 * z
                y += (y - hz) * mv * 0.05 * z
            if x < -10:
                x += W + 20
            elif x > W + 10:
                x -= W + 20
            if y >= VH or y < -4:
                self.flakes[i] = self._flake()
            else:
                f[0], f[1] = x, y

    # ---------------------------------------------------------- rendering
    def render(self):
        W, H, VH = self.W, self.H, self.VH
        ch = [" "] * (W * H)
        key = [0] * (W * H)
        px, py, a = self.px, self.py, self.a
        ca, sa = math.cos(a), math.sin(a)
        Fx, Fy = self.Fx, self.Fy
        hz = VH * 0.42 + self.bob
        amb = self.ambient()
        FL = self.fire_light
        lr = self.light_r
        r2 = max(0.3, lr * 0.85) ** 2
        t = self.t
        dawn = max(0.0, (self.night - 0.88) / 0.12)

        # ---- sky: stars and moon anchored to directions in the world
        rng = random.Random(9)
        top = int(hz)
        for s in range(int(W * hz * 0.03) + 1):
            sa_ = rng.uniform(0, TAU)
            srow = rng.uniform(0, 0.92)
            c = rng.choice("..........''`+*")
            b = rng.uniform(60, 190) * (1 - dawn) * 0.7
            d = wrap_angle(sa_ - a)
            if abs(d) < FOV / 2:
                x = int(W / 2 + math.tan(d) * Fx)
                y = int(srow * hz)
                if 0 <= x < W and 0 <= y < VH:
                    k = b * (0.55 + 0.45 * math.sin(t * rng.uniform(0.5, 3) + s))
                    ch[y * W + x] = c
                    key[y * W + x] = pk(k * 0.9, k * 0.92, k)
        md = wrap_angle(0.9 - a)
        if abs(md) < FOV / 2 + 0.3:
            mx, my = W / 2 + math.tan(md) * Fx, max(2, hz * 0.28)
            R = max(2.0, VH * 0.06)
            for y in range(int(my - R - 1), int(my + R + 2)):
                for x in range(int(mx - 2 * R - 2), int(mx + 2 * R + 3)):
                    if 0 <= x < W and 0 <= y < VH:
                        d = math.hypot((x - mx) / 2.0, y - my)
                        if d <= R:
                            crater = math.sin(x * 1.7 + a * 50) * math.cos(y * 2.3) > 0.4
                            b = (200 if crater else 225) * (1 - 0.6 * dawn)
                            ch[y * W + x] = "0" if d > R - 0.8 else ("O" if crater else "@")
                            key[y * W + x] = pk(b, b, b * 0.9)
        if dawn > 0:
            for y in range(max(0, top - 4), min(VH, top + 1)):
                for x in range(0, W, 2):
                    if (x * 7 + y * 13) % 5 == 0:
                        b = dawn * 90 * (1 - (top - y) / 5)
                        ch[y * W + x] = "_" if y == top else "-"
                        key[y * W + x] = pk(b * 1.1, b * 0.8, b * 0.9)

        # ---- ground: every cell is a patch of snow at a real place in the world
        prints = self.prints
        cache = {}
        x0 = (0.5 - W / 2) / Fx
        dcam = 1.0 / Fx
        for y in range(max(0, int(hz) + 1), VH):
            dy = y + 0.5 - hz
            z = EYE * Fy / dy
            f = math.exp(-z / FOG)
            fq = int(f * 48)
            rk = cache.get(fq)
            if rk is None:
                fv = fq / 48.0 + 0.01
                rk = [[lit(GROUND_BASE[lv], amb, lq / 12.0, fv) for lq in range(16)] for lv in range(6)]
                cache[fq] = rk
            cs = 2.0 ** round(math.log2(max(0.04, z * dcam * 1.3)))
            inv = 1.0 / cs
            salt = int(cs * 64) * 2654435761
            wx0 = px + z * (ca - sa * x0)
            wy0 = py + z * (sa + ca * x0)
            sx, sy = -z * sa * dcam, z * ca * dcam
            base = y * W
            for x in range(W):
                wx = wx0 + sx * x
                wy = wy0 + sy * x
                ix = int(wx * inv + 65536)
                iy = int(wy * inv + 65536)
                g = GROUND[((ix * 73856093) ^ (iy * 19349663) ^ salt) & 1023]
                if g is None:
                    continue
                c, lv = g
                if prints and z < 9:
                    fp = prints.get(int(wx * 2 + 4096) * 10000 + int(wy * 2 + 4096))
                    if fp is not None:
                        lv = fp[0]
                        c = '"' if lv == 4 else "v"
                d2 = wx * wx + wy * wy
                q = d2 / r2
                lq = int(FL * 12 / (1 + q + q * q))
                ch[base + x] = c
                key[base + x] = rk[lv][15 if lq > 15 else lq]

        # ---- collect sprites
        spr = []
        zmax = FOG * 2.0
        for tr in self.trees:
            dx, dy = tr.x - px, tr.y - py
            z = dx * ca + dy * sa
            if z < 0.35 or z > zmax:
                continue
            side = -dx * sa + dy * ca
            hw = tr.h * 0.24 / z * Fx
            sx = W / 2 + side / z * Fx
            if sx + hw < 0 or sx - hw > W:
                continue
            spr.append((z, 0, tr, sx))

        def add(objx, objy, kind, obj):
            dx, dy = objx - px, objy - py
            z = dx * ca + dy * sa
            if z < 0.3 or z > zmax:
                return
            side = -dx * sa + dy * ca
            spr.append((z, kind, obj, W / 2 + side / z * Fx))

        add(0.0, 0.0, 1, None)                                 # fire pit
        for it in self.items:
            add(it.x, it.y, 2, it)
        for lm in self.landmarks:
            add(lm[0], lm[1], 3, lm)
        if self.cr_active:
            add(self.cr_x, self.cr_y, 4, None)
        if self.fake_eyes:
            add(self.fake_eyes[0], self.fake_eyes[1], 5, None)
        for s in self.sparks:
            add(s[0], s[1], 6, s)
        spr.sort(key=lambda s: -s[0])

        for z, kind, obj, sx in spr:
            f = math.exp(-z / FOG)
            bot = hz + EYE / z * Fy
            if kind == 0:
                L = FL * self._fall((obj.x * obj.x + obj.y * obj.y) / r2)
                f = math.exp(-z / (FOG * 1.6))         # silhouettes carry further than snow
                keys = (lit(obj.col, amb, L, f), lit((185, 195, 210), amb, L, f), lit((80, 60, 42), amb, L, f))
                dens = max(2, int(16 * (z - 0.5) / 1.9)) if z < 2.4 else max(6, min(16, int(16 * 2.2 * f)))
                self._draw_tree(ch, key, obj, sx, bot - obj.h / z * Fy, bot, obj.h * 0.24 / z * Fx, keys, dens)
            elif kind == 1:
                L = FL * 1.2
                keys = (lit((105, 62, 32), amb, L, f), lit((110, 108, 104), amb, L, f))
                self._draw_art(ch, key, PIT_ART, sx, bot, 1.7 / z * Fx * 0.5, 0.45 / z * Fy, keys)
                self._draw_flames(ch, key, sx, bot - 0.15 / z * Fy, z, f)
            elif kind == 2:
                L = FL * self._fall((obj.x * obj.x + obj.y * obj.y) / r2)
                if obj.kind == "sticks":
                    keys = (lit((120, 80, 45), amb + 0.15, L, f),)
                    self._draw_art(ch, key, STICKS_ART, sx, bot, 0.35 / z * Fx, 0.35 / z * Fy, keys)
                else:
                    keys = (lit((115, 72, 40), amb + 0.15, L, f), lit((150, 120, 90), amb + 0.15, L, f))
                    self._draw_art(ch, key, LOG_ART, sx, bot, 0.75 / z * Fx, 0.25 / z * Fy, keys)
            elif kind == 3:
                lx, ly, k, read = obj
                name, lines, w, h, _ = LANDMARKS[k]
                L = FL * self._fall((lx * lx + ly * ly) / r2)
                keys = (lit((120, 125, 135), amb, L, f), lit((200, 190, 160), amb, L, f))
                self._draw_art(ch, key, LANDMARK_ARTS[k], sx, bot, w / 2 / z * Fx, h / z * Fy, keys)
            elif kind == 4:
                L = FL * self._fall((self.cr_x ** 2 + self.cr_y ** 2) / r2)
                ef = f ** 0.3
                keys = (lit((72, 66, 62), amb * 0.8, L, f), lit((170, 160, 140), amb * 0.8, L, f),
                        pk(255 * ef, 50 * ef, 30 * ef))
                hgt = 3.3
                self._draw_art(ch, key, CREATURE_ART, sx, bot, 0.75 / z * Fx, hgt / z * Fy, keys)
                self._draw_eyes(ch, key, sx, bot - (hgt - 0.6) / z * Fy, z, ef)
            elif kind == 5:
                ef = f ** 0.3
                self._draw_eyes(ch, key, sx, bot - 2.7 / z * Fy, z, ef * 0.8)
            elif kind == 6:
                s = obj
                y = int(hz + (EYE - s[2]) / z * Fy)
                x = int(sx)
                if 0 <= x < W and 0 <= y < VH:
                    k = 1 - s[6] / s[7]
                    ch[y * W + x] = "*" if k > 0.7 else "+" if k > 0.4 else "."
                    key[y * W + x] = pk(*lerp_color(SPARK_COLORS, k))

        # ---- falling snow
        for x, y, z, ph, c in self.flakes:
            xi, yi = int(x), int(y)
            if 0 <= xi < W and 0 <= yi < VH:
                s = (105 + 140 * z) * (0.75 + 0.25 * amb)
                ch[yi * W + xi] = c
                key[yi * W + xi] = pk(s, s, s * 1.05)

        # ---- fear: the edges of your vision crawl
        if self.fear > 0.05 and self.state == "play":
            rngf = self.rng
            n = int(self.fear * self.fear * W * VH * 0.05)
            for _ in range(n):
                e = rngf.random() ** 2 * (0.12 + 0.18 * self.fear)
                # heavier on the side it's coming from (left, right, or both when behind)
                fs = self.fear_side
                if abs(fs) > 2.3:
                    side = rngf.choice((0, 1, 3))
                elif rngf.random() < 0.75:
                    side = 1 if fs > 0 else 0
                else:
                    side = rngf.randrange(4)
                if side < 2:
                    x = int(e * W) if side == 0 else W - 1 - int(e * W)
                    y = rngf.randrange(VH)
                else:
                    x = rngf.randrange(W)
                    y = int(e * VH) if side == 2 else VH - 1 - int(e * VH)
                i = y * W + x
                ch[i] = rngf.choice("#%&@*;:")
                key[i] = pk(70 + 120 * self.fear * rngf.random(), 0, 0)
        # ---- cold: frost creeps in from the edges
        if self.warmth < 35 or (self.state == "dying" and self.death == "frozen"):
            amt = (35 - max(0.0, self.warmth)) / 35 * 0.25
            if self.state == "dying" and self.death == "frozen":
                amt = 0.25 + self.death_t / 2.6 * 0.6
            self._frost(ch, key, amt)

        self._draw_hud(ch, key)
        if self.reading is not None:
            self._draw_note(ch, key)
        return ch, key

    @staticmethod
    def _fall(q):
        return 1.0 / (1.0 + q + q * q)

    def _draw_tree(self, ch, key, tr, sx, top, bot, hw, keys, dens=16):
        W, VH = self.W, self.VH
        rows, spans = TREE_TEX[tr.tex]
        hh = bot - top
        if hh < 1 or hw < 0.3:
            return
        y0 = max(0, int(top))
        y1 = min(VH, int(bot) + 1)
        su = 12.0 / hw
        for y in range(y0, y1):
            v = int((y + 0.5 - top) / hh * TV)
            if v < 0 or v >= TV:
                continue
            trow = rows[v]
            lo, hi = spans[v]
            xa = max(0, int(sx + (lo - 12.5) / su))
            xb = min(W, int(sx + (hi - 11.5) / su) + 1)
            base = y * W
            for x in range(xa, xb):
                if dens < 16 and ((x * 7 + y * 13) & 15) >= dens:
                    continue
                u = int((x + 0.5 - sx) * su + 12.5)
                if 0 <= u < TU:
                    e = trow[u]
                    if e is not None:
                        ch[base + x] = e[0]
                        key[base + x] = keys[e[1]]

    def _draw_art(self, ch, key, art, sx, bot, hw, hgt, keys):
        rows, AW, AH = art
        W, VH = self.W, self.VH
        if hgt < 0.6 or hw < 0.3:
            # too far away to make out: a single smudge
            x, y = int(sx), int(bot - 0.5)
            if 0 <= x < W and 0 <= y < VH and hgt > 0.2:
                ch[y * W + x] = "."
                key[y * W + x] = keys[0]
            return
        top = bot - hgt
        y0 = max(0, int(top))
        y1 = min(VH, int(bot) + 1)
        xa = max(0, int(sx - hw))
        xb = min(W, int(sx + hw) + 1)
        for y in range(y0, y1):
            v = int((y + 0.5 - top) / hgt * AH)
            if v < 0 or v >= AH:
                continue
            arow = rows[v]
            base = y * W
            for x in range(xa, xb):
                u = int((x + 0.5 - (sx - hw)) / (2 * hw) * AW)
                if 0 <= u < AW:
                    e = arow[u]
                    if e is not None:
                        ch[base + x] = e[0]
                        key[base + x] = keys[min(e[1], len(keys) - 1)]

    def _draw_eyes(self, ch, key, sx, ey, z, ef):
        W, VH = self.W, self.VH
        sep = max(1, int(0.22 / z * self.Fx))
        y = int(ey)
        if not 0 <= y < VH:
            return
        c = "@" if z < 4 else "*" if z < 9 else "."
        for x in (int(sx) - sep, int(sx) + sep):
            if 0 <= x < W:
                ch[y * W + x] = c
                key[y * W + x] = pk(255 * ef, 40 * ef, 25 * ef)

    def _draw_flames(self, ch, key, sx, bot, z, f):
        heat = self.fw_heat
        FH, FW = len(heat), len(heat[0])
        W, VH = self.W, self.VH
        hgt = (0.5 + 1.4 * self.fuel_frac) / z * self.Fy if self.fuel > 0 else 0.35 / z * self.Fy
        hw = 0.7 / z * self.Fx
        if hgt < 1:
            return
        ef = f ** 0.15
        nfc = len(FIRE_CHARS) - 1
        top = bot - hgt
        for y in range(max(0, int(top)), min(VH, int(bot) + 1)):
            v = int((y + 0.5 - top) / hgt * FH)
            if not 0 <= v < FH:
                continue
            row = heat[v]
            base = y * W
            for x in range(max(0, int(sx - hw)), min(W, int(sx + hw) + 1)):
                u = int((x + 0.5 - (sx - hw)) / (2 * hw) * FW)
                if not 0 <= u < FW:
                    continue
                hv = row[u]
                if hv < 0.08:
                    continue
                hv = 1.0 if hv > 1.0 else hv
                r, g, b = lerp_color(FIRE_COLORS, hv)
                ch[base + x] = FIRE_CHARS[max(1, int(hv * nfc + 0.5))]
                key[base + x] = pk(r * ef, g * ef, b * ef)

    def _frost(self, ch, key, amt):
        W, VH = self.W, self.VH
        rng = random.Random(int(self.t * 2))
        for y in range(VH):
            ey = min(y, VH - 1 - y) / VH
            for x in range(W):
                e = min(ey, min(x, W - 1 - x) / W * 0.6)
                if e < amt * (0.6 + 0.4 * ((x * 31 + y * 17) % 7) / 7):
                    i = y * W + x
                    ch[i] = "*+.'#"[(x * 7 + y * 3) % 5]
                    b = 150 + 80 * ((x + y) % 3) / 2
                    key[i] = pk(b * 0.85, b * 0.95, b)

    def _put(self, ch, key, x, y, text, k):
        W = self.W
        if not 0 <= y < self.H:
            return
        for j, c in enumerate(text):
            if 0 <= x + j < W:
                ch[y * W + x + j] = c
                key[y * W + x + j] = k

    def _draw_hud(self, ch, key):
        W, VH = self.W, self.VH
        # messages just above the HUD
        live = [m for m in self.msgs if m[1] > self.t]
        for n, (text, end, col) in enumerate(reversed(live[-2:])):
            fade = min(1.0, (end - self.t) / 1.0)
            self._put(ch, key, (W - len(text)) // 2, VH - 2 - n, text, pk(*(c * fade for c in col)))
        # compass
        cw = min(61, W - 2) | 1
        cx0 = (W - cw) // 2
        y = VH
        half = cw // 2
        step = 180.0 / cw
        for i in range(cw):
            deg = (math.degrees(self.a + (i - half) / half * math.pi / 2) + 90) % 360
            c, k = "-", pk(70, 75, 85)
            for d, lab in ((0, "N"), (90, "E"), (180, "S"), (270, "W")):
                dd = abs((deg - d + 180) % 360 - 180)
                if dd < step / 2:
                    c, k = lab, pk(190, 195, 210)
                    break
            else:
                if abs((deg + 7.5) % 15 - 7.5) < step / 2:
                    c = "|"
            self._put(ch, key, cx0 + i, y, c, k)
        fa = wrap_angle(math.atan2(-self.py, -self.px) - self.a)
        fk = pk(255, 150, 40) if self.fuel > 0 else pk(140, 50, 20)
        if abs(fa) < math.pi / 2:
            self._put(ch, key, cx0 + half + int(fa / (math.pi / 2) * half), y, "&", fk)
        else:
            self._put(ch, key, cx0 - 2 if fa < 0 else cx0 + cw + 1, y, "<" if fa < 0 else ">", fk)
        # stats
        y = VH + 1

        def bar(v, n=10):
            f = int(round(max(0.0, min(1.0, v)) * n))
            return "#" * f + "-" * (n - f)
        warm_c = pk(120 + 1.3 * self.warmth, 140 + 0.9 * self.warmth, 230 - 0.7 * self.warmth)
        parts = [
            (" WARMTH [%s]" % bar(self.warmth / 100), warm_c),
            ("  FIRE [%s]" % bar(self.fuel_frac) if self.fuel > 0 else
             "  FIRE [EMBERS %4.1fs]" % max(0.0, self.embers), pk(255, 160, 60) if self.fuel > 20 else pk(255, 80, 50)),
            ("  STICKS %d/%d" % (self.sticks, MAX_STICKS), pk(190, 150, 110)),
            ("  LOG" if self.log else "  ---", pk(190, 150, 110) if self.log else pk(70, 70, 75)),
            ("  RUN [%s]" % bar(self.stamina / 100, 5), pk(150, 160, 170)),
            ("   %s" % self.clock(), pk(210, 210, 225)),
        ]
        near = None
        for idx, (lx, ly, k, read) in enumerate(self.landmarks):
            if (lx - self.px) ** 2 + (ly - self.py) ** 2 < 2.8 ** 2:
                near = idx
        if near is not None and self.reading is None:
            parts.append(("   [E] read the note", pk(230, 220, 170)))
        x = 0
        for text, k in parts:
            self._put(ch, key, x, y, text, k)
            x += len(text)

    def _draw_note(self, ch, key):
        W, VH = self.W, self.VH
        k = self.landmarks[self.reading][2]
        lines = wrap_text(LANDMARKS[k][4], min(48, W - 8))
        bw = max(len(s) for s in lines) + 6
        bh = len(lines) + 4
        x0, y0 = (W - bw) // 2, max(0, (VH - bh) // 2 - 2)
        paper, ink = pk(150, 140, 115), pk(225, 215, 185)
        for y in range(bh):
            for x in range(bw):
                X, Y = x0 + x, y0 + y
                if 0 <= X < W and 0 <= Y < VH:
                    i = Y * W + X
                    if y in (0, bh - 1):
                        ch[i] = "~" if 0 < x < bw - 1 else "+"
                    elif x in (0, bw - 1):
                        ch[i] = "|"
                    else:
                        ch[i] = " "
                    key[i] = paper
        for n, s in enumerate(lines):
            self._put(ch, key, x0 + 3, y0 + 2 + n, s, ink)
        self._put(ch, key, x0 + bw - 12, y0 + bh - 1, " [E] close ", paper)

    # ---------------------------------------------------------- end screens
    def render_end(self):
        W, H = self.W, self.H
        if self.state == "dying" and self.death == "caught":
            return self._jumpscare()
        if self.state == "dying":
            return self.render()
        ch = [" "] * (W * H)
        key = [0] * (W * H)
        rng = random.Random(int(time.perf_counter() * 8))
        if self.state == "won":
            title = "DAWN"
            lines = ["Grey light comes up through the trees.",
                     "Whatever was out there is gone. The fire is still burning.",
                     "", "You survived the night."]
            col = pk(230, 215, 190)
            for _ in range(int(W * H * 0.03)):
                i = rng.randrange(W * H)
                ch[i] = rng.choice(".'*")
                key[i] = pk(120, 120, 130)
        else:
            title = {"caught": "IT FOUND YOU", "frozen": "THE COLD TOOK YOU",
                     "dark": "THE FIRE WENT OUT"}[self.death]
            lines = {"caught": ["You looked away for too long."],
                     "frozen": ["You strayed too far from the fire for too long."],
                     "dark": ["The last ember faded. In the dark, something came closer.",
                              "It didn't hurry. It didn't need to."]}[self.death]
            lines = lines + ["", "You lasted until %s." % self.clock()]
            col = pk(220, 60, 45)
            for _ in range(int(W * H * 0.04)):
                i = rng.randrange(W * H)
                ch[i] = rng.choice(".:;'`")
                key[i] = pk(60 + rng.random() * 40, 0, 0)
        cy = H // 2 - 4
        self._put(ch, key, (W - len(title)) // 2, cy, title, col)
        for n, s in enumerate(lines):
            self._put(ch, key, (W - len(s)) // 2, cy + 2 + n, s, pk(190, 190, 200))
        opts = "[R] try again      [Q] quit"
        self._put(ch, key, (W - len(opts)) // 2, cy + 4 + len(lines), opts, pk(150, 150, 160))
        return ch, key

    def _jumpscare(self):
        W, H = self.W, self.H
        ch = [" "] * (W * H)
        key = [0] * (W * H)
        rng = random.Random(int(time.perf_counter() * 30))
        AH, AW = len(FACE), max(len(s) for s in FACE)
        sc = min(W / AW, H / AH) * (1.0 + 0.25 * min(1.0, self.death_t * 2))
        ox = (W - AW * sc) / 2 + rng.randint(-2, 2)
        oy = (H - AH * sc) / 2 + rng.randint(-1, 1)
        flash = int(self.death_t * 12) % 2
        for y in range(H):
            v = int((y - oy) / sc)
            if not 0 <= v < AH:
                continue
            row = FACE[v]
            for x in range(W):
                u = int((x - ox) / sc)
                if 0 <= u < len(row) and row[u] != " ":
                    c = row[u]
                    i = y * W + x
                    ch[i] = c
                    if c == "@":
                        key[i] = pk(255, 255 if flash else 40, 220 if flash else 30)
                    elif c in "vV^|\\/":
                        key[i] = pk(220, 210, 190)
                    else:
                        key[i] = pk(150 + 100 * flash, 30, 25)
        for _ in range(int(W * H * 0.08)):
            i = rng.randrange(W * H)
            ch[i] = rng.choice("#%&@*")
            key[i] = pk(120 + rng.random() * 135, 0, 0)
        return ch, key


# ------------------------------------------------------------------ title & pause

def title_frame(scene, W, H, blink):
    chs, R, G, B = scene.render(True, True)
    ch = list(chs)
    key = [pk(R[i], G[i], B[i]) if chs[i] != " " else 0 for i in range(len(chs))]
    ch += [" "] * W
    key += [0] * W
    word = "WINTER FIRE"
    width = sum(len(FONT[c][0]) + 1 for c in word)
    rows = [""] * 5
    for c in word:
        for r in range(5):
            rows[r] += FONT[c][r] + " "
    x0 = (W - width) // 2
    y0 = max(1, H // 6)

    def put(x, y, text, k, skip_space=False):
        for j, c in enumerate(text):
            if 0 <= x + j < W and 0 <= y < H and not (skip_space and c == " "):
                ch[y * W + x + j] = c
                key[y * W + x + j] = k

    # dark band behind the title text so it reads over the trees
    bx0, bx1 = max(0, min(x0, (W - 64) // 2) - 3), min(W, max(x0 + width, (W + 64) // 2) + 3)
    for y in range(max(0, y0 - 1), min(H, y0 + 15)):
        for x in range(bx0, bx1):
            ch[y * W + x] = " "
    if width < W:
        for r in range(5):
            put(x0 + 1, y0 + r + 1, rows[r].replace("#", ":"), pk(40, 45, 60), True)
            put(x0, y0 + r, rows[r], pk(225, 232, 245), True)
    else:
        put((W - len(word)) // 2, y0, word, pk(225, 232, 245))
    sub = "keep the fire alive until dawn"
    put((W - len(sub)) // 2, y0 + 7, sub, pk(230, 170, 110))
    help_lines = [
        "W/S walk    A/D step    <- -> or J/L turn    SHIFT+W run",
        "E read notes    P pause    walk into the firelight to feed it",
    ]
    for n, s in enumerate(help_lines):
        put((W - len(s)) // 2, y0 + 9 + n, s, pk(150, 155, 165))
    if blink:
        s = "[ press ENTER ]"
        put((W - len(s)) // 2, y0 + 12, s, pk(230, 230, 240))
    s = "[Q] quit"
    put((W - len(s)) // 2, y0 + 13, s, pk(110, 110, 120))
    return ch, key


def pause_overlay(game, ch, key):
    W, H = game.W, game.H
    lines = ["PAUSED", "", "[P] resume     [R] restart     [Q] quit"]
    bw = max(len(s) for s in lines) + 8
    x0, y0 = (W - bw) // 2, H // 2 - 3
    for y in range(len(lines) + 4):
        for x in range(bw):
            i = (y0 + y) * W + x0 + x
            if 0 <= i < len(ch):
                ch[i] = " "
    for n, s in enumerate(lines):
        game._put(ch, key, (W - len(s)) // 2, y0 + 2 + n, s, pk(210, 210, 220))


# ------------------------------------------------------------------ main loop

def main():
    import argparse
    ap = argparse.ArgumentParser(description="Winter Fire - keep the fire alive until dawn.")
    ap.add_argument("--seed", type=int, default=None, help="forest seed")
    ap.add_argument("--night", type=float, default=NIGHT_LEN / 60, help="night length in minutes (default 7)")
    args = ap.parse_args()
    globals()["NIGHT_LEN"] = max(1.0, args.night * 60)

    term = Terminal()
    inp = Input()
    state = "title"
    game = None
    scene = None
    size = None
    try:
        acc = 0.0
        last = time.perf_counter()
        while True:
            cols, rows = shutil.get_terminal_size((100, 30))
            if (cols, rows) != size:
                size = (cols, rows)
                scene = TitleScene(cols, rows - 1, args.seed, 3)
                for _ in range(60):
                    scene.tick(1.0, 0.3)
                if game:
                    game.resize(cols, rows)
                term.out.write("\x1b[2J")

            now = time.perf_counter()
            inp.feed(term.read(), now)
            dt = min(0.25, now - last)
            last = now

            if cols < 60 or rows < 20:
                inp.take()
                msg = "Make the terminal bigger (at least 60x20). Fullscreen is best."
                term.out.write("\x1b[H\x1b[2J\x1b[0m" + msg[:cols])
                term.out.flush()
                time.sleep(0.2)
                continue

            if state == "title":
                for p in inp.take():
                    if p == "q":
                        return
                    if p in ("enter", "space"):
                        game = Game(cols, rows, args.seed)
                        inp.clear()
                        state = "game"
                        acc = 0.0
                if state == "title":
                    acc += dt * 0.6
                    while acc >= 1 / SIM_HZ:
                        scene.tick(1.0, 0.3)
                        acc -= 1 / SIM_HZ
                    ch, key = title_frame(scene, cols, rows - 1, int(now * 1.5) % 2 == 0)
                    term.out.write(encode(ch, key, cols, rows))
                    term.out.flush()
            else:
                if game.state == "paused":
                    for p in inp.take():
                        if p in ("p", "esc"):
                            game.state = "play"
                            inp.clear()
                        elif p == "q":
                            return
                        elif p == "r":
                            game.reset()
                            inp.clear()
                    ch, key = game.render()
                    pause_overlay(game, ch, key)
                elif game.state in ("dead", "won"):
                    for p in inp.take():
                        if p == "q":
                            return
                        if p == "r":
                            game.reset()
                            inp.clear()
                    ch, key = game.render_end()
                else:
                    acc += dt
                    while acc >= 1 / SIM_HZ:
                        game.tick(1 / SIM_HZ, inp)
                        acc -= 1 / SIM_HZ
                    if game.state == "dying":
                        ch, key = game.render_end()
                    else:
                        ch, key = game.render()
                term.out.write(encode(ch, key, cols, rows))
                term.out.flush()

            spare = 1 / 30 - (time.perf_counter() - now)
            if spare > 0:
                time.sleep(spare)
    except KeyboardInterrupt:
        pass
    finally:
        term.close()


if __name__ == "__main__":
    main()
