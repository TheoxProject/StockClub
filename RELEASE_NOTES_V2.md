# Stock Club V2 — initialisation du premier administrateur

La V2 conserve les fonctionnalités de la version 1.4.3 et applique deux changements ciblés dans Comptes et sécurité :

1. Si la table utilisateurs est vide, la page de connexion propose de créer le premier compte administrateur. La création est disponible uniquement tant qu'aucun utilisateur n'existe. Une fois le compte créé, le lien disparaît et l'accès direct à la page de création est refusé.
2. Le dernier administrateur actif ne peut pas être rétrogradé. La mise à jour des rôles verrouille les lignes des administrateurs actifs pour limiter les courses entre opérations simultanées.

Le premier administrateur est créé avec un login et un mot de passe (6 caractères minimum, conformément à la version précédente). Après la création, la session est ouverte automatiquement. La commande d’administration locale `create-user` applique la même règle : si aucun utilisateur n’existe, le compte créé est administrateur ; sinon, il est membre. Une invitation résiduelle ne peut pas contourner l’initialisation du premier administrateur.

## Migration

La migration `0005_admin_bootstrap` ajoute une ligne de verrouillage singleton utilisée pour sérialiser les tentatives d'initialisation simultanées. Elle ne modifie ni les comptes, ni l'inventaire, ni les photos, ni les emprunts. Render applique la migration via la commande de démarrage existante.

Avant le déploiement, sauvegarder si possible la base Neon. Après passage de Render au statut Live, vérifier :

- Base avec des utilisateurs : aucun lien de création du premier administrateur n'apparaît.
- Base vide : la page de connexion propose l'initialisation.
- Après création : le nouveau compte a le rôle Administrateur et la page n'est plus accessible.
- Un administrateur ne peut pas rétrograder le dernier administrateur actif.
- Les invitations à usage unique, la photo mobile, la recherche, les emprunts/retours et la suppression administrateur fonctionnent toujours.


### Correctif photo mobile (V2.1)
- La caméra et la galerie utilisent désormais chacune un nom de champ multipart stable.
- Le serveur accepte les deux champs, ainsi que l'ancien nom `photo` pour compatibilité.
- Au retour de la caméra, le formulaire vérifie les fichiers présents lors des événements `visibilitychange`, `focus` et `pageshow`.
- Aucun aperçu haute résolution n'est généré sur mobile ; la limite de 7 Mo est conservée.

Version livrable : V2.1 (correctif photo mobile inclus).
