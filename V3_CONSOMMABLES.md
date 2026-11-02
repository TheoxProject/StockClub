# Consommables — StockClub V3

Livraison locale du 2 novembre 2026 sur `v3`. Aucune connexion à la production, opération réseau Git ou modification de Render.

## Parcours et droits

L’administrateur peut cocher **Consommable** lors de la création ou de la modification d’une fiche, et fixer un seuil facultatif de surveillance. Les champs envoyés par un membre sont ignorés : ils ne changent ni l’attribut ni le seuil. Les fiches existantes et les nouvelles fiches sans choix explicite d’un administrateur restent non consommables. Retirer l’attribut exige qu’aucun emprunt ne reste ouvert et désactive son seuil ; l’historique est conservé.

Les consommables gardent les emprunts, retours, pertes/casses, ajustements administrateur et toutes les fonctions des boîtes. Une sortie et un retour ne consomment jamais automatiquement du matériel. Les quantités restent entières, dans l’unité de la fiche, sans conversion de cartons, rouleaux ou sachets.

- **Consommer**, sur la fiche : choisir une provenance visible (hors boîte ou boîte avec du présent), une quantité et éventuellement un commentaire. Disponible et total diminuent ; aucun emprunt ni retour n’est créé.
- **Consommer**, dans « Matériel sorti » : saisir une quantité limitée au restant à rendre. Cette action reste séparée du retour. Le disponible ne change pas ; total et restant diminuent. L’acteur est le compte qui enregistre la consommation, même si un autre membre a créé l’emprunt.
- **Réapprovisionner**, sur la fiche : augmente disponible et total, toujours **Hors boîte**. L’affectation ultérieure utilise le parcours existant. Accessible aux membres et administrateurs pour les seules fiches consommables.
- **Annuler cette consommation**, administrateur : annulation complète, motif obligatoire, événement compensatoire lié à l’original. Pour corriger partiellement, annuler puis enregistrer la bonne quantité. Une même consommation ne peut être annulée deux fois.

Les actions de consommation et réapprovisionnement refusent une fiche redevenue non consommable, même avec un formulaire précédemment ouvert. Une correction administrative reste possible après retrait de l’attribut.

## Quantités, provenance et corrections

Les invariants restent :

```text
disponible global = disponible hors boîte + somme du présent dans les boîtes
total = disponible global + somme des restants à rendre
affecté à une boîte = présent + restant à rendre depuis cette boîte
sortie initiale = retourné + consommé net + perdu/cassé + restant à rendre
```

`Borrowing.quantity_out` reste inchangé. `quantity_consumed` contient la consommation nette après compensation ; les événements d’origine et de correction restent dans le journal. La clôture est « Soldé avec consommation ou perte/casse » si nécessaire, sans annoncer un retour fictif. Les requêtes de prêts ouverts, totaux, recherche, export et retours groupés soustraient la consommation nette.

Exemple : total 10/disponible 10 → emprunt 6 : total 10/disponible 4/restant 6 → consommation de 4 sur emprunt : total 6/disponible 4/restant 2 → retour 2 : total 6/disponible 6/restant 0.

L’annulation d’une consommation directe rétablit disponible et total à la provenance d’origine. Si la boîte a été supprimée (archivée), une case explicite autorise la restitution hors boîte ; le journal garde l’origine, la destination de substitution et le motif. L’annulation d’une consommation sur emprunt rétablit le restant et le total, rouvre l’emprunt et ne modifie pas le disponible. Elle est refusée si la fiche ou la boîte d’origine nécessaire n’existe plus : aucune recréation silencieuse ou emprunt orphelin.

La quantité d’une opération doit être comprise entre 1 et 1 000 000 ; le réapprovisionnement et les annulations vérifient aussi que le **total cumulé**, matériel emprunté compris, ne dépasse pas 1 000 000. Commentaires et motifs sont limités à 240 caractères.

## À surveiller et historique

L’ancienne rubrique comptait les fiches en état « À contrôler », « À réparer » ou « Hors service ». Ces critères sont conservés. Son lien et un filtre affichent maintenant les références concernées avec leurs raisons, dans leurs catégories habituelles.

Un seuil vide désactive seulement l’alerte de consommation ; 0 signale l’épuisement ; N signale un **total restant inférieur ou égal à N**, disponible et encore emprunté, toutes boîtes comprises. Une simple sortie ne déclenche donc pas cette alerte. Réapprovisionner peut la lever, tout en laissant une éventuelle raison liée à l’état. Aucun email n’est ajouté.

L’historique du stock distingue consommation directe, consommation sur emprunt, réapprovisionnement et annulation des mouvements existants. La fiche présente les consommations et leurs compensations. L’historique des consommables, accessible depuis la fiche et « Matériel sorti », présente les 100 dernières opérations et reste consultable après suppression d’une fiche, grâce aux noms figés.

## Transactions et doubles soumissions

Les nouvelles mutations utilisent `@synchronized`, donc le même verrou SQL singleton que les emprunts, retours, boîtes, pertes et suppressions. Le rôle et l’activité du compte sont relus après acquisition du verrou. Quantités, journal et reçu de soumission sont validés dans la même transaction ; une exception annule l’ensemble.

Chaque formulaire porte un jeton signé lié à l’acteur, à l’action et à la cible, valable 24 heures. Un reçu durable conserve l’empreinte des champs soumis. Une répétition identique du même jeton ne répète pas l’opération ; sa réutilisation avec des données différentes donne un conflit. Cette garantie concerne une même soumission : deux formulaires ouverts séparément constituent deux opérations, chacune soumise aux bornes et au verrou. CSRF reste actif.

## Migration et procédure Render

La migration `0009_consumables`, après `0008_box_fill_receipts`, ajoute :

- `items.is_consumable` (faux par défaut) et `consumption_threshold` (NULL par défaut) ;
- `borrowings.quantity_consumed` (0 par défaut) ;
- `consumptions` : journal, lien unique de compensation, provenance, snapshots et destination de substitution ;
- `consumable_receipts` : reçus durables de soumission.

Les anciennes quantités, affectations, emprunts et historiques ne sont pas transformés. Les données fictives de migration sont comparées colonne par colonne avant/après.

Le mécanisme observé dans **le fichier local `render.yaml`** est :

```sh
python -m flask --app run.py db upgrade && exec gunicorn --bind 0.0.0.0:$PORT run:app
```

Le démarrage prévu applique les migrations avant Gunicorn ; un échec empêche ce démarrage. `run.py` crée l’application mais ne lance pas lui-même de migration. Aucun état effectif du service Render n’a été consulté. Un push ne constitue donc pas une preuve d’application : vérifier la commande effective du service et la révision du code réellement déployé. Le workflow actuel réserve publication et déploiement à l’utilisateur, après revue.

Si la migration n’est pas incluse dans la commande effective, la commande manuelle, à exécuter par l’opérateur dans l’environnement de la version à publier et avec sa base explicitement sélectionnée, est :

```sh
python -m flask --app run.py db upgrade
```

Pour consulter la révision sans la modifier :

```sh
python -m flask --app run.py db current
```

Ou par lecture SQL :

```sql
SELECT version_num FROM alembic_version;
```

La tête de cette livraison est `0009_consumables`. Une version plus ancienne encore déployée possède légitimement une autre tête.

Le retour à `0008_box_fill_receipts` n’est autorisé que sans historique/reçu de consommables et sans attribut/seuil configuré. Il est volontairement refusé après utilisation afin de ne pas perdre les traces et protections. Les migrations plus anciennes ont aussi leurs propres gardes. Sauvegarde et procédure de restauration doivent être préparées par l’opérateur avant une publication ; aucune restauration ou migration de production n’a été effectuée ici.

## Validation de la livraison

**322 cas distincts ont chacun réussi sur chaque moteur**, au cours des passes ci-dessous. Aucun test ignoré ou sauté dans ces résultats. Les réexécutions ont été limitées aux tests modifiés ou aux vérifications de l’intégration finale.

| Moteur / contrôle | Résultat observé | Preuve locale sous `.test-artifacts/` |
| --- | --- | --- |
| PostgreSQL, suite complète `tests` + compatibilité Render | 321 réussites, 1 échec d’oracle de migration ; 443,48 s | `run_c7d163351f6b/results.xml` |
| PostgreSQL, test de migration corrigé, seul | 1/1 ; 3,97 s | `run_b2b599046df2/results.xml` |
| SQLite, nouveaux tests indépendants | 77 réussites + 2 échecs d’oracle, puis 2/2 corrigés | `run_6c14f587a3e4/results.xml`, `run_5cf233a9b95a/results.xml` |
| SQLite, anciennes migrations | 5/5 ; 24,61 s | `run_a8e591252434/results.xml` |
| SQLite, régressions restantes + compatibilité Render | 238/238 ; 383,20 s ; exclut les cas précédents déjà validés | `run_4c406007d9d9/results.xml` |
| SQLite, test de migration adapté à PostgreSQL | 1/1 ; 3,75 s | `run_39e5f151087a/results.xml` |
| Edge réel, CSRF actif, profil neuf, SQLite fictif | Réussite, aucune erreur JavaScript ; largeurs 390 et 1280 | `browser_consumables_c3dbfc59c0/result.json`, `mobile.png`, `desktop.png` |

Les 79 nouveaux cas indépendants portent sur les consommations hors boîte et en boîte, prêts partiellement ou totalement consommés, exemple des dix rouleaux, retour du reliquat individuel/groupé, réapprovisionnement membre/admin, droits des champs administrateur, seuils, annulations, suppression des provenances, CSRF, jetons invalides/falsifiés/expirés et panne après `flush` avant commit. Les snapshots comparent toutes les tables métier et les reçus avant/après un refus ou une panne.

Les 19 scénarios de concurrence utilisent des sessions et connexions DBAPI distinctes : dernières unités, emprunt/déplacement/retrait, retour individuel/groupé, perte, seconde consommation, double annulation, plafond de réapprovisionnement, désactivation de l’attribut, suppression objet/boîte et révocation du rôle pendant l’attente. Certains ordres inverses ont aussi été vérifiés ; aucune exhaustivité de toutes les permutations n’est prétendue.

Le testeur a d’abord vérifié 65 cas PostgreSQL puis les renforts ciblés (`run_323b4ea8b164`, `run_3061138dc514`, `run_8076f7af1cba`), avant la passe globale du coordinateur. Son rapport local indépendant est `audit/consumables_2026-11-02/testeur.md`, conservé hors commit avec les rapports préexistants.

Trois anciens tests de migration ont été adaptés à l’ajout des colonnes et à la nouvelle tête, sans réduire la comparaison des **valeurs historiques**. L’échec isolé PostgreSQL était un snapshot `SELECT *` qui incluait les nouveaux champs : PostgreSQL annule la totalité du downgrade transactionnel si une garde ultérieure le refuse, alors que SQLite peut avoir déjà retiré ces champs. Le test compare désormais les anciennes valeurs par nom de colonne. Le code applicatif n’a pas changé à la suite de cet échec. Les erreurs initiales des nouvelles gardes venaient du wrapper Flask-Migrate qui transforme `RuntimeError` en `SystemExit` ; leur oracle utilise maintenant directement Alembic.

Les deux contrôles Render par moteur appliquent la migration sur bases fictives vierge/peuplée, chargent `run:app`, vérifient `/health` et la commande CLI de santé. Aucun appel S3 n’est effectué. Le fichier `render.yaml` et `run.py` sont inchangés.

Les avertissements observés sont ceux de la configuration Alembic préexistante sans `path_separator` (37 dans la passe complète PostgreSQL). Aucun échec métier ne reste confirmé dans les scénarios exécutés. Le cluster PostgreSQL portable de test est arrêté après validation.

### Limites à vérifier avant publication

- Téléphone physique, clavier natif, caméra et installation PWA : non vérifiés pour cette livraison ; la largeur mobile émulée ne remplace pas un téléphone.
- Navigateur avec PostgreSQL, coupure réseau réelle, redémarrage réel de Render, services S3 et données de production : non testés. Les parcours navigateur utilisent SQLite et le backend partagé est validé sur PostgreSQL isolé.
- Charges prolongées, toutes les permutations concurrentes et restauration de sauvegarde réelle : non testées.
- L’historique global présente les 100 dernières opérations ; la fiche conserve l’accès à ses propres consommations. Un jeton a une validité de 24 heures et protège la répétition d’une même soumission, pas deux formulaires volontairement distincts.

Organisation : coordinateur pour intégration et revue ; développeur Terra medium et testeur indépendant Sol medium (identifiants demandés et acceptés par le mécanisme de sous-agents). Le modèle et l’effort effectifs de la conversation principale ne sont pas vérifiables par introspection. Aucune escalade high, Astra ou mode Fast utilisée ; aucune consommation ou quota prétendu.
