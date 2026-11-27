"""
Finestra principale e punto di ingresso grafico di Archivio (Qt6 / PySide6-Essentials, LGPLv3).
"""
from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import (
    QCoreApplication,
    QEvent,
    QModelIndex,
    QObject,
    QPoint,
    QRect,
    QRunnable,
    QSize,
    QThreadPool,
    QTimer,
    Qt,
)
from PySide6.QtGui import QAction, QColor, QCursor, QFont, QIcon, QKeySequence, QMouseEvent, QUndoStack
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStatusBar,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME, APP_VERSION
from .catalog import (
    ITALIAN_PUBLISHERS_PRIORITY,
    CatalogService,
    EditionBundle,
    Suggestion,
    _fuzzy_score_cut,
    _publishers_match,
)
from .config import CATEGORIES, CATEGORY_PREFIXES, DEFAULT_FILES, Settings, cache_path, config_path, reset_config_file
from .i18n import tr
from .pdf_export import export_category_pdf
from .remote_scanner import (  # noqa: F401
    MacCameraSession,
    RemoteScannerService,
    generate_qr_png,
    list_mac_cameras,
    match_cover_artwork_from_bytes,
    merge_visual_matches,
    run_apple_vision_on_image,
)
from .store import Item, load_csv, save_csv, scan_folder, sort_items, unique_collections
from .textutil import fold, search_fold, sort_key_matches_author, surname_key
from .ui_camera import MacCameraScannerDialog  # noqa: F401
from .ui_dialogs import (  # noqa: F401
    AssignCollectionDialog,
    EditItemDialog,
    FirstRunSetupDialog,
    RemoteScannerDialog,
    SaveAsCategoryDialog,
    SettingsDialog,
)
from .ui_models import (  # noqa: F401
    ITEM_ROLE,
    LAST_INSERTED_ROLE,
    SEARCH_ROLE,
    SORT_ROLE,
    ArchiveItemDelegate,
    ArchiveProxyModel,
    ArchiveTableModel,
    ArchiveTableView,
    CategorySnapshotCommand,
)
from .ui_theme import (  # noqa: F401
    MACOS_STYLESHEET,
    _MAC_CB_FULLSCREEN_AUX_ACTIVE,
    _MAC_CB_FULLSCREEN_PRIMARY_ACTIVE,
    _MAC_FS_HANDLERS,
    _MAC_NSWINDOW_STYLE_FULLSCREEN,
    _MAC_ZOOM_HANDLERS,
    _apply_light_palette,
    _disable_mac_focus_rect,
    _get_mac_objc_bridge,
    _mac_nswindow,
    _make_studio_icon,
    _qobj_valid,
    _resolve_app_icon_path,
    _resolve_ui_font_family,
)
from .ui_widgets import (  # noqa: F401
    CleanComboBox,
    CleanHeaderView,
    PublisherSuggestionPopup,
    StudioCheckBox,
    SuggestionItemDelegate,
    SuggestionPopup,
    _BaseAutocompletePopup,
)
from .ui_workers import (  # noqa: F401
    EditionFetchWorker,
    EditionWorkerSignals,
    LocalSearchWorker,
    LocalSearchWorkerSignals,
    OnlineSearchWorker,
    SearchWorkerSignals,
    _disconnect_worker_signals,
    _filter_colls_for_publisher,
    _merge_ranked_suggestions,
    _prioritize_choices,
)


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
        self._last_edition_fetched_key: Tuple[str, str, str] = ("", "", "")
        self._form_synced_from_remote = False

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(150)
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
        self._menubar_actions: List[QAction] = []
        self._closing = False

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
        self._scanner_start_timer = QTimer(self)
        self._scanner_start_timer.setSingleShot(True)
        self._scanner_start_timer.timeout.connect(self._start_remote_scanner_if_open)
        self._scanner_start_timer.start(250)

    def _start_remote_scanner_if_open(self) -> None:
        if not getattr(self, "_closing", False):
            self.remote_scanner.start()

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

        self.pub_popup = PublisherSuggestionPopup(self)
        self.pub_popup.publisherAccepted.connect(self._on_pub_popup_accepted)

        # Collegamenti eventi campi di input
        self.in_title.textEdited.connect(lambda txt: self._on_query_edited("title", txt))
        self.in_author.textEdited.connect(self._on_author_field_edited)
        self.in_sort.textEdited.connect(self._on_sort_field_edited)
        self.in_pub.activated.connect(self._on_pub_choice_changed)
        self.in_coll.activated.connect(self._on_coll_choice_changed)
        self.in_year.activated.connect(self._on_year_choice_changed)
        if self.in_pub.lineEdit():
            self.in_pub.lineEdit().textEdited.connect(self._on_pub_edited)
            self.in_pub.lineEdit().installEventFilter(self)
        if self.in_coll.lineEdit():
            self.in_coll.lineEdit().textEdited.connect(self._on_coll_edited)
            self.in_coll.lineEdit().textChanged.connect(self._on_coll_text_changed)
            self.in_coll.lineEdit().installEventFilter(self)
        if self.in_year.lineEdit():
            self.in_year.lineEdit().textEdited.connect(self._on_year_edited)
            self.in_year.lineEdit().installEventFilter(self)

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
        for old_act in getattr(self, "_menubar_actions", []):
            old_act.deleteLater()
        self._menubar_actions = []

        def _reg(act: QAction) -> QAction:
            self._menubar_actions.append(act)
            return act

        # Menu File
        m_file = mb.addMenu(tr(self.lang, "menu_file"))

        act_new_item = _reg(QAction("Nuovo inserimento" if self.lang == "it" else "New Entry", self))
        act_new_item.setShortcut(QKeySequence("Ctrl+N"))
        act_new_item.triggered.connect(lambda: (self.reset_entry_form(), self.in_title.setFocus()))
        m_file.addAction(act_new_item)

        act_mac_cam = _reg(QAction(
            "Scansiona con Fotocamera del computer…" if self.lang == "it" else "Scan with Computer Camera…",
            self,
        ))
        act_mac_cam.setShortcut(QKeySequence("Ctrl+Shift+B"))
        act_mac_cam.triggered.connect(self.open_mac_camera_dialog)
        m_file.addAction(act_mac_cam)

        act_remote_scan = _reg(QAction(
            "Scansiona da Dispositivo Mobile…" if self.lang == "it" else "Scan from Mobile Device…",
            self,
        ))
        act_remote_scan.setShortcut(QKeySequence("Ctrl+B"))
        act_remote_scan.triggered.connect(self.open_remote_scanner_dialog)
        m_file.addAction(act_remote_scan)

        act_open_folder = _reg(QAction(tr(self.lang, "btn_change_folder"), self))
        act_open_folder.setShortcut(QKeySequence("Ctrl+O"))
        act_open_folder.triggered.connect(self._on_change_folder)
        m_file.addAction(act_open_folder)

        m_file.addSeparator()

        act_save = _reg(QAction(tr(self.lang, "btn_save"), self))
        act_save.setShortcut(QKeySequence.StandardKey.Save)
        act_save.triggered.connect(self.save_current_category)
        m_file.addAction(act_save)

        act_save_as = _reg(QAction(tr(self.lang, "btn_save_as"), self))
        act_save_as.setShortcut(QKeySequence.StandardKey.SaveAs)
        act_save_as.triggered.connect(self.save_current_as)
        m_file.addAction(act_save_as)

        act_save_all = _reg(QAction(tr(self.lang, "btn_save_all"), self))
        act_save_all.setShortcut(QKeySequence("Ctrl+Alt+S"))
        act_save_all.triggered.connect(self.save_all_categories)
        m_file.addAction(act_save_all)

        m_file.addSeparator()

        act_pdf_one = _reg(QAction(tr(self.lang, "btn_export_pdf"), self))
        act_pdf_one.setShortcut(QKeySequence("Ctrl+P"))
        act_pdf_one.triggered.connect(self.export_current_pdf)
        m_file.addAction(act_pdf_one)

        # Voce Preferenze / Impostazioni (su macOS finisce automaticamente nel menu Applicazione con ⌘,)
        act_prefs = _reg(QAction(tr(self.lang, "btn_settings"), self))
        act_prefs.setShortcut(QKeySequence.StandardKey.Preferences)
        act_prefs.setMenuRole(QAction.MenuRole.PreferencesRole)
        act_prefs.triggered.connect(self._open_settings_dialog)
        m_file.addAction(act_prefs)

        # Menu Modifica
        m_edit = mb.addMenu(tr(self.lang, "menu_edit"))

        act_undo = _reg(QAction(tr(self.lang, "btn_undo"), self))
        act_undo.setShortcut(QKeySequence.StandardKey.Undo)
        act_undo.triggered.connect(self.undo_last_action)
        m_edit.addAction(act_undo)

        act_redo = _reg(QAction(tr(self.lang, "btn_redo"), self))
        act_redo.setShortcut(QKeySequence.StandardKey.Redo)
        act_redo.triggered.connect(self.redo_last_action)
        m_edit.addAction(act_redo)

        m_edit.addSeparator()

        act_find = _reg(QAction(tr(self.lang, "search_placeholder"), self))
        act_find.setShortcut(QKeySequence.StandardKey.Find)
        act_find.triggered.connect(lambda: (self.ed_search.setFocus(), self.ed_search.selectAll()))
        m_edit.addAction(act_find)

        act_edit_row = _reg(QAction(tr(self.lang, "btn_edit"), self))
        act_edit_row.setShortcut(QKeySequence("Ctrl+E"))
        act_edit_row.triggered.connect(self.edit_selected_item)
        m_edit.addAction(act_edit_row)

        act_assign_coll = _reg(QAction(tr(self.lang, "ctx_assign_coll"), self))
        act_assign_coll.setShortcut(QKeySequence("Ctrl+G"))
        act_assign_coll.triggered.connect(self.assign_collection_to_selected)
        m_edit.addAction(act_assign_coll)

        act_del_row = _reg(QAction(tr(self.lang, "btn_delete"), self))
        act_del_row.setShortcut(QKeySequence("Ctrl+Backspace"))
        act_del_row.triggered.connect(self.delete_selected_items)
        m_edit.addAction(act_del_row)

        # Menu Vista (Passaggio rapido ⌘1 Libri, ⌘2 CD, ⌘3 Vinili)
        m_view = mb.addMenu(tr(self.lang, "menu_view"))
        for idx, cat in enumerate(CATEGORIES, start=1):
            act_cat = _reg(QAction(tr(self.lang, f"cat_{cat}"), self))
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
        sub_text = lines[1] if len(lines) > 1 else ""
        if self.settings.online_enabled and (getattr(self.settings, "discogs_token", "") or "").strip():
            sub_text = f"{sub_text} · Discogs" if sub_text else "Discogs"
        sub = f"<br><span style='color:#8A8882; font-size:10.5px;'>{sub_text}</span>" if sub_text else ""
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
            try:
                self.data_by_cat[cat] = load_csv(full_path, cat)
            except OSError as exc:
                QMessageBox.warning(self, tr(self.lang, "app_title"), str(exc))
                self.data_by_cat[cat] = []
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
        self._local_req_id += 1
        self._edition_req_id += 1
        self._debounce_timer.stop()
        self.popup.hide()
        if hasattr(self, "pub_popup"):
            self.pub_popup.hide()

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

        full_path = os.path.join(self.settings.folder, chosen)
        try:
            loaded = load_csv(full_path, cat)
        except OSError as exc:
            QMessageBox.warning(self, tr(self.lang, "app_title"), str(exc))
            self._refresh_sheet_combo()
            return
        self.active_file_by_cat[cat] = chosen
        self.settings.active_files[cat] = chosen
        self.settings.save()
        self.data_by_cat[cat] = loaded
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
        if self.dirty_by_cat.get(cat):
            res = QMessageBox.question(
                self,
                tr(self.lang, "app_title"),
                tr(self.lang, "msg_unsaved_quit"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            )
            if res == QMessageBox.StandardButton.Cancel:
                return
            if res == QMessageBox.StandardButton.Yes:
                self.save_current_category()
                if self.dirty_by_cat.get(cat):
                    return
        dlg = SaveAsCategoryDialog(cat, self.lang, self.settings.folder, self)
        dlg.setWindowTitle(tr(self.lang, "btn_new_sheet"))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        fn = dlg.filename()
        full_path = os.path.join(self.settings.folder, fn)
        if os.path.exists(full_path):
            ov_msg = (
                f"Il file '{fn}' esiste già. Sovrascriverlo con un foglio vuoto?"
                if self.lang == "it"
                else f"The file '{fn}' already exists. Overwrite it with a blank sheet?"
            )
            ans = QMessageBox.question(
                self,
                tr(self.lang, "app_title"),
                ov_msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
        if not self._write_csv_safe(full_path, []):
            return
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
    def reset_entry_form(self, _checked: bool = False, *, reset_camera: bool = True) -> None:
        """Azzera tutti i campi di input e i menu a tendina delle edizioni."""
        self._search_req_id += 1
        self._local_req_id += 1
        self._edition_req_id += 1
        self._debounce_timer.stop()
        self.popup.hide()
        if hasattr(self, "pub_popup"):
            self.pub_popup.hide()

        self._sort_manually_edited = False
        self._pub_manually_edited = False
        self._coll_manually_edited = False
        self._year_manually_edited = False
        self._form_synced_from_remote = False
        self._selected_work_id = ""
        self._current_work_collections: List[str] = []
        self._current_bundle = None
        self._last_edition_fetched_key = ("", "", "")

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
        if reset_camera:
            dlg = getattr(self, "_mac_cam_dialog", None)
            if dlg is not None:
                dlg.reset_to_acquisition_mode(notify_parent=False)

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
        1) Riordina gli anni compatibili e aggiorna l'Anno solo se non modificato manualmente.
        2) Aggiorna la Collana a quella specifica dell'edizione di quell'editore/anno,
           oppure se la collana attuale appartiene a un altro editore o anno incompatibile la rimuove.
        """
        p_clean = (pub_txt or "").strip()
        if not p_clean:
            self._pub_manually_edited = False
            self._refresh_collection_choices(preserve_current=True)
            return
        self._pub_manually_edited = True

        cur_coll = self.in_coll.currentText().strip()
        cur_yr = self.in_year.currentText().strip()
        resolved_yr, resolved_coll, should_clear_coll = self.catalog.resolve_publisher_metadata(
            p_clean,
            category=self.current_category,
            author=self.in_author.text().strip(),
            bundle=self._current_bundle,
            current_collection=cur_coll,
            current_year=cur_yr,
        )

        # 1) Riordina gli anni compatibili e aggiorna Anno solo se non modificato manualmente dall'utente
        existing_yrs = [self.in_year.itemText(i) for i in range(self.in_year.count()) if self.in_year.itemText(i).strip()]
        pub_yrs = self._current_bundle.years_for_publisher(p_clean) if self._current_bundle else []
        reordered_yrs = _prioritize_choices(existing_yrs, pub_yrs)
        if resolved_yr and not self._year_manually_edited:
            self.in_year.set_choices(reordered_yrs, resolved_yr, preserve_user_text=False)
        elif reordered_yrs != existing_yrs:
            self.in_year.set_choices(reordered_yrs, cur_yr, preserve_user_text=bool(cur_yr))

        # 2) Gestione collana: se l'attuale collana apparteneva a un altro editore o è incompatibile, la toglie
        target_coll = None
        if should_clear_coll:
            target_coll = resolved_coll or ""
            self._coll_manually_edited = False
        elif resolved_coll and not self._coll_manually_edited and (not cur_coll or resolved_coll != cur_coll):
            target_coll = resolved_coll

        if target_coll is not None:
            self._refresh_collection_choices(preserve_current=False, explicit_current=target_coll)
        else:
            self._refresh_collection_choices(preserve_current=True)

    def _gather_publisher_choices(self, query_text: str) -> List[Tuple[str, str]]:
        """Raccoglie candidati di autocomplete per Editore/Etichetta senza alterare `in_pub.count()`."""
        q_clean = (query_text or "").strip()
        if len(q_clean) < 2:
            return []
        q_fold = fold(q_clean)
        q_sfold = search_fold(q_clean)

        candidates: List[Tuple[str, str, int]] = []  # (display, badge, src_priority)
        seen_fold: set[str] = set()

        def _add_cand(name: str, badge: str, prio: int) -> None:
            cs = (name or "").strip()
            if not cs:
                return
            cf = fold(cs)
            if cf and cf not in seen_fold:
                seen_fold.add(cf)
                candidates.append((cs, badge, prio))

        ed_badge = "Edizione" if self.lang == "it" else "Edition"
        arch_badge = "In archivio" if self.lang == "it" else "In archive"
        pub_badge = "Editore" if self.lang == "it" else "Publisher"

        if self._current_bundle and self._current_bundle.publishers:
            for p in self._current_bundle.publishers:
                _add_cand(p, ed_badge, 0)
        for it in self.data_by_cat.get(self.current_category, []):
            if it.publisher:
                _add_cand(it.publisher, arch_badge, 1)
        if self.current_category == "Libri":
            for p in ITALIAN_PUBLISHERS_PRIORITY.values():
                _add_cand(p, pub_badge, 2)

        scored: List[Tuple[int, int, str, str, str]] = []
        for name, badge, prio in candidates:
            nf = fold(name)
            nsf = search_fold(name)
            words = nsf.split()
            if nf.startswith(q_fold):
                tier = 0
            elif any(w.startswith(q_sfold) for w in words):
                tier = 1
            elif q_fold in nf or (q_sfold and q_sfold in nsf):
                tier = 2
            elif len(q_fold) >= 4 and _fuzzy_score_cut(q_fold, nf, 78.0) >= 78.0:
                tier = 3
            else:
                continue
            scored.append((tier, prio, nf, name, badge))

        scored.sort(key=lambda t: (t[0], t[1], t[2]))
        out = [(t[3], t[4]) for t in scored[:12]]
        if len(out) == 1 and fold(out[0][0]) == q_fold:
            return []
        return out

    def _update_publisher_popup(self, query_text: str) -> None:
        if not hasattr(self, "pub_popup"):
            return
        le = self.in_pub.lineEdit()
        if not le or not le.hasFocus():
            self.pub_popup.hide()
            return
        choices = self._gather_publisher_choices(query_text)
        if choices:
            self.pub_popup.show_choices(self.in_pub, choices, self.lang)
        else:
            self.pub_popup.hide()

    def _on_pub_popup_accepted(self, pub_name: str) -> None:
        p_clean = (pub_name or "").strip()
        if not p_clean:
            return
        self.in_pub.blockSignals(True)
        try:
            self.in_pub.setEditText(p_clean)
            if self.in_pub.lineEdit():
                self.in_pub.lineEdit().deselect()
                self.in_pub.lineEdit().setCursorPosition(len(p_clean))
        finally:
            self.in_pub.blockSignals(False)
        self._apply_publisher_changed(p_clean)

    def _on_pub_edited(self, text: str) -> None:
        t_clean = text.strip()
        if not t_clean:
            self._pub_manually_edited = False
            if hasattr(self, "pub_popup"):
                self.pub_popup.hide()
            self._refresh_collection_choices(preserve_current=True)
            return
        self._pub_manually_edited = True
        self._update_publisher_popup(t_clean)
        t_fold = fold(t_clean)
        known_in_combo = self.in_pub.findText(t_clean, Qt.MatchFlag.MatchFixedString) >= 0
        known_in_bundle = bool(
            self._current_bundle
            and any(fold(p) == t_fold for p in (self._current_bundle.publishers or []))
        )
        if known_in_combo or known_in_bundle:
            self._apply_publisher_changed(t_clean)
            return

        # Editore personalizzato / non in elenco: non toccare l'Anno, ma rimuovi eventuale
        # collana auto-compilata che appartiene chiaramente a un altro editore.
        cur_coll = self.in_coll.currentText().strip()
        if cur_coll and not self._coll_manually_edited and len(t_clean) >= 2:
            col_pub, _ = self.catalog.resolve_collection_metadata(
                cur_coll,
                self.current_category,
                self.in_author.text().strip(),
                self._current_bundle,
            )
            if (
                col_pub
                and not _publishers_match(col_pub, t_clean)
                and not fold(col_pub).startswith(t_fold)
            ):
                self._refresh_collection_choices(preserve_current=False, explicit_current="")
                return
        self._refresh_collection_choices(preserve_current=True)

    def _on_pub_choice_changed(self, _idx: int) -> None:
        if hasattr(self, "pub_popup"):
            self.pub_popup.hide()
        pub_txt = self.in_pub.currentText().strip()
        self._apply_publisher_changed(pub_txt)

    def _apply_year_changed(self, yr_txt: str) -> None:
        """
        Quando l'utente seleziona o inserisce un Anno:
        1) Riordina gli editori compatibili e aggiorna la Casa Editrice / Etichetta solo se non modificata manualmente.
        2) Aggiorna la Collana dell'edizione compatibile con editore effettivo e anno,
           oppure se la collana attuale era incompatibile la rimuove.
        """
        y_clean = (yr_txt or "").strip()
        if not y_clean:
            self._year_manually_edited = False
            return
        if len(y_clean) < 4:
            return
        self._year_manually_edited = True

        cur_coll = self.in_coll.currentText().strip()
        cur_pub = self.in_pub.currentText().strip()
        resolved_pub, resolved_coll, should_clear_coll = self.catalog.resolve_year_metadata(
            y_clean,
            category=self.current_category,
            author=self.in_author.text().strip(),
            bundle=self._current_bundle,
            current_collection=cur_coll,
            current_publisher=cur_pub,
            preserve_publisher=self._pub_manually_edited,
        )

        # 1) Riordina gli editori attestati per quest'anno e aggiorna Editore solo se non modificato manualmente
        existing_pubs = [self.in_pub.itemText(i) for i in range(self.in_pub.count()) if self.in_pub.itemText(i).strip()]
        yr_pubs = self._current_bundle.publishers_for_year(y_clean) if self._current_bundle else []
        reordered_pubs = _prioritize_choices(existing_pubs, yr_pubs)
        if resolved_pub and not self._pub_manually_edited:
            self.in_pub.set_choices(reordered_pubs, resolved_pub, preserve_user_text=False)
        elif reordered_pubs != existing_pubs:
            self.in_pub.set_choices(reordered_pubs, cur_pub, preserve_user_text=bool(cur_pub))

        # 2) Gestione collana per quest'anno ed editore effettivo
        target_coll = None
        if should_clear_coll:
            target_coll = resolved_coll or ""
            self._coll_manually_edited = False
        elif resolved_coll and not self._coll_manually_edited and (not cur_coll or resolved_coll != cur_coll):
            target_coll = resolved_coll

        if target_coll is not None:
            self._refresh_collection_choices(preserve_current=False, explicit_current=target_coll)
        elif resolved_pub and not self._pub_manually_edited:
            self._refresh_collection_choices(preserve_current=True)

    def _on_year_choice_changed(self, _idx: int) -> None:
        yr_txt = self.in_year.currentText().strip()
        self._apply_year_changed(yr_txt)

    def _on_year_edited(self, text: str) -> None:
        t_clean = text.strip()
        if not t_clean:
            self._year_manually_edited = False
            return
        self._year_manually_edited = True
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
        Editore / Casa Discografica e Anno di uscita (se noti per quella specifica collana/edizione)
        senza marcare tali campi come modificati manualmente.
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
            self.in_pub.set_choices(existing_pubs, pub_c, preserve_user_text=False)
        if yr_c and (overwrite_year or not self.in_year.currentText().strip()):
            existing_yrs = [self.in_year.itemText(i) for i in range(self.in_year.count()) if self.in_year.itemText(i).strip()]
            self.in_year.set_choices(existing_yrs, yr_c, preserve_user_text=False)

    def _on_coll_choice_changed(self, _idx: int) -> None:
        self._coll_manually_edited = True
        self._apply_collection_metadata(
            self.in_coll.currentText().strip(),
            overwrite_pub=not self._pub_manually_edited,
            overwrite_year=not self._year_manually_edited,
        )

    def _on_coll_edited(self, text: str) -> None:
        self._coll_manually_edited = True
        # Se il testo digitato coincide esattamente con una delle collane note nel menu, applica i metadati
        t_clean = text.strip()
        if t_clean and self.in_coll.findText(t_clean, Qt.MatchFlag.MatchFixedString) >= 0:
            self._apply_collection_metadata(
                t_clean,
                overwrite_pub=not self._pub_manually_edited,
                overwrite_year=not self._year_manually_edited,
            )

    def _on_coll_text_changed(self, text: str) -> None:
        if getattr(self.in_coll, "_updating_choices", False):
            return
        if text == "":
            self._coll_manually_edited = True

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
        if (
            getattr(self, "_form_synced_from_remote", False)
            and not self.in_title.text().strip()
            and not self.in_author.text().strip()
        ):
            self.reset_entry_form(reset_camera=True)
            return
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
        widget = self._suggestion_anchor(field_mode)
        if not widget.hasFocus():
            return
        ctx_author, _ctx_title = self._suggestion_context(field_mode)
        has_author_ctx = field_mode == "title" and len(ctx_author) >= 2
        if not has_author_ctx and len(widget.text().strip()) < 2:
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
        self._local_req_id += 1
        self._debounce_timer.stop()
        self.popup.hide()
        if hasattr(self, "pub_popup"):
            self.pub_popup.hide()

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
        self._last_edition_fetched_key = (self.current_category, search_fold(title), search_fold(author))
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
        cur_yr_txt = self.in_year.currentText().strip()
        from_barcode_preview = getattr(self, "_remote_preview_from_barcode", False)

        invalid_cd_yr = (
            self.current_category == "CD"
            and cur_yr_txt.isdigit()
            and int(cur_yr_txt) < 1982
            and not self._year_manually_edited
        )

        use_default_pub = bool(
            not cur_pub_txt
            and not self._pub_manually_edited
            and not from_barcode_preview
            and bundle.default_publisher
        )
        use_default_yr = bool(
            (not cur_yr_txt or invalid_cd_yr)
            and not self._year_manually_edited
            and not from_barcode_preview
            and bundle.default_year
        )
        preserve_pub = self._pub_manually_edited or (bool(cur_pub_txt) and not use_default_pub)
        preserve_yr = self._year_manually_edited or (bool(cur_yr_txt) and not use_default_yr)

        target_pub = cur_pub_txt
        if use_default_pub:
            if cur_yr_txt and not invalid_cd_yr:
                yr_pubs = bundle.publishers_for_year(cur_yr_txt)
                target_pub = yr_pubs[0] if yr_pubs else bundle.default_publisher
            else:
                target_pub = bundle.default_publisher

        target_yr = cur_yr_txt
        if use_default_yr:
            if target_pub:
                pub_yrs = bundle.years_for_publisher(target_pub)
                if pub_yrs:
                    target_yr = (
                        bundle.default_year
                        if (
                            bundle.default_publisher
                            and _publishers_match(target_pub, bundle.default_publisher)
                            and bundle.default_year in pub_yrs
                        )
                        else pub_yrs[0]
                    )
                else:
                    target_yr = bundle.default_year
            else:
                target_yr = bundle.default_year

        merged_pubs = list(bundle.publishers)
        if cur_pub_txt and all(fold(p) != fold(cur_pub_txt) for p in merged_pubs):
            merged_pubs.insert(0, cur_pub_txt)
        if target_yr:
            merged_pubs = _prioritize_choices(merged_pubs, bundle.publishers_for_year(target_yr))

        merged_yrs = list(bundle.years)
        if cur_yr_txt and not invalid_cd_yr and cur_yr_txt not in merged_yrs:
            merged_yrs.insert(0, cur_yr_txt)
        if target_pub:
            merged_yrs = _prioritize_choices(merged_yrs, bundle.years_for_publisher(target_pub))

        if merged_pubs or target_pub:
            self.in_pub.set_choices(
                merged_pubs,
                current_text=target_pub or (merged_pubs[0] if merged_pubs else ""),
                preserve_user_text=preserve_pub,
            )
        if merged_yrs or target_yr:
            self.in_year.set_choices(
                merged_yrs,
                current_text=target_yr or (merged_yrs[0] if merged_yrs else ""),
                preserve_user_text=preserve_yr,
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
                overwrite_pub=not preserve_pub,
                overwrite_year=not preserve_yr,
            )

        self._show_edition_badge(
            self.in_pub.count(),
            self.in_year.count(),
            self.in_coll.count(),
            loading=False,
        )

        # Se la finestra fotocamera Mac sta mostrando la card di anteprima dello stesso titolo,
        # aggiorna in tempo reale etichetta/anno/ordinamento con i dati ufficiali delle edizioni.
        dlg = getattr(self, "_mac_cam_dialog", None)
        if dlg is not None and dlg.isVisible() and isinstance(getattr(dlg, "_current_preview_item", None), dict):
            prev_it = dlg._current_preview_item
            if search_fold(str(prev_it.get("title") or "")) == search_fold(bundle.title):
                new_pub = self.in_pub.currentText().strip()
                new_yr = self.in_year.currentText().strip()
                new_sort = self.in_sort.text().strip()
                if new_pub:
                    prev_it["publisher"] = new_pub
                if new_yr:
                    prev_it["year"] = new_yr
                if new_sort:
                    prev_it["author_sort"] = new_sort
                details = " • ".join(
                    str(x)
                    for x in (prev_it.get("publisher"), prev_it.get("collection"), prev_it.get("year"))
                    if x
                )
                dlg.lbl_card_details.setText(details)

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

    def _maybe_fetch_editions_for_manual_entry(self) -> None:
        if not self.settings.online_enabled:
            return
        title = self.in_title.text().strip()
        author = self.in_author.text().strip()
        if len(title) < 2 or len(author) < 2:
            return
        key = (self.current_category, search_fold(title), search_fold(author))
        if key == self._last_edition_fetched_key:
            return
        if (
            self._current_bundle
            and self._current_bundle.category == key[0]
            and search_fold(self._current_bundle.title) == key[1]
            and search_fold(self._current_bundle.author) == key[2]
        ):
            self._last_edition_fetched_key = key
            return
        self.lbl_edition_badge.setText(tr(self.lang, "editions_loading"))
        self.lbl_edition_badge.show()
        self._start_edition_fetch("", title, author)

    # ------------------------------------------------------------------
    # GESTIONE TASTIERA NEI CAMPI DI INPUT (POPUP E INVIO)
    # ------------------------------------------------------------------
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        pub_le = self.in_pub.lineEdit()
        coll_le = self.in_coll.lineEdit()
        year_le = self.in_year.lineEdit()
        edition_line_edits = tuple(w for w in (pub_le, coll_le, year_le) if w is not None)

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

            if not self.popup.isVisible() and obj in (self.in_title, self.in_author) and key == Qt.Key.Key_Down:
                field_mode = "title" if obj is self.in_title else "author"
                q_txt = obj.text()
                has_ctx = field_mode == "title" and len(self.in_author.text().strip()) >= 2
                if len(q_txt.strip()) >= 2 or has_ctx:
                    self._on_query_edited(field_mode, q_txt)
                    return True

            if hasattr(self, "pub_popup") and self.pub_popup.isVisible() and obj in (pub_le, self.in_pub):
                if key == Qt.Key.Key_Down:
                    self.pub_popup.move_selection(+1)
                    return True
                if key == Qt.Key.Key_Up:
                    self.pub_popup.move_selection(-1)
                    return True
                if key == Qt.Key.Key_Tab:
                    if self.pub_popup.accept_current():
                        return True
                if key == Qt.Key.Key_Escape:
                    self.pub_popup.hide()
                    return True
                if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    cur_pub = self.pub_popup.current_publisher()
                    if self.pub_popup.has_user_navigated() or (
                        cur_pub and fold(self.in_pub.currentText().strip()) != fold(cur_pub)
                    ):
                        if self.pub_popup.accept_current():
                            return True
                    self.pub_popup.hide()

            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and (
                obj in (self.in_title, self.in_author, self.in_sort, self.in_pub, self.in_coll, self.in_year)
                or obj in edition_line_edits
            ):
                self.add_item_from_form()
                return True

        elif event.type() == QEvent.Type.FocusIn:
            if obj is self.in_title:
                if not self.in_title.text().strip() and len(self.in_author.text().strip()) >= 2:
                    QTimer.singleShot(20, lambda: self._on_query_edited("title", self.in_title.text()))
            elif obj in edition_line_edits or obj in (self.in_pub, self.in_coll, self.in_year):
                self._maybe_fetch_editions_for_manual_entry()

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
            elif obj in (pub_le, self.in_pub) and hasattr(self, "pub_popup"):
                clicking_pub_popup = (
                    self.pub_popup.isVisible()
                    and QApplication.mouseButtons() != Qt.MouseButton.NoButton
                    and self.pub_popup.geometry().contains(QCursor.pos())
                )
                if not clicking_pub_popup:
                    self.pub_popup.hide()
            if isinstance(obj, QLineEdit):
                obj.deselect()

        return super().eventFilter(obj, event)

    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)
        # La tendina è ancorata in coordinate assolute: se la finestra si sposta o cambia dimensione la chiudiamo
        if hasattr(self, "popup") and self.popup.isVisible():
            self._dismiss_suggestions()
        if hasattr(self, "pub_popup") and self.pub_popup.isVisible():
            self.pub_popup.hide()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "popup") and self.popup.isVisible():
            self._dismiss_suggestions()
        if hasattr(self, "pub_popup") and self.pub_popup.isVisible():
            self.pub_popup.hide()

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
                category=tr(self.lang, f"cat_{category}"),
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
        if hasattr(self, "pub_popup"):
            self.pub_popup.hide()
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
                latest_list = list(self.data_by_cat.get(cat, []))
                target_idx = next((idx for idx, it in enumerate(latest_list) if it is target), None)
                if target_idx is None:
                    return
                was_last = (self.last_inserted_by_cat.get(cat) is target)
                latest_list[target_idx] = dlg.updated_item()
                self._commit_category_change(
                    cat, latest_list, "Modifica elemento" if self.lang == "it" else "Edit item"
                )
                updated = self.data_by_cat[cat][target_idx]
                if was_last:
                    self.last_inserted_by_cat[cat] = updated
                    self.table_model.set_last_inserted_item(updated)
                src_idx = self.table_model.index(target_idx, 0)
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
        selected_targets = [cur_list[r] for r in rows]
        initial_coll = selected_targets[0].collection
        dlg = AssignCollectionDialog(
            category=cat,
            lang=self.lang,
            count=len(selected_targets),
            existing_collections=existing_colls,
            initial_value=initial_coll,
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_coll = dlg.collection_value()
        latest_list = list(self.data_by_cat.get(cat, []))
        target_ids = {id(it) for it in selected_targets}
        changed = False
        for idx, it in enumerate(latest_list):
            if id(it) in target_ids:
                latest_list[idx] = it.with_changes(collection=new_coll)
                changed = True
        if not changed:
            return
        self._commit_category_change(
            cat,
            latest_list,
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
        except (OSError, RuntimeError) as exc:
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
        if os.path.exists(full_path) and fn != self.active_file_by_cat.get(cat):
            ov_msg = (
                f"Il file '{fn}' esiste già. Sovrascriverlo?"
                if self.lang == "it"
                else f"The file '{fn}' already exists. Overwrite it?"
            )
            ans = QMessageBox.question(
                self,
                tr(self.lang, "app_title"),
                ov_msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                self._clear_lingering_selections()
                return
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
            col_name = tr(self.lang, "col_title")
            sort_info = f"{col_name} ({order_az})"
        elif sec in (ArchiveTableModel.COL_AUTHOR, ArchiveTableModel.COL_SORT):
            col_name = tr(self.lang, "col_author_book" if category == "Libri" else "col_author_music")
            sort_info = f"{col_name} ({order_az})"
        elif sec == ArchiveTableModel.COL_PUB:
            col_name = tr(self.lang, "col_pub_book" if category == "Libri" else "col_pub_music")
            sort_info = f"{col_name} ({order_az})"
        elif sec == ArchiveTableModel.COL_COLL:
            col_name = tr(self.lang, "col_coll_book" if category == "Libri" else "col_coll_music")
            sort_info = f"{col_name} ({order_az})"
        elif sec == ArchiveTableModel.COL_YEAR:
            col_name = tr(self.lang, "col_year")
            sort_info = f"{col_name} ({order_num})"
        else:
            col_name = tr(self.lang, "col_author_book" if category == "Libri" else "col_author_music")
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
        except (OSError, RuntimeError) as exc:
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
                self.remote_scanner.online_enabled = self.settings.online_enabled
                self.remote_scanner.set_connection_mode(
                    self.settings.scanner_mode, self.settings.scanner_gh_pages_url
                )
                self._reset_cached_scanner_dialogs()
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
            self._reset_cached_scanner_dialogs()
            self._apply_language()
        else:
            self._update_online_indicator()

    def _reset_cached_scanner_dialogs(self) -> None:
        if self._remote_dialog is not None:
            try:
                self._remote_dialog.close()
                self._remote_dialog.deleteLater()
            except Exception:
                pass
            self._remote_dialog = None
        if self._mac_cam_dialog is not None:
            try:
                self._mac_cam_dialog._force_close_now = True
                self._mac_cam_dialog.cam_session.stop()
                if self._mac_cam_dialog.isFullScreen():
                    self._mac_cam_dialog.showNormal()
                self._mac_cam_dialog.close()
                self._mac_cam_dialog.deleteLater()
            except Exception:
                pass
            self._mac_cam_dialog = None

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
        self._form_synced_from_remote = True
        badge_low = (badge_text or "").lower()
        self._remote_preview_from_barcode = any(
            kw in badge_low for kw in ("barcode", "isbn", "ean", "upc")
        )
        self._sort_manually_edited = False
        self._pub_manually_edited = False
        self._coll_manually_edited = bool(item_obj.collection.strip())
        self._year_manually_edited = False

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
        self._closing = True
        self._search_req_id += 1
        self._local_req_id += 1
        self._edition_req_id += 1
        if hasattr(self, "_scanner_start_timer"):
            self._scanner_start_timer.stop()
        self._debounce_timer.stop()
        if hasattr(self, "_status_timer"):
            self._status_timer.stop()
        for w_list in (getattr(self, "_active_workers", []), getattr(self, "_local_workers", [])):
            for w in list(w_list):
                sigs = getattr(w, "signals", None)
                if sigs is not None:
                    try:
                        sigs.blockSignals(True)
                    except Exception:
                        pass
            w_list.clear()
        if hasattr(self, "_local_pool"):
            self._local_pool.clear()
        if hasattr(self, "thread_pool") and self.thread_pool is not None:
            self.thread_pool.clear()
        if hasattr(self, "popup"):
            self.popup.cleanup()
        if hasattr(self, "pub_popup"):
            self.pub_popup.cleanup()
        if self._remote_dialog is not None:
            try:
                self._remote_dialog.close()
            except Exception:
                pass
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
