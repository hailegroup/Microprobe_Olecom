# -*- coding: utf-8 -*-
"""
Created on Thu Feb 19 15:12:46 2026

@author: luvbl
"""

import numpy as np
from scipy.optimize import curve_fit
import matplotlib.pyplot as plt
import Load_CP_Data as LD
import Plotting_Functions as PF

# ===============================
# 1️⃣ Smoothed Step Function
# ===============================

def step_function(t, V0, dV, t0, width):
    """
    Smoothed step using tanh.
    width controls sharpness of step.
    """
    return V0 + 0.5 * dV * (1 + np.tanh((t - t0)/width))

# ===============================
# 2️⃣ Baseline of response data
# ===============================
def baseline_current(t, m, b):
    """Linear baseline before the voltage step."""
    return m * t + b

# ===============================
# 2️⃣ Robust Current Model
# ===============================

def current_model(t, A1, tau1, A2, tau2, B, C, tc, t0):
    """
    A * exp(-(t-t0)/tau)  -> capacitive decay
    B / sqrt(t-t0)       -> diffusion term
    C                    -> steady offset
    """
    dt = t - t0
    dt = np.clip(dt, 1e-12, None)  # avoid divide-by-zero
    
    return A1 * np.exp(-dt/tau1) + A2 * np.exp(-dt/tau2) + B / np.sqrt(dt+tc) + C

# ===============================
# 2️⃣ Combine Response fittings
# ===============================
def piecewise_current(t, t0, pre_params, post_params):
    """
    Piecewise current model:
    - baseline before t0
    - electrochemical response after t0
    """
    m, b = pre_params
    A1, tau1, A2, tau2, B, C, tc = post_params

    t = np.array(t)

    I = np.zeros_like(t)

    # Pre-pulse region
    mask_pre = t < t0
    I[mask_pre] = baseline_current(t[mask_pre], m, b)

    # Post-pulse region
    mask_post = t >= t0
    I[mask_post] = current_model(t[mask_post], A1, tau1, A2, tau2, B, C, tc, t0)

    return I



def fit(t, V_data, I_data, method, dt):
    if method == "CA":
        applied = V_data
        response = I_data
    elif method == "CP":
        applied = I_data
        response = V_data
    else:
        print("Error, method should be 'CP' or 'CA'")

    # ===============================
    # 3️⃣ Fit Voltage Step
    # ===============================
    
    # Initial guesses
    V0_guess = np.min(applied)
    dV_guess = np.max(applied) - np.min(applied)
    t0_guess = t[np.argmax(np.gradient(applied))]
    width_guess = (t[1] - t[0]) * 5
    
    popt_V, _ = curve_fit(
        step_function,
        t,
        applied,
        p0=[V0_guess, dV_guess, t0_guess, width_guess]
    )
    
    V0_fit, dV_fit, t0_fit, width_fit = popt_V
    
    print("Voltage Fit Parameters:")
    print("V0 =", V0_fit)
    print("dV =", dV_fit)
    print("t0 =", t0_fit)
    print("width =", width_fit)
    
    # Fit baseline BEFORE the voltage step
    mask_pre = t < (t0_fit - 5*(t[1]-t[0]))
    
    t_pre = t[mask_pre]
    I_pre = response[mask_pre]
    
    # Initial guesses
    m_guess = (I_pre[-1] - I_pre[0]) / (t_pre[-1] - t_pre[0])
    b_guess = I_pre[0]
    
    popt_pre, _ = curve_fit(
        baseline_current,
        t_pre,
        I_pre,
        p0=[m_guess, b_guess]
    )
    
    m_fit, b_fit = popt_pre

    print("\nBaseline Fit Parameters:")
    print("m =", m_fit)
    print("b =", b_fit)

    # ===============================
    # 4️⃣ Fit Current Response
    # ===============================
    
    # Only fit data AFTER the step
    mask = t > (t0_fit + 5*(t[1]-t[0]))
    
    t_fit = t[mask]
    I_fit = response[mask]
    
    # ===============================
    # Better initial guesses
    # ===============================
    
    A1_guess = I_fit[0] - I_fit[-1]
    A2_guess = (I_fit[0] - I_fit[-1])*.5
    tau1_guess = (t_fit[-1] - t_fit[0]) / 10
    tau2_guess = (t_fit[-1] - t_fit[0]) / 50
    B_guess = (I_fit[0] - I_fit[-1]) * np.sqrt(t_fit[1] - t0_fit)
    C_guess = I_fit[-1]
    tc_guess = (t[1] - t[0]) * 2
    
    p0 = [A1_guess, tau1_guess, A2_guess, tau2_guess, B_guess, C_guess, tc_guess, t0_fit]
    
    # ===============================
    # Add bounds (VERY IMPORTANT)
    # ===============================
    
    bounds = (
        [-np.inf, 1e-6, -np.inf, 1e-6, -np.inf, -np.inf, 1e-10, t0_fit-1e-9],   # lower
        [ np.inf,  np.inf, np.inf,  np.inf, np.inf,  np.inf, 1, t0_fit+1e-9] # upper
    )
    
    popt_I, _ = curve_fit(
        current_model,
        t_fit,
        I_fit,
        p0=p0,
        bounds=bounds,
        maxfev=10000
    )
    
    A1_fit, tau1_fit, A2_fit, tau2_fit, B_fit, C_fit, tc_fit, t0_used = popt_I

    
    print("\nCurrent Fit Parameters:")
    print("A1 =", A1_fit)
    print("tau1 =", tau1_fit)
    print("A2 =", A2_fit)
    print("tau2 =", tau2_fit)
    print("B =", B_fit)
    print("C =", C_fit)
    
    response = np.array([])
    applied = np.array([])
    time_array = np.arange(np.round(t.min(), 3), np.round(t.max()+0.1, 3), dt)
    for time in time_array:
        response = np.append(response, piecewise_current(
        time,
        t0_fit,
        (m_fit, b_fit),
        (A1_fit, tau1_fit, A2_fit, tau2_fit, B_fit, C_fit, tc_fit)
        ))
        applied = np.append(applied, step_function(time, *popt_V))
    
    if method == "CA":
        V_fit = applied
        I_fit = response
    elif method == "CP":
        V_fit = response
        I_fit = applied
    return time_array, V_fit, I_fit

if __name__ == "__main__":
    load_prompt= "Select your DC text file in the format time, voltage,current. tab delimited."
    CP_data, cp_filepath = LD.load_txt_data(load_prompt)
    t = CP_data[:, 0]
    V_data = CP_data[:, 1]
    I_data = CP_data[:,2]
    fit_t, fit_V, fit_I = fit(t, V_data, I_data, method="CA", dt=0.05)
    PF.plot_DC_comp(t, V_data, I_data, fit_t, fit_V, fit_I)