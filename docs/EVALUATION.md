# Protocole d'évaluation

> Pré-enregistré le 2026-10-03, **avant** le calcul de tout chiffre du run final.
> Les règles de décision ci-dessous ne sont pas modifiées après avoir vu les résultats de test.
>
> *Traduit de l'anglais en français le 2026-10-04 pour le jury, sans changement de fond ; la
> version d'origine est dans l'historique git. Seule correction : §7 annonçait « three exceptions »
> et en listait quatre ; le texte dit désormais « les exceptions suivantes ».*

## 1. Question

Avec quelle exactitude le pipeline transforme-t-il la photo d'une page de registre en champs
typés, et sa confiance indique-t-elle à la sage-femme *quels* champs vérifier ?

## 2. Données et vérité terrain

* **Source** : le PDF spécimen des organisateurs (10 patientes fictives × 8 pages). La vérité
  terrain est extraite automatiquement du PDF lui-même : les valeurs manuscrites sont du texte
  composé dans des polices manuscrites, les coches sont des tracés vectoriels de couleur stylo
  (`src/dayone/evaluation/groundtruth.py`). 6 010 champs, 2 032 champs écrits (dont 129 tirets et
  93 dont les seuls caractères sont des glyphes absents de la police du générateur — invisibles
  sur l'image, donc comptés comme vides), 468 cases cochées. Chaque patiente est écrite dans
  l'une de cinq polices manuscrites, et les polices sont appariées entre les deux jeux (1↔6, 2↔7,
  3↔8, 4↔9, 5↔10). Chaque chaîne manuscrite est affectée à un champ de la mise en page, sauf les
  identifiants directs (nom, numéro d'identité, téléphone, adresse, mari) et les professions,
  volontairement absents du schéma.
* **Doublons** : les 124 fichiers PNG fournis contiennent 80 pages distinctes ; 44 sont des
  copies identiques octet pour octet (suffixes Drive). Elles sont retirées par empreinte avant
  toute autre chose.
* **Captures** : les PNG sont des rendus propres, donc les conditions de terrain sont simulées
  (`degrade.py`, graine fixée) : `clean` (rendu d'origine), `mild` (photo d'appareil, ≈ 2000-2400
  px sur le grand côté), `medium` (compressée par WhatsApp, ≈ 1600 px, ombres, faible lumière —
  les vraies photos des organisateurs font 900×1600), `severe` (≈ 1200 px, flou et obscurité
  forts).
* **Multilingue** : le spécimen ne contient ni arabe ni anglais ; les pages de 4 patientes sont
  donc réécrites en arabe, en anglais et en mélange FR/AR/EN avec des polices manuscrites libres
  (`synth.py`), et capturées au niveau `medium`.
* **Séparation par patiente** : patientes 1-5 = jeu de *calibration* (modèle de confiance,
  seuils), patientes 6-10 = jeu de *test*. Tous les chiffres principaux sont sur le jeu de test.

## 3. Mesures (par champ, sur le jeu de test)

* **Exactitude de l'extraction** — sur les champs qui contiennent une valeur manuscrite : la
  valeur canonique prédite est égale à la vérité terrain (dates en ISO, nombres en nombres,
  vocabulaires fermés en jetons canoniques quelle que soit la langue ; texte libre sans tenir
  compte de la casse ni des accents). Rapportée par niveau de capture, langue, type de page et
  type de valeur.
* **Vides et tirets** — champs vides prédits NOT_PROVIDED (ou NOT_APPLICABLE par une règle de
  cohérence) ; tirets prédits NOT_APPLICABLE.
* **Cases à cocher** — exactitude coché / non coché.
* **Incertitude** :
  * *couverture d'acceptation automatique* : part des champs manuscrits sur lesquels l'agent ne
    pose pas de question ;
  * *taux d'erreurs silencieuses* : parmi les champs manuscrits sur lesquels l'agent n'a **pas**
    posé de question (acceptés comme valeur, ou comme vide / non applicable / inconnu), la part
    qui est fausse — l'agent s'est trompé **et** ne l'a pas dit ; rapporté aussi sur tous les
    champs (vides, tirets, cases à cocher) ;
  * *calibration* : erreur de calibration attendue (ECE, 10 tranches), score de Brier, AUROC de
    la confiance pour séparer les bonnes lectures des mauvaises, tableau de fiabilité ;
  * *courbe risque–couverture* quand le seuil d'acceptation varie.
* **Contrôle qualité des captures** — part des captures renvoyées pour une reprise, par niveau,
  et exactitude sur les captures acceptées vs refusées.
* **Classification des pages** — part des pages affectées au bon type de page.

## 4. Règles de décision (fixées à l'avance)

1. Le modèle de confiance est ajusté sur le jeu de calibration seul.
2. Le seuil d'acceptation τ est le **plus petit** seuil dont le taux d'erreurs silencieuses sur
   le jeu de calibration (niveaux clean/mild/medium, toutes langues) est ≤ **2 %**, calculé sur
   des confiances **hors échantillon** (en laissant une patiente de côté à chaque fois) pour que
   τ ne soit pas choisi sur les lectures ayant servi à ajuster le modèle. Il est ensuite figé et
   appliqué au jeu de test. Si aucun τ ≤ 0,99 n'atteint l'objectif, τ = 0,99 et le rapport dit
   que l'objectif n'est pas atteint.
3. Le jeu de test sert une seule fois, pour le rapport final ; aucun paramètre n'est modifié
   ensuite. Si un bogue est trouvé après le rapport, il est corrigé, toute la séquence
   calibration → test est relancée et le changement est consigné en section 7.
4. Les captures `severe` sont censées être refusées par le contrôle qualité ; leur exactitude
   est rapportée par transparence mais n'est pas un objectif.

## 5. Reproduire

```bash
make prepare      # vérité terrain + gabarits
make dataset      # captures simulées (graines fixées)
make eval         # extraction sur chaque capture (modèles locaux, reprenable, ~3-4 h sur un M4 Pro)
make calibrate    # ajuste le modèle de confiance + le seuil sur le jeu de calibration
make report       # mesures sur le jeu de test -> docs/RESULTS.md
```

## 6. Écarts connus (déclarés avant le run final)

* **Les seuils de développement ont vu toutes les pages.** Les seuils de recalage / de type de
  page (`MIN_SIMILARITY`, `MIN_MARGIN`), la zone de remplissage des cases à cocher et les seuils
  de qualité des captures ont été réglés en regardant des statistiques sur les 80 pages du
  spécimen (les deux jeux). Ce sont des seuils géométriques/d'image, pas ajustés sur les valeurs,
  mais les chiffres du test pour la **classification des pages**, les **cases à cocher**, la
  **détection des vides** et les **demandes de reprise** sont donc optimistes. Le modèle de
  confiance et τ (les chiffres d'incertitude) ont été ajustés sur la calibration seule.
* **Une seule mise en page.** Toutes les pages de test partagent la mise en page du spécimen ;
  rien ici ne mesure la généralisation à un autre livret.
* **Arabe/anglais synthétiques.** Des polices, pas une vraie écriture manuscrite.
* **Pages mal classées.** Une page classée comme un autre type compte tous ses champs comme
  erreurs dans l'exactitude, mais aucun dans le taux d'erreurs silencieuses (dans l'application,
  ses valeurs seraient rangées dans les mauvais champs sans question). L'exactitude de
  classification des pages est rapportée à part pour que ce cas reste visible.

## 7. Journal des modifications

Toutes les entrées ci-dessous ont été écrites **avant** le calcul de toute mesure du run final.

* 2026-10-03 — **premier run (`main`) abandonné.** Il a été interrompu deux fois par un blocage
  d'un processus Ollama (glm-ocr) et, pendant la pause, l'extraction a changé. Tout est relancé
  de zéro sous le nom `final`, avec le code figé au commit consigné dans
  `artifacts/eval/runs/final/_meta.json`. Changements depuis la rédaction du protocole :
  * second lecteur rendu facultatif (délai maximal + disjoncteur ; consigné comme
    `second_missing`) ;
  * recadrages localisés à partir de l'encre elle-même (cadres de valeur) au lieu du rectangle
    brut du champ, car 1 à 3 pt d'erreur de recalage faisaient attraper la ligne voisine ; les
    bordures de tableau sont ignorées pour décider si un champ est vide ;
  * petits recadrages complétés à 64 px (le modèle de vision refuse les images de moins de
    32 px) ;
  * réparation des erreurs de lecture systématiques (virgule perdue, unité « g » lue « 9 »,
    barre de tension perdue), rapprochement de vocabulaire tolérant pour les mots de ≤ 4 lettres
    (« eAs » → RAS) ;
  * toute valeur réparée ou signalée par une règle de cohérence force désormais une révision ;
    les tirets / points d'interrogation incertains sont confirmés avec la sage-femme ; les motifs
    de téléphone / numéro d'identité sont retirés du texte OCR ;
  * la définition des erreurs silencieuses (§3) et le choix de τ (§4 règle 2) ont été amendés
    comme ci-dessus après qu'une relecture adversariale du code a montré que la première
    définition ne comptait que les valeurs acceptées comme KNOWN et que τ était choisi sur les
    données d'ajustement.
* **Ce qui a été regardé avant le run final.** Le développement a utilisé le jeu de calibration
  (patientes 1-5), avec les exceptions suivantes, déclarées ici : (1) un premier script de
  comparaison d'OCR a affiché les erreurs de lecture d'une page du test (page 43, patiente 6,
  clean) ; (2) les statistiques de classification / recalage des pages ont été calculées sur les
  80 pages (voir §6) ; (3) après l'ajout des réparations, la vérité terrain a été régénérée pour
  toutes les pages afin de vérifier qu'elle ne changeait pas (elle n'a pas changé) ; (4) le run
  abandonné `main` avait produit 52 prédictions de captures du test (les 5 patientes de test)
  avant d'être arrêté. Aucune mesure n'a été calculée dessus et elles n'ont pas été examinées,
  mais elles existaient sur le disque (trouvées par l'audit des résultats).
* 2026-10-03 — après le lancement du run `final` (commit dans `_meta.json`), deux fichiers
  d'extraction ont changé pour le téléphone/serveur uniquement, sans effet sur ce que mesure
  l'évaluation : `register.py` accepte la page sœur quand un type de page est *attendu*
  (l'évaluation n'en passe jamais), et `Extractor(ocr_cache=False)` permet au serveur d'éviter
  le cache OCR sur disque (l'évaluation garde le défaut, cache activé).

## 8. Expérience complémentaire — écriture jamais vue (pré-enregistrée le 2026-10-03, avant de la lancer)

* **Pourquoi.** L'audit du run final a montré que chaque patiente est écrite dans l'une de cinq
  polices manuscrites, partagées entre calibration et test (1↔6, 2↔7, …) : le jeu de test mesure
  de nouvelles *valeurs*, pas une nouvelle *écriture*.
* **Question.** L'extraction tient-elle sur une écriture jamais vue pendant le développement ?
* **Données.** Les 40 pages du test (patientes 6-10), réécrites en français avec deux polices
  manuscrites jamais utilisées (Indie Flower, Homemade Apple, en alternance par page), capturées
  au niveau `medium` (graine fixée). Les champs dont le texte ne tient pas dans la case sont
  retirés de la vérité terrain et comptés.
* **Figé.** Mêmes modèles, même modèle de confiance et même τ que le run final ; rien n'est
  réglé.
* **Mesures.** Exactitude des champs manuscrits et taux d'erreurs silencieuses, avec des
  intervalles bootstrap à 95 % sur les pages, comparés aux mêmes pages dans leurs polices
  d'origine au niveau `medium`.
* **Règle de publication.** Rapportée dans `docs/RESULTS.md` quel que soit le résultat.

## 9. Constats a posteriori de l'audit des résultats (2026-10-03, après le run final)

Consignés après les chiffres finaux, rien n'a été re-réglé :
* **Plancher de grille non déclaré.** `make calibrate` cherchait τ dans [0,50 ; 0,99] : τ = 0,50
  est donc le plancher de la grille, pas « le plus petit τ » de la règle 2. Le rapport donne le τ
  que la règle produit sans ce plancher et son taux d'erreurs silencieuses en test.
* **Puissance statistique.** 40 pages de test issues de 5 patientes : le rapport donne des
  intervalles bootstrap à 95 % sur les pages. L'intervalle des erreurs silencieuses franchit
  2 % : l'objectif n'est donc pas démontré.
* **Mélange.** Le chiffre principal mélange rendus d'origine, photos simulées légères et
  moyennes ; le rapport donne les chiffres des photos moyennes seules à côté.
* **Écriture vs langue de la page.** Les pages « arabes » contiennent des chiffres occidentaux
  et du texte latin ; le rapport ventile les résultats selon l'écriture réellement utilisée.
* L'ECE est dominée par les captures faciles ; le rapport ajoute des strates et des références
  triviales.
