"""Review-only numeric mirror of Claude's shader. Does not modify the shader."""
import json
import math
from pathlib import Path
import numpy as np

TAU = 2.0 * math.pi
root = Path(__file__).resolve().parent

def hash2(x, y, s):
    a = np.sin(x * 127.1 + y * 311.7 + s * 74.7) * 43758.5453
    return a - np.floor(a)

def warpfield(x, y, t, ph):
    return (np.sin(TAU * 2.0 * y + 0.10 * t + 1.7 + ph) + 0.5 * np.sin(TAU * (3.0*x-4.0*y) + 0.13*t + ph),
            np.sin(TAU * 2.0 * x - 0.08*t + 0.4 + ph) + 0.5 * np.sin(TAU * (4.0*x+3.0*y) - 0.11*t + 2.0*ph))

def layer(x, y, t, cells, warp, seed, rate, radius=1):
    wx, wy = warpfield(x, y, t, seed)
    px, py = x*cells + warp*wx, y*cells + warp*wy
    cx, cy = np.floor(px), np.floor(py)
    f0, f1, f2 = [np.full_like(x, 9.0) for _ in range(3)]
    for dy in range(-radius, radius+1):
        for dx in range(-radius, radius+1):
            gx, gy = cx+dx, cy+dy
            hx, hy = gx - cells*np.floor(gx/cells), gy - cells*np.floor(gy/cells)
            r1, r2, r3 = hash2(hx, hy, 1+seed), hash2(hx, hy, 2+seed), hash2(hx, hy, 3+seed)
            ang = TAU*r3 + rate*t*(0.6+0.8*r1)
            fx = gx+0.5+0.45*(r1-0.5)+0.22*np.cos(ang)
            fy = gy+0.5+0.45*(r2-0.5)+0.22*np.sin(ang)
            d = np.hypot(px-fx, py-fy)
            n0 = np.minimum(f0, d); d = np.maximum(f0, d)
            n1 = np.minimum(f1, d); d = np.maximum(f1, d)
            n2 = np.minimum(f2, d)
            f0, f1, f2 = n0, n1, n2
    return f1-f0, f2-f0

def evaluate(x, y, t=0.0, variation=0.4, hotspots=2.2, second=0.65, width=0.085, glow=0.5, seed=0.0, radius=1):
    vx, vy = warpfield(x, y, t*0.7, 3.0+seed)
    vary = (1.0-variation) + variation*np.sin(vx*1.7 + vy*1.3)
    e1,j1 = layer(x,y,t,8.0,0.3,seed,1.0,radius)
    e2,j2 = layer(x+0.37,y+0.11,t*1.17,11.0,0.33,seed+5.0,1.2,radius)
    a = np.exp(-e1/width)*(1+hotspots*np.exp(-j1/(width*3.5)))*vary + np.exp(-e1/max(glow,0.001))*0.45
    b = (np.exp(-e2/width)*(1+hotspots*np.exp(-j2/(width*3.5)))*vary + np.exp(-e2/max(glow,0.001))*0.45)*second
    web = a+b-a*b/4.0
    base = 0.28*(0.6+0.4*np.sin(vx+2.0*vy))
    return {"value":np.clip((web+base)/2.96,0,1)**0.9,"variation":vary,"a":a,"b":b,"web":web,"base":base}

def evidence_at_min(field, x, y, data):
    flat = int(np.argmin(field))
    return {"x":float(x.flat[flat]),"y":float(y.flat[flat]),**{k:float(v.flat[flat]) for k,v in data.items()}}

x,y = np.meshgrid(np.linspace(0,1,512,endpoint=False),np.linspace(0,1,512,endpoint=False))
report = {"evidence_limit":"CPU float64 mirror of exact OSL formulas; not an Octane render"}
default = evaluate(x,y)
report["default_variation_range"]=[float(default["variation"].min()),float(default["variation"].max())]
extreme_variation = evaluate(x,y,variation=1.0)
report["variation_1_negative_fraction"]=float(np.mean(extreme_variation["variation"]<0))
report["variation_1_example"]=evidence_at_min(extreme_variation["variation"],x,y,extreme_variation)
bright=evaluate(x,y,variation=0.0,hotspots=10.0,second=1.0,width=0.5)
report["high_hotspots_negative_web_fraction"]=float(np.mean(bright["web"]<0))
report["high_hotspots_example"]=evidence_at_min(bright["web"],x,y,bright)
report["high_hotspots_parameters"]={"Variation":0,"Hotspots":10,"SecondLayer":1,"EdgeWidth":0.5}
report["screen_blend_derivative_wrt_a_at_b_8"]=1-8/4
# A closed-form valid pair proves the unbounded blend reverses with enough intensity.
report["screen_blend_at_a_b_10"]=10+10-10*10/4
radius_report=[]
for seed,t in ((0,0),(0,3.5),(1,9),(13,17)):
    rng=np.random.default_rng(203+int(seed))
    rx,ry=rng.random(40000),rng.random(40000)
    e3,j3=layer(rx,ry,t,8.0,0.3,float(seed),1.0,1)
    e5,j5=layer(rx,ry,t,8.0,0.3,float(seed),1.0,2)
    delta=np.maximum(np.abs(e3-e5),np.abs(j3-j5))
    at=int(np.argmax(delta))
    radius_report.append({"seed":seed,"time":t,"mismatching_fraction":float(np.mean(delta>1e-8)),"max_distance_gap_error":float(delta[at]),"x":float(rx[at]),"y":float(ry[at]),"edge_gap_3x3":float(e3[at]),"edge_gap_5x5":float(e5[at]),"junction_gap_3x3":float(j3[at]),"junction_gap_5x5":float(j5[at])})
report["neighbor_search"]=radius_report
# Smooth temporal field and boundary repeat checks, in the same float64 mirror.
report["tile_boundary_max_error"]=float(np.max(np.abs(evaluate(np.zeros(512),np.linspace(0,1,512))["value"]-evaluate(np.ones(512),np.linspace(0,1,512))["value"])))
report["small_time_step_mean_difference"]=float(np.mean(np.abs(evaluate(x,y,t=0.001)["value"]-default["value"])))
(root/"math-review.json").write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
