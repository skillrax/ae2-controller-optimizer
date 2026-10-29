"""Core decomposition feasibility (RESEARCH.md open problem 4): count 5x5x5 core patterns."""
import numpy as np, itertools, time
cells=[(i,j,k) for i in range(3) for j in range(3) for k in range(3)]
idx={c:n for n,c in enumerate(cells)}
# cross rule checkable inside the core: a cell that is the middle on 2+ axes
rules=[]
for c in cells:
    axes=[]
    for a in range(3):
        if c[a]==1:
            lo=list(c); hi=list(c); lo[a]=0; hi[a]=2
            axes.append((idx[tuple(lo)],idx[tuple(hi)]))
    if len(axes)>=2: rules.append((idx[c],axes))
t=time.time(); total=0; ok=0; CH=1<<24
for start in range(0,1<<27,CH):
    v=np.arange(start,start+CH,dtype=np.uint32)
    bad=np.zeros(CH,bool)
    bit=lambda i:(v>>i)&1
    for c,axes in rules:
        pairs=sum((bit(a)&bit(b)).astype(np.uint8) for a,b in axes)
        bad|=(bit(c)==1)&(pairs>=2)
    ok+=int((~bad).sum())
print("all",1<<27,"cross-ok",ok,"approx classes /48:",ok//48, round(time.time()-t),"s")
