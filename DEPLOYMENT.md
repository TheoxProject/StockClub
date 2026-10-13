# Déploiement Stock Club v1.3 (Render + Neon + Cloudflare R2)

Ce guide déploie l'application Flask sur Render, PostgreSQL sur Neon et les photos sur Cloudflare R2.

## Important avant de déployer

La base SQLite utilisée en local et les photos du dossier `instance/uploads/` ne sont **pas copiées automatiquement** vers Neon/R2. Si tu as déjà créé des données réelles à conserver, arrête-toi avant l'étape Neon et prévois leur migration. Pour un démarrage vierge, continue ci-dessous.

Le plan Render gratuit sert à valider le déploiement : il se met en veille après 15 minutes d'inactivité et le réveil peut prendre environ une minute. Son disque est éphémère ; les données doivent donc rester dans Neon et R2. Pour un usage associatif opérationnel sans attente au premier accès, envisager une instance web payante. Voir https://render.com/docs/free.

## 1. Préparer le dépôt GitHub

1. Si tu veux mettre le projet en avant sur ton profil, crée un dépôt GitHub **public** nommé `stock-club`, mais uniquement après avoir suivi la checklist de publication et vérifié les fichiers que Git va envoyer. Le code source est public ; les identifiants et les données du club ne le sont jamais.
2. Envoie les fichiers du projet, y compris `.gitignore`, `.env.example` et `.python-version`. Ne publie jamais `.env`, la base SQLite, les photos, les sauvegardes ni des identifiants réels.
3. Vérifie que `render.yaml`, `requirements.txt`, `run.py` et `migrations/` sont à la racine du dépôt.

Avec Git déjà installé, PowerShell ouvert dans ce dossier :

```powershell
git init
git add .
git status --short
git diff --cached --name-only
# Vérifie la liste ci-dessus avant de committer.
git commit -m "Stock Club v1.3 - deployment"
git branch -M main
git remote add origin https://github.com/TON_COMPTE/stock-club.git
git push -u origin main
```

Avant `git push`, vérifie une nouvelle fois que le fichier `.env`, `instance/`, les bases SQLite, les photos et tous les secrets sont absents du commit. `.gitignore` ne retire pas les fichiers déjà suivis par Git. Si un secret a été publié, révoque-le/renouvelle-le immédiatement ; supprimer le fichier ne suffit pas à effacer l'historique.

Si Git te dit que le dépôt distant existe déjà, ne répète pas `git remote add origin`; utilise `git remote -v` pour contrôler l'adresse.

## 2. Créer la base PostgreSQL Neon

1. Crée un projet Neon dans une région européenne.
2. Dans **Connect**, sélectionne le projet, la branche, la base et le rôle, puis copie la chaîne de connexion PostgreSQL. La chaîne doit inclure `sslmode=require`.
3. Garde cette chaîne privée. Ne l'envoie pas dans un chat et ne la committe pas dans Git.

### Initialiser le schéma et créer le premier compte

Dans PowerShell, depuis ce dossier, crée et active un environnement Python si ce n'est pas déjà fait, puis installe les dépendances :

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Ensuite remplace la valeur entre apostrophes par la chaîne Neon copiée. Cette valeur est un marqueur : remplace-la par la chaîne privée copiée depuis Neon avant d'exécuter les commandes.

```powershell
$env:DATABASE_URL = '<PASTE_THE_PRIVATE_NEON_CONNECTION_STRING_HERE>'
$env:FLASK_DEBUG = '1'
python -m flask --app run.py db upgrade
python -m flask --app run.py create-user
Remove-Item Env:DATABASE_URL
Remove-Item Env:FLASK_DEBUG
```

`db upgrade` crée les tables dans Neon et `create-user` ajoute ton compte. Répète `create-user` pour chaque bénévole. Fais bien cette étape après avoir vérifié que `DATABASE_URL` cible Neon, pas le fichier SQLite local.

## 3. Créer le bucket de photos Cloudflare R2

1. Dans Cloudflare Dashboard, ouvre **Storage & databases → R2 Object Storage** et crée un bucket privé, par exemple `club-stock-photos`.
2. Pour les données du club, préfère l'option de juridiction EU si elle est disponible pour ton compte/bucket.
3. Dans R2, crée un token **Object Read & Write** limité à ce bucket.
4. Copie l'Access Key ID, le Secret Access Key et l'endpoint S3 affiché par Cloudflare. Pour un bucket standard, il ressemble à `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`. **Si tu as créé le bucket avec la juridiction EU, l'endpoint doit être `https://<ACCOUNT_ID>.eu.r2.cloudflarestorage.com`**. Le secret ne pourra généralement plus être relu après fermeture : conserve-le dans un gestionnaire de mots de passe.
5. Ne configure pas `S3_PUBLIC_BASE_URL` : l'application générera des URL signées temporaires pour les photos privées.

Documentation officielle : https://developers.cloudflare.com/r2/get-started/s3/

## 4. Créer le service Render

1. Ouvre https://dashboard.render.com/ et connecte le dépôt GitHub privé.
2. Choisis **New → Blueprint** si tu veux utiliser `render.yaml`, puis sélectionne le dépôt `stock-club`. Sinon crée un **Web Service** et configure manuellement les champs ci-dessous.
3. Si Render demande les valeurs marquées `sync: false`, renseigne les valeurs ci-dessous.

| Clé | Valeur |
|---|---|
| `DATABASE_URL` | Chaîne de connexion Neon, avec `sslmode=require` |
| `S3_ENDPOINT_URL` | Endpoint S3 de ton compte R2 |
| `S3_BUCKET` | `club-stock-photos` (ou le nom choisi) |
| `S3_ACCESS_KEY_ID` | Access Key ID de R2 |
| `S3_SECRET_ACCESS_KEY` | Secret Access Key de R2 |

Les autres réglages importants sont déjà dans `render.yaml` : `SECRET_KEY` est générée par Render, `COOKIE_SECURE=1`, `TRUST_PROXY=1`, `REQUIRE_OBJECT_STORAGE=1` et le service écoute le port Render.

Réglages manuels si tu n'utilises pas Blueprint :

- Runtime : Python 3
- Region : Frankfurt (si disponible, proche de Neon EU)
- Build command : `pip install -r requirements.txt`
- Start command : `gunicorn --bind 0.0.0.0:$PORT run:app`
- Health check path : `/health`
- Python version : `3.12.11`
- Variables d'environnement : celles du tableau ci-dessus, plus `SECRET_KEY` (une valeur aléatoire d'au moins 32 caractères), `S3_REGION=auto`, `REQUIRE_OBJECT_STORAGE=1`, `COOKIE_SECURE=1`, `TRUST_PROXY=1`, `FLASK_DEBUG=0`.

La base Neon doit avoir été initialisée à l'étape 2 avant le premier déploiement. Les migrations ne sont pas lancées automatiquement au démarrage pour éviter une migration implicite à chaque redémarrage.

## 5. Tester le déploiement

Quand Render affiche **Live**, ouvre l'URL `https://...onrender.com` fournie par Render.

1. Connecte-toi avec le login et le mot de passe créés à l'étape 2.
2. Crée une catégorie, une sous-catégorie et une fiche de test avec une photo.
3. Ouvre `/health` : le résultat attendu est `{"status":"ok"}`.
4. Déconnecte-toi puis reconnecte-toi. Teste le même objet depuis un second téléphone.
5. Dans Cloudflare R2, vérifie qu'un fichier `.webp` est apparu dans le bucket.
6. Redéploie une fois, puis vérifie que les données et la photo sont toujours là. Cette vérification confirme qu'elles ne sont pas dépendantes du disque éphémère Render.

## 6. Mises à jour suivantes

Une fois GitHub connecté à Render, un `git push` sur la branche déployée déclenche automatiquement un déploiement. Pour toute nouvelle modification du modèle SQLAlchemy, créer et relire une migration Alembic en développement, committe-la avec le code, puis applique-la à Neon avec `python -m flask --app run.py db upgrade` en ciblant explicitement Neon. Fais une sauvegarde avant toute migration touchant des données.

## Références officielles

- Render Flask : https://render.com/docs/deploy-flask
- Render Python : https://render.com/docs/python-version
- Render services gratuits : https://render.com/docs/free
- Neon : https://neon.com/docs/get-started/connect-neon
- Cloudflare R2 S3 : https://developers.cloudflare.com/r2/get-started/s3/
