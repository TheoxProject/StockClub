# Audit Stock Club

Pour un audit de Stock Club, l'agent principal est l'architecte-coordinateur. Il cartographie le périmètre, répartit le travail entre exactement deux sous-agents (développeur et testeur), recoupe leurs preuves et produit la synthèse. Ne lance pas l'audit sur la seule base de ce fichier : attends une demande explicite.

Règles communes à l'équipe pendant l'audit :

- Ne corrige pas le code applicatif.
- N'accède à aucun service ni à aucune donnée de production.
- Exécute les tests uniquement dans un environnement isolé avec des données fictives.
- Ne lis pas les fichiers contenant des secrets, notamment `.env`, les clés, identifiants, jetons et sauvegardes susceptibles d'en contenir.
- Pour chaque constat, fournis une preuve vérifiable (fichier et ligne, sortie de test ou étapes de reproduction) et une classe : `confirmé`, `suspecté` ou `non testé`.
- Ne crée aucun sous-agent supplémentaire.
- Si une vérification exige de franchir une de ces limites, classe-la `non testé` et explique pourquoi.

## Git

### Autorisation et coordination

- Dépôt attendu : `https://github.com/TheoxProject/StockClub.git`.
- `main` est la version stable destinée à la production ; `v3` est la branche de développement et d'intégration. Travaille, crée les commits et pousse uniquement sur `v3` pour les missions demandées et validées, sans confirmation à chaque livraison.
- Sans ordre explicite de l'utilisateur : aucun commit sur `main`, aucun push vers `main`, aucune fusion de `v3` dans `main` et aucun déclenchement volontaire d'un déploiement de production. Ces règles remplacent les anciennes autorisations de travail et de push directement sur `main`.
- Le service Render de production doit rester associé à `main`. Préserve la compatibilité avec la procédure Render existante et ne modifie pas sa configuration. Ne présente pas l'association de branche ou l'état Render comme vérifié sans accès effectif à cet état.
- Utilise des bases et un stockage de test indépendants de la production, avec des données et identifiants fictifs ; empêche le chargement accidentel d'une configuration de production.
- Seul le coordinateur indexe, crée les commits et pousse. Les sous-agents développent et testent les fichiers attribués, sans opération Git d'écriture. Vérifie la fin de leurs écritures avant de préparer un commit.

### Identité et messages

- Auteur et committer des commits IA : `StockClub AI Agents <stockclub-agents@users.noreply.github.com>`. Cette adresse est une convention d'identification, pas un compte GitHub ni une signature certifiée.
- Applique cette identité uniquement au processus de création du commit, avec `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`, `GIT_COMMITTER_NAME` et `GIT_COMMITTER_EMAIL`. Si l'environnement du terminal est modifié temporairement, restaure-le ensuite. Ne change jamais l'identité Git dans la configuration locale ou globale de l'utilisateur.
- Ne modifie pas les identités des commits existants. Ne désactive ni les hooks ni la signature configurée pour contourner un échec. Utilise l'authentification GitHub déjà configurée pour le push.
- Titre : `[AI] type(perimetre): description concise en français`. Types : `feat`, `fix`, `test`, `docs`, `refactor`, `chore`.
- Le corps précise les changements et leur raison, les tests réellement exécutés et leurs résultats, les migrations ou conséquences de déploiement et les limites restantes. N'invente aucun résultat de test.
- Pour un message multiligne, écris un fichier temporaire et utilise `git commit --file`. Ne versionne pas ce fichier.
- Un commit par changement cohérent, avec ses tests et sa migration éventuelle. Pas de découpage par fichier, de WIP ni de commits de tentatives abandonnées. Sépare les changements indépendants. Ne réécris pas les commits existants sans demande explicite.

### Préparation et synchronisation

1. Lis les instructions du dépôt ; examine la branche, le distant, l'état initial et l'index. Explique tout écart de branche ou d'URL avant publication.
2. Au début et avant le push, effectue un fetch normal. S'il échoue, ne prétends pas connaître l'état actuel du distant. Compare `v3` et `origin/v3` et examine les commits locaux non publiés ; ne pousse pas implicitement des commits préexistants sans rapport avec la livraison.
3. Préserve les modifications et fichiers préexistants. N'attribue pas silencieusement du travail personnel à un commit IA. Si l'attribution d'un fichier mêlant travail personnel et IA est ambiguë, sépare les portions si possible ou demande une clarification ciblée.
4. Vérifie le diff final et exécute les tests pertinents. Indexe explicitement les fichiers ou portions concernés ; pas d'indexation globale sans revue. Vérifie le contenu exact de l'index avant le commit.
5. Vérifie le diff à la recherche de secrets sans afficher leurs valeurs. Un secret potentiel suspend la publication concernée ; signale seulement son emplacement et son type.

- Si `v3` existe localement, vérifie son état puis utilise-la. Si elle existe uniquement sur `origin`, crée une branche locale qui suit `origin/v3`. Si elle n'existe nulle part, crée-la depuis `main` synchronisée avec `origin/main`. Ne réinitialise jamais une branche existante pour forcer ce résultat.
- Une avance rapide est autorisée pour une synchronisation nécessaire si la branche est seulement en retard et le répertoire de travail est propre. Préserve les fichiers non suivis et les modifications locales lors d'une transition ; si elle n'est pas sûre, explique le blocage.
- En cas de divergence, d'avance du distant pendant le travail ou de risque pour les modifications locales : explique la situation et propose la suite. Pas de reset destructif, nettoyage, stash automatique, rebase ou résolution de conflits improvisée.
- Aucun push forcé, y compris `--force-with-lease`.

### Fichiers à préserver et exclusions

- Ne versionne jamais de secrets, tokens, mots de passe ou `.env` réels, bases/sauvegardes, photos/données personnelles, temporaires, caches, environnements virtuels ou artefacts de test volumineux ou inutiles.
- `.env.example` est admissible uniquement avec des valeurs fictives.
- Examine séparément `.codex/`, `AGENTS.md`, `FIABILISATION_PHASE1.md` et `audit/`. Ne les ajoute pas automatiquement et ne les supprime pas. Distingue les instructions partageables, rapports utiles et tests réutilisables des copies de sandbox, journaux et données générées.
- Aucun token dans le code, la configuration du projet ou l'URL distante ; ne demande pas de coller un token dans la conversation et n'en crée pas. Utilise l'authentification Git existante sans modifier les identifiants. En cas d'échec, indique la commande, le contexte et l'erreur exacte sans exposer de secrets ; distingue autant que possible réseau, authentification et permission d'exécution. Ne multiplie pas les tentatives identiques et ne contourne pas les restrictions.

### Publication et compte rendu

1. Après validation, crée les commits nécessaires avec l'identité IA. Vérifie leur auteur, committer, message et contenu.
2. Effectue un push normal à la fin de chaque étape validée, uniquement vers `origin/v3`. La configuration du suivi `origin/v3` est autorisée ; n'envoie pas de modification vers `main`.
3. Vérifie la branche active et son suivi, les hashes local et distant de `v3`, l'auteur, le committer, le titre et l'état local final. Si la réponse du push est interrompue ou ambiguë, consulte la référence distante avant toute nouvelle tentative.
4. Donne les hashes et titres des commits effectivement poussés, le résumé des changements/tests, le résultat du push et sa branche distante, les fichiers laissés hors commit et l'état Render : vérifié, en cours, en échec ou non vérifié.

Un push réussi ne prouve pas que Render a terminé son déploiement. Ne déclare sa réussite que si elle a réellement été vérifiée. Ces consignes partageables peuvent faire l'objet d'un commit dédié après contrôle d'absence de secrets, sans embarquer de changements applicatifs préexistants non validés.
