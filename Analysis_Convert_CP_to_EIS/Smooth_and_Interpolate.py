# -*- coding: utf-8 -*-
"""
Created on Wed Feb 18 12:15:24 2026

@author: luvbl
"""

import numpy as np
from scipy.signal import savgol_filter
from scipy.interpolate import interp1d

def smooth_and_interpolate(time, V_data, I_data, dt,
                           window_length,
                           polyorder=1,
                           kind='linear'):
    """
    Smooth experimental data and interpolate onto a uniform grid.

    Parameters
    ----------
    time : array-like
        1D array of time values (seconds)
    data : array-like
        1D array of measured data values
    dt : float
        Desired interpolation spacing (seconds)
    window_length : int
        Savitzky-Golay filter window length (must be odd)
    polyorder : int
        Polynomial order for Savitzky-Golay filter
    kind : str
        Interpolation type ('linear', 'cubic', etc.)

    Returns
    -------
    new_time : ndarray
        Uniformly spaced time array
    new_data : ndarray
        Smoothed and interpolated data
    """

    time = np.asarray(time)
    data = np.asarray(V_data)
    current = np.asarray(I_data)

    # Ensure window_length is valid
    if window_length >= len(data):
        window_length = len(data) - 1
    if window_length % 2 == 0:
        window_length += 1

    # Step 1: Smooth
    smooth_data = savgol_filter(data, window_length, polyorder)
    smooth_current = savgol_filter(current, window_length, polyorder)
    # Step 2: Interpolate Voltage
    interp_V_func = interp1d(time, smooth_data, kind=kind)
    new_time = np.arange(time.min(), time.max(), dt)
    new_V_data = interp_V_func(new_time)
    
    interp_I_func = interp1d(time, smooth_current, kind=kind)
    new_I_data= interp_I_func(new_time)
    

    return new_time, new_V_data, new_I_data

