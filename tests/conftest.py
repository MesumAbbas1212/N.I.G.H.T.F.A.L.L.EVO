import os
import sys
import types
from pathlib import Path

# Ensure tests can import the application package from the workspace root.
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


# --------------------------------------------------------------------------- #
# Offline test shims
#
# The suite exercises logic inside modules that import Qt and other heavy
# packages at module level (core.quick_actions, ui.py, main.py). Two shims keep
# it runnable without them:
#
#   * PyQt6 is stubbed whenever the real package is missing (CI, containers
#     without libGL). A developer machine always tests the real thing.
#   * Everything else is stubbed only with NIGHTFALL_QA_STUBS=1, so a normal
#     run still fails loudly if a dependency is genuinely missing.
#
# Both shims are appended last on sys.meta_path: a module that exists is always
# imported for real.
# --------------------------------------------------------------------------- #
class _AnyMeta(type):
    """Class-level attribute access, memoised so enum-like values are stable.

    ``Qt.AlignmentFlag.AlignCenter`` must compare equal to itself across
    accesses, otherwise real assertions fail for shim reasons.
    """

    _cache: dict = {}

    def __getattr__(cls, item):
        if item.startswith("__"):
            raise AttributeError(item)
        if item not in _AnyMeta._cache:
            _AnyMeta._cache[item] = _Any(name=item)
        return _AnyMeta._cache[item]


class _Any(metaclass=_AnyMeta):
    """Permissive placeholder: accepts any call, attribute or operator."""

    def __init__(self, *a, **k):
        self._values: dict = {}
        self._name = k.get("name", "")

    def __getattr__(self, item):
        if item.startswith("_"):
            raise AttributeError(item)
        # Memoised per instance: `Enum.Value` must be the same object on every
        # access, exactly like the real Qt enums.
        cache = self.__dict__.setdefault("_attrs", {})
        if item not in cache:
            cache[item] = _Any(name=item)
        return cache[item]

    def __call__(self, *a, **k):
        return _Any()

    def __or__(self, other):
        return _Any()

    def __ror__(self, other):
        return _Any()

    def __getitem__(self, item):
        return _Any()

    def __iter__(self):
        return iter(())

    def __bool__(self):
        return False

    # -- just enough widget behaviour for real assertions to work -----------
    def setText(self, value):
        self._values["text"] = "" if value is None else str(value)

    def text(self):
        return self._values.get("text", "")

    def setChecked(self, value):
        self._values["checked"] = bool(value)

    def isChecked(self):
        return bool(self._values.get("checked", False))

    def setValue(self, value):
        self._values["value"] = value

    def value(self):
        return self._values.get("value", 0)

    def setEchoMode(self, mode):
        self._values["echo"] = mode

    def echoMode(self):
        return self._values.get("echo", None)

    def setEnabled(self, value):
        self._values["enabled"] = bool(value)

    def isEnabled(self):
        return bool(self._values.get("enabled", True))

    def setVisible(self, value):
        self._values["visible"] = bool(value)

    def isVisible(self):
        return bool(self._values.get("visible", True))


def _stub_module(name: str, as_classes: bool = False) -> types.ModuleType:
    """Create a stub module. Qt names are classes, since ui.py subclasses many."""
    module = types.ModuleType(name)
    module.__path__ = []
    module.__getattr__ = _make_attribute_getter(as_classes, {})
    if name == "PyQt6.QtCore":
        # Attribute flags are real state: the app sets AA_ShareOpenGLContexts
        # at import time and a test verifies it.
        module.QCoreApplication = _StubCoreApplication
    sys.modules[name] = module
    return module


class _StubLoader:
    def __init__(self, as_classes: bool = False):
        self.as_classes = as_classes

    def create_module(self, spec):
        return _stub_module(spec.name, as_classes=self.as_classes)

    def exec_module(self, module):
        return None


class _StubCoreApplication:
    """QCoreApplication stand-in that remembers set/testAttribute calls."""

    _attributes: dict = {}

    @classmethod
    def testAttribute(cls, attribute):
        return bool(cls._attributes.get(attribute, False))

    @classmethod
    def setAttribute(cls, attribute, value=True):
        cls._attributes[attribute] = bool(value)


def _make_attribute_getter(as_classes: bool, cache: dict):
    if not as_classes:
        def getter(item):
            if item.startswith("__"):
                raise AttributeError(item)
            if item not in cache:
                cache[item] = _Any(name=item)
            return cache[item]
        return getter

    def class_getter(item):
        if item.startswith("__"):
            raise AttributeError(item)
        if item not in cache:
            cache[item] = type(item, (_Any,), {})
        return cache[item]
    return class_getter


class _StubFinder:
    """Last-resort loader for modules that are not installed.

    ``skip`` names are left alone so the test runner itself is never stubbed.
    """

    def __init__(self, prefixes=("",), skip=("pytest", "_pytest", "pluggy", "iniconfig",
                                            "packaging", "typing_extensions"),
                 as_classes: bool = False):
        self.prefixes = tuple(prefixes)
        self.skip = tuple(skip)
        self.as_classes = as_classes

    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith(self.prefixes):
            return None
        if fullname.startswith(self.skip):
            return None
        import importlib.util

        spec = importlib.util.spec_from_loader(fullname, _StubLoader(self.as_classes))
        if spec:
            spec.submodule_search_locations = []
        return spec


def _install_qt_shim() -> None:
    if os.environ.get("NIGHTFALL_QA_STUBS") == "1":
        return  # a later shim covers everything, Qt included
    try:
        import PyQt6  # noqa: F401
        return
    except Exception:
        pass

    qt = _stub_module("PyQt6", as_classes=True)
    qt_core = _stub_module("PyQt6.QtCore", as_classes=True)
    qt_gui = _stub_module("PyQt6.QtGui", as_classes=True)
    qt_widgets = _stub_module("PyQt6.QtWidgets", as_classes=True)
    qt_core.pyqtSignal = lambda *a, **k: _Any()
    qt.QtCore, qt.QtGui, qt.QtWidgets = qt_core, qt_gui, qt_widgets
    qt.__version__ = "6.0.0-shim"
    sys.meta_path.append(_StubFinder(("PyQt6.",), as_classes=True))


def _install_missing_module_stubs() -> None:
    """Stub any module that is not installed (opt-in via NIGHTFALL_QA_STUBS=1)."""
    # Names are classes, not instances: app code derives from framework types
    # (pages, handlers), and an instance cannot be used as a base class.
    sys.meta_path.append(_StubFinder(as_classes=True))


_install_qt_shim()
if os.environ.get("NIGHTFALL_QA_STUBS") == "1":
    _install_missing_module_stubs()
