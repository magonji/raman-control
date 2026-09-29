"""Finds the serial settings the Laser Quantum controller answers to.

Tries the usual baud rates and line endings, sending only read-only queries
(POWER?, STATUS?, VERSION?). It never switches emission on or changes the power.

Run it once with the laser off and once with it emitting (switched on from the
controller), and compare the STATUS? replies: according to the manual, STATUS?
reports the interlock, and we need to know whether it also reflects emission.

Usage (close the Laser Quantum software and the panel first):
    .venv\\Scripts\\python.exe tools\\probe_laser.py COM4
"""
from __future__ import annotations

import sys
import time

import serial

BAUD_RATES = (9600, 19200, 57600, 115200, 38400)  # the smd12 manual says 9600
LINE_ENDINGS = {"CR": "\r", "CR+LF": "\r\n", "LF": "\n"}  # the manual says CR
QUERIES = ("POWER?", "STATUS?", "VERSION?")


def query(ser: serial.Serial, command: str, eol: str) -> str:
    ser.reset_input_buffer()
    ser.write((command + eol).encode("ascii"))
    ser.flush()
    time.sleep(0.15)
    reply = ser.readline() + ser.read(ser.in_waiting or 0)
    return reply.decode("ascii", errors="replace").strip()


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "COM4"
    print(f"Probing {port} (read-only queries only)\n")
    found = []
    for baud in BAUD_RATES:
        for name, eol in LINE_ENDINGS.items():
            try:
                with serial.Serial(port, baud, bytesize=8, parity="N", stopbits=1,
                                   timeout=0.6, write_timeout=1.0) as ser:
                    time.sleep(0.2)
                    replies = {q: query(ser, q, eol) for q in QUERIES}
            except serial.SerialException as exc:
                print(f"Cannot open {port}: {exc}")
                print("Is the Laser Quantum software (or the panel) still open? Is the port right?")
                return 1
            answered = any(replies.values())
            print(f"{baud:>6} baud, {name:<5}: " +
                  (" | ".join(f"{q} -> {r!r}" for q, r in replies.items()) if answered else "no reply"))
            if answered:
                found.append((baud, name))
    print()
    if found:
        print("The controller answered with: " + ", ".join(f"{b} baud / {n}" for b, n in found))
        print("Copy the baud rate into [laser] baudrate and the line ending into [laser] eol "
              '("\\r\\n" for CR+LF, "\\r" for CR, "\\n" for LF) in config.toml.')
    else:
        print("No combination answered. Check the cable, that the controller is switched on "
              "and, in the manual, the serial settings and command set.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
