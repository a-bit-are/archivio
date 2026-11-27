"""Modelli tabellari Qt, proxy di ordinamento/filtro, delegate e comandi Undo/Redo."""
from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QRect, QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen, QUndoCommand
from PySide6.QtWidgets import QAbstractItemView, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTableView, QWidget

from .config import CATEGORIES
from .i18n import tr
from .store import Item
from .textutil import fold, search_fold
from .ui_theme import _resolve_ui_font_family


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
