"""Typed GSettings access. Importing this module never changes settings."""
import gi
from contextlib import contextmanager
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Gio, GLib


class SettingsBackend:
    def __init__(self, gio_backend=None):
        self.cache = {}
        self.gio_backend = gio_backend
        self.batch_cache = None
        self.dispatch = None

    def settings(self, name):
        cache = self.cache if self.batch_cache is None else self.batch_cache
        if name not in cache:
            schema, sep, path = name.partition(':')
            source = Gio.SettingsSchemaSource.get_default()
            definition = source.lookup(schema, True)
            if definition is None:
                raise ValueError(f'Required settings are unavailable: {schema}')
            cache[name] = Gio.Settings.new_full(definition, self.gio_backend, path if sep else None)
            if self.batch_cache is not None:
                cache[name].delay()
        return cache[name]

    @contextmanager
    def batch(self):
        """Publish a group of settings together within each schema."""
        if self.batch_cache is not None:
            raise RuntimeError('A settings operation is already in progress.')
        self.batch_cache = {}
        try:
            yield
            for settings in self.batch_cache.values():
                settings.apply()
        except Exception:
            for settings in self.batch_cache.values():
                settings.revert()
            raise
        finally:
            self.batch_cache = None
            self.sync()

    def commit(self, operation):
        """GTK callers dispatch publication onto their main thread."""
        def publish():
            with self.batch():
                operation()
        return self.dispatch(publish) if self.dispatch is not None else publish()

    def has(self, schema, key):
        try:
            return self.settings(schema).props.settings_schema.has_key(key)
        except ValueError:
            return False

    def read(self, schema, key):
        return self.settings(schema).get_value(key).unpack()

    def snapshot(self, schema, key):
        s = self.settings(schema)
        value = s.get_user_value(key)
        return {'user': value.print_(True) if value is not None else None,
                'effective': s.get_value(key).print_(True)}

    def serialized(self, schema, key):
        return self.settings(schema).get_value(key).print_(True)

    def normalize(self, schema, key, value):
        s = self.settings(schema)
        v = GLib.Variant.parse(s.get_value(key).get_type(), value, None, None)
        if not s.props.settings_schema.get_key(key).range_check(v):
            raise ValueError(f'Invalid value for {key}')
        if not s.is_writable(key):
            raise ValueError(f'This setting is locked: {key}')
        return v.print_(True)

    def write(self, schema, key, value):
        s = self.settings(schema)
        v = GLib.Variant.parse(s.get_value(key).get_type(), value, None, None)
        if not s.set_value(key, v):
            raise RuntimeError(f'Could not change {key}')

    def restore(self, schema, key, snapshot):
        if snapshot['user'] is None:
            self.settings(schema).reset(key)
        else:
            self.write(schema, key, snapshot['user'])

    def sync(self):
        Gio.Settings.sync()
        # Do not retain instances created on a different operation/thread context.
        self.cache.clear()


def variant(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, str):
        return GLib.Variant('s', value).print_(True)
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return GLib.Variant('as', value).print_(True)
    return str(value)
