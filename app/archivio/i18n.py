"""Testi dell'interfaccia in italiano e inglese."""
from __future__ import annotations

import sys

from . import APP_VERSION

_LANG = "it"
_MOD = "⌘" if sys.platform == "darwin" else "Ctrl+"

TEXTS = {
    "it": {
        "app_title": "Archivio",
        "cat_Libri": "Libri",
        "cat_CD": "CD",
        "cat_Vinili": "Vinili",
        "sidebar_folder": "Cartella archivio",
        "sidebar_files": "Foglio attivo",
        "btn_change_folder": "Cambia cartella…",
        "btn_new_sheet": "Nuovo foglio vuoto",
        "btn_settings": "Impostazioni…",
        "btn_save": "Salva",
        "btn_save_as": "Salva con nome…",
        "btn_save_all": "Salva tutti",
        "btn_export_pdf": "Esporta PDF",
        "btn_mac_cam": "Fotocamera del computer",
        "btn_remote_scan": "Scansiona da dispositivo mobile",
        "entry_section_title": "Nuovo inserimento",
        "btn_add": "Aggiungi",
        "btn_edit": "Modifica…",
        "btn_duplicate": "Duplica",
        "btn_delete": "Elimina",
        "btn_clear": "Svuota elenco…",
        "btn_undo": "Annulla",
        "btn_redo": "Ripristina",
        "btn_cancel": "Annulla",
        "btn_apply": "Applica",
        "col_title": "Titolo",
        "col_author_book": "Autore",
        "col_author_music": "Artista / Gruppo",
        "col_sort": "Ordina come",
        "col_pub_book": "Editore",
        "col_pub_music": "Etichetta",
        "col_coll_book": "Collana",
        "col_coll_music": "Collezione",
        "col_year": "Anno",
        "ph_title_book": "Digita il titolo del libro o ISBN…",
        "ph_title_music": "Digita il titolo dell'album o codice EAN/UPC…",
        "ph_author_book": "Es. Umberto Eco, Antonio De Sanctis…",
        "ph_author_music": "Es. Pink Floyd, Fabrizio De André, Sting…",
        "ph_sort": "Cognome, Nome (automatico)",
        "ph_pub_book": "Scegli o digita l'editore…",
        "ph_pub_music": "Scegli o digita l'etichetta…",
        "ph_coll_book": "Es. I Meridiani, Classici #1… (opz.)",
        "ph_coll_music": "Es. Box Set, DeAgostini #1… (opz.)",
        "ph_year": "Anno…",
        "chk_group_coll": "Raggruppa Collane / Collezioni a parte",
        "chk_group_coll_tip": (
            "Se attivo (predefinito), i volumi o dischi appartenenti alla stessa Collana/Collezione\n"
            "vengono tenuti vicini e ordinati a parte (per collana e numero/autore).\n"
            "Se disattivato, tutti i volumi seguono il normale ordine alfabetico generale."
        ),
        "ctx_assign_coll": "Assegna a Collana / Collezione…",
        "ctx_clear_coll": "Rimuovi dalla Collana / Collezione",
        "dlg_assign_coll_title": "Assegna a Collana / Collezione",
        "dlg_assign_coll_prompt": "Scegli o digita il nome della Collana / Collezione per gli elementi selezionati:",
        "search_placeholder": f"Filtra nell'archivio ({_MOD}F)…",
        "status_ready": "Pronto",
        "status_online": "Catalogo online attivo\nSBN · Google · iTunes · MB",
        "status_offline": "Modalità locale offline\nSeed Index · RapidFuzz",
        "editions_loading": "Ricerca edizioni per questo supporto in corso…",
        "sort_override_hint": "Chiave usata per ordinare la colonna Autore per cognome. Puoi modificarla liberamente.",
        "dlg_settings_title": "Impostazioni",
        "dlg_settings_lang": "Lingua dell'interfaccia",
        "dlg_settings_online": "Ricerca e autocompletamento multi-motore online in tempo reale",
        "dlg_settings_discogs": "Token Discogs (opzionale)",
        "dlg_settings_discogs_ph": "Incolla un Personal Access Token gratuito Discogs (opzionale)…",
        "dlg_settings_cache": "Cache catalogo locale",
        "dlg_settings_cfg": "File di configurazione",
        "dlg_settings_commercial": (
            f"Archivio (v{APP_VERSION}) — Licenza GNU GPLv3 (Copyright © 2026 Antonio Cafaro).\n"
            "Motori attivi in parallelo:\n"
            "• Libri: Google Books API + OPAC SBN + IBS / LaFeltrinelli + Hoepli + Amazon.it + Open Library\n"
            "• CD & Vinili: Apple iTunes Search API + MusicBrainz / ListenBrainz + Discogs + IBS / Amazon (EAN/UPC)\n"
            "• Motore locale: Seed Index in RAM + SQLite WAL + RapidFuzz C++ (tollerante agli errori)"
        ),
        "dlg_edit_title": "Modifica elemento",
        "dlg_save_as_title": "Salva foglio con nome",
        "dlg_save_as_prompt": "Inserisci il nome per il nuovo foglio della categoria {cat}:",
        "dlg_save_as_preview": "Verrà salvato come: {fn}",
        "msg_missing_fields": "Inserisci almeno il Titolo e l'Autore/Artista.",
        "msg_select_to_edit": "Seleziona una riga nella tabella per modificarla.",
        "msg_select_to_delete": "Seleziona almeno una riga da eliminare.",
        "msg_confirm_delete": f"Eliminare {{n}} elementi selezionati? (Puoi annullare con {_MOD}Z)",
        "msg_confirm_clear": f"Svuotare l'intero elenco corrente di {{cat}}? (Puoi annullare con {_MOD}Z)",
        "msg_saved_one": "Archivio salvato in: {fn}",
        "msg_saved_all": "Tutti e 3 gli archivi sono stati salvati:\n{files}",
        "msg_pdf_exported": "Esportazione PDF completata:\n{files}",
        "msg_pdf_empty": "Nessun elemento da esportare in questa categoria.",
        "msg_unsaved_quit": "Ci sono modifiche non salvate. Vuoi salvarle prima di procedere?",
        "msg_duplicate_warn": "Questo elemento con la stessa edizione è già presente. Aggiungerlo comunque?",
        "tip_last_inserted": "Ultimo inserimento",
        "status_added_pos": "✓ Aggiunto (posizione {pos} di {total}): {title} — {author}",
        "status_scanned_pos": "✓ Aggiunto [{category}] (posizione {pos} di {total}): {title} — {author}",
        "status_duplicated_pos": "✓ Duplicato (posizione {pos} di {total}): {title} — {author}",
        "pdf_generated_on": "Generato il",
        "pdf_page": "Pagina",
        "menu_file": "File",
        "menu_edit": "Modifica",
        "menu_view": "Vista",
    },
    "en": {
        "app_title": "Archivio",
        "cat_Libri": "Books",
        "cat_CD": "CDs",
        "cat_Vinili": "Vinyls",
        "sidebar_folder": "Archive Folder",
        "sidebar_files": "Active Sheet",
        "btn_change_folder": "Change Folder…",
        "btn_new_sheet": "New Blank Sheet",
        "btn_settings": "Settings…",
        "btn_save": "Save",
        "btn_save_as": "Save As…",
        "btn_save_all": "Save All",
        "btn_export_pdf": "Export PDF",
        "btn_mac_cam": "Computer Camera",
        "btn_remote_scan": "Scan from Mobile Device",
        "entry_section_title": "New Entry",
        "btn_add": "Add",
        "btn_edit": "Edit…",
        "btn_duplicate": "Duplicate",
        "btn_delete": "Delete",
        "btn_clear": "Clear List…",
        "btn_undo": "Undo",
        "btn_redo": "Redo",
        "btn_cancel": "Cancel",
        "btn_apply": "Apply",
        "col_title": "Title",
        "col_author_book": "Author",
        "col_author_music": "Artist / Band",
        "col_sort": "Sort As",
        "col_pub_book": "Publisher",
        "col_pub_music": "Record Label",
        "col_coll_book": "Series / Collection",
        "col_coll_music": "Collection / Box Set",
        "col_year": "Year",
        "ph_title_book": "Type book title or ISBN…",
        "ph_title_music": "Type album title or EAN/UPC barcode…",
        "ph_author_book": "e.g. Umberto Eco, George Orwell…",
        "ph_author_music": "e.g. Pink Floyd, The Beatles, Sting…",
        "ph_sort": "Surname, Name (auto)",
        "ph_pub_book": "Choose or type publisher…",
        "ph_pub_music": "Choose or type record label…",
        "ph_coll_book": "e.g. Penguin Classics #1… (opt.)",
        "ph_coll_music": "e.g. Box Set, Collection #1… (opt.)",
        "ph_year": "Year…",
        "chk_group_coll": "Group Series / Collections separately",
        "chk_group_coll_tip": (
            "When checked (default), items belonging to the same Series/Collection are kept\n"
            "together and sorted separately by collection name and volume/author.\n"
            "When unchecked, all items follow the standard alphabetical order."
        ),
        "ctx_assign_coll": "Assign to Series / Collection…",
        "ctx_clear_coll": "Remove from Series / Collection",
        "dlg_assign_coll_title": "Assign to Series / Collection",
        "dlg_assign_coll_prompt": "Choose or type the Series / Collection name for the selected items:",
        "search_placeholder": f"Filter archive ({_MOD}F)…",
        "status_ready": "Ready",
        "status_online": "Online catalog active\nGoogle · SBN · iTunes · MB",
        "status_offline": "Offline local mode\nSeed Index · RapidFuzz",
        "editions_loading": "Fetching format-specific editions…",
        "sort_override_hint": "Key used to sort the Author column by surname. You can edit it freely.",
        "dlg_settings_title": "Settings",
        "dlg_settings_lang": "Interface language",
        "dlg_settings_online": "Real-time multi-engine online suggestions and edition lookup",
        "dlg_settings_discogs": "Discogs Token (optional)",
        "dlg_settings_discogs_ph": "Paste a free Discogs Personal Access Token (optional)…",
        "dlg_settings_cache": "Local catalog cache",
        "dlg_settings_cfg": "Configuration file",
        "dlg_settings_commercial": (
            f"Archivio (v{APP_VERSION}) — GNU GPLv3 License (Copyright © 2026 Antonio Cafaro).\n"
            "Parallel engines:\n"
            "• Books: Google Books API + OPAC SBN + IBS / LaFeltrinelli + Hoepli + Amazon.it + Open Library\n"
            "• CDs & Vinyls: Apple iTunes Search API + MusicBrainz / ListenBrainz + Discogs + IBS / Amazon (EAN/UPC)\n"
            "• Local engine: RAM Seed Index + SQLite WAL + RapidFuzz C++ (typo-tolerant)"
        ),
        "dlg_edit_title": "Edit Item",
        "dlg_save_as_title": "Save Sheet As",
        "dlg_save_as_prompt": "Enter a name for the new {cat} sheet:",
        "dlg_save_as_preview": "Will be saved as: {fn}",
        "msg_missing_fields": "Please enter at least Title and Author/Artist.",
        "msg_select_to_edit": "Select a row in the table to edit.",
        "msg_select_to_delete": "Select at least one row to delete.",
        "msg_confirm_delete": f"Delete {{n}} selected items? (You can undo with {_MOD}Z)",
        "msg_confirm_clear": f"Clear the entire current {{cat}} list? (You can undo with {_MOD}Z)",
        "msg_saved_one": "Saved to: {fn}",
        "msg_saved_all": "All 3 archives saved:\n{files}",
        "msg_pdf_exported": "PDF export completed:\n{files}",
        "msg_pdf_empty": "No items to export in this category.",
        "msg_unsaved_quit": "There are unsaved changes. Save before continuing?",
        "msg_duplicate_warn": "This item with the same edition already exists. Add anyway?",
        "tip_last_inserted": "Last added item",
        "status_added_pos": "✓ Added (position {pos} of {total}): {title} — {author}",
        "status_scanned_pos": "✓ Added [{category}] (position {pos} of {total}): {title} — {author}",
        "status_duplicated_pos": "✓ Duplicated (position {pos} of {total}): {title} — {author}",
        "pdf_generated_on": "Generated on",
        "pdf_page": "Page",
        "menu_file": "File",
        "menu_edit": "Edit",
        "menu_view": "View",
    },
}


def set_language(lang: str) -> None:
    global _LANG
    _LANG = lang if lang in TEXTS else "it"


def language() -> str:
    return _LANG


def tr(lang_or_key: str, key: str | None = None, **kwargs) -> str:
    if key is None:
        lang = _LANG
        real_key = lang_or_key
    else:
        lang = lang_or_key if lang_or_key in TEXTS else _LANG
        real_key = key
    text = TEXTS.get(lang, TEXTS["it"]).get(real_key) or TEXTS["it"].get(real_key, real_key)
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            pass
    return text
