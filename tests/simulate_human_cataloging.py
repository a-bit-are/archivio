#!/usr/bin/env python3
"""
Archivio
Copyright (C) 2026 Antonio Cafaro — Licenza GNU GPLv3

Simulazione realistica end-to-end di un utente umano che cataloga una pila eterogenea
di 60 elementi (30 Libri, 15 CD, 15 Vinili) in ordine sparso, con:
- Errori di battitura reali (fuzzy matching C++)
- Inserimenti tramite codice ISBN con rilevamento automatico della Collana (OPAC SBN / Google Books / Open Library)
- Inserimenti partendo dall'Artista/Autore e successiva modifica del solo Titolo
- Cambi continui di categoria (Libri <-> CD <-> Vinili)
- Volumi appartenenti a Collane / Collezioni (numeri arabi #1, #12, #32, numeri romani Vol. I, II, IV e non numerati)
- Verifica dell'ordinamento con spunta "Raggruppa Collane a parte" ATTIVA vs DISATTIVA
- Assegnazione multipla di Collana, Undo/Redo, Ricerca istantanea, Salvataggio 3 CSV ed Esportazione 3 PDF
- Pulizia totale di ogni file temporaneo a fine test.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from typing import List, Tuple

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false")

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_DIR = os.path.join(ROOT_DIR, "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import QApplication

from archivio.config import Settings
from archivio.pdf_export import export_category_pdf
from archivio.store import Item, load_csv, sort_items
from archivio.textutil import fold
from archivio.ui import ITEM_ROLE, MainWindow


def _table_items(win: MainWindow) -> List[Item]:
    """Restituisce la lista ordinata di Item così come visualizzata nella tabella corrente."""
    out: List[Item] = []
    for row in range(win.proxy_model.rowCount()):
        idx = win.proxy_model.index(row, 0)
        it = win.proxy_model.data(idx, ITEM_ROLE)
        if it is not None:
            out.append(it)
    return out


def _human_add_manual(
    win: MainWindow,
    category: str,
    title: str,
    author: str,
    publisher: str = "",
    collection: str = "",
    year: str = "",
    custom_sort: str = "",
) -> None:
    """Simula un utente che compila i campi a mano (o dopo aver scelto dai menu a tendina) e clicca 'Aggiungi'."""
    win._switch_category(category)
    win.reset_entry_form()

    # Simula digitazione umana carattere per carattere o incolla nel campo Titolo e Autore
    win.in_title.setText(title)
    win._on_query_edited("title", title)

    win.in_author.setText(author)
    win._on_author_field_edited(author)

    if custom_sort:
        win.in_sort.setText(custom_sort)
        win._on_sort_field_edited(custom_sort)

    if publisher:
        win.in_pub.setEditText(publisher)
        win._on_pub_choice_changed(0)

    if collection:
        win.in_coll.setEditText(collection)
        win._on_coll_edited(collection)

    if year:
        win.in_year.setEditText(year)
        win._on_year_edited(year)

    win.add_item_from_form()


def run_simulation() -> None:
    app = QApplication.instance() or QApplication(sys.argv)

    # Salva le impostazioni originali dell'utente per ripristinarle intatte a fine test
    had_config_before = os.path.isfile(Settings().path)
    orig_settings = Settings.load()

    temp_dir = tempfile.mkdtemp(prefix=".archivio_human_sim_")

    print(f"================================================================================")
    print(f"AVVIO SIMULAZIONE UTENTE REALE SU: {temp_dir}")
    print(f"================================================================================")

    try:
        sim_settings = Settings(
            archive_dir=temp_dir,
            language="it",
            online=True,
            group_by_collection=False,
            discogs_token=orig_settings.discogs_token,
        )
        win = MainWindow(sim_settings)

        # ----------------------------------------------------------------------
        # FASE 1: ERRORI DI BATTITURA REALI (FUZZY MATCHING C++ + ONLINE)
        # ----------------------------------------------------------------------
        print("\n[FASE 1] Simulazione errori di battitura umani (typos) su Libri, CD e Vinili...")
        typo_cases: List[Tuple[str, str, str, str, str]] = [
            # (Categoria, campo, query_con_errore, autore_atteso_substr, titolo_atteso_substr)
            ("Libri", "author", "umberto ecco", "eco", ""),
            ("Libri", "title", "il nome dela rosa", "eco", "nome della rosa"),
            ("Libri", "author", "italo calvno", "calvino", ""),
            ("Libri", "author", "gabriell garcia marquez", "marquez", ""),
            ("CD", "author", "punk floyd", "pink floyd", ""),
            ("Vinili", "author", "led zepelin", "led zeppelin", ""),
            ("CD", "author", "miles davies", "miles davis", ""),
            ("Vinili", "author", "fabrizo de andre", "de andre", ""),
        ]
        for cat, fmode, bad_q, exp_auth, exp_title in typo_cases:
            win._switch_category(cat)
            hits = win.catalog.search_local(cat, bad_q, [], lang="it", field_mode=fmode, limit=15)
            if not hits:
                hits = win.catalog.search_online(cat, bad_q, lang="it", field_mode=fmode)
            assert hits, f"Nessun suggerimento trovato per typo umano '{bad_q}' in {cat}"
            matched = any(
                exp_auth in fold(h.author) and (not exp_title or exp_title in fold(h.title))
                for h in hits[:8]
            )
            assert matched, f"Typo '{bad_q}' non ha restituito '{exp_auth}' tra i primi risultati: {[h.author for h in hits[:5]]}"
            print(f"  ✓ Typo '{bad_q}' ({cat}) -> Riconosciuto correttamente: {hits[0].title} — {hits[0].author}")

        # ----------------------------------------------------------------------
        # FASE 2: INSERIMENTO TRAMITE ISBN CON RILEVAMENTO AUTOMATICO COLLANA
        # ----------------------------------------------------------------------
        print("\n[FASE 2] Simulazione inserimento libri tramite codice ISBN...")
        isbn_cases = [
            ("9788804668237", "1984", "Orwell, George", "Mondadori", ("Oscar moderni",)),
            ("9788845901669", "cloche", "Wilson, Angus", "Adelphi", ("Piccola biblioteca Adelphi",)),
            ("9788806220457", "Delitto e castigo", "Dostoevskij", "Einaudi", ("ET", "Einaudi tascabili")),
        ]
        win._switch_category("Libri")
        for isbn, exp_title_sub, exp_sort, exp_pub, exp_coll_subs in isbn_cases:
            win.reset_entry_form()
            sugs = win.catalog.search_online("Libri", isbn, lang="it", field_mode="title")
            assert sugs, f"ISBN {isbn} non trovato!"
            chosen = sugs[0]
            assert chosen.from_isbn, f"Il suggerimento da ISBN {isbn} deve avere from_isbn=True"
            win._apply_suggestion(chosen)

            auto_coll = win.in_coll.currentText().strip()
            print(
                f"  ✓ ISBN {isbn} -> '{win.in_title.text()}' | Autore: {win.in_author.text()} "
                f"| Editore: {win.in_pub.currentText()} | Collana auto-compilata: '{auto_coll}'"
            )
            assert exp_title_sub.lower() in win.in_title.text().lower()
            assert any(sub.lower() in auto_coll.lower() for sub in exp_coll_subs), (
                f"Collana attesa {exp_coll_subs}, trovata '{auto_coll}'"
            )
            win.add_item_from_form()

        # ----------------------------------------------------------------------
        # FASE 3: FLUSSO UMANO "PRIMA ARTISTA/AUTORE, POI CAMBIO SOLO IL TITOLO"
        # ----------------------------------------------------------------------
        print("\n[FASE 3] Simulazione flusso 'Scelgo prima la Band/Autore, poi modifico solo il Titolo'...")
        win._switch_category("CD")
        win.reset_entry_form()
        # L'utente scrive "punk floyd" in Artista, seleziona un album qualsiasi (es. The Wall)
        pf_hits = win.catalog.search_local("CD", "punk floyd", [], lang="it", field_mode="author")
        win._apply_suggestion(pf_hits[0])
        assert win.in_author.text() == "Pink Floyd"

        # Ora l'utente vuole inserire "The Final Cut": modifica solo il campo Titolo scrivendo "the"
        win.in_title.setFocus()
        win.in_title.setText("the")
        win._on_query_edited("title", "the")
        ctx_hits = win.catalog.search_local(
            "CD", "the", [], lang="it", field_mode="title", context_author=win.in_author.text()
        )
        online_ctx_hits = win.catalog.search_online(
            "CD", "the", lang="it", field_mode="title", context_author=win.in_author.text()
        )
        all_pf_titles = [h.title for h in ctx_hits + online_ctx_hits]
        assert all(h.author.lower() == "pink floyd" for h in ctx_hits), "Tutti i suggerimenti devono essere dei Pink Floyd!"
        assert any("final cut" in t.lower() for t in all_pf_titles), f"'The Final Cut' deve apparire! Trovati: {all_pf_titles}"
        final_cut_sug = next(h for h in (ctx_hits + online_ctx_hits) if "final cut" in h.title.lower())
        win._apply_suggestion(final_cut_sug)
        ed_fc = win.catalog.fetch_editions("CD", final_cut_sug.work_id, final_cut_sug.title, final_cut_sug.author, lang="it")
        win._on_editions_fetched(win._edition_req_id, ed_fc)
        print(
            f"  ✓ Da 'Pink Floyd' + 'the' selezionato: '{win.in_title.text()}' "
            f"| Etichetta CD: {win.in_pub.currentText()} | Anno: {win.in_year.currentText()}"
        )
        win.add_item_from_form()

        # Caso Sting "Ten Summoner's Tales" in CD (verifica etichetta e anni CD > 0)
        win.reset_entry_form()
        sting_sugs = win.catalog.search_local("CD", "Ten Summoner's Tales", [], lang="it", field_mode="title")
        if not sting_sugs:
            sting_sugs = win.catalog.search_online("CD", "Ten Summoner's Tales", lang="it", field_mode="title")
        win._apply_suggestion(sting_sugs[0])
        ed_sting = win.catalog.fetch_editions("CD", sting_sugs[0].work_id, "Ten Summoner’s Tales", "Sting", lang="it")
        win._on_editions_fetched(win._edition_req_id, ed_sting)
        assert win.in_pub.count() >= 1 and win.in_pub.currentText().strip() != "", "Etichetta CD di Sting non può essere vuota!"
        assert win.in_year.count() >= 1, "Anni di stampa CD di Sting non possono essere 0!"
        print(
            f"  ✓ Verifica CD 'Ten Summoner’s Tales' — Sting -> {win.in_pub.count()} etichette CD "
            f"(attiva: '{win.in_pub.currentText()}'), {win.in_year.count()} anni CD (attivo: '{win.in_year.currentText()}')"
        )
        win.add_item_from_form()

        # ----------------------------------------------------------------------
        # FASE 4: INSERIMENTO MASSIVO IN ORDINE SPARSO DI LISTA ETEROGENEA
        #         (LIBRI, CD, VINILI + COLLANE CON NUMERI ARABI, ROMANI E SENZA NUMERO)
        # ----------------------------------------------------------------------
        print("\n[FASE 4] Inserimento incrociato di 55 ulteriori Libri, CD e Vinili in ordine sparso...")

        heterogeneous_batch = [
            # --- Collana 1 (Libri): "Classici UTET" (Autori Dickens, Manzoni, Shakespeare, Cervantes) ---
            ("Libri", "Amleto", "William Shakespeare", "UTET", "Classici UTET", "2014", ""),
            ("Vinili", "Kind of Blue", "Miles Davis", "Columbia", "Jazz in Vinile DeAgostini #1", "1959", ""),
            ("Libri", "David Copperfield", "Charles Dickens", "UTET", "Classici UTET", "2011", ""),
            ("CD", "OK Computer", "Radiohead", "Parlophone", "", "1997", ""),
            ("Libri", "I promessi sposi", "Alessandro Manzoni", "UTET", "Classici UTET", "2013", ""),
            ("Vinili", "Time Out", "The Dave Brubeck Quartet", "Columbia", "Jazz in Vinile DeAgostini #5", "1959", ""),
            ("Libri", "Don Chisciotte della Mancia", "Miguel de Cervantes", "UTET", "Classici UTET", "2015", ""),

            # --- Collana 2 (Libri): "Piccola Biblioteca Adelphi" (Numerata: #1, #12 già da ISBN, #32, #88) ---
            ("Libri", "Siddhartha", "Hermann Hesse", "Adelphi", "Piccola Biblioteca Adelphi #32", "1975", ""),
            ("CD", "Random Access Memories", "Daft Punk", "Columbia", "", "2013", ""),
            ("Libri", "Lo strano caso del dottor Jekyll e del signor Hyde", "Robert Louis Stevenson", "Adelphi", "Piccola Biblioteca Adelphi #1", "1972", ""),
            ("Vinili", "A Love Supreme", "John Coltrane", "Impulse!", "Jazz in Vinile DeAgostini #2", "1965", ""),
            ("Libri", "L'insostenibile leggerezza dell'essere", "Milan Kundera", "Adelphi", "Piccola Biblioteca Adelphi #88", "1985", ""),

            # --- Collana 3 (Libri): "I Meridiani" con Numeri Romani inseriti fuori ordine (Vol. IV, Vol. I, Vol. II) ---
            ("Libri", "Romanzi e racconti", "Italo Calvino", "Mondadori", "I Meridiani Vol. IV", "1994", ""),
            ("CD", "The Dark Side of the Moon", "Pink Floyd", "EMI", "Discovery Edition", "2011", ""),
            ("Libri", "Romanzi e racconti", "Italo Calvino", "Mondadori", "I Meridiani Vol. I", "1991", ""),
            ("Vinili", "The Dark Side of the Moon", "Pink Floyd", "Harvest", "", "1973", ""),
            ("Libri", "Romanzi e racconti", "Italo Calvino", "Mondadori", "I Meridiani Vol. II", "1992", ""),
            ("Libri", "Opere complete", "Luigi Pirandello", "Mondadori", "I Meridiani Vol. III", "1993", ""),

            # --- Libri sciolti con cognomi complessi, particelle nobiliari, inversioni "Cognome Nome" ---
            ("Libri", "Storia della letteratura italiana", "De Sanctis Francesco", "Einaudi", "", "1958", ""),
            ("Libri", "Il piacere", "D'Annunzio Gabriele", "Mondadori", "", "1989", ""),
            ("Libri", "Cent'anni di solitudine", "Gabriel García Márquez", "Mondadori", "", "1968", ""),
            ("Libri", "Il nome della rosa", "Umberto Eco", "Bompiani", "", "1980", ""),
            ("Libri", "Il pendolo di Foucault", "Umberto Eco", "Bompiani", "", "1988", ""),
            ("Libri", "La coscienza di Zeno", "Italo Svevo", "Garzanti", "", "1923", ""),
            ("Libri", "Il Gattopardo", "Giuseppe Tomasi di Lampedusa", "Feltrinelli", "", "1958", ""),
            ("Libri", "Se questo è un uomo", "Primo Levi", "Einaudi", "", "1947", ""),
            ("Libri", "La tregua", "Primo Levi", "Einaudi", "", "1963", ""),
            ("Libri", "Il sistema periodico", "Primo Levi", "Einaudi", "", "1975", ""),
            ("Libri", "Lessico famigliare", "Natalia Ginzburg", "Einaudi", "", "1963", ""),
            ("Libri", "Il deserto dei Tartari", "Dino Buzzati", "Mondadori", "", "1940", ""),
            ("Libri", "La storia", "Elsa Morante", "Einaudi", "", "1974", ""),
            ("Libri", "Cristo si è fermato a Eboli", "Carlo Levi", "Einaudi", "", "1945", ""),
            ("Libri", "Il giardino dei Finzi-Contini", "Giorgio Bassani", "Einaudi", "", "1962", ""),
            ("Libri", "I fratelli Karamazov", "Fëdor Dostoevskij", "Einaudi", "", "1990", ""),
            ("Libri", "Guerra e pace", "Lev Tolstoj", "Einaudi", "", "1995", ""),
            ("Libri", "Il processo", "Franz Kafka", "Adelphi", "", "1983", ""),
            ("Libri", "La montagna incantata", "Thomas Mann", "Mondadori", "", "1965", ""),

            # --- CD vari (Rock, Pop, Cantautori italiani, Classica, Box Set) ---
            ("CD", "A Night at the Opera", "Queen", "EMI", "", "1986", ""),
            ("CD", "Innuendo", "Queen", "Parlophone", "", "1991", ""),
            ("CD", "Brothers in Arms", "Dire Straits", "Vertigo", "", "1985", ""),
            ("CD", "Making Movies", "Dire Straits", "Vertigo", "", "1988", ""),
            ("CD", "Nevermind", "Nirvana", "DGC", "", "1991", ""),
            ("CD", "In Utero", "Nirvana", "DGC", "", "1993", ""),
            ("CD", "The Joshua Tree", "U2", "Island", "", "1987", ""),
            ("CD", "Achtung Baby", "U2", "Island", "", "1991", ""),
            ("CD", "La voce del padrone", "Battiato Franco", "EMI", "", "1985", ""),
            ("CD", "Anime salve", "Fabrizio De André", "BMG Ricordi", "", "1996", ""),
            ("CD", "Wish You Were Here", "Pink Floyd", "EMI", "Discovery Edition", "2011", ""),
            ("CD", "The Wall", "Pink Floyd", "EMI", "Discovery Edition", "2011", ""),
            ("CD", "Abbey Road", "The Beatles", "Apple", "The Beatles in Stereo", "2009", ""),
            ("CD", "Revolver", "The Beatles", "Apple", "The Beatles in Stereo", "2009", ""),

            # --- Vinili vari (LP 33 giri storici e ristampe da collezione) ---
            ("Vinili", "Led Zeppelin IV", "Led Zeppelin", "Atlantic", "", "1971", ""),
            ("Vinili", "Physical Graffiti", "Led Zeppelin", "Swan Song", "", "1975", ""),
            ("Vinili", "Abbey Road", "The Beatles", "Apple", "", "1969", ""),
            ("Vinili", "Sgt. Pepper's Lonely Hearts Club Band", "The Beatles", "Parlophone", "", "1967", ""),
            ("Vinili", "Sticky Fingers", "The Rolling Stones", "Rolling Stones Records", "", "1971", ""),
            ("Vinili", "Exile on Main St.", "The Rolling Stones", "Rolling Stones Records", "", "1972", ""),
            ("Vinili", "Rumours", "Fleetwood Mac", "Warner Bros.", "", "1977", ""),
            ("Vinili", "Hotel California", "Eagles", "Asylum", "", "1976", ""),
            ("Vinili", "Creuza de mä", "Fabrizio De André", "Ricordi", "", "1984", ""),
            ("Vinili", "Non al denaro non all'amore né al cielo", "Fabrizio De André", "Produttori Associati", "", "1971", ""),
            ("Vinili", "Storia di un minuto", "Premiata Forneria Marconi", "Numero Uno", "", "1972", ""),
            ("Vinili", "Darwin!", "Banco del Mutuo Soccorso", "Ricordi", "", "1972", ""),
            ("Vinili", "Blue Train", "John Coltrane", "Blue Note", "Blue Note Tone Poet Series", "2019", ""),
            ("Vinili", "Moanin'", "Art Blakey and the Jazz Messengers", "Blue Note", "Blue Note Tone Poet Series", "2020", ""),
        ]

        for cat, title, author, pub, coll, yr, csort in heterogeneous_batch:
            _human_add_manual(win, cat, title, author, pub, coll, yr, csort)

        n_books = len(win.data_by_cat["Libri"])
        n_cds = len(win.data_by_cat["CD"])
        n_vinyls = len(win.data_by_cat["Vinili"])
        print(f"  ✓ Totale elementi inseriti: {n_books} Libri, {n_cds} CD, {n_vinyls} Vinili (Totale: {n_books + n_cds + n_vinyls})")
        assert n_books == 33 and n_cds == 19 and n_vinyls == 18

        # ----------------------------------------------------------------------
        # FASE 5: VERIFICA ORDINAMENTO COGNOMI SPECIALI ("Ordina come")
        # ----------------------------------------------------------------------
        print("\n[FASE 5] Verifica calcolo automatico 'Ordina come' su cognomi composti, inversioni e band...")
        books_map = {it.title: it.author_sort for it in win.data_by_cat["Libri"]}
        cds_map = {it.title: it.author_sort for it in win.data_by_cat["CD"]}
        vinyls_map = {it.title: it.author_sort for it in win.data_by_cat["Vinili"]}

        assert books_map["Storia della letteratura italiana"] == "De Sanctis, Francesco"
        assert books_map["Il piacere"] == "D'Annunzio, Gabriele"
        assert books_map["Cent'anni di solitudine"] == "García Márquez, Gabriel"
        assert books_map["Il Gattopardo"] == "Tomasi di Lampedusa, Giuseppe"
        assert cds_map["La voce del padrone"] == "Battiato, Franco"
        assert cds_map["Anime salve"] == "De André, Fabrizio"
        assert cds_map["Abbey Road"] == "Beatles, The"
        assert cds_map["OK Computer"] == "Radiohead"
        assert vinyls_map["Sticky Fingers"] == "Rolling Stones, The"
        assert vinyls_map["Storia di un minuto"] == "Premiata Forneria Marconi"
        print("  ✓ Tutte le chiavi 'Ordina come' (De Sanctis, D'Annunzio, García Márquez, De André, Battiato, Beatles, Rolling Stones, PFM) sono perfette!")

        # ----------------------------------------------------------------------
        # FASE 6: VERIFICA ORDINAMENTO COLLANE (SPUNTA ATTIVA vs DISATTIVA)
        # ----------------------------------------------------------------------
        print("\n[FASE 6] Verifica ordinamento Collane con spunta ATTIVA vs DISATTIVA...")
        win._switch_category("Libri")
        assert win.chk_group_coll.isChecked() is False, "La spunta collane deve essere disattivata di default!"

        # 1) Verifica che di default (spunta disattivata) l'intera tabella segua l'ordine alfabetico per autore A-Z
        shown_default = _table_items(win)
        all_sorts_default = [fold(it.author_sort) for it in shown_default]
        assert all_sorts_default == sorted(all_sorts_default), "Di default l'intera tabella segue l'ordine alfabetico per cognome autore A-Z!"
        print("  ✓ Spunta DISATTIVATA di default — intera tabella segue l'ordine alfabetico per autore A-Z")

        # 2) Ora l'utente ATTIVA la spunta "Raggruppa Collane / Collezioni a parte"
        win.chk_group_coll.setChecked(True)
        shown_grouped = _table_items(win)
        # Tutti i 4 libri "Classici UTET" (Cervantes, Dickens, Manzoni, Shakespeare) devono essere ADIACENTI!
        utet_indices = [i for i, it in enumerate(shown_grouped) if it.collection == "Classici UTET"]
        assert len(utet_indices) == 4
        assert utet_indices == list(range(min(utet_indices), max(utet_indices) + 1)), (
            f"I volumi di 'Classici UTET' non sono contigui: indici {utet_indices}"
        )
        utet_authors = [shown_grouped[i].author_sort for i in utet_indices]
        assert [fold(a) for a in utet_authors] == sorted(fold(a) for a in utet_authors), (
            f"All'interno di Classici UTET gli autori devono essere ordinati: {utet_authors}"
        )
        print(f"  ✓ Spunta ATTIVA — 'Classici UTET' raggruppati vicini alle righe {utet_indices}: {utet_authors}")

        # 2) I 4 libri di "Piccola Biblioteca Adelphi" (#1, #12, #32, #88) devono essere ADIACENTI e in ordine numerico (#1 -> #12 -> #32 -> #88)!
        pba_indices = [i for i, it in enumerate(shown_grouped) if "piccola biblioteca adelphi" in it.collection.lower()]
        assert len(pba_indices) == 4
        assert pba_indices == list(range(min(pba_indices), max(pba_indices) + 1)), (
            f"I volumi di 'Piccola Biblioteca Adelphi' non sono contigui: indici {pba_indices}"
        )
        pba_colls = [shown_grouped[i].collection for i in pba_indices]
        numbered_pba = [int(c.split("#")[1]) for c in pba_colls if "#" in c]
        assert numbered_pba == sorted(numbered_pba) and numbered_pba[0] == 1 and numbered_pba[-1] == 88, (
            f"Ordine numerico errato in Piccola Biblioteca Adelphi: {pba_colls}"
        )
        if any("#" not in c for c in pba_colls):
            assert "#" not in pba_colls[-1], f"Il volume privo di numero deve stare in coda alla collana: {pba_colls}"
        print(f"  ✓ Spunta ATTIVA — 'Piccola Biblioteca Adelphi' ordinati per numero d'uscita: {pba_colls}")

        # 3) I 4 libri de "I Meridiani" con numeri romani (Vol. I, Vol. II, Vol. III, Vol. IV) devono essere ADIACENTI e in ordine I -> II -> III -> IV!
        mer_indices = [i for i, it in enumerate(shown_grouped) if "meridiani" in it.collection.lower()]
        assert len(mer_indices) == 4
        assert mer_indices == list(range(min(mer_indices), max(mer_indices) + 1))
        mer_colls = [shown_grouped[i].collection for i in mer_indices]
        assert mer_colls == [
            "I Meridiani Vol. I",
            "I Meridiani Vol. II",
            "I Meridiani Vol. III",
            "I Meridiani Vol. IV",
        ], f"Ordine numeri romani errato ne I Meridiani: {mer_colls}"
        print(f"  ✓ Spunta ATTIVA — 'I Meridiani' ordinati per numero romano: {mer_colls}")

        # 4) Ora l'utente TOGLIE LA SPUNTA "Raggruppa Collane / Collezioni a parte"
        win.chk_group_coll.setChecked(False)
        shown_ungrouped = _table_items(win)
        utet_indices_off = [i for i, it in enumerate(shown_ungrouped) if it.collection == "Classici UTET"]
        # Dickens (D), Manzoni (M) e Shakespeare (S) NON devono più essere adiacenti ma nelle loro posizioni alfabetiche!
        assert utet_indices_off != list(range(min(utet_indices_off), max(utet_indices_off) + 1)), (
            "Con spunta disattivata i volumi di autori diversi non devono restare raggruppati a parte!"
        )
        all_sorts_off = [fold(it.author_sort) for it in shown_ungrouped]
        assert all_sorts_off == sorted(all_sorts_off), "Con spunta disattivata l'intera tabella deve seguire l'ordine alfabetico per cognome!"
        print(
            f"  ✓ Spunta DISATTIVATA — Dickens, Manzoni e Shakespeare tornano nelle rispettive posizioni alfabetiche "
            f"(righe {utet_indices_off})"
        )

        # Lascia la spunta disattivata (comportamento predefinito)
        win.chk_group_coll.setChecked(False)

        # ----------------------------------------------------------------------
        # FASE 7: ASSEGNAZIONE COLLANA IN BLOCCO + UNDO / REDO + FILTRO RICERCA
        # ----------------------------------------------------------------------
        print("\n[FASE 7] Simulazione selezione multipla (Primo Levi) -> Assegna Collana 'Supercoralli' + Undo/Redo...")
        # Individua i 3 libri di Primo Levi e assegna loro la collana "Supercoralli"
        levi_rows = [
            idx for idx, it in enumerate(win.data_by_cat["Libri"]) if it.author == "Primo Levi"
        ]
        assert len(levi_rows) == 3
        cur_books = list(win.data_by_cat["Libri"])
        for r in levi_rows:
            cur_books[r] = cur_books[r].with_changes(collection="Supercoralli")
        win._commit_category_change("Libri", cur_books, "Assegna Collana Supercoralli")

        assert sum(1 for it in win.data_by_cat["Libri"] if it.collection == "Supercoralli") == 3
        # Testa Annulla (⌘Z) e Ripristina (⇧⌘Z)
        win.undo_last_action()
        assert sum(1 for it in win.data_by_cat["Libri"] if it.collection == "Supercoralli") == 0
        win.redo_last_action()
        assert sum(1 for it in win.data_by_cat["Libri"] if it.collection == "Supercoralli") == 3
        print("  ✓ Assegnazione collana 'Supercoralli' a 3 volumi + Annulla (⌘Z) + Ripristina (⇧⌘Z) verificati!")

        # Testa ricerca nella barra filtro per nome collana ("Meridiani")
        win.ed_search.setText("Meridiani")
        assert win.proxy_model.rowCount() == 4
        win.ed_search.clear()
        assert win.proxy_model.rowCount() == 33
        print("  ✓ Ricerca istantanea (⌘F) per nome di Collana ('Meridiani' -> 4 risultati) verificata!")

        # ----------------------------------------------------------------------
        # FASE 8: SALVATAGGIO 3 CSV ED ESPORTAZIONE 3 PDF + VERIFICA INTEGRITÀ
        # ----------------------------------------------------------------------
        print("\n[FASE 8] Salvataggio dei 3 CSV ed esportazione dei 3 PDF...")
        for cat in ("Libri", "CD", "Vinili"):
            win._switch_category(cat)
            win.save_current_category()
            csv_path = os.path.join(temp_dir, win.active_file_by_cat[cat])
            assert os.path.isfile(csv_path), f"CSV non creato: {csv_path}"
            reloaded = load_csv(csv_path, cat)
            assert len(reloaded) == len(win.data_by_cat[cat]), f"Mismatch righe rilette da {csv_path}"

            pdf_path = os.path.join(temp_dir, f"{cat.lower()}_test.pdf")
            sorted_for_pdf = sort_items(reloaded, group_by_collection=True)
            export_category_pdf(pdf_path, cat, sorted_for_pdf, lang="it", group_by_collection=True)
            assert os.path.isfile(pdf_path) and os.path.getsize(pdf_path) > 2000, f"PDF non valido: {pdf_path}"
            print(
                f"  ✓ {cat}: salvato CSV ({len(reloaded)} voci) ed esportato PDF ({os.path.getsize(pdf_path):,} byte)"
            )

        win.close()

    finally:
        # Ripristina impostazioni utente e rimuove completamente la directory temporanea di test
        if had_config_before:
            orig_settings.save()
        elif os.path.isfile(orig_settings.path):
            os.remove(orig_settings.path)
        if os.path.isdir(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        print(f"\n[PULIZIA] Rimossa cartella temporanea {temp_dir} e ripristinate impostazioni utente.")
        assert not os.path.exists(temp_dir), "La cartella temporanea deve essere stata eliminata!"

    print("\n================================================================================")
    print("SIMULAZIONE END-TO-END COMPLETATA CON SUCCESSO AL 100% (ZERO ERRORI)!")
    print("================================================================================")


if __name__ == "__main__":
    run_simulation()
