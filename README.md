# RPG Tabletop App

Éditeur de campagnes (IDE) et runtime en ligne pour jeux de rôle sur table.

Le projet contient deux applications :

- **RPG Tabletop** (MJ) : gestionnaire de campagne (personnages, sorts, objets, cartes, sessions) et runtime de partie qui héberge le serveur.
- **RPG Tabletop Player** : client joueur qui se connecte au runtime du MJ.

## Installer les applications

Un seul installeur, `RPGTabletop-Setup.exe`, propose deux types d'installation : *Joueur*, ou *Maître du jeu et joueur*. Il contient tout ce qu'il faut (Python embarqué, Qt et les autres dépendances) : rien n'est à installer avant. L'installation se fait pour l'utilisateur courant (`%LOCALAPPDATA%\Programs\RPG Tabletop`), sans droits administrateur. Windows SmartScreen peut avertir au premier lancement car l'installeur n'est pas signé : *Informations complémentaires > Exécuter quand même*.

### Mises à jour

Les raccourcis lancent d'abord le lanceur (`packaging/launcher.py`). Il lit `__version__` dans `rpg_tabletop/__init__.py` sur la branche `main` de GitHub. Si elle diffère de la version installée, il propose la mise à jour : le code de `main` est téléchargé et remplace celui installé, puis l'application démarre. *Ignorer cette version* ne la propose plus. Chez le MJ, *Help > Check for Updates…* relance le lanceur pour vérifier à la demande.

Si `requirements.txt` change, les nouvelles dépendances sont installées avec pip dans une fenêtre de console, puis l'application démarre. Si quoi que ce soit échoue (pas de réseau, pip), la version installée démarre ; les erreurs sont notées dans `%LOCALAPPDATA%\RPGTabletop\launcher.log`.

Le dépôt doit être public : le lanceur lit GitHub sans identifiant.

Lancée depuis les sources (dépôt git), l'application compare la branche locale à sa branche distante et propose un `git pull --ff-only`. Si `requirements.txt` ou `pyproject.toml` changent, elle demande de relancer `pip install -r requirements.txt`.

Variables d'environnement :
- `RPG_TABLETOP_NO_UPDATE=1` : pas de vérification au démarrage ;
- `RPG_TABLETOP_UPDATE_BRANCH=<branche>` : le lanceur suit une autre branche que `main` (tests).

## Développement

Python 3.12 ou plus récent.

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
# outils de développement (tests, pylint)
pip install -e .[dev]
```

## Lancer les applications

```powershell
python -m rpg_tabletop                 # application MJ
python -m rpg_tabletop.apps.player     # application joueur
```

Dans VS Code, utiliser une configuration de lancement de type `module` avec `rpg_tabletop` ou `rpg_tabletop.apps.player`.

## Publier une version

La version est définie à un seul endroit : `__version__` dans `rpg_tabletop/__init__.py`.

```powershell
# passer __version__ à "0.2.0", puis
git commit -am "Version 0.2.0"
git push origin main
```

Au lancement suivant, les applications installées proposent la mise à jour. Un push qui ne change pas `__version__` n'est proposé à personne : on peut pousser des corrections intermédiaires et ne changer la version que lorsqu'elles sont prêtes à être diffusées.

## Construire l'installeur

Il faut [Inno Setup 6](https://jrsoftware.org/isdl.php) (`winget install JRSoftware.InnoSetup`) et une connexion internet.

```powershell
.\packaging\build.ps1
```

Le script télécharge Python embarqué (version réglable avec `-PythonVersion`), y installe `requirements.txt`, ajoute le code et le lanceur, vérifie que les deux applications s'importent, puis écrit `dist\installers\RPGTabletop-Setup.exe`.

L'installeur n'est à refaire que pour de nouveaux utilisateurs (sinon il installe un code ancien, que le lanceur met à jour au premier lancement) ou pour changer de version de Python. On peut le diffuser comme on veut, par exemple en pièce jointe d'une release GitHub créée à la main.

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
  - `recent_campaigns.json` ;
  - `update.json`, les versions ignorées.

  La désinstallation ne touche pas à ce dossier.

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
├── net/                réseau (serveur, client, protocole, TLS, relais, mises à jour) — sans Qt
└── ui/
    ├── theme/              palette Qt et feuilles de style
    ├── common/             widgets partagés par le MJ et le joueur
    ├── cards/              cartes de ressources et aperçu des formules
    ├── campaign_manager/   fenêtre principale, navigateur, editors/ (dont map/)
    ├── gm_runtime/         fenêtre de partie du MJ
    └── player/             application joueur
tests/                  tests pytest (sans interface graphique)
packaging/              lanceur, installeur Inno Setup, build.ps1
```

Sens des dépendances : `core` ← `assets` ← `session` ← `net` ← `ui` ← `apps`. Un module n’importe jamais une couche située à sa droite. Seul `ui` dépend de Qt, à une exception près : le rendu des cartes dans `assets/map/`. Une carte est dessinée par un seul moteur, utilisé à la fois par l'éditeur et par la compilation en tuiles.
