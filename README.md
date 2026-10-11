# DraFG for Decky

[![draske.com][badge-site]][site] [![Discord][badge-discord]][discord] [![DraFG on Steam][badge-steam]][steam] [![X: @DraskeLabs][badge-x]][x]

A [Decky Loader](https://decky.xyz) plugin that controls [DraFG][steam] frame
generation in SteamOS Game Mode from the Quick Access menu.

- **On/off** for frame generation while you play, with one status line: the
  game's frame rate, what DraFG shows, and the delay it adds
  (`30 → 60 fps · +33 ms`).
- **Model** (Max / Mobile), **Multiplier** (2×–16×: at most that many times
  the game's frame rate), **Frame size** (Native or a smaller size, with
  **Upscaling**), and a small **stats** line.
- **Advanced settings**: generation mode (Extrapolate is experimental),
  processing backend, matrix acceleration, capture memory.
- **Remember for this game**: settings per game.
- One clear message when something gets in the way (VRR on, the GPU not
  keeping up, a Steam frame limit that leaves no room, Steam's performance
  overlay sharing the screen), and the Steam frame limit that would be
  smoothest.

Steam Deck support is a Preview: Game Mode is confirmed working, and it stays
a Preview until DraFG is fully confirmed on Steam Deck hardware itself.

## You need

- DraFG, installed from Steam (the plugin starts the copy Steam installed).
- Decky Loader 3.2 or newer.
- SteamOS Game Mode (or another gamescope "Gaming Mode" session, such as
  Bazzite's). In Desktop Mode, open DraFG itself instead.

## Install

This plugin is installed manually, not from the Decky store.

1. In Game Mode: Quick Access → Decky → ⚙ (settings) → General → enable
   **Developer mode**.
2. Decky settings → **Developer** → **Install plugin from URL**, and enter
   `https://github.com/DraskeLabs/drafg-decky/releases/latest/download/DraFG.zip`.
   (Or download `DraFG.zip` from
   [the releases page](https://github.com/DraskeLabs/drafg-decky/releases) and
   use **Install plugin from ZIP file**.)

**Updating:** Decky does not update plugins installed this way. Install again
from the same link (it always gives the latest release); your settings are
kept. If the panel says DraFG is older than the plugin, update DraFG in Steam.

## Use

1. Start a game.
2. Quick Access → DraFG → **Frame generation** on.
3. If the panel suggests a frame limit, set it in Quick Access → Performance →
   Frame Limit.

DraFG hides itself whenever Steam's own screens are up and comes back when the
game is focused again.

## Best results

Set Steam's Frame Limit (Quick Access → Performance) to half the refresh rate,
30 on 60 Hz or 45 on 90 Hz, and the game's own frame cap higher (e.g. 60) or
off. A game that sets its own pace shares the GPU with DraFG, so DraFG cannot
always time it evenly; while that happens, it shows a warning in the top-right
corner.

## How it works

The plugin's backend finds DraFG in your Steam library, starts
`drafg gamemode`, and talks to it over a local socket that only your user can
open ([PROTOCOL.md](PROTOCOL.md)). DraFG itself checks with Steam that you own
it. The plugin does not download anything, contains no part of DraFG, and runs
nothing except the DraFG that Steam installed.

## Build

```bash
pnpm install
pnpm run build          # writes dist/index.js
scripts/package.sh      # writes out/DraFG.zip and prints its SHA-256
python3 -m unittest discover -s tests
```

## License

BSD 3-Clause, see [LICENSE](LICENSE). Based on the Decky plugin template;
bundled third-party parts are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
DraFG itself is separate, proprietary software sold on Steam.

[site]: https://draske.com/
[discord]: https://discord.com/invite/GMt2JHbdNR
[steam]: https://store.steampowered.com/app/4934180/DraFG/
[x]: https://x.com/DraskeLabs
[badge-site]: https://img.shields.io/badge/draske.com-1E1A16?style=for-the-badge&logo=data:image/svg%2Bxml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjUuMyA1LjMgNTMuNCA1My40Ij48ZyBmaWxsPSIjRkZGRkZGIiBzdHJva2U9IiNGRkZGRkYiIHN0cm9rZS13aWR0aD0iMS40IiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48cG9seWdvbiBvcGFjaXR5PSIwLjI1IiBwb2ludHM9IjI2LjIsNiA0NS44LDYuNSA0NS44LDQ4LjQgMjYuMiw0Ny45Ii8%2BPHBvbHlnb24gb3BhY2l0eT0iMC40IiBwb2ludHM9IjIyLjIsOC4xIDM5LjksMTYuMyAzOS45LDUyLjcgMjIuMiw0NC40Ii8%2BPHBvbHlnb24gb3BhY2l0eT0iMC42OCIgcG9pbnRzPSIxOC4yLDEwLjEgMzIsMjcuMiAzMiw1OCAxOC4yLDQwLjkiLz48L2c%2BPC9zdmc%2BCg==
[badge-discord]: https://img.shields.io/badge/Discord-5865F2?style=for-the-badge&logo=discord&logoColor=white
[badge-steam]: https://img.shields.io/badge/DraFG_on_Steam-171A21?style=for-the-badge&logo=steam&logoColor=white
[badge-x]: https://img.shields.io/badge/%40DraskeLabs-000000?style=for-the-badge&logo=x&logoColor=white
