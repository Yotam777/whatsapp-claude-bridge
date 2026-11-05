"""WhatsAppDash — desktop app of the WhatsApp ↔ Claude tool (built into WhatsAppDash.exe with PyInstaller).

Opens the dashboard in its own window (Edge WebView2), with a tray icon showing the connection state.
Works offline and without the agent: the dashboard reads the tool's local databases directly.
"""
import os
import sys
import threading
import time

if getattr(sys, "frozen", False):
    sys.path.insert(0, sys._MEIPASS)
else:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webview  # noqa: E402

from dash import server  # noqa: E402


def tray(window):
    try:
        import pystray
        from PIL import Image, ImageDraw
    except Exception:
        return

    def icon_img(color):
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((4, 4, 60, 60), fill=(31, 170, 89))
        d.ellipse((40, 40, 62, 62), fill=color, outline=(255, 255, 255), width=3)
        return img

    def show(icon=None, item=None):
        window.show()
        window.restore()

    def quit_app(icon, item):
        icon.stop()
        window.destroy()

    icon = pystray.Icon("WhatsAppDash", icon_img((150, 150, 150)), "ווצאפ ↔ Claude",
                        menu=pystray.Menu(pystray.MenuItem("פתח לוח בקרה", show, default=True), pystray.MenuItem("יציאה", quit_app)))
    icon.run_detached()

    def refresh():
        while True:
            st = server.bridge("/status")
            ok = not st.get("error") and st.get("connected")
            icon.icon = icon_img((69, 196, 122) if ok else (255, 107, 94))
            icon.title = "ווצאפ ↔ Claude — " + ("מחובר" if ok else "מנותק")
            time.sleep(30)

    threading.Thread(target=refresh, daemon=True).start()


def main():
    port = server.start_free_port()
    window = webview.create_window(f"ווצאפ ↔ Claude · לוח בקרה {server.VERSION}", f"http://127.0.0.1:{port}/",
                                   width=1360, height=900, min_size=(760, 600), text_select=True)
    webview.start(tray, window, gui="edgechromium", private_mode=False,
                  storage_path=os.path.join(os.environ.get("LOCALAPPDATA", "."), "WhatsAppDash", "webview"))


if __name__ == "__main__":
    main()
