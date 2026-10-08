"""Integration tests with real schemas and an isolated memory settings backend."""
from pathlib import Path
import copy
import hashlib
import json
import tempfile
import unittest
from unittest.mock import patch

from gi.repository import Gio, GLib
from neo_mint_theme.core import Engine, DEFAULTS, COMPONENTS, INTERFACE, GNOME, CINNAMON, PLANK, BACKGROUND
from neo_mint_theme.settings import SettingsBackend

ROOT = Path(__file__).resolve().parents[1]


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'home'
        self.home.mkdir()
        self.backend = SettingsBackend(Gio.memory_settings_backend_new())
        self.engine = Engine(ROOT/'data',self.backend,home=self.home,state=Path(self.temp.name)/'state',temperature=True)
        self.original = {s:{k:self.backend.snapshot(s,k) for k in v if self.backend.has(s,k)}
                         for s,v in self.engine.profile['settings_gvariant'].items()}

    def options(self, *selected):
        o=copy.deepcopy(DEFAULTS)
        o['components']={c:c in selected for c in COMPONENTS}
        return o

    def apply(self,o,current=None):
        p=self.engine.build_plan(o,current)
        self.engine.apply(p,environment=False,launch=False)
        return p

    def test_complete_install_repeat_and_restore(self):
        self.apply(copy.deepcopy(DEFAULTS))
        self.assertEqual(self.backend.read(INTERFACE,'gtk-theme'),'Neo-Colloid-Nord')
        self.assertEqual(self.backend.read(INTERFACE,'clock-show-seconds'),True)
        self.assertTrue((self.home/'.themes/Neo-Colloid-Nord/gtk-3.0/gtk.css').is_file())
        self.assertEqual(self.engine.build_plan(self.engine.read_current(),self.engine.read_current()).summary()['change_count'],0)
        self.engine.restore_original()
        for s,values in self.original.items():
            for k,v in values.items():self.assertEqual(self.backend.snapshot(s,k),v,(s,k))
        self.assertFalse((self.home/'.themes/Neo-Colloid-Nord').exists())
        self.assertFalse(self.engine.can_undo())

    def test_resize_dock_does_not_reset_panel_or_wallpaper(self):
        o=self.options('dock');self.apply(o)
        self.backend.write(BACKGROUND,'picture-uri',"'file:///tmp/untouched.jpg'")
        self.backend.write(CINNAMON,'panels-height',"['1:42']")
        before=self.engine.read_current();o=copy.deepcopy(before);o['dock_icon_size']=60
        p=self.apply(o,before)
        self.assertEqual(set(p.settings),{(PLANK,'icon-size')})
        self.assertEqual(self.backend.read(CINNAMON,'panels-height'),['1:42'])
        self.assertEqual(self.backend.read(BACKGROUND,'picture-uri'),'file:///tmp/untouched.jpg')
        self.engine.undo();self.assertEqual(self.backend.read(PLANK,'icon-size'),44)

    def test_restore_then_reapply_in_worker(self):
        import threading
        self.backend.write(CINNAMON, 'panels-enabled', "['1:0:bottom']")
        before = self.backend.snapshot(CINNAMON, 'panels-enabled')
        errors = []
        def run(method):
            def worker():
                try: method()
                except Exception as exc: errors.append(exc)
            thread = threading.Thread(target=worker)
            thread.start();thread.join()
            if errors: raise errors[0]
        for _ in range(3):
            options = copy.deepcopy(DEFAULTS)
            plan = self.engine.build_plan(options, self.engine.read_current())
            run(lambda: self.engine.apply(plan, environment=False, launch=False))
            self.assertEqual(self.backend.read(CINNAMON, 'panels-enabled'), ['1:0:top'])
            self.assertIn('panel1:left:0:menu@cinnamon.org:0', self.backend.read(CINNAMON, 'enabled-applets'))
            menu = json.loads((self.home/'.config/cinnamon/spices/menu@cinnamon.org/0.json').read_text())
            self.assertEqual(menu['menu-icon']['value'], 'applications-all-symbolic')
            self.assertTrue(menu['menu-custom']['value'])
            run(self.engine.restore_original)
            self.assertEqual(self.backend.snapshot(CINNAMON, 'panels-enabled'), before)
            self.assertEqual(self.engine.preferences()['installed_components'], [])

    def test_reapply_repairs_panel_position_without_resetting_sizes(self):
        self.apply(self.options('panel'))
        self.backend.write(CINNAMON, 'panels-enabled', "['1:0:bottom']")
        self.backend.write(CINNAMON, 'panels-height', "['1:42']")
        current = self.engine.read_current()
        plan = self.apply(current, current)
        self.assertEqual(set(plan.settings), {(CINNAMON, 'panels-enabled')})
        self.assertEqual(self.backend.read(CINNAMON, 'panels-enabled'), ['1:0:top'])
        self.assertEqual(self.backend.read(CINNAMON, 'panels-height'), ['1:42'])

    def test_related_keys_are_published_together(self):
        settings = self.backend.settings(CINNAMON)
        seen = []
        settings.connect('changed', lambda s, key: seen.append((
            s.get_strv('panels-enabled'), s.get_strv('enabled-applets'))))
        layout = ['panel1:left:0:menu@cinnamon.org:0']
        with self.backend.batch():
            self.backend.write(CINNAMON, 'panels-enabled', "['1:0:top']")
            self.backend.write(CINNAMON, 'enabled-applets', "['panel1:left:0:menu@cinnamon.org:0']")
            self.assertEqual(seen, [])
        context = GLib.MainContext.default()
        while context.pending(): context.iteration(False)
        self.assertTrue(seen)
        self.assertTrue(all(panels == ['1:0:top'] and applets == layout for panels, applets in seen))

    def test_live_applet_notification_uses_saved_values(self):
        from neo_mint_theme.desktop import CinnamonSession
        from unittest.mock import Mock
        connection = Mock()
        desktop = CinnamonSession(connection)
        desktop.wait_for_panels = Mock()
        self.engine.desktop = desktop
        # Environment checks are stubbed; both settings and home remain isolated.
        plan = self.engine.build_plan(self.options('panel'))
        with patch.object(self.engine, 'check_environment'):
            self.engine.apply(plan, environment=True, launch=False)
        calls = [call.args for call in connection.call_sync.call_args_list]
        self.assertTrue(any(args[3] == 'updateSetting' and args[4].unpack()[:2] == ('menu@cinnamon.org', '0') for args in calls))
        self.assertTrue(all(args[0:3] == ('org.Cinnamon', '/org/Cinnamon', 'org.Cinnamon') for args in calls))
        connection.reset_mock()
        self.engine.restore_original()
        # The original memory backend has no enabled applets, so do not notify stale instances.
        connection.call_sync.assert_not_called()

    def test_power_applet_instance_and_slot_survive_apply_restore(self):
        from neo_mint_theme.settings import variant
        power = 'panel1:right:9:power@cinnamon.org:12'
        self.backend.write(CINNAMON, 'enabled-applets', variant([power]))
        for _ in range(2):
            self.apply(self.options('panel'))
            entries = self.backend.read(CINNAMON, 'enabled-applets')
            self.assertIn(power, entries)
            self.assertEqual(len({tuple(e.split(':')[:3]) for e in entries}), len(entries))
            self.engine.restore_original()
            self.assertEqual(self.backend.read(CINNAMON, 'enabled-applets'), [power])

    def test_live_panel_moves_before_applet_replacement(self):
        from unittest.mock import Mock
        desktop = Mock()
        self.engine.desktop = desktop
        self.backend.write(CINNAMON, 'panels-enabled', "['1:0:bottom']")
        original = self.backend.read(CINNAMON, 'enabled-applets')
        observations = []
        desktop.wait_for_panels.side_effect = lambda entries: observations.append(
            (entries, self.backend.read(CINNAMON, 'enabled-applets')))
        self.apply(self.options('panel'))
        self.assertEqual(observations[0], (['1:0:top'], original))
        applied = self.backend.read(CINNAMON, 'enabled-applets')
        self.engine.restore_original()
        self.assertEqual(observations[1], (['1:0:bottom'], applied))

    def test_gtk_commit_dispatch_runs_on_main_thread(self):
        import threading
        owner = threading.get_ident()
        writers = []
        original = self.backend.write
        def record(*args):
            writers.append(threading.get_ident())
            return original(*args)
        def dispatch(callback):
            complete = threading.Event()
            def run():
                callback();complete.set();return False
            GLib.idle_add(run)
            self.assertTrue(complete.wait(5))
        self.backend.dispatch = dispatch
        plan = self.engine.build_plan(self.options('panel'))
        errors = []
        def worker():
            try: self.engine.apply(plan, environment=False, launch=False)
            except Exception as exc: errors.append(exc)
        with patch.object(self.backend, 'write', side_effect=record):
            thread = threading.Thread(target=worker);thread.start()
            context = GLib.MainContext.default()
            while thread.is_alive():
                context.iteration(False)
                thread.join(.01)
        self.assertEqual(errors, [])
        self.assertTrue(writers)
        self.assertEqual(set(writers), {owner})

    def test_missing_cinnamon_is_rejected(self):
        from neo_mint_theme.desktop import CinnamonSession
        from unittest.mock import Mock
        connection = Mock()
        connection.call_sync.return_value = GLib.Variant('(b)', (False,))
        with self.assertRaisesRegex(RuntimeError, 'Cinnamon is not running'):
            CinnamonSession(connection).require_running()

    def test_applet_refresh_retries_loading_instances_and_continues(self):
        from neo_mint_theme.desktop import CinnamonSession
        from unittest.mock import Mock
        self.apply(self.options('panel'))
        connection = Mock()
        connection.call_sync.side_effect = [GLib.Error('instance still loading'), None] + [None]*30
        session = CinnamonSession(connection)
        session.refresh(self.home, {'.config/cinnamon/spices/menu@cinnamon.org/0.json',
                                  '.config/cinnamon/spices/calendar@cinnamon.org/13.json'}, self.backend)
        self.assertEqual(connection.call_sync.call_count, 3)

    def test_failed_refresh_preserves_applied_appearance(self):
        from unittest.mock import Mock
        self.engine.desktop = Mock()
        self.engine.desktop.refresh.side_effect = OSError('disconnected desktop')
        plan = self.engine.build_plan(self.options('panel'))
        with patch.object(self.engine, 'check_environment'):
            result = self.engine.apply(plan, environment=True, launch=False)
        self.assertEqual(self.backend.read(CINNAMON, 'panels-enabled'), ['1:0:top'])
        self.assertTrue(result['warnings'])
        self.assertTrue(self.engine.can_undo())

    def test_failure_restores_files_and_unset_keys(self):
        path=self.home/'.themes/Neo-Colloid-Nord';path.mkdir(parents=True)
        (path/'original.txt').write_text('preserve me')
        options=self.options('application');options['app_font_size']=14
        plan=self.engine.build_plan(options)
        old=self.backend.write
        failed=False
        def fail_once(s,k,v):
            nonlocal failed
            if k=='font-name' and not failed:
                failed=True;raise OSError('simulated interruption')
            return old(s,k,v)
        with patch.object(self.backend,'write',side_effect=fail_once):
            with self.assertRaisesRegex(RuntimeError,'Previous appearance was restored'):
                self.engine.apply(plan,environment=False,launch=False)
        self.assertEqual((path/'original.txt').read_text(),'preserve me')
        self.assertIsNone(self.backend.snapshot(INTERFACE,'gtk-theme')['user'])
        self.assertEqual(self.engine._pending(),[])

    def test_unselected_components_keep_package_defaults(self):
        self.backend.write(INTERFACE,'font-name',"'Ubuntu 15'")
        self.apply(self.options('dock'))
        self.assertEqual(self.engine.read_current()['app_font_size'],10)
        self.assertEqual(self.backend.read(INTERFACE,'font-name'),'Ubuntu 15')

    def test_second_panel_indicators_are_preserved(self):
        self.apply(self.options('panel'))
        extra='panel2:left:0:sysmonitor@orcus:50'
        entries=self.backend.read(CINNAMON,'enabled-applets')+[extra]
        from neo_mint_theme.settings import variant
        self.backend.write(CINNAMON,'enabled-applets',variant(entries))
        current=self.engine.read_current();o=copy.deepcopy(current);o['show_system_monitor']=False
        self.apply(o,current)
        entries=self.backend.read(CINNAMON,'enabled-applets')
        self.assertIn(extra,entries)
        self.assertFalse(any(e.startswith('panel1:') and 'sysmonitor@orcus' in e for e in entries))

    def test_asset_upgrade_preserves_chosen_sizes(self):
        o=self.options('dock');o['dock_icon_size']=63;self.apply(o)
        current=self.engine.read_current()
        self.engine.asset_manifest=copy.deepcopy(self.engine.asset_manifest)
        self.engine.asset_manifest['sha256']='new-theme-version'
        p=self.engine.build_plan(current,current)
        self.assertIn('.local/share/plank/themes/Neo-BlackLight',p.files)
        self.assertNotIn((PLANK,'icon-size'),p.settings)
        self.engine.apply(p,environment=False,launch=False)
        self.assertEqual(self.backend.read(PLANK,'icon-size'),63)

    def test_interrupted_original_restore_requires_recovery(self):
        self.apply(self.options('effects'))
        p=self.engine.state/'original/journal.json'
        j=json.loads(p.read_text());j['status']='restoring-original';p.write_text(json.dumps(j))
        with self.assertRaisesRegex(RuntimeError,'Recover'):self.engine.undo()
        self.engine.recover()
        self.assertFalse(self.engine.can_undo())
        self.assertEqual(self.backend.read(CINNAMON,'enabled-extensions'),[])

    def test_missing_temperature_sensor(self):
        self.engine.temperature=False
        o=self.options('panel');p=self.apply(o)
        self.assertFalse(any('temperature@fevimu' in e for e in self.backend.read(CINNAMON,'enabled-applets')))
        self.assertTrue(p.warnings)

    def test_partial_components_and_external_extensions(self):
        self.backend.write(CINNAMON,'enabled-extensions',"['other@example.org']")
        o=self.options('effects');o['window_effects']=False;self.apply(o)
        self.assertIn('other@example.org',self.backend.read(CINNAMON,'enabled-extensions'))
        self.assertFalse((self.home/'.themes/Neo-Colloid-Nord').exists())
        self.engine.undo()
        self.assertEqual(self.backend.read(CINNAMON,'enabled-extensions'),['other@example.org'])

    def test_existing_startup_file_is_not_duplicated(self):
        p=self.home/'.config/autostart/plank.desktop';p.parent.mkdir(parents=True)
        text='[Desktop Entry]\nType=Application\nExec=plank\nName=Existing dock\n'
        p.write_text(text)
        o=self.options('dock');o['dock_startup']=False;self.apply(o)
        self.assertFalse((p.parent/'neo-mint-theme-plank.desktop').exists())
        self.assertIn('X-GNOME-Autostart-enabled=false',p.read_text())
        self.engine.undo();self.assertEqual(p.read_text(),text)

    def test_wallpaper_with_spaces_and_keep_current(self):
        from gi.repository import GdkPixbuf
        image=self.home/'a picture.png'
        pix=GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB,False,8,4,4);pix.fill(0x00bb88ff);pix.savev(str(image),'png',[],[])
        before=self.backend.snapshot(BACKGROUND,'picture-uri')
        o=self.options();o['wallpaper_mode']='choose';o['wallpaper_path']=str(image)
        self.apply(o)
        self.assertTrue(self.backend.read(BACKGROUND,'picture-uri').startswith(self.home.as_uri()))
        current=self.engine.read_current();o=copy.deepcopy(current);o['wallpaper_mode']='keep'
        self.assertNotIn((BACKGROUND,'picture-uri'),self.engine.build_plan(o,current).settings)
        self.engine.undo();self.assertEqual(self.backend.snapshot(BACKGROUND,'picture-uri'),before)
        self.assertTrue(image.exists())

    def test_symbolic_link_parent_cannot_modify_outside_home(self):
        outside=Path(self.temp.name)/'outside';outside.mkdir()
        (self.home/'.themes').symlink_to(outside)
        with self.assertRaisesRegex(RuntimeError,'symbolic-link directory'):
            self.engine.build_plan(self.options('application'))
        self.assertEqual(list(outside.iterdir()),[])

    def test_validation_before_changes(self):
        o=self.options('dock');o['dock_icon_size']=900
        with self.assertRaises(ValueError):self.engine.build_plan(o)
        self.assertFalse(self.engine.state.exists())

    def test_recovery_after_journal_interruption(self):
        o=self.options('effects');self.apply(o)
        path=next((self.engine.state/'transactions').glob('*/journal.json'))
        j=json.loads(path.read_text());j['status']='applying';path.write_text(json.dumps(j))
        with self.assertRaisesRegex(RuntimeError,'interrupted'):
            self.engine.apply(self.engine.build_plan(self.options('dock')),environment=False,launch=False)
        self.engine.recover()
        self.assertFalse((self.home/'.local/share/cinnamon/extensions/transparent-panels@germanfr').exists())
        self.assertEqual(self.engine._pending(),[])


if __name__=='__main__':unittest.main()
