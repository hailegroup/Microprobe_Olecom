Motor cable custom driver package for this Windows 11 PC only.

Contents
- slabvcp_806F_custom.inf
- silabser.sys
- WdfCoInstaller01009.dll

Why this exists
- The Microprobe motor cable appears as USB\VID_10C4&PID_806F on this PC.
- The stock Silicon Labs CP210x package supports modern default PIDs (EA60, etc.)
  but not this legacy IMS custom PID.
- The old IMS package in MD-CC40x-000_DRIVERS is x86-only for the critical bus/serial
  driver files, so it will not load on Windows 11 x64.
- This local INF is a best-effort test package that tries the modern x64 CP210x
  serial driver directly against PID 806F.

Important limits
- This is not an officially signed driver package.
- Installing it requires temporary driver-signature enforcement disable or test mode.
- Success is not guaranteed; the cable firmware may still require the official ID-reset path.

Suggested install flow
1. Reboot Windows with driver signature enforcement temporarily disabled.
2. In Device Manager, right-click MD-CC4xx -> Update driver.
3. Browse my computer for drivers -> Let me pick -> Have Disk.
4. Point to slabvcp_806F_custom.inf in this folder.
5. If Windows accepts it, unplug/replug the cable and look for a new COM port.
