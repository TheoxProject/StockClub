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
- `main` est la version stable destinée à la production ; `v3` est la branche de développement et d'intégration. Continue le développement, les tests et les commits locaux sur `v3` pour les missions demandées et validées, sans confirmation à chaque livraison.
- L'utilisateur prend en charge la synchronisation et toutes les opérations réseau Git. Les agents n'effectuent aucun fetch, pull, push, ls-remote ou autre accès réseau Git, et ne poursuivent aucun diagnostic d'authentification. L'absence d'accès GitHub ne bloque pas le travail local.
- Aucun commit sur `main`, aucune fusion vers `main` et aucun déploiement. Ces règles remplacent les anciennes autorisations de synchronisation ou de publication par les agents.
- Le service Render de production doit rester associé à `main`. Préserve la compatibilité avec la procédure Render existante et ne modifie pas sa configuration. Ne présente pas l'association de branche ou l'état Render comme vérifié sans accès effectif à cet état.
- Utilise des bases et un stockage de test indépendants de la production, avec des données et identifiants fictifs ; empêche le chargement accidentel d'une configuration de production.
- Seul le coordinateur indexe et crée les commits locaux. Les sous-agents développent et testent les fichiers attribués, sans opération Git d'écriture. Vérifie la fin de leurs écritures avant de préparer un commit. Le push relève uniquement de l'utilisateur.

### Identité et messages

- Auteur et committer des commits IA : `StockClub AI Agents <stockclub-agents@users.noreply.github.com>`. Cette adresse est une convention d'identification, pas un compte GitHub ni une signature certifiée.
- Applique cette identité uniquement au processus de création du commit, avec `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`, `GIT_COMMITTER_NAME` et `GIT_COMMITTER_EMAIL`. Si l'environnement du terminal est modifié temporairement, restaure-le ensuite. Ne change jamais l'identité Git dans la configuration locale ou globale de l'utilisateur.
- Ne modifie pas les identités des commits existants. Ne désactive ni les hooks ni la signature configurée pour contourner un échec. Ne modifie pas les identifiants GitHub de l'utilisateur.
- Titre : `[AI] type(perimetre): description concise en français`. Types : `feat`, `fix`, `test`, `docs`, `refactor`, `chore`.
- Le corps précise les changements et leur raison, les tests réellement exécutés et leurs résultats, les migrations ou conséquences de déploiement et les limites restantes. N'invente aucun résultat de test.
- Pour un message multiligne, écris un fichier temporaire et utilise `git commit --file`. Ne versionne pas ce fichier.
- Un commit par changement cohérent, avec ses tests et sa migration éventuelle. Pas de découpage par fichier, de WIP ni de commits de tentatives abandonnées. Sépare les changements indépendants. Ne réécris pas les commits existants sans demande explicite.

### Préparation locale

1. Lis les instructions du dépôt ; examine la branche active, l'état local initial et l'index. Travaille sur `v3` et explique tout écart avant une opération Git d'écriture.
2. Vérifie les commits et modifications locaux pertinents sans accès réseau. Les références distantes locales, telles que `origin/v3`, sont des informations en cache : ne les présente jamais comme une confirmation de l'état actuel de GitHub. L'utilisateur vérifie la synchronisation et effectue le push.
3. Préserve les modifications et fichiers préexistants. N'attribue pas silencieusement du travail personnel à un commit IA. Si l'attribution d'un fichier mêlant travail personnel et IA est ambiguë, sépare les portions si possible ou demande une clarification ciblée.
4. Vérifie le diff final et exécute les tests pertinents. Indexe explicitement les fichiers ou portions concernés ; pas d'indexation globale sans revue. Vérifie le contenu exact de l'index avant le commit.
5. Vérifie le diff à la recherche de secrets sans afficher leurs valeurs. Un secret potentiel suspend l'indexation ou le commit concerné ; signale seulement son emplacement et son type.

- Utilise la branche locale `v3` après contrôle de son état. Si elle est absente ou si une transition menace le travail local, préserve les fichiers et explique la préparation locale nécessaire à l'utilisateur, sans tenter de synchronisation réseau ni réinitialiser une branche existante.
- Préserve les fichiers non suivis et les modifications locales. Toute divergence supposée à partir de références en cache reste à vérifier par l'utilisateur ; elle ne constitue pas une preuve de l'état de GitHub et n'empêche pas les travaux locaux indépendants.
- En cas de conflit local ou de risque pour les modifications existantes : explique la situation et propose la suite. Pas de reset destructif, nettoyage, stash automatique, rebase ou résolution de conflits improvisée.
- Aucun push forcé, y compris `--force-with-lease`.

### Fichiers à préserver et exclusions

- Ne versionne jamais de secrets, tokens, mots de passe ou `.env` réels, bases/sauvegardes, photos/données personnelles, temporaires, caches, environnements virtuels ou artefacts de test volumineux ou inutiles.
- `.env.example` est admissible uniquement avec des valeurs fictives.
- Examine séparément `.codex/`, `AGENTS.md`, `FIABILISATION_PHASE1.md` et `audit/`. Ne les ajoute pas automatiquement et ne les supprime pas. Distingue les instructions partageables, rapports utiles et tests réutilisables des copies de sandbox, journaux et données générées.
- Aucun token dans le code, la configuration du projet ou l'URL distante ; ne demande pas de coller un token dans la conversation et n'en crée pas. Aucun diagnostic d'authentification ni modification des identifiants. En cas d'échec d'une opération locale, indique la commande, le contexte et l'erreur exacte sans exposer de secrets ; ne contourne pas les restrictions et ne multiplie pas les tentatives identiques.

### Livraison locale et compte rendu

1. Après validation, crée les commits locaux nécessaires sur `v3` avec l'identité IA. Vérifie leur auteur, committer, message et contenu.
2. Vérifie la branche active, les hashes locaux et l'état local final. Ne lance aucune opération réseau et ne prétends pas avoir confirmé l'état de GitHub.
3. À chaque livraison, donne les hashes et titres des commits créés, les changements et tests réellement exécutés, les fichiers laissés non commités et les limites restantes.
4. L'utilisateur vérifie la synchronisation et effectue lui-même le push. Ne présente pas son exécution ou un déploiement comme effectué par les agents.

La livraison locale ne prouve aucun état de déploiement Render ; aucun déploiement n'est autorisé dans ce workflow. Ces consignes partageables peuvent faire l'objet d'un commit local dédié après contrôle d'absence de secrets, sans embarquer de changements applicatifs préexistants non validés.
