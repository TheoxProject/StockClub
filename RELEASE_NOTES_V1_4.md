# Stock Club 1.4 — recherche, emprunts et suppression contrôlée

## Recherche et filtres

- Résultats réactualisés automatiquement pendant la saisie, sans rechargement complet de la page.
- Filtres combinables : catégorie, disponibilité, quantité disponible minimale.
- Les filtres Emplacement et État du matériel ne sont pas proposés.

## Sorties et retours

- Nouvelle page « Matériel sorti » accessible sur ordinateur et téléphone.
- Chaque nouvelle sortie est associée automatiquement au bénévole connecté.
- Affichage de la quantité initialement sortie et du nombre d’unités encore dehors.
- Retours partiels ou complets associés à la sortie d’origine, avec historique horodaté de chaque événement.
- Aucune date de retour prévue.

## Suppression d’un objet

- Le bouton est visible mais désactivé pour les membres, avec la mention « Réservé aux administrateurs ».
- Le serveur contrôle également le rôle : une requête directe d’un membre est refusée.
- Confirmation avant suppression. Suppression impossible tant qu’une quantité de l’objet est encore à l’extérieur.
- Les lignes du journal et les historiques d’emprunt sont conservés avec l’ancien nom de l’objet. La photo est supprimée du stockage Neon après confirmation de la suppression en base, seulement si elle n’est plus utilisée.

## Migration et sécurité

- Migration de base `0004_live_search_loans` après la migration des rôles/invitations `0003_roles_invitations`.
- À déployer sur une base sauvegardée au préalable. Render doit exécuter `python -m flask --app run.py db upgrade` avant de démarrer Gunicorn.
- Les emprunts antérieurs à cette version sont reconstitués uniquement si l’ancien journal contient des mouvements dont les notes commencent par « Emprunt » et « Retour ». Ils sont regroupés par objet, et le responsable réel des unités restantes est marqué comme inconnu au lieu d’être deviné.

## Vérification locale

```powershell
python -m pytest -q
```
