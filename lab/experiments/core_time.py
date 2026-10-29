"""Core decomposition feasibility: time relaxed solves with the 5x5x5 core fixed. Run with PYTHONPATH=."""
import random, time, json, sys
from ortools.sat.python import cp_model
from ae2opt import DEFAULT_PROFILE, library
from ae2opt.optimize import OptimizeSpec, _Model
CORE=[(i,j,k) for i in range(1,4) for j in range(1,4) for k in range(1,4)]
def cross_ok(X):
    for c in X:
        n=0
        for a in range(3):
            lo=list(c); hi=list(c); lo[a]-=1; hi[a]+=1
            if tuple(lo) in X and tuple(hi) in X: n+=1
        if n>=2: return False
    return True
def solve(core_x, routing, target=None, secs=60, workers=1):
    req=frozenset(core_x); forb=frozenset(set(CORE)-req)
    model=_Model(OptimizeSpec(size=(5,5,5), required=req, forbidden=forb), DEFAULT_PROFILE, (5,5,5), routing=routing)
    m=model.clone()
    if target is not None:
        m.Add(model.tunnels>=target); m.ClearObjective()
    s=cp_model.CpSolver(); s.parameters.max_time_in_seconds=secs; s.parameters.num_workers=workers
    t=time.monotonic(); st=s.StatusName(s.Solve(m))
    return st, (int(s.ObjectiveValue()) if target is None and st in("OPTIMAL","FEASIBLE") else None), int(s.BestObjectiveBound()) if target is None else None, round(time.monotonic()-t,1)
lib=frozenset(c for c in library.load("5x5x5").controllers() if c in set(CORE))
print("library core", len(lib), "ctrl:", solve(lib,"flow"), flush=True)
random.seed(1); samples=[]
while len(samples)<6:
    X=frozenset(c for c in CORE if random.random()<0.6)
    if cross_ok(X): samples.append(X)
for X in samples:
    print(len(X), "relaxed opt", solve(X,"flow"), "| relaxed >=299", solve(X,"flow",299), flush=True)
