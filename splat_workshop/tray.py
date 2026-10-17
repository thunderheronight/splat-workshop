"""Tray icon for Splat Workshop (PySide6).

Starts the web server (or attaches to one that is already running), shows at a glance whether
the program is on and what it is doing, and offers: open, cancel the conversion, quit.
Quitting asks first when something is running – a conversion can keep running in the background
or be cancelled (it can be resumed later).
"""
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QGuiApplication, QIcon, QPainter, QPen, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import config
from .i18n import T, detect

CFG = config.load()
t = T(detect(CFG.language))
BASE = f"http://127.0.0.1:{CFG.port}"
INSTANCE = f"splat-workshop-tray-{CFG.port}"

COLORS = {"blue": "#7c9cff", "green": "#46d3b5", "orange": "#ffb86b", "grey": "#6b7283", "red": "#ff6b81"}


def request(path: str, body: dict | None = None, timeout=3):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def server_alive() -> bool:
    try:
        return request("/api/ping", timeout=1.5).get("app") == "splat-workshop"
    except (OSError, ValueError, urllib.error.URLError):
        return False


def make_icon(state: str, pct: int | None = None) -> QIcon:
    """The logo (three splats). Busy: progress ring around it. Off/error: grey."""
    size = 64
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    grey = state in ("down",)
    blobs = [((22, 26), 13, "blue", .9), ((38, 38), 15, "green", .85), ((40, 19), 8, "orange", .95)]
    scale = .78 if state == "busy" else 1.0
    off = (size - size * scale) / 2
    for (x, y), r, c, a in blobs:
        col = QColor(COLORS["grey" if grey else c])
        col.setAlphaF(a)
        p.setBrush(col)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(off + (x - r) * scale, off + (y - r) * scale, 2 * r * scale, 2 * r * scale))
    if state == "busy":
        rect = QRectF(3, 3, size - 6, size - 6)
        p.setPen(QPen(QColor(255, 255, 255, 60), 5))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(rect)
        pen = QPen(QColor(COLORS["green"]), 5)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        # Qt angles are in 1/16 degree. Known progress: arc from 12 o'clock; unknown: a quarter arc that moves on
        span = int(360 * 16 * pct / 100) if pct is not None else 90 * 16
        start = 90 * 16 if pct is not None else int(time.time() * 4 % 16) * 360
        p.drawArc(rect, start, span if pct is None else -span)
    if state == "down":
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(COLORS["red"]))
        p.drawEllipse(QRectF(40, 40, 22, 22))
    p.end()
    return QIcon(pm)


class Tray:
    def __init__(self, app: QApplication, open_browser: bool):
        self.app = app
        self.proc = None                # server process if we started it
        self.act = None                 # last /api/activity
        self.fails = 0
        self.tray = QSystemTrayIcon(make_icon("idle"))
        self.tray.setToolTip(t("app"))
        self.tray.activated.connect(self.on_activated)

        self.menu = QMenu()
        self.a_open = QAction(t("tray.open"), self.menu, triggered=self.open)
        self.a_status = QAction("", self.menu, enabled=False)
        self.a_lan = QAction("", self.menu, triggered=self.copy_lan, visible=False)
        self.a_cancel = QAction(t("tray.cancel"), self.menu, triggered=self.cancel, enabled=False)
        self.a_update = QAction(t("tray.update"), self.menu, triggered=self.check_update)
        self.update_result = None                      # from the background check, shown by refresh()
        self.update_url = None
        self.a_quit = QAction(t("tray.quit"), self.menu, triggered=self.quit)
        f = self.a_open.font()
        f.setBold(True)
        self.a_open.setFont(f)
        for a in (self.a_open, None, self.a_status, self.a_lan, None, self.a_cancel, None, self.a_update, self.a_quit):
            self.menu.addSeparator() if a is None else self.menu.addAction(a)
        self.tray.setContextMenu(self.menu)
        self.tray.messageClicked.connect(self.open_update)
        self.lan_url = None

        self.ensure_server()
        self.tray.show()
        if open_browser:
            QTimer.singleShot(400, self.open)
        self.timer = QTimer(interval=2000, timeout=self.refresh)
        self.timer.start()
        self.refresh()

    # ------------------------------------------------------------ server
    def ensure_server(self):
        if server_alive():
            return
        self.proc = subprocess.Popen([sys.executable, "-m", "splat_workshop.server", "--no-idle-exit"],
                                     cwd=config.ROOT, start_new_session=True, stdin=subprocess.DEVNULL)
        for _ in range(50):
            if server_alive():
                break
            time.sleep(0.1)
        try:
            self.lan_url = request("/api/ping").get("lan_url")
        except (OSError, ValueError):
            pass

    def refresh(self):
        if self.update_result is not None:
            self.show_update()
        try:
            self.act = request("/api/activity")
            self.fails = 0
        except (OSError, ValueError):
            self.act = None
            self.fails += 1
            if self.fails >= 3 and (self.proc is None or self.proc.poll() is not None):
                self.fails = 0
                self.ensure_server()            # server died: start it again
        self.render()

    def render(self):
        a = self.act
        if a is None:
            self.tray.setIcon(make_icon("down"))
            text = t("tray.server_down")
        elif a.get("job"):
            j = a["job"]
            phase = t(f"phase.{j['phase']}")
            text = (t("tray.running_pct", name=j["name"], phase=phase, pct=j["pct"]) if j.get("pct") is not None
                    else t("tray.running", name=j["name"], phase=phase))
            if j.get("eta") is not None:
                text += t("tray.eta", mins=round(j["eta"] / 60) + 1)
            self.tray.setIcon(make_icon("busy", j.get("pct")))
        elif a.get("download"):
            text = t("tray.downloading", pct=a["download"]["pct"])
            self.tray.setIcon(make_icon("busy", a["download"]["pct"]))
        elif a.get("uploading"):
            text = t("tray.uploading")
            self.tray.setIcon(make_icon("busy"))
        elif a.get("checking"):
            text = t("tray.checking")
            self.tray.setIcon(make_icon("busy"))
        else:
            text = t("tray.idle")
            self.tray.setIcon(make_icon("idle"))
        self.a_status.setText(text)
        self.tray.setToolTip(f"{t('app')}\n{text}")
        self.a_cancel.setEnabled(bool(a and a.get("job")))
        if self.lan_url:
            self.a_lan.setText(t("tray.lan", url=self.lan_url.removeprefix("http://").rstrip("/")))
            self.a_lan.setVisible(True)

    # ------------------------------------------------------------ actions
    def on_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.open()

    def open(self):
        webbrowser.open(BASE + "/")

    def check_update(self):
        """Ask GitHub now (in the background; the answer comes as a notification)."""
        self.a_update.setEnabled(False)

        def run():
            try:
                self.update_result = request("/api/update/check", {}, timeout=20)
            except (OSError, ValueError):
                self.update_result = {"reached": False}
        threading.Thread(target=run, daemon=True).start()

    def show_update(self):
        r, self.update_result = self.update_result, None
        self.a_update.setEnabled(True)
        u = r.get("update")
        if u:
            self.update_url = u.get("url")
            self.tray.showMessage(t("app"), t("tray.update.new", latest=u["latest"], current=u["current"]),
                                  QSystemTrayIcon.Information, 10000)
        else:
            self.update_url = None
            self.tray.showMessage(t("app"), t("tray.update.none", current=r.get("current", "")) if r.get("reached")
                                  else t("tray.update.offline"), QSystemTrayIcon.Information, 5000)

    def open_update(self):
        if self.update_url:
            webbrowser.open(self.update_url)

    def copy_lan(self):
        QGuiApplication.clipboard().setText(self.lan_url or "")
        self.tray.showMessage(t("app"), t("tray.copied"), QSystemTrayIcon.Information, 2000)

    def _box(self, title, text, info):
        box = QMessageBox()
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(title)
        box.setText(text)
        box.setInformativeText(info)
        box.setWindowIcon(make_icon("idle"))
        return box

    def cancel(self):
        j = (self.act or {}).get("job")
        if not j:
            return
        box = self._box(t("dlg.cancel.title"), t("dlg.cancel.text", name=j["name"], phase=t(f"phase.{j['phase']}")),
                        t("dlg.cancel.info"))
        yes = box.addButton(t("dlg.cancel.yes"), QMessageBox.DestructiveRole)
        back = box.addButton(t("dlg.back"), QMessageBox.RejectRole)
        box.setDefaultButton(back)
        box.setEscapeButton(back)
        box.exec()
        if box.clickedButton() is yes:
            self._post("/api/cancel", {})
            self.refresh()

    def quit(self):
        self.refresh()
        a = self.act or {}
        j = a.get("job")
        cancel_job = False
        if j:
            box = self._box(t("dlg.quit.title"), t("dlg.quit.text", name=j["name"], phase=t(f"phase.{j['phase']}")),
                            t("dlg.quit.info"))
            keep = box.addButton(t("dlg.quit.keep"), QMessageBox.AcceptRole)
            kill = box.addButton(t("dlg.quit.kill"), QMessageBox.DestructiveRole)
            back = box.addButton(t("dlg.back"), QMessageBox.RejectRole)
            box.setDefaultButton(back)
            box.setEscapeButton(back)
            box.exec()
            if box.clickedButton() not in (keep, kill):
                return
            cancel_job = box.clickedButton() is kill
        else:
            other = [t(k) for k, on in (("what.download", a.get("download")), ("what.check", a.get("checking")),
                                       ("what.upload", a.get("uploading"))) if on]
            if other:
                box = self._box(t("dlg.quit.title"), t("dlg.quit.other", what=", ".join(other)), "")
                yes = box.addButton(t("dlg.quit.other_yes"), QMessageBox.DestructiveRole)
                back = box.addButton(t("dlg.back"), QMessageBox.RejectRole)
                box.setDefaultButton(back)
                box.setEscapeButton(back)
                box.exec()
                if box.clickedButton() is not yes:
                    return
        self.timer.stop()
        self._post("/api/shutdown", {"cancel_job": cancel_job})
        if self.proc is not None:
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.tray.hide()
        self.app.quit()

    def _post(self, path, body):
        try:
            return request(path, body)
        except (OSError, ValueError):
            return None


def main():
    open_browser = "--background" not in sys.argv
    app = QApplication(sys.argv)
    app.setApplicationName("splat-workshop")
    app.setQuitOnLastWindowClosed(False)

    # Only one tray: a second start just opens the page
    sock = QLocalSocket()
    sock.connectToServer(INSTANCE)
    if sock.waitForConnected(300):
        sock.write(b"open")
        sock.flush()
        sock.waitForBytesWritten(300)
        return 0
    QLocalServer.removeServer(INSTANCE)
    single = QLocalServer()
    single.listen(INSTANCE)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("No system tray available – starting without tray icon.", file=sys.stderr)
        return 2

    tray = Tray(app, open_browser)

    def on_conn():
        c = single.nextPendingConnection()
        c.waitForReadyRead(300)
        tray.open()
        c.close()
    single.newConnection.connect(on_conn)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
