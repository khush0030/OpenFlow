# PyInstaller spec for OpenFlow.
# Produces dist/OpenFlow.app (macOS bundle) and dist/openflow (Linux/Windows).
#
# Build:
#   source .venv/bin/activate
#   pyinstaller openflow.spec
#
# Run the resulting bundle:
#   open dist/OpenFlow.app           # macOS
#   ./dist/openflow/openflow         # Linux

# -- Imports --------------------------------------------------------------
from PyInstaller.utils.hooks import collect_data_files, collect_submodules
import sys

block_cipher = None
is_macos = sys.platform == "darwin"

# faster-whisper / ctranslate2 ship native libs and tokenizer data we must bundle
hidden = []
hidden += collect_submodules("faster_whisper")
hidden += collect_submodules("ctranslate2")
hidden += collect_submodules("tokenizers")
hidden += collect_submodules("anthropic")
hidden += collect_submodules("pynput")
hidden += collect_submodules("pystray")
hidden += collect_submodules("PIL")
hidden += collect_submodules("rapidfuzz")
hidden += collect_submodules("scipy.io")

datas = []
datas += collect_data_files("faster_whisper")
datas += collect_data_files("ctranslate2")
datas += collect_data_files("tokenizers")
datas += collect_data_files("av")  # ffmpeg-style audio decoding
datas += collect_data_files("onnxruntime")

# -- Analysis -------------------------------------------------------------
a = Analysis(
    ["openflow/__main__.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pandas", "IPython"],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# -- Single-folder build (works everywhere) --------------------------------
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="openflow",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=not is_macos,    # macOS: windowed (no terminal); other: console
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="openflow",
)

# -- macOS .app bundle ----------------------------------------------------
if is_macos:
    app = BUNDLE(
        coll,
        name="OpenFlow.app",
        icon=None,                       # set later when you have an .icns
        bundle_identifier="com.openflow.dictation",
        info_plist={
            "LSUIElement": True,         # tray-only, no Dock icon
            "CFBundleShortVersionString": "0.1.0",
            "NSMicrophoneUsageDescription":
                "OpenFlow needs the microphone to capture your dictation.",
            "NSAppleEventsUsageDescription":
                "OpenFlow uses System Events to paste text into the focused app.",
            "NSHighResolutionCapable": True,
        },
    )
