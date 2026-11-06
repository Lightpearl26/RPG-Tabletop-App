# RPG Tabletop App

Éditeur de campagnes (IDE) et runtime en ligne pour jeux de rôle sur table.

Le projet contient deux applications :

- **RPG Tabletop** (MJ) : gestionnaire de campagne (personnages, sorts, objets, cartes, sessions) et runtime de partie qui héberge le serveur.
- **RPG Tabletop Player** : client joueur qui se connecte au runtime du MJ.

## Installation

Python 3.12 ou plus récent.

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
# outils de développement (tests, pylint, PyInstaller)
pip install -e .[dev]
```

## Lancer les applications

```powershell
python -m rpg_tabletop                 # application MJ
python -m rpg_tabletop.apps.player     # application joueur
```

Dans VS Code, utiliser une configuration de lancement de type `module` avec `rpg_tabletop` ou `rpg_tabletop.apps.player`.

## Construire les exécutables

```powershell
pyinstaller packaging/gm.spec
pyinstaller packaging/player.spec
```

Les exécutables sont écrits dans `dist/`.

## Organisation du code

```
rpg_tabletop/
├── apps/               points d'entrée : gm.py (MJ), player.py (joueur)
├── core/               logique pure : dés, règles, géométrie de grille
├── assets/             données de campagne et leur persistance
├── session/            partie en cours : entités, actions, ciblage, initiative, sauvegarde
├── net/                réseau (serveur, client, protocole, TLS, relais) — sans Qt
└── ui/
    ├── theme/              palette Qt et feuilles de style
    ├── common/             widgets partagés par le MJ et le joueur
    ├── cards/              cartes de ressources et aperçu des formules
    ├── campaign_manager/   fenêtre principale, navigateur, éditors/ (dont map/)
    ├── gm_runtime/         fenêtre de partie du MJ
    └── player/             application joueur
```

Sens des dépendances : `core` ← `assets` ← `session` ← `net` ← `ui` ← `apps`. Un module n’importe jamais une couche située à sa droite. Seul `ui` dépend de Qt, à une exception près : le rendu des cartes dans `assets/map.py`.
