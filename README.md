# RPG Tabletop App

Éditeur de campagnes (IDE) et runtime en ligne pour jeux de rôle sur table.

Le projet contient deux applications :

- **RPG Tabletop** (MJ) : gestionnaire de campagne (personnages, sorts, objets, cartes, sessions) et runtime de partie qui héberge le serveur.
- **RPG Tabletop Player** : client joueur qui se connecte au runtime du MJ.

## Installation

Python 3.11 ou plus récent.

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
# outils de développement (tests, pylint, PyInstaller)
pip install -e .[dev]
```

## Lancer les applications

```powershell
python app.py          # application MJ
python player_app.py   # application joueur
```

## Construire les exécutables

```powershell
pyinstaller packaging/gm.spec
pyinstaller packaging/player.spec
```

Les exécutables sont écrits dans `dist/`.
