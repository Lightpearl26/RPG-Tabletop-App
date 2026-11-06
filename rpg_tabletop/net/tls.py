# -*- coding: utf-8 -*-
"""TLS 1.3 certificate generation, contexts and certificate pinning."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import ipaddress
from pathlib import Path
import ssl
from typing import Iterable

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("The 'cryptography' package is required for TLS certificates") from exc

from .config import TLSSettings


def _require_tls13() -> None:
    if not getattr(ssl, "HAS_TLSv1_3", False):
        raise RuntimeError("This Python/OpenSSL build does not support TLS 1.3")


def normalize_fingerprint(value: str) -> str:
    return "".join(ch for ch in str(value).lower() if ch in "0123456789abcdef")


def certificate_fingerprint(cert_file: str | Path) -> str:
    cert = x509.load_pem_x509_certificate(Path(cert_file).read_bytes())
    raw = cert.public_bytes(serialization.Encoding.DER)
    digest = sha256(raw).hexdigest()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2)).upper()


def ensure_server_certificate(
    settings: TLSSettings,
    *,
    extra_dns_names: Iterable[str] = (),
    extra_ip_addresses: Iterable[str] = (),
) -> tuple[Path, Path]:
    cert_path, key_path = Path(settings.cert_file), Path(settings.key_file)
    if cert_path.is_file() and key_path.is_file():
        return cert_path, key_path

    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, settings.common_name),
    ])
    now = datetime.now(timezone.utc)
    san: list[x509.GeneralName] = [
        x509.DNSName(settings.common_name),
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
    ]
    for name in extra_dns_names:
        name = str(name).strip()
        if name:
            san.append(x509.DNSName(name))
    for value in extra_ip_addresses:
        try:
            san.append(x509.IPAddress(ipaddress.ip_address(str(value).strip())))
        except ValueError:
            continue

    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .sign(private_key, hashes.SHA256())
    )
    key_path.write_bytes(private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def build_server_ssl_context(settings: TLSSettings) -> ssl.SSLContext:
    _require_tls13()
    cert_path, key_path = ensure_server_certificate(settings)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.load_cert_chain(str(cert_path), str(key_path))
    return context


def build_client_ssl_context(*, ca_file: str = "", pinning: bool = False) -> ssl.SSLContext:
    """Build a TLS-1.3-only client context.

    When certificate pinning is used, certificate verification is performed
    immediately after the TLS handshake with :func:`verify_peer_fingerprint`.
    There is deliberately no generic "insecure" mode.
    """
    _require_tls13()
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.maximum_version = ssl.TLSVersion.TLSv1_3
    if ca_file:
        path = Path(ca_file)
        if not path.is_file():
            raise FileNotFoundError(f"TLS CA/certificate file not found: {path}")
        context.load_verify_locations(cafile=str(path))
    elif pinning:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


def peer_certificate_fingerprint(writer) -> str:
    ssl_object = writer.get_extra_info("ssl_object")
    if ssl_object is None:
        raise ssl.SSLError("Connection is not using TLS")
    der = ssl_object.getpeercert(binary_form=True)
    if not der:
        raise ssl.SSLError("Peer did not provide a certificate")
    digest = sha256(der).hexdigest()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2)).upper()


def verify_peer_fingerprint(writer, expected: str) -> str:
    actual = peer_certificate_fingerprint(writer)
    if normalize_fingerprint(actual) != normalize_fingerprint(expected):
        raise ssl.SSLCertVerificationError(
            f"Server certificate fingerprint mismatch: expected {expected}, got {actual}"
        )
    return actual
