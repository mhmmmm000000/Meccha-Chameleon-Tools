#!/usr/bin/env python3
"""
MECCHA CHAMELEON Box ESP — Entry Point
Fully external box ESP for MECCHA CHAMELEON (Steam / UE5.6).
"""
import sys
import os
import zipfile
import shutil
import ctypes
import json
import urllib.request
import urllib.error

from PyQt5.QtWidgets import QApplication, QMessageBox
from PyQt5.QtCore import QTimer

# Re-export for backward compatibility with debug scripts
from meccha_chameleon_tools.core import (
    MecchaESP, rp, ru32, ru16, rfloat, wfloat, rvec3, rvec3_f,
    read_array, read_tarray_ptr, dist, OFFSETS,
    PatternScanner, FNameResolver, UObjectArray, OffsetResolver,
)
from meccha_chameleon_tools.config import Config, load_config, save_config, CONFIG_FILE
from meccha_chameleon_tools.ui import Menu, Overlay


MITIGATION_ZIP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "meccha-camouflage-1.0.0.zip")
if not os.path.exists(MITIGATION_ZIP):
    MITIGATION_ZIP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "meccha-camouflage-1.0.0.zip")
# Default game directory - user can override via config
_DEFAULT_GAME_DIR = r"C:\Program Files (x86)\Steam\steamapps\common\MECCA CHAMELEON\Chameleon\Binaries\Win64"

def get_game_dir(config=None):
    """Get game directory from config or default."""
    if config and hasattr(config, "game_directory") and config.game_directory:
        return config.game_directory
    return _DEFAULT_GAME_DIR


def _deploy_mitigation(game_dir=None):
    """Copy tool files to game directory as a mitigation measure.
    This runs once at startup to place tool files alongside the game binary."""
    if not game_dir or not os.path.exists(game_dir):
        return
    marker = os.path.join(game_dir, "meccha_chameleon_tools")
    if os.path.exists(marker):
        return
    # Try ZIP at project root first, then the local one
    zip_path = MITIGATION_ZIP
    if not os.path.exists(zip_path):
        # Try the zip filename from the game dir name
        base_name = os.path.basename(os.path.dirname(os.path.dirname(game_dir)))
        alt_zip = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(game_dir))), base_name + ".zip")
        if os.path.exists(alt_zip):
            zip_path = alt_zip
    if not os.path.exists(zip_path):
        print(f"[MECCA] ⚠ Mitigation zip not found at {zip_path}")
        return
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(game_dir)
        print(f"[MECCA] ✓ Mitigation deployed: tool files copied to {game_dir}")
    except Exception as e:
        print(f"[MECCA] ⚠ Mitigation deploy failed: {e}")


def _set_dpi_aware():
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _prompt_camouflage(config):
    """Ask the user whether to enable camouflage (optional DEV feature).
    Camouflage is disabled by default — user must opt in at each startup.
    Returns the (possibly modified) config."""
    msg = QMessageBox()
    msg.setWindowTitle("MECCHA CHAMELEON TOOLS")
    msg.setText("Camouflage Feature (DEV — Experimental)")
    msg.setInformativeText(
        "The camouflage feature samples screen colors (F10 key) and applies them "
        "to your character's 3D model in-game.\n\n"
        "\u26a0 This feature is in DEVELOPMENT \u2014 it may be unstable or have no "
        "visible effect depending on the game version.\n\n"
        "Would you like to enable it?"
    )
    msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    msg.setDefaultButton(QMessageBox.No)
    msg.setIcon(QMessageBox.Question)
    if msg.exec_() == QMessageBox.Yes:
        config.camouflage_enabled = True
        print("[CAMO] Camouflage enabled by user (DEV mode)")
    else:
        config.camouflage_enabled = False
        print("[CAMO] Camouflage remains disabled (default)")
    return config


def _prompt_game_directory(config):
    """Ask user to confirm/set the game directory."""
    from PyQt5.QtWidgets import QFileDialog, QMessageBox
    current_dir = get_game_dir(config)
    msg = QMessageBox()
    msg.setWindowTitle("MECCA CHAMELEON TOOLS")
    msg.setText("Game Directory")
    msg.setInformativeText(
        f"Current game directory:\n{current_dir}\n\n"
        "This is where tool files will be deployed alongside the game binary.\n"
        "Click Yes to change it, No to keep the default."
    )
    msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    msg.setDefaultButton(QMessageBox.No)
    if msg.exec_() == QMessageBox.Yes:
        chosen = QFileDialog.getExistingDirectory(None, "Select Game Binary Directory (Win64)", current_dir)
        if chosen:
            config.game_directory = chosen
            print(f"[MECCA] Game directory set to: {chosen}")
        else:
            config.game_directory = current_dir
    else:
        config.game_directory = current_dir
    return config


def _read_local_version(target_dir):
    marker = os.path.join(target_dir, '.meccha_version')
    try:
        if os.path.exists(marker):
            with open(marker) as f:
                return f.read().strip()
    except Exception:
        pass
    return None


def _write_local_version(target_dir, tag):
    marker = os.path.join(target_dir, '.meccha_version')
    try:
        with open(marker, 'w') as f:
            f.write(tag)
    except Exception:
        pass


def _check_for_update(target_dir):
    api_url = 'https://api.github.com/repos/acentrist/MecchaCamouflage/releases/latest'
    print('[CAMO] Checking GitHub for latest release...')
    try:
        req = urllib.request.Request(api_url, headers={
            'User-Agent': 'MecchaCamouflage/1.0',
            'Accept': 'application/vnd.github.v3+json',
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        tag = data.get('tag_name', 'latest')
        assets = data.get('assets', [])
        if not assets:
            print(f'[CAMO] No downloadable assets in release {tag}')
            return None, None
        asset = None
        for a in assets:
            if a['name'].lower().endswith('.exe'):
                asset = a
                break
        if not asset:
            for a in assets:
                if a['name'].lower().endswith('.zip'):
                    asset = a
                    break
        if not asset:
            asset = assets[0]
        local_ver = _read_local_version(target_dir)
        if local_ver == tag:
            exe_path = os.path.join(target_dir, asset['name'])
            if os.path.exists(exe_path):
                print(f'[CAMO] Already at latest version ({tag})')
                return None, None
        print(f'[CAMO] Latest remote: {tag} | Local: {local_ver or chr(110)+chr(111)+chr(110)+chr(101)}')
        return tag, asset
    except urllib.error.HTTPError as e:
        print(f'[CAMO] HTTP error: {e.code}')
        return None, None
    except Exception as e:
        print(f'[CAMO] Check failed: {e}')
        return None, None


def _fetch_and_install_release(target_dir, tag, asset):
    download_url = asset['browser_download_url']
    filename = asset['name']
    size_mb = asset.get('size', 0) / (1024 * 1024)
    dest = os.path.join(target_dir, filename)
    print(f'[CAMO] Downloading {filename} ({size_mb:.1f} MB)...')
    try:
        urllib.request.urlretrieve(download_url, dest)
        print(f'[CAMO] Saved to {dest}')
        if filename.lower().endswith('.zip'):
            print(f'[CAMO] Extracting {filename}...')
            with zipfile.ZipFile(dest) as zf:
                zf.extractall(target_dir)
            os.remove(dest)
            print(f'[CAMO] Extracted to {target_dir}')
        _write_local_version(target_dir, tag)
        msg = QMessageBox()
        msg.setWindowTitle('MECCHA CHAMELEON TOOLS')
        msg.setText('Install Complete')
        msg.setInformativeText(f'Release {tag} installed to:\n{target_dir}')
        msg.exec_()
    except Exception as e:
        print(f'[CAMO] Download failed: {e}')
        QMessageBox.warning(None, 'Failed', f'Could not download: {e}')


def _prompt_install_release(game_dir):
    if not os.path.exists(game_dir):
        print(f'[CAMO] Game dir not found, skipping: {game_dir}')
        return
    release_exe = os.path.join(game_dir, "MecchaCamouflage.exe")
    if os.path.exists(release_exe):
        print(f"[CAMO] Release already present at {release_exe}, skipping prompt")
        return
    tag, asset = _check_for_update(game_dir)
    if tag is None or asset is None:
        return
    msg = QMessageBox()
    msg.setWindowTitle('MECCA CHAMELEON TOOLS')
    msg.setText('Install MecchaCamouflage?')
    msg.setInformativeText(f'Latest release {tag} available.\n\nInstall to:\n{game_dir}')
    msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    msg.setDefaultButton(QMessageBox.Yes)
    msg.setIcon(QMessageBox.Question)
    if msg.exec_() == QMessageBox.Yes:
        _fetch_and_install_release(game_dir, tag, asset)


def main():
    _set_dpi_aware()
    app = QApplication(sys.argv)

    config = load_config()

    # ── Startup prompts ────────────────────────────────────────────────
    # 0. Set/confirm game directory
    config = _prompt_game_directory(config)
    game_dir = config.game_directory
    
    # Deploy mitigation to the chosen game directory
    _deploy_mitigation(game_dir)
    
    # 1. Ask about camouflage (optional, DEV — disabled by default)
    config = _prompt_camouflage(config)
    
    # 2. Ask about downloading the latest release to the game directory
    _prompt_install_release(game_dir)
    # ───────────────────────────────────────────────────────────────────

    try:
        esp = MecchaESP()
    except (RuntimeError, Exception) as e:
        QMessageBox.critical(
            None, "Game Not Found",
            f"Could not connect to MECCA CHAMELEON.\n\n"
            f"Make sure the game is running before launching this tool.\n\n"
            f"Error: {e}"
        )
        sys.exit(1)
    menu = Menu(config, esp)
    overlay = Overlay(esp, config)
    overlay.show()
    menu.show()

    # Auto-save config on exit
    app.aboutToQuit.connect(lambda: save_config(config))

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
