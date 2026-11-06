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

## Tests

```powershell
pytest
```

Les tests ne demandent pas d'interface graphique. `tests/test_architecture.py` vérifie le sens des dépendances décrit plus bas.

## Où sont les données

- **Campagnes** : une campagne est un dossier autonome contenant un `campaign.json`. Il peut être rangé n'importe où (disque, clé USB, dossier synchronisé) et déplacé librement. On l'ouvre avec *File > Open Campaign…*, et *File > Open Recent* liste les dernières campagnes. Au démarrage, l'application rouvre la dernière campagne ouverte.
- **Données de cet ordinateur** : dossier `%LOCALAPPDATA%\RPGTabletop` sous Windows. Il contient :
  - `default_campaign/`, la campagne utilisée quand aucune autre n'est connue ;
  - `certs/`, le certificat TLS du serveur, unique pour la machine ;
  - `recent_campaigns.json`.

  La variable d'environnement `RPG_TABLETOP_DATA_DIR` permet de choisir un autre dossier.

Les anciennes versions créaient `default_campaign/` dans le dossier de lancement. Au premier démarrage, ce dossier est copié dans `%LOCALAPPDATA%\RPGTabletop`, ainsi que son certificat, pour que les joueurs n'aient pas à refaire confiance au serveur. L'ancien dossier n'est pas modifié.

## Organisation du code

```
rpg_tabletop/
├── apps/               points d'entrée : gm.py (MJ), player.py (joueur)
├── core/               logique pure : dés, stats, règles, géométrie, dossiers utilisateur
├── assets/             données de campagne et leur persistance (map/ : cartes)
├── session/            partie en cours : entités, actions, ciblage, initiative,
│                       journal et sauvegarde ; runtime/ : SessionRuntime découpé en
│                       mixins (events, player_rules, maps, entities, sheet_edits, combat)
├── net/                réseau (serveur, client, protocole, TLS, relais) — sans Qt
└── ui/
    ├── theme/              palette Qt et feuilles de style
    ├── common/             widgets partagés par le MJ et le joueur
    ├── cards/              cartes de ressources et aperçu des formules
    ├── campaign_manager/   fenêtre principale, navigateur, editors/ (dont map/)
    ├── gm_runtime/         fenêtre de partie du MJ
    └── player/             application joueur
tests/                  tests pytest (sans interface graphique)
packaging/              specs PyInstaller
```

Sens des dépendances : `core` ← `assets` ← `session` ← `net` ← `ui` ← `apps`. Un module n’importe jamais une couche située à sa droite. Seul `ui` dépend de Qt, à une exception près : le rendu des cartes dans `assets/map/`. Une carte est dessinée par un seul moteur, utilisé à la fois par l'éditeur et par la compilation en tuiles.
