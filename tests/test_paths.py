from rpg_tabletop.core import paths


def test_user_data_dir_can_be_overridden(user_data):
    assert paths.user_data_dir() == user_data
    assert paths.default_campaign_dir() == user_data / "default_campaign"
    assert paths.server_certificate_files() == (
        user_data / "certs" / "server.crt",
        user_data / "certs" / "server.key",
    )


def test_adopt_legacy_certificate_copies_once(tmp_path):
    legacy = tmp_path / "campaign" / ".runtime" / "certs"
    legacy.mkdir(parents=True)
    (legacy / "server.crt").write_text("CERT")
    (legacy / "server.key").write_text("KEY")

    assert paths.adopt_legacy_certificate(legacy) is True
    cert, key = paths.server_certificate_files()
    assert cert.read_text() == "CERT" and key.read_text() == "KEY"

    (legacy / "server.crt").write_text("OTHER")
    assert paths.adopt_legacy_certificate(legacy) is False
    assert cert.read_text() == "CERT"


def test_adopt_legacy_certificate_needs_both_files(tmp_path):
    legacy = tmp_path / "certs"
    legacy.mkdir()
    (legacy / "server.crt").write_text("CERT")
    assert paths.adopt_legacy_certificate(legacy) is False
    assert not paths.certificates_dir().exists()
