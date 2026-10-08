#!/usr/bin/python3
"""Build the same self-contained package that end users install."""
from pathlib import Path
import hashlib
import shutil
import subprocess
import tempfile
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from neo_mint_theme import __version__

ROOT=Path(__file__).resolve().parents[1]
VERSION=__version__


def normalize_permissions(stage):
    """Install public resources independently of the capture owner's file modes."""
    stage.chmod(0o755)
    for path in stage.rglob('*'):
        if path.is_symlink():
            continue
        if path.is_dir():
            path.chmod(0o755)
        elif path.is_file():
            path.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)


def main():
    dist=ROOT/'dist';dist.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='neo-mint-package-') as temp:
        stage=Path(temp)
        app=stage/'usr/share/neo-mint-theme';app.mkdir(parents=True)
        shutil.copytree(ROOT/'neo_mint_theme',app/'neo_mint_theme',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        shutil.copytree(ROOT/'data',app/'data',symlinks=True)
        docs=stage/'usr/share/doc/neo-mint-theme';docs.mkdir(parents=True)
        for name in ['README.md','LICENSE','THIRD_PARTY.md']:shutil.copy2(ROOT/name,docs/name)
        binaries=stage/'usr/bin';binaries.mkdir(parents=True)
        launcher=binaries/'neo-mint-theme'
        launcher.write_text('#!/bin/sh\nset -eu\ncd /usr/share/neo-mint-theme\nexec /usr/bin/python3 -B -m neo_mint_theme "$@"\n')
        launcher.chmod(0o755)
        desktop=stage/'usr/share/applications';desktop.mkdir(parents=True)
        shutil.copy2(ROOT/'packaging/neo-mint-theme.desktop',desktop/'neo-mint-theme.desktop')
        icons=stage/'usr/share/icons/hicolor/scalable/apps';icons.mkdir(parents=True)
        shutil.copy2(ROOT/'packaging/neo-mint-theme.svg',icons/'neo-mint-theme.svg')
        control=stage/'DEBIAN';control.mkdir()
        (control/'control').write_text(f'''Package: neo-mint-theme
Version: {VERSION}
Section: x11
Priority: optional
Architecture: all
Maintainer: neo
Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-3.0, gir1.2-gdkpixbuf-2.0, cinnamon (>= 6.6), nemo, gnome-terminal, gnome-system-monitor, plank, lm-sensors, fonts-ubuntu, fonts-dejavu-core, dmz-cursor-theme, breeze-icon-theme
Description: Reversible desktop customization for Linux Mint 22.3 Cinnamon
 Apply custom Colloid Nord and Vibrant Teal themes, green Reversal icons,
 a Plank dock, panel layout, terminal colors, and optional desktop effects.
 Adjust display sizes and restore the appearance saved before customization.
''')
        (control/'md5sums').write_text(''.join(hashlib.md5(p.read_bytes()).hexdigest()+'  '+str(p.relative_to(stage))+'\n'
             for p in sorted(stage.rglob('*')) if p.is_file() and not p.is_symlink() and control not in p.parents))
        normalize_permissions(stage)
        output=dist/f'neo-mint-theme_{VERSION}_all.deb'
        subprocess.run(['dpkg-deb','--root-owner-group','-Zxz','--build',str(stage),str(output)],check=True)
    digest=hashlib.sha256(output.read_bytes()).hexdigest()
    (dist/'SHA256SUMS').write_text(digest+'  '+output.name+'\n')
    print(f'Package: {output}\nSize: {output.stat().st_size/1024/1024:.2f} MiB\nSHA256: {digest}')


if __name__=='__main__':main()
