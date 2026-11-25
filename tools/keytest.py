#!/usr/bin/env python3
"""Show what your terminal sends for each key, and how the game reads it.

If a key doesn't seem to work in the game, run this in the same terminal,
press the key a few times and hold it, and look at what comes through.
Press q (or wait 30 seconds) to quit.

  python3 tools/keytest.py
"""
import os
import select
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import winter_fire_game as g  # noqa: E402

EVENT = {1: "press", 2: "repeat", 3: "release"}


def main():
    term = g.Terminal()
    term.out.write("\x1b[?1049l\x1b[?25h")      # stay on the normal screen so the log scrolls
    term.out.flush()
    print("Press keys (q quits). Raw bytes on the left, what the game sees on the right.\r")
    start = time.perf_counter()
    try:
        while time.perf_counter() - start < 30:
            if not select.select([term.fd], [], [], 0.1)[0]:
                continue
            raw = os.read(term.fd, 1024).decode(errors="ignore")
            evs = g.parse_keys(raw)
            seen = ", ".join("%s %s%s" % (name or "?", EVENT.get(ev, ev), " +shift" if sh else "")
                             for name, ev, sh in evs)
            print("%-28s -> %s\r" % (repr(raw), seen))
            if any(name == "q" and ev == 1 for name, ev, _ in evs):
                break
    finally:
        term.close()


if __name__ == "__main__":
    main()
