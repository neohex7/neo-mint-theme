"""Portable planning and journaled, reversible appearance changes."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import copy
import fcntl
import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import uuid

from .settings import variant

INTERFACE = 'org.cinnamon.desktop.interface'
GNOME = 'org.gnome.desktop.interface'
WM = 'org.cinnamon.desktop.wm.preferences'
CINNAMON = 'org.cinnamon'
BACKGROUND = 'org.cinnamon.desktop.background'
PLANK = 'net.launchpad.plank.dock.settings:/net/launchpad/plank/docks/dock1/'
TRANSPARENT = 'transparent-panels@germanfr'
COMPIZ = 'compiz-windows-effect@hermes83.github.com'
SYSTEM_MONITOR = 'sysmonitor@orcus'
TEMPERATURE = 'temperature@fevimu'
COMPONENTS = ('application', 'desktop', 'icons', 'panel', 'dock', 'terminal', 'effects')
DEFAULTS = {
    'components': {c: True for c in COMPONENTS},
    'wallpaper_mode': 'keep', 'wallpaper_path': '', 'image_fit': 'zoom',
    'app_font_size': 10.0, 'title_font_size': 11.0, 'terminal_font_size': 10.0,
    'text_scale': 100, 'cursor_size': 24, 'panel_height': 24,
    'left_icon_size': 16, 'right_icon_size': 24, 'panel_visibility': 'always',
    'clock_24h': True, 'show_date': True, 'show_seconds': True,
    'show_system_monitor': True, 'show_temperature': True,
    'dock_icon_size': 44, 'dock_zoom': True, 'dock_zoom_amount': 125,
    'dock_autohide': True, 'dock_hide_delay': 1.25, 'dock_monitor': '',
    'dock_show_running': True, 'dock_startup': True,
    'transparent_panel': True, 'window_effects': True,
}
RANGES = {'app_font_size': (6, 32), 'title_font_size': (6, 32), 'terminal_font_size': (6, 40),
          'text_scale': (75, 200), 'cursor_size': (16, 96), 'panel_height': (20, 80),
          'left_icon_size': (12, 64), 'right_icon_size': (12, 64), 'dock_icon_size': (24, 128),
          'dock_zoom_amount': (100, 200), 'dock_hide_delay': (0, 5)}


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    with temp.open('w') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    temp.chmod(0o600)
    os.replace(temp, path)


def load_json(path, default=None):
    if not path.exists():
        return copy.deepcopy(default)
    return json.loads(path.read_text())


def available_temperature():
    for d in Path('/sys/class/hwmon').glob('hwmon*'):
        try:
            name = (d / 'name').read_text().strip()
            if name in ('k10temp', 'coretemp', 'zenpower') and list(d.glob('temp*_input')):
                return True
        except OSError:
            pass
    return False


def remove_path(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def copy_path(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        destination.symlink_to(source.readlink())
    elif source.is_dir():
        shutil.copytree(source, destination, symlinks=True)
    else:
        shutil.copy2(source, destination)


@dataclass
class Plan:
    settings: dict = field(default_factory=dict)
    files: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    options: dict = field(default_factory=dict)
    installed_components: list = field(default_factory=list)
    start_dock: bool = False

    def summary(self):
        return {'settings': [{'schema': s, 'key': k, 'value': v} for (s, k), v in self.settings.items()],
                'files': list(self.files), 'warnings': self.warnings,
                'change_count': len(self.settings) + len(self.files), 'start_dock': self.start_dock}


class Engine:
    def __init__(self, data, backend, home=None, state=None, temperature=None, primary_monitor=0, monitors=None, desktop=None):
        self.data = Path(data)
        self.backend = backend
        self.home = Path(home or Path.home()).resolve()
        self.state = Path(state or os.environ.get('XDG_STATE_HOME', self.home / '.local/state')) / 'neo-mint-theme'
        self.profile = load_json(self.data / 'profile.json')
        self.templates = load_json(self.data / 'spice-settings.json')
        self.asset_manifest = load_json(self.data / 'asset-manifest.json')
        self.temperature = available_temperature() if temperature is None else temperature
        self.primary_monitor = primary_monitor
        self.monitors = monitors
        # Tests with temporary homes or an isolated backend never contact the desktop.
        self.desktop = desktop
        if desktop is None and home is None and backend.gio_backend is None:
            from .desktop import CinnamonSession
            self.desktop = CinnamonSession()

    def _refresh_desktop(self, paths):
        if self.desktop is None:
            return []
        try:
            self.desktop.refresh(self.home, paths, self.backend)
            return []
        except Exception as exc:
            atomic_json(self.state / 'desktop-error.json', {'error': str(exc)})
            return ['Some running applets could not refresh. Sign out and back in to load their saved settings.']

    def validate(self, options):
        if set(options) != set(DEFAULTS):
            raise ValueError('The appearance profile is incomplete. Reset to defaults and try again.')
        if set(options['components']) != set(COMPONENTS) or any(type(v) is not bool for v in options['components'].values()):
            raise ValueError('Invalid component selection.')
        for key, (low, high) in RANGES.items():
            component = ('dock' if key.startswith('dock_') else 'panel' if key in ('panel_height','left_icon_size','right_icon_size') else
                         'desktop' if key == 'title_font_size' else 'terminal' if key == 'terminal_font_size' else
                         'icons' if key == 'cursor_size' else 'application')
            if not options['components'][component]:
                continue
            value = options[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
                raise ValueError(f'{key.replace("_", " ").capitalize()} must be between {low} and {high}.')
        for key, value in DEFAULTS.items():
            if isinstance(value, bool) and type(options[key]) is not bool:
                raise ValueError(f'Invalid option: {key}')
        if options['wallpaper_mode'] not in ('keep', 'choose') or options['image_fit'] not in ('zoom', 'scaled', 'stretched', 'centered'):
            raise ValueError('Invalid wallpaper option.')
        if options['panel_visibility'] not in ('always', 'auto'):
            raise ValueError('Invalid panel visibility.')
        if not isinstance(options['dock_monitor'], str) or '\n' in options['dock_monitor']:
            raise ValueError('Invalid monitor.')
        if options['wallpaper_mode'] == 'choose':
            image = Path(options['wallpaper_path']).expanduser()
            if not image.is_file() or image.stat().st_size > 50 * 1024 * 1024:
                raise ValueError('Choose an existing image smaller than 50 MB.')
            from gi.repository import GdkPixbuf
            if GdkPixbuf.Pixbuf.get_file_info(str(image))[0] is None:
                raise ValueError('The selected file is not a supported image.')

    def check_environment(self):
        release = {}
        for line in Path('/etc/os-release').read_text().splitlines():
            if '=' in line:
                key, value = line.split('=', 1)
                release[key] = value.strip('"')
        if release.get('ID') != 'linuxmint' or release.get('VERSION_ID') != '22.3':
            raise RuntimeError('Neo Mint Theme supports Linux Mint 22.3 Cinnamon.')
        if 'cinnamon' not in os.environ.get('XDG_CURRENT_DESKTOP', '').lower():
            raise RuntimeError('Sign in to a Cinnamon desktop session before applying customization.')
        if os.environ.get('XDG_SESSION_TYPE') != 'x11':
            raise RuntimeError('The Plank dock requires a Cinnamon X11 session. Sign in using Cinnamon (Default).')
        if self.desktop is not None:
            self.desktop.require_running()
        if os.geteuid() == 0:
            raise RuntimeError('Run this application as your normal user, not as root.')

    def preferences(self):
        return load_json(self.state / 'preferences.json', {'options': copy.deepcopy(DEFAULTS), 'installed_components': []})

    def terminal_schema(self):
        ident = self.backend.read('org.gnome.Terminal.ProfilesList', 'default')
        if not ident:
            raise RuntimeError('Open Terminal once to create its default profile, then try again.')
        return f'org.gnome.Terminal.Legacy.Profile:/org/gnome/terminal/legacy/profiles:/:{ident}/'

    def file_target(self, relative):
        relative = Path(relative)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Invalid appearance file path.')
        target = self.home / relative
        for parent in target.parents:
            if parent == self.home:
                break
            if parent.is_symlink():
                raise RuntimeError(f'Cannot manage appearance files through a symbolic-link directory: {parent.name}')
        return target

    def build_plan(self, options, current=None):
        self.validate(options)
        preferences = self.preferences()
        installed = set(preferences['installed_components'])
        previous = current if current is not None else preferences['options']
        plan = Plan(options=copy.deepcopy(options), installed_components=sorted(installed))
        base = self.profile['settings_gvariant']
        selected = options['components']
        fresh = {c: selected[c] and c not in installed for c in COMPONENTS}
        def changed(c, *keys):
            return selected[c] and (fresh[c] or any(options[k] != previous[k] for k in keys))
        def setting(schema, key, value, raw=False):
            if not self.backend.has(schema, key):
                raise RuntimeError(f'Required desktop setting is unavailable: {key}')
            value = self.backend.normalize(schema, key, value if raw else variant(value))
            if value != self.backend.serialized(schema, key):
                plan.settings[(schema, key)] = value
        def baseline(schema, keys=None):
            for key, value in base[schema].items():
                if keys is None or key in keys:
                    setting(schema, key, value, raw=True)
        def content(path, data):
            target = self.file_target(path)
            encoded = data.encode() if isinstance(data, str) else data
            if target.is_symlink() or not target.is_file() or target.read_bytes() != encoded:
                plan.files[path] = {'content': encoded}
        def asset(path, source, preserve_existing=False):
            target = self.file_target(path)
            if preserve_existing and target.is_dir():
                return
            if target.is_symlink():
                raise RuntimeError(f'An appearance directory is a symbolic link: {path}')
            receipt = load_json(self.state / 'assets.json', {})
            fingerprint = self.asset_manifest['sha256']
            if not target.exists() or receipt.get(path) != fingerprint:
                plan.files[path] = {'source': self.data / 'assets' / source}
        def spice(uuid, instance=None, updates=None, initial=False):
            if uuid not in self.templates:
                return
            filename = f'{instance}.json' if instance is not None else f'{uuid}.json'
            path = f'.config/cinnamon/spices/{uuid}/{filename}'
            target = self.file_target(path)
            existing = load_json(target, {})
            template = self.templates[uuid]
            result = copy.deepcopy(existing or template)
            if initial:
                for key, entry in template.items():
                    if isinstance(entry, dict) and 'value' in entry:
                        if key not in result:
                            result[key] = copy.deepcopy(entry)
                        result[key]['value'] = copy.deepcopy(entry['value'])
            for key, value in (updates or {}).items():
                if key in template:
                    result.setdefault(key, copy.deepcopy(template[key]))['value'] = value
            result.pop('__md5__', None)
            content(path, json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        # Refresh owned asset copies after a package update, without resetting values.
        if selected['application']: asset('.themes/Neo-Colloid-Nord', 'themes/Neo-Colloid-Nord')
        if selected['desktop']: asset('.themes/Neo-Vibrant-Teal-Dark', 'themes/Neo-Vibrant-Teal-Dark')
        if selected['icons']: asset('.local/share/icons/Neo-Reversal-green', 'icons/Neo-Reversal-green')
        if selected['dock']: asset('.local/share/plank/themes/Neo-BlackLight', 'plank/Neo-BlackLight')
        if fresh['application']:
            asset('.themes/Neo-Colloid-Nord', 'themes/Neo-Colloid-Nord')
            baseline(INTERFACE, ['gtk-theme'])
            for schema in ['org.nemo.preferences', 'org.nemo.icon-view', 'org.nemo.list-view', 'org.nemo.compact-view']:
                baseline(schema)
            baseline(GNOME, ['font-antialiasing', 'font-hinting', 'font-rgba-order'])
        if changed('application', 'app_font_size'):
            for schema in (INTERFACE, GNOME):
                setting(schema, 'font-name', f'Ubuntu {options["app_font_size"]:g}')
            setting(GNOME, 'document-font-name', f'Sans {options["app_font_size"]:g}')
        if changed('application', 'text_scale'):
            setting(INTERFACE, 'text-scaling-factor', options['text_scale'] / 100.0)
            if self.backend.has(GNOME, 'text-scaling-factor'):
                setting(GNOME, 'text-scaling-factor', options['text_scale'] / 100.0)
        if fresh['desktop']:
            asset('.themes/Neo-Vibrant-Teal-Dark', 'themes/Neo-Vibrant-Teal-Dark')
            baseline('org.cinnamon.theme')
            baseline(WM, ['theme', 'button-layout'])
        if changed('desktop', 'title_font_size'):
            setting(WM, 'titlebar-font', f'Ubuntu Medium {options["title_font_size"]:g}')
        if fresh['icons']:
            asset('.local/share/icons/Neo-Reversal-green', 'icons/Neo-Reversal-green')
            baseline(INTERFACE, ['icon-theme', 'cursor-theme'])
        if changed('icons', 'cursor_size'):
            setting(INTERFACE, 'cursor-size', int(options['cursor_size']))
        if selected['panel']:
            # This component always uses a top panel, including a re-apply after Restore.
            setting(CINNAMON, 'panels-enabled', [f'1:{self.primary_monitor}:top'])
        if selected['panel'] and (fresh['panel'] or changed('panel', 'show_system_monitor', 'show_temperature')):
            entries = json.loads(base[CINNAMON]['enabled-applets'].replace("'", '"')) if fresh['panel'] else self.backend.read(CINNAMON, 'enabled-applets')
            if fresh['panel']:
                # Keep the user's battery indicator and its instance/slot.
                # Other entries use free slots in the customized layout.
                power = [entry for entry in self.backend.read(CINNAMON, 'enabled-applets')
                         if entry.startswith('panel1:') and ':power@cinnamon.org:' in entry]
                occupied = {tuple(entry.split(':')[:3]) for entry in power}
                adjusted = []
                for entry in entries:
                    parts = entry.split(':')
                    while tuple(parts[:3]) in occupied:
                        parts[2] = str(int(parts[2]) + 1)
                    occupied.add(tuple(parts[:3]))
                    adjusted.append(':'.join(parts))
                entries = adjusted + power
            next_id = max(self.backend.read(CINNAMON, 'next-applet-id'), 25)
            for uuid, wanted, position in [(SYSTEM_MONITOR, options['show_system_monitor'], 'panel1:left:1'),
                                           (TEMPERATURE, options['show_temperature'] and self.temperature, 'panel1:left:2')]:
                if not wanted:
                    entries = [e for e in entries if not(e.startswith('panel1:') and f':{uuid}:' in e)]
                elif not any(e.startswith('panel1:') and f':{uuid}:' in e for e in entries):
                    entries.append(f'{position}:{uuid}:{next_id}')
                    next_id += 1
                if wanted:
                    asset(f'.local/share/cinnamon/applets/{uuid}', f'applets/{uuid}', preserve_existing=True)
            if options['show_temperature'] and not self.temperature:
                plan.warnings.append('No supported CPU temperature sensor was found. The temperature indicator is omitted.')
            if fresh['panel']:
                baseline(CINNAMON, ['panels-show-delay', 'panels-hide-delay', 'panel-zone-text-sizes'])
            setting(CINNAMON, 'enabled-applets', entries)
            setting(CINNAMON, 'next-applet-id', next_id)
            for entry in entries:
                parts = entry.split(':')
                if len(parts) == 5 and (fresh['panel'] or parts[3] in (SYSTEM_MONITOR, TEMPERATURE)):
                    # Temperature preferences are shared by the applet's implementation.
                    spice(parts[3], None if parts[3] == TEMPERATURE else parts[4], initial=fresh['panel'])
        if changed('panel', 'panel_height'):
            values = self.backend.read(CINNAMON, 'panels-height')
            values = [v for v in values if not v.startswith('1:')] + [f'1:{int(options["panel_height"])}']
            setting(CINNAMON, 'panels-height', values)
        if changed('panel', 'panel_visibility'):
            values = self.backend.read(CINNAMON, 'panels-autohide')
            values = [v for v in values if not v.startswith('1:')] + [f'1:{"true" if options["panel_visibility"] == "auto" else "false"}']
            setting(CINNAMON, 'panels-autohide', values)
        if changed('panel', 'left_icon_size', 'right_icon_size'):
            for key, symbolic in [('panel-zone-icon-sizes', False), ('panel-zone-symbolic-icon-sizes', True)]:
                data = json.loads(self.backend.read(CINNAMON, key))
                data = [p for p in data if p['panelId'] != 1] + [{'panelId': 1,
                    'left': int(options['left_icon_size']) - (1 if symbolic else 0),
                    'center': 28 if symbolic else 0,
                    'right': min(16, int(options['right_icon_size'])) if symbolic else int(options['right_icon_size'])}]
                setting(CINNAMON, key, json.dumps(data))
        if changed('panel', 'clock_24h', 'show_date', 'show_seconds'):
            for option, key in [('clock_24h', 'clock-use-24h'), ('show_date', 'clock-show-date'), ('show_seconds', 'clock-show-seconds')]:
                setting(INTERFACE, key, options[option])
            # Use the Cinnamon locale-aware clock, not an author's hard-coded date string.
            entries = self.backend.read(CINNAMON, 'enabled-applets')
            if (CINNAMON, 'enabled-applets') in plan.settings:
                from gi.repository import GLib
                entries = GLib.Variant.parse(None, plan.settings[(CINNAMON, 'enabled-applets')], None, None).unpack()
            for entry in entries:
                if entry.startswith('panel1:') and ':calendar@cinnamon.org:' in entry:
                    spice('calendar@cinnamon.org', entry.rsplit(':', 1)[1], {'use-custom-format': False}, initial=fresh['panel'])
        if selected['dock']:
            if fresh['dock']:
                asset('.local/share/plank/themes/Neo-BlackLight', 'plank/Neo-BlackLight')
                baseline(PLANK)
                existing = self.home / '.config/plank/dock1/launchers'
                if not existing.is_dir() or not any(existing.glob('*.dockitem')):
                    for desktop_id in ['nemo.desktop', 'org.gnome.Terminal.desktop', 'firefox.desktop']:
                        path = Path('/usr/share/applications') / desktop_id
                        if path.is_file():
                            name = desktop_id.removesuffix('.desktop') + '.dockitem'
                            content(f'.config/plank/dock1/launchers/{name}', '[PlankDockItemPreferences]\nLauncher=' + path.as_uri() + '\n')
            mapping = {'dock_icon_size': 'icon-size', 'dock_zoom': 'zoom-enabled', 'dock_zoom_amount': 'zoom-percent',
                       'dock_monitor': 'monitor'}
            for option, key in mapping.items():
                if changed('dock', option):
                    value=int(options[option]) if option in ('dock_icon_size','dock_zoom_amount') else options[option]
                    if option=='dock_monitor' and value and self.monitors is not None and value not in self.monitors:
                        value='';plan.warnings.append('The selected monitor is unavailable. The dock uses the primary display.')
                    setting(PLANK,key,value)
            if changed('dock', 'dock_autohide'):
                setting(PLANK, 'hide-mode', 'auto' if options['dock_autohide'] else 'none')
            if changed('dock', 'dock_hide_delay'):
                setting(PLANK, 'hide-delay', int(round(options['dock_hide_delay'] * 1000)))
            if changed('dock', 'dock_show_running'):
                setting(PLANK, 'pinned-only', not options['dock_show_running'])
            if changed('dock', 'dock_startup'):
                # Manage our own launcher only; do not duplicate a user's existing Plank startup entry.
                existing = []
                for p in (self.home / '.config/autostart').glob('*.desktop'):
                    if p.name != 'neo-mint-theme-plank.desktop':
                        text = p.read_text(errors='replace')
                        if 'Exec=plank' in text and 'Hidden=true' not in text and 'X-GNOME-Autostart-enabled=false' not in text:
                            existing.append(p)
                if not existing:
                    content('.config/autostart/neo-mint-theme-plank.desktop',
                        '[Desktop Entry]\nType=Application\nName=Plank Dock\nExec=plank\nIcon=plank\nTerminal=false\n'
                        f'X-GNOME-Autostart-enabled={str(options["dock_startup"]).lower()}\n')
                else:
                    # Preserve every field; only toggle the startup behavior requested by the user.
                    import re
                    for p in existing:
                        text = p.read_text()
                        line = f'X-GNOME-Autostart-enabled={str(options["dock_startup"]).lower()}'
                        text = re.sub(r'^X-GNOME-Autostart-enabled=.*$', line, text, flags=re.M) if 'X-GNOME-Autostart-enabled=' in text else text.rstrip() + '\n' + line + '\n'
                        content(str(p.relative_to(self.home)), text)
            plan.start_dock = fresh['dock'] or any(options[k] != previous[k] for k in options if k.startswith('dock_'))
        if selected['terminal']:
            schema = self.terminal_schema()
            if fresh['terminal']:
                for key, value in self.profile['terminal_appearance_gvariant'].items():
                    if self.backend.has(schema, key):
                        setting(schema, key, value, raw=True)
            if changed('terminal', 'terminal_font_size'):
                setting(schema, 'use-system-font', False)
                setting(schema, 'font', f'DejaVu Sans Mono {options["terminal_font_size"]:g}')
        if selected['effects']:
            enabled = self.backend.read(CINNAMON, 'enabled-extensions')
            for option, uuid in [('transparent_panel', TRANSPARENT), ('window_effects', COMPIZ)]:
                if changed('effects', option):
                    if options[option]:
                        asset(f'.local/share/cinnamon/extensions/{uuid}', f'extensions/{uuid}', preserve_existing=True)
                        if uuid not in enabled: enabled.append(uuid)
                        spice(uuid, initial=fresh['effects'])
                    else:
                        enabled = [e for e in enabled if e != uuid]
            setting(CINNAMON, 'enabled-extensions', enabled)
        if options['wallpaper_mode'] == 'choose' and (options['wallpaper_path'] != previous['wallpaper_path'] or
                options['wallpaper_mode'] != previous['wallpaper_mode'] or options['image_fit'] != previous['image_fit']):
            source = Path(options['wallpaper_path']).expanduser().resolve()
            name = hashlib.sha256(source.read_bytes()).hexdigest()[:16] + source.suffix.lower()
            path = f'.local/share/neo-mint-theme/wallpapers/{name}'
            content(path, source.read_bytes())
            setting(BACKGROUND, 'picture-uri', self.file_target(path).as_uri())
            setting(BACKGROUND, 'picture-options', options['image_fit'])
        elif options['wallpaper_mode'] == 'choose' and options['image_fit'] != previous['image_fit']:
            setting(BACKGROUND, 'picture-options', options['image_fit'])
        plan.installed_components = sorted(installed | {c for c in COMPONENTS if selected[c]})
        return plan

    def read_current(self):
        prefs = self.preferences()
        values = copy.deepcopy(prefs['options'])
        if not prefs['installed_components']:
            return values
        def read(s, k, default):
            return self.backend.read(s, k) if self.backend.has(s, k) else default
        def size(value, default):
            try: return float(value.rsplit(' ', 1)[1])
            except (ValueError, IndexError): return default
        values['app_font_size'] = size(read(INTERFACE, 'font-name', ''), values['app_font_size'])
        values['title_font_size'] = size(read(WM, 'titlebar-font', ''), values['title_font_size'])
        try:
            schema = self.terminal_schema()
            font = self.backend.read(GNOME, 'monospace-font-name') if self.backend.read(schema, 'use-system-font') else self.backend.read(schema, 'font')
            values['terminal_font_size'] = size(font, values['terminal_font_size'])
        except (ValueError, RuntimeError): pass
        values['text_scale'] = round(read(INTERFACE, 'text-scaling-factor', 1.0) * 100)
        values['cursor_size'] = read(INTERFACE, 'cursor-size', 24)
        for option, key in [('clock_24h', 'clock-use-24h'), ('show_date', 'clock-show-date'), ('show_seconds', 'clock-show-seconds')]:
            values[option] = read(INTERFACE, key, values[option])
        for option, key in [('panel_height', 'panels-height'), ('panel_visibility', 'panels-autohide')]:
            for entry in read(CINNAMON, key, []):
                if entry.startswith('1:'):
                    v = entry.split(':')[1]
                    values[option] = int(v) if option == 'panel_height' else ('auto' if v == 'true' else 'always')
        data = json.loads(read(CINNAMON, 'panel-zone-icon-sizes', '[]'))
        for panel in data:
            if panel['panelId'] == 1:
                values['left_icon_size'] = panel['left']; values['right_icon_size'] = panel['right']
        entries = read(CINNAMON, 'enabled-applets', [])
        values['show_system_monitor'] = any(e.startswith('panel1:') and f':{SYSTEM_MONITOR}:' in e for e in entries)
        values['show_temperature'] = any(e.startswith('panel1:') and f':{TEMPERATURE}:' in e for e in entries)
        for option, key in [('dock_icon_size', 'icon-size'), ('dock_zoom', 'zoom-enabled'), ('dock_zoom_amount', 'zoom-percent'), ('dock_monitor', 'monitor')]:
            values[option] = read(PLANK, key, values[option])
        values['dock_autohide'] = read(PLANK, 'hide-mode', 'auto') != 'none'
        values['dock_hide_delay'] = read(PLANK, 'hide-delay', 1250) / 1000
        values['dock_show_running'] = not read(PLANK, 'pinned-only', False)
        startup_files=list((self.home/'.config/autostart').glob('*.desktop'))
        plank_entries=[p.read_text(errors='replace') for p in startup_files if 'Exec=plank' in p.read_text(errors='replace')]
        if plank_entries:
            values['dock_startup']=any('Hidden=true' not in t and 'X-GNOME-Autostart-enabled=false' not in t for t in plank_entries)
        entries = read(CINNAMON, 'enabled-extensions', [])
        values['transparent_panel'] = TRANSPARENT in entries
        values['window_effects'] = COMPIZ in entries
        if values['wallpaper_mode'] == 'choose':
            from urllib.parse import unquote, urlparse
            uri = read(BACKGROUND, 'picture-uri', '')
            if uri.startswith('file:'):
                values['wallpaper_path'] = unquote(urlparse(uri).path)
            values['image_fit'] = read(BACKGROUND, 'picture-options', 'zoom')
        owners={'application':['app_font_size','text_scale'], 'desktop':['title_font_size'],
                'icons':['cursor_size'], 'terminal':['terminal_font_size'],
                'panel':['panel_height','left_icon_size','right_icon_size','panel_visibility','clock_24h','show_date','show_seconds','show_system_monitor','show_temperature'],
                'dock':[k for k in DEFAULTS if k.startswith('dock_')],
                'effects':['transparent_panel','window_effects']}
        for component,keys in owners.items():
            if component not in prefs['installed_components']:
                for key in keys:values[key]=prefs['options'][key]
        return values

    def _snapshot_files(self, paths, directory):
        result = {}
        for relative in paths:
            target = self.file_target(relative)
            key = hashlib.sha256(relative.encode()).hexdigest()
            existed = target.exists() or target.is_symlink()
            result[relative] = {'exists': existed, 'backup': key}
            if existed:
                copy_path(target, directory / key)
        return result

    def _restore_files(self, records, directory):
        for relative, record in records.items():
            target = self.file_target(relative)
            remove_path(target)
            if record['exists']:
                copy_path(directory / record['backup'], target)

    def _publish_settings(self, entries, restore=False):
        # Move existing panels before changing applets. Publishing these keys
        # together delivers enabled-applets first, disposing actors still used
        # by the panel's orientation update.
        panels = [entry for entry in entries if (entry['schema'], entry['key']) == (CINNAMON, 'panels-enabled')]
        others = [entry for entry in entries if entry not in panels]
        def write(items):
            for entry in items:
                if restore:
                    self.backend.restore(entry['schema'], entry['key'], entry['before'])
                else:
                    self.backend.write(entry['schema'], entry['key'], entry['value'])
        if panels:
            self.backend.commit(lambda: write(panels))
            if self.desktop is not None:
                self.desktop.wait_for_panels(self.backend.read(CINNAMON, 'panels-enabled'))
        self.backend.commit(lambda: write(others))

    def _restore_records(self, journal, directory):
        self._restore_files(journal['files'], directory / 'files')
        # Restore file-backed applet settings before re-enabling their original instances.
        self._publish_settings(list(reversed(journal['settings'])), restore=True)
        for filename in ('preferences.json', 'assets.json'):
            previous = journal.get(filename)
            p = self.state / filename
            if previous is None:
                if p.exists(): p.unlink()
            else: atomic_json(p, previous)

    def _lock(self):
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state.chmod(0o700)
        handle = (self.state / 'lock').open('a')
        try: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close(); raise RuntimeError('Another appearance operation is in progress.')
        return handle

    def _pending(self):
        return [p for p in (self.state / 'transactions').glob('*/journal.json') if load_json(p)['status'] in ('prepared', 'applying', 'restoring')]

    def apply(self, plan, environment=True, launch=True):
        if environment: self.check_environment()
        with self._lock():
            if self._pending():
                raise RuntimeError('An interrupted operation needs recovery. Use Recover Interrupted Apply first.')
            original_path=self.state/'original/journal.json'
            if original_path.exists() and load_json(original_path)['status']=='restoring-original':
                raise RuntimeError('An interrupted restore needs recovery. Use Recover Interrupted Apply first.')
            if not plan.settings and not plan.files:
                paths = load_json(original_path, {}).get('files', {})
                warnings = list(plan.warnings)
                if environment:
                    warnings.extend(self._refresh_desktop(paths))
                return {'message': 'The selected appearance is already applied.', 'warnings': warnings}
            ident = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '-' + uuid.uuid4().hex[:8]
            directory = self.state / 'transactions' / ident
            directory.mkdir(parents=True)
            before_settings = [{'schema': s, 'key': k, 'before': self.backend.snapshot(s, k)} for s, k in plan.settings]
            journal = {'status': 'prepared', 'created': ident, 'settings': before_settings,
                       'files': self._snapshot_files(plan.files, directory / 'files'),
                       'preferences.json': load_json(self.state / 'preferences.json'),
                       'assets.json': load_json(self.state / 'assets.json')}
            atomic_json(directory / 'journal.json', journal)
            # Original state grows only for newly touched files/keys; never a whole dconf dump.
            original_dir = self.state / 'original'
            original = load_json(original_dir / 'journal.json', {'status': 'original', 'files': {}, 'settings': [], 'preferences.json': None, 'assets.json': None})
            known = {(e['schema'], e['key']) for e in original['settings']}
            for entry in before_settings:
                if (entry['schema'], entry['key']) not in known: original['settings'].append(entry)
            for relative, record in journal['files'].items():
                if relative not in original['files']:
                    original['files'][relative] = record
                    if record['exists']:
                        copy_path(directory / 'files' / record['backup'], original_dir / 'files' / record['backup'])
            atomic_json(original_dir / 'journal.json', original)
            try:
                journal['status'] = 'applying'; atomic_json(directory / 'journal.json', journal)
                for relative, action in plan.files.items():
                    target = self.file_target(relative)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if 'source' in action:
                        staged = Path(tempfile.mkdtemp(prefix='.neo-theme-', dir=target.parent)) / 'asset'
                        try:
                            copy_path(action['source'], staged)
                            remove_path(target)
                            os.replace(staged, target)
                        finally:
                            shutil.rmtree(staged.parent)
                    else:
                        fd, name = tempfile.mkstemp(prefix='.neo-theme-', dir=target.parent)
                        try:
                            with os.fdopen(fd, 'wb') as f:
                                f.write(action['content']); f.flush(); os.fsync(f.fileno())
                            os.chmod(name, 0o644)
                            os.replace(name, target)
                        finally:
                            if Path(name).exists(): Path(name).unlink()
                self._publish_settings([{'schema': schema, 'key': key, 'value': value}
                                        for (schema, key), value in plan.settings.items()])
                for (schema, key), value in plan.settings.items():
                    if self.backend.serialized(schema, key) != value:
                        raise RuntimeError(f'The desktop did not retain the new value for {key}.')
                prefs = {'options': plan.options, 'installed_components': plan.installed_components}
                atomic_json(self.state / 'preferences.json', prefs)
                receipt = load_json(self.state / 'assets.json', {})
                for path, action in plan.files.items():
                    if 'source' in action: receipt[path] = self.asset_manifest['sha256']
                atomic_json(self.state / 'assets.json', receipt)
                journal['status'] = 'complete'; atomic_json(directory / 'journal.json', journal)
            except Exception as exc:
                try:
                    self._restore_records(journal, directory)
                    journal['status'] = 'failed-restored'; atomic_json(directory / 'journal.json', journal)
                    if environment: self._refresh_desktop(journal['files'])
                except Exception as rollback:
                    raise RuntimeError(f'Apply failed and needs recovery: {exc}. Recovery error: {rollback}') from exc
                raise RuntimeError(f'Apply failed. Previous appearance was restored. {exc}') from exc
            warnings = list(plan.warnings)
            if environment:
                warnings.extend(self._refresh_desktop(plan.files))
            if launch and plan.start_dock:
                try: self._start_dock()
                except OSError: warnings.append('The appearance was applied, but Plank could not be started. Open Plank from the menu.')
            return {'message': 'Appearance applied.', 'warnings': warnings}

    def _start_dock(self):
        # Only record a process launched by this application; never kill an existing dock.
        for p in Path('/proc').iterdir():
            if not p.name.isdigit(): continue
            try:
                if p.stat().st_uid == os.getuid() and (p / 'comm').read_text().strip() == 'plank': return
            except OSError: pass
        with (self.state / 'plank.log').open('a') as log:
            p = subprocess.Popen(['plank'], stdout=log, stderr=log, start_new_session=True)
        try:
            start = (Path('/proc') / str(p.pid) / 'stat').read_text().split()[21]
            atomic_json(self.state / 'dock-process.json', {'pid': p.pid, 'start': start})
        except OSError: pass

    def _stop_owned_dock(self):
        info = load_json(self.state / 'dock-process.json')
        if not info: return
        p = Path('/proc') / str(info['pid'])
        try:
            if p.stat().st_uid == os.getuid() and (p/'comm').read_text().strip() == 'plank' and (p/'stat').read_text().split()[21] == info['start']:
                os.kill(info['pid'], signal.SIGTERM)
        except OSError: pass
        (self.state / 'dock-process.json').unlink(missing_ok=True)

    def can_undo(self):
        return any(load_json(p)['status'] == 'complete' for p in (self.state / 'transactions').glob('*/journal.json'))

    def can_restore(self):
        return (self.state / 'original/journal.json').exists()

    def original_pending(self):
        path=self.state/'original/journal.json'
        return path.exists() and load_json(path)['status']=='restoring-original'

    def undo(self):
        with self._lock():
            if self._pending() or self.original_pending(): raise RuntimeError('Recover the interrupted operation first.')
            entries = sorted(p for p in (self.state/'transactions').glob('*/journal.json') if load_json(p)['status'] == 'complete')
            if not entries: raise RuntimeError('There is no completed Apply to undo.')
            path = entries[-1]; journal = load_json(path)
            journal['status'] = 'restoring'; atomic_json(path, journal)
            self._restore_records(journal, path.parent)
            journal['status'] = 'undone'; atomic_json(path, journal)
            if journal['preferences.json'] is None: self._stop_owned_dock()
            return {'message': 'The last Apply was undone.', 'warnings': self._refresh_desktop(journal['files'])}

    def restore_original(self):
        with self._lock():
            if self._pending() or self.original_pending(): raise RuntimeError('Recover the interrupted operation first.')
            original_dir = self.state/'original'; path = original_dir/'journal.json'
            if not path.exists(): raise RuntimeError('No original appearance has been saved yet.')
            original = load_json(path)
            original['status'] = 'restoring-original'; atomic_json(path, original)
            self._restore_records(original, original_dir)
            self._stop_owned_dock()
            for p in (self.state/'transactions').glob('*/journal.json'):
                j=load_json(p)
                if j['status'] == 'complete': j['status']='original-restored'; atomic_json(p,j)
            original['status'] = 'restored-original'; atomic_json(path,original)
            return {'message': 'Your original appearance was restored.', 'warnings': self._refresh_desktop(original['files'])}

    def recover(self):
        with self._lock():
            paths = set()
            for path in self._pending():
                j=load_json(path); self._restore_records(j,path.parent)
                paths.update(j['files'])
                j['status']='recovered'; atomic_json(path,j)
                if j['preferences.json'] is None:self._stop_owned_dock()
            original = self.state/'original/journal.json'
            if original.exists() and load_json(original)['status'] == 'restoring-original':
                j=load_json(original); self._restore_records(j,original.parent)
                paths.update(j['files'])
                j['status']='restored-original'; atomic_json(original,j)
                self._stop_owned_dock()
                for p in (self.state/'transactions').glob('*/journal.json'):
                    transaction=load_json(p)
                    if transaction['status']=='complete':
                        transaction['status']='original-restored';atomic_json(p,transaction)
            return {'message': 'Interrupted changes were recovered.', 'warnings': self._refresh_desktop(paths)}
