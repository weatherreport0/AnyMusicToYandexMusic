"""One-time setup: open Chrome, user logs into YouTube, cookies stay in profile."""

import os
import subprocess
import sys

CHROME_PATHS = [
    os.path.join(os.environ.get("PROGRAMFILES", ""), "Google", "Chrome", "Application", "chrome.exe"),
    os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "Google", "Chrome", "Application", "chrome.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
]

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE_DIR = os.path.join(BOT_DIR, ".chrome_profile")


def _find_chrome() -> str:
    for p in CHROME_PATHS:
        if os.path.exists(p):
            return p
    raise FileNotFoundError("Chrome not found")


def setup():
    chrome = _find_chrome()
    print("=" * 50)
    print("  ONE-TIME YouTube login")
    print("  (this will never happen again)")
    print("=" * 50)
    print()
    print("Chrome will open. Please:")
    print("  1. Log into your Google account")
    print("  2. Close Chrome when done")
    print()

    proc = subprocess.Popen([
        chrome,
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        "--disable-default-apps",
        "https://accounts.google.com/ServiceLogin?continue=https://www.youtube.com/",
    ])

    print("Waiting for Chrome to close...")
    proc.wait()
    print("Done! Chrome will never open again.")


if __name__ == "__main__":
    setup()
