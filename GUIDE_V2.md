# Stock Club V2 — installation et déploiement

Cette archive part de Stock Club 1.4.3. Elle conserve les fonctionnalités existantes (photos mobile, invitations à usage unique, recherche et filtres, emprunts/retours, suppression d'objet réservée à l'administrateur) et n'ajoute que les deux modifications retenues pour V2 dans « Comptes et sécurité » :

1. Lorsque la table des utilisateurs est vide, la page de connexion propose de créer le premier compte administrateur. Cette création est atomique, ouvre ensuite la session et n'est plus accessible une fois un utilisateur créé.
2. Le dernier administrateur actif ne peut pas être rétrogradé. Les changements de rôle verrouillent les lignes des administrateurs actifs pour éviter qu'une paire de changements simultanés ne supprime tous les administrateurs.

Le mot de passe reste à 6 caractères minimum, conformément à la version précédente. Les autres fonctionnalités ne sont pas modifiées.

## Mettre à jour un dépôt local existant

1. Fais une sauvegarde de la base PostgreSQL Neon, si possible.
2. Décompresse le contenu de cette archive à la racine du dossier local qui contient `run.py` et le dossier `.git`. Accepte le remplacement des fichiers du projet.
3. **Ne remplace pas et ne publie pas `.env`.** L'archive contient uniquement `.env.example`, pas les secrets de ton environnement ni tes photos.
4. Vérifie que le code local est configuré sur la base de développement ou de test voulue avant d'exécuter des migrations. Ne vide pas la base Neon de production pour tester le premier lancement.

## Tester en local

Active ton environnement virtuel dans PowerShell, puis lance :

```powershell
python -m flask --app run.py db upgrade
python -m pytest -q
```

La migration `0005_admin_bootstrap` ajoute une seule ligne de verrouillage technique. Elle ne modifie ni les utilisateurs existants, ni les objets, ni les photos, ni les emprunts.

Pour tester la première initialisation, utilise une base SQLite de développement vierge (ou un environnement Neon de test). Sur la page de connexion, le lien « Créer le premier compte administrateur » doit apparaître. Crée le compte puis vérifie qu'il a le rôle Administrateur et que le lien disparaît. Ne supprime aucun utilisateur de la base de production pour effectuer ce test.

## Publier sur GitHub

Avant l'envoi, regarde `git status --short` et assure-toi que `.env`, les fichiers SQLite, `instance/`, les photos et les secrets n'apparaissent pas. Sélectionne les fichiers attendus explicitement :

```powershell
git add README.md DEPLOYMENT.md GUIDE_V2.md RELEASE_NOTES_V2.md app/__init__.py app/admin/routes.py app/auth/routes.py app/models.py app/templates/auth/login.html app/templates/auth/first_admin.html migrations/versions/0005_admin_bootstrap.py tests/conftest.py tests/test_bootstrap.py
git diff --cached --check
git diff --cached --name-only
git commit -m "Add safe first admin setup for Stock Club V2"
git push
```

Vérifie la liste renvoyée par `git diff --cached --name-only` avant le commit. Si d'autres fichiers apparaissent, vérifie-les individuellement.

## Déployer sur Render

Une fois le commit poussé, Render redéploie automatiquement la branche configurée. Le `startCommand` existant lance `python -m flask --app run.py db upgrade` avant Gunicorn et appliquera la migration `0005_admin_bootstrap`.

- Base de production déjà remplie : le lien d'initialisation n'apparaît pas et les comptes existants restent en place.
- Base réellement vide : le premier compte peut être créé depuis la page de connexion.
- Après le déploiement, vérifie les logs Render et attends le statut **Live** avant de tester les fonctions.

Ne crée pas une deuxième base de production et ne supprime pas les données existantes pour tester la procédure d'initialisation.


## Correctif de prise de photo mobile (V2.1)
Le formulaire conserve les deux boutons **Prendre une photo** et **Choisir depuis la galerie**. Les champs caméra et galerie ont des noms fixes ; le serveur reconnaît les deux, sans dépendre du changement dynamique de nom par JavaScript. Le formulaire vérifie aussi la sélection au retour de la caméra. Après déploiement, teste deux ajouts successifs avec la caméra et un ajout depuis la galerie. Si le navigateur recharge complètement la page au retour de la caméra et vide le champ, cette version ne peut pas récupérer un fichier que le navigateur n'a pas conservé ; dans ce cas, le modèle du téléphone et le navigateur seront à vérifier.

### Publier uniquement le correctif photo sur un dépôt V2 existant

Si tu gardes ton dépôt local déjà configuré, tu peux appliquer les fichiers du ZIP `stock_club_v2_photo_patch.zip` à la racine du dépôt (sans remplacer `.env`), puis exécuter :

```powershell
git add app/templates/inventory/item_form.html app/inventory/routes.py tests/test_app.py RELEASE_NOTES_V2.md GUIDE_V2.md
git diff --cached --check
git diff --cached --name-only
git commit -m "Fix mobile camera photo selection"
git push
```

Vérifie la liste de fichiers affichée avant de committer. Render redéploiera ensuite le commit si le dépôt et la branche sont déjà reliés au service.
