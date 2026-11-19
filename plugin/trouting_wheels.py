"""Install packages from PyPI into a folder, for a Python that has no pip
(KiCad's AppImage on Linux comes without it):

    python3 trouting_wheels.py TARGET numpy scipy shapely

Only what the plugin needs: binary wheels for this Python and this machine,
the newest that fit (numpy within what scipy and shapely ask of it), each
checked against PyPI's SHA-256, then unpacked (installing a wheel is
unpacking it). Linux only; everywhere else KiCad's Python has pip.
"""
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile

try:
    import ssl  # noqa: F401  (urllib needs it for https)
    SSL_ERROR = None
except Exception as e:       # KiCad's AppImage on some systems (Ubuntu 26.04): its ssl doesn't load
    SSL_ERROR = "%s: %s" % (type(e).__name__, e)

PYPI = "https://pypi.org/pypi/%s/json"
LEGACY = {"manylinux1": (2, 5), "manylinux2010": (2, 12), "manylinux2014": (2, 17)}


def version(s):
    """'2.2.6' -> (2, 2, 6); None for a pre-release or anything odd."""
    return tuple(int(x) for x in s.split(".")) if re.fullmatch(r"\d+(\.\d+)*", s) else None


def satisfies(v, spec):
    """Does version v (a tuple) meet a spec like '>=1.25.2,<2.6'? (the clauses
    this needs; one it can't read is taken as met)"""
    for clause in filter(None, (c.strip() for c in (spec or "").split(","))):
        m = re.fullmatch(r"(>=|<=|==|!=|>|<|~=)\s*([\d.]+)(\.\*)?", clause)
        if not m:
            continue
        op, w = m.group(1), version(m.group(2))
        if w is None:
            continue
        n = max(len(v), len(w))
        a, b = v + (0,) * (n - len(v)), w + (0,) * (n - len(w))
        if m.group(3):                        # ==2.* style: compare the given part only
            a = v[:len(w)]
        ok = {">=": a >= b, "<=": a <= b, ">": a > b, "<": a < b, "==": a == b, "!=": a != b,
              "~=": a >= b and v[:len(w) - 1] == w[:len(w) - 1]}[op]
        if not ok:
            return False
    return True


def glibc():
    try:
        m = re.search(r"(\d+)\.(\d+)", os.confstr("CS_GNU_LIBC_VERSION") or "")
        return (int(m.group(1)), int(m.group(2))) if m else None
    except (AttributeError, ValueError, OSError):
        return None


def fits(filename):
    """Is this wheel for this Python (CPython X.Y) on this Linux machine?"""
    m = re.fullmatch(r"[^-]+-[^-]+(-\d[^-]*)?-([^-]+)-([^-]+)-([^-]+)\.whl", filename)
    if not m:
        return False
    pys, abi, plats = m.group(2).split("."), m.group(3), m.group(4).split(".")
    major, minor = sys.version_info[:2]
    cp = "cp%d%d" % (major, minor)
    if abi == cp:
        py_ok = cp in pys
    elif abi == "abi3":                   # the stable ABI: built for 3.N, runs on 3.N and later
        py_ok = any(re.fullmatch(r"cp3(\d+)", t) and int(t[3:]) <= minor for t in pys)
    elif abi == "none":
        py_ok = cp in pys or "py3" in pys
    else:
        py_ok = False
    if not py_ok:
        return False
    arch, libc = platform.machine(), glibc()
    for t in plats:
        if t == "any":
            return True
        mm = re.fullmatch(r"manylinux_(\d+)_(\d+)_(\w+)", t)
        if mm:
            need, tarch = (int(mm.group(1)), int(mm.group(2))), mm.group(3)
        else:
            old = [(v, t[len(k) + 1:]) for k, v in LEGACY.items() if t.startswith(k + "_")]
            if not old:
                continue                  # musllinux, macOS, Windows...
            need, tarch = old[0]
        if tarch == arch and libc and need <= libc:
            return True
    return False


def fetch(url, timeout):
    """The bytes at url: with urllib, or (no ssl in this Python) with curl or wget.
    What comes back is checked by the caller (PyPI's SHA-256 for a wheel)."""
    if SSL_ERROR is None:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read()
    for cmd in (["curl", "-fsSL", "--max-time", str(timeout), url], ["wget", "-q", "-T", str(timeout), "-O", "-", url]):
        if shutil.which(cmd[0]):
            return subprocess.run(cmd, check=True, capture_output=True).stdout
    raise SystemExit("trouting_wheels: this Python can't use https (%s), and there is no curl or wget "
                     "to download with" % SSL_ERROR)


def meta(name, ver=None):
    url = PYPI % (name if ver is None else "%s/%s" % (name, ver))
    return json.loads(fetch(url, 60))


def pick(name, spec=""):
    """The newest release of `name` meeting spec with a wheel that fits: (version, file)."""
    d = meta(name)
    for s in sorted((s for s in d["releases"] if version(s)), key=version, reverse=True):
        if not satisfies(version(s), spec):
            continue
        for f in d["releases"][s]:
            if f["packagetype"] == "bdist_wheel" and not f.get("yanked") and fits(f["filename"]) \
                    and satisfies(sys.version_info[:3], f.get("requires_python") or ""):
                return s, f
    return None, None


def numpy_spec(name, ver):
    """What release `ver` of `name` asks of numpy, e.g. '<2.6,>=1.25.2'."""
    for r in meta(name, ver)["info"].get("requires_dist") or []:
        m = re.match(r"numpy\s*\(?([^;)]*)\)?\s*(;.*)?$", r.strip())
        if m and not (m.group(2) and "extra" in m.group(2)):
            return m.group(1).strip()
    return ""


def unpack(f, target):
    print("  %s: downloading %.0f MB" % (f["filename"], f["size"] / 1e6), flush=True)
    data = fetch(f["url"], 300)
    if hashlib.sha256(data).hexdigest() != f["digests"]["sha256"]:
        raise SystemExit("trouting_wheels: %s doesn't match PyPI's SHA-256" % f["filename"])
    path = os.path.join(target, f["filename"])
    with open(path, "wb") as fh:
        fh.write(data)
    with zipfile.ZipFile(path) as z:
        z.extractall(target)
    os.remove(path)
    # a wheel's <name>.data/purelib and platlib belong at the top; the rest (scripts, headers) isn't needed
    for dd in [x for x in os.listdir(target) if x.endswith(".data")]:
        for sub in ("purelib", "platlib"):
            src = os.path.join(target, dd, sub)
            if os.path.isdir(src):
                for x in os.listdir(src):
                    shutil.move(os.path.join(src, x), os.path.join(target, x))
        shutil.rmtree(os.path.join(target, dd), ignore_errors=True)


def main(target, names):
    if not sys.platform.startswith("linux"):
        raise SystemExit("trouting_wheels: only for Linux (elsewhere KiCad's Python has pip)")
    os.makedirs(target, exist_ok=True)
    if SSL_ERROR:
        print("(no https in this Python: %s; downloading with curl/wget)" % SSL_ERROR, flush=True)
    chosen = {}
    spec = ""
    for name in names:                       # numpy last: it has to suit the others
        if name == "numpy":
            continue
        v, f = pick(name)
        if f is None:
            raise SystemExit("trouting_wheels: no %s wheel for Python %d.%d on %s with glibc %s"
                             % ((name,) + sys.version_info[:2] + (platform.machine(), glibc())))
        chosen[name] = f
        spec = ",".join(filter(None, [spec, numpy_spec(name, v)]))
        print("%s %s" % (name, v), flush=True)
    if "numpy" in names:
        v, f = pick("numpy", spec)
        if f is None:
            raise SystemExit("trouting_wheels: no numpy wheel meeting %s for this Python" % spec)
        chosen["numpy"] = f
        print("numpy %s (wanted: %s)" % (v, spec or "any"), flush=True)
    for name, f in chosen.items():
        for x in os.listdir(target):         # an older copy goes first
            if x in (name, name + ".libs") or (x.lower().startswith(name + "-") and x.endswith(".dist-info")):
                shutil.rmtree(os.path.join(target, x), ignore_errors=True)
        unpack(f, target)
    print("installed into %s" % target, flush=True)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2:])
