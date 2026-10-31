# V3 — boîtes physiques et mouvements groupés

Livraison locale du 31 octobre 2026, destinée à la revue et aux essais avant publication. Aucun accès à la production, aucune opération réseau Git ni déploiement. Les ajustements et pertes/casses V3 restent disponibles.

## Modèle et invariants

Une `StorageBox` représente un regroupement physique, pas un objet de stock ni un emprunt. Nom obligatoire, normalisé en supprimant les espaces superflus ; unicité par `casefold`, y compris pour les noms archivés. La description est facultative. Aucune boîte imbriquée. Pour suivre la caisse elle-même, affecter un objet ordinaire « Caisse plastique » : sa perte n'affecte pas les autres objets.

`Item.quantity` reste le **disponible global**. `BoxContent.present_quantity` représente la seule quantité **présente** d'un objet dans une boîte, incluse dans ce disponible global. Un couple boîte/objet est unique, son présent non négatif. Aucun compteur de stock total ou de quantité affectée n'est ajouté.

- Disponible hors boîte = disponible global − somme des présents en boîtes.
- Sorti d'une boîte = somme des restants à rendre de ses emprunts.
- Affecté à une boîte = présent + sorti.
- Sorti global = sorti hors boîte + somme des sortis des boîtes.
- Stock total = disponible global + sorti global.

Chaque emprunt conserve `box_id` (NULL pour hors boîte), un nom de boîte figé et éventuellement `group_id`. Un `LoanGroup` relie les emprunts d'une même sortie, avec date et acteur ; il n'a pas sa propre quantité ni son propre retour. La provenance d'un emprunt reste inchangée pendant sa résolution. Les événements de retour sont attribués à l'utilisateur qui effectue réellement le retour.

## Parcours

L'onglet **Boîtes** permet la recherche, la consultation et, pour les administrateurs, la création/édition. La recherche générale progressive trouve aussi les boîtes, avec des résultats explicitement marqués « Objet » ou « Boîte ». Les filtres catégorie/disponibilité/quantité concernent les objets ; les résultats de boîtes correspondent au texte recherché.

Une fiche boîte distingue **Affecté**, **Présent** et **Sorti**. Les liens mènent aux fiches objets. Une fiche objet montre sa répartition par boîte, les disponibles et sortis hors boîte. Tous les membres peuvent consulter et emprunter/retourner ; seuls les administrateurs gèrent les boîtes et leur composition.

Les affectations, retraits de boîte et déplacements concernent uniquement le présent. Ils ne changent ni le stock global ni la provenance des anciens emprunts. `BoxEvent` enregistre quantité, objet, provenance/destination, noms figés, date et administrateur, sans créer de faux mouvement de stock.

Sur une fiche objet, le prélèvement choisit une seule provenance visible : hors boîte ou une boîte. Une provenance sans présent ne peut pas fournir d'unités. Le retour remet les unités à leur origine ; un autre rangement nécessite ensuite un déplacement administrateur.

**Sortir la boîte** ouvre un formulaire sans mutation. Les présents sont proposés, les sortis visibles ; les quantités restent modifiables et les lignes sélectionnables. **Tout sélectionner / Tout désélectionner** actualisent le résumé avant confirmation. Une sortie incomplète est possible ; chaque ligne retenue crée un emprunt distinct dans le même groupe.

**Retourner la boîte** liste les emprunts encore ouverts, individuels et groupés, séparés même s'ils portent sur le même objet. Chaque ligne affiche acteur/date de sortie et restant à rendre. Un retour peut être partiel et ne modifie pas les lignes non sélectionnées. Le bouton est désactivé si rien n'est sorti. Aucun bouton de retour de groupe historique n'est proposé, ce qui évite toute confusion avec un retour de boîte entière.

Les emprunts individuels gardent leur borne existante de 100 000 par sortie. Les formulaires groupés acceptent jusqu'à 1 000 000 par ligne, afin de proposer tout le présent, avec contrôle du stock courant. Le plafond global existant reste 1 000 000.

## Ajustements, pertes et suppressions

Un ajout de stock arrive **hors boîte**, explicitement annoncé. Un retrait définitif choisit sa provenance et ne dépasse pas son présent ; disponible et total diminuent, ainsi que le présent/affecté de la boîte concernée. Une perte sur emprunt utilise sa provenance enregistrée : sorti, affecté et total diminuent ; disponible et présent ne changent pas. Aucun faux retour.

La suppression logique d'une boîte est bloquée tant qu'un emprunt lié reste ouvert. Après confirmation, les présents passent hors boîte, sans modification du stock global ni suppression d'objets. Le nom reste réservé et la fiche archivée consultable, avec historique de composition et d'emprunts. Les noms figés rendent les anciens événements lisibles après renommage.

La suppression d'un objet garde le blocage sur les emprunts ouverts ; la confirmation explique les affectations retirées et les présente dans la fiche. Les contenus associés sont retirés ; les historiques conservent les noms et perdent seulement le lien vers l'objet supprimé.

Le CSV garde les dix premières colonnes de la première amélioration et ajoute **Disponible hors boîte** et **Répartition des boîtes** (présents/sortis par boîte). Les traitements exigeant exactement dix colonnes doivent accepter ces deux colonnes supplémentaires. Les valeurs textuelles conservent la protection contre les formules CSV.

## Transactions et sécurité

Tous les chemins de mutation coopèrent avec le verrou transactionnel singleton existant, pris avant les lectures métier ; les droits sont relus après acquisition. Le même verrou couvre les opérations à plusieurs objets, sans ordre concurrent contradictoire de verrous par objet. Les refus concurrents restent compréhensibles ; le serveur ne réduit jamais silencieusement les quantités demandées.

Sorties et retours groupés valident toutes les lignes puis enregistrent une seule transaction : quantités, groupe, emprunts, clôtures, événements et mouvements. Une panne annule l'ensemble, y compris une première ligne déjà écrite. CSRF reste actif et les permissions sont appliquées aux requêtes directes, en plus du masquage des actions.

## Migration et mise en service ultérieure

Migration **0007_stock_boxes**, après **0006_stock_adjustments**. Elle crée les tables de boîtes/composition/groupes/historique et ajoute les provenances aux emprunts et mouvements. Les objets, emprunts ouverts, retours partiels et pertes/casses existants restent hors boîte, avec anciennes quantités et historique préservés. Aucune boîte fictive n'est créée.

Avant une future mise en service autorisée : sauvegarder et vérifier la restauration, tester une copie indépendante, appliquer `python -m flask --app run.py db upgrade` avant de servir le nouveau code, puis vérifier `/health` et les parcours. Les commandes et la configuration Render existantes sont préservées ; ces opérations ne sont pas exécutées par cette livraison.

Le downgrade vers 0006 est possible si aucune boîte, composition, provenance, sortie groupée ou histoire de boîte n'existe. Il est refusé dès qu'une boîte a été créée, même archivée ensuite. Privilégier une correction en avant après utilisation. Une restauration antérieure nécessite de traiter les opérations intervenues depuis la sauvegarde ; ne pas contourner les gardes en supprimant l'historique. La garde de la migration 0006 continue de protéger les ajustements et pertes/casses antérieurs contre un downgrade destructeur vers 0005.

## Validation

Les tests utilisent exclusivement `python -I tools/run_isolated_tests.py`, des bases fictives et un stockage de test ; dotenv et l'instance d'origine sont refusés. PostgreSQL 17.11 est une instance locale dédiée, arrêtée après les validations.

| Vérification réellement exécutée | Résultat | Preuve locale ignorée |
| --- | --- | --- |
| PostgreSQL, suite complète : `--engine postgresql tests -q --tb=short` | **202 réussis**, une seule passe complète finale | `.test-artifacts/run_5b34e59b3fdb/results.xml` |
| SQLite, suite complète avant les derniers cas complémentaires | 195 réussis, 2 échecs sur note/message existants, corrigés ensuite | `.test-artifacts/run_f1963e7955d6/results.xml` |
| SQLite, reprise des deux échecs et des 43 nouveaux cas finaux | **45 réussis** ; **202 cas distincts validés** cumulés, pas une nouvelle passe complète 202 | `.test-artifacts/run_b5e051d91ef0/results.xml` |
| Démarrage/migration/entrée `run:app`/health, base vierge et peuplée au schéma courant, PostgreSQL | **2 réussis** | `.test-artifacts/run_bea6676b2273/results.xml` |
| Même contrôle de compatibilité, SQLite | **2 réussis** | `.test-artifacts/run_63dd0025582e/results.xml` |
| Navigateur réel Edge 156, SQLite fictif, CSRF actif et profil neuf | **Parcours final réussi**, aucune erreur JavaScript, pas de débordement horizontal aux largeurs 390 et 1280 px | `.test-artifacts/browser_boxes_0529f7be0d/result.json` et captures `mobile.png`/`desktop.png` |

Les 202 tests comprennent les 159 cas antérieurs et **43 nouveaux** (41 fonctionnels/concurrence et 2 migrations). Ils couvrent noms normalisés/uniques, droits directs et CSRF, révocation du rôle pendant attente, répartition hors boîte/multi-boîtes, refus excessifs, provenance individuelle, sélection groupée complète/incomplète, retours mixtes/partiels et lignes non sélectionnées, pertes/casses/ajustements, archivage et suppression d'objet, historique et recherche générale HTML/AJAX.

**Onze courses** utilisent des connexions indépendantes, des Events et la retenue du premier commit : deux sorties ; individuelle/groupée ; sortie/déplacement ; sortie/retrait d'affectation ; retour individuel/groupé ; deux retours groupés ; perte/retour ; suppression boîte/affectation ; suppression boîte/sortie ; retour groupé/suppression boîte ; suppression objet/sortie. Les invariants sont vérifiés après chaque course. Deux tests de panne injectent une erreur après la **première ligne réellement écrite en SQL**, avant les suivantes : aucune mutation de domaine ni histoire partielle ne subsiste.

Les migrations sont testées depuis une base vierge et un vrai schéma **0006 peuplé avec ses seuls anciens champs**, comprenant emprunt ouvert, retour partiel et perte/casse. Les anciens champs sont comparés avant/après, les provenances initialisées hors boîte, le retour ancien reste opérant et les gardes de downgrade sont vérifiées. L'ancien test de migration 0005 reste exécuté : seul son head attendu est actualisé, et ses deux lectures finales après downgrade partiel utilisent les mêmes assertions en SQL pour ne pas demander les colonnes nouvelles à un schéma déjà revenu à 0006. Aucun test métier antérieur n'est supprimé ni affaibli.

Le navigateur vérifie création/affectations administrateur, retrait et aperçu à une provenance (dont refus d'un aperçu excessif), ajout hors boîte puis réaffectation, masquage membre, emprunt individuel depuis boîte, sortie groupée incomplète, boutons Tout sélectionner/Tout désélectionner, retour partiel d'une ligne puis solde des autres, bouton de retour désactivé, recherche progressive et affichage mobile/large. Les captures finales ont été relues ; un ancien style rendant chaque compteur vertical a été corrigé avant cette passe finale.

Les étapes transitoires sont conservées : première passe ciblée SQLite 39 réussis/1 échec de lecture ORM après downgrade partiel (`run_71b330ccef3c`), reprise migration/rollback 4 réussis (`run_72785dba0a55`), puis les résultats du tableau. Deux régressions note/message ont été corrigées dans le helper, avec les assertions existantes conservées. Le premier parcours navigateur était déjà réussi (`browser_boxes_3491dc21b8`) ; une reprise enrichie a échoué sur une assertion immédiate de sélection (`browser_boxes_fe2f6143fb`). L'outil attend désormais le résumé initial JavaScript et l'actualisation du DOM ; la passe finale conserve toutes les assertions et réussit. Les avertissements restants sont les dépréciations Alembic `path_separator` : 16 dans la suite PostgreSQL, 4 par contrôle de démarrage.

Pour reproduire sur les seules données de test autorisées :

```powershell
python -I tools/run_isolated_tests.py tests -q
python -I tools/run_isolated_tests.py --engine postgresql tests -q
python -I tools/run_isolated_tests.py tools/check_deployment.py -q
python -I tools/run_isolated_tests.py --engine postgresql tools/check_deployment.py -q
python -I tools/check_boxes_browser.py
```

Le runner PostgreSQL exige une configuration locale marquée pour les tests et refuse une cible non autorisée. Le script navigateur nécessite Playwright dans un environnement de développement isolé ; sous Windows il utilise Edge et un profil neuf. L'option `--chromium` attend un navigateur Playwright installé dans `.test-artifacts/playwright-browsers`. Il ne faut jamais fournir une URL ou un profil de production à ces outils.

## Limites et essais restants

- Les contrôles 390/1280 utilisent un navigateur de bureau avec émulation mobile ; **téléphone réel non testé**. Vérifier gestes, clavier numérique, listes de provenance/motifs, navigation et comportement PWA sur les appareils du club. Le parcours caméra est inchangé et n'a pas été retesté sur matériel réel.
- Navigateur testé avec SQLite ; PostgreSQL testé côté serveur, transactions, migrations et concurrence. Aucun essai navigateur PostgreSQL ni test de charge prolongée.
- Aucun service Render/Neon/S3 de production, sauvegarde/restauration réelle, synchronisation GitHub ou déploiement vérifié. Configuration et démarrage existants préservés. La livraison est prête pour revue et essais avant publication, sans attestation de mise en production.
- Le rapport indépendant et l'état de reprise restent dans `audit/v3_boxes_2026-10-31/`, hors commit ; les bases, profils et preuves détaillées restent ignorés dans `.test-artifacts/`.

## Équipe

Développeur : `gpt-6.1-terra`, effort `medium`. Testeur indépendant : `gpt-6.1-sol`, effort `medium`. Paramètres acceptés à la création. La conversation principale assure coordination et interface ; Sol/medium est demandé/configuré sans introspection du modèle réellement servi. Aucun sous-agent supplémentaire ni escalade high. Aucun quota ou consommation estimé.
