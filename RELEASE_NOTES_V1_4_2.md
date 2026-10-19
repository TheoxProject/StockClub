# Stock Club v1.4.2 — caméra mobile

Deux actions distinctes : « Prendre une photo » (`capture="environment"`) et « Choisir depuis la galerie ».
Le comportement précis de l'ouverture de l'appareil photo dépend du navigateur mobile.

La validation des photos et la suppression de l'aperçu lourd sur téléphone sont conservées.

## Installer

Remplace les fichiers du projet par ceux de l’archive, en préservant `.env`. Puis :

```powershell
python -m pytest -q
git status --short
git add app/templates/inventory/item_form.html RELEASE_NOTES_V1_4_2.md
git diff --cached --check
git diff --cached --name-only
git commit -m "Restore mobile camera capture control"
git push
```

Teste la prise de photo sur ton téléphone après redéploiement.
