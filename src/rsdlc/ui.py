"""Qt UI for rs-dlc-manager.

Single entry point: :func:`run`. The rest of the project (parsing, library,
paths) is import-safe without Qt — Qt symbols are only imported in this module.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import qdarktheme
from PySide6.QtCore import (
    QAbstractItemModel, QAbstractTableModel, QModelIndex, QObject,
    QPersistentModelIndex, QPoint, QRunnable, QSettings, QSize,
    QSortFilterProxyModel, Qt, QThreadPool, Signal,
)
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialog, QDockWidget,
    QFileDialog, QFrame, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QPushButton, QStatusBar, QTableView, QToolBar, QVBoxLayout, QWidget,
)

from rsdlc.albumart import build_thumbnail, cache_path_for
from rsdlc.duplicates import duplicate_keys, is_duplicate
from rsdlc.favorites import Favorites
from rsdlc.icons import icon
from rsdlc.library import DlcEntry, Library
from rsdlc.paths import autodetect_rocksmith_root, looks_like_rocksmith_root
from rsdlc.setlists import SetlistError, SetlistStore
from rsdlc.stats import compute_stats, format_duration

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Table model
# ---------------------------------------------------------------------------

_COLUMNS: tuple[str, ...] = (
    "Artiste", "Titre", "Année", "Album", "Arrangements", "Accordage", "Statut", "★",
)
_COL_FAVORITE = 7


_THUMB_SIZE = 40  # px


class DlcTableModel(QAbstractTableModel):
    """Adapts a list of :class:`DlcEntry` for QTableView."""

    def __init__(self) -> None:
        super().__init__()
        self._rows: list[DlcEntry] = []
        self._thumb_cache_dir: Path | None = None
        self._thumb_qicon_cache: dict[Path, QIcon] = {}
        self._favorites: Favorites | None = None
        self._star_icon: QIcon | None = None
        self._star_fill_icon: QIcon | None = None

    def set_favorites(self, favorites: Favorites) -> None:
        self._favorites = favorites
        # Lazy-construct the icons the first time we have a QApplication.
        self._star_icon = icon("star", size=18)
        self._star_fill_icon = icon("star-fill", size=18, color=QColor("#f5c518"))

    def is_favorite(self, p: Path) -> bool:
        return self._favorites is not None and self._favorites.contains(p)

    def toggle_favorite(self, p: Path) -> bool:
        if self._favorites is None:
            return False
        new_state = self._favorites.toggle(p)
        # Repaint every row that shares this path.
        for row, e in enumerate(self._rows):
            if e.path == p:
                idx = self.index(row, _COL_FAVORITE)
                self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole])
        return new_state

    def rename_favorite(self, old: Path, new: Path) -> None:
        if self._favorites is not None:
            self._favorites.rename(old, new)

    # ---- rows ----
    def set_rows(self, entries: list[DlcEntry]) -> None:
        self.beginResetModel()
        self._rows = list(entries)
        self.endResetModel()

    def append_rows(self, entries: list[DlcEntry]) -> None:
        if not entries:
            return
        start = len(self._rows)
        self.beginInsertRows(QModelIndex(), start, start + len(entries) - 1)
        self._rows.extend(entries)
        self.endInsertRows()

    def entry(self, row: int) -> DlcEntry:
        return self._rows[row]

    def all_entries(self) -> list[DlcEntry]:
        return list(self._rows)

    def replace_entry(self, row: int, new_entry: DlcEntry) -> None:
        if 0 <= row < len(self._rows):
            self._rows[row] = new_entry
            top_left = self.index(row, 0)
            bottom_right = self.index(row, self.columnCount() - 1)
            self.dataChanged.emit(top_left, bottom_right)

    def replace_path(self, old_path: Path, new_path: Path, enabled: bool) -> None:
        """Update every row whose path == old_path."""
        old_resolved = old_path.resolve()
        for row, e in enumerate(self._rows):
            if e.path.resolve() == old_resolved:
                self._rows[row] = DlcEntry(
                    path=new_path, enabled=enabled,
                    title=e.title, artist=e.artist, album=e.album,
                    year=e.year, length_seconds=e.length_seconds,
                    arrangement_label=e.arrangement_label,
                    tuning_label=e.tuning_label,
                )
        self.dataChanged.emit(self.index(0, 0), self.index(self.rowCount() - 1, self.columnCount() - 1))

    def remove_paths(self, paths: list[Path]) -> None:
        """Drop every row whose path matches one of the given paths.

        Uses a single model reset — for hundreds of removals at once this is
        far faster than per-row signals.
        """
        targets = {p.resolve() for p in paths}
        if not targets:
            return
        self.beginResetModel()
        self._rows = [e for e in self._rows if e.path.resolve() not in targets]
        self.endResetModel()

    # ---- thumbnails ----

    def set_thumbnail_cache_dir(self, cache_dir: Path) -> None:
        self._thumb_cache_dir = cache_dir
        self._thumb_qicon_cache.clear()

    def notify_thumbnail_ready(self, psarc_path: Path) -> None:
        """Drop the QIcon cache for this path and trigger a repaint of its rows."""
        self._thumb_qicon_cache.pop(psarc_path, None)
        for row, e in enumerate(self._rows):
            if e.path == psarc_path:
                idx = self.index(row, 0)
                self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole])

    def _thumbnail_icon(self, psarc_path: Path) -> QIcon | None:
        if self._thumb_cache_dir is None:
            return None
        cached = self._thumb_qicon_cache.get(psarc_path)
        if cached is not None:
            return cached
        png_path = cache_path_for(psarc_path, self._thumb_cache_dir)
        if not png_path.is_file():
            return None
        pix = QPixmap(str(png_path))
        if pix.isNull():
            return None
        ic = QIcon(pix)
        self._thumb_qicon_cache[psarc_path] = ic
        return ic

    def replace_paths_batch(self, updates: list[tuple[Path, Path, bool]]) -> None:
        """Apply many ``(old_path, new_path, enabled)`` swaps with one dataChanged."""
        if not updates:
            return
        update_map: dict[Path, tuple[Path, bool]] = {
            old.resolve(): (new, en) for old, new, en in updates
        }
        for row, e in enumerate(self._rows):
            target = update_map.get(e.path.resolve())
            if target is None:
                continue
            new_path, enabled = target
            self._rows[row] = DlcEntry(
                path=new_path, enabled=enabled,
                title=e.title, artist=e.artist, album=e.album,
                year=e.year, length_seconds=e.length_seconds,
                arrangement_label=e.arrangement_label,
                tuning_label=e.tuning_label,
            )
        if self._rows:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._rows) - 1, self.columnCount() - 1),
            )

    # ---- QAbstractTableModel API ----
    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(_COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation,
                   role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return _COLUMNS[section]
        return section + 1

    def data(self, index: QModelIndex | QPersistentModelIndex,
             role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        e = self._rows[row]
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0: return e.artist
            if col == 1: return e.title
            if col == 2: return "" if e.year is None else str(e.year)
            if col == 3: return e.album
            if col == 4: return e.arrangement_label
            if col == 5: return e.tuning_label
            if col == 6: return "● actif" if e.enabled else "○ désactivé"
        if role == Qt.ItemDataRole.DecorationRole and col == 0:
            return self._thumbnail_icon(e.path)
        if role == Qt.ItemDataRole.DecorationRole and col == _COL_FAVORITE:
            if self.is_favorite(e.path):
                return self._star_fill_icon
            return self._star_icon
        if role == Qt.ItemDataRole.TextAlignmentRole and col == _COL_FAVORITE:
            return int(Qt.AlignmentFlag.AlignCenter)
        if role == Qt.ItemDataRole.ToolTipRole:
            return str(e.path)
        if role == Qt.ItemDataRole.ForegroundRole and not e.enabled:
            # Dim the entire row so disabled DLC are visually muted.
            return QBrush(QColor(140, 140, 140))
        if role == Qt.ItemDataRole.FontRole and not e.enabled:
            font = QFont()
            font.setItalic(True)
            return font
        return None


# ---------------------------------------------------------------------------
# Filter proxy
# ---------------------------------------------------------------------------

class DlcFilterProxy(QSortFilterProxyModel):
    def __init__(self) -> None:
        super().__init__()
        self._search = ""
        self._status = "all"      # all | active | disabled | duplicates | favorites
        self._tuning = ""         # "" = all, otherwise must match tuning_label
        self._dup_set: set[tuple[str, str]] | None = None

    def setSourceModel(self, source_model: QAbstractItemModel) -> None:
        old = self.sourceModel()
        if isinstance(old, QAbstractItemModel):
            try:
                old.modelReset.disconnect(self._invalidate_dup_cache)
                old.rowsInserted.disconnect(self._invalidate_dup_cache)
                old.rowsRemoved.disconnect(self._invalidate_dup_cache)
            except (RuntimeError, TypeError):
                pass
        super().setSourceModel(source_model)
        self._dup_set = None
        source_model.modelReset.connect(self._invalidate_dup_cache)
        source_model.rowsInserted.connect(self._invalidate_dup_cache)
        source_model.rowsRemoved.connect(self._invalidate_dup_cache)

    def _invalidate_dup_cache(self, *_args: object) -> None:
        self._dup_set = None
        if self._status == "duplicates":
            self.invalidateFilter()

    def _ensure_dup_set(self) -> set[tuple[str, str]]:
        if self._dup_set is None:
            model = self.sourceModel()
            entries = model.all_entries() if isinstance(model, DlcTableModel) else []
            self._dup_set = duplicate_keys(entries)
        return self._dup_set

    def set_search(self, text: str) -> None:
        self._search = text.casefold().strip()
        self.invalidateFilter()

    def set_status(self, status: str) -> None:
        self._status = status
        self.invalidateFilter()

    def set_tuning(self, tuning: str) -> None:
        self._tuning = tuning
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int,
                         source_parent: QModelIndex | QPersistentModelIndex) -> bool:
        model = self.sourceModel()
        if not isinstance(model, DlcTableModel):
            return True
        e = model.entry(source_row)
        if self._status == "active" and not e.enabled:
            return False
        if self._status == "disabled" and e.enabled:
            return False
        if self._status == "duplicates":
            if not is_duplicate(e, self._ensure_dup_set()):
                return False
        if self._status == "favorites":
            model = self.sourceModel()
            if not isinstance(model, DlcTableModel) or not model.is_favorite(e.path):
                return False
        if self._tuning and e.tuning_label != self._tuning:
            return False
        if self._search:
            hay = f"{e.artist} {e.title} {e.album}".casefold()
            if self._search not in hay:
                return False
        return True


# ---------------------------------------------------------------------------
# Scan worker
# ---------------------------------------------------------------------------

class _ThumbSignals(QObject):
    ready = Signal(int, object)   # gen, Path


class _ThumbRunnable(QRunnable):
    """Build thumbnails for a list of PSARC paths in a worker thread."""

    def __init__(self, paths: list[Path], cache_dir: Path, gen: int) -> None:
        super().__init__()
        self.paths = paths
        self.cache_dir = cache_dir
        self.gen = gen
        self.signals = _ThumbSignals()
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        for p in self.paths:
            if self._cancelled:
                return
            try:
                out = build_thumbnail(p, self.cache_dir, target_size=_THUMB_SIZE)
            except Exception:  # pylint: disable=broad-except
                logger.exception("thumbnail failed for %s", p)
                continue
            if out is not None:
                self.signals.ready.emit(self.gen, p)


class _ScanSignals(QObject):
    chunk = Signal(int, object)           # gen, list[DlcEntry]
    finished = Signal(int, object)        # gen, list[DlcEntry]
    error = Signal(int, str)              # gen, message


class _ScanRunnable(QRunnable):
    def __init__(self, library: Library, force: bool, gen: int) -> None:
        super().__init__()
        self.library = library
        self.force = force
        self.gen = gen
        self.signals = _ScanSignals()
        self._emitted = 0

    def run(self) -> None:
        def on_chunk(_path: Path, entries: list[DlcEntry]) -> None:
            new = entries[self._emitted:]
            self._emitted = len(entries)
            if new:
                self.signals.chunk.emit(self.gen, new)
        try:
            self.library.scan(force=self.force, on_chunk=on_chunk, chunk_size=20)
            remaining = self.library.entries[self._emitted:]
            if remaining:
                self.signals.chunk.emit(self.gen, remaining)
            self.signals.finished.emit(self.gen, self.library.entries)
        except Exception as exc:
            logger.exception("scan failed")
            self.signals.error.emit(self.gen, str(exc))


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

_QSS = """
QMainWindow, QWidget { font-size: 10pt; }
QToolBar {
    spacing: 8px;
    padding: 6px 10px;
    border-bottom: 1px solid rgba(127,127,127,40);
}
QLineEdit, QComboBox {
    padding: 6px 10px;
    min-height: 22px;
    border-radius: 6px;
}
QPushButton {
    padding: 6px 14px;
    min-height: 22px;
    border-radius: 6px;
}
QTableView {
    gridline-color: rgba(127,127,127,30);
    selection-background-color: #3a6ea5;
    selection-color: white;
}
QTableView::item { padding: 6px 8px; }
QHeaderView::section {
    padding: 8px 10px;
    border: none;
    border-bottom: 1px solid rgba(127,127,127,60);
    font-weight: 600;
}
QStatusBar { padding: 4px 10px; }
"""


class MainWindow(QMainWindow):
    def __init__(self, library: Library) -> None:
        super().__init__()
        self.library = library
        self.setWindowTitle("rs-dlc-manager — Rocksmith 2014 DLC")
        self.resize(1100, 640)

        self.model = DlcTableModel()
        self.proxy = DlcFilterProxy()
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(Qt.ItemDataRole.DisplayRole)

        # ---- central layout ----
        central = QWidget(self)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)

        # filter bar
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Rechercher (artiste, titre, album)…  —  Ctrl+F")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.proxy.set_search)
        bar.addWidget(self.search, 4)

        self.status_combo = QComboBox()
        self.status_combo.addItem("Tous", "all")
        self.status_combo.addItem("Actifs", "active")
        self.status_combo.addItem("Désactivés", "disabled")
        self.status_combo.addItem("Doublons", "duplicates")
        self.status_combo.addItem("Favoris", "favorites")
        self.status_combo.currentIndexChanged.connect(
            lambda _i: self.proxy.set_status(self.status_combo.currentData())
        )
        bar.addWidget(QLabel("Statut"))
        bar.addWidget(self.status_combo, 1)

        self.tuning_combo = QComboBox()
        self.tuning_combo.addItem("Tous accordages", "")
        self.tuning_combo.currentIndexChanged.connect(
            lambda _i: self.proxy.set_tuning(self.tuning_combo.currentData())
        )
        bar.addWidget(QLabel("Accordage"))
        bar.addWidget(self.tuning_combo, 2)

        outer.addLayout(bar)

        # table
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(_THUMB_SIZE + 8)
        self.table.setIconSize(QSize(_THUMB_SIZE, _THUMB_SIZE))
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)   # Artiste
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)        # Titre
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_COL_FAVORITE, QHeaderView.ResizeMode.Fixed)
        header.setHighlightSections(False)
        self.table.setColumnWidth(_COL_FAVORITE, 40)
        self.table.clicked.connect(self._on_table_clicked)
        self.table.setColumnWidth(0, 220)
        self.table.setColumnWidth(3, 200)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context_menu)
        outer.addWidget(self.table)

        self.setCentralWidget(central)

        # ---- toolbar ----
        tb = QToolBar("Actions")
        tb.setMovable(False)
        tb.setIconSize(tb.iconSize())
        self.addToolBar(tb)
        tb.setIconSize(QSize(18, 18))
        act_disable = QAction(icon("eye-off"), "Désactiver", self)
        act_disable.triggered.connect(self.disable_selected)
        tb.addAction(act_disable)
        act_enable = QAction(icon("eye"), "Activer", self)
        act_enable.setShortcut(QKeySequence(Qt.Key.Key_Return))
        act_enable.triggered.connect(self.enable_selected)
        tb.addAction(act_enable)
        tb.addSeparator()
        act_trash = QAction(icon("trash-2"), "Supprimer (Suppr)", self)
        act_trash.setShortcut(QKeySequence(Qt.Key.Key_Delete))
        act_trash.triggered.connect(self.trash_selected)
        tb.addAction(act_trash)
        act_empty = QAction(icon("flame"), "Vider la corbeille", self)
        act_empty.triggered.connect(self.empty_trash)
        tb.addAction(act_empty)
        self._act_empty = act_empty
        tb.addSeparator()
        act_rescan = QAction(icon("refresh-cw"), "Rescanner", self)
        act_rescan.setShortcut(QKeySequence("Ctrl+R"))
        act_rescan.triggered.connect(lambda: self.start_scan(force=True))
        tb.addAction(act_rescan)
        act_undo = QAction(icon("undo-2"), "Annuler (Ctrl+Z)", self)
        act_undo.setShortcut(QKeySequence("Ctrl+Z"))
        act_undo.triggered.connect(self.undo_last)
        tb.addAction(act_undo)
        tb.addSeparator()
        act_stats = QAction(icon("bar-chart-3"), "Statistiques", self)
        act_stats.triggered.connect(self.show_stats)
        tb.addAction(act_stats)
        tb.addSeparator()
        act_pick = QAction(icon("folder-open"), "Changer de dossier…", self)
        act_pick.triggered.connect(self.pick_folder)
        tb.addAction(act_pick)
        self._act_undo = act_undo

        # Ctrl+F → search focus
        focus_search = QAction(self)
        focus_search.setShortcut(QKeySequence("Ctrl+F"))
        focus_search.triggered.connect(self._focus_search)
        self.addAction(focus_search)

        # ---- setlists dock ----
        self._setlists = SetlistStore(
            Path.home() / ".rs-dlc-manager" / "setlists.json",
            Path.home() / ".rs-dlc-manager" / "setlist_state.json",
        )
        self._build_setlist_dock()
        self._build_setlist_banner(outer)

        # ---- status bar ----
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status_label = QLabel("")
        self.status.addPermanentWidget(self.status_label)

        # ---- settings + first scan ----
        self.settings = QSettings("Apptic", "rs-dlc-manager")
        geom = self.settings.value("geometry")
        if isinstance(geom, (bytes, bytearray)):
            self.restoreGeometry(geom)
        col_sizes = self.settings.value("columns")
        if isinstance(col_sizes, list):
            for i, w in enumerate(col_sizes):
                if isinstance(w, int) and w > 0 and i < self.model.columnCount():
                    self.table.setColumnWidth(i, w)

        self._pool = QThreadPool.globalInstance()
        self._scan_gen = 0
        self._thumb_runnable: _ThumbRunnable | None = None
        self._thumb_cache_dir = Path.home() / ".rs-dlc-manager" / "thumbnails"
        self.model.set_thumbnail_cache_dir(self._thumb_cache_dir)
        self._favorites = Favorites(Path.home() / ".rs-dlc-manager" / "favorites.json")
        self.model.set_favorites(self._favorites)

        # Ctrl+B or F: toggle favorite on selection
        act_fav = QAction(self)
        act_fav.setShortcut(QKeySequence("F"))
        act_fav.triggered.connect(self._toggle_favorite_on_selection)
        self.addAction(act_fav)
        self._update_status_label()
        self._refresh_undo_state()
        self._refresh_trash_label()
        self.start_scan(force=False)

    # ---- scanning ----
    def start_scan(self, force: bool) -> None:
        if self._thumb_runnable is not None:
            self._thumb_runnable.cancel()
            self._thumb_runnable = None
        self._scan_gen += 1
        self.model.set_rows([])
        self._known_tunings: set[str] = set()
        self.tuning_combo.blockSignals(True)
        self.tuning_combo.clear()
        self.tuning_combo.addItem("Tous accordages", "")
        self.tuning_combo.blockSignals(False)
        self.status.showMessage("Scan en cours…")
        runnable = _ScanRunnable(self.library, force=force, gen=self._scan_gen)
        runnable.signals.chunk.connect(self._on_chunk)
        runnable.signals.finished.connect(self._on_finished)
        runnable.signals.error.connect(self._on_error)
        self._pool.start(runnable)

    def _on_chunk(self, gen: int, new_entries: list[DlcEntry]) -> None:
        if gen != self._scan_gen:
            return  # stale event from a previous scan generation
        self.model.append_rows(new_entries)
        # learn new tunings
        added = False
        for e in new_entries:
            if e.tuning_label and e.tuning_label not in self._known_tunings:
                self._known_tunings.add(e.tuning_label)
                self.tuning_combo.addItem(e.tuning_label, e.tuning_label)
                added = True
        if added:
            # keep alphabetical order from the 2nd item onward
            data = [(self.tuning_combo.itemText(i), self.tuning_combo.itemData(i))
                    for i in range(1, self.tuning_combo.count())]
            data.sort(key=lambda x: x[0].lower())
            current = self.tuning_combo.currentData()
            self.tuning_combo.blockSignals(True)
            while self.tuning_combo.count() > 1:
                self.tuning_combo.removeItem(1)
            for text, val in data:
                self.tuning_combo.addItem(text, val)
            idx = self.tuning_combo.findData(current)
            if idx >= 0:
                self.tuning_combo.setCurrentIndex(idx)
            self.tuning_combo.blockSignals(False)
        self._update_status_label()

    def _on_finished(self, gen: int, entries: list[DlcEntry]) -> None:
        if gen != self._scan_gen:
            return
        self.status.showMessage("Scan terminé", 4000)
        self._update_status_label()
        self._start_thumbnail_build(entries)

    def _start_thumbnail_build(self, entries: list[DlcEntry]) -> None:
        seen: set[Path] = set()
        paths: list[Path] = []
        for e in entries:
            if e.path in seen:
                continue
            seen.add(e.path)
            png = cache_path_for(e.path, self._thumb_cache_dir)
            if not png.is_file():
                paths.append(e.path)
        if not paths:
            return
        runnable = _ThumbRunnable(paths, self._thumb_cache_dir, gen=self._scan_gen)
        runnable.signals.ready.connect(self._on_thumbnail_ready)
        self._thumb_runnable = runnable
        self._pool.start(runnable)

    def _on_thumbnail_ready(self, gen: int, path: Path) -> None:
        if gen != self._scan_gen:
            return
        self.model.notify_thumbnail_ready(path)

    def _on_error(self, gen: int, msg: str) -> None:
        if gen != self._scan_gen:
            return
        logger.error("scan error (gen %d): %s", gen, msg)
        self.status.showMessage(f"Erreur durant le scan: {msg}", 6000)

    def _update_status_label(self) -> None:
        entries = self.model.all_entries()
        # Count unique psarc files, not songs
        active_files = {e.path for e in entries if e.enabled}
        disabled_files = {e.path for e in entries if not e.enabled}
        total = len(active_files) + len(disabled_files)
        self.status_label.setText(
            f"{len(active_files)} actifs  ·  {len(disabled_files)} désactivés  ·  {total} total"
        )

    # ---- selection actions ----
    def _selected_unique_paths(self, want_enabled: bool | None = None) -> list[tuple[int, DlcEntry]]:
        sel = self.table.selectionModel().selectedRows()
        out: list[tuple[int, DlcEntry]] = []
        seen: set[Path] = set()
        for proxy_idx in sel:
            src_idx = self.proxy.mapToSource(proxy_idx)
            row = src_idx.row()
            e = self.model.entry(row)
            if want_enabled is not None and e.enabled is not want_enabled:
                continue
            if e.path in seen:
                continue
            seen.add(e.path)
            out.append((row, e))
        return out

    def disable_selected(self) -> None:
        self._toggle_selected(enable=False)

    def enable_selected(self) -> None:
        self._toggle_selected(enable=True)

    def trash_selected(self) -> None:
        """Move every selected PSARC to the trash. Silent — undoable via Ctrl+Z."""
        targets = self._selected_unique_paths()
        if not targets:
            return
        moved: list[Path] = []
        for _row, e in targets:
            try:
                self.library.trash(e.path)
                moved.append(e.path)
            except (OSError, ValueError) as exc:
                logger.warning("trash failed for %s: %s", e.path, exc)
        if moved:
            self.model.remove_paths(moved)
        self._refresh_undo_state()
        self._refresh_trash_label()
        self._update_status_label()

    def empty_trash(self) -> None:
        """Permanently delete every file currently in the trash."""
        n = self.library.empty_trash()
        self._refresh_trash_label()
        self._refresh_undo_state()
        if n:
            self.status.showMessage(f"Corbeille vidée — {n} fichier(s) supprimé(s)", 4000)
        else:
            self.status.showMessage("Corbeille déjà vide", 2000)

    def _refresh_trash_label(self) -> None:
        n = len(self.library.trash_files())
        self._act_empty.setText(f"Vider la corbeille ({n})")
        self._act_empty.setEnabled(n > 0)

    def _on_table_context_menu(self, pos: QPoint) -> None:
        idx_at_pos = self.table.indexAt(pos)
        if idx_at_pos.isValid():
            sm = self.table.selectionModel()
            if not sm.isRowSelected(idx_at_pos.row(), idx_at_pos.parent()):
                self.table.selectRow(idx_at_pos.row())

        targets = self._selected_unique_paths()
        if not targets:
            return
        has_active = any(e.enabled for _r, e in targets)
        has_disabled = any(not e.enabled for _r, e in targets)

        menu = QMenu(self)
        act_disable = menu.addAction("Désactiver")
        act_disable.setEnabled(has_active)
        act_disable.triggered.connect(self.disable_selected)

        act_enable = menu.addAction("Activer")
        act_enable.setEnabled(has_disabled)
        act_enable.triggered.connect(self.enable_selected)

        menu.addSeparator()
        act_delete = menu.addAction("Supprimer (Suppr)")
        act_delete.triggered.connect(self.trash_selected)

        menu.addSeparator()
        add_menu = menu.addMenu("Ajouter à la setlist")
        names = self._setlists.names()
        if names:
            for sl_name in names:
                act = add_menu.addAction(sl_name)
                act.triggered.connect(lambda _checked=False, n=sl_name: self._add_to_setlist(n))
            add_menu.addSeparator()
        act_new = add_menu.addAction("+ Nouvelle setlist…")
        act_new.triggered.connect(self._add_to_new_setlist)

        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _add_to_setlist(self, name: str) -> None:
        paths = [e.path for _r, e in self._selected_unique_paths()]
        if not paths:
            return
        try:
            added = self._setlists.add_paths(name, paths)
        except SetlistError as exc:
            self.status.showMessage(str(exc), 4000)
            return
        self._setlists.save()
        self.status.showMessage(
            f"{added} chanson(s) ajoutée(s) à '{name}'", 3000,
        )

    def _add_to_new_setlist(self) -> None:
        name, ok = QInputDialog.getText(self, "Nouvelle setlist", "Nom :")
        if not ok or not name.strip():
            return
        try:
            self._setlists.create(name.strip())
        except SetlistError as exc:
            self.status.showMessage(str(exc), 4000)
            return
        self._add_to_setlist(name.strip())
        self._refresh_setlist_list()

    def _toggle_selected(self, *, enable: bool) -> None:
        targets = self._selected_unique_paths(want_enabled=not enable)
        if not targets:
            return
        updates: list[tuple[Path, Path, bool]] = []
        for _row, e in targets:
            try:
                new_path = self.library.enable(e.path) if enable else self.library.disable(e.path)
            except (OSError, ValueError) as exc:
                logger.warning("%s failed for %s: %s",
                               "enable" if enable else "disable", e.path, exc)
                continue
            self.model.rename_favorite(e.path, new_path)
            self._setlists.rename_path(e.path, new_path)
            updates.append((e.path, new_path, enable))
        if updates:
            self.model.replace_paths_batch(updates)
        self._refresh_undo_state()
        self._update_status_label()

    def undo_last(self) -> None:
        if not self.library.can_undo():
            return
        last = self.library._undo[-1]
        src_before, dst_after = last
        restored = self.library.undo()
        if restored is None:
            return
        enabled = self._is_under(restored, self.library.dlc_dir)
        # If the undone move was a trash, the row no longer exists in the model
        # (we removed it on trash). Replacing a non-existent path is a no-op,
        # which is acceptable — the user can hit Ctrl+R to see it back.
        self.model.replace_path(dst_after, restored, enabled=enabled)
        self._refresh_undo_state()
        self._refresh_trash_label()
        self._update_status_label()

    @staticmethod
    def _is_under(child: Path, root: Path) -> bool:
        try:
            child.resolve().relative_to(root.resolve())
            return True
        except ValueError:
            return False

    def _notify_errors(self, title: str, errs: list[tuple[Path, Exception]]) -> None:
        lines = [f"{p.name} — {e}" for p, e in errs[:8]]
        if len(errs) > 8:
            lines.append(f"… et {len(errs) - 8} autres")
        QMessageBox.warning(self, title, "\n".join(lines))

    def _refresh_undo_state(self) -> None:
        self._act_undo.setEnabled(self.library.can_undo())

    # ---- folder picker ----
    def pick_folder(self) -> None:
        start = str(self.library.rocksmith_root)
        chosen = QFileDialog.getExistingDirectory(self, "Dossier Rocksmith2014", start)
        if not chosen:
            return
        new_root = Path(chosen)
        if not (new_root / "dlc").is_dir() and not looks_like_rocksmith_root(new_root):
            if QMessageBox.question(
                self, "Dossier inhabituel",
                f"{new_root} ne contient pas de sous-dossier dlc/. Continuer quand même ?",
            ) != QMessageBox.StandardButton.Yes:
                return
        self.library = Library(new_root)
        self.settings.setValue("rocksmith_root", str(new_root))
        self.start_scan(force=False)

    def _focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    # ---- setlists UI ----

    def _build_setlist_dock(self) -> None:
        dock = QDockWidget("Setlists", self)
        dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea
                             | Qt.DockWidgetArea.RightDockWidgetArea)
        dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable
                         | QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)
        self.setlist_list = QListWidget()
        self.setlist_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setlist_list.customContextMenuRequested.connect(self._on_setlist_context_menu)
        self.setlist_list.itemSelectionChanged.connect(self._refresh_setlist_buttons)
        v.addWidget(self.setlist_list, 1)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.btn_new_setlist = QPushButton("+ Nouveau")
        self.btn_new_setlist.clicked.connect(self._create_setlist)
        row.addWidget(self.btn_new_setlist)
        self.btn_activate_setlist = QPushButton("Activer")
        self.btn_activate_setlist.clicked.connect(self._activate_or_restore)
        row.addWidget(self.btn_activate_setlist)
        v.addLayout(row)
        dock.setWidget(container)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
        self._refresh_setlist_list()

    def _build_setlist_banner(self, outer: QVBoxLayout) -> None:
        self.setlist_banner = QFrame()
        self.setlist_banner.setObjectName("setlistBanner")
        self.setlist_banner.setStyleSheet(
            "QFrame#setlistBanner { background: #f5c518; border-radius: 6px; }"
            "QFrame#setlistBanner QLabel { color: #1a1a1a; font-weight: 600; }"
        )
        h = QHBoxLayout(self.setlist_banner)
        h.setContentsMargins(12, 8, 8, 8)
        self.setlist_banner_label = QLabel("")
        h.addWidget(self.setlist_banner_label, 1)
        btn = QPushButton("Restaurer l'état précédent")
        btn.clicked.connect(self._restore_setlist)
        h.addWidget(btn)
        outer.insertWidget(0, self.setlist_banner)
        self._refresh_setlist_banner()

    def _refresh_setlist_list(self) -> None:
        previous = None
        cur = self.setlist_list.currentItem()
        if cur is not None:
            previous = cur.text().lstrip("● ").strip()
        self.setlist_list.clear()
        for name in self._setlists.names():
            label = f"● {name}" if name == self._setlists.active_name else name
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, name)
            self.setlist_list.addItem(it)
            if name == previous:
                self.setlist_list.setCurrentItem(it)
        self._refresh_setlist_buttons()

    def _selected_setlist_name(self) -> str | None:
        cur = self.setlist_list.currentItem()
        if cur is None:
            return None
        name = cur.data(Qt.ItemDataRole.UserRole)
        return str(name) if isinstance(name, str) else None

    def _refresh_setlist_buttons(self) -> None:
        name = self._selected_setlist_name()
        active = self._setlists.active_name
        if name is None:
            self.btn_activate_setlist.setEnabled(False)
            self.btn_activate_setlist.setText("Activer")
            return
        self.btn_activate_setlist.setEnabled(True)
        if name == active:
            self.btn_activate_setlist.setText("Restaurer")
        elif active is not None:
            self.btn_activate_setlist.setText("Basculer vers celle-ci")
        else:
            self.btn_activate_setlist.setText("Activer cette setlist")

    def _refresh_setlist_banner(self) -> None:
        name = self._setlists.active_name
        if name is None:
            self.setlist_banner.hide()
        else:
            self.setlist_banner_label.setText(f"Setlist active : {name}")
            self.setlist_banner.show()

    def _create_setlist(self) -> None:
        name, ok = QInputDialog.getText(self, "Nouvelle setlist", "Nom :")
        if not ok or not name.strip():
            return
        try:
            self._setlists.create(name.strip())
        except SetlistError as exc:
            self.status.showMessage(str(exc), 4000)
            return
        self._setlists.save()
        self._refresh_setlist_list()
        # Select the newly created one
        for i in range(self.setlist_list.count()):
            it = self.setlist_list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == name.strip():
                self.setlist_list.setCurrentItem(it)
                break

    def _activate_or_restore(self) -> None:
        name = self._selected_setlist_name()
        if name is None:
            return
        if self._setlists.active_name == name:
            self._restore_setlist()
            return
        try:
            if self._setlists.active_name is not None:
                en, dis, errs = self._setlists.switch_to(name, self.library)
            else:
                en, dis, errs = self._setlists.activate(name, self.library)
        except SetlistError as exc:
            self.status.showMessage(str(exc), 6000)
            return
        if errs:
            logger.warning("setlist apply errors: %s", errs)
        self._setlists.save()
        self.status.showMessage(
            f"Setlist '{name}' activée — {en} activées, {dis} désactivées",
            4000,
        )
        self._refresh_setlist_list()
        self._refresh_setlist_banner()
        self.start_scan(force=False)

    def _restore_setlist(self) -> None:
        if self._setlists.active_name is None:
            return
        try:
            en, dis, errs = self._setlists.restore(self.library)
        except SetlistError:
            return
        if errs:
            logger.warning("setlist restore errors: %s", errs)
        self._setlists.save()
        self.status.showMessage(
            f"État précédent restauré — {en} activées, {dis} désactivées",
            4000,
        )
        self._refresh_setlist_list()
        self._refresh_setlist_banner()
        self.start_scan(force=False)

    def _on_setlist_context_menu(self, pos: QPoint) -> None:
        name = self._selected_setlist_name()
        item = self.setlist_list.itemAt(pos)
        if item is not None:
            self.setlist_list.setCurrentItem(item)
            name = self._selected_setlist_name()
        if name is None:
            return
        menu = QMenu(self)
        act_rename = menu.addAction("Renommer…")
        act_rename.triggered.connect(self._rename_setlist)
        act_delete = menu.addAction("Supprimer")
        act_delete.setEnabled(self._setlists.active_name != name)
        act_delete.triggered.connect(self._delete_setlist)
        menu.exec(self.setlist_list.viewport().mapToGlobal(pos))

    def _rename_setlist(self) -> None:
        name = self._selected_setlist_name()
        if name is None:
            return
        new, ok = QInputDialog.getText(self, "Renommer la setlist", "Nouveau nom :", text=name)
        if not ok or not new.strip() or new.strip() == name:
            return
        try:
            self._setlists.rename(name, new.strip())
        except SetlistError as exc:
            self.status.showMessage(str(exc), 4000)
            return
        self._setlists.save()
        self._refresh_setlist_list()
        self._refresh_setlist_banner()

    def _delete_setlist(self) -> None:
        name = self._selected_setlist_name()
        if name is None:
            return
        try:
            self._setlists.delete(name)
        except SetlistError as exc:
            self.status.showMessage(str(exc), 4000)
            return
        self._setlists.save()
        self._refresh_setlist_list()

    def _on_table_clicked(self, proxy_idx: QModelIndex) -> None:
        if proxy_idx.column() != _COL_FAVORITE:
            return
        src_idx = self.proxy.mapToSource(proxy_idx)
        e = self.model.entry(src_idx.row())
        self.model.toggle_favorite(e.path)
        # If we're filtering on favorites, the row may have disappeared.
        self.proxy.invalidateFilter()

    def _toggle_favorite_on_selection(self) -> None:
        seen: set[Path] = set()
        for proxy_idx in self.table.selectionModel().selectedRows():
            src_idx = self.proxy.mapToSource(proxy_idx)
            e = self.model.entry(src_idx.row())
            if e.path in seen:
                continue
            seen.add(e.path)
            self.model.toggle_favorite(e.path)
        if seen:
            self.proxy.invalidateFilter()

    def show_stats(self) -> None:
        entries = self.model.all_entries()
        s = compute_stats(entries)
        dlg = QDialog(self)
        dlg.setWindowTitle("Statistiques")
        dlg.resize(420, 520)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        def section(title: str) -> QLabel:
            lbl = QLabel(title)
            f = lbl.font()
            f.setBold(True)
            f.setPointSize(f.pointSize() + 1)
            lbl.setFont(f)
            return lbl

        def line(left: str, right: str) -> QLabel:
            return QLabel(f"<span style='color:#888'>{left}</span>  &nbsp;&nbsp;{right}")

        layout.addWidget(section("Vue d'ensemble"))
        layout.addWidget(line("Fichiers", f"{s.total_files}"))
        layout.addWidget(line("Actifs", f"{s.active_files}"))
        layout.addWidget(line("Désactivés", f"{s.disabled_files}"))
        layout.addWidget(line("Chansons", f"{s.total_songs}"))
        layout.addWidget(line("Durée totale", format_duration(s.total_seconds)))
        if s.songs_without_metadata:
            layout.addWidget(line("Sans métadonnées", str(s.songs_without_metadata)))

        if s.by_tuning:
            layout.addWidget(section("Accordages"))
            for name, n in s.by_tuning[:8]:
                layout.addWidget(line(name, f"{n}"))

        if s.by_decade:
            layout.addWidget(section("Par décennie"))
            for decade, n in s.by_decade:
                layout.addWidget(line(f"{decade}s", f"{n}"))

        if s.top_artists:
            layout.addWidget(section("Top artistes"))
            for name, n in s.top_artists:
                layout.addWidget(line(name, f"{n}"))

        layout.addStretch(1)
        dlg.exec()

    # ---- persistence ----
    def closeEvent(self, event: Any) -> None:
        self.settings.setValue("geometry", self.saveGeometry())
        widths = [self.table.columnWidth(i) for i in range(self.model.columnCount())]
        self.settings.setValue("columns", widths)
        self.library.cache.save()
        self._favorites.save()
        self._setlists.save()
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------

def _apply_theme(app: QApplication) -> None:
    """Apply qdarktheme + our custom QSS. Works with both pyqtdarktheme 1.x and 2.x."""
    theme = "auto"
    setup_theme = getattr(qdarktheme, "setup_theme", None)
    if callable(setup_theme):
        try:
            setup_theme(theme, additional_qss=_QSS)
            return
        except TypeError:  # older 2.x signatures
            setup_theme(theme)
            app.setStyleSheet(app.styleSheet() + _QSS)
            return
    # pyqtdarktheme 1.x
    load_stylesheet = getattr(qdarktheme, "load_stylesheet", None)
    base = ""
    if callable(load_stylesheet):
        try:
            base = load_stylesheet(theme)
        except Exception:  # noqa: BLE001
            base = load_stylesheet()
    load_palette = getattr(qdarktheme, "load_palette", None)
    if callable(load_palette):
        try:
            app.setPalette(load_palette(theme))
        except Exception:  # noqa: BLE001
            pass
    app.setStyleSheet((base or "") + _QSS)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _choose_root(initial: Path | None) -> Path | None:
    if initial and (initial / "dlc").is_dir():
        return initial
    if initial and looks_like_rocksmith_root(initial):
        return initial
    chosen = QFileDialog.getExistingDirectory(
        None, "Sélectionne le dossier Rocksmith2014",
        str(initial) if initial else "",
    )
    if not chosen:
        return None
    return Path(chosen)


def run() -> int:
    log_dir = Path.home() / ".rs-dlc-manager"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_dir / "log.txt", encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )
    if "--debug" in sys.argv:
        logging.getLogger().setLevel(logging.DEBUG)

    app = QApplication(sys.argv)
    _apply_theme(app)
    app.setApplicationName("rs-dlc-manager")
    app.setOrganizationName("Apptic")
    QApplication.setFont(QFont(QApplication.font().family(), 10))

    settings = QSettings("Apptic", "rs-dlc-manager")
    stored_root = settings.value("rocksmith_root")
    initial: Path | None = Path(stored_root) if isinstance(stored_root, str) else None
    if initial is None or not initial.is_dir():
        initial = autodetect_rocksmith_root()

    root = _choose_root(initial)
    if root is None:
        return 1
    settings.setValue("rocksmith_root", str(root))

    library = Library(root)
    window = MainWindow(library)
    window.show()
    return app.exec()


__all__ = ["run", "MainWindow"]
