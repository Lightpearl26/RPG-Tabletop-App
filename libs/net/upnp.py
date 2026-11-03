# -*- coding: utf-8 -*-
"""UPnP port mapping and CGNAT detection."""
from __future__ import annotations
from dataclasses import dataclass
import ipaddress
from typing import Any


CGNAT_RANGES = tuple(ipaddress.ip_network(value) for value in (
    "10.0.0.0/8", "100.64.0.0/10", "172.16.0.0/12", "192.168.0.0/16",
))


@dataclass(frozen=True, slots=True)
class UPnPStatus:
    available: bool
    mapped: bool
    lan_address: str = ""
    external_address: str = ""
    behind_cgnat: bool = True
    message: str = ""


class UPnPPortMapping:
    def __init__(self, port: int, *, description: str = "RPG Tabletop") -> None:
        self.port = int(port)
        self.description = description
        self._upnp: Any = None
        self._mapped = False
        self.status = UPnPStatus(False, False, message="Not initialized")

    def start(self) -> UPnPStatus:
        try:
            import miniupnpc
        except ImportError:
            self.status = UPnPStatus(False, False, message="miniupnpc is not installed")
            return self.status
        try:
            upnp = miniupnpc.UPnP()
            upnp.discoverdelay = 2000
            if not upnp.discover():
                self.status = UPnPStatus(True, False, message="No UPnP/IGD device found")
                return self.status
            upnp.selectigd()
            external = str(upnp.externalipaddress() or "")
            try:
                address = ipaddress.ip_address(external)
                behind_cgnat = any(address in network for network in CGNAT_RANGES)
            except ValueError:
                behind_cgnat = True
            mapped = bool(upnp.addportmapping(
                self.port, "TCP", upnp.lanaddr, self.port, self.description, ""
            ))
            self._upnp, self._mapped = upnp, mapped
            self.status = UPnPStatus(
                True, mapped, str(upnp.lanaddr), external, behind_cgnat,
                "Port mapped" if mapped else "Router refused UPnP mapping",
            )
            return self.status
        except (OSError, RuntimeError, ValueError) as exc:
            self.status = UPnPStatus(True, False, message=f"UPnP error: {exc}")
            return self.status

    def stop(self) -> None:
        if self._mapped and self._upnp is not None:
            try:
                self._upnp.deleteportmapping(self.port, "TCP")
            except (OSError, RuntimeError):
                pass
        self._mapped = False
