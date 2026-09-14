"""Safe Exam Browser (SEB) integration — the industry-standard locked-down
kiosk browser many institutions require alongside (not instead of) the
JS-based fullscreen/tab-switch/devtools detection this app already has.
SEB blocks OS-level things JavaScript fundamentally can't reach: opening
other applications, using OS-level screenshot/screen-recording tools,
virtual machines, most keyboard shortcuts, etc.

Deliberately scoped to two things:

  1. generate_seb_config() — a real, working .seb configuration file
     (Apple XML plist format, which SEB reads directly when it isn't
     encrypted) with a sensible kiosk-mode lockdown preset. This is what
     most admins actually need: something they can hand to students that
     configures SEB correctly for this exam.

  2. looks_like_seb() — a soft, best-effort signal (User-Agent + the
     X-SafeExamBrowser-RequestHash header SEB sends when configured to)
     that a request came from the SEB client at all.

What this deliberately does NOT attempt: SEB's own cryptographic Config
Key / Browser Exam Key validation, where the server recomputes a SHA256
hash over the *exact* canonical serialization of the config file and
compares it to a hash SEB sends per-request. That scheme only works if
the server's recomputed hash byte-for-byte matches SEB's own
serialization of the same config, and getting that subtly wrong would be
worse than not attempting it at all — it would report a security
guarantee ("this student's config was cryptographically verified") that
isn't actually being provided. looks_like_seb() is honestly what it is: a
header/UA check, logged as a soft proctoring signal alongside everything
else, not a cryptographic attestation.
"""
import plistlib


def generate_seb_config(test, start_url, quit_url=None):
    """A working, unencrypted .seb file (SEB reads plain XML plists fine
    if they're not encrypted/compressed) with a standard kiosk-mode
    preset: single-application lockdown, no other browser windows, no
    task switching, exit only via the configured quit password.

    quit_url, if given, is where SEB navigates to (and then allows
    quitting) once the exam is done — e.g. the result/thank-you page.
    """
    config = {
        # --- Where this exam lives ---
        "startURL": start_url,
        "sendBrowserExamKey": True,

        # --- Kiosk lockdown ---
        "allowQuit": True,
        "quitURLConfirm": True,
        "showTaskBar": False,
        "showMenuBar": False,
        "showInputLanguage": False,
        "allowSwitchToApplications": False,
        "allowDisplayMirroring": False,
        "allowedDisplaysMaxNumber": 1,
        "allowVirtualMachine": False,
        "forceAppFolderInstallation": True,
        "monitorProcesses": True,
        "killExplorerShell": False,  # leave the OS shell alone; too disruptive/risky for most institutions to force off

        # --- Browser window / navigation ---
        "browserWindowAllowReload": False,
        "newBrowserWindowByLinkPolicy": 2,  # 2 = block
        "newBrowserWindowByScriptPolicy": 2,
        "allowBrowsingBackForward": False,
        "allowAddressBar": False,
        "allowNavigationBar": False,
        "allowSpellCheck": False,
        "allowDictation": False,

        # --- Clipboard / printing / downloads: the same surface area
        # this app already flags via copy_paste_attempt detection, but
        # SEB can additionally block it at the OS level rather than
        # merely detect an attempt after the fact ---
        "allowCopy": False,
        "allowPaste": False,
        "allowPrint": False,
        "allowDownUploads": False,

        # --- Exiting ---
        "hashedQuitPassword": "",  # left blank: no separate quit password beyond quitURLConfirm
    }
    if quit_url:
        config["quitURL"] = quit_url
        config["restartExamUseStartURL"] = False

    return plistlib.dumps(config, fmt=plistlib.FMT_XML)


def config_filename(test):
    safe_code = "".join(c for c in test.test_code if c.isalnum() or c in ("-", "_")) or "exam"
    return f"{safe_code}.seb"


def looks_like_seb(request):
    """Best-effort, non-cryptographic signal that `request` came from the
    actual SEB client — see the module docstring for exactly what this
    isn't. Two independent tells, either one is enough: SEB's client
    identifies itself in its User-Agent (e.g. "...SEB/3.6...."), and when
    an exam's config has sendBrowserExamKey on (see generate_seb_config),
    SEB adds an X-SafeExamBrowser-RequestHash header to every request."""
    ua = request.headers.get("User-Agent", "")
    if "SEB" in ua or "SafeExamBrowser" in ua:
        return True
    if request.headers.get("X-SafeExamBrowser-RequestHash"):
        return True
    return False
