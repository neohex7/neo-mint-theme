"""English-only GTK interface; no desktop writes until a confirmed action."""
import copy
import json
from pathlib import Path
import threading

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib

from . import __version__
from .core import DEFAULTS, COMPONENTS, RANGES

CSS = b'''
window { background-color: #14191d; color: #e5ebec; }
.hero { background-color: #202a30; border-radius: 12px; padding: 18px; }
.title { font-size: 25px; font-weight: bold; color: #f0f5f5; }
.subtitle { color: #a3b8bd; }
.section-title { font-size: 19px; font-weight: bold; margin-bottom: 8px; }
.card { background-color: #1d252a; border: 1px solid #344249; border-radius: 9px; padding: 14px; }
.muted { color: #a3b8bd; }
.primary { background-image: none; background-color: #11aa83; color: white; font-weight: bold; }
.primary:hover { background-color: #18bc92; }
.status { padding: 10px; background-color: #202a30; border-radius: 8px; }
stacksidebar { background-color: #1b2328; }
stacksidebar row:selected { background-color: #215449; }
stacksidebar row label { color: #dce7e8; }
.primary:disabled { background-color: #34434a; color: #87999e; }
button, spinbutton, combobox { min-height: 28px; }
'''


class Window(Gtk.ApplicationWindow):
    def __init__(self, app, engine, preview=False):
        super().__init__(application=app, title='Neo Mint Theme')
        self.engine = engine
        self.preview = preview
        self.busy = False
        self.environment_ok = True
        self.controls = {}
        self.rows = {}
        display=Gdk.Display.get_default()
        area=(display.get_primary_monitor() or display.get_monitor(0)).get_workarea()
        self.set_default_size(min(1100,area.width-40), min(790,area.height-60))
        self.set_size_request(760, 540)
        self.connect('delete-event', self.on_close)
        Gtk.Settings.get_default().set_property('gtk-application-prefer-dark-theme', True)
        provider = Gtk.CssProvider(); provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.initial = engine.read_current()
        self.previous = copy.deepcopy(self.initial)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        outer.set_border_width(16); self.add(outer)
        header = Gtk.Box(spacing=12); header.get_style_context().add_class('hero')
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        titles.pack_start(self.label('Neo Mint Theme', 'title'), False, False, 0)
        titles.pack_start(self.label('Your Cinnamon desktop, thoughtfully customized.', 'subtitle'), False, False, 0)
        titles.pack_start(self.label('Linux Mint 22.3 · Cinnamon · X11', 'muted'), False, False, 0)
        header.pack_start(titles, True, True, 0)
        version = self.label(f'v{__version__}', 'muted'); header.pack_end(version, False, False, 0)
        outer.pack_start(header, False, False, 0)
        body = Gtk.Box(spacing=16)
        self.stack = Gtk.Stack(); self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        sidebar = Gtk.StackSidebar(); sidebar.set_stack(self.stack); sidebar.set_size_request(165, -1)
        body.pack_start(sidebar, False, False, 0); body.pack_start(self.stack, True, True, 0)
        outer.pack_start(body, True, True, 0)
        self.build_overview()
        self.build_wallpaper()
        self.build_text()
        self.build_panel()
        self.build_dock()
        self.build_effects()
        self.build_restore()
        self.status = self.label('Choose your components and settings, then click Apply.', 'status')
        self.status.set_line_wrap(True); outer.pack_start(self.status, False, False, 0)
        footer = Gtk.Box(spacing=8)
        self.reset = Gtk.Button(label='Reset to Defaults'); self.reset.connect('clicked', self.reset_defaults)
        self.review = Gtk.Button(label='Review Changes'); self.review.connect('clicked', lambda _: self.show_plan(False))
        self.apply_button = Gtk.Button(label='Apply Customization'); self.apply_button.get_style_context().add_class('primary')
        self.apply_button.connect('clicked', lambda _: self.show_plan(True))
        footer.pack_start(self.reset, False, False, 0); footer.pack_start(self.review, False, False, 0)
        footer.pack_end(self.apply_button, False, False, 0); outer.pack_start(footer, False, False, 0)
        self.populate(self.initial)
        self.show_all(); self.update_sensitivity()
        if preview:
            self.status.set_text('Preview mode — desktop changes and restore actions are disabled.')
        else:
            try: engine.check_environment()
            except RuntimeError as e:
                self.environment_ok=False
                self.apply_button.set_sensitive(False); self.status.set_text(str(e))

    def label(self, text, style=None):
        widget = Gtk.Label(label=text, xalign=0)
        if style: widget.get_style_context().add_class(style)
        return widget

    def page(self, name, title, description):
        scroll = Gtk.ScrolledWindow(); scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12); box.set_border_width(4)
        box.pack_start(self.label(title, 'section-title'), False, False, 0)
        desc = self.label(description, 'muted'); desc.set_line_wrap(True)
        box.pack_start(desc, False, False, 0)
        scroll.add(box); self.stack.add_titled(scroll, name, title)
        return box

    def note(self, page, text):
        label = self.label(text, 'muted'); label.set_line_wrap(True)
        page.pack_start(label, False, False, 0)

    def row(self, page, key, title, hint='', choices=None):
        box = Gtk.Box(spacing=16); box.get_style_context().add_class('card')
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        labels.pack_start(self.label(title), False, False, 0)
        if hint:
            label = self.label(hint, 'muted'); label.set_line_wrap(True); label.set_max_width_chars(48)
            labels.pack_start(label, False, False, 0)
        box.pack_start(labels, True, True, 0)
        if choices is not None:
            widget = Gtk.ComboBoxText()
            for ident, text in choices: widget.append(ident, text)
            widget.connect('changed', lambda _: self.update_sensitivity())
        elif isinstance(DEFAULTS.get(key, True), bool):
            widget = Gtk.Switch(); widget.set_valign(Gtk.Align.CENTER)
            widget.connect('notify::active', lambda *_: self.update_sensitivity())
        else:
            low, high = RANGES[key]
            step = .25 if key == 'dock_hide_delay' else (.5 if 'font_size' in key else 1)
            widget = Gtk.SpinButton.new_with_range(low, high, step)
            widget.set_digits(2 if key == 'dock_hide_delay' else (1 if 'font_size' in key else 0))
            widget.set_width_chars(6); widget.set_valign(Gtk.Align.CENTER)
        box.pack_end(widget, False, False, 0)
        page.pack_start(box, False, False, 0)
        self.controls[key] = widget; self.rows[key] = box
        return widget

    def build_overview(self):
        page = self.page('overview', 'Overview', 'A complete appearance profile with room to adjust it for your display.')
        preview = self.engine.data / 'preview.png'
        if preview.is_file():
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(preview), 600, 338, True)
            except GLib.Error:
                self.note(page, 'Desktop preview is unavailable. You can still configure and apply the theme.')
            else:
                image = Gtk.Image.new_from_pixbuf(pixbuf); page.pack_start(image, False, False, 0)
        self.note(page, 'Desktop example. The wallpaper and applications shown are not included.')
        for component, title, hint in [
            ('application', 'Application theme', 'Custom Colloid Nord stylesheet, application fonts, and file manager appearance'),
            ('desktop', 'Desktop theme', 'Vibrant Teal Cinnamon theme and fixed window-title font'),
            ('icons', 'Icon theme', 'Reversal green icons and DMZ White cursor'),
            ('panel', 'Panel layout', 'Top panel, centered clock, and optional system indicators'),
            ('dock', 'Dock', 'Plank with a rounded dark theme'),
            ('terminal', 'Terminal appearance', 'Dark background, golden text, and fixed monospace font'),
            ('effects', 'Desktop effects', 'Transparent panel and optional wobbly windows')]:
            self.row(page, 'component_' + component, title, hint)

    def build_wallpaper(self):
        page = self.page('wallpaper', 'Wallpaper', 'Keep your current background or choose an image from your computer.')
        self.row(page, 'wallpaper_mode', 'Wallpaper', choices=[('keep', 'Keep current wallpaper'), ('choose', 'Choose an image')])
        box = Gtk.Box(spacing=10); box.get_style_context().add_class('card')
        entry = Gtk.Entry(); entry.set_placeholder_text('No image selected'); entry.set_editable(False)
        self.controls['wallpaper_path'] = entry
        button = Gtk.Button(label='Choose Image…'); button.connect('clicked', self.choose_image)
        box.pack_start(entry, True, True, 0); box.pack_end(button, False, False, 0)
        page.pack_start(box, False, False, 0); self.rows['wallpaper_path'] = box
        self.row(page, 'image_fit', 'Image fit', choices=[('zoom', 'Zoom'), ('scaled', 'Fit'), ('stretched', 'Stretch'), ('centered', 'Center')])
        self.note(page, 'Your image stays on your computer. A local copy is kept so the background still works if the source file is moved.')

    def build_text(self):
        page = self.page('text', 'Text & Sizes', 'Defaults are intended for a 24-inch display. Adjust them for your screen size, resolution, and scaling.')
        self.note(page, 'Font families are fixed: Ubuntu, Ubuntu Medium, and DejaVu Sans Mono.')
        for key, title, hint in [('app_font_size', 'Application font size', 'Points · Application theme component'),
                                  ('title_font_size', 'Window title font size', 'Points · Desktop theme component'),
                                  ('terminal_font_size', 'Terminal font size', 'Points · Terminal appearance component'),
                                  ('text_scale', 'Text scaling', 'Percent · Application theme component'),
                                  ('cursor_size', 'Cursor size', 'Pixels · Icon theme component')]:
            self.row(page,key,title,hint)

    def build_panel(self):
        page = self.page('panel', 'Panel & Clock', 'A top panel with a centered clock and adjustable indicators.')
        for key, title in [('panel_height', 'Panel height'), ('left_icon_size', 'Left icon size'), ('right_icon_size', 'Right icon size')]:
            self.row(page,key,title,'Pixels')
        self.row(page,'panel_visibility','Panel visibility',choices=[('always','Always visible'),('auto','Auto-hide')])
        for key,title,hint in [('clock_24h','24-hour clock','Turn off to use a 12-hour clock'),
                               ('show_date','Show date',''),('show_seconds','Show seconds',''),
                               ('show_system_monitor','Show system monitor','CPU, memory, swap, and network graphs'),
                               ('show_temperature','Show CPU temperature','Automatically omitted when no supported CPU sensor is available')]:
            self.row(page,key,title,hint)
        self.note(page,'The layout uses the primary display. Additional panels are replaced only when you first apply this component.')

    def build_dock(self):
        page = self.page('dock', 'Dock', 'A centered Plank dock at the bottom of your display.')
        for key,title,hint in [('dock_icon_size','Icon size','Pixels'),('dock_zoom','Enable zoom',''),
                               ('dock_zoom_amount','Zoom amount','Percent'),('dock_autohide','Auto-hide',''),
                               ('dock_hide_delay','Hide delay','Seconds'),('dock_show_running','Show running applications',''),
                               ('dock_startup','Start dock automatically','After you sign in')]:
            self.row(page,key,title,hint)
        choices=[('', 'Primary display')]
        display=Gdk.Display.get_default()
        for i in range(display.get_n_monitors()):
            monitor=display.get_monitor(i); model=monitor.get_model() or f'Display {i+1}'
            # Plank resolves the GDK/XRandR monitor plug name, not the marketing model.
            plug=Gdk.Screen.get_default().get_monitor_plug_name(i)
            if plug: choices.append((plug,model+' ('+plug+')'))
        self.row(page,'dock_monitor','Monitor',choices=choices)
        self.note(page,'Existing dock shortcuts are preserved. On a new dock, shortcuts are added only for available applications.')

    def build_effects(self):
        page = self.page('effects', 'Effects', 'Choose which desktop effects to enable.')
        self.row(page,'transparent_panel','Transparent panel','Transparency when no window is maximized')
        self.row(page,'window_effects','Window effects','Compiz-style wobbly windows')
        self.note(page,'Existing unrelated Cinnamon extensions remain enabled. Extension changes may require signing out and back in.')

    def build_restore(self):
        page = self.page('restore','Restore','Only settings and files touched by this application are saved and restored.')
        for title,hint,method in [('Undo Last Apply','Return to the appearance before the most recent Apply.',self.engine.undo),
                                  ('Restore Original Appearance','Return to the appearance saved before your first customization.',self.engine.restore_original),
                                  ('Recover Interrupted Apply','Recover files and settings if an Apply was interrupted.',self.engine.recover)]:
            box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8);box.get_style_context().add_class('card')
            button=Gtk.Button(label=title)
            button.connect('clicked',lambda _,t=title,m=method:self.confirm_restore(t,m))
            box.pack_start(button,False,False,0)
            label=self.label(hint,'muted');label.set_line_wrap(True);box.pack_start(label,False,False,0)
            page.pack_start(box,False,False,0)
            self.rows[title]=button
        self.note(page,'Restore your original appearance before uninstalling the application if you want to remove its customization.')

    def populate(self, values):
        for key,widget in self.controls.items():
            value=values['components'][key.removeprefix('component_')] if key.startswith('component_') else values[key]
            if isinstance(widget,Gtk.Switch):widget.set_active(value)
            elif isinstance(widget,Gtk.SpinButton):widget.set_value(value)
            elif isinstance(widget,Gtk.ComboBoxText):
                if key=='dock_monitor' and value and not widget.set_active_id(value):widget.append(value,value)
                widget.set_active_id(value)
            else:widget.set_text(value)

    def values(self):
        values=copy.deepcopy(DEFAULTS)
        for key,widget in self.controls.items():
            if isinstance(widget,Gtk.Switch):value=widget.get_active()
            elif isinstance(widget,Gtk.SpinButton):value=widget.get_value()
            elif isinstance(widget,Gtk.ComboBoxText):value=widget.get_active_id()
            else:value=widget.get_text()
            if key.startswith('component_'):values['components'][key.removeprefix('component_')]=value
            else:values[key]=value
        return values

    def update_sensitivity(self):
        if not hasattr(self,'apply_button'):return
        for name in ('panel','dock','effects'):
            toggle=self.controls['component_'+name].get_active()
            for key,row in self.rows.items():
                if key.startswith('component_'):continue
                belongs=(key.startswith('dock_') if name=='dock' else key in
                    (['panel_height','left_icon_size','right_icon_size','panel_visibility','clock_24h','show_date','show_seconds','show_system_monitor','show_temperature'] if name=='panel' else ['transparent_panel','window_effects']))
                if belongs:row.set_sensitive(toggle and not self.busy)
        for key,component in [('app_font_size','application'),('text_scale','application'),('title_font_size','desktop'),('terminal_font_size','terminal'),('cursor_size','icons')]:
            self.rows[key].set_sensitive(self.controls['component_'+component].get_active() and not self.busy)
        choose=self.controls['wallpaper_mode'].get_active_id()=='choose'
        self.rows['wallpaper_path'].set_sensitive(choose and not self.busy)
        self.rows['image_fit'].set_sensitive(choose and not self.busy)
        self.rows['dock_zoom_amount'].set_sensitive(self.controls['dock_zoom'].get_active() and self.controls['component_dock'].get_active() and not self.busy)
        self.rows['dock_hide_delay'].set_sensitive(self.controls['dock_autohide'].get_active() and self.controls['component_dock'].get_active() and not self.busy)
        if not self.engine.temperature:self.controls['show_temperature'].set_sensitive(False)
        self.rows['Undo Last Apply'].set_sensitive(not self.preview and not self.busy and self.engine.can_undo())
        self.rows['Restore Original Appearance'].set_sensitive(not self.preview and not self.busy and self.engine.can_restore())
        self.rows['Recover Interrupted Apply'].set_sensitive(not self.preview and not self.busy)
        self.apply_button.set_sensitive(not self.preview and not self.busy and self.environment_ok)
        self.review.set_sensitive(not self.busy);self.reset.set_sensitive(not self.busy)

    def choose_image(self,_):
        dialog=Gtk.FileChooserDialog(title='Choose a Wallpaper',parent=self,action=Gtk.FileChooserAction.OPEN)
        dialog.add_buttons('Cancel',Gtk.ResponseType.CANCEL,'Select Image',Gtk.ResponseType.OK)
        filt=Gtk.FileFilter();filt.set_name('Image files');filt.add_pixbuf_formats();dialog.add_filter(filt)
        if dialog.run()==Gtk.ResponseType.OK:self.controls['wallpaper_path'].set_text(dialog.get_filename())
        dialog.destroy()

    def reset_defaults(self,_):
        self.populate(copy.deepcopy(DEFAULTS));self.update_sensitivity()
        self.status.set_text('Default values loaded into the form. Click Apply to use them.')

    def error(self,text):
        dialog=Gtk.MessageDialog(transient_for=self,modal=True,message_type=Gtk.MessageType.ERROR,
                                 buttons=Gtk.ButtonsType.CLOSE,text='The operation could not be completed.')
        dialog.format_secondary_text(str(text));dialog.run();dialog.destroy()

    def show_plan(self,apply):
        try:
            values=self.values();plan=self.engine.build_plan(values,self.previous)
        except Exception as e:self.error(e);return
        summary=plan.summary()
        dialog=Gtk.Dialog(title='Review Changes',transient_for=self,modal=True)
        dialog.set_default_size(650,450);dialog.add_button('Cancel' if apply else 'Close',Gtk.ResponseType.CANCEL)
        if apply and summary['change_count']:dialog.add_button('Apply Changes',Gtk.ResponseType.OK)
        box=dialog.get_content_area();box.set_border_width(16)
        label=self.label(f'{len(plan.settings)} desktop settings and {len(plan.files)} appearance files or folders will change.')
        label.set_line_wrap(True);box.pack_start(label,False,False,8)
        text=[]
        for item in summary['settings']:text.append('• '+item['key'].replace('-',' ').capitalize())
        for path in summary['files']:text.append('• '+path)
        if not text:text=['The selected appearance is already applied.']
        if plan.warnings:text+=['','Notes:']+plan.warnings
        scroll=Gtk.ScrolledWindow();scroll.set_policy(Gtk.PolicyType.AUTOMATIC,Gtk.PolicyType.AUTOMATIC)
        view=Gtk.TextView();view.set_editable(False);view.set_cursor_visible(False);view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        view.get_buffer().set_text('\n'.join(text));scroll.add(view);box.pack_start(scroll,True,True,8)
        note=self.label('Your previous appearance will be saved. Your current wallpaper is kept unless you choose an image.','muted')
        note.set_line_wrap(True);box.pack_start(note,False,False,0)
        dialog.show_all();response=dialog.run();dialog.destroy()
        if response==Gtk.ResponseType.OK and not self.preview:self.run_operation(lambda:self.engine.apply(plan))

    def confirm_restore(self,title,method):
        if self.preview:return
        dialog=Gtk.MessageDialog(transient_for=self,modal=True,message_type=Gtk.MessageType.QUESTION,
                                buttons=Gtk.ButtonsType.OK_CANCEL,text=title+'?')
        dialog.format_secondary_text('This changes the appearance settings and files saved by Neo Mint Theme.')
        response=dialog.run();dialog.destroy()
        if response==Gtk.ResponseType.OK:self.run_operation(method)

    def run_operation(self,method):
        def dispatch(operation):
            done = threading.Event()
            failures = []
            def publish():
                try: operation()
                except Exception as exc: failures.append(exc)
                finally: done.set()
                return False
            GLib.idle_add(publish)
            done.wait()
            if failures: raise failures[0]
        self.engine.backend.dispatch = dispatch
        self.busy=True;self.stack.set_sensitive(False);self.update_sensitivity()
        self.status.set_text('Working… Please keep this window open.')
        def worker():
            try:result=method();GLib.idle_add(self.finished,result,None)
            except Exception as e:GLib.idle_add(self.finished,None,str(e))
        threading.Thread(target=worker,daemon=False).start()

    def finished(self,result,error):
        self.busy=False;self.stack.set_sensitive(True)
        if error:self.status.set_text('The operation failed. See the error for details.');self.error(error)
        else:
            self.previous=self.engine.read_current();self.populate(self.previous)
            self.status.set_text(result['message']+(' '+ ' '.join(result['warnings']) if result['warnings'] else ''))
        self.update_sensitivity();return False

    def on_close(self,*_):
        if self.busy:
            self.status.set_text('Please wait for the appearance operation to finish before closing.');return True
        return False


class Application(Gtk.Application):
    def __init__(self,engine,preview=False):
        from gi.repository import Gio
        super().__init__(application_id='io.github.neo.MintTheme',flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.engine=engine;self.preview=preview;self.window=None

    def do_activate(self):
        if self.window is None:self.window=Window(self,self.engine,self.preview)
        self.window.present()
