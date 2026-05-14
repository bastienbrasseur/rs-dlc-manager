# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Run the app
```powershell
python main.py                       # depuis le repo, sans install
rs-dlc-manager                       # après `pip install -e ".[dev]"`
python main.py --debug               # log verbeux dans ~/.rs-dlc-manager/log.txt
```

### Tests et type-check
```powershell
python -m pytest -q                            # tous les tests (pythonpath = src via pyproject)
python -m pytest tests/test_psarc.py -q        # un fichier
python -m pytest tests/test_library.py::test_disable_and_undo -q  # un test
python -m mypy                                  # strict, configuré sur src/rsdlc + tests
```

### CLI debug du parser PSARC
```powershell
python -m rsdlc.psarc <chemin.psarc>            # liste le contenu
python -m rsdlc.psarc <chemin.psarc> --find .hsan   # extrait des fichiers
```

### Packaging
```powershell
pyinstaller "Rocksmith DLC Manager.spec"        # build avec icône (assets/icon.ico)
python tools/build_icon.py                       # régénère assets/icon.ico depuis le SVG 'guitar'
```

## Architecture

L'app est un PySide6 sur Python ≥ 3.11, **strict-typed** (`mypy --strict` doit passer sur `src/`).

### Règle d'or — séparation Qt
**Aucun import PySide6 hors de `ui.py` et `icons.py`.** Tout le reste (`psarc`, `manifest`, `library`, `paths`, `favorites`, `setlists`, `duplicates`, `stats`, `albumart`) est pur Python stdlib + cryptography + Pillow, utilisable en CLI ou en script sans Qt. Quand on ajoute de la logique, elle va dans le module métier ; `ui.py` n'orchestre que la vue.

### Pipeline d'une PSARC

1. `psarc.py` — parser PSARC v1.4 PC pur stdlib + zlib. Lazy : le header (32 octets), la TOC et la liste des tailles de blocs sont décodés à l'ouverture, les payloads sont inflate-és à la demande via `PsarcArchive.read(name)`. La TOC est AES-256 CFB chiffrée pour les DLC officiels (flag `archive_flags & 4`), déchiffrement dans `crypto.py`.
2. `manifest.py` — parse les fichiers `.hsan` (JSON) trouvés dans le PSARC. Groupe les entrées par `(artist, title, album)` (un PSARC pack contient plusieurs Songs). Mappe les offsets de cordes (`string0..5`, en demi-tons par rapport à E standard) sur des noms lisibles via `_KNOWN_TUNINGS` (E/Eb/D/Drop D/…). Fallback sur `(+0,-2,…)`.
3. `library.py` — `Library` gère la paire `<root>/dlc/` ↔ `<root>/dlc_disabled/` (+ `dlc_trash/`). `scan()` itère récursivement les `*_p.psarc` des deux dossiers (on ignore `_m.psarc` Mac). Émet un `on_chunk` tous les N fichiers pour que la UI puisse refresh progressivement.

### Convention disque

- Active : `<root>/dlc/...*_p.psarc`
- Désactivé : `<root>/dlc_disabled/...*_p.psarc` (chemin relatif **mirroré** depuis dlc/, donc la réactivation restaure l'arbre)
- Corbeille : `<root>/dlc_trash/...` (réversible via Ctrl+Z)

Enable/disable = `shutil.move` atomique. **On ne touche jamais aux octets d'une PSARC.** Toutes les opérations passent par `_move()` qui pousse un `(from, to)` sur `_undo` ; `undo()` dépile et inverse.

### Cache

`~/.rs-dlc-manager/cache.json` — JSON keyed sur `str(absolute_path)`. Une entrée est valide ssi `(mtime_ns, size)` correspond exactement → tout changement invalide. Bump `CACHE_VERSION` (library.py) à chaque évolution du schéma sérialisé. Le scan purge automatiquement les chemins qui n'existent plus.

Autres fichiers utilisateur dans `~/.rs-dlc-manager/` :
- `favorites.json` (set de chemins absolus, voir `favorites.py`)
- `setlists.json` (`{name: [paths]}`) + `setlist_state.json` (snapshot enable/disable quand une setlist est active, restoration exacte via `SetlistStore.restore`)
- `log.txt`
- Thumbnails album-art (un PNG par PSARC, clé = sha1 du chemin absolu — voir `albumart.cache_filename`)

### UI

`ui.py` (~1900 lignes, monolithique volontaire) :
- `DlcTableModel` (QAbstractTableModel) — adapte `list[DlcEntry]` à la table. Colonnes : Artiste, Titre, Album, Année, Arrangements, Accordage, Statut, ★ (favori).
- Scan en background via `QThreadPool` + `QRunnable`, callbacks `on_chunk` pour progressive rendering.
- Drag-and-drop pour réorganiser les setlists ; MIME type custom `application/x-rsdlc-paths`.
- Thème via `qdarktheme` ; icônes Lucide bundlées comme SVG strings dans `icons.py` et tintées au theme color au runtime.

## Conventions du repo

- Texte UI **en français** (la stack pro de Bastien est francophone). Code, identifiants, commentaires, commit messages techniques restent en anglais standard, sauf les commits de Bastien qui sont en français libre.
- Pas d'ORM, pas de framework. Stdlib + 4 deps (`cryptography`, `Pillow`, `PySide6`, `pyqtdarktheme`).
- Pas d'I/O dans les dataclasses. Les `@dataclass(frozen=True, slots=True)` (`DlcEntry`, `Song`, `Arrangement`, `Stats`) sont des valeurs pures.
- Les tests construisent leurs propres PSARC factices en mémoire (`tests/conftest.py` ajoute `src/` au path) — aucun binaire commité.
- PC uniquement. Les `_m.psarc` (Mac) sont ignorés par design ; Xbox/PS3 sont des formats différents et hors scope.
