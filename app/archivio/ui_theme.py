"""Design system, stylesheet, icone vettoriali e bridge Cocoa macOS."""
from __future__ import annotations

import os
import sys
from typing import Dict, Optional

from PySide6.QtCore import QObject, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFontDatabase, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QListWidget, QTableView, QWidget

try:
    import shiboken6 as _shiboken6
except ImportError:
    _shiboken6 = None


def _qobj_valid(obj: Optional[QObject]) -> bool:
    """Restituisce True solo se l'oggetto C++ sottostante esiste ancora."""
    if obj is None:
        return False
    if _shiboken6 is not None:
        try:
            return bool(_shiboken6.isValid(obj))
        except Exception:
            return False
    return True
_UI_FONT_FAMILY_CACHE: Optional[str] = None


def _resolve_ui_font_family() -> str:
    global _UI_FONT_FAMILY_CACHE
    if _UI_FONT_FAMILY_CACHE:
        return _UI_FONT_FAMILY_CACHE
    try:
        installed = set(QFontDatabase.families())
        for fam in ("Avenir Next", "Segoe UI", "Helvetica Neue", "DejaVu Sans", "Noto Sans", "Arial"):
            if fam in installed:
                _UI_FONT_FAMILY_CACHE = fam
                return fam
    except Exception:
        pass
    return "Avenir Next"


# ==============================================================================
# DESIGN SYSTEM STUDIO MONOCROMATICO & EDITORIALE (ISPIRATO A MARTA SALOGNI)
# ==============================================================================
MACOS_STYLESHEET = """
QMainWindow, QDialog, QMessageBox {
    background-color: #F7F6F2;
    color: #141413;
    font-family: "Avenir Next", "Segoe UI", "Helvetica Neue", ".AppleSystemUIFont", "DejaVu Sans", "Noto Sans", "Arial";
    font-size: 13px;
}

QLabel, QCheckBox, QRadioButton, QGroupBox {
    color: #141413;
    background-color: transparent;
}

/* Sidebar sinistra in carta calda d'archivio */
QFrame#Sidebar {
    background-color: #EFECE6;
    border-right: 1px solid #DDDAD2;
}

QLabel#AppBrand {
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 2.8px;
    color: #141413;
    padding: 4px 4px 6px 4px;
}

QLabel#SectionTitle {
    font-size: 10px;
    font-weight: 600;
    color: #73716C;
    text-transform: uppercase;
    letter-spacing: 1.3px;
    padding: 8px 4px 2px 4px;
}

QPushButton.CatButton {
    text-align: left;
    padding: 8px 11px;
    border-radius: 5px;
    border: 1px solid transparent;
    background-color: transparent;
    color: #262523;
    font-size: 13px;
    font-weight: 500;
}
QPushButton.CatButton:hover {
    background-color: #E4E1D9;
    border-color: #DFDDD6;
}
QPushButton.CatButton[active="true"] {
    background-color: #141413;
    border: 1px solid #141413;
    color: #FFFFFF;
    font-weight: 600;
}

/* Schede / Superfici architettoniche */
QFrame.Card {
    background-color: #FFFFFF;
    border: 1px solid #DFDDD6;
    border-radius: 6px;
}

QFrame#SidebarCard {
    background-color: #F8F7F4;
    border: 1px solid #DFDDD6;
    border-radius: 6px;
}

/* Campi di testo e menu a tendina */
QLineEdit, QComboBox {
    background-color: #FFFFFF;
    border: 1px solid #D4D2CA;
    border-radius: 5px;
    padding: 5px 9px;
    color: #141413;
    font-size: 13px;
    min-height: 21px;
    selection-background-color: #262523;
    selection-color: #FFFFFF;
}
QLineEdit:focus, QComboBox:focus {
    border: 1.5px solid #141413;
    background-color: #FFFFFF;
}
QLineEdit#SortPreviewEdit {
    background-color: #F6F5F1;
    color: #4A4945;
    border-color: #DFDDD6;
}
QLineEdit#SearchInput {
    background-color: #FFFFFF;
    border: 1px solid #D4D2CA;
    border-radius: 5px;
    padding: 5px 10px;
}
QLineEdit#SearchInput:focus {
    border: 1.5px solid #141413;
}

QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 22px;
    border-left: 1px solid #E6E4DD;
    background: transparent;
}
QComboBox[hasChoices="false"]::drop-down {
    width: 0px;
    border: none;
}
QComboBox[hasChoices="true"] {
    padding-right: 26px;
}
QComboBox QLineEdit {
    background: transparent;
    border: none;
    border-radius: 0px;
    padding: 0px;
    margin: 0px;
    min-height: 0px;
}
QComboBox QLineEdit:focus {
    border: none;
    background: transparent;
}
QComboBox[focused="true"] {
    border: 1.5px solid #141413;
    background-color: #FFFFFF;
}
QComboBox QAbstractItemView {
    background-color: #FFFFFF;
    border: 1px solid #C8C6BE;
    selection-background-color: #141413;
    selection-color: #FFFFFF;
    color: #141413;
    outline: 0;
    padding: 4px;
}

/* Pulsanti */
QPushButton {
    background-color: #FFFFFF;
    border: 1px solid #D0CECA;
    border-radius: 5px;
    padding: 5px 10px;
    color: #141413;
    font-size: 12.5px;
    font-weight: 500;
    min-height: 21px;
}
QPushButton:hover {
    background-color: #F2F0EA;
    border-color: #A8A6A0;
}
QPushButton:pressed {
    background-color: #E5E2DA;
}
QPushButton:disabled {
    color: #A8A6A0;
    background-color: #F5F4F0;
    border-color: #E4E2DC;
}

QPushButton#PrimaryButton {
    background-color: #141413;
    border: 1px solid #141413;
    color: #FFFFFF;
    font-weight: 600;
    letter-spacing: 0.3px;
    padding: 6px 18px;
}
QPushButton#PrimaryButton:hover {
    background-color: #2C2B29;
    border-color: #2C2B29;
}
QPushButton#PrimaryButton:pressed {
    background-color: #000000;
}

QPushButton#ScanActionButton {
    background-color: #F7F6F2;
    border: 1px solid #141413;
    border-radius: 5px;
    color: #141413;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.2px;
    padding: 4px 12px;
    min-height: 20px;
}
QPushButton#ScanActionButton:hover {
    background-color: #EAE7DF;
    border-color: #000000;
}
QPushButton#ScanActionButton:pressed {
    background-color: #DDD9CF;
}

QPushButton#GhostButton {
    background-color: transparent;
    border: none;
    color: #6E6D68;
    font-size: 12px;
    font-weight: 500;
    padding: 4px 8px;
    min-height: 18px;
}
QPushButton#GhostButton:hover {
    color: #141413;
    background-color: #F2F0EA;
    border-radius: 4px;
}

QPushButton#DangerButton {
    color: #9E2A2B;
    border-color: #DFC7C7;
    background-color: #FFFFFF;
}
QPushButton#DangerButton:hover {
    background-color: #FAF0F0;
    border-color: #C99E9E;
}

/* Tabella principale */
QTableView {
    background-color: #FFFFFF;
    alternate-background-color: #FAF9F6;
    border: 1px solid #DFDDD6;
    border-radius: 6px;
    gridline-color: #EFECE6;
    selection-background-color: #E5E2DA;
    selection-color: #141413;
    outline: 0;
}
QTableView::item {
    padding: 5px 10px;
    border-bottom: 1px solid #F2F0EA;
}
QTableView::item:selected {
    background-color: #E5E2DA;
    color: #141413;
}

QHeaderView::section {
    background-color: #F6F5F1;
    color: #686661;
    font-size: 10.5px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.7px;
    padding: 8px 15px 8px 8px;
    border: none;
    border-bottom: 1px solid #DFDDD6;
    border-right: 1px solid #ECEAE3;
}
QHeaderView::section:hover {
    background-color: #ECEAE2;
    color: #141413;
}
QHeaderView::up-arrow, QHeaderView::down-arrow {
    width: 0px;
    height: 0px;
    border: none;
    image: none;
}

/* Etichette editoriali */
QLabel.FieldLabel {
    color: #686661;
    font-size: 10.5px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.7px;
}
QLabel#CardOverline {
    color: #5C5A55;
    font-size: 10.5px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 1.2px;
}
QLabel#EditionBadge {
    color: #3A3936;
    background-color: #F3F1EB;
    border: 1px solid #DFDDD6;
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 500;
}
QLabel#MutedInfo {
    color: #73716C;
    font-size: 11.5px;
}

/* Barra di stato inferiore in tono carta calda d'archivio */
QStatusBar {
    background-color: #EFECE6;
    color: #262523;
    border-top: 1px solid #DDDAD2;
    font-size: 12px;
    padding: 2px 10px;
}
QStatusBar::item {
    border: none;
}

/* Barre di scorrimento minimali */
QScrollBar:vertical {
    background: transparent;
    width: 8px;
    margin: 2px 1px 2px 1px;
    border: none;
}
QScrollBar::handle:vertical {
    background: #C8C6BE;
    min-height: 24px;
    border-radius: 3px;
}
QScrollBar::handle:vertical:hover {
    background: #8E8C84;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
    width: 0px;
    border: none;
    background: transparent;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}
QScrollBar:horizontal {
    background: transparent;
    height: 8px;
    margin: 1px 2px 1px 2px;
    border: none;
}
QScrollBar::handle:horizontal {
    background: #C8C6BE;
    min-width: 24px;
    border-radius: 3px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    height: 0px;
    width: 0px;
    border: none;
    background: transparent;
}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: transparent;
}
"""


def _make_studio_icon(kind: str, color: str = "#141413", size: int = 15) -> QIcon:
    """
    Disegna icone vettoriali geometriche minimaliste ad alta densità (Retina 2x),
    eliminando qualsiasi emoji a favore di un linguaggio grafico pulito e professionale.
    """
    dpr = 2.0
    px = int(size * dpr)
    pix = QPixmap(px, px)
    pix.setDevicePixelRatio(dpr)
    pix.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor(color), 1.35)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)

    s = float(size)
    cx = s / 2.0
    cy = s / 2.0

    if kind == "scan":
        # Mirino architettonico con linee codice a barre interne
        m = 2.0
        c = 3.2
        path = QPainterPath()
        # Angolo alto-sinistra
        path.moveTo(QPointF(m, m + c))
        path.lineTo(QPointF(m, m))
        path.lineTo(QPointF(m + c, m))
        # Angolo alto-destra
        path.moveTo(QPointF(s - m - c, m))
        path.lineTo(QPointF(s - m, m))
        path.lineTo(QPointF(s - m, m + c))
        # Angolo basso-sinistra
        path.moveTo(QPointF(m, s - m - c))
        path.lineTo(QPointF(m, s - m))
        path.lineTo(QPointF(m + c, s - m))
        # Angolo basso-destra
        path.moveTo(QPointF(s - m - c, s - m))
        path.lineTo(QPointF(s - m, s - m))
        path.lineTo(QPointF(s - m, s - m - c))
        painter.drawPath(path)
        # Tratti verticali codice
        painter.drawLine(QPointF(cx - 2.3, m + 2.6), QPointF(cx - 2.3, s - m - 2.6))
        painter.drawLine(QPointF(cx, m + 2.6), QPointF(cx, s - m - 2.6))
        painter.drawLine(QPointF(cx + 2.3, m + 2.6), QPointF(cx + 2.3, s - m - 2.6))

    elif kind == "book":
        # Libro aperto minimale
        path = QPainterPath()
        path.moveTo(QPointF(cx, 3.4))
        path.lineTo(QPointF(2.2, 2.5))
        path.lineTo(QPointF(2.2, s - 3.2))
        path.lineTo(QPointF(cx, s - 2.3))
        path.lineTo(QPointF(s - 2.2, s - 3.2))
        path.lineTo(QPointF(s - 2.2, 2.5))
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawLine(QPointF(cx, 3.4), QPointF(cx, s - 2.3))

    elif kind == "cd":
        # Disco ottico minimale
        r_out = (s - 3.8) / 2.0
        painter.drawEllipse(QPointF(cx, cy), r_out, r_out)
        painter.drawEllipse(QPointF(cx, cy), 1.6, 1.6)

    elif kind == "vinyl":
        # Disco in vinile con solco interno
        r_out = (s - 3.8) / 2.0
        painter.drawEllipse(QPointF(cx, cy), r_out, r_out)
        r_mid = r_out * 0.62
        painter.drawArc(
            QRectF(cx - r_mid, cy - r_mid, r_mid * 2.0, r_mid * 2.0),
            35 * 16,
            110 * 16,
        )
        painter.setBrush(QColor(color))
        painter.drawEllipse(QPointF(cx, cy), 1.15, 1.15)

    elif kind == "camera":
        # Fotocamera minimale da studio (corpo geometrico + obiettivo centrale)
        body = QRectF(1.8, 4.2, s - 3.6, s - 6.4)
        painter.drawRoundedRect(body, 2.0, 2.0)
        top_prism = QPainterPath()
        top_prism.moveTo(QPointF(cx - 3.0, 4.2))
        top_prism.lineTo(QPointF(cx - 1.9, 2.4))
        top_prism.lineTo(QPointF(cx + 1.9, 2.4))
        top_prism.lineTo(QPointF(cx + 3.0, 4.2))
        painter.drawPath(top_prism)
        painter.drawEllipse(QPointF(cx, cy + 1.0), 2.35, 2.35)

    elif kind == "sliders":
        # Cursori impostazioni da studio
        y1 = cy - 2.7
        y2 = cy + 2.7
        painter.drawLine(QPointF(2.3, y1), QPointF(s - 2.3, y1))
        painter.drawLine(QPointF(2.3, y2), QPointF(s - 2.3, y2))
        painter.setBrush(QColor("#EFECE6"))
        painter.drawEllipse(QPointF(cx - 2.2, y1), 1.7, 1.7)
        painter.drawEllipse(QPointF(cx + 2.2, y2), 1.7, 1.7)

    painter.end()
    return QIcon(pix)


def _disable_mac_focus_rect(root: QWidget) -> None:
    """Rimuove l'alone azzurro di sistema macOS attorno ai campi per mantenere il focus nero editoriale."""
    for cls in (QLineEdit, QComboBox, QListWidget, QTableView):
        for w in root.findChildren(cls):
            w.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)


# Costanti Cocoa NSWindowCollectionBehavior per gestire overlay <-> schermo intero su macOS
_MAC_CB_FULLSCREEN_PRIMARY_ACTIVE = 0x82     # FullScreenPrimary (0x80) | MoveToActiveSpace (0x02)
_MAC_CB_FULLSCREEN_AUX_ACTIVE = 0x102        # FullScreenAuxiliary (0x100) | MoveToActiveSpace (0x02)
_MAC_NSWINDOW_STYLE_FULLSCREEN = 1 << 14     # NSWindowStyleMaskFullScreen (0x4000)
_MAC_ZOOM_HANDLERS: Dict[int, object] = {}
_MAC_FS_HANDLERS: Dict[int, tuple] = {}
_MAC_OBJC_BRIDGE: Optional[dict] = None


def _get_mac_objc_bridge() -> Optional[dict]:
    """Inizializza una sola volta i binding libobjc per controllare NSWindow overlay/fullscreen su macOS."""
    global _MAC_OBJC_BRIDGE
    if sys.platform != "darwin":
        return None
    if _MAC_OBJC_BRIDGE is not None:
        return _MAC_OBJC_BRIDGE
    try:
        import ctypes

        objc = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.objc_allocateClassPair.restype = ctypes.c_void_p
        objc.objc_allocateClassPair.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
        objc.objc_registerClassPair.argtypes = [ctypes.c_void_p]
        objc.class_addMethod.restype = ctypes.c_bool
        objc.class_addMethod.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]

        imp_type = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)

        def _on_zoom_action(self_id, _cmd, _sender):
            try:
                cb = _MAC_ZOOM_HANDLERS.get(int(self_id))
                if callable(cb):
                    cb()
            except Exception:
                pass

        def _on_will_exit_fs(self_id, _cmd, _notif):
            try:
                pair = _MAC_FS_HANDLERS.get(int(self_id))
                if pair and callable(pair[0]):
                    pair[0]()
            except Exception:
                pass

        def _on_did_exit_fs(self_id, _cmd, _notif):
            try:
                pair = _MAC_FS_HANDLERS.get(int(self_id))
                if pair and callable(pair[1]):
                    pair[1]()
            except Exception:
                pass

        imp_ref = imp_type(_on_zoom_action)
        imp_will_exit = imp_type(_on_will_exit_fs)
        imp_did_exit = imp_type(_on_did_exit_fs)
        sel_on_zoom = objc.sel_registerName(b"onGreenZoom:")
        sel_on_will_exit = objc.sel_registerName(b"onWillExitFS:")
        sel_on_did_exit = objc.sel_registerName(b"onDidExitFS:")

        cls_name = b"ArchivioMacZoomHandler"
        cls = objc.objc_getClass(cls_name)
        if not cls:
            ns_obj = objc.objc_getClass(b"NSObject")
            cls = objc.objc_allocateClassPair(ns_obj, cls_name, 0)
            objc.class_addMethod(cls, sel_on_zoom, imp_ref, b"v@:@")
            objc.class_addMethod(cls, sel_on_will_exit, imp_will_exit, b"v@:@")
            objc.class_addMethod(cls, sel_on_did_exit, imp_did_exit, b"v@:@")
            objc.objc_registerClassPair(cls)

        _MAC_OBJC_BRIDGE = {
            "objc": objc,
            "cls": cls,
            "imp_ref": imp_ref,
            "imp_will_exit": imp_will_exit,
            "imp_did_exit": imp_did_exit,
            "ns_notif_center_cls": objc.objc_getClass(b"NSNotificationCenter"),
            "ns_string_cls": objc.objc_getClass(b"NSString"),
            "msg_ptr": ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc)),
            "msg_ptr_cstr": ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p)(("objc_msgSend", objc)),
            "msg_ptr_ul": ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)(("objc_msgSend", objc)),
            "msg_ul": ctypes.CFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc)),
            "msg_void_ul": ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)(("objc_msgSend", objc)),
            "msg_void_ptr": ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc)),
            "msg_void_ptr_l": ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long)(("objc_msgSend", objc)),
            "msg_void_4ptr": ctypes.CFUNCTYPE(
                None,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
            )(("objc_msgSend", objc)),
            "sel_window": objc.sel_registerName(b"window"),
            "sel_set_cb": objc.sel_registerName(b"setCollectionBehavior:"),
            "sel_get_cb": objc.sel_registerName(b"collectionBehavior"),
            "sel_style_mask": objc.sel_registerName(b"styleMask"),
            "sel_std_btn": objc.sel_registerName(b"standardWindowButton:"),
            "sel_set_target": objc.sel_registerName(b"setTarget:"),
            "sel_set_action": objc.sel_registerName(b"setAction:"),
            "sel_on_zoom": sel_on_zoom,
            "sel_on_will_exit_fs": sel_on_will_exit,
            "sel_on_did_exit_fs": sel_on_did_exit,
            "sel_default_center": objc.sel_registerName(b"defaultCenter"),
            "sel_add_observer": objc.sel_registerName(b"addObserver:selector:name:object:"),
            "sel_remove_observer": objc.sel_registerName(b"removeObserver:"),
            "sel_str_utf8": objc.sel_registerName(b"stringWithUTF8String:"),
            "sel_add_child": objc.sel_registerName(b"addChildWindow:ordered:"),
            "sel_rem_child": objc.sel_registerName(b"removeChildWindow:"),
            "sel_alloc": objc.sel_registerName(b"alloc"),
            "sel_init": objc.sel_registerName(b"init"),
            "sel_release": objc.sel_registerName(b"release"),
        }
    except Exception:
        _MAC_OBJC_BRIDGE = None
    return _MAC_OBJC_BRIDGE


def _mac_nswindow(widget: Optional[QWidget], b: dict) -> int:
    if widget is None:
        return 0
    app = QApplication.instance()
    if app is not None and app.platformName() != "cocoa":
        return 0
    try:
        nsview = int(widget.winId())
        if not nsview:
            return 0
        return int(b["msg_ptr"](nsview, b["sel_window"]) or 0)
    except Exception:
        return 0
def _resolve_app_icon_path() -> str:
    """Individua il percorso dell'icona dell'applicazione (macOS, Windows, Linux) sia da sorgente che nel bundle."""
    if sys.platform == "darwin":
        icon_names = ("archivio.icns", "archivio.png")
    elif sys.platform == "win32":
        icon_names = ("archivio.ico", "archivio.png")
    else:
        icon_names = ("archivio.png", "archivio.ico")

    candidates = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        meipass = getattr(sys, "_MEIPASS")
        for fn in icon_names:
            candidates.append(os.path.join(meipass, "assets", fn))
            candidates.append(os.path.join(os.path.dirname(meipass), "Resources", fn))
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    for fn in icon_names:
        candidates.append(os.path.join(repo_root, "assets", fn))
    for p in candidates:
        if os.path.isfile(p):
            return p
    return ""


def _apply_light_palette(app: QApplication) -> None:
    """
    Forza una palette chiara coerente con il design monocromatico da studio dell'applicazione,
    evitando che il Dark Mode di sistema alteri i colori di etichette, spunte o indicatori.
    """
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor("#F7F6F2"))
    pal.setColor(QPalette.ColorRole.WindowText, QColor("#141413"))
    pal.setColor(QPalette.ColorRole.Base, QColor("#FFFFFF"))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor("#FAF9F6"))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor("#FFFFFF"))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor("#141413"))
    pal.setColor(QPalette.ColorRole.Text, QColor("#141413"))
    pal.setColor(QPalette.ColorRole.Button, QColor("#FFFFFF"))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor("#141413"))
    pal.setColor(QPalette.ColorRole.BrightText, QColor("#FFFFFF"))
    pal.setColor(QPalette.ColorRole.Highlight, QColor("#141413"))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor("#8E8C84"))
    app.setPalette(pal)
    app.setStyleSheet(MACOS_STYLESHEET)
