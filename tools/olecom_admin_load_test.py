from __future__ import annotations

import argparse
import datetime as _dt
import traceback
from pathlib import Path

import comtypes.client


def main() -> int:
    parser = argparse.ArgumentParser(description="Admin-context OLE-COM LoadSettings diagnostic.")
    parser.add_argument("--ip", default="192.109.209.128")
    parser.add_argument("--mps", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--channel", type=int, default=0)
    parser.add_argument("--disconnect-on-exit", action="store_true")
    args = parser.parse_args()

    log = Path(args.log)
    log.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []

    def write(line: str) -> None:
        print(line)
        lines.append(line)
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    write(f"started={_dt.datetime.now().isoformat(timespec='seconds')}")
    write(f"mps={args.mps}")
    write(f"mps_exists={Path(args.mps).exists()}")
    try:
        obj = comtypes.client.CreateObject("EClabCOM.EClabExe")
        for label, fn in [
            ("ConnectDeviceByIP", lambda: obj.ConnectDeviceByIP(args.ip)),
            ("ConnectDevice", lambda: obj.ConnectDevice(args.device)),
            ("TestConnection", lambda: obj.TestConnection(args.device)),
            ("SelectDevice", lambda: obj.SelectDevice(args.device)),
            ("SelectChannel", lambda: obj.SelectChannel(args.device, args.channel)),
            ("GetDeviceSN", lambda: obj.GetDeviceSN(args.device)),
            ("GetChannelInfos", lambda: obj.GetChannelInfos(args.device, args.channel)),
            ("LoadSettings", lambda: obj.LoadSettings(args.device, args.channel, str(Path(args.mps)))),
            ("MeasureStatus", lambda: obj.MeasureStatus(args.device, args.channel)[:8]),
        ]:
            try:
                write(f"{label}={fn()!r}")
            except Exception as exc:
                write(f"{label}_ERROR={type(exc).__name__}: {exc}")
        if args.disconnect_on_exit:
            try:
                write(f"DisconnectDevice={obj.DisconnectDevice(args.device)!r}")
            except Exception as exc:
                write(f"DisconnectDevice_ERROR={type(exc).__name__}: {exc}")
        else:
            write("DisconnectDevice=skipped (keeping EC-Lab session/device connected)")
        write(f"finished={_dt.datetime.now().isoformat(timespec='seconds')}")
        return 0
    except Exception:
        write(traceback.format_exc())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
