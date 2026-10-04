# Brancher le vrai WhatsApp (bonus — après la validation 100 % locale)

L'agent ne dépend pas du canal : le simulateur dans le navigateur et la WhatsApp Business
Platform (Cloud API) pilotent le même moteur. Le canal est
[`channels/whatsapp.py`](../src/dayone/channels/whatsapp.py) ; il est **désactivé par défaut**.
Il a été testé de bout en bout avec un vrai téléphone le 2026-10-04 : c'est la
[vidéo de démonstration](https://github.com/YoussefChaouki/dayone-codeml/releases/download/v1.0/DayOne-demo-x1.25.mp4).

> ⚠️ Avec le vrai WhatsApp, les messages (et les photos) passent par les serveurs de Meta. À
> utiliser uniquement avec les données synthétiques du spécimen. Les recadrages de champs ne sont
> envoyés que si `DAYONE_WHATSAPP_SEND_IMAGES=1`.

## Ce qui change dans l'architecture

| | Application téléphone simulée (par défaut) | Vrai WhatsApp |
|---|---|---|
| Où tourne l'agent | sur le téléphone | sur une passerelle (serveur de district) |
| Capture hors ligne | oui, stockage chiffré sur le téléphone | WhatsApp garde lui-même les photos en attente jusqu'au retour du réseau |
| Saisie manuelle hors ligne | oui | non (il faut du réseau pour joindre la passerelle) |
| Chiffrement local | stockage du téléphone (clé dérivée du PIN) | stockage de la passerelle ; côté téléphone, celui de WhatsApp |

Le simulateur montre toute la conception « hors ligne d'abord » demandée par le défi ; le canal
WhatsApp montre la même conversation dans l'outil quotidien des sages-femmes.

## Mise en place (numéro de test Meta, environ 20 min)

1. Sur developers.facebook.com : *Créer une app*, cas d'usage **« Se connecter avec les clients
   via WhatsApp »** (le produit WhatsApp est ajouté). Dans *WhatsApp > Configuration de l'API* :
   noter l'**identifiant du numéro de téléphone de test**, cliquer sur *Générer un jeton d'accès*
   (jeton temporaire), et ajouter **son propre numéro WhatsApp** comme destinataire (un code le
   confirme).
2. Dans *Paramètres de l'app > Général* : noter la **clé secrète de l'app**.
3. `cp whatsapp.env.example whatsapp.env` et le remplir (ignoré par git ; ne jamais coller de
   jeton dans une conversation ni dans un commit).
4. Terminal 1 : `make demo-whatsapp` (serveur + téléphone/passerelle avec le canal activé).
   Terminal 2 : `make tunnel` (après `brew install cloudflared`), noter l'URL
   `https://….trycloudflare.com`.
5. Dans *WhatsApp > Configuration > Webhook* : URL de rappel
   `https://….trycloudflare.com/whatsapp/webhook`, jeton de vérification =
   `DAYONE_WHATSAPP_VERIFY_TOKEN`, *Vérifier et enregistrer*, puis s'abonner au champ
   **messages**.
6. **Abonner l'app au compte WhatsApp Business** (sinon Meta n'envoie aucun message au webhook,
   sans erreur visible) : `POST https://graph.facebook.com/v25.0/<WABA_ID>/subscribed_apps` avec le
   jeton d'accès. L'identifiant du compte (WABA) est affiché dans *Configuration de l'API*.
7. Depuis son téléphone, envoyer « menu » au numéro de test. Les photos envoyées dans WhatsApp
   passent par le même pipeline ; les notifications « fiche lue » / « synchronisée » sont
   renvoyées dans WhatsApp ; avec `DAYONE_WHATSAPP_SEND_IMAGES=1`, la vignette masquée de la page
   et les recadrages de champs sont aussi envoyés.

Le numéro de test peut répondre librement pendant 24 h après le dernier message reçu (fenêtre de
service de WhatsApp). Le jeton temporaire expire : le régénérer dans *Configuration de l'API* si
les messages s'arrêtent.

**Sécurité du tunnel** : le tunnel expose le port 8000 sur Internet. Toute requête qui arrive par
le tunnel (en-têtes `cf-ray` / `cf-connecting-ip`) est refusée, sauf `/whatsapp/…` dont chaque
appel est authentifié par la signature `X-Hub-Signature-256` de Meta. Les coulisses de la démo
restent accessibles en local seulement.

Correspondance des messages : ≤ 3 boutons → boutons de réponse interactifs (titres coupés à 20
caractères), 4 à 10 boutons → liste interactive, sinon texte simple. Un texte trop long est
découpé avant les boutons. Les messages des numéros absents de `DAYONE_WHATSAPP_SENDERS` sont
ignorés et jamais stockés ; un message déjà reçu (même identifiant, mémorisé pour les 5 000 derniers
jusqu'au redémarrage de la passerelle) n'est pas retraité ; un bouton
d'un ancien message répond « Ce bouton n'est plus valable ». Vérifié par rapport à la
documentation de la Cloud API (Graph API v25.0) le 2026-10-03 ; couvert par
`tests/test_whatsapp_channel.py` (forme des messages, lecture du webhook, contrôle de signature,
restriction du tunnel) et testé en conditions réelles le 2026-10-04.
