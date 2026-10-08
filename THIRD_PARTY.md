# Third-party artwork and Cinnamon spices

Neo Mint Theme's application code is GPL-3.0. Bundled third-party materials retain their original authorship and applicable licenses. The editable CSS, SVG, JavaScript and theme configuration are included in the source distribution.

| Material | Source / attribution | License information |
| --- | --- | --- |
| Custom Colloid Nord | [vinceliuice/Colloid-gtk-theme](https://github.com/vinceliuice/Colloid-gtk-theme); local GTK 3 stylesheet modified by Neo | GPL-3.0; upstream notice included in the theme directory |
| Reversal green icons | [yeyushengfan258/Reversal-icon-theme](https://github.com/yeyushengfan258/Reversal-icon-theme) | GPL-3.0; original COPYING included |
| Vibrant Teal Dark | [aktzx/cinnamon-themes](https://github.com/aktzx/cinnamon-themes), based on [Linux Mint themes](https://github.com/linuxmint/mint-themes) | Mint's base copyright notice is included. |
| BlackLight Plank theme | Local customized mcOS Monterey Black Light configuration; [original listing](https://www.gnome-look.org/p/1541094) | See the original listing. |
| System Monitor / CPU Temperature | orcuscz / claudiux; [Cinnamon Spices applets](https://github.com/linuxmint/cinnamon-spices-applets) | GPL-3.0 repository notices included |
| Transparent Panels | germanfr; [source](https://github.com/germanfr/cinnamon-transparent-panels) | Original GPL-3.0 LICENSE included |
| Compiz windows effect | hermes83; [Cinnamon Spices extensions](https://github.com/linuxmint/cinnamon-spices-extensions) | Original GPL-3.0 LICENSE included |

Ubuntu and DejaVu fonts, DMZ cursors, Breeze fallback icons, Cinnamon, Nemo, GNOME Terminal, lm-sensors, and Plank are installed as distribution package dependencies; their font/binary payloads are not vendored.

The desktop screenshot is supplied by the project owner for promotional use. Its wallpaper is visible in the screenshot, but no wallpaper file is included in the theme assets. The screenshot is not installed or applied as a background.

Portable asset adjustments: themes use distinct Neo names; duplicate theme/icon archives and downloaded installers are omitted; the Reversal preferences/22 alias is repaired from an upstream absolute home path to a local preferences/32 alias. The owner's GTK 3 `gtk.css` is preserved byte for byte.

The bundled Compiz windows extension uses the initialized UPower client factory and handles an unavailable display device. This local compatibility fix prevents a crash when disabling or restoring its effects on Mint 22.3. Original authorship and license are retained.
