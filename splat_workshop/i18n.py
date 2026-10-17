"""Texts shown outside the browser: desktop notifications and the tray icon.
The web interface has its own catalogue in web/i18n.js. To add a language, add it in both places."""
import os

TEXTS = {
    "en": {
        "app": "Splat Workshop",
        "notify.done.frame": "“{name}” is in the Gaussian XR gallery. Took {mins} min",
        "notify.done.local": "“{name}” is finished (on this PC only). Took {mins} min",
        "notify.done.title": "Scene finished",
        "inhibit.job": "Converting “{name}”",
        "inhibit.upload": "Copying “{name}” to the Frame",
        "inhibit.download": "Downloading a recording from the Frame",
        "inhibit.combine": "Putting “{name}” together",
        "tour.full_name": "{name} (full)",
        "inhibit.busy": "Busy (converting, putting together, copying)",
        "notify.standby.title": "Everything done", "notify.standby": "The PC goes to standby in {s} seconds – to stop it, untick “Standby when done” in Splat Workshop.",
        "notify.standby_failed.title": "No standby", "notify.standby_failed": "The PC could not go to standby: {why}",
        "inhibit.mesh": "Making a mesh of “{name}”", "inhibit.import": "Importing “{name}”",
        "import.step.convert": "Converting", "import.step.preview": "Preview", "pick.splat": "Choose a splat file",
        "pick.recording": "Choose a recording", "mesh.step.mesh": "Meshing", "mesh.step.upright": "Standing it upright",
        "inhibit.versions": "Making versions of “{name}”", "ver.all": "all splats", "ver.size": "{n} M",
        "ver.clean.ceiling": "ceiling", "ver.clean.floor": "floor", "ver.clean.haze": "haze", "ver.clean.outside": "edges",
        "notify.failed.title": "Conversion failed",
        "notify.failed": "“{name}” – details in Splat Workshop",
        "notify.download.title": "Fetched from the Frame",
        "notify.download": "{n} recording(s) are on this PC – you can switch the Frame off",
        "notify.download_failed.title": "Fetching failed",
        "notify.download_failed": "{n} recording(s) could not be fetched ({ok} did) – is the Frame switched on?",
        "part.name": "{name} · part {i} of {n}",
        "notify.parts_done.title": "All parts converted",
        "notify.parts_done": "“{name}”: {n} parts are one room now – it is being put together",
        "notify.parts_done_alone": "“{name}”: {n} parts are one room now – put it together in Splat Workshop",
        "notify.parts_failed.title": "Conversion in parts stopped",
        "notify.parts_failed": "“{name}”: part {i} of {n} failed – details in Splat Workshop",
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
        "tray.update": "Check for updates", "tray.update.new": "Splat Workshop {latest} is out (you have {current}) – click for what's new. To update: git pull in the Splat Workshop folder, then restart.",
        "tray.update.none": "You have the newest version ({current}).", "tray.update.offline": "GitHub could not be reached – try again later.",
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
        "inhibit.job": "Wandelt „{name}“ um",
        "inhibit.upload": "Kopiert „{name}“ auf die Frame",
        "inhibit.download": "Lädt eine Aufnahme von der Frame",
        "inhibit.combine": "Setzt „{name}“ zusammen",
        "tour.full_name": "{name} (voll)",
        "inhibit.busy": "Beschäftigt (Umwandeln, Zusammensetzen, Kopieren)",
        "notify.standby.title": "Alles fertig", "notify.standby": "Der PC geht in {s} Sekunden in den Standby – zum Abbrechen in der Splat-Werkstatt den Haken bei „Danach in den Standby“ entfernen.",
        "notify.standby_failed.title": "Kein Standby", "notify.standby_failed": "Der PC konnte nicht in den Standby: {why}",
        "inhibit.mesh": "Erstellt ein Mesh von „{name}“", "inhibit.import": "Importiert „{name}“",
        "import.step.convert": "Umwandeln", "import.step.preview": "Vorschau", "pick.splat": "Splat-Datei wählen",
        "pick.recording": "Aufnahme wählen", "mesh.step.mesh": "Vernetzen", "mesh.step.upright": "Aufrichten",
        "inhibit.versions": "Erstellt Fassungen von „{name}“", "ver.all": "alle Splats", "ver.size": "{n} Mio.",
        "ver.clean.ceiling": "Decke", "ver.clean.floor": "Boden", "ver.clean.haze": "Dunst", "ver.clean.outside": "Rand",
        "notify.failed.title": "Umwandlung fehlgeschlagen",
        "notify.failed": "„{name}“ – Details in der Splat-Werkstatt",
        "notify.download.title": "Von der Frame geholt",
        "notify.download": "{n} Aufnahme(n) liegen auf dem PC – du kannst die Frame abschalten",
        "notify.download_failed.title": "Holen fehlgeschlagen",
        "notify.download_failed": "{n} Aufnahme(n) konnten nicht geholt werden ({ok} schon) – ist die Frame an?",
        "part.name": "{name} · Teil {i} von {n}",
        "notify.parts_done.title": "Alle Teile umgewandelt",
        "notify.parts_done": "„{name}“: {n} Teile sind jetzt ein Raum – er wird zusammengesetzt",
        "notify.parts_done_alone": "„{name}“: {n} Teile sind jetzt ein Raum – in der Splat-Werkstatt zusammensetzen",
        "notify.parts_failed.title": "Umwandlung in Teilen angehalten",
        "notify.parts_failed": "„{name}“: Teil {i} von {n} ist fehlgeschlagen – Details in der Splat-Werkstatt",
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
        "tray.update": "Nach Updates suchen", "tray.update.new": "Die Splat-Werkstatt {latest} ist da (du hast {current}) – Klick zeigt, was neu ist. Aktualisieren: git pull im Ordner der Splat-Werkstatt, dann neu starten.",
        "tray.update.none": "Du hast die neueste Version ({current}).", "tray.update.offline": "GitHub war nicht erreichbar – später noch einmal versuchen.",
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
