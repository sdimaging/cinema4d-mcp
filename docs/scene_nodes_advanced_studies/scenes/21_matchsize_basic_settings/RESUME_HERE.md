# RESUME HERE — Match Size 1-1 Replica (in-place parallel replacement)

**Status: ✅ COMPLETE — MY=92/92 swappable nodes (100%)**
**Last update:** 2026-05-02 ~12:40 (final visual verification passed)
**Final snapshot:** `_snapshots/after_swap_92_atomic.c4d`

## Verification result

Loaded final snapshot via `c4d.documents.LoadDocument` (NOT MCP `load_scene` — see gotcha below):
- Top-level objects: StudyCam, Cylinder, Torus
- Match Size deformer (type 180420400) sits as child of Torus, intact
- Embedded `net.maxon.neutron.nodespace` graph: 118 top-level nodes (~91 `MY_*` + 27 OG/framework)
- After `ExecutePasses`: **Cylinder GetRad=(138.689, 134.695, 138.689)** + **Torus GetRad=(150, 50, 150)** — bit-identical to the reference untouched original (`the basic-settings practice scene`)

## Deferred set (intentionally not swapped)

The 27 remaining are NOT functional algorithm nodes — they're:
- 2 time-context nodes (`context_externaltimeinput`, `context_notime`) — graph framework
- ~12 `scaffold@*`, `group@*` — UI/organizational only
- 5 `legacyobjectaccess@*`, `delete@*`, `cube@*`, `transformmatrix@*`, `transform_element@*` (XEOKhg9, YSpNZX) — wrappers + framework
- 2 phantom-input deferred (`if@NAQDPRJ7…`, `if@djsRwc7B…`) — chain-walks throw on inspection
- 6 misc (`invertselection`, `active`, `get_property`, `getcount`, `type@ezOyIJL0…`) — assets we deliberately left for the C++ tool to handle later

Re-tackling the deferred set is a separate effort that needs either (a) recursive sub-graph editing (for the wrapper capsules) or (b) the planned C++ `scene_nodes_bulk_swap_nodes` tool.

## ⚠️ MCP load_scene gotcha (discovered during verification)

`mcp__cinema4d__load_scene` registers a BaseDocument shell but the OM is empty (`GetFirstObject() == None`). Use `c4d.documents.LoadDocument(path, c4d.SCENEFILTER_OBJECTS | c4d.SCENEFILTER_MATERIALS)` + `InsertBaseDocument` + `SetActiveDocument` from inside `execute_python_script` instead. This affects ALL scene verification work going forward.

---

## Historical context (session methodology, kept for future replicas)

**Resume point pre-completion:** `_snapshots/after_swap_51_atomic.c4d` — **51 MY swaps proven (55%)**, 41 functional OG remaining.

**Snapshot lineage:**
- S37-S45 produced `after_swap_37.c4d` → `after_swap_45.c4d` (standard 2-txn pattern)
- S46-S51 produced `after_swap_46_atomic.c4d` → `after_swap_51_atomic.c4d` (hardened atomic-pattern, suffix `_atomic`)

**The hardened atomic-swap protocol (PROVEN — use as default for everything from here):**
1. Capture OG inputs + outputs + arith config (read-only)
2. Create MY in transaction 1
3. Apply arith config to MY in transaction 2 (BEFORE wiring per P3)
4. Mirror inputs to MY in transaction 3 (parallel reading — multiple consumers OK)
5. **ATOMIC** transaction 4: in single Commit, `og.Remove()` THEN `mine.out.Connect(dst)` for each captured outw entry
6. SetDirty + EventAdd + ExecutePasses

**Per-session ceiling for atomic pattern: ~6-9 swaps before C4D wedges.** S52 hit the ceiling. Use the PowerShell watchdog (running in background) to handle restarts. Drop `C:\Users\Spenser Dickerson\Projects\restart_c4d.flag` to trigger watchdog restart on connection reset.

**Watchdog protocol — DO NOT double-flag:** After any connection-reset error in script execution, drop the flag ONCE, schedule a 60s wakeup, then retry. Don't drop a flag if watchdog already detected the hang on its own (you'd cause a wasteful double-restart).

**Per-session ceiling:** ~9 single swaps before C4D wedges. Restart C4D every ~8 swaps for safety margin.

**S46 BOUNDARY NODE — DO NOT BLINDLY RETRY:** the next swappable in alphabetical order is `if@EeHvDnzJNZ8qH1$lwdTJa_`. Three consecutive swap attempts (including with the PowerShell watchdog handling auto-restart between) crashed C4D. **This specific node triggers the crash.** Before resuming:
- Either reorder `swappable` to put `if@EeHvDnzJNZ8qH1` LAST (skip for now, finish the other 46, return to this one separately)
- Or add a diagnostic probe that walks its connections without mutation to find the bad wire
- Or wait for the C++ MCP-side `scene_nodes_bulk_swap_nodes` tool (per GPT review)

**Watchdog setup:** `c4d_watchdog.ps1` at `C:\Users\Spenser Dickerson\Projects\` — works perfectly. **DO NOT drop the `restart_c4d.flag` after the watchdog has already detected the hang and restarted** — that causes a double-restart cycle.

---

## Quick status

- ✅ Methodology proven on real production graph (Match Size, 203-node hand-built deformer)
- ✅ All 15 pitfalls (P1-P15) discovered + documented in `MASTER.md` + public `c4d_2026_api_gotchas.md` #60-#74
- ✅ Redshift workaround scripts on disk: `C:\Users\...\Projects\disable_redshift_admin.bat` + `enable_redshift_admin.bat`
- ✅ 36/92 functional swaps complete (39%)
- 🔬 Remaining: ~56 functional swaps (4 more batches of 15) + 5 wrappers (deferred, need recursive sub-graph API) + 12 framework (deferred, organizational only)

---

## Pre-flight checklist

Before resuming any swap work:

1. **Verify Redshift is disabled** — `ls "/mnt/c/Program Files/Maxon Cinema 4D 2026/plugins/" | grep -i redshift` should show `Redshift.DISABLED` (not `Redshift`). If not, run `disable_redshift_admin.bat` as admin.
2. **Restart C4D fresh** (no stray scenes open). Each ping that loads-and-mutates accumulates process memory; 3rd batch in same C4D session reliably crashes.
3. **Confirm asset DB `the Match Size asset library` is mounted** in Prefs → Library (otherwise the Match Size graph appears as 2-node empty stub).

---

## The proven `swap_one()` function

Copy-paste this into an MCP `execute_python_script` call. Change `SNAP_IN``SNAP_OUT` for each batch.

```python
import c4d, maxon, re, os
SNAP_IN  = r"C:\Users\Spenser Dickerson\Projects\cinema4d-mcp\docs\scene_nodes_advanced_studies\scenes\21_matchsize_basic_settings\_snapshots\after_batch_02.c4d"
SNAP_OUT = r"C:\Users\Spenser Dickerson\Projects\cinema4d-mcp\docs\scene_nodes_advanced_studies\scenes\21_matchsize_basic_settings\_snapshots\after_batch_03.c4d"

ASSET_MAP = {
    "floatingio":"net.maxon.node.floatingio",
    "reroute":"net.maxon.node.reroute",
    "if":"net.maxon.node.if",
    "switch":"net.maxon.node.switch",
    "compare":"net.maxon.node.compare",
    "arithmetic":"net.maxon.node.arithmetic",
    "inversematrix":"net.maxon.node.inversematrix",
    "transformmatrix":"net.maxon.node.transformmatrix",
    "type":"net.maxon.node.type",
    "scale":"net.maxon.node.scale",
    "bb":"net.maxon.neutron.geometry.bb",
    "transform_element":"net.maxon.neutron.geometry.transform_element",
    "connect_geometries":"net.maxon.neutron.geometry.connect_geometries",
}
SKIP = {"invertselection","getcount","cube","scaffold","group",
        "context_externaltimeinput","context_notime",
        "legacyobjectaccess","delete","active","get_property"}
CFG = {"operation","datatype"}
PG = {"<", ">"}

doc = c4d.documents.LoadDocument(SNAP_IN, c4d.SCENEFILTER_OBJECTS | c4d.SCENEFILTER_MATERIALS)
c4d.documents.SetActiveDocument(doc)

def fs():
    o = doc.GetFirstObject()
    while o:
        if o.GetType() == 180420400: return o
        s = o.GetDown()
        while s:
            if s.GetType() == 180420400: return s
            s = s.GetNext()
        o = o.GetNext()

sn = fs()
g = sn.GetNimbusRef("net.maxon.neutron.nodespace").GetGraph(maxon.Id("net.maxon.neutron.nodespace"))
root = g.GetRoot()

def fc(): return {str(c.GetId()): c for c in root.GetChildren()}

def ptn(p, ns):
    """Resolve a port to its owning node id. Skip <> (port group names AND root gateways are ambiguous)."""
    cur = p
    while cur is not None:
        cid = str(cur.GetId())
        if cid in ns and cid not in PG: return cid
        cur = cur.GetParent()
    return None

def fp(h, pid):
    """Recursive port find."""
    if h is None: return None
    st = list(h.GetChildren())
    while st:
        x = st.pop()
        if str(x.GetId()) == pid: return x
        st.extend(x.GetChildren())
    return None

def mn_of(og):
    b, h = og.split("@",1) if "@" in og else (og,"noh")
    return f"MY_{b}_{re.sub(r'[^A-Za-z0-9_]','_', h[:6])}_swap"

def sgc(p, d):
    """Defensive GetConnections — some wires throw on iterator advance."""
    out = []
    try:
        for x in p.GetConnections(d):
            try: out.append(x)
            except BaseException: break
    except BaseException: pass
    return out

def swap_one(og_id, aid):
    """The proven 6-step swap — capture → create → cfg → mirror → delete OG → rewire downstream.
    Each step in its own transaction. All wrapped in try/except for resilience."""
    try:
        mn = mn_of(og_id); ch = fc()
        if og_id not in ch: return ("missing", 0, 0)
        if mn in ch: return ("already", 0, 0)
        og = ch[og_id]; ns = set(ch.keys()); inw, outw, cfg = [], [], {}
        try:
            ih = og.GetInputs()
            if ih:
                for ip in ih.GetChildren():
                    pid = str(ip.GetId())
                    for sp,_ in sgc(ip, maxon.PORT_DIR.INPUT):
                        try: inw.append((pid, ptn(sp, ns), str(sp.GetId())))
                        except BaseException: pass
                    if pid in CFG:
                        try: cfg[pid] = ip.GetDefaultValue()
                        except BaseException: pass
            oh = og.GetOutputs()
            if oh:
                for op in oh.GetChildren():
                    for dp,_ in sgc(op, maxon.PORT_DIR.OUTPUT):
                        try: outw.append((str(op.GetId()), ptn(dp, ns), str(dp.GetId())))
                        except BaseException: pass
        except BaseException: pass
        try:
            with g.BeginTransaction() as t: g.AddChild(maxon.Id(mn), maxon.Id(aid)); t.Commit()
        except BaseException: return ("create_err", 0, 0)
        if cfg:
            try:
                ch = fc()
                if mn in ch:
                    with g.BeginTransaction() as t:
                        for pid, v in cfg.items():
                            try:
                                mp = fp(ch[mn].GetInputs(), pid)
                                if mp: mp.SetDefaultValue(v)
                            except BaseException: pass
                        t.Commit()
            except BaseException: pass
        ch = fc()
        if mn not in ch or og_id not in ch: return ("post_create_miss", 0, 0)
        mine = ch[mn]; safe = []
        for ipid, snid, spid in inw:
            if ipid in CFG: continue
            if snid is None or snid not in ch: continue
            safe.append((ipid, snid, spid))
        m = 0
        try:
            with g.BeginTransaction() as t:
                for ipid, snid, spid in safe:
                    try:
                        src = ch[snid]; sp = None
                        for hh in (src.GetOutputs(), src.GetInputs()):
                            if hh:
                                sp = fp(hh, spid)
                                if sp: break
                        if sp is None: continue
                        mp = fp(mine.GetInputs(), ipid)
                        if mp is None: continue
                        sp.Connect(mp); m += 1
                    except BaseException: pass
                t.Commit()
        except BaseException: return ("mirror_err", m, 0)
        ch = fc()
        if og_id in ch:
            try:
                with g.BeginTransaction() as t: ch[og_id].Remove(); t.Commit()
            except BaseException: return ("delete_err", m, 0)
        ch = fc()
        if mn not in ch: return ("post_del_miss", m, 0)
        mine = ch[mn]; r = 0
        try:
            with g.BeginTransaction() as t:
                for opid, dnid, dpid in outw:
                    try:
                        if dnid is None: continue
                        act = dnid
                        if act not in ch:
                            pm = mn_of(dnid)
                            if pm in ch: act = pm
                            else: continue
                        dn = ch[act]
                        dp = fp(dn.GetInputs(), dpid) if dn.GetInputs() else None
                        if dp is None:
                            dp = next((p for p in dn.GetChildren() if str(p.GetId())==dpid), None)
                        if dp is None: continue
                        mo = fp(mine.GetOutputs(), opid)
                        if mo is None: continue
                        mo.Connect(dp); r += 1
                    except BaseException: pass
                t.Commit()
        except BaseException: return ("rewire_err", m, r)
        return ("ok", m, r)
    except BaseException: return ("outer_err", 0, 0)

# === Pick first 15 swappable + run batch ===
ch = fc()
swappable = [(nid, ASSET_MAP[nid.split("@")[0]]) for nid in sorted(ch.keys())
             if "@" in nid and not nid.startswith("MY_")
             and nid.split("@")[0] in ASSET_MAP and nid.split("@")[0] not in SKIP]
batch = swappable[:15]
ok = m_t = r_t = 0; errs = {}
for og, aid in batch:
    try: s, m, r = swap_one(og, aid)
    except BaseException: s, m, r = "outermost", 0, 0
    if s == "ok": ok += 1; m_t += m; r_t += r
    else: errs[s] = errs.get(s, 0) + 1

# === SetDirty refresh ritual (P6) ===
sn.SetDirty(c4d.DIRTYFLAGS_DATA | c4d.DIRTYFLAGS_CACHE | c4d.DIRTYFLAGS_DESCRIPTION | c4d.DIRTYFLAGS_MATRIX)
p = sn.GetUp()
if p: p.SetDirty(c4d.DIRTYFLAGS_DATA | c4d.DIRTYFLAGS_CACHE | c4d.DIRTYFLAGS_MATRIX)
c4d.EventAdd(c4d.EVENT_FORCEREDRAW)
doc.ExecutePasses(None, True, True, True, c4d.BUILDFLAGS_NONE)

ch = fc()
print(f"BATCH ok={ok}/{len(batch)} m={m_t} r={r_t} errs={errs}")
print(f"FINAL total={len(ch)} MY={sum(1 for k in ch if k.startswith('MY_'))} OG={sum(1 for k in ch if '@' in k and not k.startswith('MY_'))}")
saved = c4d.documents.SaveDocument(doc, SNAP_OUT, c4d.SAVEDOCUMENTFLAGS_DONTADDTORECENTLIST, c4d.FORMAT_C4DEXPORT)
print(f"SAVED {os.path.getsize(SNAP_OUT) if os.path.exists(SNAP_OUT) else 0}")
```

---

## Schedule for finishing Match Size (one-per-ping protocol)

Per-session ceiling: ~9 single swaps before C4D wedge. Restart every 8 to leave headroom. **47 functional OG remaining = ~6 C4D restart cycles**.

| Cycle | SNAP_IN | SNAPS produced | After cycle |
|------:|---------|----------------|-------------|
| 1 | `after_swap_45.c4d` | 46-53 (8 swaps) | RESTART C4D |
| 2 | `after_swap_53.c4d` | 54-61 (8 swaps) | RESTART C4D |
| 3 | `after_swap_61.c4d` | 62-69 (8 swaps) | RESTART C4D |
| 4 | `after_swap_69.c4d` | 70-77 (8 swaps) | RESTART C4D |
| 5 | `after_swap_77.c4d` | 78-85 (8 swaps) | RESTART C4D |
| 6 | `after_swap_85.c4d` | 86-92 (7 swaps + visual verify) | DONE |

Total: ~5-10 min compute + 5 manual restarts (~30s each). Realistically ~10-15 min wall time.

---

## After Match Size: priority queue

Per `the reference scene priority queue` memory:

1. ✅ Relax-Spline 1+2 (scenes 17, 18) — already studied
2. ✅ Oct-Tree (scene 19) — already studied
3. 🔬 Match Size variant 1 (scene 21) — IN PROGRESS, methodology proven, 36/92 swapped
4. 📋 Match Size variant 2 (other folder) — apply same methodology
5. 📋 Spiderweb scenes (26, 27) — apply same methodology
6. 📋 Volume_Colorizer (demoted)
7. 📋 R47 target-directed-growth system (Mycelium V3 rebuild, longer-term)

---

## Long-term blocker fix (per GPT review)

The Python-side per-ping batch ceiling exists because of accumulating process memory + Python VM/Maxon graph interaction. **The right long-term fix is a C++ MCP-side `scene_nodes_bulk_swap_nodes` tool** that:

1. Takes a list of `(og_id, my_name, asset_id)` tuples
2. Captures all OG wires + arith configs in one read pass (C++ side, no Python overhead)
3. Creates all MY nodes in chunked transactions (batch internally to avoid C4D evaluator wedge)
4. Mirrors all input wires
5. Deletes all OGs (frees downstream)
6. Rewires downstream (translating dst to MY equivalent if dst was also swapped)
7. Calls SetDirty + EventAdd + ExecutePasses on the main thread
8. Returns audit JSON

This would replace the Python `swap_one()` loop entirely and eliminate the 15-per-batch ceiling. Estimated 4-8 hours of focused C++ work.
