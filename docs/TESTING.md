# Testing Neo Mint Theme

## Automated integration checks

Run `python3 -B -m unittest discover -s tests -v` on a machine with the declared Cinnamon/Plank schemas. Tests use real GSettings schemas with an isolated in-memory backend and temporary home/state directories. They do not modify the signed-in user's appearance or start Plank.

Run `python3 -B tools/gui_smoke.py` in a graphical session to exercise the actual GTK window, seven pages, component sensitivity, default values, wallpaper selection state and review dialog. This uses the same isolated backend and does not apply anything to the desktop.

Run `python3 -B tools/dconf_smoke.py` to repeat three worker-thread Apply/Restore cycles against a real dconf backend on a private D-Bus session and temporary database. This checks persistence and delivery of related layout keys without contacting the signed-in Cinnamon desktop. The GTK smoke test also repeats two Restore/Apply cycles in the same window.

Run `./run.sh --check` to verify the original GTK stylesheet and inspect a read-only default change plan. Run `./run.sh --preview` to inspect the interface with writes disabled.

After building, run `python3 -B tools/package_check.py dist/neo-mint-theme_1.0.3_all.deb`. This inspects every archive entry for root ownership, public read/traversal access and absence of group/world write access; verifies package checksums, preview decoding, the original GTK stylesheet and maintainer metadata. Source-owner GUI tests alone cannot catch root-owned installation permission failures.

## Clean-machine acceptance test

Use Linux Mint 22.3 Cinnamon, signed in to X11. No virtual machine is created or started by the application, build scripts or tests.

1. Install the exact release `.deb` through Mint's package installer. Confirm required packages are resolved, the menu entry exists and the app opens as a normal user.
2. Open each page. Verify English strings, readable controls and scrolling at 1280×720, 1920×1080 and HiDPI text scaling.
3. Apply all components while keeping the current wallpaper. Check application colors, Nemo icons, centered clock, panel graph, Plank, terminal colors and fixed fonts.
4. Sign out and back in, then restart the guest. Confirm appearance and enabled dock startup persist.
5. Change only dock size; verify panel/font/wallpaper values stay unchanged. Repeat Apply and confirm no duplicate applets or startup entries.
6. Choose a local image with spaces and non-ASCII characters in its path. Check Zoom/Fit/Stretch/Center and verify the wallpaper remains usable after moving the original source image.
7. Toggle panel indicators and effects. On a guest without CPU sensors, verify the temperature indicator is omitted with an English explanation.
8. Disable dock startup, sign out and back in, then enable it again. Test with an existing Plank startup entry as well as a fresh user.
9. Undo several consecutive changes and verify reverse chronological order. Restore Original Appearance and compare the original keys and files, including keys that had no explicit user value.
10. Repeat Restore Original Appearance -> Apply Customization several times in the same session. Check that the panel moves back to the top, its applets return, and the four-square menu icon refreshes.
11. Upgrade the `.deb` without resetting user values; verify new theme assets update only when applied. Restore appearance before uninstalling if desired.
12. Repeat initial installation on a new ordinary user with different home path, preexisting themes, multiple panels and unrelated extensions.

Hardware-dependent animations, CPU sensors and multiple physical displays also require a real-machine check. Local automated success is not a claim that this clean-VM acceptance test has been completed.

## Recorded Mint 22.3 VM regression (2026-10-08)

Version 1.0.3 was installed and exercised as an ordinary user in GNOME Boxes with Cinnamon 6.6.4 on X11. Three actual GTK-worker Restore Original Appearance -> Apply cycles passed with all components selected and the current wallpaper retained. Each cycle verified the running panel's position through Cinnamon D-Bus, the live four-square menu icon and the GNOME Terminal menu entry. Explicit CJS garbage collection after each operation confirmed that disabling the window effect no longer triggers the reproduced native UPower crash. Repeat Apply and dock resize -> Undo also passed without moving the panel. A subsequent guest reboot preserved the top panel, menu icon, theme, enabled extensions, original wallpaper and automatic Plank startup.

The crash was independently reproduced in an isolated CJS subprocess with plain `new UPowerGlib.Client()` and eliminated using `UPowerGlib.Client.new()`. The latter initializes the client through UPower's native factory; see the [UPower reference](https://upower.freedesktop.org/docs/UpClient.html#up-client-new). The bundled extension uses this factory and guards an unavailable display device. Panel moves are published before applet changes, rather than publishing both in a single batch whose alphabetical notifications dispose applet actors too early.

These results cover the reported VM failure. They do not complete every acceptance item above: multiple physical monitors, battery behavior on a laptop, HiDPI and hardware-specific effects remain to be checked.
