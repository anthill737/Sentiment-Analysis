"""Structural checks for the Windows launcher scripts (P1-T2).

These tests verify that the launcher artifacts exist and contain the logic
required by the acceptance criteria. They do NOT execute the bat files (which
would require network access and Windows runtimes).
"""

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


# ─── Artifact existence ──────────────────────────────────────────────────────


def test_start_bat_exists():
    assert (ROOT / "Start SA Runner.bat").exists(), (
        "'Start SA Runner.bat' missing from project root"
    )


def test_stop_bat_exists():
    assert (ROOT / "Stop SA Runner.bat").exists(), (
        "'Stop SA Runner.bat' missing from project root"
    )


def test_bootstrap_ps1_exists():
    assert (ROOT / "scripts" / "bootstrap.ps1").exists(), (
        "scripts/bootstrap.ps1 missing"
    )


# ─── AC2: SHA256 mismatch prints URL, expected hash, actual hash ─────────────


def test_bootstrap_prints_sha256_url_on_mismatch():
    content = _read(ROOT / "scripts" / "bootstrap.ps1")
    assert "SHA256" in content.upper()
    assert "Expected" in content
    assert "Actual" in content
    assert "$Url" in content


# ─── AC2: partial directory is deleted on hash mismatch ─────────────────────


def test_bootstrap_deletes_cleanup_dir_on_mismatch():
    content = _read(ROOT / "scripts" / "bootstrap.ps1")
    assert "CleanupDir" in content
    assert "Remove-Item" in content


# ─── AC2: SHA256 hashes are well-formed and not placeholder values ───────────


def test_bootstrap_sha256_hashes_are_well_formed():
    content = _read(ROOT / "scripts" / "bootstrap.ps1")
    hashes = re.findall(r"Sha256\s*=\s*'([^']+)'", content)
    assert len(hashes) >= 3, f"Expected at least 3 Sha256 entries, found {len(hashes)}"

    hex_pattern = re.compile(r"^[0-9a-f]{64}$")
    for h in hashes:
        assert hex_pattern.match(h), f"Not a valid 64-char lowercase hex SHA256: {h!r}"
        # Reject sequential-nibble placeholders like a1b2c3d4e5f6... or 0102030405...
        assert not h.startswith("a1b2c3d4"), (
            f"Hash looks like a sequential placeholder: {h!r}"
        )
        assert not h.startswith("01020304"), (
            f"Hash looks like a sequential placeholder: {h!r}"
        )

    # Reject fabricated hashes sharing a suspiciously long common substring
    # (probability ~10^-38 for genuine SHA256 digests to share 20 chars)
    for i, h1 in enumerate(hashes):
        for h2 in hashes[i + 1 :]:
            for start in range(len(h1) - 19):
                substr = h1[start : start + 20]
                assert substr not in h2, (
                    f"Hashes share a 20-char substring '{substr}', "
                    f"suggesting fabrication:\n  {h1}\n  {h2}"
                )


# ─── AC3: skip-if-present logic (pinned-version sentinel) ───────────────────


def test_bootstrap_skips_existing_components():
    content = _read(ROOT / "scripts" / "bootstrap.ps1")
    assert "pinned-version" in content or "Sentinel" in content, (
        "No skip-if-present sentinel logic found"
    )
    assert "skipping" in content.lower()


# ─── AC3: pip install and npm install are gated behind sentinels ─────────────


def test_bootstrap_pip_install_is_gated():
    content = _read(ROOT / "scripts" / "bootstrap.ps1")
    assert "pip-hash" in content or "PipHashSentinel" in content, (
        "pip install must be gated behind a requirements.txt hash sentinel for warm-path speed"
    )


def test_bootstrap_npm_install_is_gated():
    content = _read(ROOT / "scripts" / "bootstrap.ps1")
    assert "npm-hash" in content or "NpmHashSentinel" in content, (
        "npm install must be gated behind a package manifest hash sentinel for warm-path speed"
    )


# ─── AC4: port conflict error includes PID ──────────────────────────────────


def test_start_bat_port_conflict_includes_pid():
    content = _read(ROOT / "Start SA Runner.bat")
    assert "PORT_PID" in content
    assert "PID" in content.upper()


# ─── AC5: browser open comes AFTER the polling loop ─────────────────────────


def test_start_bat_browser_open_after_poll():
    content = _read(ROOT / "Start SA Runner.bat")
    poll_pos = content.find("POLL_LOOP")
    browser_last = content.rfind("SERVER_URL!")
    assert poll_pos != -1, "POLL_LOOP label not found in Start SA Runner.bat"
    assert browser_last != -1, "Browser-open SERVER_URL reference not found"
    assert poll_pos < browser_last, (
        "POLL_LOOP must appear before the browser-open command"
    )


# ─── AC5: polling loop exists with goto ─────────────────────────────────────


def test_start_bat_has_polling_loop():
    content = _read(ROOT / "Start SA Runner.bat")
    assert "POLL_LOOP" in content
    assert "goto POLL_LOOP" in content or "goto OPEN_BROWSER" in content
