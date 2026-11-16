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
    lib.g_new.argtypes = [i, i, i, i, P, P, P, P, P]
    lib.g_free.argtypes = [P]
    lib.g_occ.restype = P
    lib.g_occ.argtypes = [P]
    lib.g_hist.restype = P
    lib.g_hist.argtypes = [P]
    lib.g_begin.argtypes = [P]
    lib.g_mark.argtypes = [P, P, l, i]
    lib.g_seed.restype = l
    lib.g_seed.argtypes = [P, P, l, i, f, i, i, i, i, f]
    lib.g_route.restype = l
    lib.g_route.argtypes = [P, i, i, i, f, f, f, f, f, i, i, i, i, i, i, i, i, i, P, l]
    lib.g_stamp.argtypes = [P, P, l, P, l, P, P, i]
    lib.g_conflicts.restype = l
    lib.g_conflicts.argtypes = [P, P, l, P, l, i, i, f, P]
    return lib


LIB = _lib()

M_SRC, M_TGT, M_OWN, M_NOVIA = 1, 2, 4, 8


def ptr(a):
    return a.ctypes.data_as(C.c_void_p)


def i32(a):
    return np.ascontiguousarray(a, dtype=np.int32)


class Grid:
    def __init__(self, board, K, layer_cost):
        b = board
        self.keep = [np.ascontiguousarray(x) for x in (b.d1, b.d2, b.n1, b.via_block)]
        self.lc = np.asarray(layer_cost, np.float32)
        self.g = LIB.g_new(b.W, b.H, b.L, K, *[ptr(x) for x in self.keep], ptr(self.lc))
        self.N = b.L * b.WH
        self.K = K
        self.occ = np.ctypeslib.as_array(C.cast(LIB.g_occ(self.g), C.POINTER(C.c_uint16)), (K * self.N,))
        self.hist = np.ctypeslib.as_array(C.cast(LIB.g_hist(self.g), C.POINTER(C.c_float)), (self.N,))
        self.out = np.zeros(1 << 22, np.int32)

    def __del__(self):
        if getattr(self, "g", None):
            LIB.g_free(self.g)
            self.g = None

    def route(self, net, kt, kv, thr, vthr, src, tgt, own, novia, bbox, tbox,
              via_cost, pres, hmul=1.0, hard=False):
        LIB.g_begin(self.g)
        for arr, bit in ((src, M_SRC), (tgt, M_TGT), (own, M_OWN), (novia, M_NOVIA)):
            if len(arr):
                a = i32(arr)
                LIB.g_mark(self.g, ptr(a), len(a), bit)
        s = i32(src)
        if LIB.g_seed(self.g, ptr(s), len(s), net, thr, *tbox, hmul) == 0:
            return None
        n = LIB.g_route(self.g, net, kt, kv, thr, vthr, via_cost, pres, hmul, int(hard),
                        *bbox, *tbox, ptr(self.out), len(self.out))
        if n < 0:
            return None
        return self.out[:n][::-1].copy()

    def stamp(self, nodes, vias, rt, rv, sign):
        nodes, vias = i32(nodes), i32(vias)
        rt, rv = np.asarray(rt, np.float32), np.asarray(rv, np.float32)
        LIB.g_stamp(self.g, ptr(nodes), len(nodes), ptr(vias), len(vias), ptr(rt), ptr(rv), sign)

    def conflicts(self, nodes, vias, kt, kv, hadd=0.0):
        nodes, vias = i32(nodes), i32(vias)
        out = np.zeros(len(nodes) + len(vias) * 32 + 1, np.int32)
        m = LIB.g_conflicts(self.g, ptr(nodes), len(nodes), ptr(vias), len(vias), kt, kv, hadd, ptr(out))
        return out[:m]
