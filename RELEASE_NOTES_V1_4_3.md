# Stock Club v1.4.3 — Correction de l'inscription par invitation

## Problème

Sur certains téléphones, notamment lorsque le lien d'invitation est ouvert dans un navigateur intégré à une messagerie, la session peut être perdue entre l'affichage et l'envoi du formulaire. Le formulaire était alors rejeté par le contrôle CSRF global : « Le formulaire a expiré ».

## Correction

- La page d'inscription reçoit désormais un jeton de formulaire **signé, horodaté et lié à l'invitation précise**.
- La vérification ne dépend plus d'un cookie de session. Elle a une validité de **2 heures** après l'ouverture du formulaire ; un simple rechargement en génère un nouveau.
- L'invitation elle-même reste valable **7 jours**, à **usage unique**.
- Les protections CSRF des autres formulaires (connexion, inventaire, administration, etc.) ne changent pas.
- Un jeton manquant, expiré, altéré ou issu d'une autre invitation est rejeté.

Ce changement ne demande **aucune migration de base de données** ni modification des variables Render.

## Installation et vérifications

Copier les fichiers de l'archive sur le projet local, sans toucher à `.env` ni aux secrets Neon. Exécuter :

```powershell
python -m pytest -q
git status --short
git diff --check
```

Avant d'envoyer sur GitHub, ne sélectionner que les fichiers de cette version :

```powershell
git add app/auth/routes.py app/templates/auth/register.html tests/test_registration.py tests/test_invitation_csrf.py RELEASE_NOTES_V1_4_3.md
git diff --cached --name-only
git diff --cached --check
git commit -m "Fix invitation registration CSRF on mobile"
git push
```

Après le statut **Live** sur Render, ouvrir de nouveau le lien d'invitation, de préférence dans Safari ou Chrome, et réessayer l'inscription. Ne pas partager les liens d'invitation publiquement.
