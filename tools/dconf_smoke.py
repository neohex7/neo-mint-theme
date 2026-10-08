#!/usr/bin/python3
"""Exercise GUI-style workers using dconf on a private bus and database."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def child():
    from gi.repository import GLib
    from neo_mint_theme.core import Engine, DEFAULTS, CINNAMON
    from neo_mint_theme.settings import SettingsBackend
    directory = Path(os.environ['XDG_CONFIG_HOME']).parent
    home = directory/'home';home.mkdir()
    backend = SettingsBackend()
    engine = Engine(ROOT/'data', backend, home=home, state=directory/'state', temperature=False)
    backend.write(CINNAMON, 'panels-enabled', "['1:0:bottom']")
    backend.sync()
    observer = backend.settings(CINNAMON)
    observed = []
    observer.connect('changed', lambda s, key: observed.append((
        s.get_strv('panels-enabled'), s.get_strv('enabled-applets'))))
    context = GLib.MainContext.default()
    def run(method):
        errors = []
        def worker():
            try: method()
            except Exception as exc: errors.append(exc)
        thread = threading.Thread(target=worker)
        thread.start()
        deadline = time.monotonic()+30
        while thread.is_alive():
            while context.pending(): context.iteration(False)
            if time.monotonic() > deadline:
                raise RuntimeError('The private dconf operation timed out.')
            time.sleep(.01)
        thread.join()
        # Deliver remaining observer notifications before checking the layout.
        while context.pending(): context.iteration(False)
        if errors: raise errors[0]
    for _ in range(3):
        current = engine.read_current()
        plan = engine.build_plan(copy.deepcopy(DEFAULTS), current)
        run(lambda: engine.apply(plan, environment=False, launch=False))
        assert backend.read(CINNAMON, 'panels-enabled') == ['1:0:top']
        assert 'panel1:left:0:menu@cinnamon.org:0' in backend.read(CINNAMON, 'enabled-applets')
        assert engine.preferences()['installed_components']
        run(engine.restore_original)
        assert backend.read(CINNAMON, 'panels-enabled') == ['1:0:bottom']
        assert not engine.preferences()['installed_components']
    assert observed
    assert (Path(os.environ['XDG_CONFIG_HOME'])/'dconf/user').is_file()
    print(json.dumps({'private_dconf': True, 'worker_restore_apply_cycles': 3,
                      'desktop_contact': False, 'passed': True}, indent=2))


if __name__ == '__main__':
    if '--child' in sys.argv:
        child()
    else:
        with tempfile.TemporaryDirectory(prefix='neo-private-dconf-') as temp:
            # Set paths BEFORE starting the bus so its activated dconf service
            # inherits the same private database location as the client.
            env = dict(os.environ, XDG_CONFIG_HOME=temp+'/config',
                       XDG_CACHE_HOME=temp+'/cache', GSETTINGS_BACKEND='dconf')
            env.pop('DCONF_PROFILE', None)
            subprocess.run(['dbus-run-session', '--', '/usr/bin/python3', '-B',
                            str(Path(__file__).resolve()), '--child'], env=env, check=True, timeout=60)
