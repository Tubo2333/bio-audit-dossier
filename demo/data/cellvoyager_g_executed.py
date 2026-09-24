# ── notebook cell 2 ──
import scanpy as sc
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

print("Loading data...")
adata = sc.read_h5ad(r'''<absolute-path-stripped>''')
print(f"Loaded: {adata.n_obs} cells x {adata.n_vars} genes")

# ── notebook cell 5 ──
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from scipy import stats

# --- QC metrics (before filtering) ---
adata.var['mt'] = adata.var_names.str.startswith('MT-')
sc.pp.calculate_qc_metrics(adata, qc_vars=['mt'], percent_top=None, log1p=False, inplace=True)
sc.pl.violin(adata, ['n_genes_by_counts', 'total_counts', 'pct_counts_mt'], jitter=0.4, multi_panel=True)

# --- Filtering (Smart-seq2: lenient, MT% + gene floor; drop extreme outliers) ---
print('Before filtering:', adata.shape)
sc.pp.filter_cells(adata, min_genes=1000)
sc.pp.filter_cells(adata, min_counts=1000)
sc.pp.filter_cells(adata, max_genes=12000)
sc.pp.filter_genes(adata, min_cells=3)
adata = adata[adata.obs['pct_counts_mt'] < 20].copy()
print('After filtering:', adata.shape)

# --- Normalize, log-transform, keep a log-norm layer ---
sc.pp.normalize_total(adata, target_sum=1e4)
sc.pp.log1p(adata)
adata.layers['log_norm'] = adata.X.copy()

# --- HVG selection; preserve full matrix in .raw ---
sc.pp.highly_variable_genes(adata, min_mean=0.0125, max_mean=3, min_disp=0.5)
print('HVGs:', int(adata.var['highly_variable'].sum()))
adata.raw = adata
adata = adata[:, adata.var['highly_variable']].copy()

# --- Scale, PCA, neighbors, UMAP, Leiden ---
sc.pp.scale(adata, max_value=10)
sc.tl.pca(adata, svd_solver='arpack', n_comps=50)
sc.pp.neighbors(adata, n_neighbors=15, n_pcs=30)
sc.tl.umap(adata)
sc.tl.leiden(adata, resolution=0.5)

print('Preprocessed data shape:', adata.shape)
print('Leiden cluster sizes:')
print(adata.obs['leiden'].value_counts().sort_index())
sc.pl.umap(adata, color='leiden', show=True)
sc.pl.violin(adata, ['n_genes_by_counts', 'total_counts', 'pct_counts_mt'], groupby='leiden', rotation=90)

# ── notebook cell 8 ──
# Step 2: Cell type annotation via canonical markers (full log-norm matrix in .raw)
markers = {
    'T cell':    ['CD3D', 'CD3E', 'CD2'],
    'NK':        ['NKG7', 'GNLY', 'KLRD1', 'KLRC1'],
    'B cell':    ['MS4A1', 'CD79A', 'CD79B'],
    'Myeloid':   ['CD68', 'CD163', 'LYZ', 'FCGR3A', 'ITGAX'],
    'Melanoma':  ['MLANA', 'PMEL', 'MITF', 'TYR', 'S100B'],
    'Fibroblast':['COL1A1', 'COL1A2', 'DCN', 'LUM'],
    'Endothelial':['PECAM1', 'VWF', 'CLDN5'],
}
avail = {ct: [g for g in gs if g in adata.raw.var_names] for ct, gs in markers.items()}
print('Marker genes found per type:', {k: len(v) for k, v in avail.items()})

score_df = pd.DataFrame(index=adata.obs_names)
for ct, gs in avail.items():
    if gs:
        score_df[ct] = np.asarray(adata.raw[:, gs].X.mean(axis=1)).ravel()

# Per-cell assignment by max marker score, then majority vote per Leiden cluster
per_cell = score_df.idxmax(axis=1)
clust_map = per_cell.groupby(adata.obs['leiden']).agg(lambda s: s.value_counts().idxmax())
adata.obs['cell_type'] = adata.obs['leiden'].map(clust_map)

print('\nPer-cluster majority annotation:')
print(pd.crosstab(adata.obs['leiden'], adata.obs['cell_type']))
print('\nCell type composition:')
print(adata.obs['cell_type'].value_counts())

sc.pl.umap(adata, color='cell_type', palette='tab20', show=True)
sc.pl.umap(adata, color=['CD3D', 'CD8A', 'CD68', 'MLANA', 'PMEL', 'COL1A1'],
           use_raw=True, ncols=3, show=True)

# ── notebook cell 11 ──
# Step 3: Score T-cell exhaustion and tumor ligand programs (full log-norm matrix)
exhaustion_genes = ['PDCD1', 'CTLA4', 'HAVCR2', 'LAG3', 'TIGIT']
ligand_genes = ['CD274', 'PDCD1LG2', 'LGALS9', 'CD80', 'CD86']

print('Exhaustion genes present:', [g for g in exhaustion_genes if g in adata.raw.var_names])
print('Ligand genes present:', [g for g in ligand_genes if g in adata.raw.var_names])

sc.tl.score_genes(adata, exhaustion_genes, score_name='exhaustion_score', use_raw=True)
sc.tl.score_genes(adata, ligand_genes, score_name='ligand_score', use_raw=True)

# Mask so UMAP shows each score only in its relevant compartment
adata.obs['exh_T'] = np.where(adata.obs['cell_type'] == 'T cell', adata.obs['exhaustion_score'], np.nan)
adata.obs['lig_tumor'] = np.where(adata.obs['cell_type'] == 'Melanoma', adata.obs['ligand_score'], np.nan)

t_cells = (adata.obs['cell_type'] == 'T cell').values
tumor = (adata.obs['cell_type'] == 'Melanoma').values
print('\nT cells:', int(t_cells.sum()), '| Melanoma cells:', int(tumor.sum()))

exh = adata.obs['exhaustion_score'].values
lig = adata.obs['ligand_score'].values
print('\nExhaustion score (T cells): mean=%.3f median=%.3f' % (exh[t_cells].mean(), np.median(exh[t_cells])))
print('T cells with exhaustion_score > 0 (above background): %.1f%%' % (100*np.mean(exh[t_cells] > 0)))
print('T cells with exhaustion_score > 0.5: %.1f%%' % (100*np.mean(exh[t_cells] > 0.5)))
print('\nLigand score (melanoma): mean=%.3f median=%.3f' % (lig[tumor].mean(), np.median(lig[tumor])))
print('Melanoma cells with ligand_score > 0: %.1f%%' % (100*np.mean(lig[tumor] > 0)))

# Which T-cell clusters are exhausted?
t_stats = adata.obs.loc[t_cells].groupby('leiden')['exhaustion_score'].agg(['mean', 'count'])
print('\nExhaustion score by T-cell Leiden cluster (sorted):')
print(t_stats.sort_values('mean', ascending=False).round(3))

sc.pl.umap(adata, color=['exh_T', 'lig_tumor'], na_color='#eeeeee', cmap='magma', show=True)
sc.pl.umap(adata, color=['PDCD1', 'CD274'], use_raw=True, cmap='magma', ncols=2, show=True)

# ── notebook cell 14 ──
# Step 4: Tumor ligand score vs T-cell-neighbor exhaustion (neighborhood-graph based)
# Locate the neighbor graph robustly (obsp or uns dict or AnnData), else recompute
if 'connectivities' in adata.obsp.keys():
    conn = adata.obsp['connectivities'].tocsr()
elif 'neighbors' in adata.uns:
    nb = adata.uns['neighbors']
    if isinstance(nb, sc.AnnData) and 'connectivities' in nb.obsp.keys():
        conn = nb.obsp['connectivities'].tocsr()
    elif isinstance(nb, dict) and 'connectivities' in nb:
        conn = nb['connectivities'].tocsr()
    else:
        print('uns[neighbors] type:', type(nb), '| keys:', list(nb.obsp.keys()) if isinstance(nb, sc.AnnData) else list(nb.keys()))
        sc.pp.neighbors(adata, n_neighbors=15, n_pcs=30)
        conn = adata.obsp['connectivities'].tocsr()
else:
    print('obsp keys:', list(adata.obsp.keys()))
    sc.pp.neighbors(adata, n_neighbors=15, n_pcs=30)
    conn = adata.obsp['connectivities'].tocsr()

t_idx = np.where(t_cells)[0]
tum_idx = np.where(tumor)[0]

# Connectivity-weighted mean exhaustion of T-cell neighbors per tumor cell
sub = conn[tum_idx][:, t_idx]
rowsum = np.asarray(sub.sum(axis=1)).ravel()
valid = rowsum > 0
nbr_exh = np.full(len(tum_idx), np.nan)
nbr_exh[valid] = np.asarray(sub[valid].dot(exh[t_idx])).ravel() / rowsum[valid]
print('Tumor cells with >=1 T-cell neighbor in graph:', int(valid.sum()), '/', len(tum_idx))

lig_t = lig[tum_idx]
m = valid & ~np.isnan(nbr_exh)

# (i) Spearman: continuous ligand score vs neighbor exhaustion
rho, p = stats.spearmanr(lig_t[m], nbr_exh[m])
print('\n(i) Spearman tumor ligand score vs T-neighbor exhaustion: rho=%.3f, p=%.3e (n=%d)' % (rho, p, int(m.sum())))

# (ii) Mann-Whitney: ligand-positive vs ligand-negative tumor cells
pos = lig_t > 0
if pos.sum() > 10:
    u_stat, p_mw = stats.mannwhitneyu(nbr_exh[pos & m], nbr_exh[(~pos) & m], alternative='two-sided')
    print('(ii) Ligand-positive n=%d (neighbor exh mean=%.3f) vs ligand-negative n=%d (mean=%.3f): U p=%.3e'
          % (int(pos.sum()), nbr_exh[pos & m].mean(), int((~pos).sum()), nbr_exh[(~pos) & m].mean(), p_mw))

# (iii) Sensitivity: raw CD274 log-norm expression instead of composite score
cd274 = np.asarray(adata.raw[:, 'CD274'].X.todense()).ravel()[tum_idx]
rho2, p2 = stats.spearmanr(cd274[m], nbr_exh[m])
print('(iii) Spearman CD274 (raw log-norm) vs T-neighbor exhaustion: rho=%.3f, p=%.3e' % (rho2, p2))

# Per-tumor-cluster stratification (clusters with >=50 tumor cells)
print('\nPer-cluster Spearman (tumor Leiden clusters with n>=50):')
for cl, n in adata.obs.loc[tumor].groupby('leiden').size().items():
    if n < 50: continue
    idx = np.where((tumor) & (adata.obs['leiden'].values == cl))[0]
    local_idx = np.isin(tum_idx, idx)
    mm = local_idx & m
    if mm.sum() < 30: continue
    r, pr = stats.spearmanr(lig_t[mm], nbr_exh[mm])
    print('  cluster %s (n=%d): rho=%.3f, p=%.3e' % (cl, int(mm.sum()), r, pr))

# Figure: scatter with regression line
plt.figure(figsize=(6.5, 5.5))
plt.scatter(lig_t[m], nbr_exh[m], s=8, alpha=0.35, c='#0395D8', edgecolors='none')
slope, intercept = np.polyfit(lig_t[m], nbr_exh[m], 1)
xs = np.linspace(lig_t[m].min(), lig_t[m].max(), 100)
plt.plot(xs, slope*xs + intercept, 'r-', lw=2, label='OLS fit')
plt.xlabel('Tumor immune-checkpoint ligand score (CD274/PDCD1LG2/LGALS9/CD80/CD86)')
plt.ylabel('Mean exhaustion score of T-cell neighbors')
plt.title('Tumor ligand expression vs local T-cell exhaustion\nSpearman rho=%.3f, p=%.2e' % (rho, p))
plt.legend(); plt.tight_layout(); plt.show()

# ── notebook cell 17 ──
# Step 5: PCA-space k-nearest T cells — full-coverage local exhaustion test + figures
from scipy.spatial.distance import cdist

X_pca = adata.obsm['X_pca'][:, :30]
D = cdist(X_pca[tum_idx], X_pca[t_idx])           # (1787 x 3500) euclidean on 30 PCs
k = 10
knn_idx = np.argpartition(D, k, axis=1)[:, :k]    # 10 nearest T cells per tumor cell
knn_exh = exh[t_idx][knn_idx].mean(axis=1)        # local T-cell exhaustion (all 1787 tumor cells)

lig_t = lig[tum_idx]
print('Full-coverage test (n=%d tumor cells, k=%d nearest T cells in PCA space):' % (len(lig_t), k))

# (i) Spearman composite ligand score vs local exhaustion
rho, p = stats.spearmanr(lig_t, knn_exh)
print('  (i)   Ligand score vs local T exhaustion: rho=%.3f, p=%.3e' % (rho, p))

# (ii) Mann-Whitney ligand-positive vs ligand-negative
pos = lig_t > 0
u, p_mw = stats.mannwhitneyu(knn_exh[pos], knn_exh[~pos], alternative='two-sided')
print('  (ii)  Ligand-pos (n=%d, local exh=%.3f) vs ligand-neg (n=%d, local exh=%.3f): p=%.3e'
      % (int(pos.sum()), knn_exh[pos].mean(), int((~pos).sum()), knn_exh[~pos].mean(), p_mw))

# (iii) Sensitivity: raw CD274
cd274 = np.asarray(adata.raw[:, 'CD274'].X.todense()).ravel()[tum_idx]
rho2, p2 = stats.spearmanr(cd274, knn_exh)
print('  (iii) CD274 raw log-norm vs local exhaustion: rho=%.3f, p=%.3e' % (rho2, p2))

# Per-ligand gene correlations
print('\nPer-ligand Spearman vs local T exhaustion:')
lig_expr = {}
for g in ligand_genes:
    e = np.asarray(adata.raw[:, g].X.todense()).ravel()[tum_idx]
    lig_expr[g] = e
    rg, pg = stats.spearmanr(e, knn_exh)
    print('  %-9s rho=%+.3f  p=%.3e' % (g, rg, pg))

# Figure: 1x2 grid — scatter + per-ligand heatmap across exhaustion tertiles
fig, axes = plt.subplots(1, 2, figsize=(13, 5.2))

ax = axes[0]
ax.scatter(lig_t, knn_exh, s=8, alpha=0.35, c='#0395D8', edgecolors='none')
slope, intercept = np.polyfit(lig_t, knn_exh, 1)
xs = np.linspace(lig_t.min(), lig_t.max(), 100)
ax.plot(xs, slope*xs + intercept, 'r-', lw=2)
ax.set_xlabel('Tumor checkpoint-ligand score (CD274/PDCD1LG2/LGALS9/CD80/CD86)')
ax.set_ylabel('Mean exhaustion score of 10 nearest T cells (PCA space)')
ax.set_title('Tumor ligand expression vs local T-cell exhaustion\nSpearman rho=%.3f, p=%.2e (n=%d)' % (rho, p, len(lig_t)))

bins = pd.qcut(knn_exh, 3, labels=['Low', 'Mid', 'High'])
hm = pd.DataFrame(index=ligand_genes, columns=['Low', 'Mid', 'High'], dtype=float)
for g in ligand_genes:
    for b in ['Low', 'Mid', 'High']:
        hm.loc[g, b] = lig_expr[g][bins == b].mean()
sns.heatmap(hm, cmap='magma', annot=True, fmt='.2f', ax=axes[1],
            cbar_kws={'label': 'mean log-norm expr in tumor cells'})
axes[1].set_title('Ligand gene expression by tertile of\nlocal T-cell exhaustion')
plt.tight_layout(); plt.show()
