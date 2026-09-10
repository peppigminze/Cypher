#!/usr/bin/env python3
"""
Send a Wake-on-LAN magic packet to wake the Cypher PC (project spec
section 23).

IMPORTANT - why this is a standalone script and not a Cypher API call:
the Cypher backend and agent run ON the PC you want to wake. If that PC
is asleep/off, the backend is asleep/off too - it cannot send anything.
Waking a PC over the network fundamentally requires the request to come
FROM A DIFFERENT DEVICE on the same local network (a phone via Termux/
Pythonista, a Raspberry Pi, another laptop, your router's own WOL
feature, ...), using a UDP broadcast "magic packet" containing the
target's MAC address.

A browser/PWA cannot do this itself - there is no Web API for sending
raw UDP packets, by design (browser security sandboxing). So this
script is the actual V1 implementation of section 23's "wake_pc": a
real, working tool, just not reachable by tapping a button in the PWA
without a separate always-on relay - see README.md's "Wake-on-LAN"
section for what a secure future gateway/tunnel would need to look
like. No insecure public UDP port-forwarding is set up by anything
here (see project spec section 23: "keine unsichere öffentliche
UDP-Portfreigabe als Standardlösung").

Zero dependency on the rest of the Cypher project on purpose - it must
run from a machine that doesn't have this repo checked out.

Usage:
    python wake_pc.py AA:BB:CC:DD:EE:FF
    python wake_pc.py AA:BB:CC:DD:EE:FF --broadcast 192.168.1.255 --port 9

Find your PC's MAC address on Windows with:
    ipconfig /all
(look for "Physical Address" under your active network adapter)
"""
from __future__ import annotations

import argparse
import socket
import sys


def build_magic_packet(mac_address: str) -> bytes:
    """A WOL magic packet is 6 bytes of 0xFF followed by the target MAC
    address repeated 16 times."""
    mac_clean = mac_address.strip().replace(":", "").replace("-", "")
    if len(mac_clean) != 12:
        raise ValueError(f"'{mac_address}' is not a valid MAC address.")
    try:
        mac_bytes = bytes.fromhex(mac_clean)
    except ValueError as exc:
        raise ValueError(f"'{mac_address}' is not a valid MAC address.") from exc
    return b"\xff" * 6 + mac_bytes * 16


def send_magic_packet(mac_address: str, broadcast_ip: str = "255.255.255.255", port: int = 9) -> None:
    packet = build_magic_packet(mac_address)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, (broadcast_ip, port))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mac_address", help="MAC address of the PC to wake, e.g. AA:BB:CC:DD:EE:FF")
    parser.add_argument(
        "--broadcast",
        default="255.255.255.255",
        help="Broadcast address for your local network (default: 255.255.255.255)",
    )
    parser.add_argument("--port", type=int, default=9, help="UDP port, 7 or 9 are conventional (default: 9)")
    args = parser.parse_args(argv)

    try:
        send_magic_packet(args.mac_address, args.broadcast, args.port)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Could not send packet: {exc}", file=sys.stderr)
        return 1

    print(f"Magic packet sent to {args.mac_address} via {args.broadcast}:{args.port}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
