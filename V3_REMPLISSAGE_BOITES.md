# V3 — création et remplissage visuel des boîtes

Livraison locale du 1er novembre 2026 pour revue et essais, sur `v3`. Aucun réseau Git, accès à la production, modification de `main` ni déploiement. Les déplacements, emprunts, retours, ajustements et pertes/casses existants sont préservés.

## Parcours utilisateur

La création propose **Créer et ajouter du matériel** (ouvre la sélection) et **Créer une boîte vide** (ouvre la fiche). Dans les deux cas la boîte existe immédiatement, vide. Quitter la sélection n'affecte aucun matériel. Une boîte existante dispose du même bouton **Ajouter du matériel**.

L'écran nomme la destination et propose une recherche progressive par nom, un filtre catégorie incluant ses descendants et des pages de 24 objets. Seuls les objets ayant du disponible **hors boîte** sont proposés, même s'ils sont déjà présents dans la destination. Les photos existantes sont affichées en petites vignettes chargées à la demande (`loading=lazy`), ou remplacées par un visuel neutre ; aucun nouveau service photo n'est introduit.

Cocher initialise à 1. Les commandes −/+, saisie directe et **Tout le disponible** modifient une quantité à **ajouter**, jamais le total final de la boîte. Décocher retire la ligne. Une valeur incorrecte n'est pas silencieusement réduite ou remplacée.

La sélection est une `Map` JavaScript indexée par identifiant, indépendante des lignes affichées. Recherche, catégorie, pagination et actualisation des résultats conservent les quantités saisies. **Sélectionner les résultats affichés** ne concerne que la page visible, conserve les quantités déjà retenues et les sélections masquées. **Vider la sélection** retire tout, y compris les objets masqués.

La barre fixe affiche références et unités pour toute la sélection, **Voir la sélection** et **Ajouter à la boîte**. Le récapitulatif permet de modifier et retirer des lignes, puis de revenir aux résultats. Aucune nouvelle confirmation systématique n'est demandée. La validation est désactivée si la sélection est vide ou invalide.

Les libellés associent cases/champs aux objets, les actions sont de vrais boutons, les commandes tactiles mesurent au moins 44 px. La hauteur de la barre est mesurée pour réserver de l'espace dans le contenu ; le changement de `visualViewport` remonte la barre et maintient le champ actif visible lors d'une réduction de viewport. Les réponses de recherche obsolètes sont ignorées ; une recherche échouée laisse les résultats et la sélection en place. Aucune persistance après fermeture d'onglet ou rechargement complet n'est promise.

## Validation, erreurs et invariants

Le POST unique vérifie administrateur/activité après le verrou singleton existant, CSRF, boîte active, identifiants distincts et valides, objets existants, quantités entières de 1 à 1 000 000 et disponible hors boîte courant. Toutes les lignes sont validées avant la première affectation. Les contenus, événements de composition et reçu technique sont enregistrés dans **une seule transaction**, ou entièrement annulés.

Pour chaque objet : le présent de la destination augmente et le disponible hors boîte diminue de la même quantité. Le disponible global (`Item.quantity`), le stock total et les emprunts ne changent pas. Exemple : 4 cônes présents + 6 hors boîte, ajout de 2 → 6 présents et 4 hors boîte, sans création de stock.

Chaque affectation garde les mécanismes existants : objet et nom figé, quantité, provenance « Hors boîte », destination et nom figé, date, véritable administrateur. Aucun faux emprunt, retour ou mouvement de stock n'est ajouté.

Si une ligne devient excessive, indisponible ou supprimée, le serveur refuse tout le lot. La réponse conserve les quantités demandées et actualise les disponibilités, avec un message par ligne tel que **4 demandés, seulement 2 disponibles**. Le récapitulatif est ouvert et conserve aussi les lignes masquées ou supprimées avec leur nom mémorisé. Ce nom fourni par le client sert uniquement à l'affichage d'une ligne disparue ; il ne remplace jamais les données fiables de l'historique. Aucun nouvel enregistrement n'est automatiquement tenté après une erreur réseau.

## Protection serveur contre les doublons

Aucun mécanisme adapté n'existait. Désactiver le bouton pendant la requête évite les doubles clics courants, mais la garantie repose sur un **reçu durable en base**, `BoxFillReceipt`.

Un jeton signé, valable 24 heures, lie un UUID aléatoire à l'utilisateur et à la boîte. Il s'ajoute à CSRF. Après validation, le reçu conserve l'empreinte canonique des couples objet/quantité et les comptes du lot. Il est créé dans la même transaction que les affectations. Une opération refusée ne consomme pas le jeton.

- Même jeton et mêmes quantités : retour du succès initial **sans nouvelle affectation**, même après changement de disponibilité, nouvelle session ou archivage de la boîte.
- Même jeton avec une autre sélection : refus 409. L'utilisateur doit consulter la boîte avant une nouvelle demande ; aucun jeton neuf n'est fourni automatiquement pour contourner ce contrôle.
- Erreur réseau au résultat incertain : une reprise explicite avec la même sélection utilise le même jeton. Aucun cache propre à un processus ni cookie de session ne constitue la preuve de consommation.

Le reçu survit aux redémarrages et aux travailleurs multiples. Il ne contient ni mot de passe, ni photo, ni nom d'objet client. Les totaux d'unités utilisent un entier 64 bits pour les lots multi-objets. Les reçus ne sont pas purgés automatiquement.

## Migration et compatibilité

**0008_box_fill_receipts**, après **0007_stock_boxes**, crée uniquement la table technique des reçus. Cette migration est nécessaire pour la protection transactionnelle durable contre les doubles soumissions ; elle ne transforme aucune quantité, boîte, affectation ou histoire existante.

Pour une future mise en service autorisée par l'utilisateur : sauvegarder et vérifier la restauration, essayer une copie indépendante, puis appliquer `python -m flask --app run.py db upgrade` avant de servir le nouveau code. Render, ses variables et sa commande de démarrage sont préservés ; aucun déploiement n'est effectué ici.

Le downgrade vers 0007 est possible tant qu'aucun reçu n'existe. Il est refusé après un ajout groupé réussi pour préserver la protection contre les reprises. Ne pas effacer les reçus pour contourner cette garde ; privilégier une correction en avant, ou une restauration coordonnée tenant compte des opérations intervenues depuis la sauvegarde.

Les routes classiques de transfert restent disponibles et ne sont pas refondues. Une création API sans `next_action` conserve la redirection historique vers la fiche. Aucun ajout multiple depuis l'inventaire général n'est introduit. Exports, caméra et stockage photo restent inchangés. Le nouveau parcours nécessite JavaScript, signalé en son absence.

## Validation

Les seules données utilisées sont fictives, dans des bases/instances/profils indépendants ; dotenv et l'instance d'origine sont refusés. Toutes les suites passent par `python -I tools/run_isolated_tests.py`. L'instance PostgreSQL dédiée a été arrêtée après validation.

| Vérification réellement exécutée | Résultat | Preuve locale ignorée |
| --- | --- | --- |
| Suite complète SQLite : `tests -q` | **241 réussis** | `.test-artifacts/run_2e85fc9459e3/results.xml` |
| Suite complète PostgreSQL : `--engine postgresql tests -q --tb=short` | **241 réussis** | `.test-artifacts/run_a09d8a6782dd/results.xml` |
| Migration/démarrage/entrée `run:app`/health, base vierge et peuplée au schéma courant, PostgreSQL | **2 réussis** | `.test-artifacts/run_bf436494007e/results.xml` |
| Même contrôle de compatibilité, SQLite | **2 réussis** | `.test-artifacts/run_5d1f3342bedc/results.xml` |
| Navigateur indépendant Edge 156, profil neuf, SQLite dédiée, CSRF actif | **Parcours final réussi**, aucune erreur JavaScript | `.test-artifacts/browser_fill_4cd42b2c03/result.json` et captures |

Les 241 cas regroupent les 202 cas antérieurs et **39 nouveaux** : 38 métier/API/sécurité/concurrence et une migration réelle depuis 0007 peuplée. Ils couvrent les deux créations, l'ajout à un contenu existant (4 + 2 = 6), les filtres descendants et pages disjointes, les droits directs et CSRF réel, les identifiants/quantités/jetons invalides, la restitution des lignes périmées ou supprimées et l'absence de mutation partielle.

Le rollback est injecté après une première affectation réellement écrite en SQL. Les courses utilisent des connexions indépendantes et une synchronisation maîtrisée : ajout contre emprunt hors boîte ou autre affectation, dans les deux ordres, double soumission du même jeton et révocation des droits pendant attente. Les reçus, contenus et historiques sont comparés. Le rejeu avec une nouvelle session, après épuisement du disponible ou archivage de la destination, ne crée aucune nouvelle affectation ; un contenu modifié avec le même jeton est refusé.

Le navigateur vérifie réellement la conservation des sélections entre recherches, catégories, pages et actualisation, les commandes de quantité, la sélection des seuls résultats affichés, le vidage, les totaux masqués, l'édition/retrait depuis le récapitulatif et les créations avant affectation. Une erreur de recherche réseau et des réponses volontairement inversées sont injectées. Un stock devenu insuffisant et un objet supprimé restent visibles dans le récapitulatif jusqu'à correction/retrait, sans affectation partielle.

Les positions sont vérifiées aux largeurs 390 et 1280 : dernière carte au-dessus de la barre, barre alignée au bas du viewport de bureau. Une réduction simulée de `visualViewport` à 440 px vérifie que la barre et le champ actif restent visibles. Le double clic n'émet aucune requête supplémentaire ; les deux POST observés (lot refusé puis corrigé) utilisent réellement `application/x-www-form-urlencoded`. La protection serveur est aussi testée indépendamment avec deux requêtes concurrentes.

Étapes transitoires conservées : ciblés SQLite 38 réussites et un échec d'oracle (`run_e8ed21b1297e`) ; Flask-Migrate transformait le refus attendu de downgrade en `SystemExit`. Le test utilise désormais Alembic comme les tests existants : reprise migration seule réussie (`run_f1c6e4a54fd7`), puis les deux suites intégrales vertes ci-dessus. Les premières passes navigateur `browser_fill_b83519474f` et `browser_fill_2b409623fe` sont réussies ; la dernière vérifie aussi le transport URL encoded, choisi pour éviter le plafond multipart de 1 000 champs avec plusieurs pages sélectionnées. Les assertions métier antérieures restent inchangées ; seul le head attendu du test de migration existant est actualisé à 0008. Les avertissements restants concernent Alembic `path_separator` : 21 par suite complète, 4 par contrôle de démarrage.

Pour reproduire dans les environnements de test autorisés :

```powershell
python -I tools/run_isolated_tests.py tests -q
python -I tools/run_isolated_tests.py --engine postgresql tests -q
python -I tools/run_isolated_tests.py tools/check_deployment.py -q
python -I tools/run_isolated_tests.py --engine postgresql tools/check_deployment.py -q
python -I tools/check_box_fill_browser.py
```

Le runner PostgreSQL exige la configuration locale marquée pour les tests et refuse une cible non autorisée. Le script navigateur exige Playwright dans un environnement de développement isolé ; il utilise Edge sur Windows avec un profil neuf. Sa première tentative sandbox a été refusée à la lecture d'une bibliothèque locale ; les relances autorisées conservent l'isolation interne et ne lisent aucune configuration de production.

## Équipe et limites

Développeur `gpt-6.1-terra` medium et testeur indépendant `gpt-6.1-sol` medium : paramètres acceptés à leur création. Le coordinateur réalise l'interface, la synthèse et les commits dans la conversation principale ; Sol/medium est la configuration demandée, sans introspection du modèle effectivement servi. Aucun autre sous-agent, aucune escalade high, aucune consommation ou quota estimé.

Le navigateur de bureau avec émulation mobile et simulation de `visualViewport` ne remplace pas un **téléphone et son clavier réel**. Ces essais, le comportement PWA réel, une charge prolongée et les services de production restent hors validation. Le rapport indépendant et l'état de reprise restent dans `audit/box_fill_2026-11-01/`, hors commit ; les profils, bases et captures sont ignorés dans `.test-artifacts/`.
