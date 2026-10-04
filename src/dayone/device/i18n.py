"""Conversation strings (French / English). Keys are stable; ``t(lang, key, **kw)`` formats."""

from __future__ import annotations

STRINGS: dict[str, dict[str, str]] = {
    "fr": {
        "welcome": "Bonjour 👋 Je suis l'assistante *DayOne*. Je recopie le registre à partir de photos ; "
                   "c'est vous qui validez. Que voulez-vous faire ?",
        "btn_new": "📷 Nouvelle fiche", "btn_queue": "📋 File d'attente", "btn_manual": "✍️ Saisie manuelle",
        "btn_records": "📁 Dossiers", "btn_show_record": "📁 Voir le dossier",
        "no_patients": "Aucune patiente enregistrée sur ce téléphone pour l'instant.",
        "pick_patient": "📁 Quel dossier ouvrir ? (vous pouvez aussi taper *dossier* suivi du code)",
        "patient_not_found": "Aucun dossier ne correspond au code « {code} ».",
        "btn_review_pending": "🔎 Vérifier ({n})",
        "help": "Commandes : *nouvelle* (photographier un registre), *dossier* [code] (historique d'une patiente), "
                "*file* (état des fiches), *saisie* (saisie manuelle), *vérifier*, *langue en*, *menu*, *annuler*.",
        "capture_start": "📷 Photographiez les pages du registre, *une page par photo*, à plat, sur un fond sombre. "
                         "Touchez *Terminer* quand toutes les pages sont envoyées.",
        "btn_done": "✅ Terminer", "btn_cancel": "Annuler",
        "retake_start": "📷 Envoyez une nouvelle photo de la page *{page}*.",
        "quality_bad": "⚠️ Cette photo risque d'être mal lue :\n{issues}",
        "btn_retake": "📷 Reprendre", "btn_keep": "Garder quand même",
        "page_unknown": "❌ Je ne reconnais pas cette page du registre (ou elle est trop abîmée sur la photo). "
                        "Elle *n'a pas été enregistrée* : je ne peux pas y masquer les données personnelles.",
        "btn_manual_short": "✍️ Saisir à la main",
        "page_ok": "✅ Page reçue : *{page}* ({n} page(s) dans cette fiche).\n🔒 Nom et identifiants masqués, "
                   "photo chiffrée sur le téléphone.",
        "page_replaced": "♻️ Elle remplace la photo précédente de cette page.",
        "wrong_page": "❌ Cette photo montre la page *{found}*, pas la page *{expected}* attendue. Elle n'a pas été "
                      "enregistrée. Envoyez une photo de la page *{expected}*.",
        "no_pages": "Aucune page reçue pour l'instant. Envoyez une photo, ou *Annuler*.",
        "queued_online": "⏳ Fiche enregistrée ({n} page(s)). Lecture automatique en cours… Je vous préviens dès "
                         "que c'est prêt.",
        "queued_offline": "📴 Pas de réseau : la fiche est *enregistrée et chiffrée* sur le téléphone ({n} page(s)), "
                          "état *EN ATTENTE IA*. Elle sera lue automatiquement au retour du réseau. Vous pouvez "
                          "continuer à travailler.",
        "cancelled": "Fiche annulée.",
        "processed": "🤖 La fiche du {date} a été lue : *{total}* champs, dont *{sure}* lus avec confiance et "
                     "*{doubt}* sur lesquels j'ai un doute.",
        "btn_review_now": "🔎 Vérifier maintenant", "btn_later": "Plus tard",
        "btn_retry_ai": "🔁 Relancer la lecture",
        "processing_failed": "⚠️ Je n'ai pas pu lire la fiche du {date} ({reason}). Elle reste enregistrée. "
                             "Vous pouvez la saisir à la main.",
        "review_field": "❓ ({k}/{n}) *{label}* — page {page}\nJe lis : *{value}*, mais je ne suis pas sûre "
                        "(confiance {conf} %).{alts}{why}",
        "review_alts": "\nAutre lecture possible : {alts}",
        "review_illegible": "❓ ({k}/{n}) *{label}* — page {page}\nIl y a quelque chose d'écrit mais je n'arrive "
                            "pas à le lire.",
        "review_box": "❓ ({k}/{n}) Case *{label}* — page {page}\nJe ne suis pas sûre qu'elle soit cochée.",
        "why": "\nPourquoi : {reasons}",
        "btn_confirm_value": "✅ {value}", "btn_edit": "✏️ Corriger", "btn_retake_page": "📷 Reprendre la photo",
        "btn_show": "🖼️ Voir l'image", "btn_blank": "Case vide", "btn_illegible": "Illisible",
        "btn_type_value": "✏️ Saisir la valeur", "btn_ticked": "☑️ Cochée", "btn_unticked": "⬜ Non cochée",
        "ask_value": "Tapez la valeur de *{label}* (ex. {example}).\nVous pouvez aussi écrire *vide*, "
                     "*illisible*, *inconnu* ou *-* (non applicable).",
        "bad_value": "Je n'ai pas compris « {text} » pour *{label}* (attendu : {example}). Réessayez.",
        "saved": "✔️ Noté : *{label}* = {value}",
        "crop_caption": "Voici la zone lue pour *{label}*.",
        "rest_intro": "👍 Plus de doute. Voici ce que j'ai lu avec confiance ({n} champs) :",
        "rest_more": "… et {n} autres champs (*détail* pour tout voir).",
        "rest_empty": "(dont {blank} cases laissées vides et {na} marquées « non applicable », p. ex. par un tiret)",
        "btn_confirm_all": "✅ Tout confirmer", "btn_fix_one": "✏️ Corriger un champ", "btn_detail": "📄 Détail",
        "pick_field": "Tapez le *numéro* du champ à corriger :\n{list}",
        "validated": "✅ Fiche validée ({n} champs).",
        "ask_code": "Quel est le *code patiente* écrit sur le registre ?",
        "confirm_code": "Le code patiente est la clé qui relie les visites. J'ai lu : *{code}*. Est-ce bien le code "
                        "écrit sur le registre ?",
        "btn_code_ok": "✅ C'est bien ça", "btn_code_edit": "✏️ Corriger le code",
        "match_question": "Cette fiche correspond-elle à une patiente déjà suivie ?\n{lines}",
        "match_line": "*Patiente {k}* — code {code} · {age} ans · DPA {edd} · {visits} visite(s)\n   ↳ {reasons}",
        "match_none": "Aucune patiente déjà suivie ne correspond (code {code}).",
        "btn_patient": "Patiente {k}", "btn_none_create": "Aucune, créer", "btn_unsure": "Je ne sais pas",
        "btn_create": "➕ Créer la patiente",
        "match_unsure": "D'accord. La fiche reste sur le téléphone, *à trancher plus tard* (état RÉVISION MANUELLE "
                        "REQUISE). Rien n'est créé.",
        "btn_decide_now": "Décider maintenant",
        "redigit_intro": "♻️ Cette patiente a déjà ces pages dans son dossier. *{n} champ(s) diffèrent* :\n{lines}",
        "redigit_line": "{k}) {label} : {old} → *{new}*",
        "btn_update_all": "Tout mettre à jour", "btn_keep_old": "Garder l'ancien", "btn_one_by_one": "Choisir un par un",
        "redigit_one": "{label} : ancien *{old}*, nouveau *{new}*",
        "btn_take_new": "Prendre le nouveau",
        "registered": "📁 Visite ajoutée au dossier de la patiente (code {code}). {sync}",
        "sync_pending": "☁️ Synchronisation dès que le réseau sera disponible.",
        "sync_now": "☁️ Synchronisation en cours…",
        "synced": "☁️ Fiche synchronisée avec le serveur ✔️",
        "sync_dup": "⚠️ Le serveur signale une autre patiente avec le même code : la superviseure vérifiera.",
        "sync_failed": "⚠️ La synchronisation a échoué ({error}). Je réessaierai automatiquement.",
        "queue_title": "📋 *File d'attente* — réseau : {net}",
        "queue_line": "• {date} · {pages} page(s) · *{state}*{extra}",
        "queue_empty": "Aucune fiche pour l'instant.",
        "net_on": "connecté 📶", "net_off": "hors ligne 📴",
        "manual_pick_page": "✍️ Saisie manuelle : quelle page du registre ?",
        "manual_pick_visit": "Quelle visite saisir sur la page *Grossesse actuelle* ?",
        "manual_q": "({k}/{n}) *{label}* — ex. {example}\n_passer_ = vide · _fin_ = terminer la page",
        "manual_q_box": "({k}/{n}) Case *{label}* cochée ?",
        "btn_yes": "Oui", "btn_no": "Non", "btn_skip": "Passer", "btn_end_page": "Fin de la page",
        "manual_page_done": "Page *{page}* saisie ({n} champs).",
        "btn_other_page": "➕ Autre page", "btn_finish": "✅ Terminer la fiche",
        "nothing_to_review": "Aucune fiche à vérifier pour l'instant.",
        "busy_capture": "Vous êtes en train de photographier une fiche. Envoyez une photo ou touchez *Terminer*.",
        "lang_set": "Langue : français 🇫🇷",
        "photo_lost": "L'application a redémarré : cette photo n'a pas été gardée (elle n'était pas encore masquée). "
                      "Reprenez-la.",
        "stale_button": "Ce bouton n'est plus valable : la conversation a avancé. Voici où nous en sommes.",
        "unknown": "Je n'ai pas compris. Tapez *aide* pour la liste des commandes.",
    },
    "en": {
        "welcome": "Hello 👋 I am the *DayOne* assistant. I copy the registry from photos; you validate. "
                   "What would you like to do?",
        "btn_new": "📷 New record", "btn_queue": "📋 Queue", "btn_manual": "✍️ Manual entry",
        "btn_records": "📁 Patients", "btn_show_record": "📁 Open the record",
        "no_patients": "No patient registered on this phone yet.",
        "pick_patient": "📁 Which record? (you can also type *record* followed by the code)",
        "patient_not_found": "No record matches the code \"{code}\".",
        "btn_review_pending": "🔎 Review ({n})",
        "help": "Commands: *new* (photograph a registry), *record* [code] (a patient's history), *queue* (record "
                "status), *manual* (manual entry), *review*, *language fr*, *menu*, *cancel*.",
        "capture_start": "📷 Photograph the registry pages, *one page per photo*, flat, on a dark background. "
                         "Tap *Done* when all pages are sent.",
        "btn_done": "✅ Done", "btn_cancel": "Cancel",
        "retake_start": "📷 Send a new photo of the page *{page}*.",
        "quality_bad": "⚠️ This photo may be misread:\n{issues}",
        "btn_retake": "📷 Retake", "btn_keep": "Keep anyway",
        "page_unknown": "❌ I do not recognise this registry page (or it is too damaged in the photo). It *was not "
                        "saved*: I cannot mask the personal data on it.",
        "btn_manual_short": "✍️ Type it in",
        "page_ok": "✅ Page received: *{page}* ({n} page(s) in this record).\n🔒 Name and identifiers masked, "
                   "photo encrypted on the phone.",
        "page_replaced": "♻️ It replaces the previous photo of this page.",
        "wrong_page": "❌ This photo shows the page *{found}*, not the expected page *{expected}*. It was not saved. "
                      "Send a photo of the page *{expected}*.",
        "no_pages": "No page received yet. Send a photo, or *Cancel*.",
        "queued_online": "⏳ Record saved ({n} page(s)). Automatic reading in progress… I will tell you when it is ready.",
        "queued_offline": "📴 No network: the record is *saved and encrypted* on the phone ({n} page(s)), state "
                          "*PENDING AI*. It will be read automatically when the network is back. You can keep working.",
        "cancelled": "Record cancelled.",
        "processed": "🤖 The record of {date} has been read: *{total}* fields, *{sure}* read with confidence and "
                     "*{doubt}* I am unsure about.",
        "btn_review_now": "🔎 Review now", "btn_later": "Later",
        "btn_retry_ai": "🔁 Retry reading",
        "processing_failed": "⚠️ I could not read the record of {date} ({reason}). It is still saved. You can type "
                             "it in.",
        "review_field": "❓ ({k}/{n}) *{label}* — page {page}\nI read: *{value}*, but I am not sure "
                        "(confidence {conf} %).{alts}{why}",
        "review_alts": "\nOther possible reading: {alts}",
        "review_illegible": "❓ ({k}/{n}) *{label}* — page {page}\nSomething is written but I cannot read it.",
        "review_box": "❓ ({k}/{n}) Box *{label}* — page {page}\nI am not sure it is ticked.",
        "why": "\nWhy: {reasons}",
        "btn_confirm_value": "✅ {value}", "btn_edit": "✏️ Edit", "btn_retake_page": "📷 Retake photo",
        "btn_show": "🖼️ Show image", "btn_blank": "Blank", "btn_illegible": "Illegible",
        "btn_type_value": "✏️ Type the value", "btn_ticked": "☑️ Ticked", "btn_unticked": "⬜ Not ticked",
        "ask_value": "Type the value of *{label}* (e.g. {example}).\nYou can also write *blank*, *illegible*, "
                     "*unknown* or *-* (not applicable).",
        "bad_value": "I did not understand \"{text}\" for *{label}* (expected: {example}). Try again.",
        "saved": "✔️ Saved: *{label}* = {value}",
        "crop_caption": "Here is the area I read for *{label}*.",
        "rest_intro": "👍 No more doubts. Here is what I read with confidence ({n} fields):",
        "rest_more": "… and {n} more fields (*detail* to see all).",
        "rest_empty": "(including {blank} fields left blank and {na} marked \"not applicable\", e.g. with a dash)",
        "btn_confirm_all": "✅ Confirm all", "btn_fix_one": "✏️ Fix a field", "btn_detail": "📄 Detail",
        "pick_field": "Type the *number* of the field to fix:\n{list}",
        "validated": "✅ Record validated ({n} fields).",
        "ask_code": "What is the *patient code* written on the registry?",
        "confirm_code": "The patient code links the visits together. I read: *{code}*. Is this the code written on "
                        "the registry?",
        "btn_code_ok": "✅ That's right", "btn_code_edit": "✏️ Fix the code",
        "match_question": "Does this record belong to a patient already followed?\n{lines}",
        "match_line": "*Patient {k}* — code {code} · {age} y · EDD {edd} · {visits} visit(s)\n   ↳ {reasons}",
        "match_none": "No patient already followed matches (code {code}).",
        "btn_patient": "Patient {k}", "btn_none_create": "None, create", "btn_unsure": "I'm not sure",
        "btn_create": "➕ Create patient",
        "match_unsure": "OK. The record stays on the phone, *to be decided later* (state MANUAL REVIEW REQUIRED). "
                        "Nothing is created.",
        "btn_decide_now": "Decide now",
        "redigit_intro": "♻️ This patient already has these pages. *{n} field(s) differ*:\n{lines}",
        "redigit_line": "{k}) {label}: {old} → *{new}*",
        "btn_update_all": "Update all", "btn_keep_old": "Keep old", "btn_one_by_one": "One by one",
        "redigit_one": "{label}: old *{old}*, new *{new}*",
        "btn_take_new": "Take new",
        "registered": "📁 Visit added to the patient's record (code {code}). {sync}",
        "sync_pending": "☁️ Will synchronise when the network is available.",
        "sync_now": "☁️ Synchronising…",
        "synced": "☁️ Record synchronised with the server ✔️",
        "sync_dup": "⚠️ The server reports another patient with the same code: the supervisor will check.",
        "sync_failed": "⚠️ Synchronisation failed ({error}). I will retry automatically.",
        "queue_title": "📋 *Queue* — network: {net}",
        "queue_line": "• {date} · {pages} page(s) · *{state}*{extra}",
        "queue_empty": "No record yet.",
        "net_on": "online 📶", "net_off": "offline 📴",
        "manual_pick_page": "✍️ Manual entry: which registry page?",
        "manual_pick_visit": "Which visit of the *Current pregnancy* page?",
        "manual_q": "({k}/{n}) *{label}* — e.g. {example}\n_skip_ = blank · _end_ = finish the page",
        "manual_q_box": "({k}/{n}) Is the box *{label}* ticked?",
        "btn_yes": "Yes", "btn_no": "No", "btn_skip": "Skip", "btn_end_page": "End of page",
        "manual_page_done": "Page *{page}* entered ({n} fields).",
        "btn_other_page": "➕ Another page", "btn_finish": "✅ Finish record",
        "nothing_to_review": "Nothing to review for now.",
        "busy_capture": "You are photographing a record. Send a photo or tap *Done*.",
        "lang_set": "Language: English 🇬🇧",
        "photo_lost": "The app restarted: that photo was not kept (it was not masked yet). Please take it again.",
        "stale_button": "This button is no longer valid: the conversation has moved on. Here is where we are.",
        "unknown": "I did not understand. Type *help* for the list of commands.",
    },
}

REASONS = {
    "fr": {"edd_inconsistent_with_lmp": "DPA incohérente avec la DDR", "post_term_inconsistent_with_edd":
           "date de dépassement incohérente", "gest_age_inconsistent_with_dates": "âge gestationnel incohérent avec "
           "les dates", "visits_out_of_order": "dates de visite dans le désordre", "weight_jump_between_visits":
           "variation de poids inhabituelle", "bp_implausible": "tension inhabituelle", "out_of_range":
           "valeur hors des plages habituelles", "parity_exceeds_gravidity": "parité > gestité",
           "several_options_ticked": "plusieurs cases cochées", "date_unparsed": "date incomplète",
           "number_unparsed": "nombre illisible", "not_in_vocabulary": "mot inattendu",
           "two_readers_disagree": "deux lectures différentes",
           "repaired": "j'ai corrigé une erreur de lecture probable (virgule, unité ou barre)"},
    "en": {"edd_inconsistent_with_lmp": "EDD inconsistent with LMP", "post_term_inconsistent_with_edd":
           "post-term date inconsistent", "gest_age_inconsistent_with_dates": "gestational age inconsistent with "
           "dates", "visits_out_of_order": "visit dates out of order", "weight_jump_between_visits":
           "unusual weight change", "bp_implausible": "unusual blood pressure", "out_of_range":
           "value outside usual range", "parity_exceeds_gravidity": "parity > gravidity",
           "several_options_ticked": "several boxes ticked", "date_unparsed": "incomplete date",
           "number_unparsed": "unreadable number", "not_in_vocabulary": "unexpected word",
           "two_readers_disagree": "two different readings",
           "repaired": "I fixed a likely misreading (decimal point, unit or slash)"},
}

QUALITY = {
    "fr": {"low_resolution": "la page est trop petite dans la photo : rapprochez-vous",
           "blurry": "la photo est floue : tenez le téléphone immobile",
           "too_dark": "la photo est trop sombre : rapprochez-vous de la lumière",
           "glare": "il y a un reflet : inclinez légèrement le téléphone"},
    "en": {"low_resolution": "the page is too small in the photo: move closer",
           "blurry": "the photo is blurred: hold the phone still",
           "too_dark": "the photo is too dark: move closer to the light",
           "glare": "there is glare: tilt the phone slightly"},
}


def t(lang: str, key: str, **kw) -> str:
    table = STRINGS.get(lang, STRINGS["fr"])
    return table.get(key, STRINGS["fr"][key]).format(**kw)
