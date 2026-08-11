import json, math, sys, shutil
from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Rectangle
from matplotlib.gridspec import GridSpec

PROJECT = Path(r'C:\Users\mmq8658\Desktop\Microprobe\Microprobe Python')
RUN = PROJECT / 'results' / 'continuous_prepost_seeded_reference_20260426_220607'
OUTDIR = Path(r'C:\Users\mmq8658\Desktop\Microprobe\Convert_CP_to_EIS 1\result\260426\parameter_logic')
PPT = Path(r'C:\Users\mmq8658\Desktop\Microprobe\PPT')
OUTDIR.mkdir(parents=True, exist_ok=True)
PPT.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / 'Analysis_Convert_CP_to_EIS'))
from cp_first_policy import recommend_peis_lf_from_cp_txt
from Analysis_Convert_CP_to_EIS import EIS_Fitting as EF
import Load_CP_Data as LD

summary = json.loads((RUN / 'continuous_prepost_seeded_reference_summary.json').read_text())
settings = summary['settings']
seed = summary['seed_recommendation']
analysis = summary['analysis_result']
fit = summary['merged_fit']
cutoff = float(summary['merged_cutoff_hz'])

def load3(name):
    p = RUN / name
    a = np.loadtxt(p, skiprows=1)
    if a.ndim == 1:
        a = a.reshape(1, -1)
    return a

def avg_bin(arr, bin_s=1.0):
    if len(arr) == 0:
        return arr[:, :2]
    t = arr[:,0]
    y = arr[:,2]*1e6
    b = np.floor((t-t[0])/bin_s).astype(int)
    xs=[]; ys=[]
    for bi in np.unique(b):
        m=b==bi
        xs.append(t[m].mean())
        ys.append(y[m].mean())
    return np.column_stack([xs, ys])

pre = load3('pre_ca_continuous.txt')
scout = load3('dv_scout_continuous.txt')
fft_ready = load3('combined_prepost_fft_ready.txt')
peis_raw = np.asarray(LD.load_eis_from_path(str(RUN / 'seeded_peis.txt')), dtype=float)
if peis_raw.ndim == 1:
    peis_raw = peis_raw.reshape(1, -1)
peis_f = peis_raw[:,0]
peis_z = peis_raw[:,1] + 1j*peis_raw[:,2]
# FFT low frequency reconstruction from the same FFT-ready input.
bundle = recommend_peis_lf_from_cp_txt(str(RUN / 'combined_prepost_fft_ready.txt'), current_in_mA=False)
fft_f = np.asarray(bundle['analysis_f'], dtype=float)
fft_z = np.asarray(bundle['analysis_z'], dtype=complex)
keep_fft = np.isfinite(fft_f) & np.isfinite(fft_z.real) & np.isfinite(fft_z.imag) & (fft_f > 0) & (fft_f < cutoff*(1-1e-9))
keep_peis = np.isfinite(peis_f) & (peis_f > 0)
merged_f = np.concatenate([peis_f[keep_peis], fft_f[keep_fft]])
merged_z = np.concatenate([peis_z[keep_peis], fft_z[keep_fft]])
order = np.argsort(merged_f)[::-1]
merged_f = merged_f[order]
merged_z = merged_z[order]
fit_f = np.logspace(np.log10(np.nanmin(merged_f)), np.log10(np.nanmax(merged_f)), 600)
fit_z = EF.Z_from_fit_result(fit, fit_f)

# Style
bg = '#f7f4ed'
ink = '#102027'
muted = '#5a6570'
blue = '#1d4ed8'
orange = '#e85d04'
green = '#2a9d8f'
red = '#d62828'
purple = '#6a4c93'
line = '#d5c7ad'

fig = plt.figure(figsize=(16,9), dpi=220, facecolor=bg)
gs = GridSpec(12, 25, figure=fig, left=0.035, right=0.985, top=0.885, bottom=0.065, wspace=1.05, hspace=1.18)
fig.suptitle('Hybrid-EIS Parameter Optimization Logic | 350C, 0 V example', x=0.035, ha='left', fontsize=20, fontweight='bold', color=ink)
fig.text(0.035, 0.925, 'Live CA decides timing and low-frequency coverage; PEIS owns measured overlap, FFT fills only below PEIS low end.', fontsize=10.5, color=muted, ha='left')

# Left workflow panel
ax_flow = fig.add_subplot(gs[:, :7])
ax_flow.axis('off')
ax_flow.set_xlim(0,1); ax_flow.set_ylim(0,1)
ax_flow.add_patch(FancyBboxPatch((0.00,0.00),1,1, boxstyle='round,pad=0.018,rounding_size=0.02', fc='#fffefd', ec=line, lw=1.2))
ax_flow.text(0.05,0.965,'Optimization loop', fontsize=16, fontweight='bold', color=ink, va='top')

cards = [
    ('1. Pre-CA @ bias', 'Hold 0 V until relative tail slope/noise is stable.\nFor this run: stable at 249 s; stopped at 272 s.', blue),
    ('2. dV scout CA', '+30 mV perturbation; monitor transient decay.\nStop after saturation + 20 s buffer. CP used ~112 s.', orange),
    ('3. FFT LF estimate', 'Use pre-tail + scout tail as FFT-ready CA.\nRaw LF rec = 0.75 Hz; analysis LF = 1.71 Hz.', purple),
    ('4. PEIS bounds', 'Keep overlap by measuring PEIS down to 0.5 Hz.\nPEIS high = 1 MHz, 60 pts, 10 mV.', green),
    ('5. Merge + fit gate', 'PEIS >= 0.5 Hz; FFT only < 0.5 Hz.\\nFit score = 0.058; CP saturated = yes.', red),
]
y = 0.875
for idx,(title,body,col) in enumerate(cards):
    h=0.118
    ax_flow.add_patch(FancyBboxPatch((0.045,y-h),0.91,h, boxstyle='round,pad=0.012,rounding_size=0.018', fc='#ffffff', ec=col, lw=1.4))
    ax_flow.add_patch(Rectangle((0.045,y-h),0.018,h, fc=col, ec='none'))
    ax_flow.text(0.085,y-0.026,title, fontsize=10.4, fontweight='bold', color=ink, va='top')
    ax_flow.text(0.085,y-0.058,body, fontsize=7.45, color=muted, va='top', linespacing=1.22)
    if idx < len(cards)-1:
        ax_flow.add_patch(FancyArrowPatch((0.50,y-h-0.008),(0.50,y-h-0.045), arrowstyle='-|>', mutation_scale=12, lw=1.2, color='#8a817c'))
    y -= 0.155

ax_flow.add_patch(FancyBboxPatch((0.045,0.025),0.91,0.095, boxstyle='round,pad=0.012,rounding_size=0.018', fc='#102027', ec='none'))
ax_flow.text(0.075,0.107,'Decision rule', fontsize=9.5, fontweight='bold', color='white', va='top')
ax_flow.text(0.075,0.078,'Bad guard/fit/overlap -> extend CA, adjust dV/BW, or deep-PEIS check.\\nPEIS-only sufficient -> skip unnecessary hybrid extension.', fontsize=6.7, color='#e7ecef', va='top')

# Top right CA panels
ax_pre = fig.add_subplot(gs[0:4, 8:16])
ax_scout = fig.add_subplot(gs[0:4, 17:25])
for ax, arr, title, col, stable in [
    (ax_pre, pre, 'Pre-CA live stabilization', blue, summary.get('pre',{}).get('stable_detected_hold_s')),
    (ax_scout, scout, 'dV scout live saturation', orange, seed.get('cp_saturation',{}).get('plateau_start_time_s')),
]:
    av = avg_bin(arr, 1.0)
    ax.plot(arr[:,0], arr[:,2]*1e6, color=col, alpha=0.16, lw=0.7)
    ax.plot(av[:,0], av[:,1], color=col, lw=2.2)
    if stable is not None and np.isfinite(stable):
        ax.axvline(float(stable), color='black', lw=1.1, ls='--', alpha=0.55)
        ax.text(float(stable), ax.get_ylim()[1], ' stable marker ', fontsize=7.5, color=ink, ha='left', va='top', rotation=90)
    ax.set_title(title, fontsize=11, fontweight='bold', color=ink)
    ax.set_xlabel('time / s', fontsize=8)
    ax.set_ylabel('current / uA', fontsize=8)
    ax.grid(True, alpha=0.22)
    ax.tick_params(labelsize=8)

# FFT input panel
ax_fft = fig.add_subplot(gs[4:8, 8:16])
raw = load3('combined_prepost_raw.txt')
trim = fft_ready
ax_fft.plot(raw[:,0], raw[:,2]*1e6, color='#9aa0a6', alpha=0.35, lw=0.6, label='raw concat')
ax_fft.plot(trim[:,0], trim[:,2]*1e6, color=purple, alpha=0.95, lw=1.15, label='FFT-ready trace')
ax_fft.set_title('FFT input after trimming/segmentation', fontsize=11, fontweight='bold', color=ink)
ax_fft.set_xlabel('combined time / s', fontsize=8)
ax_fft.set_ylabel('current / uA', fontsize=8)
ax_fft.grid(True, alpha=0.22)
ax_fft.legend(frameon=False, fontsize=8, loc='best')
ax_fft.tick_params(labelsize=8)

# Nyquist panel
ax_n = fig.add_subplot(gs[4:10, 17:25])
peis_mask = np.isfinite(peis_z.real) & np.isfinite(peis_z.imag)
ax_n.scatter(peis_z[peis_mask].real, -peis_z[peis_mask].imag, s=15, color=green, alpha=0.72, label='measured PEIS')
ax_n.scatter(fft_z[keep_fft].real, -fft_z[keep_fft].imag, s=20, color=purple, marker='^', alpha=0.75, label='FFT LF only')
ax_n.plot(fit_z.real, -fit_z.imag, color=red, lw=2.1, label='RQRQ fit')
ax_n.axhline(0, color='black', lw=0.5, alpha=0.4)
ax_n.set_title('Full arc assembly and final fit', fontsize=11, fontweight='bold', color=ink)
ax_n.set_xlabel('Zre / Ohm', fontsize=8)
ax_n.set_ylabel('-Zim / Ohm', fontsize=8)
ax_n.grid(True, alpha=0.22)
ax_n.legend(frameon=False, fontsize=8, loc='best')
ax_n.tick_params(labelsize=8)
x = np.concatenate([peis_z.real[peis_mask], fft_z[keep_fft].real, fit_z.real])
yy = np.concatenate([-peis_z.imag[peis_mask], -fft_z[keep_fft].imag, -fit_z.imag])
if len(x):
    lo,hi=np.nanpercentile(x,[1,99]); pad=(hi-lo)*0.08 if hi>lo else 1
    ax_n.set_xlim(lo-pad, hi+pad)
if len(yy):
    lo,hi=np.nanpercentile(yy,[1,99]); pad=(hi-lo)*0.1 if hi>lo else 1
    ax_n.set_ylim(lo-pad, hi+pad)

# Bottom metrics strip
ax_m = fig.add_subplot(gs[8:12, 8:16])
ax_m.axis('off')
ax_m.set_xlim(0,1); ax_m.set_ylim(0,1)
metrics = [
    ('Protocol', f"dV={settings['dv_v']*1000:.0f} mV | BW4 | dt={settings['ca_dt_s']:.1f}s | Auto range"),
    ('PEIS', f"{settings['peis_high_hz']/1e6:.0f} MHz to {settings['peis_minimum_depth_hz']:.1f} Hz | {settings['peis_npts']} pts | {settings['peis_amplitude_mv']:.0f} mV"),
    ('LF policy', f"raw {seed['raw_recommended_lf_hz']:.2f} Hz -> actual PEIS {seed['applied_peis_lf_hz']:.1f} Hz; cutoff {cutoff:.3f} Hz"),
    ('QC result', f"fit score {fit['Fit quality score']:.3f}; R0 {fit['R0 (Ohm)']/1000:.2f} kOhm; guard: {analysis['postcheck_reason'] or 'clear'}"),
]
for i,(k,v) in enumerate(metrics):
    y0=0.88-i*0.225
    ax_m.add_patch(FancyBboxPatch((0.00,y0-0.15),1.0,0.17, boxstyle='round,pad=0.008,rounding_size=0.015', fc='#ffffff', ec=line, lw=1.0))
    ax_m.text(0.03,y0,k, fontsize=9.5, fontweight='bold', color=ink, va='top')
    ax_m.text(0.22,y0,v, fontsize=8.7, color=muted, va='top')

fig.text(0.035,0.018, f"Source run: {RUN.name} | started {summary['started_at']} | saved by Codex", fontsize=7.5, color='#777')

out = OUTDIR / 'parameter_optimization_logic_350C_0V_onepage.png'
fig.savefig(out, facecolor=bg)
plt.close(fig)
shutil.copy2(out, PPT / out.name)
print(out)
print(PPT / out.name)

