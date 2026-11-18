"""Build the KiCad Plugin and Content Manager package: dist/trouting-<version>-pcm.zip

    python3 tools/make_pcm.py

Install it in KiCad: Plugin and Content Manager > Install from File.
The zip holds the plugin, the router, prebuilt cores for Linux and Windows
(KiCad's own Python has no C compiler to build one with), and the PCM metadata.
The Windows core is cross-compiled with MinGW: x86_64-w64-mingw32-gcc if it is
installed, else in a Debian container with Docker.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from trouting import __version__  # noqa: E402

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
        "own folder with KiCad's pip (it asks first). Linux and Windows."),
    "identifier": "com.github.tommyholt.trouting",
    "type": "plugin",
    "author": {"name": "Tommy Holt", "contact": {"web": "https://github.com/TommyHolt/trouting"}},
    "license": "MIT",
    "resources": {"homepage": "https://github.com/TommyHolt/trouting"},
    "tags": ["routing", "autorouter", "pcb"],
    "versions": [{"version": __version__, "status": "testing", "kicad_version": "9.0", "platforms": ["linux", "windows"]}],
}


FLAGS = ["-O3", "-shared", "-Wall"]       # portable: no -march=native, it runs on other people's CPUs
MINGW_IMAGE = "trouting-mingw"
MINGW_DOCKERFILE = b"FROM debian:bookworm-slim\nRUN apt-get update && apt-get install -y gcc-mingw-w64-x86-64\n"


def build_linux(src, out):
    subprocess.check_call(["gcc"] + FLAGS + ["-fPIC", "-o", out, src, "-lm"])


def build_windows(src, out):
    cc = shutil.which("x86_64-w64-mingw32-gcc")
    if cc:
        subprocess.check_call([cc] + FLAGS + ["-static-libgcc", "-o", out, src])
        return
    if not shutil.which("docker"):
        sys.exit("make_pcm: need x86_64-w64-mingw32-gcc or docker to build the Windows core")
    if subprocess.run(["docker", "image", "inspect", MINGW_IMAGE], capture_output=True).returncode:
        subprocess.run(["docker", "build", "-q", "-t", MINGW_IMAGE, "-"], input=MINGW_DOCKERFILE, check=True)
    d = os.path.dirname(out)
    subprocess.check_call(["docker", "run", "--rm", "--user", "%d:%d" % (os.getuid(), os.getgid()),
                           "-v", "%s:/src/core.c:ro" % src, "-v", "%s:/out" % d, MINGW_IMAGE,
                           "x86_64-w64-mingw32-gcc"] + FLAGS + ["-static-libgcc", "-o", "/out/" + os.path.basename(out),
                                                                "/src/core.c"])


def main():
    src = os.path.join(ROOT, "trouting", "core.c")
    h = hashlib.sha1(open(src, "rb").read()).hexdigest()[:12]
    pre_dir = os.path.join(ROOT, "trouting", "prebuilt")
    os.makedirs(pre_dir, exist_ok=True)
    # named as trouting/core.py looks for them: core-<hash>-<sys.platform>-<machine><ext>
    cores = [os.path.join(pre_dir, "core-%s-linux-x86_64.so" % h), os.path.join(pre_dir, "core-%s-win32-amd64.dll" % h)]
    build_linux(src, cores[0])
    build_windows(src, cores[1])

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
        for c in cores:
            z.write(c, "plugins/trouting/prebuilt/" + os.path.basename(c))
    print("wrote %s (%d kB), cores for Linux and Windows" % (out, os.path.getsize(out) // 1024))


if __name__ == "__main__":
    main()
