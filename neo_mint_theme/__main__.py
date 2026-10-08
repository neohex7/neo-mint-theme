import argparse
import json
import os
from pathlib import Path
import sys

from . import __version__


def main():
    parser=argparse.ArgumentParser(description='Neo Mint Theme — Cinnamon appearance manager')
    parser.add_argument('--preview',action='store_true',help='Open the interface with all desktop writes disabled')
    parser.add_argument('--check',action='store_true',help='Validate the installation and build a read-only default plan')
    args=parser.parse_args()
    # All application strings, GTK dialogs, and subprocess errors are English.
    os.environ['LANGUAGE']='en'
    os.environ['LC_MESSAGES']='C.UTF-8'
    import locale
    locale.setlocale(locale.LC_MESSAGES,'C.UTF-8')
    from .core import Engine,DEFAULTS
    from .settings import SettingsBackend
    root=Path(__file__).resolve().parents[1]
    data=root/'data'
    engine=Engine(data,SettingsBackend())
    if args.check:
        import hashlib
        manifest=json.loads((data/'asset-manifest.json').read_text())
        css=data/'assets/themes/Neo-Colloid-Nord/gtk-3.0/gtk.css'
        assert hashlib.sha256(css.read_bytes()).hexdigest()==manifest['gtk_css_sha256']
        plan=engine.build_plan(DEFAULTS)
        print(json.dumps({'version':__version__,'gtk_css_verified':True,'read_only':True,'plan':plan.summary()},indent=2))
        return 0
    from .gui import Application
    from gi.repository import Gdk
    screen=Gdk.Screen.get_default()
    engine.primary_monitor=max(0,screen.get_primary_monitor())
    engine.monitors=[screen.get_monitor_plug_name(i) for i in range(screen.get_n_monitors())]
    app=Application(engine,args.preview)
    return app.run([sys.argv[0]])


if __name__=='__main__':
    raise SystemExit(main())
