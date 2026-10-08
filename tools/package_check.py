#!/usr/bin/python3
"""Check the built Debian artifact, including access by ordinary users."""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
import tarfile
import tempfile


def check(package):
    count = 0
    with subprocess.Popen(['dpkg-deb', '--fsys-tarfile', str(package)], stdout=subprocess.PIPE) as process:
        with tarfile.open(fileobj=process.stdout, mode='r|') as archive:
            for entry in archive:
                assert entry.uid == entry.gid == 0, f'Unexpected owner: {entry.name}'
                if entry.isdir():
                    assert entry.mode & 0o005 == 0o005, f'Not traversable by users: {entry.name}'
                elif entry.isfile():
                    assert entry.mode & 0o004, f'Not readable by users: {entry.name}'
                    count += 1
                if not entry.issym():
                    assert not entry.mode & 0o022, f'Writable public resource: {entry.name}'
        assert process.wait() == 0
    assert subprocess.check_output(['dpkg-deb', '-f', str(package), 'Maintainer'], text=True).strip() == 'neo'
    with tempfile.TemporaryDirectory(prefix='neo-package-check-') as temp:
        root = Path(temp)
        subprocess.run(['dpkg-deb', '-x', str(package), str(root)], check=True)
        subprocess.run(['dpkg-deb', '-e', str(package), str(root/'control')], check=True)
        for line in (root/'control/md5sums').read_text().splitlines():
            expected, name = line.split('  ', 1)
            assert hashlib.md5((root/name).read_bytes()).hexdigest() == expected, name
        data = root/'usr/share/neo-mint-theme/data'
        import gi
        gi.require_version('GdkPixbuf', '2.0')
        from gi.repository import GdkPixbuf
        GdkPixbuf.Pixbuf.new_from_file(str(data/'preview.png'))
        manifest = json.loads((data/'asset-manifest.json').read_text())
        assert hashlib.sha256((data/'assets/themes/Neo-Colloid-Nord/gtk-3.0/gtk.css').read_bytes()).hexdigest() == manifest['gtk_css_sha256']
        assert (root/'usr/bin/neo-mint-theme').stat().st_mode & 0o111 == 0o111
    return {'package': str(package), 'regular_files_checked': count, 'ordinary_user_access': 'passed',
            'checksums': 'passed', 'preview_decode': 'passed', 'original_gtk_css': 'passed', 'maintainer': 'neo'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    print(json.dumps(check(parser.parse_args().package), indent=2))
