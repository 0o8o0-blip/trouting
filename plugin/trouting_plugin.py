"""Trouting as a KiCad action plugin: route the open board from the PCB editor.

The router runs in a Python process of its own (KiCad's Python, with numpy,
scipy and shapely installed next to this plugin on first use), so the editor
stays responsive and the run can be cancelled. The routes then go into the
open board; Ctrl+Z takes them out again.
"""
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

import pcbnew

HERE = os.path.dirname(os.path.realpath(__file__))
# packaged: trouting/ sits beside this file; from a checkout: one folder up
ROOT = HERE if os.path.isdir(os.path.join(HERE, "trouting")) else os.path.dirname(HERE)
DEPS = os.path.join(HERE, "_deps")
NEEDED = ["numpy", "scipy", "shapely"]
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import trouting  # noqa: E402  (needs ROOT on the path; only its __init__: no numpy)
import trouting_kicad  # noqa: E402


# on Windows a python.exe started from KiCad would open a console window: none
NO_WINDOW = {"creationflags": 0x08000000} if os.name == "nt" else {}       # CREATE_NO_WINDOW


# ---- the Python that runs the router ----
def find_python():
    """KiCad's own Python interpreter (inside KiCad, sys.executable may be KiCad itself)."""
    want = "%d.%d" % sys.version_info[:2]
    cands = [sys.executable, os.path.join(sys.prefix, "bin", "python3"), os.path.join(sys.prefix, "bin", "python"),
             os.path.join(sys.prefix, "python.exe"),                          # Windows: KiCad's bin folder
             os.path.join(os.path.dirname(sys.executable), "python.exe"),
             # KiCad's AppImage: its Python is bin/python3.X at the mount ($APPDIR), the
             # prefix being <mount>/shared
             os.path.join(os.environ.get("APPDIR", ""), "bin", "python" + want) if os.environ.get("APPDIR") else None,
             os.path.join(os.path.dirname(sys.prefix), "bin", "python" + want),
             os.path.join(sys.prefix, "bin", "python" + want),
             shutil.which("python3"), shutil.which("python")]
    for c in cands:
        if not c or not os.path.isfile(c) or "python" not in os.path.basename(c).lower():
            continue
        try:
            v = subprocess.run([c, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
                               capture_output=True, text=True, timeout=20, **NO_WINDOW).stdout.strip()
        except Exception:
            continue
        if v == want:              # the same version: packages installed for one work for the other
            return c
    return None


def child_env():
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (DEPS, ROOT, env.get("PYTHONPATH", "")) if p)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def boot(code):
    """`python -c` code that first puts our folders on sys.path: KiCad's Python on
    Windows ignores PYTHONPATH (spawned workers inherit sys.path from it)."""
    return "import sys; sys.path[:0] = %r; %s" % ([DEPS, ROOT], code)


def deps_error(py):
    """Why the router's Python can't import its packages (None: it can)."""
    r = subprocess.run([py, "-c", boot("import " + ", ".join(NEEDED))], env=child_env(), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", **NO_WINDOW)
    return None if r.returncode == 0 else (r.stderr.strip() or "exit code %d" % r.returncode)


def deps_ok(py):
    return deps_error(py) is None


def has_pip(py):
    try:
        return subprocess.run([py, "-m", "pip", "--version"], capture_output=True, timeout=60,
                              **NO_WINDOW).returncode == 0
    except Exception:
        return False


def install_cmd(py):
    """The command that installs NEEDED into DEPS: KiCad's pip, or (KiCad's AppImage
    has none) trouting_wheels.py, which fetches the same wheels from PyPI itself."""
    if not has_pip(py):
        return [py, "-u", os.path.join(HERE, "trouting_wheels.py"), DEPS] + NEEDED
    return [py, "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
            "--upgrade", "--target", DEPS] + NEEDED


# ---- running a child process, with its output line by line ----
class Child:
    def __init__(self, cmd):
        kw = {"start_new_session": True} if os.name == "posix" else \
             {"creationflags": NO_WINDOW["creationflags"] | subprocess.CREATE_NEW_PROCESS_GROUP}
        # stdin: a pipe nobody writes to. It closes when KiCad exits, however that happens
        # (a crash too), which tells the router to stop (trouting.job --watch-stdin)
        self.p = subprocess.Popen(cmd, cwd=ROOT, env=child_env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                  bufsize=1, **kw)
        trouting.keep_awake(self.p.pid)            # macOS: don't idle-sleep mid-route
        self.q = queue.Queue()
        self.lines = []
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.p.stdout:
            self.q.put(line.rstrip("\n"))

    def new_lines(self):
        out = []
        while True:
            try:
                out.append(self.q.get_nowait())
            except queue.Empty:
                break
        self.lines += out
        return out

    def stop(self):
        try:
            if os.name == "posix":
                os.killpg(self.p.pid, signal.SIGTERM)     # the router's worker processes too
            else:                                         # the same on Windows: the whole tree
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.p.pid)], capture_output=True, **NO_WINDOW)
                self.p.kill()
        except Exception:
            pass

    def wait(self, poll, interval=0.15):
        """Run until done; poll(new lines) returns False to cancel. Returns the exit code (None: cancelled)."""
        while self.p.poll() is None:
            if not poll(self.new_lines()):
                self.stop()
                self.p.wait()
                return None
            time.sleep(interval)
        time.sleep(0.2)
        poll(self.new_lines())
        return self.p.returncode


# ---- the routing itself (no dialogs: the test drives this directly) ----
def route_board(board, py, keep_tracks=False, suggest=True, tries=12, poll=lambda lines: True):
    """Route `board` in place. Returns (result dict, new items), or (None, log lines) if
    cancelled or failed."""
    work = tempfile.mkdtemp(prefix="trouting-")
    try:
        bj, rj = os.path.join(work, "board.json"), os.path.join(work, "result.json")
        trouting_kicad.dump(board, bj, rip_up=not keep_tracks)
        job = "import runpy; runpy.run_module('trouting.job', run_name='__main__', alter_sys=True)"
        cmd = [py, "-u", "-c", boot(job), bj, rj, "--tries", str(tries), "--jobs", os.environ.get("TROUTING_JOBS") or str(os.cpu_count() or 4),
               "--watch-stdin"]
        if not suggest:
            cmd.append("--no-suggest")
        ch = Child(cmd)
        rc = ch.wait(poll)
        if rc != 0 or not os.path.exists(rj):
            return None, ch.lines
        with open(rj, encoding="utf-8") as fh:
            res = json.load(fh)
        new = trouting_kicad.apply_routes(board, res["out"], rip_up=not keep_tracks)
        return res, new
    finally:
        shutil.rmtree(work, ignore_errors=True)


def move_part(board, s):
    """Make a suggested move: turn the footprint about its origin, then shift it."""
    fp = board.FindFootprintByReference(s["ref"])
    if fp is None:
        return False
    if s["rot"]:
        fp.SetOrientationDegrees(fp.GetOrientationDegrees() + s["rot"])
    if s["dx"] or s["dy"]:
        fp.Move(pcbnew.VECTOR2I(pcbnew.FromMM(s["dx"]), pcbnew.FromMM(s["dy"])))
    return True


def summary(res):
    out = res["out"]
    u = out["unrouted"]
    lines = []
    if not u:
        lines.append("All connections routed: %d tracks, %d vias." % (len(out["tracks"]), len(out["vias"])))
    else:
        lines.append("%d connection%s not routed:" % (len(u), "s" if len(u) > 1 else ""))
        for x in u[:12]:
            lines.append("    %s  %s" % (x["net"], ", ".join(x.get("pads", []))))
        if len(u) > 12:
            lines.append("    ...")
    return "\n".join(lines)


# ---- the plugin ----
class TroutingPlugin(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "Trouting: route the board"
        self.category = "Routing"
        self.description = ("Route every net of the board at once (negotiated congestion), stitch GND to "
                            "its pour, and suggest parts to move or rotate when it can't route as placed.")
        self.show_toolbar_button = True
        self.icon_file_name = os.path.join(HERE, "icon.png")
        self.dark_icon_file_name = os.path.join(HERE, "icon.png")

    def Run(self):
        import wx
        parent = wx.GetActiveWindow() or wx.FindWindowByName("PcbFrame")
        board = pcbnew.GetBoard()

        py = find_python()
        if not py:
            wx.MessageBox("Trouting couldn't find KiCad's Python interpreter (Python %d.%d) to run the "
                          "router in." % sys.version_info[:2], "Trouting", wx.OK | wx.ICON_ERROR, parent)
            return
        if not deps_ok(py) and not self.install(parent, py):
            return

        opts = OptionsDialog(parent)
        if opts.ShowModal() != wx.ID_OK:
            opts.Destroy()
            return
        keep, sugg, tries = opts.keep.GetValue(), opts.suggest.GetValue(), opts.tries.GetValue()
        opts.Destroy()

        while True:
            res, new = self.run_router(parent, board, py, keep, sugg, tries)
            if res is None:
                return
            pcbnew.Refresh()
            dlg = ResultDialog(parent, res)
            choice = dlg.ShowModal()
            pick = dlg.picked()
            dlg.Destroy()
            if choice != ResultDialog.MOVE_AND_ROUTE or pick is None:
                return
            # take out what this run put in, make the move, and route again
            for it in new:
                try:
                    board.Delete(it)
                except Exception:
                    pass
            move_part(board, pick)
            pcbnew.Refresh()

    def run_router(self, parent, board, py, keep, sugg, tries):
        import wx
        prog = wx.ProgressDialog("Trouting", "Reading the board...", maximum=100, parent=parent,
                                 style=wx.PD_APP_MODAL | wx.PD_CAN_ABORT | wx.PD_ELAPSED_TIME)
        last = ["Routing..."]

        def poll(lines):
            for l in lines:
                if l.strip():
                    last[0] = l.strip()[:90]
            go, _ = prog.Pulse(last[0])
            return go
        try:
            res, new = route_board(board, py, keep_tracks=keep, suggest=sugg, tries=tries, poll=poll)
        finally:
            prog.Destroy()
        if res is None:
            if new and not any("Traceback" in l for l in new):
                return None, None          # cancelled
            wx.MessageBox("The router stopped with an error:\n\n" + "\n".join((new or [])[-15:]),
                          "Trouting", wx.OK | wx.ICON_ERROR, parent)
            return None, None
        return res, new

    def install(self, parent, py):
        import wx
        ask = wx.MessageBox("Trouting needs the Python packages %s, which KiCad's Python doesn't have.\n\n"
                            "Install them now from PyPI (about 55 MB to download, 215 MB on disk), into\n%s ?"
                            % (", ".join(NEEDED), DEPS), "Trouting", wx.YES_NO | wx.ICON_QUESTION, parent)
        if ask != wx.YES:
            return False
        prog = wx.ProgressDialog("Trouting", "Installing %s..." % ", ".join(NEEDED), maximum=100, parent=parent,
                                 style=wx.PD_APP_MODAL | wx.PD_CAN_ABORT | wx.PD_ELAPSED_TIME)
        last = ["Installing..."]

        def poll(lines):
            for l in lines:
                if l.strip():
                    last[0] = l.strip()[:90]
            go, _ = prog.Pulse(last[0])
            return go
        try:
            ch = Child(install_cmd(py))
            rc = ch.wait(poll)
        finally:
            prog.Destroy()
        if rc == 0 and deps_ok(py):
            return True
        if rc is not None:
            wx.MessageBox("Installing the packages failed:\n\n" + "\n".join(ch.lines[-12:]) +
                          "\n\nYou can install them by hand:\n" + " ".join(install_cmd(py)),
                          "Trouting", wx.OK | wx.ICON_ERROR, parent)
        return False


def _dialogs():
    import wx

    class OptionsDialog(wx.Dialog):
        def __init__(self, parent):
            super().__init__(parent, title="Trouting")
            s = wx.BoxSizer(wx.VERTICAL)
            s.Add(wx.StaticText(self, label="Routes every net of the board. Unlocked tracks and vias are replaced;\n"
                                            "locked ones stay. Ctrl+Z undoes it all."), 0, wx.ALL, 12)
            self.keep = wx.CheckBox(self, label="Keep the tracks already on the board (route only what is missing)")
            self.suggest = wx.CheckBox(self, label="If it doesn't route fully, look for parts to move or rotate")
            self.suggest.SetValue(True)
            s.Add(self.keep, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
            s.Add(self.suggest, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
            row = wx.BoxSizer(wx.HORIZONTAL)
            row.Add(wx.StaticText(self, label="Router setups to try (more: better result, longer):"),
                    0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
            self.tries = wx.SpinCtrl(self, min=1, max=12, initial=12)
            row.Add(self.tries)
            s.Add(row, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
            s.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 12)
            self.FindWindowById(wx.ID_OK).SetLabel("Route")
            self.SetSizerAndFit(s)

    class ResultDialog(wx.Dialog):
        MOVE_AND_ROUTE = wx.ID_HIGHEST + 17         # a plain int: ShowModal returns ints

        def __init__(self, parent, res):
            super().__init__(parent, title="Trouting", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
            self.sugg = res.get("suggestions") or []
            s = wx.BoxSizer(wx.VERTICAL)
            s.Add(wx.StaticText(self, label=summary(res)), 0, wx.ALL, 12)
            self.list = None
            if self.sugg:
                s.Add(wx.StaticText(self, label="To make it route, move or rotate a part "
                                                "(each was checked by routing the board with it):"),
                      0, wx.LEFT | wx.RIGHT, 12)
                items = ["%s:  %s" % (g["text"], "routes fully" if not g["unrouted"] else
                                      "%d left unrouted" % g["unrouted"]) for g in self.sugg]
                self.list = wx.ListBox(self, choices=items, style=wx.LB_SINGLE)
                self.list.SetSelection(0)
                s.Add(self.list, 1, wx.EXPAND | wx.ALL, 12)
            elif res["out"]["unrouted"]:
                s.Add(wx.StaticText(self, label="No single move or rotation of a nearby part made it route better."),
                      0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
            s.Add(wx.StaticText(self, label="Run the DRC to check the result."), 0, wx.LEFT | wx.RIGHT, 12)
            btns = wx.BoxSizer(wx.HORIZONTAL)
            if self.sugg:
                b = wx.Button(self, self.MOVE_AND_ROUTE, "Make this move and route again")
                b.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(self.MOVE_AND_ROUTE))
                btns.Add(b, 0, wx.RIGHT, 8)
            btns.Add(wx.Button(self, wx.ID_OK, "Close"))
            s.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 12)
            self.SetSizerAndFit(s)

        def picked(self):
            if not self.list or self.list.GetSelection() < 0:
                return None
            return self.sugg[self.list.GetSelection()]

    return OptionsDialog, ResultDialog


try:
    OptionsDialog, ResultDialog = _dialogs()
except Exception:             # no wx (a test outside KiCad's editor): the routing still works
    OptionsDialog = ResultDialog = None
