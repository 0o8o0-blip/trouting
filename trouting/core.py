"""ctypes binding for core.c (compiled on first use, rebuilt when the source changes)."""
import ctypes as C
import hashlib
import os
import subprocess

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "core.c")


def _lib():
    h = hashlib.sha1(open(SRC, "rb").read()).hexdigest()[:12]
    so = os.path.join(HERE, "_build", "core-%s.so" % h)
    if not os.path.exists(so):
        os.makedirs(os.path.dirname(so), exist_ok=True)
        subprocess.check_call(["gcc", "-O3", "-march=native", "-shared", "-fPIC", "-Wall", "-o", so, SRC, "-lm"])
    lib = C.CDLL(so)
    P = C.c_void_p
    f, i, l = C.c_float, C.c_int, C.c_long
    lib.g_new.restype = P
    lib.g_new.argtypes = [i, i, i, i, i, P, P, P, P, P]
    lib.g_free.argtypes = [P]
    lib.g_occ.restype = P
    lib.g_occ.argtypes = [P]
    lib.g_hist.restype = P
    lib.g_hist.argtypes = [P]
    lib.g_begin.argtypes = [P, i]
    lib.g_mark.argtypes = [P, P, l, i]
    lib.g_seed.restype = l
    lib.g_seed.argtypes = [P, P, l, i, f, f, i, i, i, f, i, i, i, i, i, f]
    lib.g_route.restype = l
    lib.g_route.argtypes = [P, i, i, i, f, f, i, f, f, f, f, f, f, f, i, i, i, i, i, i, i, i, i, P, l]
    lib.g_stamp.argtypes = [P, P, l, P, P, l, P, P, P, i]
    lib.g_conflicts.restype = l
    lib.g_conflicts.argtypes = [P, P, l, P, P, l, i, i, i, f, P]
    return lib


LIB = _lib()

M_SRC, M_TGT, M_OWN, M_NOVIA, M_OWN2 = 1, 2, 4, 8, 16


class Path(np.ndarray):
    """A routed path: its grid nodes (an int32 array), and .thin, which of them
    are narrow track (None: none). Anything sliced or joined from a path is a
    plain array of nodes again."""
    def __new__(cls, nodes, thin=None):
        p = np.asarray(nodes, np.int32).view(cls)
        p.thin = None if thin is None or not np.any(thin) else np.asarray(thin, bool)
        return p

    def __array_finalize__(self, obj):
        self.thin = None


def thin_of(p):
    t = getattr(p, "thin", None)
    return t if t is not None else np.zeros(len(p), bool)


def ptr(a):
    return a.ctypes.data_as(C.c_void_p)


def i32(a):
    return np.ascontiguousarray(a, dtype=np.int32)


class Grid:
    def __init__(self, board, K, layer_cost):
        b = board
        self.keep = [np.ascontiguousarray(x) for x in (b.D1, b.D2, b.N1, b.via_block)]
        self.lc = np.asarray(layer_cost, np.float32)
        self.g = LIB.g_new(b.W, b.H, b.L, K, len(b.clrs), *[ptr(x) for x in self.keep], ptr(self.lc))
        self.N = b.L * b.WH
        self.K = K
        self.occ = np.ctypeslib.as_array(C.cast(LIB.g_occ(self.g), C.POINTER(C.c_uint16)), (K * self.N,))
        self.hist = np.ctypeslib.as_array(C.cast(LIB.g_hist(self.g), C.POINTER(C.c_float)), (self.N,))
        self.out = np.zeros(1 << 22, np.int32)
        self.empty = np.zeros(0, np.int32)

    def __del__(self):
        if getattr(self, "g", None):
            LIB.g_free(self.g)
            self.g = None

    def route(self, net, cc, kt, kv, thr, vthr, src, tgt, own, novia, bbox, tbox,
              via_cost, pres, hmul=1.0, hard=False, kt2=None, thr2=None, own2=None, neck_cost=0.0, neck_switch=4.0, turn_cost=0.0):
        """A path (a Path, source first) or None. With neck_cost > 0 the track may
        narrow to class kt2 (threshold thr2, own copper own2) where it has to."""
        LIB.g_begin(self.g, cc)
        narrow = neck_cost > 0
        marks = [(src, M_SRC), (tgt, M_TGT), (own, M_OWN), (novia, M_NOVIA)]
        if narrow:
            marks += [(own, M_OWN2), (own2, M_OWN2)]
        for arr, bit in marks:
            if arr is not None and len(arr):
                a = i32(arr)
                LIB.g_mark(self.g, ptr(a), len(a), bit)
        s = i32(src)
        if not narrow:
            kt2, thr2 = kt, thr
        if LIB.g_seed(self.g, ptr(s), len(s), net, thr, thr2, int(narrow), kt, kt2, pres, int(hard),
                      *tbox, hmul) == 0:
            return None
        n = LIB.g_route(self.g, net, kt, kv, thr, vthr, kt2, thr2, neck_cost if narrow else 0.0, neck_switch, turn_cost,
                        via_cost, pres, hmul, int(hard), *bbox, *tbox, ptr(self.out), len(self.out))
        if n < 0:
            return None
        u = self.out[:n][::-1]
        thin = u >= self.N
        nodes = np.where(thin, u - self.N, u)
        # a width change is two search nodes on one grid node: keep one, wide (the
        # wider stamp is the safe one; both widths fit there)
        if n > 1:
            same = np.r_[False, nodes[1:] == nodes[:-1]]
            if same.any():
                thin[np.nonzero(same)[0] - 1] = False
                keep = ~same
                nodes, thin = nodes[keep], thin[keep]
        return Path(nodes, thin)

    def stamp(self, nodes, thin, vias, rt, rt2, rv, sign):
        nodes, vias = i32(nodes), i32(vias)
        th = np.ascontiguousarray(thin, np.uint8) if thin is not None else None
        rt, rt2, rv = (np.asarray(x, np.float32) for x in (rt, rt2, rv))
        LIB.g_stamp(self.g, ptr(nodes), len(nodes), ptr(th) if th is not None else None,
                    ptr(vias), len(vias), ptr(rt), ptr(rt2), ptr(rv), sign)

    def conflicts(self, nodes, thin, vias, kt, kt2, kv, hadd=0.0):
        nodes, vias = i32(nodes), i32(vias)
        th = np.ascontiguousarray(thin, np.uint8) if thin is not None else None
        out = np.zeros(len(nodes) + len(vias) * 32 + 1, np.int32)
        m = LIB.g_conflicts(self.g, ptr(nodes), len(nodes), ptr(th) if th is not None else None,
                            ptr(vias), len(vias), kt, kt2, kv, hadd, ptr(out))
        return out[:m]
