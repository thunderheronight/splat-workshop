"""Texts shown outside the browser: desktop notifications and the tray icon.
The web interface has its own catalogue in web/i18n.js. To add a language, add it in both places."""
import os

TEXTS = {
    "en": {
        "app": "Splat Workshop",
        "notify.done.frame": "“{name}” is in the Gaussian XR gallery. Took {mins} min",
        "notify.done.local": "“{name}” is finished (on this PC only). Took {mins} min",
        "notify.done.title": "Scene finished",
        "notify.failed.title": "Conversion failed",
        "notify.failed": "“{name}” – details in Splat Workshop",
        "notify.upload.title": "Copied to the Frame",
        "notify.upload": "“{name}” is in the Gaussian XR gallery",
        "notify.upload_failed.title": "Copying failed",
        "notify.upload_failed": "“{name}” – is the Frame switched on?",
        "phase.0": "Extracting images", "phase.1": "Camera positions", "phase.2": "Training",
        "phase.3": "Converting", "phase.4": "Copying to the Frame",
        "tray.open": "Open Splat Workshop",
        "tray.idle": "Ready – nothing running",
        "tray.running": "{name}: {phase}",
        "tray.running_pct": "{name}: {phase} {pct} %",
        "tray.eta": " · about {mins} min left",
        "tray.checking": "Checking recordings …",
        "tray.downloading": "Downloading from the Frame … {pct} %",
        "tray.uploading": "Copying to the Frame …",
        "tray.lan": "In your network: {url}",
        "tray.copied": "Address copied",
        "tray.cancel": "Cancel conversion …",
        "tray.quit": "Quit",
        "tray.server_down": "Server is not responding",
        "dlg.cancel.title": "Cancel conversion?",
        "dlg.cancel.text": "“{name}” is being converted right now ({phase}).",
        "dlg.cancel.info": "Cancelling stops it immediately. You can continue later: start it again with the same name "
                           "and choose “Resume” – it picks up from the last saved state.",
        "dlg.cancel.yes": "Cancel conversion",
        "dlg.back": "Back",
        "dlg.quit.title": "Quit Splat Workshop?",
        "dlg.quit.text": "“{name}” is being converted right now ({phase}).",
        "dlg.quit.info": "You can let it finish in the background – open Splat Workshop again later to see the result. "
                         "Or cancel it; it can be resumed later with the same name and “Resume”.",
        "dlg.quit.keep": "Quit, keep converting",
        "dlg.quit.kill": "Cancel conversion and quit",
        "dlg.quit.other": "Still running: {what}. Quitting stops it.",
        "dlg.quit.other_yes": "Quit anyway",
        "what.download": "a download from the Frame",
        "what.check": "recording checks",
        "what.upload": "copying to the Frame",
    },
    "de": {
        "app": "Splat-Werkstatt",
        "notify.done.frame": "„{name}“ liegt in der Gaussian-XR-Galerie. Dauer: {mins} min",
        "notify.done.local": "„{name}“ ist fertig (nur auf dem PC). Dauer: {mins} min",
        "notify.done.title": "Szene fertig",
        "notify.failed.title": "Umwandlung fehlgeschlagen",
        "notify.failed": "„{name}“ – Details in der Splat-Werkstatt",
        "notify.upload.title": "Auf die Frame übertragen",
        "notify.upload": "„{name}“ liegt in der Gaussian-XR-Galerie",
        "notify.upload_failed.title": "Übertragung fehlgeschlagen",
        "notify.upload_failed": "„{name}“ – ist die Frame an?",
        "phase.0": "Bilder extrahieren", "phase.1": "Kamerapositionen", "phase.2": "Training",
        "phase.3": "Umwandeln", "phase.4": "Auf die Frame",
        "tray.open": "Splat-Werkstatt öffnen",
        "tray.idle": "Bereit – nichts läuft",
        "tray.running": "{name}: {phase}",
        "tray.running_pct": "{name}: {phase} {pct} %",
        "tray.eta": " · noch ca. {mins} min",
        "tray.checking": "Prüfe Aufnahmen …",
        "tray.downloading": "Hole von der Frame … {pct} %",
        "tray.uploading": "Übertrage auf die Frame …",
        "tray.lan": "Im Heimnetz: {url}",
        "tray.copied": "Adresse kopiert",
        "tray.cancel": "Umwandlung abbrechen …",
        "tray.quit": "Beenden",
        "tray.server_down": "Server antwortet nicht",
        "dlg.cancel.title": "Umwandlung abbrechen?",
        "dlg.cancel.text": "Gerade wird „{name}“ umgewandelt ({phase}).",
        "dlg.cancel.info": "Abbrechen stoppt den Lauf sofort. Du kannst später weitermachen: mit gleichem Namen erneut "
                           "starten und „Fortsetzen“ wählen – es geht ab dem letzten Zwischenstand weiter.",
        "dlg.cancel.yes": "Umwandlung abbrechen",
        "dlg.back": "Zurück",
        "dlg.quit.title": "Splat-Werkstatt beenden?",
        "dlg.quit.text": "Gerade wird „{name}“ umgewandelt ({phase}).",
        "dlg.quit.info": "Du kannst sie im Hintergrund fertig rechnen lassen – öffne die Splat-Werkstatt später "
                         "wieder, dann siehst du das Ergebnis. Oder du brichst sie ab; mit gleichem Namen und "
                         "„Fortsetzen“ geht es später weiter.",
        "dlg.quit.keep": "Beenden, weiterrechnen lassen",
        "dlg.quit.kill": "Umwandlung abbrechen und beenden",
        "dlg.quit.other": "Es läuft noch: {what}. Beenden stoppt das.",
        "dlg.quit.other_yes": "Trotzdem beenden",
        "what.download": "ein Download von der Frame",
        "what.check": "eine Aufnahmeprüfung",
        "what.upload": "eine Übertragung auf die Frame",
    },
}


def detect(setting: str = "auto") -> str:
    if setting in TEXTS:
        return setting
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        v = os.environ.get(var, "")
        code = v.split("_")[0].split(".")[0].split(":")[0].lower()
        if code in TEXTS:
            return code
    return "en"


class T:
    def __init__(self, lang: str):
        self.lang = lang if lang in TEXTS else "en"

    def __call__(self, key: str, **kw) -> str:
        s = TEXTS[self.lang].get(key) or TEXTS["en"].get(key) or key
        return s.format(**kw) if kw else s
