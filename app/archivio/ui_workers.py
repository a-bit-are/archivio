"""Worker asincroni QThreadPool e helper di fusione/filtro suggerimenti."""
from __future__ import annotations

from dataclasses import replace
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, QRunnable, Signal

from .catalog import CatalogService, EditionBundle, Suggestion, _publishers_match
from .store import Item
from .textutil import fold
from .ui_theme import _qobj_valid


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


def _prioritize_choices(all_choices: List[str], preferred: List[str]) -> List[str]:
    """Ordina prima le scelte compatibili (senza perdere né duplicare le altre voci)."""
    out: List[str] = []
    seen: set[str] = set()
    for src in (preferred, all_choices):
        for item in src:
            s = str(item or "").strip()
            if s and fold(s) not in seen:
                seen.add(fold(s))
                out.append(s)
    return out


def _filter_colls_for_publisher(
    colls: List[str],
    pub_name: str,
    bundle: Optional[EditionBundle],
) -> List[str]:
    """Esclude le collane di una bundle che appartengono esplicitamente a un altro editore/etichetta."""
    if not colls:
        return []
    pub_clean = (pub_name or "").strip()
    if not pub_clean or not bundle or not bundle.collection_meta:
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
            mp = (meta.get("publisher") or "").strip()
            if mp and not _publishers_match(mp, pub_clean):
                continue
        out.append(cs)
    return out
