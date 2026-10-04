# Choix de conception

Chaque section donne la décision, sa raison, et ce qui a été mesuré ou écarté. Les chiffres
marqués *exploration* viennent du jeu de calibration (patientes 1-5) pendant le développement ;
les résultats rapportés sont dans [RESULTS.md](RESULTS.md) (jeu de test, patientes 6-10).

## 1. Partir du registre, pas de l'OCR

Le spécimen est un formulaire fixe (« Fiche de surveillance de la grossesse et du
post-partum »). Chaque champ est déclaré dans [`forms/layout.py`](../src/dayone/forms/layout.py) :
page, section, libellé FR/EN, nature (texte ou case à cocher), type de valeur, unité, plage de
plausibilité, vocabulaire fermé, groupe à choix unique ou multiple, ligne/colonne de tableau, et
sa position sur la page de référence (points PDF).

| Page | Champs | Texte | Cases à cocher |
|---|---|---|---|
| Couverture / établissement | 21 | 5 | 16 |
| Identification et antécédents | 76 | 67 | 9 |
| Grossesse actuelle (9 visites × 30 lignes) | 280 | 274 | 6 |
| Accouchement | 34 | 10 | 24 |
| Post-partum précoce — mère / nouveau-né | 51 / 44 | 11 / 13 | 40 / 31 |
| Post-partum tardif — mère / nouveau-né | 51 / 44 | 11 / 13 | 40 / 31 |
| **Total** | **601** | | |

Types de valeur : `date` (ISO), `bp` (tension « 120/80 » ; « 12/8 » en cmHg converti), `int`,
`float`, `gest_age` (semaines, « SA »), `bool`, `enum` (vocabulaires fermés avec formes FR/EN/AR :
« Neg », « Négatif », « Negative », « سلبي » → `negative`), `code`, `text` libre.

**Volontairement absents du schéma** : nom de la patiente, nom du mari, numéro de CIN,
téléphone, adresse (identifiants directs) et professions (inutiles). Leurs zones sont listées à
part (`PII_ZONES`) pour être noircies.

## 2. Modèle de statuts : l'information manquante est un état

| Statut | Signification | Qui le fixe |
|---|---|---|
| `KNOWN` (CONNU) | valeur fiable : confiance ≥ τ, ou confirmée / saisie par la sage-femme | pipeline / sage-femme |
| `NEEDS_REVIEW` (À_RÉVISER) | une valeur a été lue mais sa confiance est < τ, une règle de cohérence l'a signalée, ou elle a été réparée automatiquement | pipeline |
| `ILLEGIBLE` (ILLISIBLE) | il y a de l'encre mais elle n'a pas pu être lue | pipeline / sage-femme |
| `NOT_PROVIDED` (NON_FOURNI) | le champ est vide sur le papier | contrôle des pixels (pas d'encre) / sage-femme |
| `NOT_APPLICABLE` (NON_APPLICABLE) | un tiret est écrit, ou le champ est logiquement sans objet (indication de césarienne après une voie basse, colonnes d'accouchements antérieurs au-delà de la parité, date de vaccin si non vaccinée…) | lecteur / règles / sage-femme |
| `UNKNOWN` (INCONNU) | « ? », « NSP », « inconnu » est écrit, ou la sage-femme dit que c'est inconnu | lecteur / sage-femme |

Cases à cocher : une case non cochée vaut `KNOWN false` (c'est ainsi que le papier encode
« non ») ; un taux de remplissage dans la zone ambiguë donne `NEEDS_REVIEW` ; deux coches dans un
groupe à choix unique sont signalées.

Chaque valeur garde aussi sa provenance : `source` (`ocr`, `checkbox`, `ink`, `rule`,
`confirmed`, `corrected`, `manual`), identifiant de page, texte brut, lectures alternatives,
alertes et trace d'audit des décisions humaines.

## 3. Pipeline d'extraction

```
photo ─► contrôle qualité ─► contour de la page ─► type de page + recalage ─► masquage des identifiants ─► (stockée, chiffrée)
                                                        │
                          ┌─────────────────────────────┴─────────────────────────┐
                cases à cocher (remplissage)   champs vides (pas d'encre)   champs de texte encrés
                          │                             │                         │
                          │                             │      qwen3.5:9b (prompt avec libellé + format)
                          │                             │      glm-ocr (second lecteur indépendant)
                          └──────► analyse/normalisation ► règles de cohérence ► confiance calibrée ► statut
```

Tout ce qui précède l'OCR est de la vision par ordinateur classique et tourne sur le téléphone,
hors ligne. Les décisions et ce qu'elles ont remplacé :

* **Recalage en deux étapes.** L'appariement de points seul (SIFT + RANSAC sur toute la photo)
  échouait sur les captures dégradées du tableau des visites : le tableau est répétitif et
  l'en-tête/le pied de page sont communs à toutes les pages, si bien qu'une homographie fausse
  avait quand même plus de 80 points concordants (exploration : 22/40 corrects sur photos très
  dégradées). Désormais (1) on segmente la feuille du fond — sur la luminosité *et* sur le canal
  a* de l'espace Lab, car le papier rose résiste mieux aux ombres que la luminosité — et on
  redresse ses quatre coins, (2) on affine avec des correspondances SIFT contraintes à rester à
  moins de 4 % de la largeur de page de leur position redressée, (3) on affine finement avec ECC,
  et (4) on vérifie chaque type de page candidat en corrélant la capture recalée avec tout le
  gabarit vierge. Résultat en développement : 320/320 captures bien classées (les 80 pages × 4
  niveaux — cela a utilisé **les deux** jeux, voir EVALUATION.md §6), erreur de recalage médiane
  1-2 pt, ≈ 0,5 s par page sur CPU. La reprise d'une page donnée est classée comme toute capture,
  et refusée si elle montre une autre page.
* **Cadres de valeur.** 1 à 3 pt d'erreur résiduelle de recalage suffisent pour qu'un recadrage
  attrape la valeur de la ligne voisine ou un en-tête de colonne (exploration : une page
  d'antécédents est passée de 39 % à 100 % une fois corrigé). La valeur est donc localisée à
  partir de l'encre : dans une fenêtre un peu plus haute que le champ, on trouve les bandes
  horizontales d'encre nouvelle (les longues bordures de tableau sont ignorées) et on ne garde
  que celles qui tombent majoritairement dans le champ.
* **Les cases à cocher** sont lues sur les pixels : elles sont dessinées à la main et chacune est
  décalée de 1-2 pt, donc le carré est d'abord localisé par un noyau « carré creux » près de sa
  position attendue, puis on mesure le remplissage de son intérieur. Les cases vides restent
  ≤ 0,09 même sur photos très dégradées, les cases cochées ≥ 0,09 (médiane 0,5-0,8).
* **Les champs vides** sont décidés sur les pixels : de l'encre que le gabarit imprimé
  n'explique pas. Un champ vide ne coûte aucun appel au modèle et vaut `NOT_PROVIDED` sans IA.
* **Lecture** — modèles comparés sur les mêmes recadrages (*exploration*, photos moyennes,
  430 champs manuscrits) :

  | Lecteur | Exactitude | Temps / champ |
  |---|---|---|
  | glm-ocr, recadrage brut | 77,4 % | 0,09 s |
  | glm-ocr, recadrage avec contraste rehaussé | 66,0 % | 0,10 s |
  | **qwen3.5:9b, avec le libellé du champ et le format attendu dans le prompt** | **91,4 %** | 0,8 s |

  qwen3.5 lit aussi l'écriture arabe (glm-ocr non). Le prétraitement (correction d'éclairage,
  suppression des lignes, étirement du contraste) *dégradait* les deux modèles et a été
  abandonné. glm-ocr est gardé comme second lecteur indépendant : l'accord de deux modèles
  différents est un fort signal de justesse, et sa lecture est proposée comme alternative.
* **L'analyse** transforme le texte en valeurs typées canoniques (dates, tension, nombres avec
  unités, chiffres arabes orientaux, vocabulaires FR/EN/AR avec appariement approché). Le même
  analyseur normalise la vérité terrain et les prédictions (nous avons vérifié que les
  modifications ultérieures de l'analyseur laissent la vérité terrain inchangée). Les erreurs
  de lecture systématiques ne sont réparées que si la réparation tombe dans la plage plausible —
  virgule perdue (« 791 » → 79,1 kg), unité « g » lue « 9 » (« 36269 » → 3626 g), barre de
  tension perdue (« 137192 » → 137/92) — et une valeur réparée part toujours en révision.
* **Les règles de cohérence** ([`validators.py`](../src/dayone/extraction/validators.py)) ne
  changent jamais une valeur, elles la signalent : DPA = DDR + 280 j, date de terme dépassé =
  DPA + 7 j, âge gestationnel cohérent avec la date de visite et la DDR, visites dans l'ordre
  chronologique, sauts de poids invraisemblables entre visites, gestité ≥ parité, tension
  invraisemblable, plusieurs coches dans un groupe à choix unique. Aucune interprétation
  clinique (ni score de risque, ni triage : hors périmètre).

## 4. Confiance, et quand poser une question

La confiance propre d'un modèle de vision-langage n'est pas calibrée. La confiance d'un champ
est une régression logistique sur : log-probabilité moyenne et minimale des jetons du lecteur
principal, accord avec le second lecteur (après normalisation), le texte s'analyse-t-il dans le
type attendu, distance à l'entrée de vocabulaire la plus proche, nombre d'alertes de cohérence,
alerte hors plage, quantité d'encre, similarité du recalage, netteté de la capture, écriture
(arabe ou non) et type de valeur. Elle est ajustée sur le jeu de calibration seul
(`make calibrate`) et stockée sous forme de poids JSON (aucun objet sérialisé n'est jamais
chargé).

Le seuil d'acceptation τ est choisi **avant** de regarder le test, par une règle
pré-enregistrée ([EVALUATION.md](EVALUATION.md)) : le plus petit τ dont le taux d'erreurs
silencieuses sur la calibration est ≤ 2 %, calculé sur des confiances hors échantillon (en
laissant une patiente de côté à chaque fois). La sage-femme est interrogée sur un champ quand sa
confiance est sous τ, quand une règle de cohérence l'a signalé, quand il a été réparé, quand il
est illisible, ou quand un tiret / un point d'interrogation a été lu sans confiance. Tout le
reste est résumé (y compris le nombre de champs laissés vides ou marqués non applicables) et
confirmé en bloc.

## 5. Conversation

L'agent ([`device/agent.py`](../src/dayone/device/agent.py)) est une machine à états
déterministe — aucun LLM dans le dialogue — : il tourne hors ligne sur le téléphone, ne peut pas
halluciner, et son comportement se teste. Principes :

* **Aucun changement au travail sur papier** : la sage-femme remplit le registre comme
  aujourd'hui et prend des photos ; l'agent ne lui demande jamais d'écrire autrement. Le code
  qu'elle écrit déjà (« N° de la fiche ») est la clé de liaison.
* **Dire quand on doute, et pourquoi** : « Je lis *109/74*, mais je ne suis pas sûre (confiance
  62 %). Autre lecture possible : 104/74. Pourquoi : deux lectures différentes. » Boutons :
  confirmer, l'alternative, corriger, voir l'image du champ, reprendre la photo. Ce qui est écrit
  est montré à côté de ce qui a été compris (« Lycée » → Secondaire).
* **Un coût de révision proportionnel au doute** : les champs douteux un par un, les champs sûrs
  résumés et confirmés en bloc (avec un détail numéroté pour en corriger un).
* **Saisie manuelle de chaque champ** quand l'IA est indisponible ou que la page n'est pas
  reconnue (réponses spéciales : *vide*, *illisible*, *inconnu*, *-*, *fin*).
* **Sessions multipages** : les pages d'un même registre forment une fiche ; une page
  photographiée deux fois dans une session remplace la photo précédente ; rephotographier un
  registre déjà enregistré montre les différences champ par champ et laisse la sage-femme
  choisir.
* Les boutons respectent les limites de WhatsApp (≤ 3 boutons de réponse, sinon une liste) : le
  même moteur pilote le vrai canal WhatsApp.
* **Dossier continu** : *📁 Dossiers* ou *dossier <code>* affiche le dossier longitudinal d'une
  patiente, construit à partir de chaque visite validée (ni nom, ni identifiant ; aucune
  interprétation clinique).
* Français par défaut, anglais avec *language en* ; valeurs acceptées en FR/EN/AR.

## 6. Hors ligne d'abord

* **Stockage local chiffré** (SQLite, WAL, `synchronous=FULL`) : images, valeurs, index des
  patientes et conversation sont des blocs AES-256-GCM ; la clé est dérivée du PIN de la
  sage-femme avec scrypt ; l'identifiant de la fiche est lié comme donnée associée.
* **File d'attente** : chaque action réseau est une tâche persistante écrite dans la même
  transaction que le changement d'état qui la nécessite. Les tâches sont idempotentes côté
  serveur (identifiant de page ; identifiant de fiche + version) : une requête perdue, une
  réponse perdue ou un crash entre les deux sont rejoués sans risque. Au démarrage, `recover()`
  recrée toute tâche qu'un crash aurait pu empêcher.
* **Cycle de vie explicite** ([LIFECYCLE.md](LIFECYCLE.md)) avec un historique en ajout seul.
* Testé par injection de pannes et par un test à base de propriétés (séquences aléatoires de
  réseau coupé/rétabli, requêtes perdues, réponses perdues, erreurs serveur et crashs de
  l'application) : chaque fiche finit synchronisée ou dans un état d'échec explicite, et aucune
  page ni fiche n'est dupliquée.

## 7. Liaison patiente

Clé : le code écrit sur le registre. Comme un seul chiffre mal lu rattacherait une visite à la
mauvaise femme (l'évaluation finale mesure 85,7 % d'exactitude sur les codes), le code lu par
l'IA est **toujours montré à la sage-femme pour une confirmation en un geste** avant la
recherche (sauf si elle l'a tapé). Il est ensuite comparé après normalisation des confusions
d'OCR (O/0, I/1, S/5, B/8, Z/2), avec une tolérance en distance d'édition. Les candidates sont
notées à l'aide d'attributs non identifiants (âge, DDR, DPA, gestité/parité, province) ; les
conflits sont affichés. Toute candidate notée ≥ 0,35 doit être tranchée par la sage-femme :
**[Patiente 1] [Patiente 2] [Aucune, créer] [Je ne sais pas]** — « Je ne sais pas » met la
fiche de côté (`MANUAL_REVIEW_REQUIRED`) sans rien créer. Les identifiants internes sont des
uuid4 aléatoires. Le serveur signale aussi au superviseur les profils qui partagent un code
entre plusieurs téléphones (doublon possible).

## 8. Confidentialité

* Les identifiants directs ne sont jamais extraits (absents du schéma), et leurs zones sont
  noircies sur la photo **sur le téléphone, avant que la photo soit chiffrée et stockée**.
  L'« image d'origine » conservée est cette photo masquée. Une page dont la mise en page n'est
  pas reconnue n'est pas stockée du tout (ses zones d'identifiants ne peuvent pas être
  localisées) ; la sage-femme peut la saisir à la main.
* Les valeurs en texte libre (lues par l'OCR ou tapées) sont nettoyées des motifs de téléphone
  et de numéros d'identité ; les codes ne sont jamais modifiés.
* Sur le serveur, les images d'origine sont chiffrées au repos et servies selon le rôle : la
  sage-femme qui les a capturées et les superviseurs ; les autres sages-femmes et les
  épidémiologistes reçoivent un 403 ; chaque accès est journalisé.
* Les agrégats épidémiologiques sont calculés par femme (une valeur par femme et par indicateur)
  et toute cellule de moins de 5 femmes est masquée. Le formulaire n'a pas de ligne hépatite C
  (il relève l'Ag HBs, l'hépatite B) : l'hépatite C n'apparaît que via le CSV de référence des
  organisateurs.
* Rôles serveur : seules les sages-femmes envoient des pages et enregistrent des fiches ; les
  valeurs et les images ne sont visibles que par la sage-femme qui a capturé et par les
  superviseurs.
* Toute l'IA tourne en local (Ollama) ; rien n'est envoyé à un tiers. Le canal WhatsApp, qui
  ferait transiter les messages par Meta, est désactivé sauf configuration explicite.

## 9. Hypothèses faites sans réponse des organisateurs

| Question | Hypothèse | Conséquence |
|---|---|---|
| Format du jeu de test caché | Photos de pages ayant la mise en page du spécimen (même formulaire, nouvelles valeurs), éventuellement dégradées | Le recalage sur gabarit est la voie principale ; l'évaluation simule des photos de terrain |
| API cloud autorisées ? | Non : modèles 100 % locaux | qwen3.5:9b + glm-ocr via Ollama |
| « Conserver l'image d'origine » vs « ne jamais stocker les identifiants » | L'original est la photo dont les zones d'identifiants sont noircies sur le téléphone | Les mises en page non reconnues ne sont pas stockées |
| Quel code relie les visites | Le « N° de la fiche » écrit par la sage-femme (demandé s'il manque) | La sage-femme peut le taper pendant la liaison |
| Où tourne l'IA | Une machine de district, joignable quand le téléphone a du réseau | Le téléphone fait tout le reste hors ligne |
