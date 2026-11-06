# -*- coding: utf-8 -*-
"""Windows firewall helper carried forward from the legacy server."""
from __future__ import annotations
from dataclasses import dataclass
import platform
import subprocess


@dataclass(frozen=True, slots=True)
class FirewallResult:
    supported: bool
    present: bool
    created: bool
    message: str
    manual_command: str = ""


def ensure_windows_firewall_rule(port: int, *, prefix: str = "RPG Tabletop Server") -> FirewallResult:
    if platform.system() != "Windows":
        return FirewallResult(False, False, False, "Firewall automation is Windows-only")
    port = int(port)
    if not 1 <= port <= 65535:
        raise ValueError("Invalid TCP port")
    rule_name = f"{prefix} {port}"
    ps_rule = (
        f"New-NetFirewallRule -DisplayName '{rule_name}' -Direction Inbound "
        f"-Protocol TCP -LocalPort {port} -Action Allow -Profile Any"
    )
    check = subprocess.run(
        ["powershell", "-NonInteractive", "-Command",
         f"Get-NetFirewallRule -DisplayName '{rule_name}' -ErrorAction Stop; exit 0"],
        capture_output=True, text=True, check=False,
    )
    if check.returncode == 0:
        return FirewallResult(True, True, False, f"Rule '{rule_name}' already exists")

    direct = subprocess.run(
        ["powershell", "-NonInteractive", "-Command", ps_rule],
        capture_output=True, text=True, check=False,
    )
    if direct.returncode == 0:
        return FirewallResult(True, True, True, f"Rule '{rule_name}' created")

    elevated = subprocess.run(
        ["powershell", "-Command",
         f"Start-Process powershell -Verb RunAs -Wait "
         f"-ArgumentList '-NonInteractive', '-Command', \"{ps_rule}\""],
        check=False,
    )
    if elevated.returncode == 0:
        return FirewallResult(True, True, True, f"Rule '{rule_name}' created with UAC elevation")
    return FirewallResult(
        True, False, False,
        "Firewall rule was not created (UAC denied or command failed)",
        ps_rule,
    )
