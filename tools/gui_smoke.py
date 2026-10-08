#!/usr/bin/python3
"""Exercise the real GTK window against an isolated settings backend and home."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import copy
import json
import tempfile
import time
from unittest.mock import patch
import gi
gi.require_version('Gtk','3.0')
gi.require_version('Gdk','3.0')
from gi.repository import Gtk,Gdk,GLib,Gio
from neo_mint_theme.core import Engine,DEFAULTS
from neo_mint_theme.settings import SettingsBackend
from neo_mint_theme.gui import Application,Window

root=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='neo-gui-test-') as temp:
    home=Path(temp)/'home';home.mkdir()
    engine=Engine(root/'data',SettingsBackend(Gio.memory_settings_backend_new()),home=home,state=Path(temp)/'state',temperature=False)
    app=Application(engine,preview=True);app.register(None)
    window=Window(app,engine,preview=True)
    window.stack.set_transition_type(Gtk.StackTransitionType.NONE)
    window.present()
    while Gtk.events_pending():Gtk.main_iteration()
    assert len(window.controls)==len(DEFAULTS)-1+7
    assert not window.apply_button.get_sensitive()
    assert not window.rows['Undo Last Apply'].get_sensitive()
    pages=['overview','wallpaper','text','panel','dock','effects','restore']
    for name in pages:
        window.stack.set_visible_child_name(name)
        while Gtk.events_pending():Gtk.main_iteration()
    window.controls['wallpaper_mode'].set_active_id('choose')
    assert window.rows['wallpaper_path'].get_sensitive()
    window.controls['wallpaper_mode'].set_active_id('keep')
    assert not window.rows['wallpaper_path'].get_sensitive()
    window.controls['component_dock'].set_active(False)
    assert not window.rows['dock_icon_size'].get_sensitive()
    window.reset_defaults(None)
    assert window.values()==DEFAULTS
    # Close the review dialog through GTK's event loop, not keyboard automation.
    def close_review():
        for item in Gtk.Window.list_toplevels():
            if isinstance(item,Gtk.Dialog):item.response(Gtk.ResponseType.CANCEL)
        return False
    GLib.timeout_add(150,close_review)
    window.show_plan(False)
    assert not engine.state.exists()
    window.stack.set_visible_child_name('overview')
    for _ in range(8):
        while Gtk.events_pending():Gtk.main_iteration()
        time.sleep(.03)
    # Screenshot only this application's window for visual inspection.
    drawable=window.get_window()
    width,height=drawable.get_width(),drawable.get_height()
    image=Gdk.pixbuf_get_from_window(drawable,0,0,width,height)
    out=root/'work';out.mkdir(exist_ok=True)
    image.savev(str(out/'gui-overview.png'),'png',[],[])
    window.stack.set_visible_child_name('text')
    for _ in range(8):
        while Gtk.events_pending():Gtk.main_iteration()
        time.sleep(.03)
    image=Gdk.pixbuf_get_from_window(drawable,0,0,width,height)
    image.savev(str(out/'gui-text.png'),'png',[],[])
    # Exercise the worker-thread/GTK completion bridge with actual file operations,
    # but only against this temporary home and the isolated settings backend.
    plan=engine.build_plan(copy.deepcopy(DEFAULTS))
    window.run_operation(lambda:engine.apply(plan,environment=False,launch=False))
    for _ in range(200):
        while Gtk.events_pending():Gtk.main_iteration()
        if not window.busy:break
        time.sleep(.03)
    assert not window.busy and engine.can_undo()
    assert window.controls['dock_icon_size'].get_value()==44
    window.run_operation(engine.undo)
    for _ in range(200):
        while Gtk.events_pending():Gtk.main_iteration()
        if not window.busy:break
        time.sleep(.03)
    assert not window.busy and not engine.can_undo()
    # Repeat the owner's failing Restore -> Apply flow in the same open window.
    for _ in range(2):
        plan=engine.build_plan(window.values(),window.previous)
        window.run_operation(lambda:engine.apply(plan,environment=False,launch=False))
        for _ in range(300):
            while Gtk.events_pending():Gtk.main_iteration()
            if not window.busy:break
            time.sleep(.03)
        assert not window.busy and engine.can_undo()
        assert engine.backend.read('org.cinnamon','panels-enabled')==['1:0:top']
        window.run_operation(engine.restore_original)
        for _ in range(300):
            while Gtk.events_pending():Gtk.main_iteration()
            if not window.busy:break
            time.sleep(.03)
        assert not window.busy and not engine.preferences()['installed_components']
    window.destroy()
    # An optional image must never prevent the appearance manager from opening.
    with patch('neo_mint_theme.gui.GdkPixbuf.Pixbuf.new_from_file_at_scale', side_effect=GLib.Error('Permission denied')):
        fallback = Window(app,engine,preview=True)
        assert len(fallback.controls) == 34
        assert fallback.stack.get_child_by_name('overview') is not None
        fallback.destroy()
    print(json.dumps({'pages':pages,'control_count':len(window.controls),'review_dialog':True,
                     'isolated_apply_and_undo':True,'restore_apply_cycles':2,'unreadable_preview_fallback':True,
                     'desktop_writes':False,'passed':True},indent=2))
