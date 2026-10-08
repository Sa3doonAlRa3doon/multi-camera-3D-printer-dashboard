"""Port validation and deterministic selection helpers."""

from __future__ import annotations

import socket
from collections.abc import Callable

DEFAULT_PORT = 8080
MIN_PORT = 1000
MAX_PORT = 9999
AUTO_PORTS = [
    1010, 2020, 3030, 4040, 5050, 6060, 7070, 8080, 9090,
    1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 9000,
]
INVALID_PORT_MESSAGE = (
    "Please enter a 4-digit port number between 1000 and 9999, "
    "for example 8080, 9090, or 9000."
)


def is_valid_custom_port(value: str) -> bool:
    return len(value) == 4 and value.isdigit() and MIN_PORT <= int(value) <= MAX_PORT


def is_port_available(bind_host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in bind_host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        sock.bind((bind_host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def automatic_port(
    bind_host: str,
    checker: Callable[[str, int], bool] = is_port_available,
) -> int:
    checked: set[int] = set()
    for port in AUTO_PORTS:
        if port not in checked:
            checked.add(port)
            if checker(bind_host, port):
                return port
    for port in range(MIN_PORT, MAX_PORT + 1):
        if port not in checked and checker(bind_host, port):
            return port
    raise RuntimeError("No available port was found between 1000 and 9999.")


def select_port_interactive(
    bind_host: str,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
    checker: Callable[[str, int], bool] = is_port_available,
) -> int:
    while True:
        print_fn("Use port 8080?\n[Y] Yes\n[N] Enter a custom port\n[A] Automatically select an available port\n[D] Use the default port")
        choice = input_fn("> ").strip().lower()
        if choice in {"y", "d"}:
            if checker(bind_host, DEFAULT_PORT):
                return DEFAULT_PORT
            print_fn("Port 8080 is occupied. Selecting an available port automatically.")
            return automatic_port(bind_host, checker)
        if choice == "a":
            return automatic_port(bind_host, checker)
        if choice == "n":
            while True:
                value = input_fn("Enter a custom 4-digit port: ").strip()
                if not is_valid_custom_port(value):
                    print_fn(INVALID_PORT_MESSAGE)
                    continue
                port = int(value)
                if checker(bind_host, port):
                    return port
                print_fn(f"Port {port} is already occupied.")
                next_choice = input_fn("[N] Enter another port or [A] automatically select one: ").strip().lower()
                if next_choice == "a":
                    return automatic_port(bind_host, checker)
        else:
            print_fn("Please choose Y, N, A, or D.")
