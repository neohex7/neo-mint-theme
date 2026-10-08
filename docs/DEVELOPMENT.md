# Development

Target: Linux Mint 22.3 Cinnamon on X11. Install the dependencies listed in `tools/build_deb.py`, then run:

```bash
./run.sh
```

`./run.sh --preview` opens the app with appearance changes disabled. `./run.sh --check` verifies the captured GTK stylesheet and prints a read-only change plan.

## Validate and build

```bash
python3 -B -m unittest discover -s tests -v
python3 -B tools/gui_smoke.py
python3 -B tools/dconf_smoke.py
python3 -B tools/build_deb.py
python3 -B tools/package_check.py dist/neo-mint-theme_1.0.3_all.deb
```

See [Testing](TESTING.md) for the recorded VM results and remaining acceptance checks. Generated packages go into the ignored `dist/` directory. Publish the current package and its SHA-256 checksum as GitHub Release assets.

## Layout

- `neo_mint_theme/`: English GTK interface, settings backend and reversible apply engine.
- `data/`: prepared themes, icons, Cinnamon spices and sanitized defaults.
- `packaging/`: launcher and application icon.
- `tests/` and `tools/`: integration checks and package tooling.

The public source builds from the prepared `data/` bundle. `tools/prepare_assets.py` is a maintainer helper that reads ignored local captures; it is not an installation step. Workstation captures, original configuration and VM diagnostics are excluded from Git.

Appearance files are managed per user. Journals and saved originals live under `${XDG_STATE_HOME:-~/.local/state}/neo-mint-theme`. Restore affects the files and settings recorded by the app; subsequent manual edits to those same items can be replaced by the saved original.

## Branches

`main` contains published beta work. Use `dev` for ongoing development and propose changes through pull requests.
