"""Dialoghi secondari (modifica elemento, collana, salva con nome, impostazioni, scanner remoto)."""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QRunnable, QThreadPool, QTimer, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME, APP_VERSION
from .config import CATEGORIES, CATEGORY_PREFIXES, DEFAULT_FILES, Settings, cache_path, config_path, reset_config_file
from .catalog import CatalogService, EditionBundle, Suggestion, _publishers_match
from .i18n import tr
from .remote_scanner import RemoteScannerService, generate_qr_png
from .store import Item, scan_folder, unique_collections
from .textutil import fold, search_fold, surname_key
from .ui_theme import MACOS_STYLESHEET, _disable_mac_focus_rect, _qobj_valid
from .ui_widgets import CleanComboBox, StudioCheckBox, SuggestionPopup
from .ui_workers import (
    EditionFetchWorker,
    LocalSearchWorker,
    OnlineSearchWorker,
    _disconnect_worker_signals,
    _filter_colls_for_publisher,
    _merge_ranked_suggestions,
    _prioritize_choices,
)


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
        self._debounce_timer.setInterval(150)
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
        cur_yr = self.cb_year.currentText().strip()
        cur_coll = self.cb_coll.currentText().strip()
        resolved_yr, resolved_coll, should_clear_coll = self.catalog.resolve_publisher_metadata(
            pub_txt,
            category=self.category,
            author=self.ed_author.text().strip(),
            bundle=self._bundle,
            current_collection=cur_coll,
            current_year=cur_yr,
        )
        yrs = [self.cb_year.itemText(i) for i in range(self.cb_year.count()) if self.cb_year.itemText(i).strip()]
        pub_yrs = self._bundle.years_for_publisher(pub_txt) if self._bundle else []
        reordered_yrs = _prioritize_choices(yrs, pub_yrs)
        # In Modifica preserva l'anno già presente sull'elemento; compila solo se è vuoto.
        if not cur_yr and resolved_yr:
            self.cb_year.set_choices(reordered_yrs, resolved_yr, preserve_user_text=False)
        elif reordered_yrs != yrs:
            self.cb_year.set_choices(reordered_yrs, cur_yr, preserve_user_text=bool(cur_yr))
        target_coll = cur_coll
        if should_clear_coll:
            target_coll = resolved_coll or ""
        elif resolved_coll and not cur_coll:
            target_coll = resolved_coll
        self._refresh_dlg_collections(pub_txt, target_coll)

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
        raw_text = self.ed_folder.text().strip()
        if not raw_text:
            return
        target = os.path.abspath(os.path.expanduser(raw_text))
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
        self.ed_gh_url.editingFinished.connect(
            lambda: self._on_gh_url_changed(self.ed_gh_url.text())
        )
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
