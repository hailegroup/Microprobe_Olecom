import pyvisa, time

rm = pyvisa.ResourceManager()
inst = rm.open_resource('ASRL5::INSTR')
inst.baud_rate    = 9600
inst.data_bits    = 8
inst.parity       = pyvisa.constants.Parity.none
inst.stop_bits    = pyvisa.constants.StopBits.one
inst.flow_control = pyvisa.constants.ControlFlow.none
inst.write_termination = '\r'
inst.timeout = 500  # 짧게 설정해서 스캔 속도 높임

print("Scanning addresses 0~63 ...")
found = []

for addr in range(64):
    for fmt in [f'@{addr:03d}Q', f'@{addr:d}Q', f'@{addr:02d}Q']:
        try:
            inst.read_termination = '\r\n'
            resp = inst.query(fmt)
            print(f"  *** HIT *** addr={addr} fmt={fmt!r} -> {resp!r}")
            found.append((addr, fmt, resp))
            break
        except pyvisa.errors.VisaIOError:
            pass

if not found:
    print("No response from any address (0~63).")
    print("Trying broadcast / special addresses ...")
    for cmd in ['@000Q', '*Q', '?', '@Q', 'Q']:
        try:
            resp = inst.query(cmd)
            print(f"  *** HIT *** {cmd!r} -> {resp!r}")
        except pyvisa.errors.VisaIOError:
            print(f"  {cmd!r} -> no response")

inst.close()
