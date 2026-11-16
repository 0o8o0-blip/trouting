/* Trouting's hot loop: grid A* with negotiated congestion (PathFinder) costs,
 * and the occupancy bookkeeping that makes nets "see" each other.
 *
 * The grid: L copper layers of W x H cells, node = l*W*H + y*W + x.
 *
 * Static copper (pads, board edge, keepouts, holes, locked tracks) is a hard
 * block, precomputed in Python as, per node: d1 = distance (mm, minus the
 * item's own clearance) to the nearest copper, n1 = its net, d2 = the same
 * for the nearest copper of any other net. A net may put a track centreline
 * on a node when (n1 == net ? d2 : d1) >= its threshold (half width + margin).
 *
 * Other nets' routed copper is soft: each routed net stamps, into occ[k], a
 * band around its copper as wide as the spacing a shape of class k must keep
 * from it (class = a track width or a via diameter). So "may a track of
 * class k go here" is a single lookup: occ[k][node] counts the other nets in
 * the way. Overlap is allowed but costs more each iteration, plus a history
 * cost that remembers where the fights were: nets that have alternatives
 * move away, the one that needs the spot keeps it.
 */
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    int W, H, L, K;
    long N;                 /* nodes = L*W*H */
    const float *d1, *d2;   /* L*W*H */
    const int32_t *n1;
    const uint8_t *via_block;
    uint16_t *occ;          /* K*N */
    float *hist;            /* N */
    float layer_cost[32];
    /* search scratch */
    float *g;
    int32_t *parent;
    uint32_t *seen;         /* generation when g/parent were set */
    uint32_t *mark_gen;
    uint8_t *mark;          /* 1 source, 2 target, 4 own copper (always allowed), 8 no via */
    uint32_t *stamp_gen;
    uint32_t gen, sgen;
    /* heap */
    float *hf;
    int32_t *hn;
    long hcap, hlen;
} grid_t;

enum { M_SRC = 1, M_TGT = 2, M_OWN = 4, M_NOVIA = 8 };

grid_t *g_new(int W, int H, int L, int K, const float *d1, const float *d2, const int32_t *n1,
              const uint8_t *via_block, const float *layer_cost)
{
    grid_t *G = calloc(1, sizeof *G);
    G->W = W; G->H = H; G->L = L; G->K = K;
    G->N = (long)L * W * H;
    G->d1 = d1; G->d2 = d2; G->n1 = n1; G->via_block = via_block;
    for (int l = 0; l < L && l < 32; l++) G->layer_cost[l] = layer_cost[l];
    G->occ = calloc((size_t)K * G->N, sizeof *G->occ);
    G->hist = calloc(G->N, sizeof *G->hist);
    G->g = malloc(G->N * sizeof *G->g);
    G->parent = malloc(G->N * sizeof *G->parent);
    G->seen = calloc(G->N, sizeof *G->seen);
    G->mark_gen = calloc(G->N, sizeof *G->mark_gen);
    G->mark = calloc(G->N, 1);
    G->stamp_gen = calloc(G->N, sizeof *G->stamp_gen);
    G->hcap = 1 << 20;
    G->hf = malloc(G->hcap * sizeof *G->hf);
    G->hn = malloc(G->hcap * sizeof *G->hn);
    return G;
}

void g_free(grid_t *G)
{
    free(G->occ); free(G->hist); free(G->g); free(G->parent); free(G->seen);
    free(G->mark_gen); free(G->mark); free(G->stamp_gen); free(G->hf); free(G->hn);
    free(G);
}

uint16_t *g_occ(grid_t *G) { return G->occ; }
float *g_hist(grid_t *G) { return G->hist; }

/* ---- heap ---- */
static void hpush(grid_t *G, float f, int32_t n)
{
    if (G->hlen == G->hcap) {
        G->hcap *= 2;
        G->hf = realloc(G->hf, G->hcap * sizeof *G->hf);
        G->hn = realloc(G->hn, G->hcap * sizeof *G->hn);
    }
    long i = G->hlen++;
    while (i > 0) {
        long p = (i - 1) / 2;
        if (G->hf[p] <= f) break;
        G->hf[i] = G->hf[p]; G->hn[i] = G->hn[p]; i = p;
    }
    G->hf[i] = f; G->hn[i] = n;
}

static int32_t hpop(grid_t *G, float *f)
{
    int32_t top = G->hn[0];
    *f = G->hf[0];
    float lf = G->hf[--G->hlen];
    int32_t ln = G->hn[G->hlen];
    long i = 0, n = G->hlen;
    for (;;) {
        long c = 2 * i + 1;
        if (c >= n) break;
        if (c + 1 < n && G->hf[c + 1] < G->hf[c]) c++;
        if (G->hf[c] >= lf) break;
        G->hf[i] = G->hf[c]; G->hn[i] = G->hn[c]; i = c;
    }
    if (n) { G->hf[i] = lf; G->hn[i] = ln; }
    return top;
}

static inline uint8_t mk(const grid_t *G, long v) { return G->mark_gen[v] == G->gen ? G->mark[v] : 0; }

static inline int static_ok(const grid_t *G, long v, int net, float thr)
{
    float d = G->n1[v] == net ? G->d2[v] : G->d1[v];
    return d >= thr;
}

static inline int track_ok(const grid_t *G, long v, int net, float thr)
{
    return static_ok(G, v, net, thr) || (mk(G, v) & M_OWN);
}

static int via_ok(const grid_t *G, long xy, int net, float vthr)
{
    long WH = (long)G->W * G->H;
    for (int l = 0; l < G->L; l++) {
        long v = l * WH + xy;
        if (G->via_block[v] || (mk(G, v) & M_NOVIA)) return 0;
        if (!static_ok(G, v, net, vthr)) return 0;
    }
    return 1;
}

/* Set marks for this search: srcs (M_SRC), tgts (M_TGT), own (M_OWN), novia (M_NOVIA). */
void g_begin(grid_t *G)
{
    G->gen++;
    if (G->gen == 0) { memset(G->mark_gen, 0, G->N * sizeof *G->mark_gen); memset(G->seen, 0, G->N * sizeof *G->seen); G->gen = 1; }
}

void g_mark(grid_t *G, const int32_t *nodes, long n, int bit)
{
    for (long i = 0; i < n; i++) {
        long v = nodes[i];
        if (G->mark_gen[v] != G->gen) { G->mark_gen[v] = G->gen; G->mark[v] = 0; }
        G->mark[v] |= bit;
    }
}

/* A* from every source node to the nearest target node.
 * kt / kv: occupancy class of this net's tracks / vias.
 * thr / vthr: static thresholds (mm) for a track / a via.
 * bbox: x0,y0,x1,y1 (inclusive) the search stays inside; tb: the targets' bbox for the heuristic.
 * Costs, in cells: a step costs its length * layer cost * (1 + hist) * (1 + pres * others),
 * a via via_cost * (1 + hist) * (1 + pres * others).
 * hard: other nets' copper is a wall, not a cost.
 * Writes the path target -> source into out (at most maxout nodes); returns its length,
 * -1 if no path, -2 if the path didn't fit in out. */
long g_route(grid_t *G, int net, int kt, int kv, float thr, float vthr,
             float via_cost, float pres, float hmul, int hard,
             int x0, int y0, int x1, int y1, int tx0, int ty0, int tx1, int ty1,
             int32_t *out, long maxout)
{
    const int W = G->W, H = G->H, L = G->L;
    const long WH = (long)W * H, N = G->N;
    const uint16_t *occt = G->occ + (long)kt * N, *occv = G->occ + (long)kv * N;
    const float SQ2 = 1.41421356f;
    static const int DX[8] = {1, -1, 0, 0, 1, 1, -1, -1}, DY[8] = {0, 0, 1, -1, 1, -1, 1, -1};
    float minlc = 1e9f;
    for (int l = 0; l < L; l++) if (G->layer_cost[l] < minlc) minlc = G->layer_cost[l];
    if (x0 < 0) x0 = 0;
    if (y0 < 0) y0 = 0;
    if (x1 >= W) x1 = W - 1;
    if (y1 >= H) y1 = H - 1;
    uint32_t gen = G->gen;              /* the queue holds the seeds from g_seed */

#define HEUR(x, y) ({ int ddx = (x) < tx0 ? tx0 - (x) : (x) > tx1 ? (x) - tx1 : 0; \
                      int ddy = (y) < ty0 ? ty0 - (y) : (y) > ty1 ? (y) - ty1 : 0; \
                      int mn = ddx < ddy ? ddx : ddy, mx = ddx < ddy ? ddy : ddx; \
                      hmul * minlc * (mx - mn + SQ2 * mn); })

    long found = -1;
    float f;
    while (G->hlen) {
        long u = hpop(G, &f);
        if (G->seen[u] != gen) continue;
        float gu = G->g[u];
        int l = u / WH, xy = u % WH, x = xy % W, y = xy / W;
        if (f > gu + HEUR(x, y) + 1e-3f) continue;        /* stale */
        if (mk(G, u) & M_TGT) { found = u; break; }
        for (int d = 0; d < 8; d++) {
            int nx = x + DX[d], ny = y + DY[d];
            if (nx < x0 || nx > x1 || ny < y0 || ny > y1) continue;
            long v = l * WH + (long)ny * W + nx;
            if (!track_ok(G, v, net, thr)) continue;
            if (hard && occt[v]) continue;
            float c = (d < 4 ? 1.0f : SQ2) * G->layer_cost[l] * (1.0f + G->hist[v]) * (1.0f + pres * occt[v]);
            float gv = gu + c;
            if (G->seen[v] != gen || gv < G->g[v]) {
                G->seen[v] = gen; G->g[v] = gv; G->parent[v] = u;
                hpush(G, gv + HEUR(nx, ny), v);
            }
        }
        if (L > 1 && via_ok(G, xy, net, vthr)) {
            int o = 0;
            float hs = 0;
            for (int l2 = 0; l2 < L; l2++) {
                long v = l2 * WH + xy;
                if (occv[v] > o) o = occv[v];
                hs += G->hist[v];
            }
            float c = via_cost * (1.0f + hs / L) * (1.0f + pres * o);
            if (hard && o) goto novia;
            for (int l2 = 0; l2 < L; l2++) {
                if (l2 == l) continue;
                long v = l2 * WH + xy;
                if (!track_ok(G, v, net, thr)) continue;
                float gv = gu + c;
                if (G->seen[v] != gen || gv < G->g[v]) {
                    G->seen[v] = gen; G->g[v] = gv; G->parent[v] = u;
                    hpush(G, gv + HEUR(x, y), v);
                }
            }
        }
    novia:;
    }
    if (found < 0) return -1;
    long n = 0;
    for (long v = found; v >= 0; v = G->parent[v]) {
        if (n >= maxout) return -2;
        out[n++] = v;
    }
    return n;
#undef HEUR
}

/* Seed the search with the source nodes (call after g_begin/g_mark, before g_route).
 * Sources that are not allowed for a track are skipped. Returns how many were seeded. */
long g_seed(grid_t *G, const int32_t *src, long n, int net, float thr,
            int tx0, int ty0, int tx1, int ty1, float hmul)
{
    const long WH = (long)G->W * G->H;
    float minlc = 1e9f;
    for (int l = 0; l < G->L; l++) if (G->layer_cost[l] < minlc) minlc = G->layer_cost[l];
    G->hlen = 0;
    long k = 0;
    for (long i = 0; i < n; i++) {
        long v = src[i];
        if (!track_ok(G, v, net, thr)) continue;
        int xy = v % WH, x = xy % G->W, y = xy / G->W;
        int ddx = x < tx0 ? tx0 - x : x > tx1 ? x - tx1 : 0;
        int ddy = y < ty0 ? ty0 - y : y > ty1 ? y - ty1 : 0;
        int mn = ddx < ddy ? ddx : ddy, mx = ddx < ddy ? ddy : ddx;
        G->seen[v] = G->gen; G->g[v] = 0; G->parent[v] = -1;
        hpush(G, hmul * minlc * (mx - mn + 1.41421356f * mn), v);
        k++;
    }
    return k;
}

/* Add (sign=+1) or remove (sign=-1) one net's band to every class's occupancy.
 * nodes: the net's track nodes; vias: the (layer 0) xy of its vias.
 * rt[k] / rv[k]: band radius in cells around a track node / a via, for class k.
 * Each net counts once per cell (the bands of its own nodes are unioned). */
void g_stamp(grid_t *G, const int32_t *nodes, long n, const int32_t *vias, long nv,
             const float *rt, const float *rv, int sign)
{
    const int W = G->W, H = G->H, L = G->L;
    const long WH = (long)W * H;
    for (int k = 0; k < G->K; k++) {
        uint16_t *occ = G->occ + (long)k * G->N;
        G->sgen++;
        if (G->sgen == 0) { memset(G->stamp_gen, 0, G->N * sizeof *G->stamp_gen); G->sgen = 1; }
        for (int pass = 0; pass < 2; pass++) {
            long cnt = pass ? nv : n;
            float r = pass ? rv[k] : rt[k];
            int R = (int)ceilf(r);
            float r2 = r * r;
            for (long i = 0; i < cnt; i++) {
                long c = pass ? vias[i] : nodes[i];
                int l0 = c / WH, xy = c % WH, cx = xy % W, cy = xy / W;
                int la = pass ? 0 : l0, lb = pass ? L - 1 : l0;
                for (int l = la; l <= lb; l++)
                    for (int dy = -R; dy <= R; dy++) {
                        int y = cy + dy;
                        if (y < 0 || y >= H) continue;
                        for (int dx = -R; dx <= R; dx++) {
                            int x = cx + dx;
                            if (x < 0 || x >= W || dx * dx + dy * dy >= r2) continue;
                            long v = l * WH + (long)y * W + x;
                            if (G->stamp_gen[v] == G->sgen) continue;
                            G->stamp_gen[v] = G->sgen;
                            occ[v] = (uint16_t)(occ[v] + sign);
                        }
                    }
            }
        }
    }
}

/* Nodes of a routed (and stamped) net that another net's band covers.
 * Writes them to out; returns how many. Adds hadd to their history if hadd > 0. */
long g_conflicts(grid_t *G, const int32_t *nodes, long n, const int32_t *vias, long nv,
                 int kt, int kv, float hadd, int32_t *out)
{
    const long WH = (long)G->W * G->H;
    const uint16_t *occt = G->occ + (long)kt * G->N, *occv = G->occ + (long)kv * G->N;
    long m = 0;
    for (long i = 0; i < n; i++)
        if (occt[nodes[i]] >= 2) {
            out[m++] = nodes[i];
            if (hadd > 0) G->hist[nodes[i]] += hadd;
        }
    for (long i = 0; i < nv; i++)
        for (int l = 0; l < G->L; l++) {
            long v = l * WH + vias[i];
            if (occv[v] >= 2) {
                out[m++] = v;
                if (hadd > 0) G->hist[v] += hadd;
            }
        }
    return m;
}
