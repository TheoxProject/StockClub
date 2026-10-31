# V3 — ajustements administrateur et pertes sur emprunt

Livraison locale pour revue et essais, le 31 octobre 2026. Aucun accès à la production, aucune opération réseau Git et aucun déploiement. La configuration Render, le démarrage, les variables d'environnement, le stockage photo et le parcours caméra sont préservés.

## Comportement

`Item.quantity` reste le **disponible**. Le **sorti** est la somme des quantités initialement empruntées moins les retours et les pertes/casses. Le **stock total** est calculé : disponible + sorti ; aucun compteur total redondant n'est ajouté.

| Action administrateur | Disponible | Sorti | Stock total |
| --- | --- | --- | --- |
| Ajout de n unités | +n | inchangé | +n |
| Retrait définitif de n unités disponibles | −n | inchangé | −n |
| Perte/casse de n unités empruntées | inchangé | −n | −n |

Depuis 10 au total / 7 disponibles / 3 sortis, retirer 1 disponible donne **9 / 6 / 3** ; déclarer 1 perte sur l'emprunt donne **9 / 7 / 2**.

**Ajuster le stock**, sur la fiche, accepte un ajout (achat, don, correction d'inventaire, autre) ou un retrait (perte, casse, correction d'inventaire, autre). La quantité est un entier positif ; la précision est obligatoire pour « Autre » et limitée à 240 caractères. Un retrait ne dépasse pas le disponible. Un ajout respecte le plafond de 1 000 000 pour le total, emprunts inclus.

**Déclarer une perte ou une casse**, sur un emprunt ouvert, ne modifie ni le disponible ni la quantité initialement empruntée. La quantité ne dépasse pas le restant à rendre ; le motif est perte ou casse, la précision facultative. Un emprunt est soldé quand :

`quantité initiale = quantité retournée + quantité perdue/cassée + restant à rendre`.

Un solde incluant une perte affiche « Soldé avec perte/casse ». Il quitte les emprunts ouverts sans être présenté comme « Tout retourné ». Une fiche à zéro reste consultable et réapprovisionnable. La suppression manuelle reste interdite tant que du matériel est sorti ; les historiques et noms figés sont conservés après suppression autorisée.

Les fiches, listes, historiques et compteurs distinguent les trois quantités. Le CSV conserve l'ordre des huit colonnes existantes, renomme la colonne de quantité en « Disponible » et ajoute « Stock total » et « Sorti ». Les consommateurs exigeant un nombre exact de colonnes doivent être adaptés.

## Cohérence et historique

Les actions sont réservées aux administrateurs côté serveur, protégées par CSRF et masquées aux membres. Le verrou transactionnel partagé existant est réutilisé ; les droits et quantités sont relus après son acquisition. Quantités, clôture, événements et mouvements sont enregistrés ensemble, avec rollback en cas d'échec. L'aperçu JavaScript est indicatif : une demande devenue excessive est refusée avec les données courantes.

`Borrowing.quantity_lost` cumule pertes et casses. Les événements conservent leur distinction. Les mouvements portent type, motif, précision, acteur, date, lien d'emprunt et nom figé. `delta` et `quantity_after` restent l'effet et le solde du **disponible** ; `total_delta` porte l'effet sur le **total**. Une perte sur emprunt crée un événement et un mouvement liés : ce sont deux vues d'une seule opération, pas deux retraits. Les totaux sont calculés à partir des fiches et emprunts, sans additionner ces deux historiques.

## Migration et mise en service ultérieure

Migration **0006_stock_adjustments**, depuis **0005_admin_bootstrap** : ajout du compteur de pertes et des métadonnées de mouvement. Les anciens compteurs sont initialisés à zéro ; les anciens mouvements restent `legacy`, avec effet total inconnu (`NULL`). Aucun ancien retour n'est requalifié, aucune perte n'est inventée.

Pour une future mise en service autorisée par l'utilisateur : sauvegarder et vérifier la restauration de la base, essayer la migration sur une copie indépendante, puis utiliser la commande existante `python -m flask --app run.py db upgrade` dans l'environnement prévu. Ne pas servir le nouveau code avant la migration. Le mécanisme Render existant applique déjà les migrations ; sa configuration n'a pas été modifiée. Contrôler ensuite `/health` et les parcours administrateur/membre. Ces opérations ne sont pas effectuées par cette livraison.

Un downgrade vers 0005 est testé et possible **avant** tout nouvel ajustement ou perte/casse. Il est explicitement refusé après de telles opérations pour éviter la perte de leur signification historique. Après utilisation, privilégier une correction en avant ; une restauration d'une sauvegarde antérieure implique de traiter les opérations intervenues depuis cette sauvegarde. Ne pas contourner la garde ni supprimer les lignes d'historique pour forcer un downgrade.

## Tests réellement exécutés

Les commandes passent par `python -I tools/run_isolated_tests.py`, qui désactive dotenv et isole bases, instance et fichiers. PostgreSQL 17.11 est une instance locale dédiée avec données fictives ; SQLite utilise des bases temporaires indépendantes.

| Validation | Résultat | Preuve locale ignorée par Git |
| --- | --- | --- |
| Suite complète PostgreSQL : `--engine postgresql tests -q --tb=short` | **159 réussis** | `.test-artifacts/run_a9dbc067133e/results.xml` |
| Suite complète SQLite : `tests -q --tb=short` | 158 réussis, 1 échec sur un libellé historique | `.test-artifacts/run_3a57263e6d74/results.xml` |
| SQLite, reprise du seul test concerné après correction du libellé | **1 réussi**, soit **159 cas distincts validés** au total ; pas une nouvelle passe complète | `.test-artifacts/run_91b7a52c5557/results.xml` |
| Démarrage/migrations/entrée `run:app`/health, SQLite, base vierge puis peuplée au schéma courant | **2 cas distincts réussis**, sur deux exécutions | `.test-artifacts/run_8161f45ef9e6/results.xml`, `run_148eb375a052/results.xml` |
| Même contrôle de compatibilité, PostgreSQL | **2 cas distincts réussis**, sur deux exécutions | `.test-artifacts/run_aebbd5a2a190/results.xml`, `run_05b8cc0f0be5/results.xml` |

Les 159 tests comprennent 97 tests existants inchangés et 62 nouveaux : 60 cas fonctionnels/concurrence, 2 migrations. Sur les deux moteurs sont vérifiés les exemples métier, motifs/bornes, zéro/réapprovisionnement, pertes et retours partiels/combinés, historique et attribution, droits directs et masquage, CSRF activé, révocation du rôle pendant attente, rollback après flush et suppression avec conservation de l'historique.

Les six courses utilisent des connexions indépendantes et des Events de synchronisation : retrait/emprunt, deux retraits, ajout/retour à la borne totale, perte/retour, deux pertes, ajustement/suppression. Les migrations couvrent une base vierge et un vrai schéma **0005 peuplé avec ses seuls anciens champs**, la conservation exacte des données et les deux comportements de downgrade.

Les premiers contrôles de démarrage exécutés parallèlement au navigateur ont rencontré des erreurs d'allocation mémoire (scrypt côté SQLite, PostgreSQL côté nettoyage). Seuls les deux cas concernés ont été rejoués, séparément, avec succès, sans modifier le hachage ni affaiblir les assertions. Sept avertissements Alembic `path_separator` restent dans les suites complètes.

Pour reproduire :

```powershell
python -I tools/run_isolated_tests.py tests -q
python -I tools/run_isolated_tests.py --engine postgresql tests -q
python -I tools/run_isolated_tests.py tools/check_deployment.py -q
python -I tools/run_isolated_tests.py --engine postgresql tools/check_deployment.py -q
```

Le runner PostgreSQL exige une configuration locale marquée pour les tests et refuse une cible non autorisée. Ne pas lui fournir une URL de production.

## Limites et revue restante

- **Navigateur non validé de bout en bout** : une première tentative a vérifié les sorties et droits membre, puis s'est bloquée ; les tentatives suivantes avec Edge se sont fermées avant la page. L'installation d'un Chromium correspondant à Playwright a échoué avec `ENOSPC`. Aucun succès global navigateur, responsive ou aperçu JavaScript n'est revendiqué.
- `tools/check_stock_adjustments_browser.py` est fourni pour reprendre les essais sur ressources suffisantes : données SQLite fictives, profil neuf, CSRF réel, exemples, solde mixte, zéro/réapprovisionnement, aperçu périmé, CSV et largeurs 390/1280. Installer Playwright dans un environnement de développement isolé ; lancer avec `python -I tools/check_stock_adjustments_browser.py` pour Edge Windows. L'option `--chromium` attend un navigateur Playwright dans `.test-artifacts/playwright-browsers`. Ce script n'est pas une preuve de réussite tant que son résultat n'est pas positif.
- À vérifier sur téléphone réel : lisibilité et gestes dans les deux formulaires, clavier numérique, menu des motifs, navigation et comportement PWA/cache. Aucun test caméra supplémentaire ; son parcours est inchangé.
- Aucun déploiement, service photo réel, navigateur PostgreSQL, sauvegarde/restauration de production ni état GitHub vérifié. La validation serveur est acquise dans le périmètre testé ; la livraison reste destinée à la revue et aux essais avant publication.

## Équipe et traçabilité

Développeur : `gpt-6.1-terra`, effort `medium` ; testeur indépendant : `gpt-6.1-sol`, effort `medium`, paramètres acceptés lors de leur création. Le coordinateur utilise la conversation principale : Sol/medium est la configuration demandée, sans introspection du modèle/effort effectif. Aucun agent supplémentaire, aucune escalade high, aucun mode Fast ni consommation/quota estimé.

Le rapport indépendant et l'état de reprise restent dans `audit/v3_stock_adjustments_2026-10-31/`, hors commit avec les autres rapports préexistants. Les artefacts détaillés et bases fictives restent ignorés dans `.test-artifacts/`.
