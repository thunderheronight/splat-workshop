# Splat Workshop (Splat-Werkstatt)

**Eine grafische Oberfläche für die Rekonstruktions-Pipeline von Arcturus Vision: Aufnahme der Arcturus Vision Camera an der Steam Frame auswählen, prüfen, umwandeln lassen und das Ergebnis direkt in die Gaussian-XR-Galerie auf der Brille bringen. Mit Weboberfläche, Tray-Symbol und Aufnahmeprüfung.**

![Kein Root nötig](https://img.shields.io/badge/root-not%20needed-2ea44f) ![Kein Jailbreak](https://img.shields.io/badge/jailbreak-not%20needed-2ea44f) [![Release](https://img.shields.io/github/v/release/thunderheronight/splat-workshop)](https://github.com/thunderheronight/splat-workshop/releases)

> **Kein Root, kein Jailbreak.** Auf der Frame nutzt die Splat-Werkstatt nur den offiziellen *Entwicklermodus* aus den Steam-Einstellungen und SSH als normaler Benutzer – auf der Brille wird nichts installiert, ihr System bleibt unangetastet. Auch am PC läuft sie als dein Benutzer (nur die Voraussetzungen wie ffmpeg kommen aus der Paketverwaltung).

[English](README.md)

![Splat-Werkstatt (Beispiel-Screenshot, Vorschaubilder durch Platzhalter ersetzt)](docs/screenshot.png)

Räume, eine Statue, dein Auto, ein Garten – alles, um das du mit der Kamera herumgehen kannst, wird zu einer Szene, in die du hineinsteigen kannst.

Die Splat-Werkstatt rechnet die Splats nicht selbst: Die Rekonstruktion übernehmen die Pipeline von [Arcturus Vision](https://arcturus.vision) und Spirula Studio. Die Werkstatt ist die Oberfläche drumherum – sie bereitet alles vor, startet und überwacht die Pipeline und erledigt alles davor und danach.

Arcturus Vision stellt die Rekonstruktions-Skripte und einen Spirula-Studio-Fork bereit. Bis eine Aufnahme als Szene in [Gaussian XR](https://store.steampowered.com/app/3977540/Arcturus_Vision_Gaussian_XR/) auftaucht, sind aber viele Handgriffe nötig. Die Splat-Werkstatt packt den ganzen Weg in eine lokale Weboberfläche:

1. **Aufnahme wählen** – direkt von der Frame (per SSH) oder vom PC, mit Vorschaubildern.
2. **Prüfen**, bevor eine Stunde Rechenzeit draufgeht: Helligkeit, Struktur, schnelle Schwenks, verdeckte Linse – Sekunde für Sekunde.
3. **Umwandeln** mit Qualitätsstufe (Schnell / Hoch / Maximal) oder eigenen Werten, mit Fortschritt, Restzeit und PSNR-Kurve.
4. **In die Galerie**: Vorschaubild und Namensschild im Stil der Beispielszenen, kopiert in den `Scenes`-Ordner von Gaussian XR auf der Frame.
5. **Die Wohnung zusammensetzen**: Räume gruppieren und zu einem Rundgang machen, durch den du gehst – voll für den PC, kleiner für die Frame.

## Neu in 0.2

- 🏠 **Die ganze Wohnung als eine Szene** – Räume gruppieren und *Rundgang zusammensetzen*: Die Räume eines Aufnahmedurchgangs werden aneinander ausgerichtet und zu einer begehbaren Szene verbunden, als volle Fassung für den PC und als kleinere für die Frame.
- 🎯 **Saubere Übergänge** – vier Korrekturen zur Wahl; die Gruppe zeigt, wie gut die Räume aneinanderpassen (meist auf 1–2 cm), so lässt sich vergleichen.
- 📁 **Gruppen** halten die Liste übersichtlich – nacheinander aufgenommene Räume werden automatisch vorgeschlagen.
- 🧹 **Speicherplatz, Aufräumen, Löschen** pro Szene und eine Liste **abgebrochener Läufe** zum Fortsetzen oder Entfernen.
- 🛡️ **Sicherer**: *Neu rechnen* behält die alte Szene, bis die neue fertig ist; der PC geht während einer Umwandlung nicht mehr in den Standby.

<img src="docs/groups.png" width="800" alt="Eine Gruppe „Example home“ mit sechs Räumen: der daraus zusammengesetzte Rundgang, sein Grundriss als Vorschau, wie gut die Räume aneinanderpassen, darunter die Räume">
<br><sub>Beispieldaten (englische Oberfläche) – Raumnamen und Vorschaubilder sind Platzhalter, die Rundgang-Vorschau ist die synthetische Beispielwohnung.</sub>

Alle Änderungen: [CHANGELOG.md](CHANGELOG.md) (englisch)

## Funktionen

- **Weboberfläche** (nur Python-Standardbibliothek) – auf Wunsch auch vom Handy oder einem zweiten Rechner im Heimnetz.
- **Tray-Symbol** – zeigt, ob die Werkstatt läuft und was sie tut (Fortschrittsring). Öffnen, Umwandlung abbrechen, Beenden. Beenden während einer Umwandlung fragt nach: im Hintergrund fertig rechnen lassen oder abbrechen.
- **Umwandlungen laufen unabhängig weiter** – Browser zu, Tray beendet, Neustart: Werkstatt wieder öffnen, Fortschritt ist wieder da.
- **Abbrechen & Fortsetzen** ab dem letzten Zwischenstand.
- **Aufnahmeprüfung ohne KI**: reine Messwerte je Sekunde, farbige Zeitleiste und konkrete Hinweise („0:45–0:59 wenig Struktur“).
- **Gruppen und Rundgänge**: Szenen gruppieren (z. B. alle Räume der Wohnung) und zu einer Szene zusammensetzen – eine volle Fassung für Gaussian XR am PC und eine kleinere für die Frame. Aufnahmen aus einem Durchgang teilen das Tracking-Koordinatensystem der Frame; die Werkstatt richtet die Räume aneinander aus (Korrekturen A–D zur Wahl), legt die Grenzen zwischen den Aufnahmen in die Türen und zeigt, wie gut die Räume aneinanderpassen. Vor dem Start schätzt sie den Speicherbedarf.
  <br><img src="docs/combine.png" width="360" alt="Dialog „Zusammensetzen“: Korrekturen A bis D, Grenzen zwischen den Aufnahmen, Größe der Frame-Fassung, volle Fassung, Speicherschätzung">
  <br><sub>Beispieldaten (englische Oberfläche) – Raumnamen und Vorschaubilder sind Platzhalter.</sub>
- **Grundrisse** *(experimentell)* einzelner Szenen oder einer ganzen Gruppe – Aufnahmen aus einem Durchgang fügen sich von selbst zum Grundriss der ganzen Wohnung. Zwei Stile: *Hübsch* (Draufsicht in Farbe, Wände, gelaufener Weg) und *Klassisch* (leuchtende Punktdichte), dazu ein einfaches Messwerkzeug (Punkte anklicken, Abstände ablesen – etwa ±5 cm genau).
- **Fertige Szenen**: später auf die Frame übertragen, erneut übertragen, umbenennen (Ordner und Namensschild ziehen mit), löschen (in den Papierkorb), Ordner oder Spirula Studio öffnen.
- **Abgebrochene Läufe** werden mit ihrem Stand angezeigt: fortsetzen, neu starten oder löschen.
- **Speicherplatz und Aufräumen**: Jede Szene zeigt, wie viel Platz sie belegt (etwa 4 GB pro Raum). Ein Klick löscht Zwischenstände oder Trainingsbilder – Szene, Grundriss und Viewer funktionieren weiter.
- **Aufnahmen umbenennen** auf dem PC; Aufnahmedatum und Prüfergebnisse bleiben erhalten.
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
