"""Build the KiCad Plugin and Content Manager package: dist/trouting-<version>-pcm.zip

    python3 tools/make_pcm.py

Install it in KiCad: Plugin and Content Manager > Install from File.
The zip holds the plugin, the router, a prebuilt core for this platform (KiCad's
own Python has no C compiler to build one with), and the PCM metadata.
"""
import hashlib
import json
import os
import platform
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from trouting import __version__  # noqa: E402
from trouting.core import EXT, platform_tag  # noqa: E402

META = {
    "$schema": "https://go.kicad.org/pcm/schemas/v1",
    "name": "Trouting",
    "description": "Autorouter: routes every net at once and suggests parts to move when a board won't route.",
    "description_full": (
        "Trouting (short for Tommy's routing) routes the open board from the PCB editor. It routes every "
        "net at once with negotiated congestion, stitches GND into its pour with short tracks and vias, "
        "uses the board's own net classes and rules, and puts the result in the open board (Ctrl+Z undoes it). "
        "When a board can't be routed as placed, it suggests which part to move or rotate, each suggestion "
        "checked by routing the board with it.\n\nOn first use it installs numpy, scipy and shapely into its "
        "own folder with KiCad's pip (it asks first). Linux for now."),
    "identifier": "com.github.tommyholt.trouting",
    "type": "plugin",
    "author": {"name": "Tommy Holt", "contact": {"web": "https://github.com/TommyHolt/trouting"}},
    "license": "MIT",
    "resources": {"homepage": "https://github.com/TommyHolt/trouting"},
    "tags": ["routing", "autorouter", "pcb"],
    "versions": [{"version": __version__, "status": "testing", "kicad_version": "9.0", "platforms": ["linux"]}],
}


def main():
    src = os.path.join(ROOT, "trouting", "core.c")
    h = hashlib.sha1(open(src, "rb").read()).hexdigest()[:12]
    pre_dir = os.path.join(ROOT, "trouting", "prebuilt")
    os.makedirs(pre_dir, exist_ok=True)
    pre = os.path.join(pre_dir, "core-%s-%s%s" % (h, platform_tag(), EXT))
    # portable: no -march=native, it runs on other people's CPUs
    subprocess.check_call(["gcc", "-O3", "-shared", "-fPIC", "-Wall", "-o", pre, src, "-lm"])

    os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
    out = os.path.join(ROOT, "dist", "trouting-%s-pcm.zip" % __version__)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("metadata.json", json.dumps(META, indent=2))
        z.write(os.path.join(ROOT, "plugin", "icon-64.png"), "resources/icon.png")
        for f in ("__init__.py", "trouting_plugin.py", "icon.png"):
            z.write(os.path.join(ROOT, "plugin", f), "plugins/" + f)
        z.write(os.path.join(ROOT, "trouting_kicad.py"), "plugins/trouting_kicad.py")
        for f in sorted(os.listdir(os.path.join(ROOT, "trouting"))):
            if f.endswith((".py", ".c")):
                z.write(os.path.join(ROOT, "trouting", f), "plugins/trouting/" + f)
        z.write(pre, "plugins/trouting/prebuilt/" + os.path.basename(pre))
    print("wrote %s (%d kB), core for %s" % (out, os.path.getsize(out) // 1024, platform_tag()))


if __name__ == "__main__":
    main()
