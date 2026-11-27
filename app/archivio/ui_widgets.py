"""Widget personalizzati (CleanComboBox, StudioCheckBox, popup autocompletamento)."""
from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6.QtCore import QEvent, QModelIndex, QObject, QPoint, QPointF, QRect, QRectF, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHeaderView,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from .catalog import Suggestion
from .i18n import tr
from .ui_theme import _qobj_valid, _resolve_ui_font_family


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


class _BaseAutocompletePopup(QFrame):
    """Base non bloccante per menu a tendina di autocompletamento (senza furto di focus)."""

    dismissed = Signal()

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
                padding: 6px 10px;
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
        layout.addWidget(self.list_widget)
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
        """Scollega i listener globali prima che il parent venga chiuso/distrutto."""
        if self._cleaned_up:
            return
        self._cleaned_up = True
        self._anchor = None
        self._clear_items()
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

    def _clear_items(self) -> None:
        self.list_widget.clear()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
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

    def _place_for_anchor(self, anchor: QWidget, width: int, total_h: int) -> None:
        global_pos = anchor.mapToGlobal(QPoint(0, anchor.height() + 3))
        x = global_pos.x()
        y = global_pos.y()
        screen = QApplication.screenAt(global_pos) or QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            if avail.isValid():
                if x + width > avail.right() - 8:
                    x = max(avail.left() + 8, avail.right() - width - 8)
                if x < avail.left() + 8:
                    x = avail.left() + 8
                if y + total_h > avail.bottom() - 8:
                    above_y = anchor.mapToGlobal(QPoint(0, -total_h - 3)).y()
                    if above_y >= avail.top() + 8:
                        y = above_y
                    else:
                        y = max(avail.top() + 8, avail.bottom() - total_h - 8)
        self.setGeometry(x, y, width, total_h)
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


class SuggestionItemDelegate(QStyledItemDelegate):
    """Delegato editoriale a 2 righe per le proposte: titolo + badge fonte/edizioni e metadati secondari."""

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._lang = "it"
        fam = _resolve_ui_font_family()
        self._title_font = QFont(fam, 13, QFont.Weight.DemiBold)
        self._meta_font = QFont(fam, 11, QFont.Weight.Normal)
        self._badge_font = QFont(fam, 10, QFont.Weight.DemiBold)

    def set_language(self, lang: str) -> None:
        self._lang = lang if lang in ("it", "en") else "it"

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        return QSize(max(option.rect.width(), 200), 48)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:  # noqa: N802
        sug = index.data(Qt.ItemDataRole.UserRole)
        if not isinstance(sug, Suggestion):
            super().paint(painter, option, index)
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = QRectF(option.rect).adjusted(2.0, 1.5, -2.0, -1.5)
        is_sel = bool(option.state & QStyle.StateFlag.State_Selected)
        is_hover = bool(option.state & QStyle.StateFlag.State_MouseOver)

        if is_sel:
            bg_col = QColor("#141413")
        elif is_hover:
            bg_col = QColor("#F2F0EA")
        else:
            bg_col = QColor("#FFFFFF")

        path = QPainterPath()
        path.addRoundedRect(rect, 4.5, 4.5)
        painter.fillPath(path, bg_col)

        # Badge sorgente (+ numero edizioni se note)
        if sug.source == "archive":
            badge = "In archivio" if self._lang == "it" else "In archive"
        elif sug.from_isbn:
            badge = "ISBN" if sug.category == "Libri" else "EAN/UPC"
        elif sug.source == "catalog":
            badge = "Catalogo" if self._lang == "it" else "Catalog"
        else:
            badge = "Online"
        n_pubs = len(sug.publishers or [])
        if n_pubs > 1:
            badge = f"{badge} · {n_pubs} ed."

        fm_badge = QFontMetrics(self._badge_font)
        pill_w = min(float(fm_badge.horizontalAdvance(badge)) + 16.0, rect.width() * 0.40)
        pill_h = 18.0
        pill_rect = QRectF(rect.right() - 10.0 - pill_w, rect.top() + 6.0, pill_w, pill_h)

        if is_sel:
            pill_bg = QColor(255, 255, 255, 42)
            pill_fg = QColor("#F2F0EA")
        elif sug.source == "archive":
            pill_bg = QColor("#E7F2EA")
            pill_fg = QColor("#245C33")
        else:
            pill_bg = QColor("#EFECE6")
            pill_fg = QColor("#51504B")

        pill_path = QPainterPath()
        pill_path.addRoundedRect(pill_rect, 4.0, 4.0)
        painter.fillPath(pill_path, pill_bg)
        painter.setFont(self._badge_font)
        painter.setPen(pill_fg)
        painter.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, badge)

        # Riga 1: Titolo
        title_color = QColor("#FFFFFF") if is_sel else QColor("#141413")
        title_avail = max(40, int(pill_rect.left() - rect.left() - 18.0))
        fm_title = QFontMetrics(self._title_font)
        elided_title = fm_title.elidedText(sug.title or "", Qt.TextElideMode.ElideRight, title_avail)
        title_rect = QRectF(rect.left() + 10.0, rect.top() + 5.0, float(title_avail), 20.0)
        painter.setFont(self._title_font)
        painter.setPen(title_color)
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided_title)

        # Riga 2: Autore · Editore · Anno · Collana
        meta_parts: List[str] = []
        if sug.author:
            meta_parts.append(sug.author)
        if sug.publisher:
            pub_lbl = f"{sug.publisher} (+{n_pubs - 1})" if n_pubs > 1 else sug.publisher
            meta_parts.append(pub_lbl)
        if sug.year:
            meta_parts.append(sug.year)
        if sug.collection:
            coll_lbl = "Collana" if self._lang == "it" else "Series"
            meta_parts.append(f"{coll_lbl}: {sug.collection}")
        meta_str = "  ·  ".join(meta_parts)

        if meta_str:
            meta_color = QColor("#D4D2CC") if is_sel else QColor("#6E6C66")
            meta_avail = max(40, int(rect.width() - 20.0))
            fm_meta = QFontMetrics(self._meta_font)
            elided_meta = fm_meta.elidedText(meta_str, Qt.TextElideMode.ElideRight, meta_avail)
            meta_rect = QRectF(rect.left() + 10.0, rect.top() + 25.0, float(meta_avail), 18.0)
            painter.setFont(self._meta_font)
            painter.setPen(meta_color)
            painter.drawText(meta_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided_meta)

        painter.restore()


class SuggestionPopup(_BaseAutocompletePopup):
    """
    Menu flottante non bloccante per i suggerimenti in tempo reale (rendering editoriale a 2 righe).
    Non ruba il focus di digitazione alla QLineEdit e supporta frecce Su/Giù, Invio ed Esc.
    """

    suggestionAccepted = Signal(object)  # Emette l'oggetto Suggestion scelto

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self._suggestions: List[Suggestion] = []
        self._delegate = SuggestionItemDelegate(self.list_widget)
        self.list_widget.setItemDelegate(self._delegate)
        self.list_widget.setStyleSheet(
            "QListWidget { background-color: transparent; border: none; outline: 0; padding: 4px; }"
        )
        self.list_widget.itemClicked.connect(self._on_item_clicked)

    def _clear_items(self) -> None:
        self._suggestions.clear()
        self.list_widget.clear()

    def show_suggestions(self, anchor: QWidget, suggestions: List[Suggestion], lang: str = "it") -> None:
        self._anchor = anchor
        self._suggestions = list(suggestions)
        self._delegate.set_language(lang)
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
        row_h = 48
        visible_rows = min(len(self._suggestions), 7)
        total_h = visible_rows * row_h + 12
        width = max(anchor.width(), 560)
        self._place_for_anchor(anchor, width, total_h)

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


class PublisherSuggestionPopup(_BaseAutocompletePopup):
    """
    Menu a tendina type-ahead non bloccante per il campo Editore / Etichetta.
    Propone editori dell'opera, dell'archivio e canonici senza alterare i valori attestati di `in_pub`.
    """

    publisherAccepted = Signal(str)

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self._user_navigated = False
        self.list_widget.itemClicked.connect(self._on_item_clicked)

    def has_user_navigated(self) -> bool:
        return self._user_navigated

    def move_selection(self, delta: int) -> None:
        self._user_navigated = True
        super().move_selection(delta)

    def show_choices(self, anchor: QWidget, choices: List[Tuple[str, str]], lang: str = "it") -> None:
        self._anchor = anchor
        self._user_navigated = False
        self.list_widget.clear()
        if not choices:
            self.hide()
            return
        for pub_name, source_badge in choices:
            label = f"{pub_name}   [{source_badge}]" if source_badge else pub_name
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, pub_name)
            self.list_widget.addItem(item)
        self.list_widget.setCurrentRow(0)
        row_h = max(self.list_widget.sizeHintForRow(0), 32)
        visible_rows = min(len(choices), 7)
        total_h = visible_rows * row_h + 12
        width = max(anchor.width(), 320)
        self._place_for_anchor(anchor, width, total_h)

    def current_publisher(self) -> str:
        if not self.isVisible() or self.list_widget.count() == 0:
            return ""
        item = self.list_widget.currentItem() or self.list_widget.item(0)
        if not item:
            return ""
        return str(item.data(Qt.ItemDataRole.UserRole) or "")

    def accept_current(self) -> bool:
        pub = self.current_publisher()
        if not pub:
            return False
        self.hide()
        self.publisherAccepted.emit(pub)
        return True

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        pub = str(item.data(Qt.ItemDataRole.UserRole) or "")
        self.hide()
        if pub:
            self.publisherAccepted.emit(pub)
