#!/usr/bin/env python3
"""
Headless playtester for winter_fire_game.py.

A bot plays full nights the way a careful person would, using only what a
player could know: what is on screen (things in view, in line of sight and
close enough to make out through the blizzard), the compass, the HUD and
the red "fear" at the edges of the screen. It turns with the turn keys at
normal speed instead of snapping its view around.

  python3 tools/playtest.py              # 10 nights, summary
  python3 tools/playtest.py -n 30 -v     # 30 nights, one line per event
  python3 tools/playtest.py --shots DIR  # also save a few frames per night as text
"""

import argparse
import math
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))
import winter_fire_game as g  # noqa: E402

DT = 1 / g.SIM_HZ


class BotInput:
    def __init__(self):
        self.keys = set()
        self.run = False
        self.presses = []

    def held(self, *names):
        return any(n in self.keys for n in names)

    def running(self):
        return self.run

    def take(self):
        p, self.presses = self.presses, []
        return p

    def clear(self):
        self.keys.clear()


class Bot:
    """A cautious player."""

    SIGHT = 11.0          # how far it can make out a stick or log in the dark

    def __init__(self, game, rng):
        self.G = game
        self.rng = rng
        self.inp = BotInput()
        self.mode = "home"
        self.next_check = 3.0
        self.check_until = 0.0
        self.check_dir = 0.0
        self.wander = rng.uniform(0, math.tau)
        self.last_seen = None
        self.stuck = 0.0
        self.last_pos = (0.0, 0.0)
        self.read_notes = set()

    # what the player can actually see
    def sees_creature(self):
        G = self.G
        if not G.cr_active:
            return False
        dx, dy = G.cr_x - G.px, G.cr_y - G.py
        d = math.hypot(dx, dy)
        ang = g.wrap_angle(math.atan2(dy, dx) - G.a)
        return abs(ang) < g.FOV * 0.48 and d < g.SIGHT and G.los(G.px, G.py, G.cr_x, G.cr_y, skip=2.4)

    def visible_items(self):
        G = self.G
        out = []
        for it in G.items:
            dx, dy = it.x - G.px, it.y - G.py
            d = math.hypot(dx, dy)
            near_fire = math.hypot(it.x, it.y) < G.light_r + 2
            if d < (self.SIGHT if not near_fire else 20):
                out.append((d, it))
        out.sort(key=lambda p: p[0])
        return out

    def turn_to(self, ang):
        diff = g.wrap_angle(ang - self.G.a)
        if diff > 0.08:
            self.inp.keys.add("right")
        elif diff < -0.08:
            self.inp.keys.add("left")
        return abs(diff)

    def move_dir(self, ang):
        """Hold the walk keys that move toward world angle `ang` given where we face."""
        G = self.G
        diff = g.wrap_angle(ang - G.a)
        c, s = math.cos(diff), math.sin(diff)
        if c > 0.38:
            self.inp.keys.add("w")
        elif c < -0.38:
            self.inp.keys.add("s")
        if s > 0.38:
            self.inp.keys.add("d")
        elif s < -0.38:
            self.inp.keys.add("a")

    def path_angle(self, want):
        """Pick a direction near `want` that isn't blocked by a tree."""
        G = self.G
        for off in (0, 0.45, -0.45, 0.9, -0.9, 1.4, -1.4, 2.0, -2.0, 2.8):
            a = want + off
            if not G.blocked(G.px + math.cos(a) * 0.6, G.py + math.sin(a) * 0.6):
                return a
        return want + math.pi

    def step(self):
        G = self.G
        inp = self.inp
        inp.keys = set()
        inp.run = False
        t = G.t
        pd = math.hypot(G.px, G.py)
        in_light = pd < G.light_r * 0.75 + 0.8 and G.fuel > 0
        to_fire = math.atan2(-G.py, -G.px)
        carrying = G.sticks > 0 or G.log
        full = G.sticks >= g.MAX_STICKS or G.log
        sees = self.sees_creature()

        # ---- where to go
        if sees and not in_light:
            self.last_seen = (G.cr_x, G.cr_y, t)
        need_home = (full or G.warmth < 50 or (carrying and G.fuel < 30)
                     or (G.fuel < 18 and pd > 4) or (G.fear > 0.45 and pd > 6)
                     or (sees and not in_light and G.cr_dist < 7))
        if need_home:
            self.mode = "home"
        elif in_light and G.fuel > 85 and not carrying:
            self.mode = "rest"
        elif self.mode in ("home", "rest") and in_light and (G.warmth > 85 or G.fuel < 60):
            self.mode = "gather"
            self.wander = self.rng.uniform(0, math.tau)
        elif self.mode == "rest" and G.fuel < 60:
            self.mode = "gather"

        goal = None
        if self.mode == "home":
            goal = to_fire if pd > 1.6 else None
        elif self.mode == "gather":
            items = self.visible_items()
            if items:
                d, it = items[0]
                goal = math.atan2(it.y - G.py, it.x - G.px)
            else:
                if pd > 24:
                    self.wander = to_fire + self.rng.uniform(-1.2, 1.2)
                self.wander += self.rng.uniform(-0.05, 0.05)
                goal = self.wander
            for lx, ly, k, read in G.landmarks:
                if not read and (lx - G.px) ** 2 + (ly - G.py) ** 2 < 2.5 ** 2:
                    inp.presses.append("e")
        move = self.path_angle(goal) if goal is not None else None

        # ---- where to look
        if sees and not in_light:
            look = math.atan2(G.cr_y - G.py, G.cr_x - G.px)        # never look away from it
        else:
            if not in_light and t > self.next_check:
                self.check_dir = G.a + math.pi
                self.check_until = t + 1.2
                self.next_check = t + self.rng.uniform(3.0, 5.0) * (1.6 - G.fear)
            if G.fear > 0.2 and not in_light and t > self.check_until + 0.5:
                # the red at the screen edge shows which side it's on
                self.check_dir = G.a + G.fear_side
                self.check_until = t + 1.0
            if t < self.check_until:
                look = self.check_dir
            elif self.mode == "rest":
                look = G.a + 0.4
            else:
                look = move if move is not None else G.a
        self.turn_to(look)
        if move is not None:
            self.move_dir(move)
            facing_goal = abs(g.wrap_angle(move - G.a)) < 0.5
            if facing_goal and self.mode == "home" and (G.fear > 0.3 or G.fuel < 10) and G.stamina > 30:
                inp.run = True

        # ---- unstick
        moved = math.hypot(G.px - self.last_pos[0], G.py - self.last_pos[1])
        self.stuck = self.stuck + DT if moved < 0.01 and move is not None else 0.0
        self.last_pos = (G.px, G.py)
        if self.stuck > 0.8:
            self.wander += self.rng.choice((-1.6, 1.6))
            self.stuck = 0.0
        if G.reading is not None and self.rng.random() < 0.02:
            inp.presses.append("e")


def play(seed, night_len, verbose=False, shots=None):
    g.NIGHT_LEN = night_len
    rng = random.Random(seed)
    G = g.Game(160, 48, seed)
    bot = Bot(G, rng)
    stats = dict(seed=seed, wood=0, min_fuel=100.0, min_warm=100.0, close_calls=0, sightings=0,
                 teleports=0, embers=0, notes=0)
    was_close = False
    was_seen = False
    last_cr = (G.cr_x, G.cr_y)
    fuel_before = G.fuel
    shot_times = {60, 150, 240, 330} if shots else set()
    while G.state == "play":
        bot.step()
        G.tick(DT, bot.inp)
        if G.fuel > fuel_before + 1:
            stats["wood"] += 1
            if verbose:
                print("  %s fed the fire -> %.0f" % (G.clock(), G.fuel))
        fuel_before = G.fuel
        stats["min_fuel"] = min(stats["min_fuel"], G.fuel)
        stats["min_warm"] = min(stats["min_warm"], G.warmth)
        if G.embers > 0 and G.embers > EMB_PREV[0]:
            stats["embers"] += 1
        EMB_PREV[0] = G.embers
        close = G.cr_active and G.cr_dist < 3.0
        if close and not was_close:
            stats["close_calls"] += 1
            if verbose:
                print("  %s close call (%.1f away)" % (G.clock(), G.cr_dist))
        was_close = close
        seen = bot.sees_creature()
        if seen and not was_seen:
            stats["sightings"] += 1
        was_seen = seen
        if math.hypot(G.cr_x - last_cr[0], G.cr_y - last_cr[1]) > 3:
            stats["teleports"] += 1
        last_cr = (G.cr_x, G.cr_y)
        if shots and int(G.t * g.SIM_HZ) in {s * int(g.SIM_HZ) for s in shot_times}:
            ch, key = G.render()
            path = os.path.join(shots, "night%d_%03ds.txt" % (seed, int(G.t)))
            with open(path, "w") as f:
                f.write("\n".join("".join(ch[y * G.W:(y + 1) * G.W]) for y in range(G.H)))
    stats["notes"] = sum(1 for lm in G.landmarks if lm[3])
    stats["result"] = "won" if G.state == "won" else G.death
    stats["clock"] = G.clock()
    return stats


EMB_PREV = [0.0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=10)
    ap.add_argument("--night", type=float, default=7.0, help="minutes")
    ap.add_argument("-v", action="store_true")
    ap.add_argument("--shots")
    a = ap.parse_args()
    results = []
    for seed in range(1, a.n + 1):
        if a.v:
            print("night", seed)
        s = play(seed, a.night * 60, a.v, a.shots)
        results.append(s)
        print("night %2d: %-7s at %s | wood %2d | lowest fire %3.0f warmth %3.0f | "
              "close calls %d, sightings %d, teleports %d, ember saves %d, notes %d" % (
                  seed, s["result"], s["clock"], s["wood"], s["min_fuel"], s["min_warm"],
                  s["close_calls"], s["sightings"], s["teleports"], s["embers"], s["notes"]))
    from collections import Counter
    print("summary:", dict(Counter(r["result"] for r in results)))


if __name__ == "__main__":
    main()
