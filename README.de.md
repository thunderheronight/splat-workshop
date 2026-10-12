# Splat Workshop (Splat-Werkstatt)

**Aufnahmen der Arcturus Vision Camera an der Steam Frame in Gaussian Splats umwandeln – und direkt in die Gaussian-XR-Galerie auf der Brille. Mit Weboberfläche, Tray-Symbol und Aufnahmeprüfung.**

[English](README.md)

![Splat-Werkstatt (Beispiel-Screenshot, Vorschaubilder durch Platzhalter ersetzt)](docs/screenshot.png)

Arcturus Vision stellt die Rekonstruktions-Skripte und einen Spirula-Studio-Fork bereit. Bis eine Aufnahme als Szene in [Gaussian XR](https://store.steampowered.com/app/3977540/Arcturus_Vision_Gaussian_XR/) auftaucht, sind aber viele Handgriffe nötig. Die Splat-Werkstatt packt den ganzen Weg in eine lokale Weboberfläche:

1. **Aufnahme wählen** – direkt von der Frame (per SSH) oder vom PC, mit Vorschaubildern.
2. **Prüfen**, bevor eine Stunde Rechenzeit draufgeht: Helligkeit, Struktur, schnelle Schwenks, verdeckte Linse – Sekunde für Sekunde.
3. **Umwandeln** mit Qualitätsstufe (Schnell / Hoch / Maximal) oder eigenen Werten, mit Fortschritt, Restzeit und PSNR-Kurve.
4. **In die Galerie**: Vorschaubild und Namensschild im Stil der Beispielszenen, kopiert in den `Scenes`-Ordner von Gaussian XR auf der Frame.

## Funktionen

- **Weboberfläche** (nur Python-Standardbibliothek) – auf Wunsch auch vom Handy oder einem zweiten Rechner im Heimnetz.
- **Tray-Symbol** – zeigt, ob die Werkstatt läuft und was sie tut (Fortschrittsring). Öffnen, Umwandlung abbrechen, Beenden. Beenden während einer Umwandlung fragt nach: im Hintergrund fertig rechnen lassen oder abbrechen.
- **Umwandlungen laufen unabhängig weiter** – Browser zu, Tray beendet, Neustart: Werkstatt wieder öffnen, Fortschritt ist wieder da.
- **Abbrechen & Fortsetzen** ab dem letzten Zwischenstand.
- **Aufnahmeprüfung ohne KI**: reine Messwerte je Sekunde, farbige Zeitleiste und konkrete Hinweise („0:45–0:59 wenig Struktur“).
- **Fertige Szenen**: später auf die Frame übertragen, erneut übertragen, Ordner oder Spirula Studio öffnen.
- Deutsche und englische Oberfläche (automatisch, umschaltbar).

## Voraussetzungen, Installation, Bedienung

Siehe die [englische README](README.md) – kurz:

```bash
git clone https://github.com/thunderheronight/splat-workshop.git
cd splat-workshop
./install.sh --desktop        # Menüeintrag + Desktop-Verknüpfung; --autostart für das Tray beim Anmelden
```

Danach in `~/.config/splat-workshop/config.toml` die Pfade zur Arcturus-Pipeline ([arcturus.vision/apps](https://arcturus.vision/apps)), zu Spirula Studio und die Adresse der Frame eintragen. Was noch fehlt, zeigt die Werkstatt oben auf der Seite an.

Nach dem Übertragen **Gaussian XR auf der Frame neu starten**, damit die Szene in der Galerie erscheint.

## Aufnahmetipps

- Viel Licht (Tageslicht plus Raumlicht), **HDR mit 30 fps** in der Kamera-App, nicht „Linear“.
- **30–90 s pro Raum**, langsam gehen und langsam drehen, Dinge von mehreren Seiten zeigen.
- Nicht lange auf kahle Wände, Decke oder Fliesen halten.
- Bewegte Personen werden zu Geisterschleiern.

## Lizenz

Ein Community-Tool, **nicht mit Valve oder Arcturus Vision verbunden**. Lizenz: [GNU GPL v3.0](LICENSE).
