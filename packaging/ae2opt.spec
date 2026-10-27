# PyInstaller spec: a one-folder Windows build of the app (no console window).
#   pyinstaller packaging/ae2opt.spec --noconfirm
# Output: dist/AE2ControllerOptimizer/AE2ControllerOptimizer.exe
# One folder rather than one file: a one-file build unpacks OR-Tools to a temp
# folder on every launch (slow) and gets flagged by antivirus more often.

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent
datas = [
    (str(ROOT / "ae2opt" / "gui" / "static"), "ae2opt/gui/static"),
    (str(ROOT / "ae2opt" / "library"), "ae2opt/library"),
]
binaries, hiddenimports = [], ["ae2opt.gui", "ae2opt.gui.server"]
for pkg in ("ortools",):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    # pandas and numpy stay: OR-Tools' cp_model imports both.
    excludes=["tkinter", "matplotlib", "PIL", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AE2ControllerOptimizer",
    icon=str(ROOT / "packaging" / "icon.ico"),
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="AE2ControllerOptimizer", upx=False)
