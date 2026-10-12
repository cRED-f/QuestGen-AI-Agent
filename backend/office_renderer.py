"""Locate and run LibreOffice on the *backend* host, including Windows installs.

A configured executable takes precedence over auto-detection; failures are kept
separate from missing installations so the UI can give a useful remedy.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


class OfficeRenderError(RuntimeError):
    """User-actionable conversion error with no raw file path or document text."""


@dataclass(frozen=True)
class OfficeExecutable:
    path: str | None
    source: str | None
    issue: str | None


def _windows_install_locations() -> list[Path]:
    locations: list[Path] = []
    for key in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)", "LOCALAPPDATA"):
        root = os.environ.get(key)
        if root:
            # Some user-scoped installations live under LocalAppData/Programs.
            locations.append(Path(root) / ("Programs/LibreOffice/program/soffice.exe" if key == "LOCALAPPDATA" else "LibreOffice/program/soffice.exe"))
    try:
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for key_name in (r"SOFTWARE\LibreOffice\UNO\InstallPath", r"SOFTWARE\WOW6432Node\LibreOffice\UNO\InstallPath"):
                try:
                    with winreg.OpenKey(hive, key_name) as handle:
                        directory, _ = winreg.QueryValueEx(handle, "")
                    locations.append(Path(directory) / "soffice.exe")
                except (OSError, ValueError):
                    continue
    except ImportError:
        pass
    return locations


def locate_office() -> OfficeExecutable:
    """Find executable without relying on an already-running server's PATH alone."""
    configured = os.getenv("QUESTGEN_LIBREOFFICE_CMD", "").strip().strip('"').strip("'")
    if configured:
        configured = os.path.expandvars(os.path.expanduser(configured))
        candidate = Path(configured)
        if candidate.is_dir():
            candidate = candidate / ("soffice.exe" if sys.platform == "win32" else "soffice")
        resolved = str(candidate) if candidate.is_file() else shutil.which(configured)
        if resolved:
            return OfficeExecutable(resolved, "QUESTGEN_LIBREOFFICE_CMD", None)
        return OfficeExecutable(None, None, "QUESTGEN_LIBREOFFICE_CMD does not point to a valid LibreOffice executable on the backend machine.")

    for executable in ("soffice", "soffice.exe", "libreoffice"):
        resolved = shutil.which(executable)
        if resolved:
            return OfficeExecutable(resolved, "PATH", None)

    if sys.platform == "win32":
        for path in _windows_install_locations():
            if path.is_file():
                return OfficeExecutable(str(path), "Windows installation", None)
    elif sys.platform == "darwin":
        candidate = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
        if candidate.is_file():
            return OfficeExecutable(str(candidate), "macOS application", None)

    return OfficeExecutable(None, None,
        "LibreOffice was not found by the QuestGen backend. Install it on the backend computer "
        "or configure QUESTGEN_LIBREOFFICE_CMD to the full soffice executable path, then reprocess the file.")


def check_office() -> tuple[bool, str, str | None]:
    """Probe the executable, not just its PATH presence. Never return local paths."""
    found = locate_office()
    if not found.path:
        return False, found.issue or "LibreOffice not found", None
    try:
        result = subprocess.run([found.path, "--version"], capture_output=True, timeout=8, check=False)
        if result.returncode != 0:
            return False, "LibreOffice was found but could not start. Verify permissions and the executable, then restart QuestGen.", found.source
    except subprocess.TimeoutExpired:
        return False, "LibreOffice was found but timed out during startup. Close other stuck LibreOffice processes and retry.", found.source
    except OSError:
        return False, "LibreOffice was found but Windows/the OS could not launch it. Check permissions and the installed executable.", found.source
    return True, f"LibreOffice executable verified ({found.source}). Reprocess existing PowerPoints to analyze their slides.", found.source


def convert_pptx_to_pdf(source: Path, output: Path) -> Path:
    found = locate_office()
    if not found.path:
        raise OfficeRenderError(found.issue or "LibreOffice executable was not found")

    # LibreOffice cannot share a profile between multiple conversion processes.
    # The job-specific output directory is always absolute (TemporaryDirectory).
    profile = (output / "profile").resolve().as_uri()
    try:
        result = subprocess.run(
            [found.path, f"-env:UserInstallation={profile}", "--headless", "--convert-to", "pdf:impress_pdf_Export",
             "--outdir", str(output), str(source)],
            capture_output=True, timeout=90, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise OfficeRenderError("LibreOffice slide rendering timed out. Close stalled LibreOffice processes and retry reprocessing.") from exc
    except OSError as exc:
        raise OfficeRenderError("LibreOffice was found but could not be launched by the QuestGen backend. Check the backend account's permissions.") from exc
    if result.returncode != 0:
        raise OfficeRenderError(f"LibreOffice failed converting the PowerPoint (exit code {result.returncode}). Check that the file opens in LibreOffice and retry.")
    rendered = output / (source.stem + ".pdf")
    if not rendered.is_file() or rendered.stat().st_size == 0:
        raise OfficeRenderError("LibreOffice started but produced no PDF slides. Verify the presentation opens in LibreOffice and retry.")
    return rendered
