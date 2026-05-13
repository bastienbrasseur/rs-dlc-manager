# rs-dlc-manager

Petit gestionnaire pour les DLC Rocksmith 2014 (PC, Steam).

- Scanne le dossier `dlc/` de ton install Rocksmith.
- Lit titre / artiste / album / année / arrangements / accordage **sans toucher aux `.psarc`**.
- Désactive une chanson en la déplaçant dans un dossier frère `dlc_disabled/`. Réactivation = mouvement inverse. Annulation possible (Ctrl+Z).
- Filtres : recherche live, statut (tous / actifs / désactivés), accordage.
- Cache local pour ne pas re-parser les fichiers à chaque lancement.

## Lancement rapide (Windows)

```powershell
cd rs-dlc-manager
python -m pip install -r requirements-min.txt   # ou : pip install -e .
python main.py
```

Au premier lancement l'app détecte ton install Steam (`C:\Program Files (x86)\Steam\steamapps\common\Rocksmith2014`). Sinon, choisis manuellement le dossier `Rocksmith2014`.

## Installation propre

```bash
python -m venv .venv
. .venv/Scripts/activate     # Linux/Mac: . .venv/bin/activate
pip install -e ".[dev]"
rs-dlc-manager               # script entry-point installé par pip
```

Python ≥ 3.11 requis. Tout est typé strict (`mypy --strict` passe sur `src/`).

## Raccourcis clavier

| Raccourci | Action                                    |
|-----------|-------------------------------------------|
| Ctrl+F    | Focus sur la recherche                    |
| Suppr     | Désactiver la sélection                   |
| Entrée    | Réactiver la sélection                    |
| Ctrl+R    | Rescan complet (ignore le cache)          |
| Ctrl+Z    | Annuler le dernier déplacement            |

Sélection multiple supportée (Shift+clic, Ctrl+clic).

## Architecture

```
src/rsdlc/
├── psarc.py     # Parser PSARC v1.4 (lazy, stdlib uniquement)
├── crypto.py    # AES-256 CFB pour le TOC chiffré (DLC officiels + CDLC)
├── manifest.py  # Décode le hsan JSON → dataclasses Song/Arrangement
├── library.py   # Scan, cache JSON, enable/disable atomique, undo
├── paths.py     # Auto-détection chemins Steam
└── ui.py        # PySide6 (toutes les classes Qt sont ici)
```

Aucun import PySide6 hors de `ui.py`. Le reste est sans dépendance UI et utilisable en CLI / script.

CLI de debug pour le parser :

```bash
python -m rsdlc.psarc /chemin/vers/un_p.psarc
python -m rsdlc.psarc /chemin/vers/un_p.psarc --find .hsan
```

## Cache et logs

- Cache : `~/.rs-dlc-manager/cache.json` (clé : chemin + mtime + taille → invalidation auto).
- Log : `~/.rs-dlc-manager/log.txt`. Lance avec `--debug` pour plus de détails.

## Tests

```bash
PYTHONPATH=src python -m pytest -q
```

Les tests construisent leurs propres PSARC factices en mémoire — aucun binaire commité. Le test du parser couvre round-trip multi-blocs, fichier manquant, mauvais magic, version/compression non supportée. Le test de la bibliothèque couvre scan, cache, disable/enable, undo, et collision de destination.

## Packaging (binaire unique)

PyInstaller fonctionne sans config particulière :

```bash
pip install pyinstaller
pyinstaller --onefile --noconsole --name rs-dlc-manager main.py
```

Le `.exe` (~50 Mo) inclut Python, PySide6 et toutes les deps.

## Limites connues

- **PC uniquement** : on ignore les `_m.psarc` (Mac). Pas de support Xbox/PS3 (formats différents).
- **Lecture seule sur les `.psarc`** — c'est volontaire. Tu ne peux pas modifier le contenu d'une chanson, juste activer/désactiver le fichier en bloc.
- Les `.psarc` qui ne contiennent pas de `.hsan` (tones packs, etc.) apparaissent quand même dans la liste, avec `(no metadata)` comme artiste — utile pour les désactiver sans connaître leur contenu.
