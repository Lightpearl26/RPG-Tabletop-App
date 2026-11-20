# RPG Tabletop App

Éditeur de campagnes (IDE) et runtime en ligne pour jeux de rôle sur table.

Le projet contient deux applications :

- **RPG Tabletop** (MJ) : gestionnaire de campagne (personnages, sorts, objets, cartes, sessions) et runtime de partie qui héberge le serveur.
- **RPG Tabletop Player** : client joueur qui se connecte au runtime du MJ.

## Installer les applications

Télécharger l'installeur sur la [page des versions](https://github.com/Lightpearl26/RPG-Tabletop-App/releases/latest) :

- `RPGTabletop-Setup.exe` pour le MJ ;
- `RPGTabletopPlayer-Setup.exe` pour les joueurs.

L'installation se fait pour l'utilisateur courant (`%LOCALAPPDATA%\Programs`), sans droits administrateur. Windows SmartScreen peut avertir au premier lancement car l'installeur n'est pas signé : *Informations complémentaires > Exécuter quand même*.

### Mises à jour

Au démarrage, chaque application compare sa version à la dernière version publiée sur GitHub. Si elle est plus récente, elle propose de la télécharger : l'installeur est vérifié (somme SHA-256 de `SHA256SUMS.txt`), lancé en mode silencieux, puis l'application redémarre. *Ignorer cette version* ne la propose plus au démarrage ; chez le MJ, *Help > Check for Updates…* vérifie à la demande.

L'application joueur vérifie avant de se connecter, pour rester à la même version que le serveur du MJ.

Lancée depuis les sources (dépôt git), l'application compare la branche locale à sa branche distante et propose un `git pull --ff-only`. Si `requirements.txt` ou `pyproject.toml` changent, elle demande de relancer `pip install -r requirements.txt`.

La variable d'environnement `RPG_TABLETOP_NO_UPDATE=1` désactive la vérification au démarrage.

## Développement

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

## Construire les installeurs

Il faut PyInstaller (`pip install -e .[dev]`) et [Inno Setup 6](https://jrsoftware.org/isdl.php) (`winget install JRSoftware.InnoSetup`).

```powershell
.\packaging\build.ps1
```

Le script construit les deux applications (`dist\RPGTabletop\`, `dist\RPGTabletopPlayer\`), puis les installeurs et `SHA256SUMS.txt` dans `dist\installers\`.

## Publier une version

La version est définie à un seul endroit : `__version__` dans `rpg_tabletop/__init__.py`.

```powershell
# après avoir passé __version__ à "0.2.0" et commité
git tag v0.2.0
git push origin v0.2.0
```

Le workflow `.github/workflows/release.yml` lance les tests, construit les installeurs sous Windows et crée la version GitHub avec les deux installeurs et `SHA256SUMS.txt`. Les applications installées la proposent à leur démarrage suivant.

Un tag avec suffixe (`v0.2.0-rc1`) est publié comme pré-version : les applications ne la proposent pas, ce qui permet de tester un build avant de le diffuser.

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
packaging/              specs PyInstaller, installeur Inno Setup, build.ps1
```

Sens des dépendances : `core` ← `assets` ← `session` ← `net` ← `ui` ← `apps`. Un module n’importe jamais une couche située à sa droite. Seul `ui` dépend de Qt, à une exception près : le rendu des cartes dans `assets/map/`. Une carte est dessinée par un seul moteur, utilisé à la fois par l'éditeur et par la compilation en tuiles.
