"""Synchronize live Cinnamon panels and file-backed applet settings."""
import json
import time
from pathlib import Path
from gi.repository import Gio, GLib


class CinnamonSession:
    def __init__(self, connection=None):
        self.connection = connection

    def bus(self):
        return self.connection or Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def require_running(self):
        reply = self.bus().call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                                    'org.freedesktop.DBus', 'NameHasOwner',
                                    GLib.Variant('(s)', ('org.Cinnamon',)), None,
                                    Gio.DBusCallFlags.NONE, 3000, None)
        if not reply.unpack()[0]:
            raise RuntimeError('Cinnamon is not running. Sign out and sign back in using Cinnamon (Default), then try again.')

    def wait_for_panels(self, entries, timeout=4):
        expected = sorted((int(p[0]), int(p[1]), {'top': 0, 'bottom': 1, 'left': 2, 'right': 3}[p[2]])
                          for entry in entries if len(p := entry.split(':')) == 3)
        script = 'imports.ui.main.panelManager.panels.filter(p=>p).map(p=>[p.panelId,p.monitorIndex,p.panelPosition])'
        deadline = time.monotonic() + timeout
        while True:
            reply = self.bus().call_sync('org.Cinnamon', '/org/Cinnamon', 'org.Cinnamon',
                                        'Eval', GLib.Variant('(s)', (script,)), None,
                                        Gio.DBusCallFlags.NONE, 3000, None).unpack()
            if reply[0] and sorted(tuple(p) for p in json.loads(reply[1])) == expected:
                return
            if time.monotonic() >= deadline:
                raise RuntimeError('Cinnamon did not finish moving the panel. Sign out and back in, then try again.')
            time.sleep(.1)

    def refresh(self, home, paths, backend):
        targets = []
        enabled = backend.read('org.cinnamon', 'enabled-applets')
        applets = {(p[3], p[4]) for entry in enabled
                   if len(p := entry.split(':')) >= 5}
        extensions = set(backend.read('org.cinnamon', 'enabled-extensions'))
        for relative in paths:
            parts = Path(relative).parts
            if len(parts) != 5 or parts[:3] != ('.config', 'cinnamon', 'spices'):
                continue
            uuid, filename = parts[3:]
            instance = Path(filename).stem
            if (uuid, instance) not in applets and not (uuid in extensions and instance == uuid):
                continue
            path = Path(home) / relative
            if not path.is_file():
                continue
            settings = json.loads(path.read_text())
            for key, entry in settings.items():
                if isinstance(entry, dict) and 'value' in entry:
                    targets.append((uuid, instance, key, json.dumps(entry['value'])))
                    break  # updateSetting reloads the entire settings file.
        pending = targets
        deadline = time.monotonic() + 4
        failures = {}
        while pending:
            retry = []
            for target in pending:
                try:
                    self.bus().call_sync('org.Cinnamon', '/org/Cinnamon', 'org.Cinnamon',
                                         'updateSetting', GLib.Variant('(ssss)', target),
                                         None, Gio.DBusCallFlags.NONE, 3000, None)
                except GLib.Error as exc:
                    retry.append(target)
                    failures[target[:2]] = str(exc)
            if not retry:
                return
            if time.monotonic() >= deadline:
                details = '; '.join(f'{uuid}/{instance}: {failures[(uuid, instance)]}' for uuid, instance, *_ in retry)
                raise RuntimeError(details)
            pending = retry
            time.sleep(.1)
