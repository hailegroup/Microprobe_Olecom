from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib_compat import install_pyplot_compat
install_pyplot_compat(plt)
import numpy as np
import pandas as pd

import Convert_CP_to_EIS as DC_EIS
import EIS_Fitting as EISFIT
import Load_CP_Data as LD
import Plotting_Functions as PF
import Smooth_and_Interpolate as SI
import export_dict_to_excel as Export
import export_origin_friendly as OriginExport


BASE_DIR = Path(__file__).resolve().parent
INPUT_DIR = BASE_DIR / "Input data"
RESULT_DIR = BASE_DIR / "result"


SETTINGS = {
    "f_max": None,
    "zero_pad_factor": 50,
    "n_log_points": 100,
    "window": 51,
    "data_treatment": "Smooth",
    "dt": 0.01,
    "current_in_mA": True,
    "current_scale_factor": 1.0,
    "ca_step_only": False,
    "trim_start_time": None,
    "auto_trim": False,
    "auto_trim_kwargs": {
        "voltage_step_sigma": 8.0,
        "smooth_window_points": 11,
        "stability_window_s": 2.0,
        "sustain_window_s": 3.0,
        "std_factor": 2.5,
    },
    "recommendation_extension_factor": 8.0,
    "cp_periods_required": 1.0,
    "cp_conservative_factor": 3.0,
    "fit_freq_range": None,
}


def recover_and_export(sample_name, dc_path, eis_path, output_root, auto_trim=False):
    out_dir = output_root / sample_name
    out_dir.mkdir(parents=True, exist_ok=True)

    dc_data = LD.load_dc_from_path(
        dc_path,
        current_in_mA=SETTINGS["current_in_mA"],
        CA_step_only=SETTINGS["ca_step_only"],
        trim_start_time=SETTINGS["trim_start_time"],
        current_scale_factor=SETTINGS["current_scale_factor"],
        auto_trim=auto_trim,
        auto_trim_kwargs=SETTINGS["auto_trim_kwargs"],
    )
    eis_data = LD.load_eis_from_path(eis_path)

    ref_sort = np.argsort(eis_data[:, 0])[::-1]
    ref_fig = PF.plot_reference_peis(
        eis_data[ref_sort, 0],
        eis_data[ref_sort, 1] + 1j * eis_data[ref_sort, 2],
        title=f"Reference PEIS - {sample_name}",
    )

    figures = [ref_fig] + [0] * 6
    figures[3] = PF.plot_DC(dc_data[:, 0], dc_data[:, 1], dc_data[:, 2], "Raw")

    time, volt, current = SI.smooth_and_interpolate(
        dc_data[:, 0],
        dc_data[:, 1],
        dc_data[:, 2],
        SETTINGS["dt"],
        SETTINGS["window"],
    )

    figures[4] = PF.plot_DC_comp(
        dc_data[:, 0], dc_data[:, 1], dc_data[:, 2], time, volt, current
    )

    (
        dif_time,
        dif_v,
        dif_i,
        ft_f,
        ft_v,
        ft_i,
        filtered_f,
        recovered_z,
        z_full,
        figures[5],
        figures[6],
        figures[1],
        figures[2],
    ) = DC_EIS.FFT_EIS(
        time,
        volt,
        current,
        SETTINGS["data_treatment"],
        SETTINGS["dt"],
        SETTINGS["window"],
        SETTINGS["f_max"],
        SETTINGS["zero_pad_factor"],
        SETTINGS["n_log_points"],
        eis_data=eis_data,
    )

    target_f_max = DC_EIS.resolve_fft_max_f(SETTINGS["f_max"], eis_data=eis_data, ft_freq=ft_f)
    ft_mask = (ft_f > 0) & (ft_f <= target_f_max)
    ft_f_e = ft_f[ft_mask]
    ft_v_e = ft_v[ft_mask]
    ft_i_e = ft_i[ft_mask]
    ft_z_e = ft_v_e / ft_i_e

    analysis_max_f = min(
        float(np.max(ft_f[ft_f > 0])),
        float(target_f_max) * float(SETTINGS["recommendation_extension_factor"]),
    )
    analysis_f, analysis_z = DC_EIS.extract_log_spaced_impedance(
        ft_f,
        ft_v,
        ft_i,
        max_f=analysis_max_f,
        n_log_points=max(SETTINGS["n_log_points"] * 2, 160),
    )
    recommendation = DC_EIS.recommend_peis_lowest_frequency(analysis_f, analysis_z)
    cp_duration_info = DC_EIS.summarize_cp_duration_requirements(
        time,
        target_f_max,
        recommended_peis_lowest_freq_hz=recommendation["recommended_peis_lowest_freq_hz"],
        periods_required=SETTINGS["cp_periods_required"],
        conservative_factor=SETTINGS["cp_conservative_factor"],
    )
    recommendation_fig = DC_EIS.plot_peis_cutoff_recommendation(
        analysis_f,
        analysis_z,
        recommendation,
        title=sample_name + (" (auto-trim)" if auto_trim else " (no auto-trim)"),
    )
    figures.append(recommendation_fig)

    summary_dict = {
        "Selected freq range": filtered_f,
        "Recovered ZRe": recovered_z.real,
        "Recovered ZIm": recovered_z.imag,
        "Recovered Zabs": np.abs(recovered_z),
        "Recovered Phase": np.angle(recovered_z, deg=True),
        "Data Treatment": SETTINGS["data_treatment"],
        "Max freq": target_f_max,
        "FFT analysis max freq": analysis_max_f,
        "Recommended PEIS lowest freq": recommendation["recommended_peis_lowest_freq_hz"],
        "Actual CP duration (s)": cp_duration_info["actual_cp_duration_s"],
        "Duration-limited lowest freq (Hz)": cp_duration_info["duration_limited_lowest_freq_hz"],
        "Target min CP time (s)": cp_duration_info["target_min_cp_time_s"],
        "Target conservative CP time (s)": cp_duration_info["target_conservative_cp_time_s"],
        "Recommended PEIS min CP time (s)": cp_duration_info["recommended_peis_min_cp_time_s"],
        "Recommended PEIS conservative CP time (s)": cp_duration_info["recommended_peis_conservative_cp_time_s"],
        "Target duration margin": cp_duration_info["target_duration_margin"],
        "Interpolation time step (s)": SETTINGS["dt"],
        "SavGol window length": SETTINGS["window"],
        "Current scale factor": SETTINGS["current_scale_factor"],
        "CA step only": SETTINGS["ca_step_only"],
        "Auto trim enabled": auto_trim,
        "DC data filepath": str(dc_path),
        "EIS data filepath": str(eis_path),
    }
    raw_dict = {
        "Imported time(s), V(V), I(A)": dc_data,
        "Treated Data time": time,
        "Treated Data Voltage": volt,
        "Treated Data Current": current,
        "Differentiated time": dif_time,
        "dV/dt": dif_v,
        "dI/dt": dif_i,
        "FFT frequencies": ft_f_e,
        "FFT Voltage real": ft_v_e.real,
        "FFT Voltage imag": ft_v_e.imag,
        "FFT Current real": ft_i_e.real,
        "FFT Current imag": ft_i_e.imag,
        "All recovered ZRe": ft_z_e.real,
        "All recovered ZImag": ft_z_e.imag,
        "FFT analysis freq": analysis_f,
        "FFT analysis ZRe": analysis_z.real,
        "FFT analysis ZImag": analysis_z.imag,
        "FFT analysis stable mask": recommendation["stable_mask"].astype(int),
        "FFT analysis mag residual": recommendation["mag_rel_residual"],
        "FFT analysis phase residual deg": recommendation["phase_residual_deg"],
    }

    rec_sort = np.argsort(filtered_f)[::-1]
    total_arc_data = {
        "ref": {
            "freq": eis_data[ref_sort, 0],
            "ReZ": eis_data[ref_sort, 1],
            "ImZ": eis_data[ref_sort, 2],
        },
        "recovered": {
            "freq": filtered_f[rec_sort],
            "ReZ": recovered_z[rec_sort].real,
            "ImZ": recovered_z[rec_sort].imag,
        },
    }

    freq_all = np.concatenate([eis_data[:, 0], filtered_f])
    rez_all = np.concatenate([eis_data[:, 1], recovered_z.real])
    imz_all = np.concatenate([eis_data[:, 2], recovered_z.imag])
    sort_idx = np.argsort(freq_all)[::-1]
    fit_result = EISFIT.fit_RQRQRQ(
        freq_all[sort_idx],
        rez_all[sort_idx],
        imz_all[sort_idx],
        p0=None,
        freq_range=SETTINGS["fit_freq_range"],
    )
    fit_fig = EISFIT.plot_fit(
        freq_all[sort_idx],
        rez_all[sort_idx],
        imz_all[sort_idx],
        fit_result,
        title=sample_name + (" (auto-trim)" if auto_trim else " (no auto-trim)"),
    )
    figures.append(fit_fig)

    summary_dict.update({k: str(v) for k, v in fit_result.items()})

    excel_path = out_dir / f"{sample_name}.xlsx"
    Export.export_to_excel(
        summary_dict,
        raw_dict,
        figures,
        save_path=str(excel_path),
        img_dir=str(out_dir),
        total_arc_data=total_arc_data,
    )
    OriginExport.export_origin_bundle(
        out_dir,
        sample_name,
        dc_data=dc_data,
        treated_time=time,
        treated_voltage=volt,
        treated_current=current,
        diff_time=dif_time,
        diff_voltage=dif_v,
        diff_current=dif_i,
        reference_freq=eis_data[ref_sort, 0],
        reference_re=eis_data[ref_sort, 1],
        reference_im=eis_data[ref_sort, 2],
        recovered_freq=filtered_f[rec_sort],
        recovered_re=recovered_z[rec_sort].real,
        recovered_im=recovered_z[rec_sort].imag,
        fit_freq=freq_all[sort_idx],
        fit_re=rez_all[sort_idx],
        fit_im=imz_all[sort_idx],
        fft_analysis_freq=analysis_f,
        fft_analysis_re=analysis_z.real,
        fft_analysis_im=analysis_z.imag,
        stable_mask=recommendation["stable_mask"],
    )

    plt.close("all")
    return {
        "dc_data": dc_data,
        "eis_data": eis_data,
        "filtered_f": filtered_f,
        "recovered_z": recovered_z,
        "fit_result": fit_result,
        "excel_path": excel_path,
    }


def make_auto_trim_comparison_plot(no_trim_result, auto_trim_result, output_path):
    ref = no_trim_result["eis_data"]
    f0 = no_trim_result["filtered_f"]
    z0 = no_trim_result["recovered_z"]
    f1 = auto_trim_result["filtered_f"]
    z1 = auto_trim_result["recovered_z"]

    fig, axs = plt.subplot_mosaic(
        [["nyquist", "mag"], ["nyquist", "phase"]],
        figsize=(12, 7),
        layout="constrained",
    )

    ref_z = ref[:, 1] + 1j * ref[:, 2]

    axs["nyquist"].plot(ref[:, 1], -ref[:, 2], color="0.82", linewidth=2.5, label="Reference PEIS")
    axs["nyquist"].scatter(z0.real, -z0.imag, s=28, color="#1b9e77", label="No auto-trim")
    axs["nyquist"].scatter(z1.real, -z1.imag, s=22, marker="x", color="#d95f02", label="Auto-trim")
    axs["nyquist"].set_xlabel("Re(Z) / Ohm")
    axs["nyquist"].set_ylabel("-Im(Z) / Ohm")
    axs["nyquist"].set_title("Nyquist")
    axs["nyquist"].grid(True, alpha=0.25)
    axs["nyquist"].legend(loc="best", fontsize=9)

    axs["mag"].plot(np.log10(ref[:, 0]), np.log10(np.abs(ref_z)), color="0.82", linewidth=2.5)
    axs["mag"].scatter(np.log10(f0), np.log10(np.abs(z0)), s=28, color="#1b9e77")
    axs["mag"].scatter(np.log10(f1), np.log10(np.abs(z1)), s=22, marker="x", color="#d95f02")
    axs["mag"].set_ylabel("log10(|Z| / Ohm)")
    axs["mag"].set_title("Magnitude")
    axs["mag"].grid(True, alpha=0.25)

    axs["phase"].plot(np.log10(ref[:, 0]), np.angle(ref_z, deg=True), color="0.82", linewidth=2.5)
    axs["phase"].scatter(np.log10(f0), np.angle(z0, deg=True), s=28, color="#1b9e77")
    axs["phase"].scatter(np.log10(f1), np.angle(z1, deg=True), s=22, marker="x", color="#d95f02")
    axs["phase"].set_xlabel("log10(f / Hz)")
    axs["phase"].set_ylabel("Phase / deg")
    axs["phase"].set_title("Phase")
    axs["phase"].grid(True, alpha=0.25)

    fig.suptitle("Trusted-default workflow: auto-trim OFF vs ON", fontsize=14)
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def main():
    sample_name = "300 rapid measurement"
    output_root = RESULT_DIR / "260417-8-trusted-defaults"

    dc_path = INPUT_DIR / "260417-8" / "300 rapid measurement_02_CA_C02.mpr"
    eis_path = INPUT_DIR / "260417-8" / "300 rapid measurement_01_PEIS_C02.mpr"

    no_trim_result = recover_and_export(
        sample_name + "_no_auto_trim",
        dc_path,
        eis_path,
        output_root,
        auto_trim=False,
    )
    auto_trim_result = recover_and_export(
        sample_name + "_auto_trim",
        dc_path,
        eis_path,
        output_root,
        auto_trim=True,
    )

    comparison_plot = output_root / "auto_trim_vs_no_trim_comparison.png"
    make_auto_trim_comparison_plot(no_trim_result, auto_trim_result, comparison_plot)

    summary = {
        "sample": sample_name,
        "settings": SETTINGS,
        "no_auto_trim_excel": str(no_trim_result["excel_path"]),
        "auto_trim_excel": str(auto_trim_result["excel_path"]),
        "comparison_plot": str(comparison_plot),
        "fit_no_auto_trim": no_trim_result["fit_result"],
        "fit_auto_trim": auto_trim_result["fit_result"],
    }
    summary_path = output_root / "run_summary.json"
    summary_path.write_text(json_dumps(summary), encoding="utf-8")

    print("Saved:")
    print(no_trim_result["excel_path"])
    print(auto_trim_result["excel_path"])
    print(comparison_plot)
    print(summary_path)


def json_dumps(data):
    import json

    def default(obj):
        if isinstance(obj, (np.floating, np.integer)):
            return obj.item()
        if isinstance(obj, Path):
            return str(obj)
        return str(obj)

    return json.dumps(data, indent=2, ensure_ascii=False, default=default)


if __name__ == "__main__":
    main()
