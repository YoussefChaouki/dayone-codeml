# Script de démonstration

Deux démonstrations couvrent les quatre moments demandés : capture hors ligne, retour de la
connexion, révision d'un champ incertain, décision de correspondance.

## A. Vidéo sur le vrai WhatsApp (celle de la soumission)

[Vidéo (3 min 40)](https://github.com/YoussefChaouki/dayone-codeml/releases/download/v1.0/DayOne-demo-x1.25.mp4),
filmée sur un téléphone ([WHATSAPP.md](WHATSAPP.md)). Pour montrer la file d'attente, la
passerelle a été lancée **hors ligne** :

```bash
set -a; . ./whatsapp.env; set +a; uv run python -m dayone.demo --offline --reset   # terminal 1
make tunnel                                                                          # terminal 2
```

puis remise en ligne une vingtaine de secondes après la première fiche, avec l'interrupteur réseau
du panneau coulisses (http://127.0.0.1:8000, accessible en local seulement). Sans `--offline`
(`make demo-whatsapp`), la fiche est lue tout de suite.

| # | Sur le téléphone | Ce qu'on voit |
|---|---|---|
| 1 | `menu` → *Nouvelle fiche* → photo de la couverture | la photo revient **avec le nom noirci** : « Nom et identifiants masqués, photo chiffrée sur le téléphone » |
| 2 | *Terminer* | « 📴 Pas de réseau : la fiche est enregistrée et chiffrée… EN ATTENTE IA » |
| 3 | (attendre ; réseau remis en ligne dans les coulisses) | au retour du réseau, sans rien faire sur le téléphone : « 🤖 La fiche a été lue : 21 champs, dont 20 lus avec confiance et 1 sur lesquels j'ai un doute » |
| 4 | *Vérifier la fiche* | « Établissement sanitaire — Je lis : *CSy Ernanda*, mais je ne suis pas sûre (confiance 11 %). Autre lecture possible… Pourquoi : deux lectures différentes » → *Corriger* → `CSU Ennahda` |
| 5 | *Tout confirmer* → confirmation du code | l'IA a lu `2026-63-007` (un chiffre perdu) ; *Corriger le code* → `2026-163-007` → *Créer la patiente* → « Fiche synchronisée avec le serveur ✔️ » |
| 6 | 2ᵉ visite : même page, même révision | **[Patiente 1] [Aucune, créer] [Je ne sais pas]** avec la raison (« même code » ou « code à 1 caractère près ») → *Patiente 1* ; si des valeurs diffèrent, l'agent montre les différences à arbitrer (renumérisation) |

## B. Simulateur local (≈ 5 minutes, sans WhatsApp)

```bash
ollama serve            # s'il ne tourne pas déjà (modèles : make models)
make demo-offline       # = uv run python -m dayone.demo --offline   (ajouter --reset pour repartir de zéro)
```

Ouvrir http://127.0.0.1:8000. À gauche : la conversation de la sage-femme, façon WhatsApp. À
droite : le panneau « coulisses » (interrupteur réseau, pannes, cycle de vie, file d'attente,
stockage chiffré brut).

| # | Action | À montrer |
|---|---|---|
| 1 | Les coulisses affichent **📴 Hors ligne**. Toucher *📷 Nouvelle fiche*, puis 📷 → choisir `spec_p02_medium.jpg` (page d'identification) | « Page reçue … nom et identifiants masqués » : la vignette montre CIN, adresse, téléphone et nom du mari noircis. Coulisses : fiche `CAPTURÉ`, la ligne brute en base est chiffrée |
| 2 | Ajouter `spec_p01_medium.jpg` et `spec_p04_medium.jpg`, toucher *Terminer* | « Pas de réseau : la fiche est enregistrée et chiffrée… », état `EN_ATTENTE_IA` |
| 3 | Facultatif : choisir `spec_p41_severe.jpg` | Contrôle qualité : « la photo est floue » → *Reprendre* / *Garder quand même* |
| 4 | Cliquer **💥 Crash + redémarrage** | L'application est reconstruite depuis le stockage chiffré ; « 3 tâche(s) en attente retrouvée(s) » |
| 5 | Passer le réseau **📶 En ligne** (éventuellement armer « réponse perdue » avant) | La file se vide : envoi → le serveur lit avec les modèles locaux → `TRAITÉ_IA` → `À_RÉVISER` ; une réponse perdue est relancée sans doublon |
| 6 | Toucher *🔎 Vérifier maintenant* | Pour chaque champ douteux : valeur, confiance, *pourquoi* (p. ex. « valeur hors des plages habituelles, deux lectures différentes »), lecture alternative. Essayer *🖼️ Voir l'image* (le recadrage exact), puis choisir l'alternative (p. ex. poids de naissance lu *36269 g* → *3626 g*) |
| 7 | *✅ Tout confirmer* | Résumé des champs sûrs ; `VALIDÉ` |
| 8 | Liaison : l'agent demande d'abord de confirmer le code lu (« J'ai lu : 2026-…, est-ce bien le code ? ») ; la première fois, *➕ Créer la patiente* ; puis rephotographier `spec_p03_medium.jpg` + `spec_p01_medium.jpg` de la même patiente | La deuxième fois : **[Patiente 1] [Aucune, créer] [Je ne sais pas]** avec les raisons (« même code, DPA cohérente ») ; si des valeurs diffèrent, les différences de renumérisation laissent la sage-femme garder l'ancienne ou prendre la nouvelle |
| 9 | Suivre `ENREGISTRÉ` → `SYNCHRONISÉ`, puis *📁 Voir le dossier* (ou taper `dossier 2026-…`) | « Fiche synchronisée avec le serveur ✔️ » ; le dossier longitudinal de la patiente : visites (date, âge gestationnel, poids, TA), tests, accouchement, post-partum |
| 10 | Coulisses, « Image d'origine — accès par rôle » | sage-femme autrice ✅, autre sage-femme ⛔ 403, superviseur ✅, épidémiologiste ⛔ — tout est journalisé |
| 11 | Ouvrir http://127.0.0.1:8100/dashboard | Agrégats anonymisés, cellules < 5 affichées « <5 » |
| 12 | Taper `language en`, ou `saisie` pour la saisie manuelle | Interface bilingue ; saisie manuelle complète sans IA |

Durées : sur un M4 Pro, une page simple est lue en 10 à 25 s, le tableau dense des visites en
1 à 3 min (≈ 280 cases). Le serveur ne garde aucun cache OCR (les lectures brutes seraient en
clair sur le disque) : une page rejouée est relue.
