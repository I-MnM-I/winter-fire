# Winter Fire

A campfire on a snowy night, drawn entirely with text characters in your terminal.

There are two parts:

- **`winter_fire.py`**: an animated scene with pine trees, a blizzard, the moon and a crackling fire whose glow lights up the snow.
- **`winter_fire_game.py`**: a first-person survival-horror game set in the same forest. Keep the fire alive until dawn. Something out in the trees is waiting for it to go out.

Both are pure Python 3 with no packages to install. You need a terminal with 24-bit colour (Konsole, GNOME Terminal, kitty, WezTerm, Alacritty, Ptyxis...). Fullscreen looks best.

## The game

```bash
python3 winter_fire_game.py
```

It's 22:00 and the fire is burning low. The only wood is out in the dark between the trees. Bring back sticks and logs and walk into the firelight to feed the fire. Make it to 06:00.

- The fire is always burning down, and every minute or two a gust tears through camp and knocks it lower. If it goes out, you get a few seconds to save the embers.
- Away from the fire you get colder. Stay out too long and you freeze.
- Something lives in the forest. It won't come into the firelight, and the smaller the fire gets, the closer it can come. When it's near, the edges of your vision crawl red, heaviest on the side it's coming from.
- Notes left by the last people who camped here tell you how it behaves.

| Key | Action |
| --- | --- |
| W / S | walk forward / back |
| A / D | step left / right |
| ← / → (or J / L) | turn |
| Shift + W | run |
| E | read a note |
| P / Esc | pause |

Options: `--night 5` sets the night length in minutes (default 7). `--seed N` picks a forest.

In terminals that support the kitty keyboard protocol (kitty, WezTerm, foot, Ghostty, Alacritty), movement is precise. In others (Konsole, GNOME Terminal), holding a key works through key repeat, so there's a short pause after you first press it.

### Playtesting

`tools/playtest.py` plays full nights with a bot that only uses what a player could see, and reports how each night ended. Handy for checking balance after a change:

```bash
python3 tools/playtest.py -n 10
```

## The animation

```bash
python3 winter_fire.py
```

| Key | Action |
| --- | --- |
| ← / → | wind |
| ↑ / ↓ | fire size |
| + / - | speed |
| space | pause |
| g | fire glow on/off |
| m | moon on/off |
| s | snow amount (up to blizzard) |
| r | grow a new forest |
| h | help |
| q | quit |

Options: `--speed 0.25`, `--snow 0-4`, `--seed N`.

## Credit

Inspired by a TikTok from [@meatballsammichh](https://www.tiktok.com/@meatballsammichh) of their Python ASCII-art fire in a snowy forest.
