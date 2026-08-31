"""Read-only on-robot probe (docs/mode-m1/03 §2 — M1 gate, ~5 min).

Prints the facts that pin downstream decisions. Deliberately sends **no
motion command** (motion tests are manual, wheels-up, with the G-g watchdog
procedure = docs/mode-m1/02 §4):

  1. car_type            -> firmware clamp ceiling (expect M1 = 0x0A -> 0.7
                            m/s, V3.6.5 source) = ADR-0010 §Open 1 pin value
  5. firmware version    -> match against the investigated V3.6.5 source (U-5)
  6. 4x encoder counts   -> 0x0D path liveness for the odom slice
  +  battery voltage     -> sanity / ADR-0005 context

Run on the robot (Rosmaster_Lib present):  m1_probe [--device /dev/myserial]
"""

from __future__ import annotations

import argparse
import time


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default=None, help="serial device (default: library default)")
    parser.add_argument("--samples", type=int, default=3, help="encoder samples (1s apart)")
    args = parser.parse_args()

    from Rosmaster_Lib import Rosmaster  # type: ignore[import-not-found]

    bot = Rosmaster(com=args.device) if args.device else Rosmaster()
    bot.create_receive_threading()
    time.sleep(0.5)  # let the 25Hz auto-report stream arrive

    def read(*method_names: str) -> object:
        # Vendor lib versions differ; a missing/failing getter must not kill
        # the whole read-only probe (each line is an independent fact).
        for name in method_names:
            fn = getattr(bot, name, None)
            if callable(fn):
                try:
                    return fn()
                except Exception as exc:  # noqa: BLE001 - report, keep probing
                    return f"<error: {exc}>"
        return f"<unavailable: {'/'.join(method_names)}>"

    print("== M1 read-only probe (docs/mode-m1/03 §2) ==")
    car_type = read("get_car_type_from_machine", "get_car_type")
    print(
        f"car_type            : {car_type}  "
        "(expect M1 = 0x0A -> clamp 0.7 m/s, V3.6.5 = ADR-0010 Open 1)"
    )
    print(f"firmware version    : {read('get_version')}  (compare vs V3.6.5 = U-5)")
    print(f"battery voltage [V] : {read('get_battery_voltage')}")

    print(f"encoder samples x{args.samples} (int32 M1..M4, 1s apart):")
    prev = None
    for i in range(args.samples):
        counts = read("get_motor_encoder")
        both_ok = isinstance(counts, (tuple, list)) and isinstance(prev, (tuple, list))
        delta = tuple(c - p for c, p in zip(counts, prev, strict=True)) if both_ok else None
        print(f"  [{i}] counts={counts}" + (f" delta={delta}" if delta else ""))
        prev = counts
        if i < args.samples - 1:
            time.sleep(1.0)
    print(
        "NOTE: wheel RPM label / wheel diameter / track+wheelbase are manual"
        " caliper items (docs/mode-m1/03 §2 items 2-3)."
    )


if __name__ == "__main__":
    main()
