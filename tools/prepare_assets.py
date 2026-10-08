#!/usr/bin/python3
"""Prepare a portable, wallpaper-free asset bundle from the local capture."""
from pathlib import Path
import copy
import hashlib
import json
import posixpath
import shutil
import tarfile

ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / 'local-backups/2026-10-08_15-36-04-mint-22.3'
DATA = ROOT / 'data'
ASSETS = DATA / 'assets'


def extract_group(tar, prefix, destination, accept=lambda rel: True):
    for m in tar.getmembers():
        if not m.name.startswith(prefix + '/'):
            continue
        rel = m.name[len(prefix) + 1:]
        if not accept(rel):
            continue
        if '..' in Path(rel).parts or Path(rel).is_absolute():
            raise ValueError('Unsafe archive entry')
        out = destination / rel
        if m.isdir():
            out.mkdir(parents=True, exist_ok=True)
        elif m.isfile():
            out.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(m) as src, out.open('wb') as dst:
                shutil.copyfileobj(src, dst)
            out.chmod(0o755 if m.mode & 0o111 else 0o644)
        elif m.issym():
            linkname = m.linkname
            # Repair an upstream developer's absolute, non-portable icon alias.
            if prefix.endswith('Reversal-green') and rel == 'preferences/22' and linkname.endswith('/Reversal-green/preferences/32'):
                linkname = '32'
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(rel), linkname))
            if linkname.startswith('/') or resolved.startswith('../'):
                raise ValueError(f'External symlink: {m.name}')
            out.parent.mkdir(parents=True, exist_ok=True)
            if not out.is_symlink():
                out.symlink_to(linkname)


def theme_file(rel):
    if rel == 'index.theme':
        return True
    parts = rel.split('/')
    if parts[0] not in ('gtk-2.0', 'gtk-3.0', 'gtk-4.0', 'metacity-1', 'cinnamon'):
        return False
    if parts[0] == 'gtk-3.0' and len(parts) > 1:
        return parts[1] in ('assets', 'gtk.css', 'gtk-dark.css')
    return True


def patch_compiz_power_client(assets):
    """Use UPower's initialized factory; plain GObject construction crashes on GC."""
    path = assets / 'extensions/compiz-windows-effect@hermes83.github.com/extension.js'
    code = path.read_text()
    code = code.replace('this._upClient = new UPowerGlib.Client();',
                        'this._upClient = UPowerGlib.Client.new();')
    code = code.replace('this._upDisplayDevice = this._upClient.get_display_device();',
                        'this._upDisplayDevice = this._upClient ? this._upClient.get_display_device() : null;')
    code = code.replace('settings.settings.getValue("power-onbattery") === true &&',
                        'settings.settings.getValue("power-onbattery") === true && this._upDisplayDevice != null &&')
    path.write_text(code)


def main():
    ASSETS.mkdir(parents=True, exist_ok=True)
    with tarfile.open(CAPTURE / 'system-appearance-without-wallpaper.tar.gz') as t:
        extract_group(t, 'usr/share/themes/Colloid-Nord', ASSETS / 'themes/Neo-Colloid-Nord', theme_file)
        extract_group(t, 'usr/share/themes/Vibrant-Teal-Dark', ASSETS / 'themes/Neo-Vibrant-Teal-Dark',
                      lambda r: r == 'index.theme' or r.startswith('cinnamon/'))
        extract_group(t, 'usr/share/icons/Reversal-green', ASSETS / 'icons/Neo-Reversal-green',
                      lambda r: r != 'icon-theme.cache')
        extract_group(t, 'usr/share/plank/themes/mcOS-Monterey-BlackLight', ASSETS / 'plank/Neo-BlackLight')
    for directory, previous, new in [('themes/Neo-Colloid-Nord', 'Colloid-Nord', 'Neo-Colloid-Nord'),
                                     ('themes/Neo-Vibrant-Teal-Dark', 'Mint-Y-Dark-Teal', 'Neo-Vibrant-Teal-Dark'),
                                     ('icons/Neo-Reversal-green', 'Reversal-green', 'Neo-Reversal-green')]:
        p = ASSETS / directory / 'index.theme'
        p.write_text(p.read_text().replace(previous, new))
    with tarfile.open(CAPTURE / 'user-appearance-private.tar.gz') as t:
        for kind, names in {'applets': ['sysmonitor@orcus', 'temperature@fevimu'],
                            'extensions': ['transparent-panels@germanfr', 'compiz-windows-effect@hermes83.github.com']}.items():
            for name in names:
                extract_group(t, f'.local/share/cinnamon/{kind}/{name}', ASSETS / kind / name,
                              lambda r: not any(p in ('__pycache__', 'DEBUG') for p in Path(r).parts))
        templates = {}
        allowed = ['calendar@cinnamon.org', 'menu@cinnamon.org', 'expo@cinnamon.org', 'scale@cinnamon.org',
                   'user@cinnamon.org', 'sysmonitor@orcus', 'temperature@fevimu',
                   'transparent-panels@germanfr', 'compiz-windows-effect@hermes83.github.com']
        for m in t:
            if m.isfile() and m.name.startswith('.config/cinnamon/spices/') and m.name.endswith('.json'):
                uuid = m.name.split('/')[3]
                if uuid in allowed:
                    content = json.load(t.extractfile(m))
                    content.pop('__md5__', None)
                    templates[uuid] = content
        (DATA / 'spice-settings.json').write_text(json.dumps(templates, indent=2, ensure_ascii=False) + '\n')
    patch_compiz_power_client(ASSETS)
    profile = json.loads((ROOT / 'config/baseline-22.3.json').read_text())
    profile.pop('captured_at', None)
    profile['display_defaults_description'] = 'Defaults are intended for a 24-inch display. Adjust them for your screen size, resolution, and scaling.'
    for schema, values in profile['settings_gvariant'].items():
        if 'gtk-theme' in values: values['gtk-theme'] = "'Neo-Colloid-Nord'"
        if 'icon-theme' in values: values['icon-theme'] = "'Neo-Reversal-green'"
        if schema == 'org.cinnamon.theme': values['name'] = "'Neo-Vibrant-Teal-Dark'"
        if schema.startswith('net.launchpad.plank'): values['theme'] = "'Neo-BlackLight'"
    (DATA / 'profile.json').write_text(json.dumps(profile, indent=2, ensure_ascii=False) + '\n')
    # Preserve the user's exact stylesheet, not a regenerated upstream theme.
    css = ASSETS / 'themes/Neo-Colloid-Nord/gtk-3.0/gtk.css'
    expected = json.loads((CAPTURE / 'manifest.json').read_text())['css_sha256']
    assert hashlib.sha256(css.read_bytes()).hexdigest() == expected
    digest = hashlib.sha256()
    for p in sorted(ASSETS.rglob('*')):
        if p.is_file() or p.is_symlink():
            digest.update(str(p.relative_to(ASSETS)).encode())
            digest.update(str(p.readlink()).encode() if p.is_symlink() else p.read_bytes())
    (DATA / 'asset-manifest.json').write_text(json.dumps({'version': 1, 'sha256': digest.hexdigest(),
        'gtk_css_sha256': expected, 'wallpaper_included': False}, indent=2) + '\n')
    print('Prepared portable assets. Original gtk.css verified. No wallpapers or personal launchers included.')


if __name__ == '__main__':
    main()
