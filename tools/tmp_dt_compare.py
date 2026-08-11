import json
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

import sys

ROOT = Path(r"C:\Users\mmq8658\Desktop\Microprobe\Microprobe Python")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from matplotlib_compat import install_pyplot_compat
install_pyplot_compat(plt)

from driver_biologic import BioLogicController
from cp_first_policy import build_fft_ready_ca_trace

OUT = ROOT / "results" / "dt001_vs_dt0001_test_20260424_160000"
OUT.mkdir(parents=True, exist_ok=True)

runs = [("dt0p01", 0.01), ("dt0p001", 0.001)]
results = []
bl = BioLogicController()
bl.connect()
try:
    for name, dt in runs:
        outdir = OUT / name
        outdir.mkdir(exist_ok=True)
        path = outdir / f"{name}_ca.txt"
        arr = bl.run_ca_sequence(
            voltage_steps=[0.0, 0.1],
            duration_steps=[5.0, 10.0],
            dt_record=dt,
        )
        arr = np.asarray(arr, dtype=float)
        pd.DataFrame({"time_s": arr[:,0], "voltage_v": arr[:,1], "current_a": arr[:,2]}).to_csv(path, sep='\t', index=False)
        fft_arr = build_fft_ready_ca_trace(
            arr,
            average_bin_s=0.1,
            segmented_smoothing_window_points=5,
        )
        fft_path = outdir / f"{name}_fft_ready.txt"
        pd.DataFrame({"time_s": fft_arr[:,0], "voltage_v": fft_arr[:,1], "current_a": fft_arr[:,2]}).to_csv(fft_path, sep='\t', index=False)
        t = arr[:,0]
        i = arr[:,2]
        post_mask = t >= 5.0
        tail_mask = t >= 12.0
        fft_t = fft_arr[:,0]
        fft_i = fft_arr[:,2]
        fft_tail_mask = fft_t >= 12.0
        results.append({
            "name": name,
            "dt_s": dt,
            "raw_rows": int(len(t)),
            "fft_ready_rows": int(len(fft_t)),
            "raw_post_std_a": float(np.std(i[post_mask])) if np.any(post_mask) else None,
            "raw_tail_std_a": float(np.std(i[tail_mask])) if np.any(tail_mask) else None,
            "fft_ready_tail_std_a": float(np.std(fft_i[fft_tail_mask])) if np.any(fft_tail_mask) else None,
        })
finally:
    bl.disconnect()

fig, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
for idx, (name, dt) in enumerate(runs):
    outdir = OUT / name
    raw = pd.read_csv(outdir / f"{name}_ca.txt", sep='\t')
    fft = pd.read_csv(outdir / f"{name}_fft_ready.txt", sep='\t')
    ax = axes[0, idx]
    ax.plot(raw["time_s"], raw["current_a"], lw=1)
    ax.set_title(f"{name} raw CA")
    ax.set_xlabel("time / s")
    ax.set_ylabel("current / A")
    ax.axvline(5.0, color='r', ls='--', lw=1)
    ax2 = axes[1, idx]
    ax2.plot(raw["time_s"], raw["current_a"], alpha=0.35, lw=0.8, label="raw")
    ax2.plot(fft["time_s"], fft["current_a"], lw=1.5, label="fft-ready")
    ax2.set_xlim(4.5, 15.0)
    ax2.set_title(f"{name} near step")
    ax2.set_xlabel("time / s")
    ax2.set_ylabel("current / A")
    ax2.axvline(5.0, color='r', ls='--', lw=1)
    ax2.legend()
fig.savefig(OUT / "dt001_vs_dt0001_compare.png", dpi=180)
plt.close(fig)

with open(OUT / "summary.json", "w", encoding="utf-8") as f:
    json.dump({"results": results}, f, indent=2)

print(json.dumps({"outdir": str(OUT), "results": results}, indent=2))
