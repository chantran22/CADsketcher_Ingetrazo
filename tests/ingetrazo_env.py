"""Import IngeTrazo's own frozen modules (core.*, tools.*, views.*) for testing.

Reads the PYZ archive embedded in ingetrazo.exe (PyInstaller) read-only and
registers a meta-path finder. Needs the same Python minor version as the app (3.12).
"""
import importlib.abc, importlib.util, marshal, os, struct, sys, zlib

EXE = r"C:\Users\Admin\AppData\Local\Programs\IngeTrazo\ingetrazo.exe"
INTERNAL = r"C:\Users\Admin\AppData\Local\Programs\IngeTrazo\_internal"
MAGIC = b"MEI\014\013\012\013\016"


def _read_pyz():
    with open(EXE, "rb") as f:
        data = f.read()
    pos = data.rfind(MAGIC)
    _, pkg_len, toc_off, toc_len, _pyver = struct.unpack("!8sIIII", data[pos:pos + 24])
    start = pos + 88 - pkg_len if data[pos + 24:pos + 88] else pos
    # cookie is 88 bytes (magic, len, toc, toclen, pyver, 64s libname)
    start = pos + 88 - pkg_len
    toc = data[start + toc_off:start + toc_off + toc_len]
    i = 0
    pyz = None
    while i < len(toc):
        (elen,) = struct.unpack("!I", toc[i:i + 4])
        epos, clen, ulen, cflag, typ = struct.unpack("!IIIBc", toc[i + 4:i + 18])
        name = toc[i + 18:i + elen].split(b"\0", 1)[0].decode()
        if typ == b"z":
            pyz = data[start + epos:start + epos + clen]
            break
        i += elen
    assert pyz and pyz[:4] == b"PYZ\0", "PYZ archive not found"
    (toc_pos,) = struct.unpack("!I", pyz[8:12])
    table = marshal.loads(pyz[toc_pos:])
    if isinstance(table, list):
        table = dict(table)
    return pyz, table


class _Finder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self):
        self.pyz, self.table = _read_pyz()

    def find_spec(self, name, path=None, target=None):
        if name in self.table:
            ispkg = self.table[name][0] in (1, 3)
            spec = importlib.util.spec_from_loader(name, self, is_package=ispkg)
            if ispkg:
                spec.submodule_search_locations = [os.path.join(INTERNAL, *name.split("."))]
            return spec
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        typ, pos, length = self.table[module.__name__]
        code = marshal.loads(zlib.decompress(self.pyz[pos:pos + length]))
        base = os.path.join(INTERNAL, *module.__name__.split("."))
        if typ in (1, 3):
            module.__path__ = [base]
            module.__file__ = os.path.join(base, "__init__.py")
        else:
            module.__file__ = base + ".py"
        exec(code, module.__dict__)

    def source_names(self, prefix=""):
        return sorted(n for n in self.table if n.startswith(prefix))

    def code(self, name):
        typ, pos, length = self.table[name]
        return marshal.loads(zlib.decompress(self.pyz[pos:pos + length]))


def install():
    if INTERNAL not in sys.path:
        sys.path.insert(0, INTERNAL)
    dirs = [INTERNAL, os.path.join(INTERNAL, "PySide6"), os.path.join(INTERNAL, "shiboken6")]
    for d in dirs:
        os.add_dll_directory(d)
    os.environ["PATH"] = ";".join(dirs) + ";" + os.environ["PATH"]
    finder = _Finder()
    sys.meta_path.insert(0, finder)
    return finder
