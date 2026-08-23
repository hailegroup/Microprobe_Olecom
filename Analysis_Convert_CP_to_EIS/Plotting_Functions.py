# -*- coding: utf-8 -*-
"""
Created on Wed Feb 18 11:49:24 2026

@author: luvbl
"""
import numpy as np
import matplotlib.pyplot as plt
from matplotlib_compat import install_pyplot_compat
install_pyplot_compat(plt)
plt.ioff()


def _maybe_show():
    backend = str(plt.get_backend()).lower()
    if "agg" not in backend:
        plt.show()


def _apply_nyquist_equal_scale(ax, x_values, y_values, pad_frac=0.05):
    x = np.asarray(x_values, dtype=float)
    y = np.asarray(y_values, dtype=float)
    finite = np.isfinite(x) & np.isfinite(y)
    if not np.any(finite):
        return

    x = x[finite]
    y = y[finite]
    x_min = float(np.min(x))
    x_max = float(np.max(x))
    y_min = float(np.min(y))
    y_max = float(np.max(y))

    x_center = 0.5 * (x_min + x_max)
    y_center = 0.5 * (y_min + y_max)
    span = max(x_max - x_min, y_max - y_min, 1e-9)
    half = 0.5 * span * (1.0 + float(pad_frac))

    ax.set_xlim(x_center - half, x_center + half)
    ax.set_ylim(y_center - half, y_center + half)
    ax.set_aspect("equal", adjustable="box")


def _safe_legend(*axes, **kwargs):
    """Build legends from real matplotlib handles only.

    Older matplotlib versions on Win7 can misinterpret style strings from
    pyplot state as legend handles when ``plt.legend()`` is used globally.
    """
    handles = []
    labels = []
    for ax in axes:
        if ax is None:
            continue
        ax_handles, ax_labels = ax.get_legend_handles_labels()
        for handle, label in zip(ax_handles, ax_labels):
            if label and not str(label).startswith("_"):
                handles.append(handle)
                labels.append(label)
    if not handles:
        return None
    return axes[0].legend(handles, labels, **kwargs)


def plot_reference_peis(freq, z, title="Reference PEIS"):
    freq = np.asarray(freq, dtype=float)
    z = np.asarray(z, dtype=complex)
    theta = np.angle(z, deg=True)

    fig, axs = plt.subplot_mosaic([['Z', 'abs|Z|'], ['Z', 'theta']], figsize=(10, 5), layout='constrained')
    axs['Z'].plot(z.real, -z.imag, color='tab:blue', linewidth=2)
    axs['Z'].scatter(z.real, -z.imag, c=freq, cmap='plasma', s=14)
    axs['Z'].set_xlabel('ZRe')
    axs['Z'].set_ylabel('-ZIm')
    _apply_nyquist_equal_scale(axs['Z'], z.real, -z.imag)
    axs['Z'].set_title(title)

    axs['abs|Z|'].plot(np.log10(freq), np.log10(np.abs(z)), color='tab:blue', linewidth=2)
    axs['abs|Z|'].scatter(np.log10(freq), np.log10(np.abs(z)), c=freq, cmap='plasma', s=14)
    axs['abs|Z|'].set_ylabel('log(|Z|)\\Ohm')
    axs['abs|Z|'].set_title('Magnitude Z and Theta')

    axs['theta'].plot(np.log10(freq), theta, color='tab:blue', linewidth=2)
    axs['theta'].scatter(np.log10(freq), theta, c=freq, cmap='plasma', s=14)
    axs['theta'].set_ylabel('Theta')
    axs['theta'].set_xlabel('log(f)')

    _maybe_show()
    return fig

def plot_Z(Z, f):
    neg_Z_Im = np.array([])
    theta = np.array([])
    for current_Z in Z:
        neg_Z_Im = np.append(neg_Z_Im, -1*current_Z.imag)
        theta = np.append(theta, np.arctan(current_Z.imag/current_Z.real)*180/np.pi)
    
    fig, axs = plt.subplot_mosaic([['Z', 'abs|Z|'], ['Z', 'theta']], figsize = (10,5), layout='constrained')
    axs['Z'].scatter(Z.real, neg_Z_Im, c=f, cmap = 'plasma', s = 10)
    axs['Z'].set_xlabel('ZRe')
    axs['Z'].set_ylabel('-ZIm')
    _apply_nyquist_equal_scale(axs['Z'], Z.real, neg_Z_Im)
    axs['Z'].set_title('Nyquist')

    axs['abs|Z|'].scatter(np.log10(f), np.log10(np.abs(Z)), c=f, cmap = 'plasma', s = 10)
    axs['abs|Z|'].set_ylabel('log(|Z|)\\Ohm')
    axs['abs|Z|'].set_title('Magnitude Z and Theta')
    axs['theta'].scatter(np.log10(f), theta, c=f, cmap = 'plasma', s = 10)
    axs['theta'].set_ylabel('Theta')
    axs['theta'].set_xlabel('log(f)')

    _maybe_show()
    
    return fig

def plot_Z_comp(ref_Z, ref_f, Z_exp, f_exp):
    ref_Z = np.asarray(ref_Z, dtype=complex).reshape(-1)
    Z_exp = np.asarray(Z_exp, dtype=complex).reshape(-1)
    ref_f = np.asarray(ref_f, dtype=float).reshape(-1)
    f_exp = np.asarray(f_exp, dtype=float).reshape(-1)
    neg_Z_Im = np.array([])
    theta = np.array([])
    for current_Z in ref_Z:
        neg_Z_Im = np.append(neg_Z_Im, -1*current_Z.imag)
        theta = np.append(theta, np.arcsin(current_Z.imag/np.abs(current_Z))*180/np.pi)

    neg_Z2_Im = np.array([])
    theta2 = np.array([])
    for current_Z in Z_exp:
        neg_Z2_Im = np.append(neg_Z2_Im, -1*current_Z.imag)
        theta2 = np.append(theta2, np.arcsin(current_Z.imag/np.abs(current_Z))*180/np.pi)
    
    fig, axs = plt.subplot_mosaic([['Z', 'abs|Z|'], ['Z', 'theta']], figsize = (10,5), layout='constrained')
    axs['Z'].plot(ref_Z.real, neg_Z_Im, label='Simulated EIS')
    axs['Z'].scatter(Z_exp.real, neg_Z2_Im, label='Recovered from CP', c=f_exp, cmap = 'plasma', s = 10)
    axs['Z'].set_xlabel('ZRe')
    axs['Z'].set_ylabel('-ZIm')
    _apply_nyquist_equal_scale(
        axs['Z'],
        np.concatenate([np.asarray(ref_Z.real), np.asarray(Z_exp.real)]),
        np.concatenate([np.asarray(neg_Z_Im), np.asarray(neg_Z2_Im)]),
    )
    axs['Z'].set_title('Nyquist')
    fig.legend(loc='upper left')

    axs['abs|Z|'].plot(np.log10(ref_f), np.log10(np.abs(ref_Z)), label='Simulated EIS')
    axs['abs|Z|'].scatter(np.log10(f_exp), np.log10(np.abs(Z_exp)), label='Recovered from CP', c=f_exp, cmap = 'plasma', s = 10)
    axs['abs|Z|'].set_ylabel('log(|Z|)\\Ohm')
    axs['abs|Z|'].set_title('Magnitude Z and Theta')
    axs['theta'].plot(np.log10(ref_f), theta, label='Simulated EIS')
    axs['theta'].scatter(np.log10(f_exp), theta2, label='Recovered from CP', c=f_exp, cmap = 'plasma', s = 10)
    axs['theta'].set_ylabel('Theta')
    axs['theta'].set_xlabel('log(f)')

    _maybe_show()
    return fig

def plot_DC(time,V,I, status):
    fig, ax1 = plt.subplots(figsize=(10,5))

    ax1.plot(time, V, label = status + "Voltage")
    ax1.set_xlabel("Time (s)")
    ax1.set_ylabel(status+ " Voltage (V)")
    ax1.set_xscale('linear')
    ax1.set_title(status + ": CP or CA data")
    ax1.grid(True)
    
    # Optional: show current profile on second axis
    ax2 = ax1.twinx()
    ax2.plot(time, I, linestyle = "--",color='green', label=status + 'Current Profile')
    ax2.set_ylabel(status + " Current (A)")
    _safe_legend(ax1, ax2)
    _maybe_show()
    
    return fig
    
def plot_DC_comp(exp_t, exp_V, exp_I, sim_t, sim_V, sim_I):
    
    fig = plt.figure(figsize=(8,6))
    ax = plt.gca()
    ax.plot(exp_t, exp_V, 'k.', label='Voltage Data')
    ax.plot(sim_t, sim_V, 'r-', label='Fitted Data')
    _safe_legend(ax)
    ax.set_ylabel('Voltage')
    
    fig2 = plt.figure(figsize=(8,6))
    ax = plt.gca()
    ax.plot(exp_t, exp_I, 'k.', label='Data')
    ax.plot(sim_t, sim_I,
             'r-', linewidth=2, label='Fit')
    
    _safe_legend(ax)
    ax.set_xlabel('Time (s)')
    ax.set_ylabel('Current')
    _maybe_show()
    
    fig2.tight_layout()
    _maybe_show()
    
    return fig    
