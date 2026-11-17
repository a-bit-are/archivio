"""
Esportazione PDF vettoriale ad alta risoluzione con supporto Unicode completo e impaginazione editoriale.

Utilizza il motore nativo Qt6 (`QPdfWriter` + `QPainter`, licenza LGPLv3),
garantendo resa tipografica impeccabile per caratteri speciali, accenti e simboli tipografici.
"""
from __future__ import annotations

import datetime
import os
from typing import List

from PySide6.QtCore import QMarginsF, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QPageLayout,
    QPageSize,
    QPainter,
    QPdfWriter,
    QPen,
)

from .i18n import tr
from .store import Item

IT_MONTHS = [
    "",
    "gennaio",
    "febbraio",
    "marzo",
    "aprile",
    "maggio",
    "giugno",
    "luglio",
    "agosto",
    "settembre",
    "ottobre",
    "novembre",
    "dicembre",
]

EN_MONTHS = [
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


def _pick_pdf_font_family() -> str:
    installed = set(QFontDatabase.families())
    for fam in ("Avenir Next", "Segoe UI", "Helvetica Neue", "DejaVu Sans", "Noto Sans", "Arial"):
        if fam in installed:
            return fam
    return "Sans Serif"


def _format_date(date: datetime.date, lang: str) -> str:
    if lang == "it":
        return f"{date.day} {IT_MONTHS[date.month]} {date.year}"
    return f"{EN_MONTHS[date.month]} {date.day}, {date.year}"


def format_sheet_title(raw: str, default_category: str = "") -> str:
    """
    Formatta una stringa di foglio o nome file (es. 'libri_musica', 'libri_musica.csv', 'cd_jazz')
    in un titolo editoriale pulito ed elegante (es. 'Libri Musica', 'CD Jazz').
    Preserva acronimi musicali ed editoriali noti (CD, LP, EP, DVD, SACD, MC).
    """
    if not raw:
        return default_category
    base = os.path.splitext(os.path.basename(raw))[0].strip()
    if not base:
        return default_category
    cleaned = base.replace("_", " ").replace("-", " ")
    parts = cleaned.split()
    if not parts:
        return default_category
    acronyms = {"cd": "CD", "lp": "LP", "ep": "EP", "dvd": "DVD", "sacd": "SACD", "mc": "MC", "isbn": "ISBN", "sbn": "SBN"}
    out = []
    for p in parts:
        p_lower = p.lower()
        if p_lower in acronyms:
            out.append(acronyms[p_lower])
        else:
            out.append(p.capitalize())
    return " ".join(out)


def export_category_pdf(
    file_path: str,
    category: str,
    items: List[Item],
    lang: str = "it",
    sort_label: str = "",
    group_by_collection: bool = True,
    sheet_title: str = "",
) -> int:
    """
    Genera un documento PDF A4 vettoriale moderno, elegante ed estremamente leggibile.
    - Titolo fedele al foglio/file attivo (es. 'libri_musica' anziché il generico 'Libri')
    - Sfondo bianco pulito senza righe alternate (niente zebra striping da foglio di calcolo)
    - Gerarchia tipografica nitida (Titolo SemiBold in evidenza, autore distinto, editore elegante)
    - Separatori orizzontali ultra-delicati e niente trattini ripetitivi nelle celle vuote
    """
    writer = QPdfWriter(file_path)
    writer.setResolution(300)
    page_layout = QPageLayout(
        QPageSize(QPageSize.PageSizeId.A4),
        QPageLayout.Orientation.Portrait,
        QMarginsF(16.0, 16.0, 16.0, 16.0),
        QPageLayout.Unit.Millimeter,
    )
    writer.setPageLayout(page_layout)

    cat_label = tr(lang, f"cat_{category}")
    clean_sheet = sheet_title.strip() if sheet_title else os.path.splitext(os.path.basename(file_path))[0]
    display_title = format_sheet_title(clean_sheet, default_category=cat_label)

    writer.setTitle(f"{tr(lang, 'app_title')} — {display_title}")
    writer.setCreator(tr(lang, "app_title"))

    painter = QPainter()
    if not painter.begin(writer):
        raise OSError(f"Impossibile scrivere il file PDF: {file_path}")

    try:
        rect = painter.viewport()
        w = float(rect.width())
        h = float(rect.height())

        font_family = _pick_pdf_font_family()

        # Font con pesi e spaziature proporzionate per massima leggibilità
        f_eyebrow = QFont(font_family, 8.2, QFont.Weight.DemiBold)
        f_eyebrow.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.4)
        f_title = QFont(font_family, 23, QFont.Weight.Bold)
        f_meta = QFont(font_family, 8.8, QFont.Weight.Normal)
        f_header = QFont(font_family, 8.5, QFont.Weight.DemiBold)
        f_header.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)

        f_num = QFont(font_family, 8.2, QFont.Weight.Normal)
        f_title_cell = QFont(font_family, 10.0, QFont.Weight.DemiBold)
        f_author_cell = QFont(font_family, 9.4, QFont.Weight.Medium)
        f_pub_cell = QFont(font_family, 8.8, QFont.Weight.Normal)
        f_coll_cell = QFont(font_family, 8.8, QFont.Weight.Normal)
        f_year_cell = QFont(font_family, 8.8, QFont.Weight.Normal)
        f_group = QFont(font_family, 9.0, QFont.Weight.DemiBold)
        f_group.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)
        f_footer = QFont(font_family, 7.8, QFont.Weight.Normal)

        has_any_collection = any(bool(it.collection) for it in items)
        col_author_lbl = tr(lang, "col_author_book") if category == "Libri" else tr(lang, "col_author_music")
        col_pub_lbl = tr(lang, "col_pub_book") if category == "Libri" else tr(lang, "col_pub_music")
        col_coll_lbl = tr(lang, "col_coll_book") if category == "Libri" else tr(lang, "col_coll_music")

        # Bilanciamento proporzionale delle colonne: molto più spazio al Titolo
        if has_any_collection:
            headers = [
                "#",
                tr(lang, "col_title"),
                col_author_lbl,
                col_pub_lbl,
                col_coll_lbl,
                tr(lang, "col_year"),
            ]
            col_ratios = [0.040, 0.390, 0.230, 0.150, 0.115, 0.075]
        else:
            headers = [
                "#",
                tr(lang, "col_title"),
                col_author_lbl,
                col_pub_lbl,
                tr(lang, "col_year"),
            ]
            col_ratios = [0.040, 0.435, 0.260, 0.190, 0.075]

        col_widths = [w * r for r in col_ratios]
        col_x = [0.0]
        for cw in col_widths[:-1]:
            col_x.append(col_x[-1] + cw)

        # Palette tipografica monocromatica e pulita
        c_ink = QColor("#141413")
        c_author = QColor("#2E2C28")
        c_pub = QColor("#5E5C56")
        c_coll = QColor("#141413")
        c_muted = QColor("#76746E")
        c_rule = QColor("#141413")
        c_hairline = QColor("#EAE7E0")
        c_grp_bg = QColor("#F9F8F5")
        c_grp_border = QColor("#E2DFD7")
        c_footer_line = QColor("#DFDDD6")

        fm_eyebrow = QFontMetricsF(f_eyebrow, writer)
        fm_title = QFontMetricsF(f_title, writer)
        fm_meta = QFontMetricsF(f_meta, writer)
        fm_hdr = QFontMetricsF(f_header, writer)
        fm_num = QFontMetricsF(f_num, writer)
        fm_title_cell = QFontMetricsF(f_title_cell, writer)
        fm_author_cell = QFontMetricsF(f_author_cell, writer)
        fm_pub_cell = QFontMetricsF(f_pub_cell, writer)
        fm_coll_cell = QFontMetricsF(f_coll_cell, writer)
        fm_year_cell = QFontMetricsF(f_year_cell, writer)
        fm_footer = QFontMetricsF(f_footer, writer)

        row_h = 92.0
        hdr_h = 76.0
        grp_h = 70.0
        footer_h = 56.0
        usable_bottom = h - footer_h

        today = datetime.date.today()
        today_str = _format_date(today, lang)

        if category == "Libri":
            if lang == "en":
                unit_str = f"{len(items)} volumes" if len(items) != 1 else "1 volume"
            else:
                unit_str = f"{len(items)} volumi" if len(items) != 1 else "1 volume"
        else:
            if lang == "en":
                unit_str = f"{len(items)} albums" if len(items) != 1 else "1 album"
            else:
                unit_str = f"{len(items)} album" if len(items) != 1 else "1 album"

        meta_parts = [unit_str]
        if sort_label:
            meta_parts.append(sort_label)
        meta_parts.append(today_str)
        meta_line = "   •   ".join(meta_parts)

        page_num = 1
        pad = 10.0
        last_col_idx = len(headers) - 1
        current_coll_base = ""

        # Calcola conteggio elementi per ciascuna collana (se group_by_collection attivo)
        coll_counts: dict[str, int] = {}
        if group_by_collection:
            for it in items:
                if it.k_collection_base:
                    coll_counts[it.k_collection_base] = coll_counts.get(it.k_collection_base, 0) + 1

        def draw_page_header(is_first_page: bool) -> float:
            y = 0.0
            if is_first_page:
                # 1. Eyebrow categoria
                painter.setFont(f_eyebrow)
                painter.setPen(c_muted)
                eb_h = fm_eyebrow.height() * 1.25
                app_title_str = tr(lang, "app_title").upper()
                painter.drawText(
                    QRectF(0, y, w, eb_h),
                    int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                    f"{app_title_str}  /  {cat_label.upper()}",
                )
                y += eb_h + 6.0

                # 2. Titolo effettivo del documento/foglio (es. 'libri_musica')
                painter.setFont(f_title)
                painter.setPen(c_ink)
                th = fm_title.height() * 1.15
                painter.drawText(
                    QRectF(0, y, w, th),
                    int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                    display_title,
                )
                y += th + 4.0

                # 3. Riga metadati chiara e distesa
                painter.setFont(f_meta)
                painter.setPen(c_muted)
                mh = fm_meta.height() * 1.25
                painter.drawText(
                    QRectF(0, y, w, mh),
                    int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                    meta_line,
                )
                y += mh + 18.0
            else:
                painter.setFont(f_meta)
                painter.setPen(c_muted)
                sub_h = fm_meta.height() * 1.25
                painter.drawText(
                    QRectF(0, y, w, sub_h),
                    int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                    f"{display_title}  —  {cat_label}  ({tr(lang, 'pdf_page')} {page_num})",
                )
                y += sub_h + 14.0

            # Intestazione tabella: pulita, sfondo bianco con due cornici orizzontali nitide
            painter.setPen(QPen(c_rule, 1.3))
            painter.drawLine(QPointF(0, y), QPointF(w, y))

            painter.setFont(f_header)
            painter.setPen(c_ink)
            for idx, h_text in enumerate(headers):
                align = Qt.AlignmentFlag.AlignRight if idx in (0, last_col_idx) else Qt.AlignmentFlag.AlignLeft
                avail_w = col_widths[idx] - 2 * pad
                elided_h = fm_hdr.elidedText(h_text.upper(), Qt.TextElideMode.ElideRight, avail_w)
                painter.drawText(
                    QRectF(col_x[idx] + pad, y, avail_w, hdr_h),
                    int(align | Qt.AlignmentFlag.AlignVCenter),
                    elided_h,
                )

            painter.setPen(QPen(c_rule, 1.1))
            painter.drawLine(QPointF(0, y + hdr_h), QPointF(w, y + hdr_h))
            return y + hdr_h

        def draw_page_footer(pno: int) -> None:
            painter.setPen(QPen(c_footer_line, 0.7))
            painter.drawLine(QPointF(0, h - footer_h + 8.0), QPointF(w, h - footer_h + 8.0))

            painter.setFont(f_footer)
            painter.setPen(c_muted)
            painter.drawText(
                QRectF(0, h - footer_h + 16.0, w, footer_h - 16.0),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                f"{tr(lang, 'app_title')}  •  {display_title}",
            )
            painter.drawText(
                QRectF(0, h - footer_h + 16.0, w, footer_h - 16.0),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                f"{tr(lang, 'pdf_page')} {pno}",
            )

        y_cursor = draw_page_header(is_first_page=True)

        for idx, item in enumerate(items, start=1):
            # Se il raggruppamento per collana è attivo, disegna una sezione elegante
            if group_by_collection and item.k_collection_base and item.k_collection_base != current_coll_base:
                current_coll_base = item.k_collection_base
                if y_cursor + grp_h + row_h > usable_bottom:
                    draw_page_footer(page_num)
                    writer.newPage()
                    page_num += 1
                    y_cursor = draw_page_header(is_first_page=False)

                painter.fillRect(QRectF(0, y_cursor, w, grp_h), c_grp_bg)
                painter.setPen(QPen(c_grp_border, 0.8))
                painter.drawLine(QPointF(0, y_cursor), QPointF(w, y_cursor))
                painter.drawLine(QPointF(0, y_cursor + grp_h), QPointF(w, y_cursor + grp_h))

                painter.setFont(f_group)
                painter.setPen(c_ink)
                disp_coll = item.collection_base_display or item.collection
                c_count = coll_counts.get(current_coll_base, 0)
                if category == "Libri":
                    c_unit = ("volume" if c_count == 1 else "volumes") if lang == "en" else ("volume" if c_count == 1 else "volumi")
                else:
                    c_unit = ("album" if c_count == 1 else "albums") if lang == "en" else "album"
                count_str = f"({c_count} {c_unit})" if c_count > 0 else ""
                sec_title = f"◆  {col_coll_lbl.upper()}: {disp_coll.upper()}   {count_str}".strip()
                elided_sec = QFontMetricsF(f_group).elidedText(
                    sec_title, Qt.TextElideMode.ElideRight, w - 2 * pad
                )
                painter.drawText(
                    QRectF(pad, y_cursor, w - 2 * pad, grp_h),
                    int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                    elided_sec,
                )
                y_cursor += grp_h

            if y_cursor + row_h > usable_bottom:
                draw_page_footer(page_num)
                writer.newPage()
                page_num += 1
                y_cursor = draw_page_header(is_first_page=False)

            # NESSUN COLORE ALTERNATO: fondo bianco puro uniforme
            if has_any_collection:
                cells = [
                    str(idx),
                    item.title or "—",
                    item.author or "—",
                    item.publisher or "—",
                    item.collection or "",  # Vuoto se assente, niente trattini ripetitivi
                    item.year or "",
                ]
            else:
                cells = [
                    str(idx),
                    item.title or "—",
                    item.author or "—",
                    item.publisher or "—",
                    item.year or "",
                ]

            for c_idx, val in enumerate(cells):
                if c_idx == 0:
                    painter.setFont(f_num)
                    painter.setPen(c_muted)
                    fm = fm_num
                elif c_idx == 1:
                    painter.setFont(f_title_cell)
                    painter.setPen(c_ink)
                    fm = fm_title_cell
                elif c_idx == 2:
                    painter.setFont(f_author_cell)
                    painter.setPen(c_author)
                    fm = fm_author_cell
                elif c_idx == 3:
                    painter.setFont(f_pub_cell)
                    painter.setPen(c_pub)
                    fm = fm_pub_cell
                elif has_any_collection and c_idx == 4:
                    painter.setFont(f_coll_cell)
                    painter.setPen(c_coll)
                    fm = fm_coll_cell
                else:
                    painter.setFont(f_year_cell)
                    painter.setPen(c_muted)
                    fm = fm_year_cell

                avail_w = col_widths[c_idx] - 2 * pad
                elided = fm.elidedText(val, Qt.TextElideMode.ElideRight, avail_w)
                align = Qt.AlignmentFlag.AlignRight if c_idx in (0, last_col_idx) else Qt.AlignmentFlag.AlignLeft
                painter.drawText(
                    QRectF(col_x[c_idx] + pad, y_cursor, avail_w, row_h),
                    int(align | Qt.AlignmentFlag.AlignVCenter),
                    elided,
                )

            # Filetto divisorio orizzontale ultra-delicato (0.6 pt)
            painter.setPen(QPen(c_hairline, 0.6))
            painter.drawLine(
                QPointF(0, y_cursor + row_h),
                QPointF(w, y_cursor + row_h),
            )
            y_cursor += row_h

        draw_page_footer(page_num)
    finally:
        painter.end()

    return len(items)
