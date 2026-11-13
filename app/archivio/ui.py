"""
Interfaccia grafica minimalista, nativa e ad alte prestazioni basata su Qt6 (PySide6-Essentials, LGPLv3).
"""
from __future__ import annotations

import os
import sys
from dataclasses import replace
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import (
    QAbstractTableModel,
    QCoreApplication,
    QEvent,
    QModelIndex,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QRunnable,
    QSize,
    QSortFilterProxyModel,
    QThreadPool,
    QTimer,
    Qt,
    Signal,
)
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
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QFont,
    QFontDatabase,
    QIcon,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QShortcut,
    QUndoCommand,
    QUndoStack,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStatusBar,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME, APP_VERSION
from .catalog import CatalogService, EditionBundle, Suggestion
from .config import CATEGORIES, CATEGORY_PREFIXES, DEFAULT_FILES, Settings, cache_path, config_path, reset_config_file
from .i18n import tr
from .pdf_export import export_category_pdf
from .remote_scanner import (
    MacCameraSession,
    RemoteScannerService,
    generate_qr_png,
    list_mac_cameras,
    run_apple_vision_on_image,
)
from .store import Item, load_csv, save_csv, scan_folder, sort_items, unique_collections
from .textutil import fold, search_fold, sort_key_matches_author, surname_key


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
_MAC_ZOOM_HANDLERS: Dict[int, object] = {}
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
            cb = _MAC_ZOOM_HANDLERS.get(int(self_id))
            if callable(cb):
                cb()

        imp_ref = imp_type(_on_zoom_action)
        cls_name = b"ArchivioMacZoomHandler"
        cls = objc.objc_getClass(cls_name)
        if not cls:
            ns_obj = objc.objc_getClass(b"NSObject")
            cls = objc.objc_allocateClassPair(ns_obj, cls_name, 0)
            objc.class_addMethod(cls, objc.sel_registerName(b"onGreenZoom:"), imp_ref, b"v@:@")
            objc.objc_registerClassPair(cls)

        _MAC_OBJC_BRIDGE = {
            "objc": objc,
            "cls": cls,
            "imp_ref": imp_ref,
            "msg_ptr": ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc)),
            "msg_ptr_ul": ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)(("objc_msgSend", objc)),
            "msg_ul": ctypes.CFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc)),
            "msg_void_ul": ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)(("objc_msgSend", objc)),
            "msg_void_ptr": ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc)),
            "msg_void_ptr_l": ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long)(("objc_msgSend", objc)),
            "sel_window": objc.sel_registerName(b"window"),
            "sel_set_cb": objc.sel_registerName(b"setCollectionBehavior:"),
            "sel_get_cb": objc.sel_registerName(b"collectionBehavior"),
            "sel_std_btn": objc.sel_registerName(b"standardWindowButton:"),
            "sel_set_target": objc.sel_registerName(b"setTarget:"),
            "sel_set_action": objc.sel_registerName(b"setAction:"),
            "sel_on_zoom": objc.sel_registerName(b"onGreenZoom:"),
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


class StudioCheckBox(QCheckBox):
    """Casella di spunta vettoriale minimalista in stile editoriale monocromatico (#141413)."""

    def __init__(self, text: str = "", parent: Optional[QWidget] = None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            "QCheckBox { spacing: 8px; color: #262523; font-size: 12.5px; font-weight: 500; padding-right: 6px; } "
            "QCheckBox::indicator { width: 16px; height: 16px; background: transparent; border: none; }"
        )

    def sizeHint(self) -> QSize:  # noqa: N802
        sh = super().sizeHint()
        txt_w = self.fontMetrics().horizontalAdvance(self.text()) + 34
        return QSize(max(sh.width() + 18, txt_w), max(sh.height(), 22))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        sh = super().minimumSizeHint()
        txt_w = self.fontMetrics().horizontalAdvance(self.text()) + 34
        return QSize(max(sh.width() + 18, txt_w), max(sh.height(), 22))

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        cy = float(self.height()) / 2.0
        box = QRectF(1.0, cy - 7.5, 15.0, 15.0)
        if self.isChecked():
            painter.setPen(QPen(QColor("#141413"), 1.2))
            painter.setBrush(QColor("#141413"))
            painter.drawRoundedRect(box, 3.0, 3.0)
            chk_pen = QPen(QColor("#FFFFFF"), 1.75)
            chk_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            chk_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(chk_pen)
            path = QPainterPath()
            path.moveTo(QPointF(box.left() + 3.6, box.center().y() + 0.3))
            path.lineTo(QPointF(box.left() + 6.4, box.bottom() - 3.8))
            path.lineTo(QPointF(box.right() - 3.3, box.top() + 4.0))
            painter.drawPath(path)
        else:
            painter.setPen(QPen(QColor("#ABA8A0"), 1.3))
            painter.setBrush(QColor("#FFFFFF"))
            painter.drawRoundedRect(box, 3.0, 3.0)
        painter.end()


# ==============================================================================
# WIDGET PERSONALIZZATI (COMBOBOX SENZA SELEZIONE RESIDUA + POPUP AUTOCOMPLETE)
# ==============================================================================
class CleanComboBox(QComboBox):
    """
    QComboBox (editabile o sola lettura) che deseleziona sempre il testo
    dopo il popolamento, la selezione di una voce o la perdita del focus,
    eliminando il difetto visivo dei campi che restavano evidenziati in blu.
    """

    def __init__(self, editable: bool = True, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._updating_choices = False
        self._repolishing = False
        self.setEditable(editable)
        # Disabilita il QCompleter implicito di Qt (QCompletionModel): gestiamo già tendina ed
        # autocompletamento espliciti, evitando reset asincroni del proxy interno su clear()/addItems().
        if editable:
            self.setCompleter(None)
        self.setProperty("hasChoices", "false")
        self.setProperty("focused", "false")
        if editable:
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            self.setMinimumContentsLength(6)
        if editable and self.lineEdit():
            self.lineEdit().setClearButtonEnabled(False)
            self.lineEdit().installEventFilter(self)
        self._deselect_timer = QTimer(self)
        self._deselect_timer.setSingleShot(True)
        self._deselect_timer.setInterval(0)
        self._deselect_timer.timeout.connect(self._deselect_line_edit_now)
        self.currentIndexChanged.connect(self._deselect_text_soon)
        mdl = self.model()
        if mdl is not None:
            mdl.rowsInserted.connect(self._sync_has_choices)
            mdl.rowsRemoved.connect(self._sync_has_choices)
            mdl.modelReset.connect(self._sync_has_choices)

    def _repolish(self) -> None:
        if self._repolishing or not _qobj_valid(self):
            return
        self._repolishing = True
        try:
            st = self.style()
            if st is not None:
                st.unpolish(self)
                st.polish(self)
            self.update()
        finally:
            self._repolishing = False

    def _sync_has_choices(self, *_args) -> None:
        if self._updating_choices or not _qobj_valid(self):
            return
        val = "true" if self.count() > 1 else "false"
        if self.property("hasChoices") != val:
            self.setProperty("hasChoices", val)
            self._repolish()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if _qobj_valid(self) and obj is self.lineEdit():
            et = event.type()
            if et == QEvent.Type.FocusIn:
                if self.property("focused") != "true":
                    self.setProperty("focused", "true")
                    self._repolish()
            elif et == QEvent.Type.FocusOut:
                if self.property("focused") != "false":
                    self.setProperty("focused", "false")
                    self._repolish()
        return super().eventFilter(obj, event)

    def _deselect_line_edit_now(self) -> None:
        if not _qobj_valid(self):
            return
        le = self.lineEdit()
        if _qobj_valid(le):
            le.deselect()

    def _deselect_text_soon(self) -> None:
        if _qobj_valid(self) and self.lineEdit() is not None:
            self._deselect_timer.start()

    def focusOutEvent(self, event) -> None:  # noqa: N802
        super().focusOutEvent(event)
        le = self.lineEdit()
        if le:
            le.deselect()
        if self.property("focused") != "false":
            self.setProperty("focused", "false")
            self._repolish()

    def showPopup(self) -> None:  # noqa: N802
        # Non aprire alcun menu a tendina se c'è una sola opzione (o nessuna)
        if self.count() <= 1:
            return
        view = self.view()
        if view is not None:
            fm = self.fontMetrics()
            max_w = 0
            for i in range(self.count()):
                max_w = max(max_w, fm.horizontalAdvance(self.itemText(i)))
            # Considera lo spazio per il segno di spunta nativo macOS (✓) + margini laterali
            view.setMinimumWidth(max(self.width(), max_w + 56))
        super().showPopup()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        # Disegna la freccia chevron macOS (▾) solo se sono presenti più opzioni alternative
        if self.count() <= 1:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        cx = float(self.width()) - 11.0
        cy = float(self.height()) / 2.0
        pen = QPen(QColor("#515154" if self.isEnabled() else "#AEAEB2"), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        path = QPainterPath()
        path.moveTo(QPointF(cx - 3.8, cy - 1.6))
        path.lineTo(QPointF(cx, cy + 2.2))
        path.lineTo(QPointF(cx + 3.8, cy - 1.6))
        painter.drawPath(path)
        painter.end()

    def set_choices(self, choices: List[str], current_text: str = "", preserve_user_text: bool = False) -> None:
        existing_text = self.currentText().strip()
        target_text = existing_text if (preserve_user_text and existing_text) else current_text.strip()

        seen = set()
        unique_choices: List[str] = []
        for c in choices:
            cs = str(c or "").strip()
            if not cs:
                continue
            low = cs.lower()
            if low not in seen:
                seen.add(low)
                unique_choices.append(cs)

        if target_text and target_text.lower() not in seen:
            unique_choices.insert(0, target_text)

        self._updating_choices = True
        self.blockSignals(True)
        try:
            self.clear()
            if unique_choices:
                self.addItems(unique_choices)
            if self.isEditable():
                if not target_text:
                    self.setCurrentIndex(-1)
                else:
                    idx = self.findText(target_text, Qt.MatchFlag.MatchFixedString)
                    if idx >= 0:
                        self.setCurrentIndex(idx)
                self.setEditText(target_text)
                if self.lineEdit():
                    self.lineEdit().deselect()
                    if self.lineEdit().hasFocus() and preserve_user_text:
                        self.lineEdit().setCursorPosition(len(target_text))
                    else:
                        self.lineEdit().setCursorPosition(0)
            else:
                idx = self.findText(target_text)
                if idx >= 0:
                    self.setCurrentIndex(idx)
        finally:
            self.blockSignals(False)
            self._updating_choices = False
        self._sync_has_choices()


class CleanHeaderView(QHeaderView):
    """
    Intestazione tabellare che disegna un indicatore di ordinamento vettoriale (▴ / ▾)
    nitido e ad alto contrasto anche quando macOS è impostato in Dark Mode.
    """

    def __init__(self, orientation: Qt.Orientation, parent: Optional[QWidget] = None):
        super().__init__(orientation, parent)
        self.setSectionsClickable(True)
        self.setSortIndicatorShown(True)

    def paintSection(self, painter: QPainter, rect, logicalIndex: int) -> None:  # noqa: N802
        painter.save()
        super().paintSection(painter, rect, logicalIndex)
        painter.restore()
        if self.isSortIndicatorShown() and self.sortIndicatorSection() == logicalIndex:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            cx = float(rect.right()) - 11.0
            cy = float(rect.center().y())
            pen = QPen(QColor("#141413"), 1.55)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            path = QPainterPath()
            if self.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder:
                path.moveTo(QPointF(cx - 3.6, cy + 1.8))
                path.lineTo(QPointF(cx, cy - 2.0))
                path.lineTo(QPointF(cx + 3.6, cy + 1.8))
            else:
                path.moveTo(QPointF(cx - 3.6, cy - 1.8))
                path.lineTo(QPointF(cx, cy + 2.0))
                path.lineTo(QPointF(cx + 3.6, cy - 1.8))
            painter.drawPath(path)
            painter.restore()


class SuggestionPopup(QFrame):
    """
    Menu flottante non bloccante per i suggerimenti in tempo reale.
    Non ruba il focus di digitazione alla QLineEdit e supporta frecce Su/Giù, Invio ed Esc.
    """

    suggestionAccepted = Signal(object)  # Emette l'oggetto Suggestion scelto
    dismissed = Signal()  # Emesso quando l'utente "tocca altrove" e il menu si chiude da solo

    def __init__(self, parent: QWidget):
        super().__init__(
            parent,
            Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet(
            """
            QFrame {
                background-color: #FFFFFF;
                border: 1px solid #C8C6BE;
                border-radius: 6px;
            }
            QListWidget {
                background-color: transparent;
                border: none;
                outline: 0;
                padding: 4px;
                font-family: "Avenir Next", "Segoe UI", "Helvetica Neue", ".AppleSystemUIFont", "DejaVu Sans", "Noto Sans", "Arial";
                font-size: 13px;
            }
            QListWidget::item {
                padding: 7px 10px;
                border-radius: 4px;
                color: #141413;
            }
            QListWidget::item:selected {
                background-color: #141413;
                color: #FFFFFF;
            }
            QListWidget::item:hover:!selected {
                background-color: #F2F0EA;
            }
            """
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        self.list_widget = QListWidget(self)
        self.list_widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setMouseTracking(True)
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.list_widget)
        self._suggestions: List[Suggestion] = []
        self._anchor: Optional[QWidget] = None
        self._app_filter_installed = False
        self._cleaned_up = False
        app = QApplication.instance()
        if app is not None:
            # Se l'app perde il primo piano (es. Cmd+Tab) il menu non deve restare sospeso sullo schermo
            app.applicationStateChanged.connect(self._on_app_state_changed)

    def _on_app_state_changed(self, state) -> None:
        if state != Qt.ApplicationState.ApplicationActive and self.isVisible():
            self.hide()
            self.dismissed.emit()

    def cleanup(self) -> None:
        """Scollega i listener globali prima che il parent dialog venga chiuso/distrutto."""
        if self._cleaned_up:
            return
        self._cleaned_up = True
        self._anchor = None
        self._suggestions.clear()
        if self.isVisible():
            self.hide()
        app = QApplication.instance()
        if app is not None:
            if self._app_filter_installed:
                try:
                    app.removeEventFilter(self)
                except RuntimeError:
                    pass
                self._app_filter_installed = False
            try:
                app.applicationStateChanged.disconnect(self._on_app_state_changed)
            except (RuntimeError, TypeError):
                pass

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # Mentre il menu è visibile, intercetta ogni clic nell'app: un tocco fuori dal menu e dal campo
        # ancorato chiude subito i suggerimenti (anche se il clic non sposta il focus, es. sfondo o etichette).
        app = QApplication.instance()
        if app is not None and not self._cleaned_up and not self._app_filter_installed:
            app.installEventFilter(self)
            self._app_filter_installed = True

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        app = QApplication.instance()
        if app is not None and self._app_filter_installed:
            try:
                app.removeEventFilter(self)
            except RuntimeError:
                pass
            self._app_filter_installed = False

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if self._cleaned_up:
            return False
        if event.type() == QEvent.Type.MouseButtonPress and self.isVisible():
            try:
                gp = event.globalPosition().toPoint()
            except AttributeError:
                gp = event.globalPos()
            inside_popup = self.geometry().contains(gp)
            inside_anchor = False
            if _qobj_valid(self._anchor):
                tl = self._anchor.mapToGlobal(QPoint(0, 0))
                inside_anchor = QRect(tl, self._anchor.size()).contains(gp)
            if not inside_popup and not inside_anchor:
                self.hide()
                self.dismissed.emit()
        return False

    def show_suggestions(self, anchor: QWidget, suggestions: List[Suggestion], lang: str = "it") -> None:
        self._anchor = anchor
        self._suggestions = list(suggestions)
        self.list_widget.clear()
        if not self._suggestions:
            self.hide()
            return

        for sug in self._suggestions:
            meta_parts = []
            if sug.author:
                meta_parts.append(sug.author)
            if sug.publisher:
                meta_parts.append(sug.publisher)
            if sug.year:
                meta_parts.append(sug.year)
            if sug.collection:
                coll_lbl = "Collana" if lang == "it" else "Series"
                meta_parts.append(f"{coll_lbl}: {sug.collection}")
            meta_str = "  •  ".join(meta_parts)

            if sug.source == "archive":
                badge = "In archivio" if lang == "it" else "In archive"
            elif sug.from_isbn:
                badge = "ISBN • Online" if sug.category == "Libri" else "EAN/UPC • Online"
            elif sug.source == "catalog":
                badge = "Catalogo" if lang == "it" else "Catalog"
            else:
                badge = "Online"

            label = f"{sug.title}"
            if meta_str:
                label += f"   —   {meta_str}"
            label += f"   [{badge}]"

            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, sug)
            self.list_widget.addItem(item)

        self.list_widget.setCurrentRow(0)
        row_h = max(self.list_widget.sizeHintForRow(0), 32)
        visible_rows = min(len(self._suggestions), 8)
        total_h = visible_rows * row_h + 14
        width = max(anchor.width(), 540)

        global_pos = anchor.mapToGlobal(QPoint(0, anchor.height() + 3))
        self.setGeometry(global_pos.x(), global_pos.y(), width, total_h)
        if not self.isVisible():
            self.show()
        self.raise_()

    def move_selection(self, delta: int) -> None:
        count = self.list_widget.count()
        if count == 0:
            return
        cur = self.list_widget.currentRow()
        nxt = max(0, min(count - 1, cur + delta))
        self.list_widget.setCurrentRow(nxt)

    def accept_current(self) -> bool:
        if not self.isVisible() or self.list_widget.count() == 0:
            return False
        item = self.list_widget.currentItem() or self.list_widget.item(0)
        if not item:
            return False
        sug = item.data(Qt.ItemDataRole.UserRole)
        self.hide()
        if sug is not None:
            self.suggestionAccepted.emit(sug)
            return True
        return False

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        sug = item.data(Qt.ItemDataRole.UserRole)
        self.hide()
        if sug is not None:
            self.suggestionAccepted.emit(sug)


# ==============================================================================
# WORKER ASINCRONI IN BACKGROUND (QTHREADPOOL)
# ==============================================================================
def _disconnect_worker_signals(worker: object) -> None:
    """Scollega in sicurezza tutti i segnali Qt del worker (ignora se già scollegati o eliminati)."""
    sig = getattr(worker, "signals", None)
    if not _qobj_valid(sig):
        return
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for name in ("chunk", "finished", "completed"):
            attr = getattr(sig, name, None)
            if attr is not None:
                try:
                    attr.disconnect()
                except (RuntimeError, TypeError):
                    pass


class SearchWorkerSignals(QObject):
    chunk = Signal(int, str, str, list)  # (req_id, category, field_mode, List[Suggestion])
    finished = Signal(int, str, str, list)  # (req_id, category, field_mode, List[Suggestion])
    completed = Signal(object)  # worker instance, emesso sempre nel finally di run()


class OnlineSearchWorker(QRunnable):
    def __init__(
        self,
        catalog: CatalogService,
        req_id: int,
        category: str,
        query: str,
        lang: str,
        field_mode: str,
        is_cancelled_fn,
        context_author: str = "",
        context_title: str = "",
    ):
        super().__init__()
        self.setAutoDelete(False)
        self.catalog = catalog
        self.req_id = req_id
        self.category = category
        self.query = query
        self.lang = lang
        self.field_mode = field_mode
        self.is_cancelled_fn = is_cancelled_fn
        self.context_author = context_author
        self.context_title = context_title
        self._run_finished = False
        self.signals = SearchWorkerSignals()

    def _cancelled(self) -> bool:
        try:
            return bool(self.is_cancelled_fn(self.req_id))
        except Exception:
            return True

    def run(self) -> None:
        try:
            if self._cancelled():
                return

            def _handle_chunk(chunk_items: List[Suggestion]) -> None:
                if not self._cancelled():
                    try:
                        self.signals.chunk.emit(self.req_id, self.category, self.field_mode, chunk_items)
                    except RuntimeError:
                        pass

            items = self.catalog.search_online(
                category=self.category,
                query=self.query,
                lang=self.lang,
                field_mode=self.field_mode,
                limit=25,
                is_cancelled=self._cancelled,
                context_author=self.context_author,
                context_title=self.context_title,
                on_chunk=_handle_chunk,
            )
            if not self._cancelled():
                try:
                    self.signals.finished.emit(self.req_id, self.category, self.field_mode, items)
                except RuntimeError:
                    pass
        finally:
            self._run_finished = True
            try:
                self.signals.completed.emit(self)
            except RuntimeError:
                pass


class LocalSearchWorkerSignals(QObject):
    finished = Signal(int, str, str, list)  # (req_id, category, field_mode, List[Suggestion])
    completed = Signal(object)


class LocalSearchWorker(QRunnable):
    """
    Ricerca locale istantanea (Archivio + Seed Index + cache) eseguita fuori dal thread grafico:
    digitare un titolo lungo non blocca mai l'interfaccia, e le ricerche rese obsolete da un nuovo
    carattere si interrompono da sole (`is_cancelled_fn`).
    """

    def __init__(
        self,
        catalog: CatalogService,
        req_id: int,
        category: str,
        query: str,
        lang: str,
        field_mode: str,
        archive_items: List[Item],
        is_cancelled_fn,
        context_author: str = "",
        context_title: str = "",
    ):
        super().__init__()
        self.setAutoDelete(False)
        self.catalog = catalog
        self.req_id = req_id
        self.category = category
        self.query = query
        self.lang = lang
        self.field_mode = field_mode
        self.archive_items = archive_items
        self.is_cancelled_fn = is_cancelled_fn
        self.context_author = context_author
        self.context_title = context_title
        self._run_finished = False
        self.signals = LocalSearchWorkerSignals()

    def _cancelled(self) -> bool:
        try:
            return bool(self.is_cancelled_fn(self.req_id))
        except Exception:
            return True

    def run(self) -> None:
        try:
            if self._cancelled():
                return
            try:
                hits = self.catalog.search_local(
                    category=self.category,
                    query=self.query,
                    archive_items=self.archive_items,
                    lang=self.lang,
                    field_mode=self.field_mode,
                    limit=25,
                    context_author=self.context_author,
                    context_title=self.context_title,
                    is_cancelled=self._cancelled,
                )
            except Exception:
                hits = []
            if self._cancelled():
                return
            try:
                self.signals.finished.emit(self.req_id, self.category, self.field_mode, hits)
            except RuntimeError:
                pass
        finally:
            self._run_finished = True
            try:
                self.signals.completed.emit(self)
            except RuntimeError:
                pass


class EditionWorkerSignals(QObject):
    finished = Signal(int, object)  # (req_id, EditionBundle)
    completed = Signal(object)


class EditionFetchWorker(QRunnable):
    def __init__(
        self,
        catalog: CatalogService,
        req_id: int,
        category: str,
        work_id: str,
        title: str,
        author: str,
        lang: str,
        is_cancelled_fn,
    ):
        super().__init__()
        self.setAutoDelete(False)
        self.catalog = catalog
        self.req_id = req_id
        self.category = category
        self.work_id = work_id
        self.title = title
        self.author = author
        self.lang = lang
        self.is_cancelled_fn = is_cancelled_fn
        self._run_finished = False
        self.signals = EditionWorkerSignals()

    def _cancelled(self) -> bool:
        try:
            return bool(self.is_cancelled_fn(self.req_id))
        except Exception:
            return True

    def run(self) -> None:
        try:
            if self._cancelled():
                return
            bundle = self.catalog.fetch_editions(
                category=self.category,
                work_id=self.work_id,
                title=self.title,
                author=self.author,
                lang=self.lang,
                is_cancelled=self._cancelled,
            )
            if bundle and not self._cancelled():
                try:
                    self.signals.finished.emit(self.req_id, bundle)
                except RuntimeError:
                    pass
        finally:
            self._run_finished = True
            try:
                self.signals.completed.emit(self)
            except RuntimeError:
                pass


# ==============================================================================
# MODELLO DATI VIRTUALIZZATO E PROXY DI ORDINAMENTO PER COGNOME
# ==============================================================================
SORT_ROLE = Qt.ItemDataRole.UserRole + 10
SEARCH_ROLE = Qt.ItemDataRole.UserRole + 11
ITEM_ROLE = Qt.ItemDataRole.UserRole + 12
LAST_INSERTED_ROLE = Qt.ItemDataRole.UserRole + 13


class ArchiveTableModel(QAbstractTableModel):
    """Modello tabellare ad alte prestazioni per migliaia di righe."""

    COL_TITLE = 0
    COL_AUTHOR = 1
    COL_SORT = 2
    COL_PUB = 3
    COL_COLL = 4
    COL_YEAR = 5

    def __init__(self, category: str = "Libri", lang: str = "it", parent: Optional[QObject] = None):
        super().__init__(parent)
        self.category = category
        self.lang = lang
        self.group_by_collection: bool = False
        self.items: List[Item] = []
        self.last_inserted_item: Optional[Item] = None

    def set_category_and_items(self, category: str, items: List[Item], lang: str) -> None:
        self.beginResetModel()
        self.category = category
        self.lang = lang
        self.items = list(items)
        if self.last_inserted_item not in self.items:
            self.last_inserted_item = None
        self.endResetModel()

    def set_last_inserted_item(self, item: Optional[Item]) -> None:
        if self.last_inserted_item is not item:
            self.last_inserted_item = item
            if self.rowCount() > 0:
                self.dataChanged.emit(
                    self.index(0, 0),
                    self.index(self.rowCount() - 1, self.columnCount() - 1),
                    [LAST_INSERTED_ROLE, Qt.ItemDataRole.BackgroundRole, Qt.ItemDataRole.ToolTipRole],
                )

    def set_language(self, lang: str) -> None:
        self.lang = lang
        self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, 5)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.items)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else 6

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if section == self.COL_TITLE:
                return tr(self.lang, "col_title")
            if section == self.COL_AUTHOR:
                return tr(self.lang, "col_author_book") if self.category == "Libri" else tr(self.lang, "col_author_music")
            if section == self.COL_SORT:
                return tr(self.lang, "col_sort")
            if section == self.COL_PUB:
                return tr(self.lang, "col_pub_book") if self.category == "Libri" else tr(self.lang, "col_pub_music")
            if section == self.COL_COLL:
                return tr(self.lang, "col_coll_book") if self.category == "Libri" else tr(self.lang, "col_coll_music")
            if section == self.COL_YEAR:
                return tr(self.lang, "col_year")
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self.items)):
            return None
        item = self.items[index.row()]
        col = index.column()

        if role in (
            Qt.ItemDataRole.DisplayRole,
            Qt.ItemDataRole.EditRole,
        ):
            if col == self.COL_TITLE:
                return item.title
            if col == self.COL_AUTHOR:
                return item.author
            if col == self.COL_SORT:
                return item.author_sort
            if col == self.COL_PUB:
                return item.publisher
            if col == self.COL_COLL:
                return item.collection
            if col == self.COL_YEAR:
                return item.year

        if role == Qt.ItemDataRole.ToolTipRole:
            val = None
            if col == self.COL_TITLE:
                val = item.title
            elif col == self.COL_AUTHOR:
                val = item.author
            elif col == self.COL_SORT:
                val = item.author_sort
            elif col == self.COL_PUB:
                val = item.publisher
            elif col == self.COL_COLL:
                val = item.collection
            elif col == self.COL_YEAR:
                val = item.year
            if self.last_inserted_item is not None and item is self.last_inserted_item:
                tag = f"[{tr(self.lang, 'tip_last_inserted')}]"
                return f"{val}  •  {tag}" if val else tag
            return val

        if role == LAST_INSERTED_ROLE:
            return self.last_inserted_item is not None and item is self.last_inserted_item

        if role == SORT_ROLE:
            # Ordinamento per Autore/Artista usa SEMPRE la chiave cognome (k_author)!
            if col == self.COL_TITLE:
                return (item.k_title, item.k_author, item.k_year)
            if col in (self.COL_AUTHOR, self.COL_SORT):
                return (item.k_author, item.k_title, item.k_year)
            if col == self.COL_PUB:
                return (item.k_publisher, item.k_author, item.k_title)
            if col == self.COL_COLL:
                return (item.k_collection_base or "\uffff", item.k_collection_num, item.k_author, item.k_title)
            if col == self.COL_YEAR:
                return (item.k_year, item.k_author, item.k_title)

        if role == SEARCH_ROLE:
            return item.blob

        if role == ITEM_ROLE:
            return item

        if role == Qt.ItemDataRole.ForegroundRole:
            if col == self.COL_SORT:
                return QColor("#73716C")
            if col == self.COL_COLL and item.collection:
                return QColor("#2B2A27")
            if col == self.COL_YEAR:
                return QColor("#575551")

        if role == Qt.ItemDataRole.BackgroundRole:
            if self.last_inserted_item is not None and item is self.last_inserted_item:
                return QColor("#FEF3D6")
            if self.group_by_collection and item.k_collection_base:
                return QColor("#F4F2EC")

        if role == Qt.ItemDataRole.FontRole:
            fam = _resolve_ui_font_family()
            if col == self.COL_TITLE:
                f = QFont(fam, 13)
                f.setWeight(QFont.Weight.Medium)
                return f
            if col == self.COL_COLL and item.collection:
                f = QFont(fam, 12)
                f.setWeight(QFont.Weight.Medium)
                return f

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col == self.COL_YEAR:
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        return None


class ArchiveProxyModel(QSortFilterProxyModel):
    """
    Proxy che filtra istantaneamente su tutte le colonne e ordina per cognome/chiave normalizzata.
    Quando `group_by_collection=True` (default), i volumi appartenenti a una Collana / Collezione
    vengono tenuti vicini e ordinati a parte per collana (e internamente per volume/autore).
    Quando `group_by_collection=False`, tutti gli elementi seguono il normale ordine alfabetico generale.
    """

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._query_tokens: List[str] = []
        self.group_by_collection: bool = False
        self.setSortRole(SORT_ROLE)
        self.setDynamicSortFilter(True)

    def set_group_by_collection(self, enabled: bool) -> None:
        self.group_by_collection = bool(enabled)
        src = self.sourceModel()
        if isinstance(src, ArchiveTableModel):
            src.group_by_collection = self.group_by_collection
        self.invalidate()
        if src and src.rowCount() > 0:
            src.dataChanged.emit(src.index(0, 0), src.index(src.rowCount() - 1, src.columnCount() - 1))

    def set_search_query(self, raw_query: str) -> None:
        folded = fold(raw_query)
        tokens = [t for t in folded.split() if t]
        if hasattr(self, "beginFilterChange") and hasattr(self, "endFilterChange"):
            self.beginFilterChange()
            self._query_tokens = tokens
            self.endFilterChange()
        else:
            self._query_tokens = tokens
            self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N802
        if not self._query_tokens:
            return True
        idx = self.sourceModel().index(source_row, 0, source_parent)
        blob = self.sourceModel().data(idx, SEARCH_ROLE) or ""
        return all(tok in blob for tok in self._query_tokens)

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:  # noqa: N802
        l_val = self.sourceModel().data(left, SORT_ROLE)
        r_val = self.sourceModel().data(right, SORT_ROLE)
        if l_val is None or r_val is None:
            return super().lessThan(left, right)

        if self.group_by_collection:
            l_item: Optional[Item] = self.sourceModel().data(left, ITEM_ROLE)
            r_item: Optional[Item] = self.sourceModel().data(right, ITEM_ROLE)
            if l_item is not None and r_item is not None:
                l_has = bool(l_item.k_collection_base)
                r_has = bool(r_item.k_collection_base)
                is_desc = self.sortOrder() == Qt.SortOrder.DescendingOrder
                # 1) I volumi singoli precedono la sezione "a parte" delle Collane
                if l_has != r_has:
                    res = not l_has  # True se left è singolo e right è in collana
                    return (not res) if is_desc else res
                # 2) Se entrambi appartengono a una Collana, raggruppa per nome collana
                #    e all'interno della stessa collana ordina per numero volume -> colonna attiva (es. cognome)
                if l_has and r_has:
                    if l_item.k_collection_base != r_item.k_collection_base:
                        res = l_item.k_collection_base < r_item.k_collection_base
                        return (not res) if is_desc else res
                    if l_item.k_collection_num != r_item.k_collection_num:
                        res = l_item.k_collection_num < r_item.k_collection_num
                        return (not res) if is_desc else res
                    return l_val < r_val

        return l_val < r_val


class ArchiveItemDelegate(QStyledItemDelegate):
    """
    Delegato tabellare ad alte prestazioni che gestisce:
    1. Evidenziazione visiva calda dell'ultimo inserimento (#FEF3D6 / #F4DE9F se selezionato).
    2. Barra verticale color ambra (#D97706) sul bordo sinistro della colonna Titolo.
    3. Rendering corretto dello sfondo per le collezioni (#F4F2EC).
    4. Separatore inferiore nitido (#F2F0EA o #E8D296 per l'ultimo inserimento).
    5. Tipografia, allineamento e troncatura (elisione) del testo.
    """

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)

        is_selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        is_last_inserted = bool(index.data(LAST_INSERTED_ROLE))
        bg_role = index.data(Qt.ItemDataRole.BackgroundRole)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 1. Sfondo cella
        if is_selected:
            bg_color = QColor("#F4DE9F") if is_last_inserted else QColor("#E5E2DA")
        elif is_last_inserted:
            bg_color = QColor("#FEF3D6")
        elif bg_role:
            bg_color = bg_role
        elif opt.features & QStyleOptionViewItem.ViewItemFeature.Alternate:
            bg_color = QColor("#FAF9F6")
        else:
            bg_color = QColor("#FFFFFF")

        painter.fillRect(opt.rect, bg_color)

        # 2. Linea di divisione inferiore
        sep_color = QColor("#E8D296") if (is_last_inserted and not is_selected) else QColor("#F2F0EA")
        painter.setPen(sep_color)
        painter.drawLine(opt.rect.bottomLeft(), opt.rect.bottomRight())

        # 3. Indicatore verticale ambra per l'ultimo inserimento (colonna Titolo)
        if is_last_inserted and index.column() == ArchiveTableModel.COL_TITLE:
            indicator_rect = QRect(opt.rect.left(), opt.rect.top() + 1, 4, opt.rect.height() - 2)
            painter.fillRect(indicator_rect, QColor("#D97706"))

        # 4. Testo della cella
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if text is not None and text != "":
            fg = index.data(Qt.ItemDataRole.ForegroundRole)
            if not fg:
                fg = QColor("#141413")
            font = index.data(Qt.ItemDataRole.FontRole) or opt.font
            align = index.data(Qt.ItemDataRole.TextAlignmentRole) or int(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )

            painter.setFont(font)
            painter.setPen(fg)

            left_pad = 15 if (is_last_inserted and index.column() == ArchiveTableModel.COL_TITLE) else 10
            text_rect = opt.rect.adjusted(left_pad, 0, -10, 0)

            metrics = painter.fontMetrics()
            elided = metrics.elidedText(str(text), Qt.TextElideMode.ElideRight, text_rect.width())
            painter.drawText(text_rect, align, elided)

        painter.restore()


class ArchiveTableView(QTableView):
    """Tabella principale personalizzata che deseleziona automaticamente al clic su aree vuote."""

    def mousePressEvent(self, event: QMouseEvent) -> None:
        idx = self.indexAt(event.pos())
        if not idx.isValid():
            self.clearSelection()
            if self.selectionModel():
                self.selectionModel().clearCurrentIndex()
        super().mousePressEvent(event)


# ==============================================================================
# COMANDO UNDO / REDO UNIVERSALE (SNAPSHOT LEGGERO PER CATEGORIA)
# ==============================================================================
class CategorySnapshotCommand(QUndoCommand):
    def __init__(
        self,
        window: "MainWindow",
        category: str,
        before: List[Item],
        after: List[Item],
        description: str,
    ):
        super().__init__(description)
        self.window = window
        self.category = category
        self.before = [
            Item(i.title, i.author, i.publisher, i.year, i.author_sort, i.isbn_or_barcode, category, i.collection)
            for i in before
        ]
        self.after = [
            Item(i.title, i.author, i.publisher, i.year, i.author_sort, i.isbn_or_barcode, category, i.collection)
            for i in after
        ]
        self._first_run = True

    def redo(self) -> None:
        if self._first_run:
            self._first_run = False
            self.window._apply_snapshot(self.category, self.after)
        else:
            self.window._apply_snapshot(self.category, self.after)
            if len(self.after) > len(self.before):
                inserted = self.window.data_by_cat[self.category][-1]
                self.window.last_inserted_by_cat[self.category] = inserted
                if self.category == self.window.current_category:
                    self.window.table_model.set_last_inserted_item(inserted)

    def undo(self) -> None:
        self.window._apply_snapshot(self.category, self.before)
        last = self.window.last_inserted_by_cat.get(self.category)
        if last not in self.window.data_by_cat.get(self.category, []):
            self.window.last_inserted_by_cat[self.category] = None
            if self.category == self.window.current_category:
                self.window.table_model.set_last_inserted_item(None)


# ==============================================================================
# DIALOGHI SECONDARI (MODIFICA ELEMENTO, ASSEGNA COLLANA, SALVA CON NOME, IMPOSTAZIONI)
# ==============================================================================
def _merge_ranked_suggestions(
    catalog: CatalogService,
    local_hits: Optional[List[Suggestion]],
    online_hits: List[Suggestion],
    limit: int = 25,
) -> List[Suggestion]:
    """Fonde risultati locali e online senza alterare gli oggetti originali."""

    def _clone(s_: Suggestion) -> Suggestion:
        return replace(
            s_,
            publishers=list(s_.publishers),
            years=list(s_.years),
            collections=list(s_.collections),
        )

    res_map: Dict[str, Suggestion] = {}
    for sug in local_hits or []:
        catalog._merge_suggestion(res_map, _clone(sug))
    for sug in online_hits:
        catalog._merge_suggestion(res_map, _clone(sug))
    return sorted(res_map.values(), key=lambda x: -x.score)[:limit]


def _filter_colls_for_publisher(
    colls: List[str],
    pub_name: str,
    bundle: Optional[EditionBundle],
) -> List[str]:
    """Esclude le collane di una bundle che appartengono esplicitamente a un altro editore/etichetta."""
    if not colls:
        return []
    pub_k = search_fold(pub_name)
    if not pub_k or not bundle or not bundle.collection_meta:
        return list(colls)
    out: List[str] = []
    for c in colls:
        cs = (c or "").strip()
        if not cs:
            continue
        meta = (
            bundle.collection_meta.get(cs)
            or bundle.collection_meta.get(fold(cs))
        )
        if meta:
            mp = search_fold(meta.get("publisher") or "")
            if mp and not (mp == pub_k or pub_k in mp or mp in pub_k):
                continue
        out.append(cs)
    return out


class EditItemDialog(QDialog):
    """Dialogo pulito per modificare un elemento esistente, con ricalcolo 'Ordina come' e Collana."""

    def __init__(
        self,
        item: Item,
        category: str,
        lang: str,
        catalog: CatalogService,
        existing_collections: List[str],
        parent: QWidget,
        archive_items: Optional[List[Item]] = None,
        online_enabled: bool = True,
        local_pool: Optional[QThreadPool] = None,
    ):
        super().__init__(parent)
        self.category = category
        self.lang = lang
        self.catalog = catalog
        self.archive_items = list(archive_items or [])
        self._online_enabled = bool(online_enabled)
        self._orig_isbn = item.isbn_or_barcode
        self._sort_manually_edited = False
        self._edition_req_id = 0
        self._search_req_id = 0
        self._local_req_id = 0
        self._active_suggestion_field = "title"
        self._local_hits: Optional[List[Suggestion]] = None
        self._online_hits: List[Suggestion] = []
        self._in_flight_workers: List[QRunnable] = []
        self._owns_local_pool = local_pool is None
        if local_pool is not None:
            self._local_pool = local_pool
        else:
            self._local_pool = QThreadPool(self)
            self._local_pool.setMaxThreadCount(2)
        self._programmatic_title_change = False
        self._last_title_text_edited: Optional[str] = None
        self._pending_online_query: Optional[str] = None
        self._closed = False
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(75)
        self._debounce_timer.timeout.connect(self._trigger_dlg_online_autocomplete)
        self.setWindowTitle(tr(lang, "dlg_edit_title"))
        self.setMinimumWidth(560)
        self.setStyleSheet(MACOS_STYLESHEET)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 18)
        layout.setSpacing(14)

        form = QFormLayout()
        form.setSpacing(10)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.ed_title = QLineEdit(item.title)
        self.ed_title.setClearButtonEnabled(True)
        self.ed_title.setToolTip(
            "Cancella o modifica il titolo (o premi ↓) per vedere i titoli suggeriti per questo artista/autore."
            if lang == "it"
            else "Clear or type the title (or press ↓) to see suggested titles for this artist/author."
        )
        self.ed_author = QLineEdit(item.author)
        self.ed_author.setClearButtonEnabled(True)
        self.ed_sort = QLineEdit(item.author_sort or surname_key(item.author, category))
        self.ed_sort.setToolTip(tr(lang, "sort_override_hint"))

        self.cb_pub = CleanComboBox(editable=True)
        self.cb_coll = CleanComboBox(editable=True)
        self.cb_year = CleanComboBox(editable=True)
        for cb in (self.cb_pub, self.cb_coll, self.cb_year):
            cb.setMinimumWidth(180)
            cb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        # Apri subito il dialogo senza bloccare la GUI con I/O di rete: usa solo cache locale e poi carica le edizioni in background
        self._bundle: Optional[EditionBundle] = None
        self._bundle_colls: List[str] = []
        self._existing_colls = list(existing_collections)
        pubs: List[str] = [item.publisher] if item.publisher.strip() else []
        yrs: List[str] = [item.year] if item.year.strip() else []

        self.cb_pub.set_choices(pubs, item.publisher)
        self._refresh_dlg_collections(item.publisher, item.collection)
        if self.cb_coll.lineEdit():
            self.cb_coll.lineEdit().setPlaceholderText(
                tr(lang, "ph_coll_book") if category == "Libri" else tr(lang, "ph_coll_music")
            )
            self.cb_coll.lineEdit().setClearButtonEnabled(True)
        self.cb_year.set_choices(yrs, item.year)
        self.cb_pub.activated.connect(self._on_dlg_pub_activated)
        self.cb_coll.activated.connect(self._on_dlg_coll_activated)

        lbl_author = tr(lang, "col_author_book") if category == "Libri" else tr(lang, "col_author_music")
        lbl_pub = tr(lang, "col_pub_book") if category == "Libri" else tr(lang, "col_pub_music")
        lbl_coll = tr(lang, "col_coll_book") if category == "Libri" else tr(lang, "col_coll_music")

        def _dlg_lbl(txt: str) -> QLabel:
            l = QLabel(txt)
            l.setStyleSheet("color: #141413; font-weight: 600;")
            return l

        form.addRow(_dlg_lbl(tr(lang, "col_title") + ":"), self.ed_title)
        form.addRow(_dlg_lbl(lbl_author + ":"), self.ed_author)
        form.addRow(_dlg_lbl(tr(lang, "col_sort") + ":"), self.ed_sort)
        form.addRow(_dlg_lbl(lbl_pub + ":"), self.cb_pub)
        form.addRow(_dlg_lbl(lbl_coll + ":"), self.cb_coll)
        form.addRow(_dlg_lbl(tr(lang, "col_year") + ":"), self.cb_year)
        layout.addLayout(form)

        self.popup = SuggestionPopup(self)
        self.popup.suggestionAccepted.connect(self._apply_dlg_suggestion)
        self.popup.dismissed.connect(self._dismiss_dlg_suggestions)

        self.ed_title.textEdited.connect(self._on_dlg_title_edited)
        self.ed_title.textChanged.connect(self._on_dlg_title_text_changed)
        self.ed_author.textEdited.connect(self._on_author_edited)
        self.ed_sort.textEdited.connect(self._on_sort_edited)
        self.ed_title.installEventFilter(self)
        self.ed_author.installEventFilter(self)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_ok = btns.button(QDialogButtonBox.StandardButton.Ok)
        btn_ok.setText(tr(lang, "btn_apply"))
        btn_ok.setObjectName("PrimaryButton")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText(tr(lang, "btn_cancel"))
        btns.accepted.connect(self._validate_and_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)
        _disable_mac_focus_rect(self)
        self._start_dlg_edition_fetch(item.title, item.author)

    def _dlg_suggestion_anchor(self, field_mode: str) -> QLineEdit:
        return self.ed_title if field_mode == "title" else self.ed_author

    def _dlg_suggestion_context(self, field_mode: str) -> Tuple[str, str]:
        ctx_author = self.ed_author.text().strip() if field_mode == "title" else ""
        ctx_title = self.ed_title.text().strip() if field_mode == "author" else ""
        return ctx_author, ctx_title

    def _dismiss_dlg_suggestions(self) -> None:
        self._search_req_id += 1
        self._local_req_id += 1
        self._debounce_timer.stop()
        self._local_hits = None
        self._online_hits = []
        self._pending_online_query = None
        self.popup.hide()

    def _on_dlg_title_edited(self, text: str) -> None:
        self._pending_online_query = None
        self._last_title_text_edited = text
        self._on_dlg_query_edited("title", text)

    def _on_dlg_title_text_changed(self, text: str) -> None:
        if self._programmatic_title_change or not self.isVisible():
            return
        # Cliccando sul pulsante '×' Qt emette textChanged("") ma non textEdited("")
        if text == "" and self._last_title_text_edited != "":
            self._pending_online_query = None
            self._last_title_text_edited = ""
            self.ed_title.setFocus()
            self._on_dlg_query_edited("title", "")

    def _on_dlg_query_edited(self, field_mode: str, text: str) -> None:
        self._active_suggestion_field = field_mode
        self._search_req_id += 1
        self._local_req_id += 1
        self._local_hits = None
        self._online_hits = []
        q = text.strip()
        ctx_author, ctx_title = self._dlg_suggestion_context(field_mode)
        has_author_ctx = field_mode == "title" and len(ctx_author) >= 2
        if not has_author_ctx and len(q) < 2:
            self._debounce_timer.stop()
            self.popup.hide()
            return

        worker = LocalSearchWorker(
            catalog=self.catalog,
            req_id=self._local_req_id,
            category=self.category,
            query=q,
            lang=self.lang,
            field_mode=field_mode,
            archive_items=self.archive_items,
            is_cancelled_fn=lambda rid: self._closed or rid != self._local_req_id,
            context_author=ctx_author,
            context_title=ctx_title,
        )
        worker.signals.finished.connect(self._on_dlg_local_search_finished)
        worker.signals.completed.connect(self._on_dlg_worker_completed)
        self._in_flight_workers.append(worker)
        self._local_pool.start(worker)

        if self._online_enabled and (len(q) >= 2 or has_author_ctx):
            self._debounce_timer.start()
        else:
            self._debounce_timer.stop()

    def _show_dlg_merged_suggestions(self, field_mode: str) -> None:
        merged = _merge_ranked_suggestions(self.catalog, self._local_hits, self._online_hits)
        if merged:
            self.popup.show_suggestions(self._dlg_suggestion_anchor(field_mode), merged, self.lang)
        elif self._local_hits is not None and not self._online_hits:
            self.popup.hide()

    def _on_dlg_local_search_finished(
        self, req_id: int, category: str, field_mode: str, local_hits: List[Suggestion]
    ) -> None:
        if self._closed or req_id != self._local_req_id or category != self.category:
            return
        if not self._dlg_suggestion_anchor(field_mode).hasFocus():
            return
        self._local_hits = list(local_hits or [])
        self._show_dlg_merged_suggestions(field_mode)

    def _trigger_dlg_online_autocomplete(self) -> None:
        if self._closed or not self._online_enabled:
            return
        field_mode = self._active_suggestion_field
        widget = self._dlg_suggestion_anchor(field_mode)
        if not widget.hasFocus():
            return
        q = self._pending_online_query if hasattr(self, "_pending_online_query") and self._pending_online_query is not None else widget.text().strip()
        self._pending_online_query = None
        ctx_author, ctx_title = self._dlg_suggestion_context(field_mode)
        has_author_ctx = field_mode == "title" and len(ctx_author) >= 2
        if not has_author_ctx and len(q) < 2:
            return

        self._search_req_id += 1
        req_id = self._search_req_id
        worker = OnlineSearchWorker(
            catalog=self.catalog,
            req_id=req_id,
            category=self.category,
            query=q,
            lang=self.lang,
            field_mode=field_mode,
            is_cancelled_fn=lambda rid: self._closed or rid != self._search_req_id,
            context_author=ctx_author,
            context_title=ctx_title,
        )
        worker.signals.chunk.connect(self._on_dlg_online_search_finished)
        worker.signals.finished.connect(self._on_dlg_online_search_finished)
        worker.signals.completed.connect(self._on_dlg_worker_completed)
        self._in_flight_workers.append(worker)
        QThreadPool.globalInstance().start(worker)

    def _on_dlg_online_search_finished(
        self, req_id: int, category: str, field_mode: str, online_items: List[Suggestion]
    ) -> None:
        if self._closed or req_id != self._search_req_id or category != self.category:
            return
        if not self._dlg_suggestion_anchor(field_mode).hasFocus():
            return
        self._online_hits = list(online_items or [])
        if self._local_hits is None:
            return
        self._show_dlg_merged_suggestions(field_mode)

    def _apply_dlg_suggestion(self, sug: Suggestion) -> None:
        self._dismiss_dlg_suggestions()
        self._programmatic_title_change = True
        try:
            self.ed_title.setText(sug.title)
            self.ed_title.deselect()
            self._last_title_text_edited = sug.title
            if not self.ed_author.text().strip() and sug.author:
                self.ed_author.setText(sug.author)
                self.ed_author.deselect()
                if not self._sort_manually_edited:
                    self.ed_sort.setText(sug.display_author_sort())
                    self.ed_sort.deselect()
        finally:
            self._programmatic_title_change = False

        active_author = self.ed_author.text().strip() or sug.author
        t_k = search_fold(sug.title)
        a_k = search_fold(active_author)
        same_work = [
            it
            for it in self.archive_items
            if search_fold(it.title) == t_k and (not a_k or search_fold(it.author) == a_k)
        ]
        self._existing_colls = unique_collections(same_work)

        # In Modifica preserva etichetta, collezione e anno già presenti (comodo per cofanetti/box duplicati)
        cur_pub = self.cb_pub.currentText().strip()
        pubs = list(sug.publishers) if sug.publishers else ([sug.publisher] if sug.publisher else [])
        self.cb_pub.set_choices(pubs, cur_pub or sug.publisher or (pubs[0] if pubs else ""), preserve_user_text=bool(cur_pub))

        cur_yr = self.cb_year.currentText().strip()
        yrs = list(sug.years) if sug.years else ([sug.year] if sug.year else [])
        self.cb_year.set_choices(yrs, cur_yr or sug.year or (yrs[0] if yrs else ""), preserve_user_text=bool(cur_yr))

        self._bundle = None
        self._bundle_colls = [c for c in ([sug.collection] if sug.collection else []) + list(sug.collections or []) if (c or "").strip()]
        cur_coll = self.cb_coll.currentText().strip()
        self._refresh_dlg_collections(self.cb_pub.currentText().strip(), cur_coll)
        self._start_dlg_edition_fetch(sug.title, active_author, sug.work_id)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        et = event.type()
        if et == QEvent.Type.FocusOut and obj in (self.ed_title, self.ed_author):
            if obj is self._dlg_suggestion_anchor(self._active_suggestion_field):
                self._dismiss_dlg_suggestions()
        elif et == QEvent.Type.KeyPress and obj in (self.ed_title, self.ed_author):
            key = event.key()
            if self.popup.isVisible():
                if key == Qt.Key.Key_Down:
                    self.popup.move_selection(+1)
                    return True
                if key == Qt.Key.Key_Up:
                    self.popup.move_selection(-1)
                    return True
                if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab):
                    if self.popup.accept_current():
                        return True
                if key == Qt.Key.Key_Escape:
                    self._dismiss_dlg_suggestions()
                    return True
            elif obj is self.ed_title and key == Qt.Key.Key_Down and len(self.ed_author.text().strip()) >= 2:
                # Freccia giù nel campo Titolo mostra subito tutti i titoli suggeriti per quell'artista/autore
                self._pending_online_query = ""
                self._on_dlg_query_edited("title", "")
                return True
        return super().eventFilter(obj, event)

    def _start_dlg_edition_fetch(self, title: str, author: str, work_id: str = "") -> None:
        if not title.strip() or not author.strip():
            return
        self._edition_req_id += 1
        req_id = self._edition_req_id
        worker = EditionFetchWorker(
            catalog=self.catalog,
            req_id=req_id,
            category=self.category,
            work_id=work_id,
            title=title,
            author=author,
            lang=self.lang,
            is_cancelled_fn=lambda rid: self._closed or rid != self._edition_req_id,
        )
        worker.signals.finished.connect(self._on_dlg_editions_fetched)
        worker.signals.completed.connect(self._on_dlg_worker_completed)
        self._in_flight_workers.append(worker)
        QThreadPool.globalInstance().start(worker)

    def _on_dlg_worker_completed(self, worker: object) -> None:
        _disconnect_worker_signals(worker)
        try:
            self._in_flight_workers.remove(worker)
        except ValueError:
            pass

    def _on_dlg_editions_fetched(self, req_id: int, bundle: EditionBundle) -> None:
        if self._closed or req_id != self._edition_req_id:
            return
        self._bundle = bundle
        self._bundle_colls = list(bundle.collections) if bundle else []

        cur_pub = self.cb_pub.currentText()
        merged_pubs = list(bundle.publishers) if bundle else []
        if cur_pub.strip() and cur_pub.strip() not in merged_pubs:
            merged_pubs.insert(0, cur_pub.strip())
        self.cb_pub.set_choices(merged_pubs, cur_pub)

        cur_yr = self.cb_year.currentText()
        merged_yrs = list(bundle.years) if bundle else []
        if cur_yr.strip() and cur_yr.strip() not in merged_yrs:
            merged_yrs.insert(0, cur_yr.strip())
        self.cb_year.set_choices(merged_yrs, cur_yr)

        self._refresh_dlg_collections(cur_pub.strip(), self.cb_coll.currentText())

    def take_in_flight_workers(self) -> List[QRunnable]:
        """Restituisce i worker non ancora terminati per mantenerli in vita lato MainWindow."""
        remaining = [w for w in self._in_flight_workers if not getattr(w, "_run_finished", False)]
        self._in_flight_workers.clear()
        return remaining

    def done(self, result: int) -> None:  # noqa: N802
        self._closed = True
        self._local_req_id += 1
        self._search_req_id += 1
        self._edition_req_id += 1
        self._debounce_timer.stop()
        self._dismiss_dlg_suggestions()
        self.popup.cleanup()
        for w in self._in_flight_workers:
            _disconnect_worker_signals(w)
        for le in (self.ed_title, self.ed_author):
            if _qobj_valid(le):
                try:
                    le.removeEventFilter(self)
                except RuntimeError:
                    pass
        if self._owns_local_pool:
            self._local_pool.clear()
            self._local_pool.waitForDone(300)
        super().done(result)

    def _refresh_dlg_collections(self, pub_name: str, current_coll: str) -> None:
        merged: List[str] = []
        author_txt = self.ed_author.text().strip()
        work_colls = _filter_colls_for_publisher(self._bundle_colls, pub_name, self._bundle)
        fallback_colls: List[str] = []
        if self.category == "Libri":
            if not work_colls and pub_name.strip():
                fallback_colls = self.catalog.collections_for_publisher(pub_name, self.category, author_txt)
        else:
            if not self._bundle or not self._bundle_colls:
                fallback_colls = self.catalog.collections_for_publisher(pub_name, self.category, author_txt)
        seen_low = set()
        for c in work_colls + self._existing_colls + fallback_colls:
            cs = (c or "").strip()
            if cs and cs.lower() not in seen_low:
                seen_low.add(cs.lower())
                merged.append(cs)
        self.cb_coll.set_choices(merged, current_coll)

    def _on_dlg_pub_activated(self, _idx: int) -> None:
        pub_txt = self.cb_pub.currentText().strip()
        self._refresh_dlg_collections(pub_txt, self.cb_coll.currentText().strip())
        if self._bundle and self._bundle.publisher_years and pub_txt:
            p_fold = search_fold(pub_txt)
            for k, yr in self._bundle.publisher_years.items():
                if search_fold(k) == p_fold and yr:
                    yrs = [self.cb_year.itemText(i) for i in range(self.cb_year.count()) if self.cb_year.itemText(i).strip()]
                    self.cb_year.set_choices(yrs, yr)
                    break

    def _on_dlg_coll_activated(self, _idx: int) -> None:
        coll_txt = self.cb_coll.currentText().strip()
        if not coll_txt:
            return
        pub_c, yr_c = self.catalog.resolve_collection_metadata(
            coll_txt,
            self.category,
            self.ed_author.text().strip(),
            self._bundle,
        )
        if pub_c:
            pubs = [self.cb_pub.itemText(i) for i in range(self.cb_pub.count()) if self.cb_pub.itemText(i).strip()]
            self.cb_pub.set_choices(pubs, pub_c)
        if yr_c:
            yrs = [self.cb_year.itemText(i) for i in range(self.cb_year.count()) if self.cb_year.itemText(i).strip()]
            self.cb_year.set_choices(yrs, yr_c)

    def _on_author_edited(self, text: str) -> None:
        self._pending_online_query = None
        if not self._sort_manually_edited:
            self.ed_sort.setText(surname_key(text, self.category))
        self._refresh_dlg_collections(self.cb_pub.currentText().strip(), self.cb_coll.currentText().strip())
        self._on_dlg_query_edited("author", text)

    def _on_sort_edited(self, _text: str) -> None:
        self._sort_manually_edited = True

    def _validate_and_accept(self) -> None:
        if not self.ed_title.text().strip() or not self.ed_author.text().strip():
            QMessageBox.warning(self, tr(self.lang, "dlg_edit_title"), tr(self.lang, "msg_missing_fields"))
            return
        self.accept()

    def updated_item(self) -> Item:
        title = self.ed_title.text().strip()
        author = self.ed_author.text().strip()
        sort_val = self.ed_sort.text().strip() or surname_key(author, self.category)
        pub = self.cb_pub.currentText().strip()
        coll = self.cb_coll.currentText().strip()
        year = self.cb_year.currentText().strip()
        return Item(
            title=title,
            author=author,
            publisher=pub,
            year=year,
            author_sort=sort_val,
            isbn_or_barcode=self._orig_isbn,
            category=self.category,
            collection=coll,
        )


class AssignCollectionDialog(QDialog):
    """Dialogo rapido per assegnare una Collana / Collezione a uno o più volumi selezionati in tabella."""

    def __init__(
        self,
        category: str,
        lang: str,
        existing_collections: List[str],
        initial_value: str = "",
        count: int = 1,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(tr(lang, "dlg_assign_coll_title"))
        self.setMinimumWidth(430)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 18)
        layout.setSpacing(12)

        lbl = QLabel(tr(lang, "dlg_assign_coll_prompt"))
        lbl.setWordWrap(True)
        layout.addWidget(lbl)

        self.cb_coll = CleanComboBox(editable=True)
        self.cb_coll.set_choices(existing_collections, initial_value)
        if self.cb_coll.lineEdit():
            self.cb_coll.lineEdit().setPlaceholderText(
                tr(lang, "ph_coll_book") if category == "Libri" else tr(lang, "ph_coll_music")
            )
            self.cb_coll.lineEdit().setClearButtonEnabled(True)
        layout.addWidget(self.cb_coll)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_ok = btns.button(QDialogButtonBox.StandardButton.Ok)
        btn_ok.setText(tr(lang, "btn_apply"))
        btn_ok.setObjectName("PrimaryButton")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText(tr(lang, "btn_cancel"))
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)
        self.setStyleSheet(MACOS_STYLESHEET)
        _disable_mac_focus_rect(self)

    def collection_value(self) -> str:
        return self.cb_coll.currentText().strip()

    def collection_name(self) -> str:
        return self.cb_coll.currentText().strip()


class SaveAsCategoryDialog(QDialog):
    """Dialogo 'Salva con nome' che mostra l'anteprima esatta del file con prefisso di categoria."""

    def __init__(self, category: str, lang: str, folder: str, parent: QWidget):
        super().__init__(parent)
        self.category = category
        self.lang = lang
        self.folder = folder
        self.prefix = CATEGORY_PREFIXES[category]
        self.setWindowTitle(tr(lang, "dlg_save_as_title"))
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 18)
        layout.setSpacing(12)

        prompt = QLabel(tr(lang, "dlg_save_as_prompt", cat=tr(lang, f"cat_{category}")))
        layout.addWidget(prompt)

        self.ed_name = QLineEdit()
        self.ed_name.setPlaceholderText(
            "es. classici, jazz, 2026..." if lang == "it" else "e.g. classics, jazz, 2026..."
        )
        layout.addWidget(self.ed_name)

        self.lbl_preview = QLabel()
        self.lbl_preview.setObjectName("MutedInfo")
        layout.addWidget(self.lbl_preview)

        self.ed_name.textChanged.connect(self._update_preview)
        self._update_preview("")

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_save_ok = btns.button(QDialogButtonBox.StandardButton.Ok)
        btn_save_ok.setText(tr(lang, "btn_save"))
        btn_save_ok.setObjectName("PrimaryButton")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText(tr(lang, "btn_cancel"))
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)
        self.setStyleSheet(MACOS_STYLESHEET)
        _disable_mac_focus_rect(self)

    def _sanitize(self, text: str) -> str:
        cleaned = "".join(ch if (ch.isalnum() or ch in ("_", "-")) else "_" for ch in text.strip())
        cleaned = cleaned.strip("_-")
        if cleaned.lower().startswith(self.prefix):
            cleaned = cleaned[len(self.prefix):]
        if cleaned.lower().endswith(".csv"):
            cleaned = cleaned[:-4]
        return cleaned

    def filename(self) -> str:
        s = self._sanitize(self.ed_name.text())
        if not s:
            return DEFAULT_FILES[self.category]
        return f"{self.prefix}{s}.csv"

    def _update_preview(self, _text: str) -> None:
        fn = self.filename()
        self.lbl_preview.setText(tr(self.lang, "dlg_save_as_preview", fn=fn))


class SettingsDialog(QDialog):
    """Finestra Impostazioni dell'applicazione (⌘,) con lingua, modalità online e gestione cache."""

    def __init__(self, settings: Settings, catalog: CatalogService, parent: QWidget):
        super().__init__(parent)
        self.settings = settings
        self.catalog = catalog
        self.lang = settings.language
        self.setWindowTitle(f"{tr(self.lang, 'dlg_settings_title')} — {APP_NAME} (v{APP_VERSION})")
        self.setMinimumWidth(780)
        self.setStyleSheet(MACOS_STYLESHEET)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 18)
        layout.setSpacing(14)

        form = QFormLayout()
        form.setSpacing(12)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        def _set_lbl(txt: str) -> QLabel:
            l = QLabel(txt)
            l.setStyleSheet("color: #141413; font-weight: 600;")
            return l

        self.cb_lang = CleanComboBox(editable=False)
        self.cb_lang.setMinimumWidth(180)
        self.cb_lang.addItem("Italiano", "it")
        self.cb_lang.addItem("English", "en")
        idx = self.cb_lang.findData(settings.language)
        if idx >= 0:
            self.cb_lang.setCurrentIndex(idx)
        form.addRow(_set_lbl(tr(self.lang, "dlg_settings_lang") + ":"), self.cb_lang)

        self.chk_online = StudioCheckBox(tr(self.lang, "dlg_settings_online"))
        self.chk_online.setChecked(settings.online_enabled)
        form.addRow(_set_lbl(""), self.chk_online)

        self.ed_discogs = QLineEdit(settings.discogs_token)
        self.ed_discogs.setMinimumWidth(380)
        self.ed_discogs.setPlaceholderText(tr(self.lang, "dlg_settings_discogs_ph"))
        self.ed_discogs.setClearButtonEnabled(True)
        form.addRow(_set_lbl(tr(self.lang, "dlg_settings_discogs") + ":"), self.ed_discogs)

        ed_arch_dir = QLineEdit(settings.folder)
        ed_arch_dir.setReadOnly(True)
        ed_arch_dir.setObjectName("SortPreviewEdit")
        ed_arch_dir.setCursorPosition(0)
        lbl_arch_txt = "Cartella Archivio (CSV/PDF):" if self.lang == "it" else "Archive Folder (CSV/PDF):"
        form.addRow(_set_lbl(lbl_arch_txt), ed_arch_dir)

        self.config_was_reset = False

        cfg_row = QHBoxLayout()
        cfg_row.setSpacing(8)
        ed_cfg = QLineEdit(config_path())
        ed_cfg.setReadOnly(True)
        ed_cfg.setObjectName("SortPreviewEdit")
        ed_cfg.setCursorPosition(0)
        btn_reset_cfg = QPushButton("Elimina config" if self.lang == "it" else "Reset config")
        btn_reset_cfg.setObjectName("DangerButton")
        btn_reset_cfg.setMinimumWidth(126)
        btn_reset_cfg.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_reset_cfg.setToolTip(
            "Elimina config.json e riapre la configurazione iniziale"
            if self.lang == "it"
            else "Delete config.json and reopen initial setup"
        )
        btn_reset_cfg.clicked.connect(self._reset_config)
        cfg_row.addWidget(ed_cfg, 1)
        cfg_row.addWidget(btn_reset_cfg)
        form.addRow(_set_lbl(tr(self.lang, "dlg_settings_cfg") + ":"), cfg_row)

        cache_row = QHBoxLayout()
        cache_row.setSpacing(8)
        ed_cache = QLineEdit(cache_path())
        ed_cache.setReadOnly(True)
        ed_cache.setObjectName("SortPreviewEdit")
        ed_cache.setCursorPosition(0)
        btn_clear_cache = QPushButton("Svuota cache" if self.lang == "it" else "Clear cache")
        btn_clear_cache.setMinimumWidth(126)
        btn_clear_cache.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_clear_cache.clicked.connect(self._clear_cache)
        cache_row.addWidget(ed_cache, 1)
        cache_row.addWidget(btn_clear_cache)
        form.addRow(_set_lbl(tr(self.lang, "dlg_settings_cache") + ":"), cache_row)

        layout.addLayout(form)

        note = QLabel(tr(self.lang, "dlg_settings_commercial"))
        note.setWordWrap(True)
        note.setObjectName("MutedInfo")
        note.setStyleSheet(
            "background-color: #EFECE6; border: 1px solid #DFDDD6; border-radius: 6px; padding: 11px 13px; color: #3A3936; font-size: 11.5px;"
        )
        layout.addWidget(note)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_ok = btns.button(QDialogButtonBox.StandardButton.Ok)
        btn_ok.setText(tr(self.lang, "btn_apply"))
        btn_ok.setObjectName("PrimaryButton")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText(tr(self.lang, "btn_cancel"))
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)
        _disable_mac_focus_rect(self)

    def _reset_config(self) -> None:
        reset_config_file()
        self.config_was_reset = True
        self.reject()

    def _clear_cache(self) -> None:
        try:
            self.catalog.cache.clear()
            msg = "Cache locale svuotata con successo." if self.lang == "it" else "Local cache cleared."
            QMessageBox.information(self, tr(self.lang, "dlg_settings_title"), msg)
        except Exception as e:
            QMessageBox.warning(self, tr(self.lang, "dlg_settings_title"), str(e))

    def selected_language(self) -> str:
        return str(self.cb_lang.currentData() or "it")

    def selected_online(self) -> bool:
        return bool(self.chk_online.isChecked())

    def selected_discogs_token(self) -> str:
        return self.ed_discogs.text().strip()


class FirstRunSetupDialog(QDialog):
    """
    Finestra di Benvenuto e Configurazione Iniziale mostrata esclusivamente al primo avvio assoluto
    (quando `config.json` non esiste ancora).
    - Parte in Inglese ("en") con traduzione istantanea di tutta la finestra se l'utente seleziona "Italiano".
    - Permette di scegliere o confermare la cartella di riferimento dell'Archivio senza creare nulla
      su disco finché l'utente non conferma con "Get Started / Inizia".
    """

    def __init__(self, settings: Settings, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.settings = settings
        self.lang = settings.language if settings.language in ("en", "it") else "en"
        self.setMinimumWidth(580)
        self.setStyleSheet(MACOS_STYLESHEET)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)

        self.lbl_title = QLabel()
        self.lbl_title.setStyleSheet("font-size: 19px; font-weight: 700; letter-spacing: 0.4px; color: #141413;")
        layout.addWidget(self.lbl_title)

        self.lbl_sub = QLabel()
        self.lbl_sub.setObjectName("MutedInfo")
        self.lbl_sub.setWordWrap(True)
        layout.addWidget(self.lbl_sub)

        # Card impostazioni iniziali
        card = QFrame()
        card.setProperty("class", "Card")
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(16, 14, 16, 14)
        c_layout.setSpacing(12)

        # 1. Scelta Lingua (Default: English)
        lang_row = QHBoxLayout()
        self.lbl_lang = QLabel()
        self.lbl_lang.setStyleSheet("font-weight: 600; color: #141413;")
        self.cb_lang = CleanComboBox(editable=False)
        self.cb_lang.setMinimumWidth(180)
        self.cb_lang.addItem("English", "en")
        self.cb_lang.addItem("Italiano", "it")
        self.cb_lang.setCurrentIndex(0 if self.lang == "en" else 1)
        self.cb_lang.currentIndexChanged.connect(self._on_lang_changed)
        lang_row.addWidget(self.lbl_lang)
        lang_row.addStretch(1)
        lang_row.addWidget(self.cb_lang)
        c_layout.addLayout(lang_row)

        # 2. Scelta Cartella di Riferimento dell'Archivio
        self.lbl_folder_title = QLabel()
        self.lbl_folder_title.setStyleSheet("font-weight: 600; color: #141413;")
        c_layout.addWidget(self.lbl_folder_title)

        folder_row = QHBoxLayout()
        folder_row.setSpacing(8)
        self.ed_folder = QLineEdit(self.settings.folder)
        self.ed_folder.textChanged.connect(self._update_folder_status)
        self.btn_browse = QPushButton()
        self.btn_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_browse.clicked.connect(self._browse_folder)
        folder_row.addWidget(self.ed_folder, 1)
        folder_row.addWidget(self.btn_browse)
        c_layout.addLayout(folder_row)

        self.lbl_folder_hint = QLabel()
        self.lbl_folder_hint.setObjectName("MutedInfo")
        self.lbl_folder_hint.setWordWrap(True)
        c_layout.addWidget(self.lbl_folder_hint)

        layout.addWidget(card)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.btn_ok = btns.button(QDialogButtonBox.StandardButton.Ok)
        self.btn_ok.setObjectName("PrimaryButton")
        self.btn_cancel = btns.button(QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._validate_and_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        self._retranslate()
        _disable_mac_focus_rect(self)

    def _on_lang_changed(self, _idx: int) -> None:
        self.lang = str(self.cb_lang.currentData() or "en")
        self._retranslate()

    def _retranslate(self) -> None:
        if self.lang == "it":
            self.setWindowTitle("Benvenuto — Archivio")
            self.lbl_title.setText("Benvenuto in Archivio")
            self.lbl_sub.setText(
                "Scegli la lingua dell'interfaccia e la cartella di riferimento in cui salvare o "
                "caricare i tuoi fogli CSV (Libri, CD, Vinili). Potrai modificarle in qualsiasi momento."
            )
            self.lbl_lang.setText("Lingua:")
            self.lbl_folder_title.setText("Cartella di riferimento dell'Archivio:")
            self.btn_browse.setText("Sfoglia…")
            self.btn_ok.setText("Inizia")
            self.btn_cancel.setText("Esci")
        else:
            self.setWindowTitle("Welcome — Archivio")
            self.lbl_title.setText("Welcome to Archivio")
            self.lbl_sub.setText(
                "Please choose your preferred interface language and the reference folder where your "
                "CSV sheets (Books, CDs, Vinyls) will be stored or loaded. You can change both anytime."
            )
            self.lbl_lang.setText("Language:")
            self.lbl_folder_title.setText("Reference Archive Folder:")
            self.btn_browse.setText("Browse…")
            self.btn_ok.setText("Get Started")
            self.btn_cancel.setText("Quit")
        self._update_folder_status()

    def _update_folder_status(self) -> None:
        p = os.path.expanduser(self.ed_folder.text().strip())
        if not p:
            self.lbl_folder_hint.setText("")
            return
        if os.path.isdir(p):
            scanned = scan_folder(p)
            existing_csv = {
                fn
                for files in scanned.values()
                for fn in files
                if os.path.isfile(os.path.join(p, fn))
            }
            total_csv = len(existing_csv)
            if self.lang == "it":
                if total_csv == 1:
                    self.lbl_folder_hint.setText(
                        "Cartella esistente · 1 file CSV trovato e pronto al caricamento."
                    )
                elif total_csv > 1:
                    self.lbl_folder_hint.setText(
                        f"Cartella esistente · {total_csv} file CSV trovati e pronti al caricamento."
                    )
                else:
                    self.lbl_folder_hint.setText(
                        "Cartella esistente · nessun file CSV presente (i nuovi fogli verranno salvati qui)."
                    )
            else:
                if total_csv == 1:
                    self.lbl_folder_hint.setText(
                        "Existing folder · 1 CSV file found and ready to load."
                    )
                elif total_csv > 1:
                    self.lbl_folder_hint.setText(
                        f"Existing folder · {total_csv} CSV files found and ready to load."
                    )
                else:
                    self.lbl_folder_hint.setText(
                        "Existing folder · no CSV files found (new sheets will be saved here)."
                    )
        else:
            if self.lang == "it":
                self.lbl_folder_hint.setText("La cartella verrà creata solo quando premi «Inizia».")
            else:
                self.lbl_folder_hint.setText("This folder will be created only when you click «Get Started».")

    def _browse_folder(self) -> None:
        cur = os.path.expanduser(self.ed_folder.text().strip())
        start_dir = cur if os.path.isdir(cur) else os.path.expanduser("~")
        title = "Scegli la cartella dell'Archivio" if self.lang == "it" else "Choose Archive Folder"
        chosen = QFileDialog.getExistingDirectory(self, title, start_dir)
        if chosen:
            self.ed_folder.setText(chosen)

    def _validate_and_accept(self) -> None:
        target = os.path.abspath(os.path.expanduser(self.ed_folder.text().strip()))
        if not target:
            return
        try:
            os.makedirs(target, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, self.windowTitle(), str(e))
            return
        self.settings.folder = target
        self.settings.language = self.lang
        self.settings.first_run_completed = True
        self.settings.save()
        self.accept()


# ==============================================================================
# FINESTRA DI SCANSIONE REMOTA DA DISPOSITIVO MOBILE (GITHUB PAGES / HTTPS / LAN)
# ==============================================================================
class RemoteScannerDialog(QDialog):
    """
    Finestra non bloccante che mostra il QR Code di accoppiamento e il registro
    delle scansioni in tempo reale provenienti da qualsiasi dispositivo mobile (smartphone o tablet).
    """

    def __init__(
        self,
        scanner: RemoteScannerService,
        settings: Settings,
        get_category_fn,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.scanner = scanner
        self.settings = settings
        self.get_category_fn = get_category_fn
        self.lang = settings.language
        self.setModal(False)
        self.setMinimumWidth(700)
        self.setMinimumHeight(620)

        self.setWindowTitle(
            "Scansione da Dispositivo Mobile — Archivio"
            if self.lang == "it"
            else "Mobile Device Scanner — Archivio"
        )
        self.setStyleSheet(MACOS_STYLESHEET)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        lbl_hdr = QLabel(
            "Scansiona Codici a Barre e Copertine da Dispositivo Mobile"
            if self.lang == "it"
            else "Scan Barcodes & Covers from Mobile Device"
        )
        lbl_hdr.setStyleSheet("font-size: 16px; font-weight: 600; letter-spacing: 0.3px; color: #141413;")
        layout.addWidget(lbl_hdr)

        lbl_desc = QLabel(
            "Inquadra il QR Code con la fotocamera del tuo dispositivo mobile (smartphone o tablet iOS / Android, sia in Wi-Fi che 4G/5G). "
            "Si aprirà lo scanner web senza installare alcuna app: potrai acquisire codici ISBN/EAN/UPC, "
            "usare lo scatto antiriflesso o fotografare le copertine dei volumi d'epoca senza codice a barre."
            if self.lang == "it"
            else "Scan the QR Code with your mobile device camera (iOS or Android phone/tablet, on Wi-Fi or 4G/5G). "
            "A web scanner opens with zero app installation: scan ISBN/EAN/UPC barcodes, "
            "use anti-glare photo capture, or photograph vintage covers without barcodes (OCR)."
        )
        lbl_desc.setWordWrap(True)
        lbl_desc.setStyleSheet("color: #5C5A55; font-size: 12.5px;")
        layout.addWidget(lbl_desc)

        # Selettore modalità: GitHub Pages Ufficiale (senza siti esterni) o Rete Locale Offline
        mode_row = QHBoxLayout()
        lbl_mode = QLabel("Modalità di connessione:" if self.lang == "it" else "Connection mode:")
        lbl_mode.setStyleSheet("font-weight: 600; color: #141413;")
        self.cb_mode = CleanComboBox(editable=False)
        self.cb_mode.setMinimumWidth(380)
        if self.lang == "it":
            self.cb_mode.addItem("GitHub Pages Ufficiale — HTTPS (Consigliata)", "ghpages")
            self.cb_mode.addItem("Rete Locale Offline — HTTP Wi-Fi (solo Scatto Foto, senza Live)", "local")
        else:
            self.cb_mode.addItem("Official GitHub Pages — HTTPS (Recommended)", "ghpages")
            self.cb_mode.addItem("Offline Local Network — HTTP Wi-Fi (Photo Capture only, no Live)", "local")

        saved_mode = self.settings.scanner_mode if self.settings.scanner_mode in ("ghpages", "local") else "ghpages"
        idx_mode = self.cb_mode.findData(saved_mode)
        if idx_mode >= 0:
            self.cb_mode.setCurrentIndex(idx_mode)
        self.cb_mode.currentIndexChanged.connect(self._on_mode_changed)
        mode_row.addWidget(lbl_mode)
        mode_row.addWidget(self.cb_mode, 1)
        layout.addLayout(mode_row)

        # Card centrale con QR Code a sinistra e dettagli sessione a destra
        qr_card = QFrame()
        qr_card.setProperty("class", "Card")
        qr_layout = QHBoxLayout(qr_card)
        qr_layout.setContentsMargins(16, 14, 16, 14)
        qr_layout.setSpacing(18)

        self.lbl_qr = QLabel()
        self.lbl_qr.setFixedSize(210, 210)
        self.lbl_qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_qr.setWordWrap(True)
        self.lbl_qr.setStyleSheet(
            "background-color: #FFFFFF; border: 1px solid #DFDDD6; border-radius: 6px; padding: 8px; color: #5C5A55; font-size: 12px;"
        )
        qr_layout.addWidget(self.lbl_qr)

        right_col = QVBoxLayout()
        right_col.setSpacing(8)

        self.lbl_token = QLabel()
        self.lbl_token.setStyleSheet(
            "background-color: #EFECE6; color: #141413; border: 1px solid #DFDDD6; font-weight: 600; "
            "letter-spacing: 0.6px; font-size: 12px; padding: 5px 10px; border-radius: 4px;"
        )
        right_col.addWidget(self.lbl_token)

        self.lbl_tunnel_status = QLabel()
        self.lbl_tunnel_status.setWordWrap(True)
        self.lbl_tunnel_status.setStyleSheet("color: #2E5E3E; font-size: 12px; font-weight: 600;")
        right_col.addWidget(self.lbl_tunnel_status)

        self.lbl_gh = QLabel(
            "URL pagina GitHub Pages (es. https://<tuo-username>.github.io/archivio/scanner.html):"
            if self.lang == "it"
            else "GitHub Pages URL (e.g. https://<username>.github.io/archivio/scanner.html):"
        )
        self.lbl_gh.setWordWrap(True)
        self.lbl_gh.setStyleSheet("font-size: 11px; font-weight: 600; color: #4A4945; margin-top: 2px;")
        right_col.addWidget(self.lbl_gh)

        self.ed_gh_url = QLineEdit(self.settings.scanner_gh_pages_url)
        self.ed_gh_url.setClearButtonEnabled(True)
        self.ed_gh_url.setPlaceholderText("https://<tuo-username>.github.io/archivio/scanner.html")
        self.ed_gh_url.textChanged.connect(self._on_gh_url_changed)
        right_col.addWidget(self.ed_gh_url)

        lbl_url_title = QLabel("LINK COMPLETO DELLA SESSIONE" if self.lang == "it" else "COMPLETE SESSION LINK")
        lbl_url_title.setStyleSheet("font-size: 10px; font-weight: 600; letter-spacing: 0.9px; color: #73716C;")
        right_col.addWidget(lbl_url_title)

        self.ed_url = QLineEdit()
        self.ed_url.setReadOnly(True)
        right_col.addWidget(self.ed_url)

        btn_row = QHBoxLayout()
        self.btn_copy = QPushButton("Copia Link" if self.lang == "it" else "Copy Link")
        self.btn_copy.clicked.connect(self._copy_url)
        self.btn_regen = QPushButton("Nuovo Codice Sessione" if self.lang == "it" else "New Session Code")
        self.btn_regen.clicked.connect(self._regenerate_token)
        btn_row.addWidget(self.btn_copy)
        btn_row.addWidget(self.btn_regen)
        right_col.addLayout(btn_row)

        right_col.addStretch(1)
        qr_layout.addLayout(right_col, 1)
        layout.addWidget(qr_card)

        # Registro scansioni in tempo reale
        lbl_log = QLabel(
            "REGISTRO SCANSIONI IN TEMPO REALE"
            if self.lang == "it"
            else "REAL-TIME RECEIVED SCANS LOG"
        )
        lbl_log.setStyleSheet("font-size: 10.5px; font-weight: 600; letter-spacing: 0.9px; color: #5C5A55;")
        layout.addWidget(lbl_log)

        self.list_log = QListWidget()
        self.list_log.setMaximumHeight(125)
        self.list_log.setStyleSheet(
            "background-color: #FFFFFF; border: 1px solid #DFDDD6; border-radius: 6px; padding: 4px; font-size: 12px; color: #141413;"
        )
        layout.addWidget(self.list_log)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        btn_close = QPushButton("Chiudi (lo scanner resta attivo)" if self.lang == "it" else "Close (scanner stays active)")
        btn_close.setObjectName("PrimaryButton")
        btn_close.setMinimumWidth(220)
        btn_close.clicked.connect(self.hide)
        bottom.addWidget(btn_close)
        layout.addLayout(bottom)

        self.scanner.set_connection_mode(saved_mode, self.settings.scanner_gh_pages_url)
        self.refresh_qr()
        _disable_mac_focus_rect(self)

    def _on_mode_changed(self, _idx: int) -> None:
        mode = str(self.cb_mode.currentData() or "ghpages")
        self.settings.scanner_mode = mode
        self.settings.save()
        self.scanner.set_connection_mode(mode, self.ed_gh_url.text())
        self.refresh_qr()

    def _on_gh_url_changed(self, txt: str) -> None:
        clean = txt.strip()
        self.settings.scanner_gh_pages_url = clean
        self.scanner.custom_gh_pages_url = clean
        self.settings.save()
        self.refresh_qr()

    def _copy_url(self) -> None:
        cb = QApplication.clipboard()
        if cb and self.ed_url.text().strip():
            cb.setText(self.ed_url.text())

    def _regenerate_token(self) -> None:
        self.scanner.regenerate_session()
        self.refresh_qr()

    def append_log(self, _level: str, message: str) -> None:
        self.list_log.insertItem(0, message)
        while self.list_log.count() > 50:
            self.list_log.takeItem(self.list_log.count() - 1)

    def refresh_qr(self) -> None:
        cat = self.get_category_fn()
        mode = str(self.cb_mode.currentData() or "ghpages")
        is_gh = mode == "ghpages"
        self.lbl_gh.setVisible(is_gh or bool(self.ed_gh_url.text().strip()))
        self.ed_gh_url.setVisible(is_gh or bool(self.ed_gh_url.text().strip()))

        waiting_msg = ""
        url = ""
        status_color = "#2E5E3E"

        if mode == "local":
            url = self.scanner.local_url(self.lang, cat)
            status_txt = (
                f"✓ Rete locale Wi-Fi ({self.scanner.lan_ip}:{self.scanner.port}) — nessun sito esterno contattato (solo Scatto Foto)"
                if self.lang == "it"
                else f"✓ Local Wi-Fi ({self.scanner.lan_ip}:{self.scanner.port}) — zero external sites contacted (Photo Capture only)"
            )
        else:
            url = self.scanner.ghpages_url(self.lang, cat)
            if url:
                status_txt = (
                    "✓ GitHub Pages HTTPS ufficiale attivo — nessun sito esterno o tunnel, Scanner Live abilitato ovunque"
                    if self.lang == "it"
                    else "✓ Official GitHub Pages HTTPS active — zero external sites or tunnels, Live Scanner enabled everywhere"
                )
            else:
                status_color = "#8A4B08"
                status_txt = (
                    "Inserisci qui sotto l'URL della tua pagina GitHub Pages per generare il QR code."
                    if self.lang == "it"
                    else "Paste your GitHub Pages URL below to generate your permanent QR code."
                )
                waiting_msg = (
                    "In attesa dell'URL\nGitHub Pages\n(incollalo a destra)"
                    if self.lang == "it"
                    else "Waiting for\nGitHub Pages URL\n(paste on the right)"
                )

        sess_lbl = "SESSIONE" if self.lang == "it" else "SESSION"
        self.lbl_token.setText(f"{sess_lbl}  ·  {self.scanner.room_token}")
        self.lbl_tunnel_status.setStyleSheet(f"color: {status_color}; font-size: 12px; font-weight: 600;")
        self.lbl_tunnel_status.setText(status_txt)
        self.ed_url.setText(url)
        self.ed_url.setCursorPosition(0)
        self.ed_url.deselect()
        self.btn_copy.setEnabled(bool(url))

        if waiting_msg or not url:
            self.lbl_qr.setPixmap(QPixmap())
            self.lbl_qr.setText(waiting_msg or "QR Code")
            return

        tmp_qr = os.path.join(os.path.dirname(cache_path()), "current_qr.png")
        if generate_qr_png(url, tmp_qr):
            pix = QPixmap(tmp_qr)
            if not pix.isNull():
                self.lbl_qr.setPixmap(
                    pix.scaled(194, 194, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                )
                return
        self.lbl_qr.setText("QR Code\n" + self.scanner.room_token)


# ==============================================================================
# FINESTRA DI SCANSIONE DIRETTA CON LA FOTOCAMERA DEL COMPUTER (MACOS, WINDOWS, LINUX)
# ==============================================================================
class MacCameraScannerDialog(QDialog):
    """
    Finestra non bloccante per scansionare codici ISBN/EAN/UPC e copertine (OCR)
    usando direttamente la fotocamera integrata o esterna del computer (macOS, Windows, Linux).
    La fotocamera resta aperta dopo ogni acquisizione per consentire scansioni consecutive.
    Include anteprima a schede con inserimento rapido in archivio e flusso copertina "scatta e poi cerca".
    """

    _lookupFinished = Signal(int, object)
    _frozenOcrFinished = Signal(object)

    def __init__(
        self,
        scanner: RemoteScannerService,
        settings: Settings,
        get_category_fn,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        import threading as _th
        self._th = _th
        self._overlay_parent: Optional[QWidget] = parent
        self._mac_zoom_bridge_id: int = 0
        self._mac_zoom_bridge_ref: list[int] = [0]
        self._was_fullscreen: bool = False
        self._overlay_transitioning: bool = False
        self._close_after_fs: bool = False
        self._force_close_now: bool = False
        self._floating_geo: QRect = QRect()
        self.destroyed.connect(
            lambda _obj=None, ref=self._mac_zoom_bridge_ref: MacCameraScannerDialog._release_mac_zoom_bridge_id(ref)
        )
        self.scanner = scanner
        self.settings = settings
        self.get_category_fn = get_category_fn
        self.lang = settings.language
        win_type = Qt.WindowType.Dialog if sys.platform == "darwin" else Qt.WindowType.Window
        self.setWindowFlags(
            win_type
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowFullscreenButtonHint
        )
        self.setMinimumSize(560, 520)
        self.resize(760, 780)
        self.setStyleSheet(MACOS_STYLESHEET)

        self.setWindowTitle(
            "Scansione da Fotocamera del Computer — Archivio"
            if self.lang == "it"
            else "Computer Camera Scanner — Archivio"
        )

        self.cam_session = MacCameraSession()
        self.cam_session.signals.cameraStarted.connect(self._on_cam_started)
        self.cam_session.signals.frameReady.connect(self._on_frame_ready)
        self.cam_session.signals.ocrCaptured.connect(self._on_ocr_captured)
        self.cam_session.signals.cameraError.connect(self._on_cam_error)
        self._lookupFinished.connect(self._on_lookup_finished)
        self._frozenOcrFinished.connect(self._on_ocr_captured)

        self._live_barcode: str = ""
        self._last_auto_code: str = ""
        self._last_auto_ts: float = 0.0
        self._empty_bc_since: float = 0.0
        self._req_seq: int = 0
        self._ocr_waiting: bool = False
        self._is_cover_frozen: bool = False
        self._last_raw_pixmap: Optional[QPixmap] = None
        self._last_full_jpg_bytes: bytes = b""
        self._frozen_full_jpg_bytes: bytes = b""
        self._pending_frozen_tmp_paths: List[str] = []
        self._multi_candidates: List[dict] = []
        self._current_preview_item: Optional[dict] = None
        self._current_preview_cat: str = "Libri"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(8)

        # Riga superiore: titolo + selettore fotocamera
        top_row = QHBoxLayout()
        lbl_hdr = QLabel(
            "Scansiona con la Fotocamera del Computer"
            if self.lang == "it"
            else "Scan with Computer Camera"
        )
        lbl_hdr.setStyleSheet("font-size: 15px; font-weight: 600; color: #141413;")
        top_row.addWidget(lbl_hdr)
        top_row.addStretch(1)

        self.cb_cams = CleanComboBox(editable=False)
        self.cb_cams.setMinimumWidth(210)
        self.cb_cams.currentIndexChanged.connect(self._on_camera_choice_changed)
        top_row.addWidget(self.cb_cams)

        self.btn_reload_cams = QPushButton("Aggiorna" if self.lang == "it" else "Refresh")
        self.btn_reload_cams.clicked.connect(self._populate_cameras)
        top_row.addWidget(self.btn_reload_cams)
        layout.addLayout(top_row)

        lbl_sub = QLabel(
            "Avvicina il codice a barre o la copertina all'obiettivo della fotocamera. "
            "Puoi usare il pulsante verde della barra del titolo per ingrandirla a schermo intero o affiancarla all'archivio."
            if self.lang == "it"
            else "Hold the barcode or cover in front of your camera. "
            "Use the window's green titlebar button to expand to full screen or tile beside the archive."
        )
        lbl_sub.setWordWrap(True)
        lbl_sub.setStyleSheet("color: #5C5A55; font-size: 11.5px;")
        layout.addWidget(lbl_sub)

        # Riquadro anteprima video live a espansione dinamica per riempire l'intera finestra o schermo intero
        self.lbl_video = QLabel(
            "Avvio fotocamera in corso…"
            if self.lang == "it"
            else "Starting camera…"
        )
        self.lbl_video.setMinimumSize(460, 220)
        self.lbl_video.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.lbl_video.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_video.setStyleSheet(
            "background-color: #141413; color: #DFDDD6; border: 1px solid #141413; border-radius: 6px; font-size: 13px;"
        )
        layout.addWidget(self.lbl_video, 1)

        # Opzioni: Modalità Raffica + Acquisizione automatica
        opts_row = QHBoxLayout()
        opts_row.setContentsMargins(4, 16, 4, 8)
        self.chk_burst = StudioCheckBox(
            "Modalità Raffica (aggiungi subito)" if self.lang == "it" else "Burst Mode (add immediately)"
        )
        self.chk_burst.setChecked(bool(getattr(self.settings, "camera_burst_mode", False)))
        self.chk_auto = StudioCheckBox(
            "Acquisisci automaticamente appena inquadrato"
            if self.lang == "it"
            else "Auto-capture as soon as framed"
        )
        self.chk_auto.setChecked(bool(getattr(self.settings, "camera_auto_capture", True)))
        self.chk_burst.toggled.connect(self._on_camera_prefs_changed)
        self.chk_auto.toggled.connect(self._on_camera_prefs_changed)
        opts_row.addWidget(self.chk_burst)
        opts_row.addStretch(1)
        opts_row.addWidget(self.chk_auto)
        layout.addLayout(opts_row)

        # Pulsanti operativi principali
        act_row = QHBoxLayout()
        act_row.setSpacing(10)

        self.btn_capture_bc = QPushButton()
        self.btn_capture_bc.setObjectName("PrimaryButton")
        self.btn_capture_bc.setMinimumHeight(34)
        self.btn_capture_bc.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_capture_bc.clicked.connect(self.capture_barcode_now)
        act_row.addWidget(self.btn_capture_bc, 3)

        self.btn_capture_ocr = QPushButton(
            "  Scatta foto copertina (OCR)"
            if self.lang == "it"
            else "  Take cover photo (OCR)"
        )
        self.btn_capture_ocr.setIcon(_make_studio_icon("camera", "#141413", 14))
        self.btn_capture_ocr.setIconSize(QSize(14, 14))
        self.btn_capture_ocr.setObjectName("ScanActionButton")
        self.btn_capture_ocr.setMinimumHeight(34)
        self.btn_capture_ocr.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_capture_ocr.clicked.connect(self.capture_cover_ocr_now)
        act_row.addWidget(self.btn_capture_ocr, 2)
        layout.addLayout(act_row)

        # Scheda di anteprima con inserimento rapido direttamente nella schermata
        self.card_preview = QFrame()
        self.card_preview.setObjectName("CameraPreviewCard")
        self.card_preview.setStyleSheet(
            "QFrame#CameraPreviewCard { "
            "  background-color: #FFFFFF; "
            "  border: 1px solid #DFDDD6; "
            "  border-radius: 8px; "
            "  padding: 10px 14px; "
            "}"
        )
        card_lay = QVBoxLayout(self.card_preview)
        card_lay.setContentsMargins(12, 10, 12, 10)
        card_lay.setSpacing(4)

        card_top = QHBoxLayout()
        self.lbl_card_cat = QLabel("LIBRI")
        self.lbl_card_cat.setStyleSheet(
            "font-size: 10.5px; font-weight: 700; color: #73716C; letter-spacing: 0.5px;"
        )
        self.lbl_card_badge = QLabel("Elemento individuato" if self.lang == "it" else "Item identified")
        self.lbl_card_badge.setStyleSheet(
            "font-size: 11px; font-weight: 500; color: #1E5231; background: #F1F6F2; "
            "border: 1px solid #C6DBC9; border-radius: 4px; padding: 2px 7px;"
        )
        card_top.addWidget(self.lbl_card_cat)
        card_top.addWidget(self.lbl_card_badge)
        card_top.addStretch(1)
        card_lay.addLayout(card_top)

        self.lbl_card_title = QLabel("")
        self.lbl_card_title.setWordWrap(True)
        self.lbl_card_title.setStyleSheet("font-size: 14.5px; font-weight: 600; color: #141413;")
        card_lay.addWidget(self.lbl_card_title)

        self.lbl_card_author = QLabel("")
        self.lbl_card_author.setStyleSheet("font-size: 13px; font-weight: 500; color: #2B2A27;")
        card_lay.addWidget(self.lbl_card_author)

        self.lbl_card_details = QLabel("")
        self.lbl_card_details.setStyleSheet("font-size: 11.5px; color: #73716C;")
        card_lay.addWidget(self.lbl_card_details)

        card_btn_row = QHBoxLayout()
        card_btn_row.setContentsMargins(0, 6, 0, 0)
        card_btn_row.setSpacing(8)

        self.btn_card_add = QPushButton("✓ Aggiungi in Archivio (Invio)" if self.lang == "it" else "✓ Add to Archive (Enter)")
        self.btn_card_add.setMinimumHeight(34)
        self.btn_card_add.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_card_add.setStyleSheet(
            "background-color: #141413; color: #FFFFFF; font-size: 13px; font-weight: 600; "
            "border: 1px solid #141413; border-radius: 5px; padding: 6px 16px;"
        )
        self.btn_card_add.clicked.connect(self._on_card_add_clicked)
        card_btn_row.addWidget(self.btn_card_add, 3)

        self.btn_card_cancel = QPushButton("✕ Annulla / Prossimo (Esc)" if self.lang == "it" else "✕ Cancel / Next (Esc)")
        self.btn_card_cancel.setMinimumHeight(34)
        self.btn_card_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_card_cancel.setStyleSheet(
            "background-color: #FFFFFF; color: #5C5A55; font-size: 12.5px; "
            "border: 1px solid #DFDDD6; border-radius: 5px; padding: 6px 14px;"
        )
        self.btn_card_cancel.clicked.connect(self._on_card_cancel_clicked)
        card_btn_row.addWidget(self.btn_card_cancel, 2)

        card_lay.addLayout(card_btn_row)
        self.card_preview.hide()
        layout.addWidget(self.card_preview)

        # Stato immediato subito sotto
        self.lbl_scan_status = QLabel("")
        self.lbl_scan_status.setWordWrap(True)
        self.lbl_scan_status.setStyleSheet(
            "background-color: #EFECE6; color: #141413; border: 1px solid #DFDDD6; "
            "border-radius: 5px; padding: 8px 11px; font-size: 12.5px;"
        )
        self.lbl_scan_status.hide()
        layout.addWidget(self.lbl_scan_status)

        # Lista candidati per ISBN condivisi/multipli o OCR
        self.list_candidates = QListWidget()
        self.list_candidates.setMaximumHeight(110)
        self.list_candidates.setStyleSheet(
            "background-color: #FFFFFF; border: 1px solid #141413; border-radius: 5px; padding: 3px; font-size: 12px;"
        )
        self.list_candidates.itemClicked.connect(self._on_candidate_clicked)
        self.list_candidates.hide()
        layout.addWidget(self.list_candidates)

        # Chiusura
        btm = QHBoxLayout()
        btm.addStretch(1)
        btn_close = QPushButton("Chiudi fotocamera" if self.lang == "it" else "Close Camera")
        btn_close.clicked.connect(self.close)
        btm.addWidget(btn_close)
        layout.addLayout(btm)

        self._update_capture_button_text()
        self._populate_cameras(start_now=False)
        _disable_mac_focus_rect(self)

        # Disabilita default e autoDefault sui pulsanti ordinari per riservare Invio all'inserimento
        for btn in (
            self.btn_reload_cams,
            self.btn_capture_bc,
            self.btn_capture_ocr,
            self.btn_card_cancel,
            btn_close,
        ):
            btn.setAutoDefault(False)
            btn.setDefault(False)

        # Scorciatoie a livello di finestra: hanno la precedenza sul controllo che ha il focus
        for key in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(lambda k=key: self._handle_action_key(k))

    def _handle_action_key(self, k: int) -> bool:
        """
        Azioni rapide da tastiera:
        - Spazio: acquisisce SEMPRE il codice (barcode) inquadrato.
        - Invio: inserisce l'articolo individuato nell'archivio.
        - Esc: annulla anteprima / torna a riprendere / esce da schermo intero.
        """
        if k == Qt.Key.Key_Escape:
            if self.isFullScreen():
                self.showNormal()
                return True
            if not self.card_preview.isHidden():
                self._on_card_cancel_clicked()
                return True
            if self._is_cover_frozen:
                self.retake_cover_photo()
                return True
            return False

        if k == Qt.Key.Key_Space:
            self.capture_barcode_now()
            return True

        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if not self.card_preview.isHidden():
                self._on_card_add_clicked()
                return True
            if not self.list_candidates.isHidden() and self.list_candidates.currentItem():
                self._on_candidate_clicked(self.list_candidates.currentItem())
                return True
            if self._is_cover_frozen:
                self.confirm_search_from_frozen_photo()
                return True
            self._set_status_box(
                "warn",
                "Nessun elemento individuato da inserire. Inquadra un codice e premi Spazio per acquisirlo."
                if self.lang == "it"
                else "No item identified to insert. Frame a barcode and press Space to capture it.",
            )
            return True

        return False

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if self._handle_action_key(event.key()):
            event.accept()
            return
        super().keyPressEvent(event)

    def _prepare_mac_overlay(self) -> None:
        """Imposta NSWindow in FullScreenAuxiliary|MoveToActiveSpace prima di show() per aprire sopra Archivio."""
        b = _get_mac_objc_bridge()
        if not b:
            return
        try:
            ns_w = _mac_nswindow(self, b)
            if ns_w:
                b["msg_void_ul"](ns_w, b["sel_set_cb"], _MAC_CB_FULLSCREEN_AUX_ACTIVE)
        except Exception:
            pass

    def _attach_mac_overlay(self) -> None:
        """Aggancia la finestra in sovraimpressione sopra Archivio e collega il tasto verde a schermo intero."""
        b = _get_mac_objc_bridge()
        if not b:
            return
        try:
            ns_w = _mac_nswindow(self, b)
            if not ns_w:
                return
            b["msg_void_ul"](ns_w, b["sel_set_cb"], _MAC_CB_FULLSCREEN_AUX_ACTIVE)
            ns_p = _mac_nswindow(self._overlay_parent, b)
            if ns_p and self._overlay_parent is not None and self._overlay_parent.isVisible():
                b["msg_void_ptr_l"](ns_p, b["sel_add_child"], ns_w, 1)  # NSWindowAbove = 1
            zb = b["msg_ptr_ul"](ns_w, b["sel_std_btn"], 2)  # NSWindowZoomButton = 2
            if zb:
                if not self._mac_zoom_bridge_id:
                    obj = int(
                        b["msg_ptr"](
                            b["msg_ptr"](b["cls"], b["sel_alloc"]),
                            b["sel_init"],
                        )
                        or 0
                    )
                    if obj:
                        self._mac_zoom_bridge_id = obj
                        self._mac_zoom_bridge_ref[0] = obj
                        _MAC_ZOOM_HANDLERS[obj] = self._on_green_zoom_clicked
                if self._mac_zoom_bridge_id:
                    b["msg_void_ptr"](zb, b["sel_set_target"], self._mac_zoom_bridge_id)
                    b["msg_void_ptr"](zb, b["sel_set_action"], b["sel_on_zoom"])
        except Exception:
            pass

    def _detach_mac_for_fullscreen(self) -> None:
        """Stacca la finestra dal padre e abilita FullScreenPrimary prima dello schermo intero nativo."""
        b = _get_mac_objc_bridge()
        if not b:
            return
        try:
            ns_w = _mac_nswindow(self, b)
            if not ns_w:
                return
            ns_p = _mac_nswindow(self._overlay_parent, b)
            if ns_p:
                b["msg_void_ptr"](ns_p, b["sel_rem_child"], ns_w)
            b["msg_void_ul"](ns_w, b["sel_set_cb"], _MAC_CB_FULLSCREEN_PRIMARY_ACTIVE)
        except Exception:
            pass

    def _detach_mac_child_only(self) -> None:
        """Rimuove il legame childWindow quando la finestra viene nascosta o chiusa."""
        b = _get_mac_objc_bridge()
        if not b:
            return
        try:
            ns_w = _mac_nswindow(self, b)
            ns_p = _mac_nswindow(self._overlay_parent, b)
            if ns_w and ns_p:
                b["msg_void_ptr"](ns_p, b["sel_rem_child"], ns_w)
        except Exception:
            pass

    def _capture_floating_geo(self) -> None:
        if (
            self.isVisible()
            and not self.isFullScreen()
            and not self.isMaximized()
            and not self._overlay_transitioning
        ):
            geo = self.geometry()
            scr = self.screen() or QApplication.primaryScreen()
            if scr is not None:
                avail = scr.availableGeometry()
                if geo.width() >= int(avail.width() * 0.96) and geo.height() >= int(avail.height() * 0.96):
                    return
            self._floating_geo = QRect(geo)

    def _on_green_zoom_clicked(self) -> None:
        """Il pulsante verde alterna tra sovraimpressione sopra Archivio e schermo intero in una Space propria."""
        if self.isFullScreen():
            self.showNormal()
            return
        self._capture_floating_geo()
        self._overlay_transitioning = True
        self._detach_mac_for_fullscreen()
        self.showFullScreen()

    @staticmethod
    def _release_mac_zoom_bridge_id(ref: list[int]) -> None:
        bid = ref[0] if ref else 0
        if not bid or not _MAC_OBJC_BRIDGE:
            return
        ref[0] = 0
        _MAC_ZOOM_HANDLERS.pop(bid, None)
        try:
            _MAC_OBJC_BRIDGE["msg_ptr"](bid, _MAC_OBJC_BRIDGE["sel_release"])
        except Exception:
            pass

    def _restore_mac_overlay_after_fullscreen(self) -> None:
        if self._close_after_fs:
            self._close_after_fs = False
            self._force_close_now = True
            if self.isMaximized():
                self.showNormal()
            if self._floating_geo.isValid():
                self.setGeometry(self._floating_geo)
            self._overlay_transitioning = False
            self.close()
            return
        if (
            self._overlay_parent is not None
            and self._overlay_parent.isVisible()
            and self._overlay_parent.isFullScreen()
        ):
            self._overlay_parent.raise_()
            self._overlay_parent.activateWindow()
        if self.isMaximized():
            self.showNormal()
        if self._floating_geo.isValid():
            self.setGeometry(self._floating_geo)
        self._attach_mac_overlay()
        self.raise_()
        self.activateWindow()
        self._overlay_transitioning = False

    def _finish_deferred_close_after_fs(self) -> None:
        if not self._close_after_fs:
            return
        self._close_after_fs = False
        self._force_close_now = True
        self._overlay_transitioning = False
        self.close()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if sys.platform != "darwin" or event.type() != QEvent.Type.WindowStateChange:
            return
        is_fs = self.isFullScreen()
        if is_fs:
            self._was_fullscreen = True
            self._overlay_transitioning = False
            return
        if self._was_fullscreen:
            self._was_fullscreen = False
            self._overlay_transitioning = True
            QTimer.singleShot(0, self._restore_mac_overlay_after_fullscreen)
            return
        if not self._overlay_transitioning and self.isMaximized():
            # Fallback se l'utente attiva Zoom dal menu Finestra invece che dal tasto verde
            self._overlay_transitioning = True
            saved = QRect(self._floating_geo)

            def _unzoom_and_fs() -> None:
                self.showNormal()
                if saved.isValid():
                    self.setGeometry(saved)
                self._detach_mac_for_fullscreen()
                QTimer.singleShot(80, self.showFullScreen)

            QTimer.singleShot(0, _unzoom_and_fs)

    def reject(self) -> None:  # noqa: N802
        self.close()

    def showEvent(self, event) -> None:  # noqa: N802
        self._close_after_fs = False
        self._force_close_now = False
        if not self.isFullScreen():
            self._prepare_mac_overlay()
        super().showEvent(event)
        if not self.isFullScreen():
            self._attach_mac_overlay()
            self._capture_floating_geo()
        if not self.cam_session.is_running:
            self._start_selected_camera()

    def hideEvent(self, event) -> None:  # noqa: N802
        if not self.isFullScreen():
            self._detach_mac_child_only()
        self.cam_session.stop()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.cam_session.stop()
        if (
            sys.platform == "darwin"
            and self.isFullScreen()
            and not self._force_close_now
            and not QApplication.closingDown()
        ):
            self._close_after_fs = True
            event.ignore()
            self.showNormal()
            QTimer.singleShot(600, self._finish_deferred_close_after_fs)
            return
        self._close_after_fs = False
        self._force_close_now = False
        self._detach_mac_child_only()
        self._cleanup_pending_frozen_tmp_paths()
        super().closeEvent(event)

    def _populate_cameras(self, start_now: bool = True) -> None:
        cams = list_mac_cameras()
        saved_cam_id = str(getattr(self.settings, "camera_id", "") or "")
        self.cb_cams.blockSignals(True)
        self.cb_cams.clear()
        selected_idx = 0
        if not cams:
            self.cb_cams.addItem(
                "Nessuna fotocamera rilevata" if self.lang == "it" else "No camera detected",
                "",
            )
        else:
            for idx, c in enumerate(cams):
                self.cb_cams.addItem(c["name"], c["id"])
                if saved_cam_id and c["id"] == saved_cam_id:
                    selected_idx = idx
            self.cb_cams.setCurrentIndex(selected_idx)
        self.cb_cams.blockSignals(False)
        if start_now and self.isVisible():
            self._start_selected_camera()

    def _on_camera_choice_changed(self, _idx: int) -> None:
        cam_id = str(self.cb_cams.currentData() or "")
        if cam_id != getattr(self.settings, "camera_id", ""):
            self.settings.camera_id = cam_id
            self.settings.save()
        if self.isVisible():
            self._start_selected_camera()

    def _on_camera_prefs_changed(self, _checked: bool = False) -> None:
        auto_val = self.chk_auto.isChecked()
        burst_val = self.chk_burst.isChecked()
        if (
            auto_val != getattr(self.settings, "camera_auto_capture", True)
            or burst_val != getattr(self.settings, "camera_burst_mode", False)
        ):
            self.settings.camera_auto_capture = auto_val
            self.settings.camera_burst_mode = burst_val
            self.settings.save()

    def _start_selected_camera(self) -> None:
        cam_id = str(self.cb_cams.currentData() or "")
        self._live_barcode = ""
        self._is_cover_frozen = False
        self._update_capture_button_text()
        self.lbl_video.setPixmap(QPixmap())
        self.lbl_video.setText(
            "Avvio fotocamera in corso…"
            if self.lang == "it"
            else "Starting camera…"
        )
        self.cam_session.start(cam_id)

    def _on_cam_started(self, cam_name: str) -> None:
        self.lbl_video.setText("")
        self._set_status_box(
            "neutral",
            f"Fotocamera attiva: {cam_name}. Inquadra un codice a barre o una copertina."
            if self.lang == "it"
            else f"Active camera: {cam_name}. Frame a barcode or cover.",
        )

    def _on_cam_error(self, msg: str) -> None:
        self._ocr_waiting = False
        self.btn_capture_ocr.setEnabled(True)
        self.lbl_video.setPixmap(QPixmap())
        self.lbl_video.setText(msg)
        self._set_status_box("error", msg)

    def _update_capture_button_text(self) -> None:
        if self._is_cover_frozen:
            return
        if self._live_barcode:
            self.btn_capture_bc.setStyleSheet(
                "background-color: #1F4E35; border: 1px solid #1F4E35; color: #FFFFFF; font-weight: 600; padding: 6px 16px;"
            )
            self.btn_capture_bc.setText(
                f"Acquisisci codice: {self._live_barcode} (Spazio)"
                if self.lang == "it"
                else f"Capture barcode: {self._live_barcode} (Space)"
            )
        else:
            self.btn_capture_bc.setStyleSheet("")
            self.btn_capture_bc.setText(
                "Inquadra codice e premi per acquisire (Spazio)"
                if self.lang == "it"
                else "Frame barcode and press to capture (Space)"
            )

    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)
        self._capture_floating_geo()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._capture_floating_geo()
        if self._is_cover_frozen and self._frozen_full_jpg_bytes:
            self._render_frozen_cover(self._frozen_full_jpg_bytes)
        elif self._last_full_jpg_bytes:
            self._render_frame(self._last_full_jpg_bytes, self._live_barcode)

    def _render_frame(self, jpg_bytes: bytes, barcode: str) -> None:
        if not jpg_bytes:
            return
        pix = QPixmap()
        if not pix.loadFromData(jpg_bytes):
            return

        tgt_w = max(160, self.lbl_video.width())
        tgt_h = max(120, self.lbl_video.height())
        scaled = pix.scaled(
            tgt_w,
            tgt_h,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        x_off = max(0, (scaled.width() - tgt_w) // 2)
        y_off = max(0, (scaled.height() - tgt_h) // 2)
        canvas = scaled.copy(x_off, y_off, tgt_w, tgt_h)

        p = QPainter(canvas)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = float(canvas.width()), float(canvas.height())
        bw = min(560.0, max(260.0, w * 0.55))
        bh = min(280.0, max(130.0, h * 0.42))
        bx, by = (w - bw) / 2.0, (h - bh) / 2.0
        has_bc = bool(barcode.strip())
        frame_col = QColor("#3CC47C") if has_bc else QColor(255, 255, 255, 190)
        pen = QPen(frame_col, 2.4 if has_bc else 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        c_len = min(28.0, bw * 0.12)
        for cx0, cy0, sx, sy in (
            (bx, by, 1, 1),
            (bx + bw, by, -1, 1),
            (bx, by + bh, 1, -1),
            (bx + bw, by + bh, -1, -1),
        ):
            p.drawLine(QPointF(cx0, cy0), QPointF(cx0 + sx * c_len, cy0))
            p.drawLine(QPointF(cx0, cy0), QPointF(cx0, cy0 + sy * c_len))
        p.end()

        self._last_raw_pixmap = canvas.copy()
        self.lbl_video.setText("")
        self.lbl_video.setPixmap(canvas)

    def _render_frozen_cover(self, jpg_bytes: bytes) -> None:
        if not jpg_bytes:
            return
        pix = QPixmap()
        if not pix.loadFromData(jpg_bytes):
            return

        tgt_w = max(160, self.lbl_video.width())
        tgt_h = max(120, self.lbl_video.height())
        scaled = pix.scaled(
            tgt_w,
            tgt_h,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        x_off = max(0, (scaled.width() - tgt_w) // 2)
        y_off = max(0, (scaled.height() - tgt_h) // 2)
        snap = scaled.copy(x_off, y_off, tgt_w, tgt_h)

        p = QPainter(snap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        banner_h = 36.0
        p.fillRect(
            QRectF(0, snap.height() - banner_h, snap.width(), banner_h),
            QColor(20, 20, 19, 215),
        )
        p.setPen(QColor("#FFFFFF"))
        font = p.font()
        font.setPointSize(11)
        font.setBold(True)
        p.setFont(font)
        banner_text = (
            "FOTO SCATTATA — Verifica che il titolo sia leggibile"
            if self.lang == "it"
            else "PHOTO CAPTURED — Check title framing"
        )
        p.drawText(
            QRectF(14, snap.height() - banner_h, snap.width() - 28, banner_h),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            banner_text,
        )
        p.end()

        self.lbl_video.setPixmap(snap)

    def _on_frame_ready(self, jpg_bytes: bytes, barcode: str) -> None:
        import time as _time
        if self._is_cover_frozen:
            return

        self._last_full_jpg_bytes = jpg_bytes
        self._render_frame(jpg_bytes, barcode)

        clean_bc = barcode.strip()
        if clean_bc != self._live_barcode:
            self._live_barcode = clean_bc
            self._update_capture_button_text()

        if not clean_bc:
            if not self._empty_bc_since:
                self._empty_bc_since = _time.time()
            elif self._last_auto_code and (_time.time() - self._empty_bc_since) > 1.2:
                self._last_auto_code = ""
            return

        self._empty_bc_since = 0.0
        if self.chk_auto.isChecked():
            # Non azzerare scheda o candidati già aperti mentre l'utente tiene ancora lo stesso articolo davanti all'obiettivo
            same_code_pending = (
                clean_bc == self._last_auto_code
                and (not self.card_preview.isHidden() or not self.list_candidates.isHidden())
            )
            if same_code_pending:
                return
            now = _time.time()
            cooldown = 6.0 if self.chk_burst.isChecked() else 2.8
            if clean_bc != self._last_auto_code or (now - self._last_auto_ts) > cooldown:
                self._last_auto_code = clean_bc
                self._last_auto_ts = now
                self._start_barcode_lookup(clean_bc)

    def capture_barcode_now(self) -> None:
        if self._is_cover_frozen:
            self.retake_cover_photo()
            return
        if self._live_barcode:
            self._start_barcode_lookup(self._live_barcode)
            return
        # Se Vision non ha ancora agganciato il barcode nel frame ridotto, esegue una scansione completa sul frame HD
        self._ocr_waiting = True
        req_id = self._begin_status_search("fotogramma fotocamera" if self.lang == "it" else "camera frame")
        self._pending_ocr_req_id = req_id
        if not self.cam_session.request_ocr():
            self._ocr_waiting = False
            self._set_status_box(
                "warn",
                "Fotocamera non ancora pronta: attendi un istante."
                if self.lang == "it"
                else "Camera not ready yet: please wait a moment.",
            )
            return
        QTimer.singleShot(28000, lambda r=req_id: self._on_ocr_timeout(r))

    def capture_cover_ocr_now(self) -> None:
        if not self._is_cover_frozen:
            self.freeze_cover_photo()
        else:
            self.confirm_search_from_frozen_photo()

    def freeze_cover_photo(self) -> None:
        if not self._last_full_jpg_bytes and self._last_raw_pixmap is None:
            self._set_status_box(
                "warn",
                "Fotocamera non ancora pronta: attendi un istante."
                if self.lang == "it"
                else "Camera not ready yet: please wait a moment.",
            )
            return

        self._is_cover_frozen = True
        self._frozen_full_jpg_bytes = self._last_full_jpg_bytes
        self._render_frozen_cover(self._frozen_full_jpg_bytes)

        self.btn_capture_ocr.setIcon(QIcon())
        self.btn_capture_ocr.setText(
            "Cerca nel catalogo da questa foto (Invio)"
            if self.lang == "it"
            else "Search catalog from this photo (Enter)"
        )
        self.btn_capture_ocr.setStyleSheet(
            "background-color: #141413; color: #FFFFFF; font-weight: 600; padding: 6px 16px; border-radius: 5px;"
        )

        self.btn_capture_bc.setText(
            "Riscatta / Riprendi fotocamera (Esc)"
            if self.lang == "it"
            else "Retake / Resume camera (Esc)"
        )
        self.btn_capture_bc.setStyleSheet(
            "background-color: #FFFFFF; color: #141413; border: 1px solid #DFDDD6; font-weight: 500; padding: 6px 16px; border-radius: 5px;"
        )

        self._set_status_box(
            "neutral",
            "Foto scattata con successo. Premi «Cerca nel catalogo» per cercare, oppure «Riscatta» per riprendere la fotocamera."
            if self.lang == "it"
            else "Photo captured. Press «Search catalog» to search, or «Retake» to resume camera.",
        )

    def _cleanup_pending_frozen_tmp_paths(self) -> None:
        paths = list(getattr(self, "_pending_frozen_tmp_paths", []) or [])
        self._pending_frozen_tmp_paths = []
        for p in paths:
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass

    def retake_cover_photo(self) -> None:
        self._is_cover_frozen = False
        self._ocr_waiting = False
        self._cleanup_pending_frozen_tmp_paths()
        self.btn_capture_ocr.setEnabled(True)
        self.card_preview.hide()
        self._current_preview_item = None
        self._update_capture_button_text()
        self.btn_capture_ocr.setIcon(_make_studio_icon("camera", "#141413", 14))
        self.btn_capture_ocr.setText(
            "  Scatta foto copertina (OCR)"
            if self.lang == "it"
            else "  Take cover photo (OCR)"
        )
        self.btn_capture_ocr.setStyleSheet("")
        # Rimuove subito il banner "FOTO SCATTATA" prima ancora che arrivi il fotogramma successivo
        if self._last_full_jpg_bytes:
            self._render_frame(self._last_full_jpg_bytes, self._live_barcode)
        else:
            self.lbl_video.setPixmap(QPixmap())
        self._set_status_box(
            "neutral",
            "Fotocamera attiva. Inquadra un codice a barre o una copertina."
            if self.lang == "it"
            else "Camera active. Frame a barcode or cover.",
        )
        if self.isVisible() and not self.cam_session.is_running:
            self._start_selected_camera()

    def _on_ocr_timeout(self, req_id: int) -> None:
        if not self._ocr_waiting or req_id != getattr(self, "_pending_ocr_req_id", 0):
            return
        self._ocr_waiting = False
        self._cleanup_pending_frozen_tmp_paths()
        self.btn_capture_ocr.setEnabled(True)
        if self._is_cover_frozen:
            self.btn_capture_ocr.setText(
                "Cerca nel catalogo da questa foto (Invio)"
                if self.lang == "it"
                else "Search catalog from this photo (Enter)"
            )
        self._set_status_box(
            "warn",
            "Lettura OCR scaduta: riprova o riscatta la foto con più luce."
            if self.lang == "it"
            else "OCR timed out: try again or retake the photo in better light.",
        )

    def confirm_search_from_frozen_photo(self) -> None:
        self._ocr_waiting = True
        self.btn_capture_ocr.setEnabled(False)
        self.btn_capture_ocr.setText(
            "Ricerca in corso…" if self.lang == "it" else "Searching…"
        )
        req_id = self._begin_status_search("copertina OCR" if self.lang == "it" else "cover OCR")
        self._pending_ocr_req_id = req_id
        QTimer.singleShot(28000, lambda r=req_id: self._on_ocr_timeout(r))
        frozen_bytes = self._frozen_full_jpg_bytes
        if frozen_bytes:
            import io
            import tempfile
            from PIL import Image

            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".jpg")
            os.close(tmp_fd)
            try:
                with Image.open(io.BytesIO(frozen_bytes)) as im:
                    im.convert("RGB").save(tmp_path, format="JPEG", quality=95)
            except Exception:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
                tmp_path = ""

            if tmp_path:
                # Percorso rapido su macOS: usa lo stesso processo livecam già riscaldato (evita ricompilazione E5)
                if self.cam_session.request_scan_file(tmp_path):
                    self._pending_frozen_tmp_paths.append(tmp_path)
                    return

                def _frozen_ocr_worker(path_arg: str = tmp_path) -> None:
                    try:
                        ocr_res = run_apple_vision_on_image(path_arg, mode="ocr")
                    except Exception:
                        ocr_res = {"barcodes": [], "lines": []}
                    finally:
                        try:
                            os.remove(path_arg)
                        except OSError:
                            pass
                    try:
                        self._frozenOcrFinished.emit(ocr_res)
                    except RuntimeError:
                        pass

                self._th.Thread(target=_frozen_ocr_worker, daemon=True).start()
                return

        if not self.cam_session.request_ocr():
            self._ocr_waiting = False
            self.btn_capture_ocr.setEnabled(True)
            self.btn_capture_ocr.setText(
                "Cerca nel catalogo da questa foto (Invio)"
                if self.lang == "it"
                else "Search catalog from this photo (Enter)"
            )
            self._set_status_box(
                "warn",
                "Fotocamera non pronta per la lettura OCR."
                if self.lang == "it"
                else "Camera not ready for OCR.",
            )

    def _on_ocr_captured(self, payload_obj: object) -> None:
        self._cleanup_pending_frozen_tmp_paths()
        if not self._ocr_waiting or not isinstance(payload_obj, dict):
            return
        self._ocr_waiting = False
        req_id = getattr(self, "_pending_ocr_req_id", self._req_seq)
        barcodes = payload_obj.get("barcodes") or []
        lines = payload_obj.get("lines") or []
        cat = self.get_category_fn()
        auto_add = self.chk_burst.isChecked()

        def _cam_label() -> str:
            return "Fotocamera del computer" if self.lang == "it" else "Computer Camera"

        def _worker() -> None:
            res = self.scanner.process_remote_action(
                {
                    "action": "scan_ocr_lines",
                    "category": cat,
                    "barcodes": barcodes,
                    "lines": lines,
                    "auto_add": auto_add,
                    "device_label": _cam_label(),
                    "req_id": req_id,
                }
            )
            try:
                self._lookupFinished.emit(req_id, res)
            except RuntimeError:
                pass

        self._th.Thread(target=_worker, daemon=True).start()

    def _begin_status_search(self, label: str) -> int:
        self._req_seq += 1
        req_id = self._req_seq
        self.list_candidates.hide()
        self.card_preview.hide()
        self._multi_candidates = []
        msg = (
            f"● Codice {label} acquisito — Ricerca nel catalogo in corso…"
            if self.lang == "it"
            else f"● Captured {label} — Searching catalog…"
        )
        self._set_status_box("progress", msg)
        return req_id

    def _start_barcode_lookup(self, code: str) -> None:
        req_id = self._begin_status_search(code)
        cat = self.get_category_fn()
        auto_add = self.chk_burst.isChecked()

        def _worker() -> None:
            res = self.scanner.process_remote_action(
                {
                    "action": "scan_barcode",
                    "code": code,
                    "category": cat,
                    "auto_add": auto_add,
                    "device_label": "Fotocamera del computer" if self.lang == "it" else "Computer Camera",
                    "req_id": req_id,
                }
            )
            try:
                self._lookupFinished.emit(req_id, res)
            except RuntimeError:
                pass

        self._th.Thread(target=_worker, daemon=True).start()

    def _on_lookup_finished(self, req_id: int, res: object) -> None:
        if req_id < self._req_seq or not isinstance(res, dict):
            return
        self.btn_capture_ocr.setEnabled(True)
        if self._is_cover_frozen:
            self.btn_capture_ocr.setText(
                "Cerca nel catalogo da questa foto (Invio)"
                if self.lang == "it"
                else "Search catalog from this photo (Enter)"
            )
        status = res.get("status")
        it = res.get("item") or {}
        cands = res.get("candidates") or []
        if res.get("multi_isbn") and len(cands) > 1:
            self._multi_candidates = cands
            self.list_candidates.clear()
            for c in cands:
                meta = " • ".join(x for x in (c.get("author"), c.get("publisher"), c.get("year")) if x)
                self.list_candidates.addItem(f"{c.get('title', '')}  —  {meta}")
            self.list_candidates.show()
            self._set_status_box(
                "warn",
                f"Codice ISBN condiviso da {len(cands)} titoli: clicca il tuo volume nell'elenco qui sotto per inserirlo."
                if self.lang == "it"
                else f"ISBN shared by {len(cands)} books: click your book in the list below to add it.",
            )
            return

        if status == "ok" and it.get("title"):
            if res.get("added"):
                self.card_preview.hide()
                self._current_preview_item = None
                self._set_status_box(
                    "ok",
                    f"✓ Aggiunto con successo in {res.get('category', '')}: {it.get('title')} — {it.get('author')}"
                    if self.lang == "it"
                    else f"✓ Added successfully to {res.get('category', '')}: {it.get('title')} — {it.get('author')}",
                )
                if self._is_cover_frozen:
                    self.retake_cover_photo()
            else:
                self._current_preview_item = it
                cat_name = res.get("category") or self.get_category_fn()
                self._current_preview_cat = cat_name
                self.lbl_card_cat.setText(cat_name.upper())
                self.lbl_card_title.setText(str(it.get("title") or ""))
                self.lbl_card_author.setText(str(it.get("author") or ""))
                details = " • ".join(
                    str(x) for x in (it.get("publisher"), it.get("collection"), it.get("year")) if x
                )
                self.lbl_card_details.setText(details)
                self.card_preview.show()
                self.btn_card_add.setDefault(True)
                self.btn_card_add.setFocus()
                self._set_status_box(
                    "ok",
                    f"✓ Individuato: «{it.get('title')}». Premi «Aggiungi in Archivio» (o Invio) per inserire."
                    if self.lang == "it"
                    else f"✓ Identified: «{it.get('title')}». Press «Add to Archive» (or Enter) to insert.",
                )
        elif status == "issn_warning":
            self._set_status_box(
                "warn",
                f"Codice periodico da edicola ({res.get('code')}): inquadra il titolo sulla copertina e premi «Scatta foto copertina»."
                if self.lang == "it"
                else f"Newsstand periodical code ({res.get('code')}): frame the cover title and press «Take cover photo».",
            )
        else:
            self._set_status_box(
                "warn",
                f"Nessuna corrispondenza trovata per «{res.get('code', '')}». Prova a scattare la copertina."
                if self.lang == "it"
                else f"No match found for «{res.get('code', '')}». Try taking a photo of the cover.",
            )

    def _on_card_add_clicked(self) -> None:
        if not self._current_preview_item:
            return
        cat = getattr(self, "_current_preview_cat", None) or self.get_category_fn()
        item_dict = self._current_preview_item
        self.scanner.process_remote_action(
            {
                "action": "add_item",
                "category": cat,
                "item": item_dict,
                "device_label": "Fotocamera del computer" if self.lang == "it" else "Computer Camera",
            }
        )
        title = item_dict.get("title", "")
        author = item_dict.get("author", "")
        import time as _time
        self.btn_card_add.setDefault(False)
        self.card_preview.hide()
        self._current_preview_item = None
        self._last_auto_ts = _time.time()
        self._set_status_box(
            "ok",
            f"✓ Aggiunto con successo in {cat}: {title} — {author}"
            if self.lang == "it"
            else f"✓ Added successfully to {cat}: {title} — {author}",
        )
        if self._is_cover_frozen:
            self.retake_cover_photo()

    def _on_card_cancel_clicked(self) -> None:
        import time as _time
        self.btn_card_add.setDefault(False)
        self.card_preview.hide()
        self._current_preview_item = None
        self._last_auto_ts = _time.time()
        self._set_status_box(
            "neutral",
            "Inserimento ignorato. Fotocamera pronta per la prossima scansione."
            if self.lang == "it"
            else "Skipped. Ready for next scan.",
        )
        if self._is_cover_frozen:
            self.retake_cover_photo()

    def _on_candidate_clicked(self, item_widget: QListWidgetItem) -> None:
        row = self.list_candidates.row(item_widget)
        if 0 <= row < len(self._multi_candidates):
            c = self._multi_candidates[row]
            cat = self.get_category_fn()
            if self.chk_burst.isChecked():
                self.scanner.process_remote_action(
                    {
                        "action": "add_item",
                        "category": cat,
                        "device_label": "Fotocamera del computer" if self.lang == "it" else "Computer Camera",
                        "item": c,
                    }
                )
                self.list_candidates.hide()
                self._set_status_box(
                    "ok",
                    f"✓ Aggiunto in {cat}: {c.get('title')} — {c.get('author')}"
                    if self.lang == "it"
                    else f"✓ Added to {cat}: {c.get('title')} — {c.get('author')}",
                )
                if self._is_cover_frozen:
                    self.retake_cover_photo()
            else:
                self.scanner.process_remote_action(
                    {
                        "action": "preview_item",
                        "category": cat,
                        "device_label": "Fotocamera del computer" if self.lang == "it" else "Computer Camera",
                        "item": c,
                        "fetch_remote": False,
                    }
                )
                self.list_candidates.hide()
                self._current_preview_item = c
                self._current_preview_cat = cat
                self.lbl_card_cat.setText(cat.upper())
                self.lbl_card_title.setText(str(c.get("title") or ""))
                self.lbl_card_author.setText(str(c.get("author") or ""))
                details = " • ".join(
                    str(x) for x in (c.get("publisher"), c.get("collection"), c.get("year")) if x
                )
                self.lbl_card_details.setText(details)
                self.card_preview.show()
                self.btn_card_add.setFocus()
                self._set_status_box(
                    "ok",
                    f"✓ Selezionato: {c.get('title')} — {c.get('author')}. Premi Aggiungi in Archivio."
                    if self.lang == "it"
                    else f"✓ Selected: {c.get('title')} — {c.get('author')}. Press Add to Archive.",
                )

    def _set_status_box(self, kind: str, text: str) -> None:
        styles = {
            "progress": "background-color: #EFECE6; color: #141413; border: 1px solid #141413;",
            "ok": "background-color: #F1F6F2; color: #1E5231; border: 1px solid #C6DBC9;",
            "warn": "background-color: #FAF5EA; color: #6E470B; border: 1px solid #DEC69B;",
            "error": "background-color: #FAF0F0; color: #8E2424; border: 1px solid #DFC0C0;",
            "neutral": "background-color: #F7F6F2; color: #5C5A55; border: 1px solid #DFDDD6;",
        }
        base = styles.get(kind, styles["neutral"])
        self.lbl_scan_status.setStyleSheet(
            f"{base} border-radius: 5px; padding: 8px 11px; font-size: 12.5px; font-weight: 500;"
        )
        self.lbl_scan_status.setText(text)
        self.lbl_scan_status.show()



# ==============================================================================
# FINESTRA PRINCIPALE MACOS
# ==============================================================================
class MainWindow(QMainWindow):
    def __init__(self, settings: Optional[Settings] = None):
        super().__init__()
        self.settings = settings or Settings.load()
        self.lang = self.settings.language
        self.catalog = CatalogService(discogs_token=self.settings.discogs_token)
        self.thread_pool = QThreadPool.globalInstance()

        # Stato archivio per le 3 categorie
        self.current_category: str = "Libri"
        self.data_by_cat: Dict[str, List[Item]] = {c: [] for c in CATEGORIES}
        self.last_inserted_by_cat: Dict[str, Optional[Item]] = {c: None for c in CATEGORIES}
        self.dirty_by_cat: Dict[str, bool] = {c: False for c in CATEGORIES}
        self.files_by_cat: Dict[str, List[str]] = {c: [DEFAULT_FILES[c]] for c in CATEGORIES}
        self.active_file_by_cat: Dict[str, str] = {
            c: self.settings.active_files.get(c, DEFAULT_FILES[c]) for c in CATEGORIES
        }
        self.undo_stacks: Dict[str, QUndoStack] = {c: QUndoStack(self) for c in CATEGORIES}

        # Stato autocompletamento e controllo concorrenza richieste
        self._search_req_id = 0
        self._local_req_id = 0
        self._edition_req_id = 0
        self._active_workers: List[QRunnable] = []
        # Pool dedicato (1 thread) alla ricerca locale: non compete mai con le richieste di rete in corso
        self._local_pool = QThreadPool(self)
        self._local_pool.setMaxThreadCount(1)
        self._local_workers: List[QRunnable] = []
        self._retired_workers: List[QRunnable] = []
        self._local_hits: Optional[List[Suggestion]] = None
        self._online_hits: List[Suggestion] = []
        self._active_suggestion_field = "title"
        self._sort_manually_edited = False
        self._pub_manually_edited = False
        self._coll_manually_edited = False
        self._year_manually_edited = False
        self._selected_work_id = ""
        self._current_work_collections: List[str] = []
        self._current_bundle: Optional[EditionBundle] = None

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(75)
        self._debounce_timer.timeout.connect(self._trigger_online_autocomplete)

        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self._clear_status_message)

        # Servizio di scansione da Dispositivo Mobile e Fotocamera del Computer
        self.remote_scanner = RemoteScannerService(
            catalog=self.catalog,
            get_state_fn=lambda: (
                self.current_category,
                self.lang,
                {c: list(v) for c, v in self.data_by_cat.items()},
            ),
        )
        self.remote_scanner.set_connection_mode(
            self.settings.scanner_mode, self.settings.scanner_gh_pages_url
        )
        self.remote_scanner.online_enabled = self.settings.online_enabled
        self.remote_scanner.signals.categoryChanged.connect(self._on_remote_category_changed)
        self.remote_scanner.signals.itemPreviewed.connect(self._on_remote_item_previewed)
        self.remote_scanner.signals.itemAdded.connect(self._on_remote_item_added)
        self.remote_scanner.signals.undoRequested.connect(self.undo_last_action)
        self.remote_scanner.signals.scanLogEvent.connect(self._on_remote_scan_log)
        self._remote_dialog: Optional[RemoteScannerDialog] = None
        self._mac_cam_dialog: Optional[MacCameraScannerDialog] = None

        self._build_ui()
        self._build_menubar()
        self.setStyleSheet(MACOS_STYLESHEET)

        # Caricamento iniziale della cartella
        if not os.path.isdir(self.settings.folder):
            try:
                os.makedirs(self.settings.folder, exist_ok=True)
            except OSError:
                self.settings.folder = os.getcwd()
        self._load_all_from_folder(self.settings.folder)
        self._apply_language()
        self._switch_category("Libri", force=True)

        # Pre-avvia in background il server di scansione (rispettando la modalità scelta: es. GitHub Pages senza tunnel)
        QTimer.singleShot(250, self.remote_scanner.start)

    # ------------------------------------------------------------------
    # COSTRUZIONE INTERFACCIA GRAFICA MINIMALISTA
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        self.resize(1260, 780)
        self.setMinimumSize(1060, 640)

        central = QWidget(self)
        self.setCentralWidget(central)
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ================= 1. SIDEBAR SINISTRA =================
        self.sidebar = QFrame()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(210)
        sb_layout = QVBoxLayout(self.sidebar)
        sb_layout.setContentsMargins(15, 18, 15, 15)
        sb_layout.setSpacing(8)

        self.lbl_brand = QLabel(APP_NAME.upper())
        self.lbl_brand.setObjectName("AppBrand")
        sb_layout.addWidget(self.lbl_brand)

        self.lbl_sec_cat = QLabel("COLLEZIONI")
        self.lbl_sec_cat.setObjectName("SectionTitle")
        sb_layout.addWidget(self.lbl_sec_cat)

        self.cat_buttons: Dict[str, QPushButton] = {}
        cat_icon_kinds = {"Libri": "book", "CD": "cd", "Vinili": "vinyl"}
        for cat in CATEGORIES:
            btn = QPushButton(cat)
            btn.setProperty("class", "CatButton")
            btn.setIcon(_make_studio_icon(cat_icon_kinds[cat], "#262523", 15))
            btn.setIconSize(QSize(15, 15))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _checked=False, c=cat: self._switch_category(c))
            sb_layout.addWidget(btn)
            self.cat_buttons[cat] = btn

        sb_layout.addSpacing(8)

        # Card Foglio CSV attivo nella categoria
        self.lbl_sec_sheet = QLabel("FOGLIO ATTIVO")
        self.lbl_sec_sheet.setObjectName("SectionTitle")
        sb_layout.addWidget(self.lbl_sec_sheet)

        sheet_card = QFrame()
        sheet_card.setObjectName("SidebarCard")
        sc_layout = QVBoxLayout(sheet_card)
        sc_layout.setContentsMargins(10, 10, 10, 10)
        sc_layout.setSpacing(7)

        self.cb_sheet = CleanComboBox(editable=False)
        self.cb_sheet.activated.connect(self._on_sheet_selected)
        sc_layout.addWidget(self.cb_sheet)

        self.btn_new_sheet = QPushButton("+ Nuovo foglio vuoto")
        self.btn_new_sheet.clicked.connect(self._on_new_blank_sheet)
        sc_layout.addWidget(self.btn_new_sheet)
        sb_layout.addWidget(sheet_card)

        sb_layout.addSpacing(6)

        # Card Cartella Archivio
        self.lbl_sec_folder = QLabel("CARTELLA ARCHIVIO")
        self.lbl_sec_folder.setObjectName("SectionTitle")
        sb_layout.addWidget(self.lbl_sec_folder)

        folder_card = QFrame()
        folder_card.setObjectName("SidebarCard")
        fc_layout = QVBoxLayout(folder_card)
        fc_layout.setContentsMargins(10, 10, 10, 10)
        fc_layout.setSpacing(6)

        self.lbl_folder_path = QLabel()
        self.lbl_folder_path.setObjectName("MutedInfo")
        self.lbl_folder_path.setWordWrap(True)
        fc_layout.addWidget(self.lbl_folder_path)

        self.btn_change_folder = QPushButton("Cambia cartella…")
        self.btn_change_folder.clicked.connect(self._on_change_folder)
        fc_layout.addWidget(self.btn_change_folder)
        sb_layout.addWidget(folder_card)

        sb_layout.addStretch(1)

        # Indicatore stato Catalogo + Pulsante Impostazioni
        self.lbl_online_pill = QLabel()
        self.lbl_online_pill.setObjectName("MutedInfo")
        self.lbl_online_pill.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_online_pill.setWordWrap(True)
        sb_layout.addWidget(self.lbl_online_pill)

        self.btn_settings = QPushButton("Impostazioni…")
        self.btn_settings.setIcon(_make_studio_icon("sliders", "#262523", 14))
        self.btn_settings.setIconSize(QSize(14, 14))
        self.btn_settings.clicked.connect(self._open_settings_dialog)
        sb_layout.addWidget(self.btn_settings)

        root_layout.addWidget(self.sidebar)

        # ================= 2. AREA PRINCIPALE DESTRA =================
        main_pane = QWidget()
        mp_layout = QVBoxLayout(main_pane)
        mp_layout.setContentsMargins(20, 16, 20, 14)
        mp_layout.setSpacing(12)

        # --- 2A. Barra superiore (Titolo categoria + Ricerca istantanea + Salvataggio/PDF) ---
        top_bar = QHBoxLayout()
        top_bar.setSpacing(6)

        self.lbl_active_cat_header = QLabel("Libri")
        self.lbl_active_cat_header.setStyleSheet(
            "font-size: 21px; font-weight: 600; letter-spacing: 0.3px; color: #141413;"
        )
        self.lbl_active_cat_header.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        top_bar.addWidget(self.lbl_active_cat_header)

        top_bar.addSpacing(8)

        self.ed_search = QLineEdit()
        self.ed_search.setObjectName("SearchInput")
        self.ed_search.setClearButtonEnabled(True)
        self.ed_search.setMinimumWidth(110)
        self.ed_search.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.ed_search.textChanged.connect(self._on_filter_text_changed)
        top_bar.addWidget(self.ed_search, 1)

        top_bar.addSpacing(2)

        self.btn_save = QPushButton()
        self.btn_save.clicked.connect(self.save_current_category)
        top_bar.addWidget(self.btn_save)

        self.btn_save_as = QPushButton()
        self.btn_save_as.clicked.connect(self.save_current_as)
        top_bar.addWidget(self.btn_save_as)

        self.btn_save_all = QPushButton()
        self.btn_save_all.clicked.connect(self.save_all_categories)
        top_bar.addWidget(self.btn_save_all)

        self.btn_pdf_one = QPushButton()
        self.btn_pdf_one.clicked.connect(self.export_current_pdf)
        top_bar.addWidget(self.btn_pdf_one)

        for tb_btn in (
            self.btn_save,
            self.btn_save_as,
            self.btn_save_all,
            self.btn_pdf_one,
        ):
            tb_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        mp_layout.addLayout(top_bar)

        # --- 2B. Scheda di inserimento rapido (con Scansiona da telefono posizionato qui) ---
        entry_card = QFrame()
        entry_card.setProperty("class", "Card")
        ec_layout = QVBoxLayout(entry_card)
        ec_layout.setContentsMargins(16, 12, 16, 12)
        ec_layout.setSpacing(8)

        # Intestazione scheda di inserimento: titolo sezione + badge edizioni a sinistra,
        # Pulisci campi + Scansiona da telefono a destra
        entry_hdr = QHBoxLayout()
        entry_hdr.setContentsMargins(0, 0, 0, 2)
        entry_hdr.setSpacing(10)

        self.lbl_entry_overline = QLabel("NUOVO INSERIMENTO")
        self.lbl_entry_overline.setObjectName("CardOverline")
        entry_hdr.addWidget(self.lbl_entry_overline)

        self.lbl_edition_badge = QLabel("")
        self.lbl_edition_badge.setObjectName("EditionBadge")
        self.lbl_edition_badge.hide()
        entry_hdr.addWidget(self.lbl_edition_badge)

        entry_hdr.addStretch(1)

        self.btn_clear_form = QPushButton("Pulisci campi")
        self.btn_clear_form.setObjectName("GhostButton")
        self.btn_clear_form.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear_form.clicked.connect(self.reset_entry_form)
        entry_hdr.addWidget(self.btn_clear_form)

        self.btn_mac_cam = QPushButton()
        self.btn_mac_cam.setObjectName("ScanActionButton")
        self.btn_mac_cam.setIcon(_make_studio_icon("camera", "#141413", 14))
        self.btn_mac_cam.setIconSize(QSize(14, 14))
        self.btn_mac_cam.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_mac_cam.clicked.connect(self.open_mac_camera_dialog)
        entry_hdr.addWidget(self.btn_mac_cam)

        self.btn_remote_scan = QPushButton()
        self.btn_remote_scan.setObjectName("ScanActionButton")
        self.btn_remote_scan.setIcon(_make_studio_icon("scan", "#141413", 14))
        self.btn_remote_scan.setIconSize(QSize(14, 14))
        self.btn_remote_scan.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_remote_scan.clicked.connect(self.open_remote_scanner_dialog)
        entry_hdr.addWidget(self.btn_remote_scan)

        ec_layout.addLayout(entry_hdr)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)

        self.lbl_f_title = QLabel("Titolo")
        self.lbl_f_title.setProperty("class", "FieldLabel")
        self.lbl_f_author = QLabel("Autore")
        self.lbl_f_author.setProperty("class", "FieldLabel")
        self.lbl_f_sort = QLabel("Ordina come")
        self.lbl_f_sort.setProperty("class", "FieldLabel")
        self.lbl_f_pub = QLabel("Editore")
        self.lbl_f_pub.setProperty("class", "FieldLabel")
        self.lbl_f_coll = QLabel("Collana")
        self.lbl_f_coll.setProperty("class", "FieldLabel")
        self.lbl_f_year = QLabel("Anno")
        self.lbl_f_year.setProperty("class", "FieldLabel")

        self.in_title = QLineEdit()
        self.in_title.setClearButtonEnabled(True)
        self.in_author = QLineEdit()
        self.in_author.setClearButtonEnabled(True)
        self.in_sort = QLineEdit()
        self.in_sort.setObjectName("SortPreviewEdit")

        self.in_pub = CleanComboBox(editable=True)
        self.in_coll = CleanComboBox(editable=True)
        if self.in_coll.lineEdit():
            self.in_coll.lineEdit().setClearButtonEnabled(True)
        self.in_year = CleanComboBox(editable=True)
        self.in_year.setFixedWidth(130)

        self.btn_add = QPushButton("Aggiungi")
        self.btn_add.setObjectName("PrimaryButton")
        self.btn_add.setMinimumWidth(120)
        self.btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add.clicked.connect(self.add_item_from_form)

        # Layout su 2 righe spaziose e bilanciate per evitare qualsiasi troncamento dei campi
        # Riga 1: Titolo | Autore / Gruppo | Ordina come (span 2 colonne)
        grid.addWidget(self.lbl_f_title, 0, 0)
        grid.addWidget(self.lbl_f_author, 0, 1)
        grid.addWidget(self.lbl_f_sort, 0, 2, 1, 2)

        grid.addWidget(self.in_title, 1, 0)
        grid.addWidget(self.in_author, 1, 1)
        grid.addWidget(self.in_sort, 1, 2, 1, 2)

        # Riga 2: Editore / Etichetta | Collana / Collezione | Anno | [Aggiungi]
        grid.addWidget(self.lbl_f_pub, 2, 0)
        grid.addWidget(self.lbl_f_coll, 2, 1)
        grid.addWidget(self.lbl_f_year, 2, 2)

        grid.addWidget(self.in_pub, 3, 0)
        grid.addWidget(self.in_coll, 3, 1)
        grid.addWidget(self.in_year, 3, 2)
        grid.addWidget(self.btn_add, 3, 3)

        grid.setColumnStretch(0, 44)
        grid.setColumnStretch(1, 36)
        grid.setColumnStretch(2, 10)
        grid.setColumnStretch(3, 10)

        ec_layout.addLayout(grid)

        # Riga opzione sotto gli input (spunta raggruppamento collane)
        sub_entry = QHBoxLayout()
        sub_entry.setContentsMargins(0, 2, 0, 0)
        sub_entry.setSpacing(12)

        self.chk_group_coll = StudioCheckBox()
        self.chk_group_coll.setChecked(self.settings.group_by_collection)
        self.chk_group_coll.toggled.connect(self._on_group_coll_toggled)
        sub_entry.addWidget(self.chk_group_coll)

        self.lbl_form_hint = QLabel("")
        self.lbl_form_hint.setObjectName("MutedInfo")
        sub_entry.addWidget(self.lbl_form_hint)
        sub_entry.addStretch(1)

        ec_layout.addLayout(sub_entry)
        mp_layout.addWidget(entry_card)

        # --- 2C. Tabella Principale Virtualizzata ---
        self.table_model = ArchiveTableModel("Libri", self.lang, self)
        self.table_model.group_by_collection = self.settings.group_by_collection
        self.proxy_model = ArchiveProxyModel(self)
        self.proxy_model.group_by_collection = self.settings.group_by_collection
        self.proxy_model.setSourceModel(self.table_model)

        self.table = ArchiveTableView()
        self.table.setHorizontalHeader(CleanHeaderView(Qt.Orientation.Horizontal, self.table))
        self.table.setModel(self.proxy_model)
        self.table.setItemDelegate(ArchiveItemDelegate(self.table))
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(33)
        self.table.setShowGrid(False)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context_menu)
        self.table.doubleClicked.connect(self._on_table_double_clicked)

        hdr = self.table.horizontalHeader()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(ArchiveTableModel.COL_TITLE, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(ArchiveTableModel.COL_AUTHOR, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(ArchiveTableModel.COL_SORT, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(ArchiveTableModel.COL_PUB, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(ArchiveTableModel.COL_COLL, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(ArchiveTableModel.COL_YEAR, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(ArchiveTableModel.COL_AUTHOR, 160)
        self.table.setColumnWidth(ArchiveTableModel.COL_SORT, 136)
        self.table.setColumnWidth(ArchiveTableModel.COL_PUB, 130)
        self.table.setColumnWidth(ArchiveTableModel.COL_COLL, 155)
        self.table.setColumnWidth(ArchiveTableModel.COL_YEAR, 78)

        # Ordine predefinito: per Autore (cognome) crescente
        self.table.sortByColumn(ArchiveTableModel.COL_AUTHOR, Qt.SortOrder.AscendingOrder)
        mp_layout.addWidget(self.table, 1)

        # --- 2D. Barra inferiore (Azioni riga + Annulla/Ripristina + Contatori) ---
        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(8)

        self.btn_edit = QPushButton()
        self.btn_edit.clicked.connect(self.edit_selected_item)
        bottom_bar.addWidget(self.btn_edit)

        self.btn_dup = QPushButton()
        self.btn_dup.clicked.connect(self.duplicate_selected_item)
        bottom_bar.addWidget(self.btn_dup)

        self.btn_del = QPushButton()
        self.btn_del.setObjectName("DangerButton")
        self.btn_del.clicked.connect(self.delete_selected_items)
        bottom_bar.addWidget(self.btn_del)

        self.btn_clear_all = QPushButton()
        self.btn_clear_all.clicked.connect(self.clear_current_category)
        bottom_bar.addWidget(self.btn_clear_all)

        bottom_bar.addSpacing(12)

        self.btn_undo = QPushButton()
        self.btn_undo.clicked.connect(self.undo_last_action)
        bottom_bar.addWidget(self.btn_undo)

        self.btn_redo = QPushButton()
        self.btn_redo.clicked.connect(self.redo_last_action)
        bottom_bar.addWidget(self.btn_redo)

        bottom_bar.addStretch(1)

        self.lbl_count_summary = QLabel()
        self.lbl_count_summary.setObjectName("MutedInfo")
        bottom_bar.addWidget(self.lbl_count_summary)

        mp_layout.addLayout(bottom_bar)
        root_layout.addWidget(main_pane, 1)

        # Barra di stato inferiore dedicata (con notifiche temporanee e stato Pronto)
        sb = QStatusBar(self)
        sb.setSizeGripEnabled(True)
        self.setStatusBar(sb)
        sb.showMessage(tr(self.lang, "status_ready"))

        # Popup di autocompletamento
        self.popup = SuggestionPopup(self)
        self.popup.suggestionAccepted.connect(self._apply_suggestion)
        self.popup.dismissed.connect(self._dismiss_suggestions)

        # Collegamenti eventi campi di input
        self.in_title.textEdited.connect(lambda txt: self._on_query_edited("title", txt))
        self.in_author.textEdited.connect(self._on_author_field_edited)
        self.in_sort.textEdited.connect(self._on_sort_field_edited)
        self.in_pub.activated.connect(self._on_pub_choice_changed)
        self.in_coll.activated.connect(self._on_coll_choice_changed)
        self.in_year.activated.connect(self._on_year_choice_changed)
        if self.in_pub.lineEdit():
            self.in_pub.lineEdit().textEdited.connect(self._on_pub_edited)
            self.in_pub.lineEdit().editingFinished.connect(
                lambda: self._apply_publisher_changed(self.in_pub.currentText().strip())
            )
        if self.in_coll.lineEdit():
            self.in_coll.lineEdit().textEdited.connect(self._on_coll_edited)
        if self.in_year.lineEdit():
            self.in_year.lineEdit().textEdited.connect(self._on_year_edited)
            self.in_year.lineEdit().editingFinished.connect(
                lambda: self._apply_year_changed(self.in_year.currentText().strip())
            )

        self.in_title.installEventFilter(self)
        self.in_author.installEventFilter(self)
        self.in_sort.installEventFilter(self)
        self.in_pub.installEventFilter(self)
        self.in_coll.installEventFilter(self)
        self.in_year.installEventFilter(self)

        _disable_mac_focus_rect(self)

    def show_status_message(self, message: str, timeout: int = 4000) -> None:
        """Visualizza un messaggio nella barra di stato inferiore e ripristina «Pronto» alla scadenza."""
        self.statusBar().showMessage(message, 0)
        self._status_timer.stop()
        if timeout > 0:
            self._status_timer.start(timeout)

    def _clear_status_message(self) -> None:
        self.statusBar().showMessage(tr(self.lang, "status_ready"), 0)

    # ------------------------------------------------------------------
    # MENU BAR NATIVA MACOS E SCORCIATOIE DA TASTIERA
    # ------------------------------------------------------------------
    def _build_menubar(self) -> None:
        mb = self.menuBar()
        mb.clear()

        # Menu File
        m_file = mb.addMenu(tr(self.lang, "menu_file"))

        act_new_item = QAction("Nuovo inserimento" if self.lang == "it" else "New Entry", self)
        act_new_item.setShortcut(QKeySequence("Ctrl+N"))
        act_new_item.triggered.connect(lambda: (self.reset_entry_form(), self.in_title.setFocus()))
        m_file.addAction(act_new_item)

        act_mac_cam = QAction(
            "Scansiona con Fotocamera del computer…" if self.lang == "it" else "Scan with Computer Camera…",
            self,
        )
        act_mac_cam.setShortcut(QKeySequence("Ctrl+Shift+B"))
        act_mac_cam.triggered.connect(self.open_mac_camera_dialog)
        m_file.addAction(act_mac_cam)

        act_remote_scan = QAction(
            "Scansiona da Dispositivo Mobile…" if self.lang == "it" else "Scan from Mobile Device…",
            self,
        )
        act_remote_scan.setShortcut(QKeySequence("Ctrl+B"))
        act_remote_scan.triggered.connect(self.open_remote_scanner_dialog)
        m_file.addAction(act_remote_scan)

        act_open_folder = QAction(tr(self.lang, "btn_change_folder"), self)
        act_open_folder.setShortcut(QKeySequence("Ctrl+O"))
        act_open_folder.triggered.connect(self._on_change_folder)
        m_file.addAction(act_open_folder)

        m_file.addSeparator()

        act_save = QAction(tr(self.lang, "btn_save"), self)
        act_save.setShortcut(QKeySequence.StandardKey.Save)
        act_save.triggered.connect(self.save_current_category)
        m_file.addAction(act_save)

        act_save_as = QAction(tr(self.lang, "btn_save_as"), self)
        act_save_as.setShortcut(QKeySequence.StandardKey.SaveAs)
        act_save_as.triggered.connect(self.save_current_as)
        m_file.addAction(act_save_as)

        act_save_all = QAction(tr(self.lang, "btn_save_all"), self)
        act_save_all.setShortcut(QKeySequence("Ctrl+Alt+S"))
        act_save_all.triggered.connect(self.save_all_categories)
        m_file.addAction(act_save_all)

        m_file.addSeparator()

        act_pdf_one = QAction(tr(self.lang, "btn_export_pdf"), self)
        act_pdf_one.setShortcut(QKeySequence("Ctrl+P"))
        act_pdf_one.triggered.connect(self.export_current_pdf)
        m_file.addAction(act_pdf_one)

        # Voce Preferenze / Impostazioni (su macOS finisce automaticamente nel menu Applicazione con ⌘,)
        act_prefs = QAction(tr(self.lang, "btn_settings"), self)
        act_prefs.setShortcut(QKeySequence.StandardKey.Preferences)
        act_prefs.setMenuRole(QAction.MenuRole.PreferencesRole)
        act_prefs.triggered.connect(self._open_settings_dialog)
        m_file.addAction(act_prefs)

        # Menu Modifica
        m_edit = mb.addMenu(tr(self.lang, "menu_edit"))

        act_undo = QAction(tr(self.lang, "btn_undo"), self)
        act_undo.setShortcut(QKeySequence.StandardKey.Undo)
        act_undo.triggered.connect(self.undo_last_action)
        m_edit.addAction(act_undo)

        act_redo = QAction(tr(self.lang, "btn_redo"), self)
        act_redo.setShortcut(QKeySequence.StandardKey.Redo)
        act_redo.triggered.connect(self.redo_last_action)
        m_edit.addAction(act_redo)

        m_edit.addSeparator()

        act_find = QAction(tr(self.lang, "search_placeholder"), self)
        act_find.setShortcut(QKeySequence.StandardKey.Find)
        act_find.triggered.connect(lambda: (self.ed_search.setFocus(), self.ed_search.selectAll()))
        m_edit.addAction(act_find)

        act_edit_row = QAction(tr(self.lang, "btn_edit"), self)
        act_edit_row.setShortcut(QKeySequence("Ctrl+E"))
        act_edit_row.triggered.connect(self.edit_selected_item)
        m_edit.addAction(act_edit_row)

        act_assign_coll = QAction(tr(self.lang, "ctx_assign_coll"), self)
        act_assign_coll.setShortcut(QKeySequence("Ctrl+G"))
        act_assign_coll.triggered.connect(self.assign_collection_to_selected)
        m_edit.addAction(act_assign_coll)

        act_del_row = QAction(tr(self.lang, "btn_delete"), self)
        act_del_row.setShortcut(QKeySequence("Ctrl+Backspace"))
        act_del_row.triggered.connect(self.delete_selected_items)
        m_edit.addAction(act_del_row)

        # Menu Vista (Passaggio rapido ⌘1 Libri, ⌘2 CD, ⌘3 Vinili)
        m_view = mb.addMenu(tr(self.lang, "menu_view"))
        for idx, cat in enumerate(CATEGORIES, start=1):
            act_cat = QAction(tr(self.lang, f"cat_{cat}"), self)
            act_cat.setShortcut(QKeySequence(f"Ctrl+{idx}"))
            act_cat.triggered.connect(lambda _c=False, c=cat: self._switch_category(c))
            m_view.addAction(act_cat)

    # ------------------------------------------------------------------
    # TRADUZIONI E AGGIORNAMENTO ETICHETTE LIVE
    # ------------------------------------------------------------------
    def _apply_language(self) -> None:
        self.lang = self.settings.language
        self.setWindowTitle(tr(self.lang, "app_title"))
        self.lbl_brand.setText(tr(self.lang, "app_title").upper())
        self.lbl_sec_cat.setText("COLLEZIONI" if self.lang == "it" else "COLLECTIONS")
        self.lbl_sec_sheet.setText(tr(self.lang, "sidebar_files").upper())
        self.lbl_sec_folder.setText(tr(self.lang, "sidebar_folder").upper())

        self.btn_new_sheet.setText(f"+ {tr(self.lang, 'btn_new_sheet')}")
        self.btn_change_folder.setText(tr(self.lang, "btn_change_folder"))
        self.btn_settings.setText(f"  {tr(self.lang, 'btn_settings')}")

        self.ed_search.setPlaceholderText(tr(self.lang, "search_placeholder"))
        self.btn_save.setText(tr(self.lang, "btn_save"))
        self.btn_save_as.setText(tr(self.lang, "btn_save_as"))
        self.btn_save_all.setText(tr(self.lang, "btn_save_all"))
        self.btn_pdf_one.setText(tr(self.lang, "btn_export_pdf"))

        self.lbl_entry_overline.setText(tr(self.lang, "entry_section_title").upper())
        self.btn_mac_cam.setText(f"  {tr(self.lang, 'btn_mac_cam')}")
        self.btn_remote_scan.setText(f"  {tr(self.lang, 'btn_remote_scan')}")

        # Garantisce che nessun pulsante della barra superiore o dello scanner venga mai troncato né sovrapposto
        from PySide6.QtGui import QFontMetrics
        fam = _resolve_ui_font_family()
        btn_font = QFont(fam)
        btn_font.setPixelSize(13)
        btn_font.setWeight(QFont.Weight.Medium)
        fm_exact = QFontMetrics(btn_font)
        for tb_btn in (
            self.btn_save,
            self.btn_save_as,
            self.btn_save_all,
            self.btn_pdf_one,
        ):
            w_txt = fm_exact.horizontalAdvance(tb_btn.text())
            tb_btn.setMinimumWidth(w_txt + 24)

        scan_font = QFont(fam)
        scan_font.setPixelSize(12)
        scan_font.setWeight(QFont.Weight.DemiBold)
        fm_scan = QFontMetrics(scan_font)
        self.btn_mac_cam.setMinimumWidth(fm_scan.horizontalAdvance(self.btn_mac_cam.text()) + 42)
        self.btn_remote_scan.setMinimumWidth(fm_scan.horizontalAdvance(self.btn_remote_scan.text()) + 42)

        if not self._status_timer.isActive():
            self.statusBar().showMessage(tr(self.lang, "status_ready"), 0)

        self.btn_add.setText(tr(self.lang, "btn_add"))
        self.chk_group_coll.setText(tr(self.lang, "chk_group_coll"))
        self.chk_group_coll.setToolTip(tr(self.lang, "chk_group_coll_tip"))
        self.btn_clear_form.setText("Pulisci campi" if self.lang == "it" else "Clear fields")
        self.btn_edit.setText(tr(self.lang, "btn_edit"))
        self.btn_dup.setText(tr(self.lang, "btn_duplicate"))
        self.btn_del.setText(tr(self.lang, "btn_delete"))
        self.btn_clear_all.setText(tr(self.lang, "btn_clear"))
        self.btn_undo.setText(tr(self.lang, "btn_undo"))
        self.btn_redo.setText(tr(self.lang, "btn_redo"))

        self._update_category_dependent_labels()
        self._update_sidebar_badges()
        self._update_online_indicator()
        self.table_model.set_language(self.lang)
        self._build_menubar()
        cw = self.centralWidget()
        if cw is not None and cw.layout() is not None:
            cw.layout().activate()
            self.setMinimumWidth(max(1060, cw.minimumSizeHint().width()))

    def _update_category_dependent_labels(self) -> None:
        cat = self.current_category
        is_book = cat == "Libri"
        self.lbl_active_cat_header.setText(tr(self.lang, f"cat_{cat}"))

        self.lbl_f_title.setText(tr(self.lang, "col_title"))
        self.lbl_f_author.setText(tr(self.lang, "col_author_book") if is_book else tr(self.lang, "col_author_music"))
        self.lbl_f_sort.setText(tr(self.lang, "col_sort"))
        self.lbl_f_pub.setText(tr(self.lang, "col_pub_book") if is_book else tr(self.lang, "col_pub_music"))
        self.lbl_f_coll.setText(tr(self.lang, "col_coll_book") if is_book else tr(self.lang, "col_coll_music"))
        self.lbl_f_year.setText(tr(self.lang, "col_year"))

        self.in_title.setPlaceholderText(tr(self.lang, "ph_title_book") if is_book else tr(self.lang, "ph_title_music"))
        self.in_author.setPlaceholderText(tr(self.lang, "ph_author_book") if is_book else tr(self.lang, "ph_author_music"))
        self.in_sort.setPlaceholderText(tr(self.lang, "ph_sort"))
        self.in_sort.setToolTip(tr(self.lang, "sort_override_hint"))
        if self.in_pub.lineEdit():
            self.in_pub.lineEdit().setPlaceholderText(tr(self.lang, "ph_pub_book") if is_book else tr(self.lang, "ph_pub_music"))
        if self.in_coll.lineEdit():
            self.in_coll.lineEdit().setPlaceholderText(tr(self.lang, "ph_coll_book") if is_book else tr(self.lang, "ph_coll_music"))
        if self.in_year.lineEdit():
            self.in_year.lineEdit().setPlaceholderText(tr(self.lang, "ph_year"))

    def _on_group_coll_toggled(self, checked: bool) -> None:
        """
        Attiva o disattiva il raggruppamento a parte delle Collane / Collezioni.
        - Se attivo (default): i volumi di una stessa Collana vanno vicini a parte, ordinati tra loro.
        - Se disattivato: i volumi tornano al normale ordine alfabetico generale.
        """
        self.settings.group_by_collection = bool(checked)
        self.settings.save()
        self.proxy_model.set_group_by_collection(bool(checked))

    def _refresh_collection_choices(
        self,
        preserve_current: bool = False,
        explicit_current: Optional[str] = None,
    ) -> None:
        """
        Aggiorna il menu a tendina delle Collane / Collezioni unendo in ordine di priorità:
        1) Le collane specifiche dell'opera/ISBN correntemente selezionata (da OPAC SBN / Open Library / Google Books)
        2) Le collane ufficiali della casa editrice / etichetta attualmente selezionata in Editore
        3) Le collane già presenti nell'archivio dell'utente per la categoria attiva
        """
        items = self.data_by_cat.get(self.current_category, [])
        active_pub = self.in_pub.currentText().strip()
        active_author = self.in_author.text().strip()
        active_title = self.in_title.text().strip()
        # Dall'archivio dell'utente propone solo collane di edizioni pertinenti:
        # se Titolo è noto, solo voci dello stesso titolo (+ autore/editore se presenti);
        # altrimenti per i Libri quelle dello stesso editore (mai collezioni di dischi/libri non correlati).
        if active_title:
            title_k = search_fold(active_title)
            auth_k = search_fold(active_author)
            pub_k = search_fold(active_pub)
            rel_items = [
                i
                for i in items
                if search_fold(i.title) == title_k and (not auth_k or search_fold(i.author) == auth_k)
            ]
            if pub_k:
                pub_matched = [i for i in rel_items if search_fold(i.publisher) == pub_k]
                if pub_matched:
                    rel_items = pub_matched
        elif self.current_category == "Libri" and active_pub:
            pub_k = search_fold(active_pub)
            rel_items = [i for i in items if search_fold(i.publisher) == pub_k]
        else:
            rel_items = []
        archive_colls = unique_collections(rel_items)
        work_colls = _filter_colls_for_publisher(
            self._current_work_collections, active_pub, self._current_bundle
        )
        # Suggerisce collane generiche dell'editore/artista solo se non abbiamo già edizioni reali dell'opera
        if self.current_category == "Libri":
            pub_colls = (
                self.catalog.collections_for_publisher(active_pub, self.current_category, active_author)
                if (active_pub and not work_colls)
                else []
            )
        else:
            pub_colls = (
                self.catalog.collections_for_publisher(active_pub, self.current_category, active_author)
                if (not self._current_bundle or not work_colls)
                else []
            )

        # Se l'utente ha scelto/modificato l'Editore, le collane di quell'Editore
        # vanno in cima al menu a tendina; altrimenti vengono prima le collane specifiche dell'ISBN/opera
        if self._pub_manually_edited and pub_colls:
            source_groups = (pub_colls, work_colls, archive_colls)
        else:
            source_groups = (work_colls, pub_colls, archive_colls)

        merged_colls: List[str] = []
        seen_low = set()
        for src_list in source_groups:
            for c in src_list:
                cs = (c or "").strip()
                if cs and cs.lower() not in seen_low:
                    seen_low.add(cs.lower())
                    merged_colls.append(cs)

        if explicit_current is not None:
            cur_txt = explicit_current.strip()
        elif preserve_current:
            cur_txt = self.in_coll.currentText().strip()
        else:
            cur_txt = ""

        self.in_coll.set_choices(
            merged_colls,
            current_text=cur_txt,
            preserve_user_text=self._coll_manually_edited and preserve_current,
        )

    def _update_online_indicator(self) -> None:
        raw = tr(self.lang, "status_online" if self.settings.online_enabled else "status_offline")
        lines = raw.split("\n", 1)
        head = lines[0]
        sub = f"<br><span style='color:#8A8882; font-size:10.5px;'>{lines[1]}</span>" if len(lines) > 1 else ""
        dot_color = "#2E6F40" if self.settings.online_enabled else "#8A8882"
        dot_char = "●" if self.settings.online_enabled else "○"
        self.lbl_online_pill.setText(
            f"<span style='color:{dot_color}; font-size:10px;'>{dot_char}</span>&nbsp;&nbsp;"
            f"<span style='color:#3A3936; font-weight:500;'>{head}</span>{sub}"
        )

    def _update_sidebar_badges(self) -> None:
        cat_icon_kinds = {"Libri": "book", "CD": "cd", "Vinili": "vinyl"}
        for cat, btn in self.cat_buttons.items():
            cnt = len(self.data_by_cat.get(cat, []))
            dirty_dot = "  ·" if self.dirty_by_cat.get(cat) else ""
            is_act = cat == self.current_category
            label = f"  {tr(self.lang, f'cat_{cat}')} ({cnt}){dirty_dot}"
            btn.setText(label)
            btn.setIcon(_make_studio_icon(cat_icon_kinds[cat], "#FFFFFF" if is_act else "#262523", 15))
            btn.setProperty("active", "true" if is_act else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

        # Aggiorna riepilogo e stato undo/redo
        total_cat = len(self.data_by_cat.get(self.current_category, []))
        shown = self.proxy_model.rowCount()
        if shown == total_cat:
            self.lbl_count_summary.setText(
                f"{total_cat} elementi" if self.lang == "it" else f"{total_cat} items"
            )
        else:
            self.lbl_count_summary.setText(
                f"{shown} di {total_cat} elementi" if self.lang == "it" else f"{shown} of {total_cat} items"
            )

        stack = self.undo_stacks[self.current_category]
        self.btn_undo.setEnabled(stack.canUndo())
        self.btn_redo.setEnabled(stack.canRedo())

    # ------------------------------------------------------------------
    # GESTIONE CARTELLA E FOGLI CSV
    # ------------------------------------------------------------------
    def _load_all_from_folder(self, folder: str) -> None:
        self.settings.folder = folder
        self.lbl_folder_path.setText(folder)
        scanned = scan_folder(folder)

        for cat in CATEGORIES:
            avail = scanned.get(cat) or [DEFAULT_FILES[cat]]
            self.files_by_cat[cat] = avail
            preferred = self.settings.active_files.get(cat, DEFAULT_FILES[cat])
            chosen = preferred if preferred in avail else avail[0]
            self.active_file_by_cat[cat] = chosen
            self.settings.active_files[cat] = chosen

            full_path = os.path.join(folder, chosen)
            self.data_by_cat[cat] = load_csv(full_path, cat)
            self.dirty_by_cat[cat] = False
            self.undo_stacks[cat].clear()

        self.settings.save()
        self._refresh_sheet_combo()
        self._update_sidebar_badges()

    def _refresh_sheet_combo(self) -> None:
        cat = self.current_category
        avail = self.files_by_cat.get(cat) or [DEFAULT_FILES[cat]]
        active = self.active_file_by_cat.get(cat, avail[0])
        self.cb_sheet.set_choices(avail, active)

    def _switch_category(self, category: str, force: bool = False) -> None:
        if category not in CATEGORIES:
            return
        if category == self.current_category and not force:
            return

        # CRITICO: al cambio tra Libri / CD / Vinili annulla ogni ricerca pendente
        # e azzera completamente i campi di compilazione per non lasciare residui!
        self._search_req_id += 1
        self._edition_req_id += 1
        self._debounce_timer.stop()
        self.popup.hide()

        self.current_category = category
        self.reset_entry_form()
        self._update_category_dependent_labels()
        self._refresh_sheet_combo()

        self.table_model.set_category_and_items(
            category, self.data_by_cat.get(category, []), self.lang
        )
        last = self.last_inserted_by_cat.get(category)
        if last not in self.data_by_cat.get(category, []):
            last = None
            self.last_inserted_by_cat[category] = None
        self.table_model.set_last_inserted_item(last)
        self._update_sidebar_badges()
        self.table.clearSelection()
        if self.table.selectionModel():
            self.table.selectionModel().clearCurrentIndex()

    def _on_sheet_selected(self, index: int) -> None:
        chosen = self.cb_sheet.itemText(index).strip()
        if not chosen:
            return
        cat = self.current_category
        if chosen == self.active_file_by_cat.get(cat):
            return
        if self.dirty_by_cat.get(cat):
            res = QMessageBox.question(
                self,
                tr(self.lang, "app_title"),
                tr(self.lang, "msg_unsaved_quit"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            )
            if res == QMessageBox.StandardButton.Cancel:
                self._refresh_sheet_combo()
                return
            if res == QMessageBox.StandardButton.Yes:
                self.save_current_category()

        self.active_file_by_cat[cat] = chosen
        self.settings.active_files[cat] = chosen
        self.settings.save()
        full_path = os.path.join(self.settings.folder, chosen)
        self.data_by_cat[cat] = load_csv(full_path, cat)
        self.dirty_by_cat[cat] = False
        self.last_inserted_by_cat[cat] = None
        self.undo_stacks[cat].clear()
        self.reset_entry_form()
        self.table_model.set_category_and_items(cat, self.data_by_cat[cat], self.lang)
        self.table_model.set_last_inserted_item(None)
        self._refresh_sheet_combo()
        self._update_sidebar_badges()
        self.table.clearSelection()
        if self.table.selectionModel():
            self.table.selectionModel().clearCurrentIndex()

    def _on_new_blank_sheet(self) -> None:
        cat = self.current_category
        dlg = SaveAsCategoryDialog(cat, self.lang, self.settings.folder, self)
        dlg.setWindowTitle(tr(self.lang, "btn_new_sheet"))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        fn = dlg.filename()
        full_path = os.path.join(self.settings.folder, fn)
        save_csv(full_path, [])
        if fn not in self.files_by_cat[cat]:
            self.files_by_cat[cat].append(fn)
            self.files_by_cat[cat].sort()
        self.active_file_by_cat[cat] = fn
        self.settings.active_files[cat] = fn
        self.settings.save()
        self.data_by_cat[cat] = []
        self.dirty_by_cat[cat] = False
        self.last_inserted_by_cat[cat] = None
        self.undo_stacks[cat].clear()
        self.reset_entry_form()
        self.table_model.set_category_and_items(cat, [], self.lang)
        self.table_model.set_last_inserted_item(None)
        self._refresh_sheet_combo()
        self._update_sidebar_badges()
        self.table.clearSelection()
        if self.table.selectionModel():
            self.table.selectionModel().clearCurrentIndex()

    def _on_change_folder(self) -> None:
        if any(self.dirty_by_cat.values()):
            res = QMessageBox.question(
                self,
                tr(self.lang, "app_title"),
                tr(self.lang, "msg_unsaved_quit"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            )
            if res == QMessageBox.StandardButton.Cancel:
                return
            if res == QMessageBox.StandardButton.Yes:
                self.save_all_categories()

        chosen_dir = QFileDialog.getExistingDirectory(
            self, tr(self.lang, "btn_change_folder"), self.settings.folder
        )
        if not chosen_dir:
            return
        self.last_inserted_by_cat = {c: None for c in CATEGORIES}
        self.table_model.set_last_inserted_item(None)
        self._load_all_from_folder(chosen_dir)
        self._switch_category(self.current_category, force=True)

    # ------------------------------------------------------------------
    # AUTOCOMPLETAMENTO IBRIDO E COMPILAZIONE MULTI-EDIZIONE (LIBRI / CD / VINILI)
    # ------------------------------------------------------------------
    def reset_entry_form(self) -> None:
        """Azzera tutti i campi di input e i menu a tendina delle edizioni."""
        self._search_req_id += 1
        self._edition_req_id += 1
        self._debounce_timer.stop()
        self.popup.hide()

        self._sort_manually_edited = False
        self._pub_manually_edited = False
        self._coll_manually_edited = False
        self._year_manually_edited = False
        self._selected_work_id = ""
        self._current_work_collections: List[str] = []
        self._current_bundle = None

        self.in_title.blockSignals(True)
        self.in_author.blockSignals(True)
        self.in_sort.blockSignals(True)
        try:
            self.in_title.clear()
            self.in_author.clear()
            self.in_sort.clear()
            self.in_title.deselect()
            self.in_author.deselect()
            self.in_sort.deselect()
        finally:
            self.in_title.blockSignals(False)
            self.in_author.blockSignals(False)
            self.in_sort.blockSignals(False)

        self.in_pub.set_choices([], "")
        self.in_year.set_choices([], "")
        self._refresh_collection_choices(preserve_current=False)
        self.lbl_edition_badge.hide()
        self.lbl_form_hint.clear()

    def _on_author_field_edited(self, text: str) -> None:
        if not self._sort_manually_edited:
            self.in_sort.setText(surname_key(text, self.current_category))
            self.in_sort.deselect()
        self._refresh_collection_choices(preserve_current=True)
        self._on_query_edited("author", text)

    def _on_sort_field_edited(self, _text: str) -> None:
        self._sort_manually_edited = True

    def _apply_publisher_changed(self, pub_txt: str) -> None:
        """
        Quando l'utente seleziona o inserisce un Editore / Etichetta:
        1) Aggiorna l'Anno alla prima data nota per quell'editore.
        2) Aggiorna la Collana a quella specifica dell'edizione di quell'editore,
           oppure se la collana attuale appartiene a un altro editore la rimuove ("o la toglie").
        """
        p_clean = (pub_txt or "").strip()
        self._pub_manually_edited = True
        if not p_clean:
            self._refresh_collection_choices(preserve_current=True)
            return

        cur_coll = self.in_coll.currentText().strip()
        resolved_yr, resolved_coll, should_clear_coll = self.catalog.resolve_publisher_metadata(
            p_clean,
            category=self.current_category,
            author=self.in_author.text().strip(),
            bundle=self._current_bundle,
            current_collection=cur_coll,
        )

        # 1) Aggiorna l'anno se trovato per questo editore
        if resolved_yr:
            existing_yrs = [self.in_year.itemText(i) for i in range(self.in_year.count()) if self.in_year.itemText(i).strip()]
            self.in_year.set_choices(existing_yrs, resolved_yr)
            self._year_manually_edited = True

        # 2) Gestione collana: se l'attuale collana apparteneva a un altro editore o è incompatibile, la toglie
        target_coll = None
        if should_clear_coll:
            target_coll = resolved_coll or ""
        elif resolved_coll and (not cur_coll or (not self._coll_manually_edited and resolved_coll != cur_coll)):
            target_coll = resolved_coll

        if target_coll is not None:
            self._refresh_collection_choices(preserve_current=False, explicit_current=target_coll)
        else:
            self._refresh_collection_choices(preserve_current=True)

    def _on_pub_edited(self, text: str) -> None:
        self._pub_manually_edited = True
        t_clean = text.strip()
        if t_clean and self.in_pub.findText(t_clean, Qt.MatchFlag.MatchFixedString) >= 0:
            self._apply_publisher_changed(t_clean)
        else:
            self._refresh_collection_choices(preserve_current=True)

    def _on_pub_choice_changed(self, _idx: int) -> None:
        pub_txt = self.in_pub.currentText().strip()
        self._apply_publisher_changed(pub_txt)

    def _apply_year_changed(self, yr_txt: str) -> None:
        """
        Quando l'utente seleziona o inserisce un Anno:
        1) Aggiorna la Casa Editrice / Etichetta che ha pubblicato l'opera in quell'anno.
        2) Aggiorna la Collana dell'edizione di quell'anno, oppure se la collana attuale
           era di un altro anno/editore la rimuove ("o la toglie").
        """
        y_clean = (yr_txt or "").strip()
        self._year_manually_edited = True
        if not y_clean or len(y_clean) < 4:
            return

        cur_coll = self.in_coll.currentText().strip()
        cur_pub = self.in_pub.currentText().strip()
        resolved_pub, resolved_coll, should_clear_coll = self.catalog.resolve_year_metadata(
            y_clean,
            category=self.current_category,
            author=self.in_author.text().strip(),
            bundle=self._current_bundle,
            current_collection=cur_coll,
            current_publisher=cur_pub,
        )

        # 1) Aggiorna l'editore se trovato per quest'anno
        if resolved_pub:
            existing_pubs = [self.in_pub.itemText(i) for i in range(self.in_pub.count()) if self.in_pub.itemText(i).strip()]
            self.in_pub.set_choices(existing_pubs, resolved_pub)
            self._pub_manually_edited = True

        # 2) Gestione collana per quest'anno
        target_coll = None
        if should_clear_coll:
            target_coll = resolved_coll or ""
        elif resolved_coll and (not cur_coll or (not self._coll_manually_edited and resolved_coll != cur_coll)):
            target_coll = resolved_coll

        if target_coll is not None:
            self._refresh_collection_choices(preserve_current=False, explicit_current=target_coll)
        elif resolved_pub:
            self._refresh_collection_choices(preserve_current=True)

    def _on_year_choice_changed(self, _idx: int) -> None:
        yr_txt = self.in_year.currentText().strip()
        self._apply_year_changed(yr_txt)

    def _on_year_edited(self, text: str) -> None:
        self._year_manually_edited = True
        t_clean = text.strip()
        if len(t_clean) == 4 and t_clean.isdigit():
            self._apply_year_changed(t_clean)

    def _apply_collection_metadata(
        self,
        coll_txt: str,
        overwrite_pub: bool = True,
        overwrite_year: bool = True,
    ) -> None:
        """
        Quando l'utente seleziona una Collana / Collezione, aggiorna automaticamente
        Editore / Casa Discografica e Anno di uscita (se noti per quella specifica collana/edizione).
        """
        c_clean = (coll_txt or "").strip()
        if not c_clean:
            return
        pub_c, yr_c = self.catalog.resolve_collection_metadata(
            c_clean,
            self.current_category,
            self.in_author.text().strip(),
            self._current_bundle,
        )
        if pub_c and (overwrite_pub or not self.in_pub.currentText().strip()):
            existing_pubs = [self.in_pub.itemText(i) for i in range(self.in_pub.count()) if self.in_pub.itemText(i).strip()]
            self.in_pub.set_choices(existing_pubs, pub_c)
            self._pub_manually_edited = True
        if yr_c and (overwrite_year or not self.in_year.currentText().strip()):
            existing_yrs = [self.in_year.itemText(i) for i in range(self.in_year.count()) if self.in_year.itemText(i).strip()]
            self.in_year.set_choices(existing_yrs, yr_c)
            self._year_manually_edited = True

    def _on_coll_choice_changed(self, _idx: int) -> None:
        self._coll_manually_edited = True
        self._apply_collection_metadata(self.in_coll.currentText().strip())

    def _on_coll_edited(self, text: str) -> None:
        self._coll_manually_edited = True
        # Se il testo digitato coincide esattamente con una delle collane note nel menu, applica i metadati
        t_clean = text.strip()
        if t_clean and self.in_coll.findText(t_clean, Qt.MatchFlag.MatchFixedString) >= 0:
            self._apply_collection_metadata(t_clean)

    def _dismiss_suggestions(self) -> None:
        """
        Chiude subito la tendina dei suggerimenti e invalida ogni ricerca ancora in corso,
        così nessun risultato tardivo (locale o online) la fa riapparire da sola.
        """
        self._search_req_id += 1
        self._local_req_id += 1
        self._debounce_timer.stop()
        self._local_hits = None
        self._online_hits = []
        self.popup.hide()

    def _suggestion_context(self, field_mode: str) -> Tuple[str, str]:
        """(autore di contesto, titolo di contesto) usati per filtrare i suggerimenti dell'altro campo."""
        ctx_author = self.in_author.text().strip() if field_mode == "title" else ""
        ctx_title = self.in_title.text().strip() if field_mode == "author" else ""
        return ctx_author, ctx_title

    def _on_query_edited(self, field_mode: str, text: str) -> None:
        self._active_suggestion_field = field_mode
        self._search_req_id += 1
        self._local_req_id += 1
        self._local_hits = None
        self._online_hits = []
        q = text.strip()
        ctx_author, ctx_title = self._suggestion_context(field_mode)

        # Se l'utente modifica il Titolo mentre Artista/Autore è già compilato,
        # mostriamo subito i suggerimenti filtrati per quell'artista anche con 0 o 1 carattere!
        has_author_ctx = field_mode == "title" and len(ctx_author) >= 2
        if not has_author_ctx and len(q) < 2:
            self._debounce_timer.stop()
            self.popup.hide()
            return

        # 1) Risposta locale (Archivio + Seed Index + SQLite Cache) calcolata in un thread dedicato:
        #    il campo di testo resta sempre reattivo, anche con titoli molto lunghi e archivi grandi.
        worker = LocalSearchWorker(
            catalog=self.catalog,
            req_id=self._local_req_id,
            category=self.current_category,
            query=q,
            lang=self.lang,
            field_mode=field_mode,
            archive_items=list(self.data_by_cat.get(self.current_category, [])),
            is_cancelled_fn=lambda rid: rid != self._local_req_id,
            context_author=ctx_author,
            context_title=ctx_title,
        )
        worker.signals.finished.connect(self._on_local_search_finished)
        worker.signals.completed.connect(self._on_main_worker_completed)
        self._local_workers.append(worker)
        self._local_pool.start(worker)

        # 2) Pianifica arricchimento online debounced (75ms) se attivo
        if self.settings.online_enabled and (len(q) >= 2 or has_author_ctx):
            self._debounce_timer.start()
        else:
            self._debounce_timer.stop()

    def _suggestion_anchor(self, field_mode: str) -> QLineEdit:
        return self.in_title if field_mode == "title" else self.in_author

    def _show_merged_suggestions(self, field_mode: str) -> None:
        """Fonde risultati locali e online correnti (copie, per non alterare gli originali) e aggiorna la tendina."""
        merged = _merge_ranked_suggestions(self.catalog, self._local_hits, self._online_hits)
        if merged:
            self.popup.show_suggestions(self._suggestion_anchor(field_mode), merged, self.lang)
        elif self._local_hits is not None and not self._online_hits:
            self.popup.hide()

    def _on_local_search_finished(
        self, req_id: int, category: str, field_mode: str, local_hits: List[Suggestion]
    ) -> None:
        if req_id != self._local_req_id or category != self.current_category:
            return
        if not self._suggestion_anchor(field_mode).hasFocus():
            return
        self._local_hits = list(local_hits or [])
        self._show_merged_suggestions(field_mode)

    def _trigger_online_autocomplete(self) -> None:
        if not self.settings.online_enabled:
            return
        field_mode = self._active_suggestion_field
        widget = self._suggestion_anchor(field_mode)
        if not widget.hasFocus():
            return
        q = widget.text().strip()
        ctx_author, ctx_title = self._suggestion_context(field_mode)
        has_author_ctx = field_mode == "title" and len(ctx_author) >= 2
        if not has_author_ctx and len(q) < 2:
            return

        self._search_req_id += 1
        req_id = self._search_req_id
        worker = OnlineSearchWorker(
            catalog=self.catalog,
            req_id=req_id,
            category=self.current_category,
            query=q,
            lang=self.lang,
            field_mode=field_mode,
            is_cancelled_fn=lambda rid: rid != self._search_req_id,
            context_author=ctx_author,
            context_title=ctx_title,
        )
        worker.signals.chunk.connect(self._on_online_search_finished)
        worker.signals.finished.connect(self._on_online_search_finished)
        worker.signals.completed.connect(self._on_main_worker_completed)
        self._active_workers.append(worker)
        self.thread_pool.start(worker)

    def _on_online_search_finished(
        self, req_id: int, category: str, field_mode: str, online_items: List[Suggestion]
    ) -> None:
        if req_id != self._search_req_id or category != self.current_category:
            return
        widget = self._suggestion_anchor(field_mode)
        if not widget.hasFocus():
            return
        ctx_author, _ctx_title = self._suggestion_context(field_mode)
        has_author_ctx = field_mode == "title" and len(ctx_author) >= 2
        if not has_author_ctx and len(widget.text().strip()) < 2:
            return

        # I risultati online (cumulativi a ogni blocco progressivo) si uniscono a quelli locali già calcolati
        # in background: nessuna ricerca pesante sul thread grafico durante la digitazione.
        self._online_hits = list(online_items or [])
        if self._local_hits is None:
            return  # la ricerca locale non è ancora pronta: li fonderà lei appena termina
        self._show_merged_suggestions(field_mode)

    def _apply_suggestion(self, sug: Suggestion) -> None:
        """
        Compila tutti i campi con la proposta selezionata e avvia il recupero
        di tutte le edizioni specifiche per la categoria attiva (Libri, CD o Vinili).
        """
        if sug.category != self.current_category:
            return

        self._search_req_id += 1
        self._debounce_timer.stop()
        self.popup.hide()

        self._selected_work_id = sug.work_id
        self._current_bundle = None
        self._sort_manually_edited = False
        self._pub_manually_edited = bool(sug.from_isbn and sug.publisher)
        self._coll_manually_edited = bool(sug.from_isbn and sug.collection)
        self._year_manually_edited = bool(sug.from_isbn and sug.year)

        # Collane dell'opera già note nel suggerimento (es. da ISBN o archivio)
        work_colls: List[str] = []
        for c in ([sug.collection] if sug.collection else []) + list(sug.collections or []):
            cs = (c or "").strip()
            if cs and cs not in work_colls:
                work_colls.append(cs)
        self._current_work_collections = work_colls

        self.in_title.blockSignals(True)
        self.in_author.blockSignals(True)
        self.in_sort.blockSignals(True)
        try:
            self.in_title.setText(sug.title)
            self.in_author.setText(sug.author)
            sort_val = sug.display_author_sort()
            self.in_sort.setText(sort_val)
            self.in_title.deselect()
            self.in_author.deselect()
            self.in_sort.deselect()
        finally:
            self.in_title.blockSignals(False)
            self.in_author.blockSignals(False)
            self.in_sort.blockSignals(False)

        # Popola immediatamente le opzioni già disponibili (es. profilo curato o cache)
        pubs = list(sug.publishers) if sug.publishers else ([sug.publisher] if sug.publisher else [])
        yrs = list(sug.years) if sug.years else ([sug.year] if sug.year else [])
        self.in_pub.set_choices(pubs, sug.publisher or (pubs[0] if pubs else ""))
        self.in_year.set_choices(yrs, sug.year or (yrs[0] if yrs else ""))

        # Se la ricerca proviene da ISBN o da un elemento già in Archivio con una Collana,
        # compila automaticamente il campo Collana; altrimenti popola il menu a tendina
        # con le collane dell'opera + dell'editore attivo lasciando il testo libero a scelta dell'utente
        auto_coll = sug.collection if (sug.from_isbn or sug.source == "archive") else ""
        self._refresh_collection_choices(preserve_current=False, explicit_current=auto_coll)

        if len(pubs) > 1 or len(yrs) > 1 or self.in_coll.count() > 0:
            self._show_edition_badge(
                len(pubs),
                len(yrs),
                self.in_coll.count(),
                loading=self.settings.online_enabled,
            )
        elif self.settings.online_enabled:
            self.lbl_edition_badge.setText(tr(self.lang, "editions_loading"))
            self.lbl_edition_badge.show()

        # Avvia il recupero completo delle edizioni (filtrate per Libri vs CD vs Vinili)
        self._start_edition_fetch(sug.work_id, sug.title, sug.author)

    def _start_edition_fetch(self, work_id: str, title: str, author: str) -> None:
        if not title.strip() or not author.strip():
            return
        self._edition_req_id += 1
        ed_req_id = self._edition_req_id
        ed_worker = EditionFetchWorker(
            catalog=self.catalog,
            req_id=ed_req_id,
            category=self.current_category,
            work_id=work_id,
            title=title,
            author=author,
            lang=self.lang,
            is_cancelled_fn=lambda rid: rid != self._edition_req_id,
        )
        ed_worker.signals.finished.connect(self._on_editions_fetched)
        ed_worker.signals.completed.connect(self._on_main_worker_completed)
        self._active_workers.append(ed_worker)
        self.thread_pool.start(ed_worker)

    def _on_main_worker_completed(self, worker: object) -> None:
        _disconnect_worker_signals(worker)
        for pool_list in (self._local_workers, self._active_workers, self._retired_workers):
            try:
                pool_list.remove(worker)
            except ValueError:
                pass

    def _retire_workers(self, workers: List[QRunnable]) -> None:
        self._retired_workers = [w for w in self._retired_workers if not getattr(w, "_run_finished", False)]
        for w in workers:
            if not getattr(w, "_run_finished", False):
                sig = getattr(w, "signals", None)
                if _qobj_valid(sig) and hasattr(sig, "completed"):
                    try:
                        sig.completed.connect(self._on_main_worker_completed)
                    except (RuntimeError, TypeError):
                        pass
                self._retired_workers.append(w)

    def _on_editions_fetched(self, req_id: int, bundle: EditionBundle) -> None:
        if req_id != self._edition_req_id or bundle.category != self.current_category:
            return
        # Verifica che l'utente stia ancora lavorando sullo stesso titolo
        if search_fold(self.in_title.text()) != search_fold(bundle.title):
            return

        self._current_bundle = bundle

        if (
            bundle.author_sort
            and not self._sort_manually_edited
            and sort_key_matches_author(bundle.author_sort, self.in_author.text())
        ):
            self.in_sort.blockSignals(True)
            self.in_sort.setText(bundle.author_sort)
            self.in_sort.deselect()
            self.in_sort.blockSignals(False)

        cur_pub_txt = self.in_pub.currentText().strip()
        merged_pubs = list(bundle.publishers)
        if cur_pub_txt and cur_pub_txt not in merged_pubs:
            merged_pubs.insert(0, cur_pub_txt)

        cur_yr_txt = self.in_year.currentText().strip()
        merged_yrs = list(bundle.years)
        if cur_yr_txt and cur_yr_txt not in merged_yrs:
            merged_yrs.insert(0, cur_yr_txt)

        if merged_pubs:
            self.in_pub.set_choices(
                merged_pubs,
                current_text=cur_pub_txt or bundle.default_publisher or merged_pubs[0],
                preserve_user_text=self._pub_manually_edited or bool(cur_pub_txt),
            )
        if merged_yrs:
            self.in_year.set_choices(
                merged_yrs,
                current_text=cur_yr_txt or bundle.default_year or merged_yrs[0],
                preserve_user_text=self._year_manually_edited or bool(cur_yr_txt),
            )

        # Unisce le collane reali trovate per l'opera (OPAC SBN, Open Library, Google Books)
        seen_c = {c.lower() for c in self._current_work_collections}
        for c in bundle.collections or []:
            cs = (c or "").strip()
            if cs and cs.lower() not in seen_c:
                seen_c.add(cs.lower())
                self._current_work_collections.append(cs)

        self._refresh_collection_choices(preserve_current=True)

        # Se una collana è già selezionata, riallinea Editore/Etichetta e Anno solo se non erano
        # già stati valorizzati puntualmente dal codice ISBN o dallo smartphone
        active_coll = self.in_coll.currentText().strip()
        if active_coll:
            self._apply_collection_metadata(
                active_coll,
                overwrite_pub=not (self._pub_manually_edited or bool(cur_pub_txt)),
                overwrite_year=not (self._year_manually_edited or bool(cur_yr_txt)),
            )

        self._show_edition_badge(
            self.in_pub.count(),
            self.in_year.count(),
            self.in_coll.count(),
            loading=False,
        )

    def _show_edition_badge(
        self,
        n_pubs: int,
        n_years: int,
        n_colls: int = 0,
        loading: bool = False,
    ) -> None:
        if n_pubs == 0 and n_years <= 1 and n_colls == 0 and not loading:
            self.lbl_edition_badge.hide()
            return
        cat = self.current_category
        if self.lang == "it":
            if not loading and n_pubs <= 1 and n_years <= 1 and n_colls <= 1:
                txt = "Edizione unica"
            elif cat == "Libri":
                coll_part = f" · {n_colls} collane" if n_colls > 0 else ""
                txt = f"{n_pubs} editori{coll_part} · {n_years} anni"
            elif cat == "CD":
                coll_part = f" · {n_colls} serie" if n_colls > 0 else ""
                txt = f"CD: {n_pubs} etichette{coll_part} · {n_years} anni"
            else:
                coll_part = f" · {n_colls} serie" if n_colls > 0 else ""
                txt = f"Vinile: {n_pubs} etichette{coll_part} · {n_years} anni"
            if loading:
                txt += " · aggiornamento…"
        else:
            if not loading and n_pubs <= 1 and n_years <= 1 and n_colls <= 1:
                txt = "Single edition"
            elif cat == "Libri":
                coll_part = f" · {n_colls} series" if n_colls > 0 else ""
                txt = f"{n_pubs} publishers{coll_part} · {n_years} years"
            elif cat == "CD":
                coll_part = f" · {n_colls} series" if n_colls > 0 else ""
                txt = f"CD Editions: {n_pubs} labels{coll_part} · {n_years} years"
            else:
                coll_part = f" · {n_colls} series" if n_colls > 0 else ""
                txt = f"Vinyl Editions: {n_pubs} labels{coll_part} · {n_years} years"
            if loading:
                txt += "  (updating…)"
        self.lbl_edition_badge.setText(txt)
        self.lbl_edition_badge.show()

    # ------------------------------------------------------------------
    # GESTIONE TASTIERA NEI CAMPI DI INPUT (POPUP E INVIO)
    # ------------------------------------------------------------------
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if self.popup.isVisible() and obj in (self.in_title, self.in_author):
                if key == Qt.Key.Key_Down:
                    self.popup.move_selection(+1)
                    return True
                if key == Qt.Key.Key_Up:
                    self.popup.move_selection(-1)
                    return True
                if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab):
                    if self.popup.accept_current():
                        return True
                if key == Qt.Key.Key_Escape:
                    self._dismiss_suggestions()
                    return True

            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and obj in (
                self.in_title,
                self.in_author,
                self.in_sort,
                self.in_pub,
                self.in_coll,
                self.in_year,
            ):
                self.add_item_from_form()
                return True

        elif event.type() == QEvent.Type.FocusIn:
            if obj is self.in_title:
                if not self.in_title.text().strip() and len(self.in_author.text().strip()) >= 2:
                    QTimer.singleShot(20, lambda: self._on_query_edited("title", self.in_title.text()))

        elif event.type() == QEvent.Type.FocusOut:
            if obj in (self.in_title, self.in_author):
                # Appena il campo perde il focus (anche passando da Titolo ad Autore) la tendina sparisce
                # subito. Unica eccezione: il clic è in corso proprio su una voce della tendina.
                clicking_popup = (
                    self.popup.isVisible()
                    and QApplication.mouseButtons() != Qt.MouseButton.NoButton
                    and self.popup.geometry().contains(QCursor.pos())
                )
                if not clicking_popup:
                    self._dismiss_suggestions()
            if isinstance(obj, QLineEdit):
                obj.deselect()

        return super().eventFilter(obj, event)

    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)
        # La tendina è ancorata in coordinate assolute: se la finestra si sposta o cambia dimensione la chiudiamo
        if hasattr(self, "popup") and self.popup.isVisible():
            self._dismiss_suggestions()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "popup") and self.popup.isVisible():
            self._dismiss_suggestions()

    # ------------------------------------------------------------------
    # OPERAZIONI CRUD SUGLI ELEMENTI (CON SUPPORTO UNDO / REDO)
    # ------------------------------------------------------------------
    def _commit_category_change(self, category: str, new_items: List[Item], description: str) -> None:
        old_items = self.data_by_cat.get(category, [])
        cmd = CategorySnapshotCommand(self, category, old_items, new_items, description)
        self.undo_stacks[category].push(cmd)

    def _apply_snapshot(self, category: str, items: List[Item]) -> None:
        self.data_by_cat[category] = list(items)
        self.dirty_by_cat[category] = True
        if category == self.current_category:
            self.table_model.set_category_and_items(category, self.data_by_cat[category], self.lang)
            self._refresh_collection_choices(preserve_current=True)
        self._update_sidebar_badges()

    def _highlight_and_reveal_item(
        self, category: str, item: Item, message_type: str = "added"
    ) -> None:
        """
        Evidenzia l'elemento inserito in elenco, azzera eventuali filtri di ricerca per
        renderlo visibile, centra la visuale sulla sua riga nella tabella, lo seleziona
        e mostra nella barra di stato la sua posizione esatta in elenco.
        """
        if category not in CATEGORIES:
            return
        if category != self.current_category:
            self._switch_category(category)

        # Se il filtro di ricerca corrente nasconderebbe l'elemento, azzeralo per mostrare l'elenco completo
        if self.ed_search.text().strip():
            self.ed_search.clear()

        self.last_inserted_by_cat[category] = item
        self.table_model.set_last_inserted_item(item)

        try:
            src_row = self.table_model.items.index(item)
        except ValueError:
            return

        src_idx = self.table_model.index(src_row, 0)
        proxy_idx = self.proxy_model.mapFromSource(src_idx)
        if not proxy_idx.isValid():
            return

        proxy_row = proxy_idx.row()
        total = self.proxy_model.rowCount()
        pos = proxy_row + 1

        self.table.scrollTo(proxy_idx, QAbstractItemView.ScrollHint.PositionAtCenter)
        self.table.selectRow(proxy_row)

        if message_type == "scanned":
            msg = tr(
                self.lang,
                "status_scanned_pos",
                category=category,
                pos=pos,
                total=total,
                title=item.title,
                author=item.author,
            )
        elif message_type == "duplicated":
            msg = tr(
                self.lang,
                "status_duplicated_pos",
                pos=pos,
                total=total,
                title=item.title,
                author=item.author,
            )
        else:
            msg = tr(
                self.lang,
                "status_added_pos",
                pos=pos,
                total=total,
                title=item.title,
                author=item.author,
            )
        self.show_status_message(msg, 5000)

    def add_item_from_form(self) -> None:
        self.popup.hide()
        title = self.in_title.text().strip()
        author = self.in_author.text().strip()
        if not title or not author:
            QMessageBox.warning(self, tr(self.lang, "app_title"), tr(self.lang, "msg_missing_fields"))
            return

        cat = self.current_category
        sort_val = self.in_sort.text().strip() or surname_key(author, cat)
        pub = self.in_pub.currentText().strip()
        coll = self.in_coll.currentText().strip()
        year = self.in_year.currentText().strip()

        new_item = Item(
            title=title,
            author=author,
            publisher=pub,
            year=year,
            author_sort=sort_val,
            collection=coll,
            category=cat,
        )

        # Verifica duplicati esatti (Titolo + Autore + Editore + Anno)
        cur_list = list(self.data_by_cat.get(cat, []))
        is_dup = any(
            fold(i.title) == fold(new_item.title)
            and fold(i.author) == fold(new_item.author)
            and fold(i.publisher) == fold(new_item.publisher)
            and fold(i.year) == fold(new_item.year)
            for i in cur_list
        )
        if is_dup:
            res = QMessageBox.question(
                self,
                tr(self.lang, "app_title"),
                tr(self.lang, "msg_duplicate_warn"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if res != QMessageBox.StandardButton.Yes:
                return

        cur_list.append(new_item)
        desc = f"Aggiungi {title}" if self.lang == "it" else f"Add {title}"
        self._commit_category_change(cat, cur_list, desc)
        inserted_item = self.data_by_cat[cat][-1]
        self.reset_entry_form()
        self.in_title.setFocus()
        self._highlight_and_reveal_item(cat, inserted_item, message_type="added")

    def _selected_source_rows(self) -> List[int]:
        sel_model = self.table.selectionModel()
        if not sel_model:
            return []
        proxy_rows = sel_model.selectedRows()
        src_rows = sorted({self.proxy_model.mapToSource(idx).row() for idx in proxy_rows if idx.isValid()})
        return [r for r in src_rows if 0 <= r < len(self.data_by_cat.get(self.current_category, []))]

    def _on_table_double_clicked(self, proxy_index: QModelIndex) -> None:
        # Si attiva solo su una cella valida della tabella (mai sull'intestazione!)
        if not proxy_index.isValid():
            return
        src_idx = self.proxy_model.mapToSource(proxy_index)
        if not src_idx.isValid():
            return
        self._edit_source_row(src_idx.row())

    def edit_selected_item(self) -> None:
        rows = self._selected_source_rows()
        if not rows:
            QMessageBox.information(self, tr(self.lang, "app_title"), tr(self.lang, "msg_select_to_edit"))
            return
        self._edit_source_row(rows[0])

    def _edit_source_row(self, src_row: int) -> None:
        cat = self.current_category
        cur_list = list(self.data_by_cat.get(cat, []))
        if not (0 <= src_row < len(cur_list)):
            return
        target = cur_list[src_row]
        t_k = search_fold(target.title)
        a_k = search_fold(target.author)
        same_work = [
            i
            for i in cur_list
            if search_fold(i.title) == t_k and (not a_k or search_fold(i.author) == a_k)
        ]
        existing_colls = unique_collections(same_work or [target])
        dlg = EditItemDialog(
            target,
            cat,
            self.lang,
            self.catalog,
            existing_colls,
            self,
            archive_items=cur_list,
            online_enabled=self.settings.online_enabled,
            local_pool=self._local_pool,
        )
        try:
            if dlg.exec() == QDialog.DialogCode.Accepted:
                was_last = (self.last_inserted_by_cat.get(cat) is cur_list[src_row])
                cur_list[src_row] = dlg.updated_item()
                self._commit_category_change(
                    cat, cur_list, "Modifica elemento" if self.lang == "it" else "Edit item"
                )
                updated = self.data_by_cat[cat][src_row]
                if was_last:
                    self.last_inserted_by_cat[cat] = updated
                    self.table_model.set_last_inserted_item(updated)
                src_idx = self.table_model.index(src_row, 0)
                proxy_idx = self.proxy_model.mapFromSource(src_idx)
                if proxy_idx.isValid():
                    self.table.scrollTo(proxy_idx, QAbstractItemView.ScrollHint.PositionAtCenter)
                    self.table.selectRow(proxy_idx.row())
        finally:
            self._retire_workers(dlg.take_in_flight_workers())
            QCoreApplication.removePostedEvents(dlg)
            dlg.deleteLater()

    def duplicate_selected_item(self) -> None:
        rows = self._selected_source_rows()
        if not rows:
            QMessageBox.information(self, tr(self.lang, "app_title"), tr(self.lang, "msg_select_to_edit"))
            return
        cat = self.current_category
        cur_list = list(self.data_by_cat.get(cat, []))
        for r in rows:
            orig = cur_list[r]
            cur_list.append(
                Item(
                    title=orig.title,
                    author=orig.author,
                    publisher=orig.publisher,
                    year=orig.year,
                    author_sort=orig.author_sort,
                    isbn_or_barcode=orig.isbn_or_barcode,
                    collection=orig.collection,
                    category=cat,
                )
            )
        self._commit_category_change(
            cat, cur_list, "Duplica elemento" if self.lang == "it" else "Duplicate item"
        )
        inserted_item = self.data_by_cat[cat][-1]
        self._highlight_and_reveal_item(cat, inserted_item, message_type="duplicated")

    def assign_collection_to_selected(self) -> None:
        """Assegna una Collana / Collezione a tutti i volumi/dischi selezionati nella tabella."""
        rows = self._selected_source_rows()
        if not rows:
            QMessageBox.information(self, tr(self.lang, "app_title"), tr(self.lang, "msg_select_to_edit"))
            return
        cat = self.current_category
        cur_list = list(self.data_by_cat.get(cat, []))
        existing_colls = list(unique_collections(cur_list))
        seen_low = {c.lower() for c in existing_colls}
        for r in rows:
            pub_colls = self.catalog.collections_for_publisher(cur_list[r].publisher, cat)
            for pc in pub_colls:
                if pc and pc.lower() not in seen_low:
                    seen_low.add(pc.lower())
                    existing_colls.append(pc)
        initial_coll = cur_list[rows[0]].collection
        dlg = AssignCollectionDialog(
            category=cat,
            lang=self.lang,
            count=len(rows),
            existing_collections=existing_colls,
            initial_value=initial_coll,
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_coll = dlg.collection_value()
        for r in rows:
            cur_list[r] = cur_list[r].with_changes(collection=new_coll)
        self._commit_category_change(
            cat,
            cur_list,
            "Assegna Collana / Collezione" if self.lang == "it" else "Assign Series / Collection",
        )

    def clear_collection_from_selected(self) -> None:
        """Rimuove la Collana / Collezione dai volumi/dischi selezionati."""
        rows = self._selected_source_rows()
        if not rows:
            return
        cat = self.current_category
        cur_list = list(self.data_by_cat.get(cat, []))
        changed = False
        for r in rows:
            if cur_list[r].collection:
                cur_list[r] = cur_list[r].with_changes(collection="")
                changed = True
        if changed:
            self._commit_category_change(
                cat,
                cur_list,
                "Rimuovi Collana / Collezione" if self.lang == "it" else "Remove Series / Collection",
            )

    def delete_selected_items(self) -> None:
        rows = self._selected_source_rows()
        if not rows:
            QMessageBox.information(self, tr(self.lang, "app_title"), tr(self.lang, "msg_select_to_delete"))
            return
        res = QMessageBox.question(
            self,
            tr(self.lang, "app_title"),
            tr(self.lang, "msg_confirm_delete", n=len(rows)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if res != QMessageBox.StandardButton.Yes:
            return
        cat = self.current_category
        drop_set = set(rows)
        new_list = [it for idx, it in enumerate(self.data_by_cat.get(cat, [])) if idx not in drop_set]
        self._commit_category_change(
            cat, new_list, "Elimina elementi" if self.lang == "it" else "Delete items"
        )
        if self.last_inserted_by_cat.get(cat) not in self.data_by_cat.get(cat, []):
            self.last_inserted_by_cat[cat] = None
            self.table_model.set_last_inserted_item(None)

    def clear_current_category(self) -> None:
        cat = self.current_category
        if not self.data_by_cat.get(cat):
            return
        res = QMessageBox.question(
            self,
            tr(self.lang, "app_title"),
            tr(self.lang, "msg_confirm_clear", cat=tr(self.lang, f"cat_{cat}")),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if res != QMessageBox.StandardButton.Yes:
            return
        self._commit_category_change(cat, [], "Svuota elenco" if self.lang == "it" else "Clear list")
        self.last_inserted_by_cat[cat] = None
        self.table_model.set_last_inserted_item(None)

    def undo_last_action(self) -> None:
        stack = self.undo_stacks[self.current_category]
        if stack.canUndo():
            stack.undo()

    def redo_last_action(self) -> None:
        stack = self.undo_stacks[self.current_category]
        if stack.canRedo():
            stack.redo()

    def _on_filter_text_changed(self, text: str) -> None:
        self.proxy_model.set_search_query(text)
        self._update_sidebar_badges()

    def _on_table_context_menu(self, pos: QPoint) -> None:
        menu = QMenu(self)
        a_edit = menu.addAction(tr(self.lang, "btn_edit"))
        a_dup = menu.addAction(tr(self.lang, "btn_duplicate"))
        menu.addSeparator()
        a_assign_coll = menu.addAction(tr(self.lang, "ctx_assign_coll"))
        a_clear_coll = menu.addAction(tr(self.lang, "ctx_clear_coll"))
        menu.addSeparator()
        a_del = menu.addAction(tr(self.lang, "btn_delete"))
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen == a_edit:
            self.edit_selected_item()
        elif chosen == a_dup:
            self.duplicate_selected_item()
        elif chosen == a_assign_coll:
            self.assign_collection_to_selected()
        elif chosen == a_clear_coll:
            self.clear_collection_from_selected()
        elif chosen == a_del:
            self.delete_selected_items()

    # ------------------------------------------------------------------
    # SALVATAGGIO CSV ATOMICO E ESPORTAZIONE PDF
    # ------------------------------------------------------------------
    def _clear_lingering_selections(self) -> None:
        """Deseleziona qualsiasi testo evidenziato nei campi dopo dialoghi di salvataggio/esportazione."""
        for w in (self.in_title, self.in_author, self.in_sort, self.ed_search):
            w.deselect()
        for cb in (self.in_pub, self.in_coll, self.in_year, self.cb_sheet):
            if cb.lineEdit():
                cb.lineEdit().deselect()

    def _write_csv_safe(self, full_path: str, items: List[Item]) -> bool:
        try:
            save_csv(full_path, items)
            return True
        except OSError as exc:
            QMessageBox.warning(self, tr(self.lang, "app_title"), str(exc))
            return False

    def save_current_category(self) -> None:
        cat = self.current_category
        fn = self.active_file_by_cat.get(cat, DEFAULT_FILES[cat])
        full_path = os.path.join(self.settings.folder, fn)
        if not self._write_csv_safe(full_path, self.data_by_cat.get(cat, [])):
            return
        self.dirty_by_cat[cat] = False
        self._update_sidebar_badges()
        self._clear_lingering_selections()
        self.show_status_message(tr(self.lang, "msg_saved_one", fn=fn), 4000)

    def save_current_as(self) -> None:
        cat = self.current_category
        dlg = SaveAsCategoryDialog(cat, self.lang, self.settings.folder, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            self._clear_lingering_selections()
            return
        fn = dlg.filename()
        full_path = os.path.join(self.settings.folder, fn)
        if not self._write_csv_safe(full_path, self.data_by_cat.get(cat, [])):
            return
        if fn not in self.files_by_cat[cat]:
            self.files_by_cat[cat].append(fn)
            self.files_by_cat[cat].sort()
        self.active_file_by_cat[cat] = fn
        self.settings.active_files[cat] = fn
        self.settings.save()
        self.dirty_by_cat[cat] = False
        self._refresh_sheet_combo()
        self._update_sidebar_badges()
        self._clear_lingering_selections()
        self.show_status_message(tr(self.lang, "msg_saved_one", fn=fn), 4000)

    def save_all_categories(self) -> None:
        saved_names = []
        for cat in CATEGORIES:
            fn = self.active_file_by_cat.get(cat, DEFAULT_FILES[cat])
            full_path = os.path.join(self.settings.folder, fn)
            if not self._write_csv_safe(full_path, self.data_by_cat.get(cat, [])):
                self._update_sidebar_badges()
                return
            self.dirty_by_cat[cat] = False
            saved_names.append(fn)
        self._update_sidebar_badges()
        self._clear_lingering_selections()
        msg = tr(self.lang, "msg_saved_all", files=", ".join(saved_names))
        self.show_status_message(msg, 5000)
        QMessageBox.information(self, tr(self.lang, "app_title"), msg)

    def _sorted_items_for_export(self, category: str) -> List[Item]:
        """Restituisce gli elementi della categoria ordinati secondo l'ordinamento corrente."""
        items = list(self.data_by_cat.get(category, []))
        if category == self.current_category:
            out: List[Item] = []
            for row in range(self.proxy_model.rowCount()):
                idx = self.proxy_model.index(row, 0)
                it = self.proxy_model.data(idx, ITEM_ROLE)
                if it is not None:
                    out.append(it)
            return out
        # Per le altre categorie ordina rispettando l'impostazione di raggruppamento collane
        return sort_items(items, group_by_collection=self.settings.group_by_collection)

    def _current_pdf_sort_label(self, category: str) -> str:
        """Determina l'etichetta descrittiva dell'ordinamento applicato al PDF."""
        hdr = self.table.horizontalHeader()
        sec = hdr.sortIndicatorSection() if category == self.current_category else ArchiveTableModel.COL_AUTHOR
        is_asc = (hdr.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder) if category == self.current_category else True
        order_az = "A–Z" if is_asc else "Z–A"
        order_num = ("crescente" if is_asc else "decrescente") if self.lang == "it" else ("asc" if is_asc else "desc")

        if sec == ArchiveTableModel.COL_TITLE:
            col_name = "Titolo" if self.lang == "it" else "Title"
            sort_info = f"{col_name} ({order_az})"
        elif sec in (ArchiveTableModel.COL_AUTHOR, ArchiveTableModel.COL_SORT):
            col_name = "Autore" if self.lang == "it" else "Author"
            sort_info = f"{col_name} ({order_az})"
        elif sec == ArchiveTableModel.COL_PUB:
            col_name = "Editore" if self.lang == "it" else "Publisher"
            sort_info = f"{col_name} ({order_az})"
        elif sec == ArchiveTableModel.COL_COLL:
            col_name = "Collana" if self.lang == "it" else "Collection"
            sort_info = f"{col_name} ({order_az})"
        elif sec == ArchiveTableModel.COL_YEAR:
            col_name = "Anno" if self.lang == "it" else "Year"
            sort_info = f"{col_name} ({order_num})"
        else:
            col_name = "Autore" if self.lang == "it" else "Author"
            sort_info = f"{col_name} ({order_az})"

        if self.settings.group_by_collection:
            prefix = "Collane raggruppate  •  " if self.lang == "it" else "Grouped by collection  •  "
            return f"{prefix}{sort_info}"

        prefix = "Ordinamento: " if self.lang == "it" else "Sorted by: "
        return f"{prefix}{sort_info}"

    def export_current_pdf(self) -> None:
        cat = self.current_category
        items = self._sorted_items_for_export(cat)
        if not items:
            QMessageBox.information(self, tr(self.lang, "app_title"), tr(self.lang, "msg_pdf_empty"))
            return
        active_csv = self.active_file_by_cat.get(cat, DEFAULT_FILES[cat])
        base_sheet = os.path.splitext(active_csv)[0]
        default_pdf = f"{base_sheet}.pdf"
        default_path = os.path.join(self.settings.folder, default_pdf)

        out_path, _ = QFileDialog.getSaveFileName(
            self,
            tr(self.lang, "btn_export_pdf"),
            default_path,
            "PDF (*.pdf)",
        )
        if not out_path:
            self._clear_lingering_selections()
            return
        if not out_path.lower().endswith(".pdf"):
            out_path += ".pdf"

        # Usa il nome effettivo del foglio scelto dall'utente nel salvataggio
        sheet_title = os.path.splitext(os.path.basename(out_path))[0] or base_sheet
        try:
            export_category_pdf(
                out_path,
                cat,
                items,
                lang=self.lang,
                sort_label=self._current_pdf_sort_label(cat),
                group_by_collection=self.settings.group_by_collection,
                sheet_title=sheet_title,
            )
        except OSError as exc:
            QMessageBox.warning(self, tr(self.lang, "app_title"), str(exc))
            return
        self._clear_lingering_selections()
        msg = tr(self.lang, "msg_pdf_exported", files=out_path)
        self.show_status_message(msg, 5000)
        QMessageBox.information(self, tr(self.lang, "app_title"), msg)

    # ------------------------------------------------------------------
    # IMPOSTAZIONI E CHIUSURA APPLICAZIONE
    # ------------------------------------------------------------------
    def _open_settings_dialog(self) -> None:
        dlg = SettingsDialog(self.settings, self.catalog, self)
        res = dlg.exec()
        if dlg.config_was_reset:
            fresh = Settings.load()
            setup_dlg = FirstRunSetupDialog(fresh, self)
            if setup_dlg.exec() == QDialog.DialogCode.Accepted:
                self.settings = fresh
                self.lang = self.settings.language
                self.catalog.discogs_token = self.settings.discogs_token
                self.remote_scanner.set_connection_mode(
                    self.settings.scanner_mode, self.settings.scanner_gh_pages_url
                )
                self.chk_group_coll.blockSignals(True)
                self.chk_group_coll.setChecked(self.settings.group_by_collection)
                self.chk_group_coll.blockSignals(False)
                self.table_model.group_by_collection = self.settings.group_by_collection
                self.proxy_model.group_by_collection = self.settings.group_by_collection
                self._load_all_from_folder(self.settings.folder)
                self._apply_language()
                self._switch_category("Libri", force=True)
                msg = (
                    "Configurazione iniziale ripristinata."
                    if self.lang == "it"
                    else "Initial configuration reset."
                )
                self.show_status_message(msg, 4000)
            else:
                self.close()
            return
        if res != QDialog.DialogCode.Accepted:
            return
        new_lang = dlg.selected_language()
        new_online = dlg.selected_online()
        new_discogs = dlg.selected_discogs_token()
        changed_lang = new_lang != self.settings.language
        self.settings.language = new_lang
        self.settings.online_enabled = new_online
        self.settings.discogs_token = new_discogs
        self.catalog.discogs_token = new_discogs
        self.remote_scanner.online_enabled = new_online
        self.settings.save()
        if changed_lang:
            self._apply_language()
        else:
            self._update_online_indicator()

    # ------------------------------------------------------------------
    # SCANSIONE DA FOTOCAMERA MAC E DA DISPOSITIVO MOBILE
    # ------------------------------------------------------------------
    def open_mac_camera_dialog(self) -> None:
        if self._mac_cam_dialog is None:
            self._mac_cam_dialog = MacCameraScannerDialog(
                scanner=self.remote_scanner,
                settings=self.settings,
                get_category_fn=lambda: self.current_category,
                parent=self,
            )
        if not self._mac_cam_dialog.isVisible() and not self._mac_cam_dialog.isFullScreen():
            dlg_geo = self._mac_cam_dialog.frameGeometry()
            dlg_geo.moveCenter(self.frameGeometry().center())
            self._mac_cam_dialog.move(dlg_geo.topLeft())
            self._mac_cam_dialog._prepare_mac_overlay()
        elif self._mac_cam_dialog.isMinimized():
            self._mac_cam_dialog.showNormal()
        self._mac_cam_dialog.show()
        if not self._mac_cam_dialog.isFullScreen():
            self._mac_cam_dialog._attach_mac_overlay()
        self._mac_cam_dialog.raise_()
        self._mac_cam_dialog.activateWindow()

    def open_remote_scanner_dialog(self) -> None:
        self.remote_scanner.start()
        if self._remote_dialog is None:
            self._remote_dialog = RemoteScannerDialog(
                scanner=self.remote_scanner,
                settings=self.settings,
                get_category_fn=lambda: self.current_category,
                parent=self,
            )
        else:
            self._remote_dialog.refresh_qr()
        self._remote_dialog.show()
        self._remote_dialog.raise_()
        self._remote_dialog.activateWindow()

    def _on_remote_scan_log(self, level: str, message: str) -> None:
        self.show_status_message(message, 5000)
        if self._remote_dialog is not None:
            self._remote_dialog.append_log(level, message)

    def _on_remote_category_changed(self, cat: str) -> None:
        if cat in CATEGORIES and cat != self.current_category:
            self._switch_category(cat)
        if self._remote_dialog is not None and self._remote_dialog.isVisible():
            self._remote_dialog.refresh_qr()

    def _sync_form_with_remote_item(self, item_obj: Item, badge_text: str = "", fetch_editions: bool = True) -> None:
        self.popup.hide()
        self._sort_manually_edited = False
        self._pub_manually_edited = bool(item_obj.publisher.strip())
        self._coll_manually_edited = bool(item_obj.collection.strip())
        self._year_manually_edited = bool(item_obj.year.strip())

        prev_title_fold = search_fold(self.in_title.text())
        new_title_fold = search_fold(item_obj.title)

        self.in_title.blockSignals(True)
        self.in_author.blockSignals(True)
        self.in_sort.blockSignals(True)
        self.in_title.setText(item_obj.title)
        self.in_author.setText(item_obj.author)
        self.in_sort.setText(item_obj.author_sort or surname_key(item_obj.author, self.current_category))
        self.in_title.deselect()
        self.in_author.deselect()
        self.in_sort.deselect()
        self.in_title.blockSignals(False)
        self.in_author.blockSignals(False)
        self.in_sort.blockSignals(False)

        existing_pubs = [self.in_pub.itemText(i) for i in range(self.in_pub.count()) if self.in_pub.itemText(i).strip()]
        if item_obj.publisher:
            pubs_list = [item_obj.publisher] + [p for p in existing_pubs if p != item_obj.publisher]
            self.in_pub.set_choices(pubs_list, current_text=item_obj.publisher)

        existing_yrs = [self.in_year.itemText(i) for i in range(self.in_year.count()) if self.in_year.itemText(i).strip()]
        if item_obj.year:
            yrs_list = [item_obj.year] + [y for y in existing_yrs if y != item_obj.year]
            self.in_year.set_choices(yrs_list, current_text=item_obj.year)

        if item_obj.collection:
            if item_obj.collection not in self._current_work_collections:
                self._current_work_collections.insert(0, item_obj.collection)
        self._refresh_collection_choices(explicit_current=item_obj.collection)

        if badge_text:
            pub_part = item_obj.publisher.strip()
            yr_part = f"({item_obj.year.strip()})" if item_obj.year.strip() else ""
            meta = " ".join(p for p in (pub_part, yr_part) if p)
            self.lbl_edition_badge.setText(f"{badge_text}  •  {meta}" if meta else badge_text)
            self.lbl_edition_badge.show()
        if fetch_editions and new_title_fold and new_title_fold != prev_title_fold:
            self._start_edition_fetch("", item_obj.title, item_obj.author)

    def _on_remote_item_previewed(self, category: str, item_obj: object, badge_text: str) -> None:
        if not isinstance(item_obj, Item):
            return
        if category in CATEGORIES and category != self.current_category:
            self._switch_category(category)
        self._sync_form_with_remote_item(item_obj, badge_text=badge_text, fetch_editions=True)

    def _on_remote_item_added(self, category: str, item_obj: object) -> None:
        if not isinstance(item_obj, Item):
            return
        if category in CATEGORIES and category != self.current_category:
            self._switch_category(category)
        cur_list = list(self.data_by_cat.get(category, []))
        cur_list.append(item_obj)
        self._commit_category_change(
            category,
            cur_list,
            f"Scansione {item_obj.title}" if self.lang == "it" else f"Scan {item_obj.title}",
        )
        inserted_item = self.data_by_cat[category][-1]
        self._highlight_and_reveal_item(category, inserted_item, message_type="scanned")
        # L'elemento è ormai in archivio: i campi di ricerca vanno svuotati (restano compilati solo
        # nell'anteprima, per consentire eventuali modifiche prima dell'inserimento).
        self.reset_entry_form()

    def closeEvent(self, event) -> None:  # noqa: N802
        if any(self.dirty_by_cat.values()):
            res = QMessageBox.question(
                self,
                tr(self.lang, "app_title"),
                tr(self.lang, "msg_unsaved_quit"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            )
            if res == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if res == QMessageBox.StandardButton.Yes:
                for cat in CATEGORIES:
                    if self.dirty_by_cat.get(cat):
                        fn = self.active_file_by_cat.get(cat, DEFAULT_FILES[cat])
                        if not self._write_csv_safe(
                            os.path.join(self.settings.folder, fn), self.data_by_cat.get(cat, [])
                        ):
                            event.ignore()
                            return
                        self.dirty_by_cat[cat] = False
        self._search_req_id += 1
        self._local_req_id += 1
        self._edition_req_id += 1
        self._debounce_timer.stop()
        if self._mac_cam_dialog is not None:
            self._mac_cam_dialog._force_close_now = True
            self._mac_cam_dialog.cam_session.stop()
            if self._mac_cam_dialog.isFullScreen():
                self._mac_cam_dialog.showNormal()
            self._mac_cam_dialog.close()
        self.remote_scanner.stop()
        if hasattr(self, "catalog") and self.catalog is not None:
            self.catalog.close()
        event.accept()


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


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("Archivio")
    app.setOrganizationDomain("archivio.app")
    _apply_light_palette(app)

    icon_path = _resolve_app_icon_path()
    if icon_path:
        app.setWindowIcon(QIcon(icon_path))

    settings = Settings.load()
    if not settings.first_run_completed:
        setup_dlg = FirstRunSetupDialog(settings)
        if icon_path:
            setup_dlg.setWindowIcon(QIcon(icon_path))
        if setup_dlg.exec() != QDialog.DialogCode.Accepted:
            return 0

    win = MainWindow(settings)
    if icon_path:
        win.setWindowIcon(QIcon(icon_path))
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
