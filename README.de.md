# Splat Workshop (Splat-Werkstatt)

**Eine grafische Oberfläche für die Rekonstruktions-Pipeline von Arcturus Vision: Aufnahme der Arcturus Vision Camera an der Steam Frame auswählen, prüfen, umwandeln lassen und das Ergebnis direkt in die Gaussian-XR-Galerie auf der Brille bringen. Mit Weboberfläche, Tray-Symbol und Aufnahmeprüfung.**

[English](README.md)

![Splat-Werkstatt (Beispiel-Screenshot, Vorschaubilder durch Platzhalter ersetzt)](docs/screenshot.png)

Räume, eine Statue, dein Auto, ein Garten – alles, um das du mit der Kamera herumgehen kannst, wird zu einer Szene, in die du hineinsteigen kannst.

Die Splat-Werkstatt rechnet die Splats nicht selbst: Die Rekonstruktion übernehmen die Pipeline von [Arcturus Vision](https://arcturus.vision) und Spirula Studio. Die Werkstatt ist die Oberfläche drumherum – sie bereitet alles vor, startet und überwacht die Pipeline und erledigt alles davor und danach.

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
- **Grundrisse** *(experimentell – bisher mit zwei Räumen getestet)* einzelner Szenen oder mehrerer zusammen – Aufnahmen aus einer Sitzung teilen das Tracking-Koordinatensystem der Frame, die Räume fügen sich also von selbst zum Grundriss der ganzen Wohnung. Zwei Stile: *Hübsch* (Draufsicht in Farbe, Wände, gelaufener Weg) und *Klassisch* (leuchtende Punktdichte), dazu ein einfaches Messwerkzeug (Punkte anklicken, Abstände ablesen – etwa ±5 cm genau).
- **Fertige Szenen**: später auf die Frame übertragen, erneut übertragen, Ordner oder Spirula Studio öffnen.
- Deutsche und englische Oberfläche (automatisch, umschaltbar).

## Voraussetzungen, Installation, Bedienung

Siehe die [englische README](README.md) – kurz:

```bash
git clone https://github.com/thunderheronight/splat-workshop.git
cd splat-workshop
./install.sh --desktop        # Menüeintrag + Desktop-Verknüpfung; --autostart für das Tray beim Anmelden
```

**Läuft unter Linux** (getestet mit Nobara/Fedora und einer RTX 4070 SUPER). macOS ist ungetestet, Windows wird von der Arcturus-Pipeline noch nicht unterstützt.

**Arcturus-Pipeline und Spirula Studio einrichten:** Schritt für Schritt in [docs/arcturus-setup.de.md](docs/arcturus-setup.de.md) (Pakete, Spirula bauen, bekannte Stolperfallen).

**SSH auf der Frame einrichten** (für Download der Aufnahmen und Kopieren in die Galerie):
1. Auf der Frame: **Steam-Einstellungen → System → Enable Developer Mode**.
2. Im linken Menü unter **Developer** ganz unten **Set User Password** wählen. Es gibt kein Standardpasswort – SSH geht erst, wenn du eins vergibst.
3. Am PC: `ssh-copy-id steamos@<adresse der frame>` (Passwort einmal eingeben), danach sollte `ssh steamos@<adresse>` ohne Passwort klappen.
4. Der Frame im Router eine feste IP geben und `steamos@<diese adresse>` in der Konfiguration unter `[frame] host` eintragen.

Danach in `~/.config/splat-workshop/config.toml` die Pfade zur Arcturus-Pipeline ([arcturus.vision/apps](https://arcturus.vision/apps)), zu Spirula Studio und die Adresse der Frame eintragen. Was noch fehlt, zeigt die Werkstatt oben auf der Seite an.

Nach dem Übertragen **Gaussian XR auf der Frame neu starten**, damit die Szene in der Galerie erscheint.

## Aufnahmetipps

- Viel Licht (Tageslicht plus Raumlicht), **HDR mit 30 fps** in der Kamera-App, nicht „Linear“.
- **Räume**: 30–90 s pro Raum, langsam gehen und langsam drehen, Dinge von mehreren Seiten zeigen.
- **Gegenstände**: langsam im Kreis darum herumgehen (15–40 s), Motiv in der Bildmitte halten.
- Nicht lange auf kahle Wände, Decke oder Fliesen halten.
- Bewegte Personen werden zu Geisterschleiern.

## Transparenzhinweis

Dieses Projekt ist mit Hilfe einer KI entstanden: Code, Dokumentation und die Platzhalter im Beispiel-Screenshot wurden gemeinsam mit **Claude (Opus 5.5) von Anthropic** in Claude Code erstellt. Ideen, Tests auf echter Hardware, Entscheidungen und Durchsicht kamen von mir. Fehlermeldungen sind willkommen – wenn etwas komisch aussieht, kann es gut ein Fehler sein, den keiner von uns bemerkt hat.

## Lizenz

Ein Community-Tool, **nicht mit Valve oder Arcturus Vision verbunden**. Lizenz: [GNU GPL v3.0](LICENSE).
