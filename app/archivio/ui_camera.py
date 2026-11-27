"""Dialogo di scansione da fotocamera del computer (macOS, Windows, Linux)."""
from __future__ import annotations

import os
import sys
import tempfile
from typing import Dict, List, Optional

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .config import CATEGORIES, Settings
from .i18n import tr
from .remote_scanner import (
    MacCameraSession,
    RemoteScannerService,
    list_mac_cameras,
    match_cover_artwork_from_bytes,
    merge_visual_matches,
    run_apple_vision_on_image,
)
from .store import Item
from .textutil import fold
from .ui_theme import (
    MACOS_STYLESHEET,
    _MAC_CB_FULLSCREEN_AUX_ACTIVE,
    _MAC_CB_FULLSCREEN_PRIMARY_ACTIVE,
    _MAC_FS_HANDLERS,
    _MAC_NSWINDOW_STYLE_FULLSCREEN,
    _MAC_ZOOM_HANDLERS,
    _disable_mac_focus_rect,
    _get_mac_objc_bridge,
    _mac_nswindow,
    _make_studio_icon,
)
from .ui_widgets import CleanComboBox, StudioCheckBox


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
        self._mac_fs_obs_nswindow: int = 0
        self._was_fullscreen: bool = False
        self._mac_exiting_fullscreen: bool = False
        self._overlay_transitioning: bool = False
        self._close_after_fs: bool = False
        self._force_close_now: bool = False
        self._floating_geo: QRect = QRect()
        self._deferred_close_timer = QTimer(self)
        self._deferred_close_timer.setSingleShot(True)
        self._deferred_close_timer.timeout.connect(self._finish_deferred_close_after_fs)
        self.destroyed.connect(
            lambda _obj=None, ref=self._mac_zoom_bridge_ref: MacCameraScannerDialog._release_mac_zoom_bridge_id(ref)
        )
        self.scanner = scanner
        self.settings = settings
        self.get_category_fn = get_category_fn
        self.lang = settings.language
        win_type = Qt.WindowType.Dialog if sys.platform == "darwin" else Qt.WindowType.Window
        flags = (
            win_type
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        if sys.platform == "darwin":
            flags |= Qt.WindowType.WindowFullscreenButtonHint
        self.setWindowFlags(flags)
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
            "Avvicina il codice a barre (acquisito in automatico) o la copertina (premi Spazio per scattare la foto). "
            "Puoi usare il pulsante verde della barra del titolo per ingrandirla a schermo intero o affiancarla all'archivio."
            if self.lang == "it"
            else "Hold a barcode (captured automatically) or a cover (press Space to take a photo) in front of the camera. "
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

        # Opzioni: Modalità Raffica (l'acquisizione automatica dei codici a barre è sempre attiva)
        opts_row = QHBoxLayout()
        opts_row.setContentsMargins(4, 16, 4, 8)
        self.chk_burst = StudioCheckBox(
            "Modalità Raffica (aggiungi subito)" if self.lang == "it" else "Burst Mode (add immediately)"
        )
        self.chk_burst.setChecked(bool(getattr(self.settings, "camera_burst_mode", False)))
        self.chk_burst.toggled.connect(self._on_camera_prefs_changed)
        opts_row.addWidget(self.chk_burst)
        opts_row.addStretch(1)
        layout.addLayout(opts_row)

        # Pulsanti operativi principali
        act_row = QHBoxLayout()
        act_row.setSpacing(10)

        self.btn_capture_bc = QPushButton()
        self.btn_capture_bc.setObjectName("PrimaryButton")
        self.btn_capture_bc.setMinimumHeight(34)
        self.btn_capture_bc.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_capture_bc.clicked.connect(self.capture_barcode_now)
        self.btn_capture_bc.hide()
        act_row.addWidget(self.btn_capture_bc, 3)

        self.btn_capture_ocr = QPushButton(
            "  Scatta foto copertina (Spazio)"
            if self.lang == "it"
            else "  Take cover photo (Space)"
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

        self.btn_card_cancel = QPushButton("✕ Annulla / Prossimo (Canc)" if self.lang == "it" else "✕ Cancel / Next (Del)")
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

        self._update_capture_button_text()
        self._populate_cameras(start_now=False)
        _disable_mac_focus_rect(self)

        # Disabilita default e autoDefault sui pulsanti ordinari per riservare Invio all'inserimento
        for btn in (
            self.btn_reload_cams,
            self.btn_capture_bc,
            self.btn_capture_ocr,
            self.btn_card_cancel,
        ):
            btn.setAutoDefault(False)
            btn.setDefault(False)

        # Scorciatoie a livello di finestra: hanno la precedenza sul controllo che ha il focus
        for key in (
            Qt.Key.Key_Space,
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_Delete,
            Qt.Key.Key_Backspace,
            Qt.Key.Key_Escape,
        ):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(lambda k=key: self._handle_action_key(k))

    def _handle_action_key(self, k: int) -> bool:
        """
        Azioni rapide da tastiera:
        - Spazio: scatta la foto alla copertina (o acquisisce il barcode rilevato).
        - Invio: cerca dalla foto scattata / inserisce l'articolo individuato nell'archivio.
        - Canc (Delete/Backspace): annulla anteprima / riscatta foto senza uscire da schermo intero.
        - Esc: esce da schermo intero.
        """
        if k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if not self.card_preview.isHidden():
                self._on_card_cancel_clicked()
                return True
            if self._is_cover_frozen or not self.list_candidates.isHidden():
                self.reset_to_acquisition_mode(notify_parent=True)
                return True
            if self._overlay_parent is not None and hasattr(self._overlay_parent, "reset_entry_form"):
                self._overlay_parent.reset_entry_form(reset_camera=False)
                self.reset_to_acquisition_mode(notify_parent=False)
                return True
            return False

        if k == Qt.Key.Key_Escape:
            if self.isFullScreen():
                self.showNormal()
                return True
            return False

        if k == Qt.Key.Key_Space:
            if not self._is_cover_frozen:
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
                "Nessun elemento individuato da inserire. Inquadra un codice a barre o premi Spazio per fotografare la copertina."
                if self.lang == "it"
                else "No item identified to insert. Frame a barcode or press Space to photograph the cover.",
            )
            return True

        return False

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if self._handle_action_key(event.key()):
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            event.accept()
            return
        super().keyPressEvent(event)

    def _ensure_mac_bridge_id(self, b: dict) -> int:
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
                _MAC_FS_HANDLERS[obj] = (self._on_mac_will_exit_fs, self._on_mac_did_exit_fs)
        return self._mac_zoom_bridge_id

    def _ensure_mac_fs_observer(self, b: dict, ns_w: int) -> None:
        if not ns_w or ns_w == self._mac_fs_obs_nswindow:
            return
        bid = self._ensure_mac_bridge_id(b)
        if not bid:
            return
        try:
            nc = b["msg_ptr"](b["ns_notif_center_cls"], b["sel_default_center"])
            if not nc:
                return
            if self._mac_fs_obs_nswindow:
                b["msg_void_ptr"](nc, b["sel_remove_observer"], bid)
            for notif_name, sel in (
                (b"NSWindowWillExitFullScreenNotification", b["sel_on_will_exit_fs"]),
                (b"NSWindowDidExitFullScreenNotification", b["sel_on_did_exit_fs"]),
            ):
                ns_name = b["msg_ptr_cstr"](b["ns_string_cls"], b["sel_str_utf8"], notif_name)
                if ns_name:
                    b["msg_void_4ptr"](nc, b["sel_add_observer"], bid, sel, ns_name, ns_w)
            self._mac_fs_obs_nswindow = ns_w
        except Exception:
            pass

    def _is_mac_fullscreen_active(self) -> bool:
        if self.isFullScreen():
            return True
        b = _get_mac_objc_bridge()
        if not b:
            return False
        try:
            ns_w = _mac_nswindow(self, b)
            if ns_w:
                sm = int(b["msg_ul"](ns_w, b["sel_style_mask"]) or 0)
                return bool(sm & _MAC_NSWINDOW_STYLE_FULLSCREEN)
        except Exception:
            pass
        return False

    def _on_mac_will_exit_fs(self) -> None:
        self._mac_exiting_fullscreen = True

    def _on_mac_did_exit_fs(self) -> None:
        self._mac_exiting_fullscreen = False
        QTimer.singleShot(0, self._handle_mac_did_exit_fs)

    def _handle_mac_did_exit_fs(self) -> None:
        if self._close_after_fs:
            self._finish_deferred_close_after_fs()
            return
        if self._overlay_transitioning:
            self._restore_mac_overlay_after_fullscreen()

    def _prepare_mac_overlay(self) -> None:
        """Imposta NSWindow in FullScreenAuxiliary|MoveToActiveSpace prima di show() per aprire sopra Archivio."""
        b = _get_mac_objc_bridge()
        if not b:
            return
        try:
            ns_w = _mac_nswindow(self, b)
            if ns_w:
                b["msg_void_ul"](ns_w, b["sel_set_cb"], _MAC_CB_FULLSCREEN_AUX_ACTIVE)
                self._ensure_mac_fs_observer(b, ns_w)
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
            self._ensure_mac_fs_observer(b, ns_w)
            b["msg_void_ul"](ns_w, b["sel_set_cb"], _MAC_CB_FULLSCREEN_AUX_ACTIVE)
            ns_p = _mac_nswindow(self._overlay_parent, b)
            if ns_p and self._overlay_parent is not None and self._overlay_parent.isVisible():
                b["msg_void_ptr_l"](ns_p, b["sel_add_child"], ns_w, 1)  # NSWindowAbove = 1
            zb = b["msg_ptr_ul"](ns_w, b["sel_std_btn"], 2)  # NSWindowZoomButton = 2
            if zb:
                bid = self._ensure_mac_bridge_id(b)
                if bid:
                    b["msg_void_ptr"](zb, b["sel_set_target"], bid)
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
            self._ensure_mac_fs_observer(b, ns_w)
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

    def _release_mac_zoom_bridge_id(ref: list[int], win_ref=None) -> None:
        bid = ref[0] if ref else 0
        bridge = _get_mac_objc_bridge()
        if not bid or not bridge:
            return
        ref[0] = 0
        _MAC_ZOOM_HANDLERS.pop(bid, None)
        _MAC_FS_HANDLERS.pop(bid, None)
        try:
            if win_ref is not None:
                ns_w = _mac_nswindow(win_ref, bridge)
                if ns_w:
                    zb = bridge["msg_ptr_ul"](ns_w, bridge["sel_std_btn"], 2)
                    if zb:
                        bridge["msg_void_ptr"](zb, bridge["sel_set_target"], None)
                        bridge["msg_void_ptr"](zb, bridge["sel_set_action"], None)
            nc = bridge["msg_ptr"](
                bridge["ns_notif_center_cls"], bridge["sel_default_center"]
            )
            if nc:
                bridge["msg_void_ptr"](nc, bridge["sel_remove_observer"], bid)
            bridge["msg_ptr"](bid, bridge["sel_release"])
        except Exception:
            pass

    def _restore_mac_overlay_after_fullscreen(self) -> None:
        if self._mac_exiting_fullscreen:
            return
        if self._close_after_fs:
            self._finish_deferred_close_after_fs()
            return
        if not self.isVisible():
            self._overlay_transitioning = False
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
        self._deferred_close_timer.stop()
        self._mac_exiting_fullscreen = False
        self._close_after_fs = False
        self._force_close_now = True
        self._overlay_transitioning = False
        if self.isMaximized():
            self.showNormal()
        if self._floating_geo.isValid():
            self.setGeometry(self._floating_geo)
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
        self._deferred_close_timer.stop()
        self._close_after_fs = False
        self._force_close_now = False
        self._mac_exiting_fullscreen = False
        if not self.isFullScreen():
            self._was_fullscreen = False
            self._overlay_transitioning = False
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
        is_fs_or_exiting = self._is_mac_fullscreen_active() or self._mac_exiting_fullscreen
        if (
            sys.platform == "darwin"
            and is_fs_or_exiting
            and not self._force_close_now
            and not QApplication.closingDown()
        ):
            if self._close_after_fs:
                event.ignore()
                return
            self._close_after_fs = True
            self._overlay_transitioning = True
            event.ignore()
            if self._is_mac_fullscreen_active():
                self.showNormal()
            self._deferred_close_timer.start(1500)
            return
        self._deferred_close_timer.stop()
        self._close_after_fs = False
        self._force_close_now = False
        self._was_fullscreen = False
        self._mac_exiting_fullscreen = False
        self._overlay_transitioning = False
        self._detach_mac_child_only()
        self._cleanup_pending_frozen_tmp_paths()
        # QDialog.closeEvent ignora l'evento e invoca reject(), che qui richiama close():
        # accettiamo direttamente l'evento dopo aver impostato Rejected per chiudere davvero la finestra.
        self.setResult(QDialog.DialogCode.Rejected)
        event.accept()

    def _populate_cameras(self, start_now: bool = True) -> None:
        _ui_mod = sys.modules.get("archivio.ui")
        _list_cams = getattr(_ui_mod, "list_mac_cameras", list_mac_cameras)
        cams = _list_cams()
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
        burst_val = self.chk_burst.isChecked()
        if burst_val != getattr(self.settings, "camera_burst_mode", False):
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
            f"Fotocamera attiva: {cam_name}. I codici a barre vengono letti in automatico; premi Spazio per fotografare una copertina."
            if self.lang == "it"
            else f"Active camera: {cam_name}. Barcodes are read automatically; press Space to photograph a cover.",
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
        self.btn_capture_bc.hide()
        self.btn_capture_bc.setStyleSheet("")

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
            "FOTO SCATTATA — Premi Invio per cercare nel catalogo"
            if self.lang == "it"
            else "PHOTO CAPTURED — Press Enter to search catalog"
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
        # Acquisizione automatica sempre attiva: non azzerare scheda o candidati già aperti sullo stesso codice
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
            self.retake_cover_photo(clear_parent=True)
            return
        if self._live_barcode:
            self._start_barcode_lookup(self._live_barcode)
            return
        self.freeze_cover_photo()

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
            "Riscatta / Riprendi fotocamera (Canc)"
            if self.lang == "it"
            else "Retake / Resume camera (Del)"
        )
        self.btn_capture_bc.setStyleSheet(
            "background-color: #FFFFFF; color: #141413; border: 1px solid #DFDDD6; font-weight: 500; padding: 6px 16px; border-radius: 5px;"
        )
        self.btn_capture_bc.show()

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

    def retake_cover_photo(self, clear_parent: bool = True) -> None:
        import time as _time
        self._is_cover_frozen = False
        self._ocr_waiting = False
        self._pending_ocr_req_id = getattr(self, "_pending_ocr_req_id", 0) + 1
        self._cleanup_pending_frozen_tmp_paths()
        self.btn_capture_ocr.setEnabled(True)
        self.btn_card_add.setDefault(False)
        self.card_preview.hide()
        self.list_candidates.hide()
        self._multi_candidates = []
        self._current_preview_item = None
        self._last_auto_ts = _time.time()
        self._update_capture_button_text()
        self.btn_capture_ocr.setIcon(_make_studio_icon("camera", "#141413", 14))
        self.btn_capture_ocr.setText(
            "  Scatta foto copertina (Spazio)"
            if self.lang == "it"
            else "  Take cover photo (Space)"
        )
        self.btn_capture_ocr.setStyleSheet("")
        # Rimuove subito il banner "FOTO SCATTATA" prima ancora che arrivi il fotogramma successivo
        if self._last_full_jpg_bytes:
            self._render_frame(self._last_full_jpg_bytes, self._live_barcode)
        else:
            self.lbl_video.setPixmap(QPixmap())
        self._set_status_box(
            "neutral",
            "Fotocamera attiva. I codici a barre vengono letti in automatico; premi Spazio per fotografare una copertina."
            if self.lang == "it"
            else "Camera active. Barcodes are read automatically; press Space to photograph a cover.",
        )
        if self.isVisible() and not self.cam_session.is_running:
            self._start_selected_camera()
        if clear_parent and self._overlay_parent is not None and hasattr(self._overlay_parent, "reset_entry_form"):
            self._overlay_parent.reset_entry_form(reset_camera=False)

    def reset_to_acquisition_mode(
        self, status_text: Optional[str] = None, notify_parent: bool = False
    ) -> None:
        """Riporta la fotocamera in modalità acquisizione attiva chiudendo anteprime e candidati."""
        import time as _time
        self.btn_card_add.setDefault(False)
        self.card_preview.hide()
        self.list_candidates.hide()
        self._multi_candidates = []
        self._current_preview_item = None
        self._pending_ocr_req_id = getattr(self, "_pending_ocr_req_id", 0) + 1
        self._ocr_waiting = False
        self._last_auto_ts = _time.time()
        default_msg = (
            "Fotocamera attiva. I codici a barre vengono letti in automatico; premi Spazio per fotografare una copertina."
            if self.lang == "it"
            else "Camera active. Barcodes are read automatically; press Space to photograph a cover."
        )
        if self._is_cover_frozen:
            self.retake_cover_photo(clear_parent=False)
            if status_text:
                self._set_status_box("neutral", status_text)
        else:
            self._update_capture_button_text()
            self._set_status_box("neutral", status_text or default_msg)
        if notify_parent and self._overlay_parent is not None and hasattr(self._overlay_parent, "reset_entry_form"):
            self._overlay_parent.reset_entry_form(reset_camera=False)

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
            "Analisi della foto scaduta: riprova o riscatta la foto con più luce."
            if self.lang == "it"
            else "Photo analysis timed out: try again or retake the photo in better light.",
        )

    def _dispatch_full_frame_ocr(self, jpg_bytes: Optional[bytes], fallback_live_ocr: bool = True) -> bool:
        """Salva il frame JPEG ad alta risoluzione e avvia la scansione Vision completa (barcode + OCR + copertine)."""
        if jpg_bytes:
            import io
            import tempfile
            from PIL import Image

            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".jpg")
            os.close(tmp_fd)
            try:
                with Image.open(io.BytesIO(jpg_bytes)) as im:
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
                    return True

                def _frozen_ocr_worker(path_arg: str = tmp_path) -> None:
                    try:
                        ocr_res = run_apple_vision_on_image(path_arg, mode="ocr")
                    except Exception:
                        ocr_res = {"barcodes": [], "lines": [], "visual_matches": []}
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
                return True

        if fallback_live_ocr:
            return bool(self.cam_session.request_ocr())
        return False

    def confirm_search_from_frozen_photo(self) -> None:
        self._ocr_waiting = True
        self.btn_capture_ocr.setEnabled(False)
        self.btn_capture_ocr.setText(
            "Ricerca in corso…" if self.lang == "it" else "Searching…"
        )
        req_id = self._begin_status_search("copertina" if self.lang == "it" else "cover", is_cover=True)
        self._pending_ocr_req_id = req_id
        if not self._dispatch_full_frame_ocr(self._frozen_full_jpg_bytes, fallback_live_ocr=True):
            self._ocr_waiting = False
            self.btn_capture_ocr.setEnabled(True)
            self.btn_capture_ocr.setText(
                "Cerca nel catalogo da questa foto (Invio)"
                if self.lang == "it"
                else "Search catalog from this photo (Enter)"
            )
            self._set_status_box(
                "warn",
                "Fotocamera non pronta per l'analisi della foto."
                if self.lang == "it"
                else "Camera not ready for photo analysis.",
            )
            return
        QTimer.singleShot(15000, lambda r=req_id: self._on_ocr_timeout(r))

    def _on_ocr_captured(self, payload_obj: object) -> None:
        self._cleanup_pending_frozen_tmp_paths()
        if not self._ocr_waiting or not isinstance(payload_obj, dict):
            return
        self._ocr_waiting = False
        req_id = getattr(self, "_pending_ocr_req_id", self._req_seq)
        barcodes = payload_obj.get("barcodes") or []
        lines = payload_obj.get("lines") or []
        visual_matches = list(payload_obj.get("visual_matches") or [])
        frame_bytes = bytes(
            (self._frozen_full_jpg_bytes if self._is_cover_frozen else self._last_full_jpg_bytes)
            or self._last_full_jpg_bytes
            or b""
        )
        cat = self.get_category_fn()
        auto_add = self.chk_burst.isChecked()

        def _cam_label() -> str:
            return "Fotocamera del computer" if self.lang == "it" else "Computer Camera"

        def _worker() -> None:
            eff_visual = visual_matches
            if not barcodes and frame_bytes:
                try:
                    if eff_visual or sys.platform != "darwin":
                        xp_matches = match_cover_artwork_from_bytes(frame_bytes)
                        eff_visual = merge_visual_matches(eff_visual, xp_matches)
                    elif not lines:
                        xp_matches = [
                            m for m in match_cover_artwork_from_bytes(frame_bytes)
                            if float(m.get("distance", 9.9)) <= 0.56
                        ]
                        eff_visual = merge_visual_matches([], xp_matches)
                except Exception:
                    pass
            res = self.scanner.process_remote_action(
                {
                    "action": "scan_ocr_lines",
                    "category": cat,
                    "barcodes": barcodes,
                    "lines": lines,
                    "visual_matches": eff_visual,
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

    def _begin_status_search(self, label: str, is_cover: bool = False) -> int:
        self._req_seq += 1
        req_id = self._req_seq
        self.list_candidates.hide()
        self.card_preview.hide()
        self._multi_candidates = []
        if is_cover:
            msg = (
                "● Foto copertina acquisita — Ricerca nel catalogo in corso…"
                if self.lang == "it"
                else "● Cover photo captured — Searching catalog…"
            )
        else:
            msg = (
                f"● Codice {label} acquisito — Ricerca nel catalogo in corso…"
                if self.lang == "it"
                else f"● Barcode {label} captured — Searching catalog…"
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
            not_found_code = str(res.get("code", "") or "").strip()
            if not_found_code in ("OCR Copertina", "Foto", "fotogramma fotocamera", "camera frame"):
                msg_nf = (
                    "Nessuna corrispondenza trovata dalla foto. Avvicina la copertina o il codice a barre, oppure inserisci il titolo manualmente."
                    if self.lang == "it"
                    else "No match found from photo. Move the cover or barcode closer, or enter the title manually."
                )
            else:
                msg_nf = (
                    f"Nessuna corrispondenza trovata per «{not_found_code}». Prova a scattare la copertina."
                    if self.lang == "it"
                    else f"No match found for «{not_found_code}». Try taking a photo of the cover."
                )
            self._set_status_box("warn", msg_nf)

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
        msg = (
            "Inserimento ignorato. Fotocamera pronta per la prossima scansione."
            if self.lang == "it"
            else "Skipped. Ready for next scan."
        )
        self.reset_to_acquisition_mode(status_text=msg, notify_parent=True)

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
