# Catalogue des courriels — Yessal Gui

Ce document liste **tous** les courriels que la plateforme sera amenée à
envoyer, avec pour chacun : le déclencheur réel dans le code, le destinataire,
l'objet, un corps de départ, et les variables disponibles dans le gabarit.

Chaque entrée porte un **code**. Ce code désigne trois choses à la fois :

| | |
|---|---|
| l'objet | `SUJETS['<code>']` dans [`core/mail.py`](../core/mail.py) |
| le gabarit HTML | `templates/emails/<code>.html` |
| le gabarit texte | `templates/emails/<code>.txt` *(facultatif)* |

**Nommage des fichiers.** Les gabarits portent le préfixe du catalogue —
`A1-inscription_recue.html` — pour rester triés par section dans
l'explorateur. Le module résout les deux formes : `<code>.html` comme
`<préfixe>-<code>.html`. Renommer les fichiers pour satisfaire le code aurait
sacrifié un classement utile à une contrainte technique.

**Sans `.txt`**, la version texte est dérivée du HTML : les liens sont
conservés sous la forme « libellé (URL) », et les `<span display:block>` —
que les gabarits utilisent comme sur-titres, faute de `<div>` fiable en
courriel — sont traités comme des blocs. Le résultat garde le sens, pas la
forme. Un vrai `.txt` reste préférable.

L'appel se réduit alors à :

```python
from core.mail import send_to_user

send_to_user(membre, 'document_valide', {'document': doc})
```

---

## Trois contraintes à garder en tête avant d'écrire un gabarit

**1. Une partie des membres n'a pas d'adresse e-mail.**
`User.email` est `null=True` : on s'inscrit au choix par e-mail **ou** par
téléphone, et les comptes créés sur le terrain par un collecteur n'ont souvent
qu'un numéro. Le courriel **double** la notification en base
(`comms.Notification`) et la notification push — il ne les remplace jamais.
Aucune information indispensable ne doit exister uniquement par e-mail.

**2. Le sujet est dans `core/mail.py`, pas dans le gabarit.**
Vingt sujets côte à côte se relisent d'un coup d'œil ; dispersés dans vingt
fichiers, ils divergent. Les sujets acceptent des variables : `{campagne}`,
`{fete}`, `{titre}`.

**3. Chaque gabarit est un document autonome.**
Pas d'héritage, pas de coque partagée : chaque fichier porte son `<!DOCTYPE>`,
son en-tête et son pied. C'est plus verbeux qu'un `{% extends %}`, et c'est le
bon choix ici — un courriel se relit isolément, et sa mise en page par
`<table>` avec styles en ligne supporte mal l'indirection.

Contraintes du HTML de courriel, qui expliquent la verbosité : mise en page par
`<table>`, styles **en ligne** sur chaque balise (pas de feuille), largeur fixe
de 600 px, couleurs en hexadécimal littéral. Outlook rend encore via le moteur
de Word — ni flexbox, ni grid, ni variables CSS.

**Variables présentes partout**, sans avoir à les passer : `user`, `prenom`,
`base_url`, `illustrations`, `nom_expediteur`, `annee`.

**Images.** Une boîte mail ne résout aucun chemin relatif : `src="illustrations/gift.png"`
ne pointe nulle part une fois le message ouvert. Les images vivent donc dans
`static/emails/illustrations/` (servies par WhiteNoise) et se référencent
`src="{{ illustrations }}/gift.png"`.

> ⚠️ **En test local, les images resteront vides.** Gmail relaie les images par
> ses propres serveurs, qui n'atteindront jamais un `localhost:8000`. Ce n'est
> pas un défaut du gabarit — il faut que le backend soit publiquement joignable.

---

## Légende de la colonne « déclencheur »

- **● existe** — le point d'accroche est déjà dans le code, il suffit d'y
  ajouter l'appel `send_to_user`.
- **○ à créer** — aucun déclencheur n'existe aujourd'hui ; il faut l'écrire.

---

# A. Compte et accès

## A1 · `inscription_recue` ● existe

**Déclencheur** `RegisterView` — [`accounts/views.py:50`](../accounts/views.py#L50)
**Destinataire** le nouvel inscrit
**Objet** `Votre inscription à Yessal Gui a bien été reçue`

> Bonjour {prenom},
>
> Votre demande d'inscription à Yessal Gui a bien été enregistrée. Elle est
> maintenant soumise à la validation d'un administrateur de votre Daara.
>
> **Votre Daara :** {daara}
>
> Vous recevrez un message dès que votre compte sera actif. En attendant, vous
> n'avez rien à faire.
>
> *Si vous n'êtes pas à l'origine de cette demande, ignorez ce message.*

**Variables** `user`, `prenom`, `daara` (peut être absent), `base_url`

---

## A2 · `compte_valide` ● existe

**Déclencheur** `UserManagementViewSet.validate` — [`accounts/views.py:869`](../accounts/views.py#L869)
**Destinataire** le membre validé
**Objet** `Votre compte Yessal Gui est actif`

> Bonjour {prenom},
>
> Votre compte a été validé : vous pouvez dès maintenant vous connecter,
> suivre les Ndiguels de votre Daara et enregistrer vos Jëfs.
>
> [**Accéder à mon tableau de bord**]
>
> Pensez à compléter votre profil et à déposer votre pièce d'identité : c'est
> ce qui permet à votre Daara de vous rattacher avec certitude.

**Variables** `user`, `prenom`, `daara`, `base_url`

---

## A3 · `compte_bloque` ● existe

**Déclencheur** `UserManagementViewSet.block` — [`accounts/views.py:876`](../accounts/views.py#L876)
**Destinataire** le membre bloqué
**Objet** `Votre accès à Yessal Gui a été suspendu`

> Bonjour {prenom},
>
> L'accès à votre compte Yessal Gui a été suspendu par un administrateur.
>
> Vos Jëfs déjà enregistrés sont conservés et restent comptabilisés. Si vous
> pensez qu'il s'agit d'une erreur, rapprochez-vous du chef de votre Daara.

**Variables** `user`, `prenom`, `daara`, `motif` (optionnel — champ à ajouter)

> ⚠️ Le ton compte ici. Un blocage est souvent administratif (doublon, compte
> de test), rarement une sanction. Le message ne doit pas accuser.

---

## A4 · `mot_de_passe_oublie` ● existe *(mais la vue est un stub)*

**Déclencheur** `ForgotPasswordView` — [`accounts/views.py:892`](../accounts/views.py#L892)
**Destinataire** le titulaire du compte
**Objet** `Réinitialisez votre mot de passe Yessal Gui`

> ✅ **Gabarit écrit** :
> [`A4-mot_de_passe_oublie.html`](../templates/emails/A4-mot_de_passe_oublie.html)

**Variables** `user`, `prenom`, `lien_reinitialisation`, `duree_validite`

> ⚠️ **La vue actuelle ne fait rien** : elle répond « un email a été envoyé »
> sans générer de jeton ni envoyer quoi que ce soit, et **la page
> `/reset-password` n'existe pas côté front**. C'est le seul courriel de la
> liste qui demande aussi du travail hors backend. Voir « Ce qu'il reste à
> construire » en fin de document.

---

## A5 · `mot_de_passe_provisoire` ● existe

**Déclencheur** `UserSerializer.update()` quand un tiers pose un mot de passe —
appelé depuis `resetMemberPasswordAction`
([`front-web/src/app/actions/users.ts:531`](../../front-web/src/app/actions/users.ts#L531))
**Destinataire** le membre concerné
**Objet** `Un mot de passe provisoire vous a été attribué`

> Bonjour {prenom},
>
> {auteur} vous a attribué un nouveau mot de passe provisoire pour votre compte
> Yessal Gui.
>
> **Ce mot de passe vous a été communiqué de vive voix. Il ne figure pas dans
> ce message, volontairement.**
>
> [**Me connecter et choisir mon mot de passe**]
>
> À votre prochaine connexion, il vous sera demandé d'en choisir un nouveau,
> connu de vous seul.

**Variables** `user`, `prenom`, `auteur` (nom de l'administrateur), `base_url`

> ⚠️ **Ne jamais mettre le mot de passe dans le corps du message.** Il est déjà
> dicté de vive voix ; l'écrire dans un e-mail le rend indéfiniment lisible par
> quiconque accède à la boîte.

---

## A6 · `mot_de_passe_modifie` ● existe

**Déclencheur** `ChangePasswordView` — [`accounts/views.py:102`](../accounts/views.py#L102)
**Destinataire** le titulaire du compte
**Objet** `Votre mot de passe Yessal Gui a été modifié`

> Bonjour {prenom},
>
> Le mot de passe de votre compte Yessal Gui vient d'être modifié, le
> {date} à {heure}.
>
> **Toutes vos autres sessions ont été fermées.** Si vous étiez connecté sur un
> autre téléphone ou un autre ordinateur, il faudra vous y reconnecter.
>
> Si vous n'êtes pas à l'origine de ce changement, contactez immédiatement un
> administrateur : votre compte est peut-être compromis.

**Variables** `user`, `prenom`, `date`, `heure`

> C'est un courriel de **sécurité** : il part même quand tout va bien, parce
> que c'est précisément le cas où il alerte.

---

# B. Documents d'identité

## B1 · `document_recu` ● existe

**Déclencheur** `UserDocumentViewSet.perform_create` — [`accounts/views.py:482`](../accounts/views.py#L482)
**Destinataire** le membre qui dépose
**Objet** `Votre document a bien été reçu`

> Bonjour {prenom},
>
> Votre {type_document} a bien été reçu. Un administrateur va le vérifier.
>
> Vous serez prévenu(e) du résultat. Ce contrôle prend généralement quelques
> jours.

**Variables** `user`, `prenom`, `document`, `type_document`

---

## B2 · `document_valide` ● existe

**Déclencheur** `DocumentValidationView.patch`, statut `validated` — [`accounts/views.py:514`](../accounts/views.py#L514)
**Destinataire** le membre
**Objet** `Votre document a été validé`

> Bonjour {prenom},
>
> Votre {type_document} a été validé. Votre profil est désormais complet.

**Variables** `user`, `prenom`, `document`, `type_document`

---

## B3 · `document_a_corriger` ● existe

**Déclencheur** `DocumentValidationView.patch`, statut `rejected` — [`accounts/views.py:514`](../accounts/views.py#L514)
**Destinataire** le membre
**Objet** `Votre document doit être corrigé`

> Bonjour {prenom},
>
> Votre {type_document} n'a pas pu être validé.
>
> **Motif :** {motif}
>
> [**Déposer un nouveau document**]
>
> Une photo nette, prise à plat et bien éclairée, suffit le plus souvent.

**Variables** `user`, `prenom`, `document`, `type_document`, `motif`
(`document.rejection_note`), `base_url`

> Le motif est **obligatoire** dans ce message : sans lui, le membre redépose
> la même photo et le cycle recommence.

---

## B4 · `document_a_valider` ● existe

**Déclencheur** `_notify_admins_document_submission` — [`accounts/views.py:496`](../accounts/views.py#L496)
**Destinataire** tous les administrateurs
**Objet** `Un document attend votre validation`

> {membre} ({daara}) a déposé un {type_document}.
>
> [**Examiner le document**]

**Variables** `membre`, `daara`, `type_document`, `document`, `base_url`

> Courriel **interne**. À regrouper si le volume monte (voir « Volume » en fin
> de document) : dix dépôts dans l'heure ne doivent pas faire dix messages.

---

# C. Titres

## C1 · `titre_approuve` ● existe

**Déclencheur** `approve_title_request` — [`accounts/services/title_service.py:10`](../accounts/services/title_service.py#L10)
**Destinataire** le membre demandeur
**Objet** `Votre demande de titre a été approuvée`

> Bonjour {prenom},
>
> Votre demande a été approuvée. Vous portez désormais le titre de
> **{titre}**.
>
> {note}
>
> Rappel : le changement de titre n'est possible qu'une fois. Celui-ci est
> définitif.

**Variables** `user`, `prenom`, `titre`, `note` (optionnelle)

---

## C2 · `titre_refuse` ● existe

**Déclencheur** `refuse_title_request` — [`accounts/services/title_service.py:37`](../accounts/services/title_service.py#L37)
**Destinataire** le membre demandeur
**Objet** `Votre demande de titre n'a pas été retenue`

> Bonjour {prenom},
>
> Votre demande pour le titre de **{titre}** n'a pas été retenue.
>
> **Note de l'administrateur :** {note}
>
> Votre unique possibilité de changement de titre **n'a pas été consommée** :
> vous pouvez faire une nouvelle demande.

**Variables** `user`, `prenom`, `titre`, `note`

> La dernière phrase est importante : le refus n'incrémente pas
> `title_change_count`, et le membre n'a aucun moyen de le savoir autrement.

---

## C3 · `titre_attribue` ● existe

**Déclencheur** `MemberAssignTitleView` — [`accounts/views.py:533`](../accounts/views.py#L533)
**Destinataire** le membre
**Objet** `Un titre vous a été attribué`

> Bonjour {prenom},
>
> Un administrateur vous a attribué le titre de **{titre}**.

**Variables** `user`, `prenom`, `titre`, `auteur`

---

## C4 · `titre_a_examiner` ● existe

**Déclencheur** `TitleRequestViewSet.perform_create` — [`accounts/views.py:439`](../accounts/views.py#L439)
**Destinataire** tous les administrateurs
**Objet** `Une demande de titre attend votre examen`

> {membre} ({daara}) demande le titre de **{titre}**.
>
> [**Examiner la demande**]

**Variables** `membre`, `daara`, `titre`, `base_url`

---

# D. Jëfs (dons)

## D1 · `jef_enregistre` ○ à créer

**Déclencheur** à ajouter dans `DonationViewSet.perform_create`
**Destinataire** le donateur
**Objet** `Votre Jëf a bien été enregistré`

> Bonjour {prenom},
>
> Votre Jëf de **{montant} FCFA** pour le Ndiguel *{campagne}* a bien été
> enregistré.
>
> **Mode de règlement :** {mode_paiement}
> **Statut :** {statut}
>
> [**Voir mes Jëfs**]

**Variables** `user`, `prenom`, `donation`, `montant`, `campagne`,
`mode_paiement`, `statut`, `base_url`

---

## D2 · `jef_a_collecter` ● existe

**Déclencheur** `notify_for_collector_payment` — [`contributions/signals.py:10`](../contributions/signals.py#L10)
**Destinataire** collecteurs et chef du Daara, plus les administrateurs
**Objet** `Un Jëf est à collecter`

> **{membre}** a enregistré un Jëf de **{montant} FCFA** pour le Ndiguel
> *{campagne}*, à régler en espèces.
>
> **Daara :** {daara}
> **Téléphone :** {telephone}
>
> [**Voir la collecte**]

**Variables** `membre`, `montant`, `campagne`, `daara`, `telephone`, `base_url`

> Courriel **interne**, destiné à faire agir. Le téléphone y a sa place : le
> collecteur appelle avant de se déplacer.

---

## D3 · `virement_instructions` ○ à créer

**Déclencheur** à ajouter là où `payment_status` passe à `pending_wire`
**Destinataire** le donateur
**Objet** `Comment effectuer votre virement`

> Bonjour {prenom},
>
> Voici les coordonnées pour votre Jëf de **{montant} FCFA** au profit du
> Ndiguel *{campagne}*.
>
> | | |
> |---|---|
> | Banque | {banque} |
> | Titulaire | {titulaire} |
> | IBAN | `{iban}` |
> | BIC | `{bic}` |
> | **Référence à indiquer** | **`{reference}`** |
>
> **La référence est indispensable.** Sans elle, votre virement ne peut pas
> être rattaché à votre Jëf, et il restera en attente.
>
> Le rapprochement est fait manuellement par un administrateur : comptez
> quelques jours après réception.

**Variables** `user`, `prenom`, `donation`, `montant`, `campagne`,
`banque`, `titulaire`, `iban`, `bic`, `reference` (`donation.wire_reference`)

> Les coordonnées viennent de `settings.BANK_ACCOUNT`, alimenté par les
> variables `BANK_*`. C'est le courriel le plus consulté de la liste : il sera
> rouvert plusieurs fois, parfois depuis un téléphone, devant un guichet.
> Privilégier des blocs copiables et une référence bien visible.

---

## D4 · `virement_confirme` ● existe

**Déclencheur** `confirm_wire` — [`contributions/views.py:156`](../contributions/views.py#L156)
**Destinataire** le donateur
**Objet** `Votre virement a été confirmé`

> Bonjour {prenom},
>
> Nous avons bien reçu votre virement de **{montant} FCFA**. Votre Jëf pour le
> Ndiguel *{campagne}* est confirmé.
>
> Jërëjëf.

**Variables** `user`, `prenom`, `donation`, `montant`, `campagne`, `date_confirmation`

---

## D5 · `paiement_confirme` ● existe

**Déclencheur** `bictorys_webhook`, statut `succeeded` / `authorized` — [`contributions/views_webhooks.py`](../contributions/views_webhooks.py)
**Destinataire** le donateur
**Objet** `Votre paiement a été confirmé`

> Bonjour {prenom},
>
> Votre paiement de **{montant} FCFA** par {mode_paiement} a été confirmé.
> Votre Jëf pour le Ndiguel *{campagne}* est enregistré.
>
> **Référence :** {reference}
>
> Jërëjëf.

**Variables** `user`, `prenom`, `donation`, `montant`, `campagne`,
`mode_paiement`, `reference`

> Fait office de **reçu**. Le montant, la date et la référence doivent y
> figurer, c'est ce qu'on rouvre six mois plus tard.

---

## D6 · `paiement_echoue` ● existe

**Déclencheur** `bictorys_webhook`, statut `failed` — [`contributions/views_webhooks.py`](../contributions/views_webhooks.py)
**Destinataire** le donateur
**Objet** `Votre paiement n'a pas abouti`

> Bonjour {prenom},
>
> Votre paiement de **{montant} FCFA** pour le Ndiguel *{campagne}* n'a pas
> abouti. **Aucune somme n'a été prélevée.**
>
> [**Réessayer**]
>
> Si le problème persiste, essayez un autre mode de règlement, ou remettez
> votre Jëf en main propre à un collecteur de votre Daara.

**Variables** `user`, `prenom`, `donation`, `montant`, `campagne`, `base_url`

> « Aucune somme n'a été prélevée » est la phrase que le membre cherche. Elle
> passe avant tout le reste.

---

# E. Ndiguels et fêtes

## E1 · `ndiguel_responsable` ● existe

**Déclencheur** `CampaignViewSet._notify_organizer` — [`events/views.py:172`](../events/views.py#L172)
**Destinataire** l'organisateur désigné
**Objet** `Vous êtes responsable du Ndiguel {campagne}`

> Bonjour {prenom},
>
> Vous avez été désigné(e) responsable du Ndiguel **{campagne}**.
>
> **Objectif :** {objectif} FCFA
> **Échéance :** {echeance}
>
> [**Ouvrir l'espace d'organisation**]
>
> Vos privilèges de gestion restent actifs jusqu'à l'échéance.

**Variables** `user`, `prenom`, `campaign`, `campagne`, `objectif`,
`echeance`, `base_url`

---

## E2 · `ndiguel_echeance` ○ à créer

**Déclencheur** aucun — demande une **tâche planifiée** (voir fin de document)
**Destinataire** l'organisateur, ou tous les membres concernés
**Objet** `Le Ndiguel {campagne} se termine bientôt`

> Bonjour {prenom},
>
> Le Ndiguel **{campagne}** se termine {delai}.
>
> **Collecté :** {collecte} FCFA sur {objectif} FCFA ({pourcentage} %)
>
> [**Participer**]

**Variables** `user`, `prenom`, `campagne`, `delai`, `collecte`, `objectif`,
`pourcentage`, `base_url`

---

## E3 · `fete_date_modifiee` ● existe

**Déclencheur** `FeteViewSet.perform_update`, quand la date change — [`events/views.py:57`](../events/views.py#L57)
**Destinataire** tous les membres actifs *(envoi de masse)*
**Objet** `Nouvelle date pour {fete}`

> Bonjour {prenom},
>
> La date de **{fete}** a été fixée au **{date}**.
>
> [**Voir les Ndiguels liés**]

**Variables** `user`, `prenom`, `fete`, `date`, `base_url`

> ⚠️ Seul courriel de **masse** de la liste — il part à tous les membres.
> Passer par `send_to_users`, jamais par une boucle sur `send_to_user` : la
> requête de l'administrateur qui déplace la date attendrait sinon plusieurs
> centaines d'envois.

---

# F. Communauté

## F1 · `promotion_collecteur` ● existe

**Déclencheur** `promote_collector` — [`accounts/views.py:377`](../accounts/views.py#L377)
**Destinataire** le membre promu
**Objet** `Vous êtes désormais collecteur`

> Bonjour {prenom},
>
> {auteur} vous a nommé(e) collecteur pour le Daara de **{daara}**.
>
> Vous pouvez désormais enregistrer les Jëfs remis en espèces par les membres
> et suivre les collectes en cours.
>
> [**Ouvrir la collecte physique**]

**Variables** `user`, `prenom`, `auteur`, `daara`, `base_url`

---

## F2 · `invitation_salon` ● existe

**Déclencheur** `ChatInvitationViewSet.perform_create` — [`comms/views.py:246`](../comms/views.py#L246)
**Destinataire** l'invité
**Objet** `Vous êtes invité(e) dans un salon de discussion`

> Bonjour {prenom},
>
> {auteur} vous invite à rejoindre le salon **{salon}**.
>
> [**Voir l'invitation**]

**Variables** `user`, `prenom`, `auteur`, `salon`, `base_url`

> Candidat au **regroupement** : un membre invité dans cinq salons le même jour
> ne doit pas recevoir cinq messages.

---

## F3 · `annonce` ● existe

**Déclencheur** `AnnouncementViewSet.perform_create` — [`comms/views.py:512`](../comms/views.py#L512)
**Destinataire** selon `target` / `target_role` / `daara` *(envoi de masse)*
**Objet** `{titre}`

> {contenu}
>
> [**Voir dans Yessal Gui**]

**Variables** `user`, `prenom`, `titre`, `contenu`, `urgence`, `base_url`

> ⚠️ **N'envoyer par courriel que les annonces `urgency = critical`.** Les
> annonces `info` sont nombreuses et déjà visibles dans le Hub ; les envoyer
> toutes est le plus court chemin vers le dossier « indésirables », et vers un
> désabonnement de masse qui emporterait aussi les messages qui comptent.

---

# Ce qu'il reste à construire

Six chantiers, par ordre de dépendance :

**1. `ForgotPasswordView` est une coquille vide** — [`accounts/views.py:892`](../accounts/views.py#L892)
Elle répond « un email a été envoyé » sans générer de jeton ni envoyer quoi que
ce soit. Il manque : la génération d'un jeton
(`django.contrib.auth.tokens.default_token_generator`), l'envoi, un endpoint de
validation `POST /api/auth/reset-password/`, **et une page `/reset-password`
côté front, qui n'existe pas**. C'est le seul courriel qui demande du travail
hors backend.

**2. Une préférence de désabonnement.**
Le pied de page renvoie vers `/dashboard/profile`, mais aucun champ n'y règle
les courriels. Sans ce réglage, la seule sortie d'un membre agacé est de
signaler le message comme indésirable — ce qui abîme la réputation du domaine
pour tout le monde.

**3. Une vraie file d'attente.**
`send_to_users` travaille dans un fil non supervisé. Cela tient pour un rappel
de date ; cela ne tiendra pas pour des reçus de paiement, ni au-delà de
quelques milliers de destinataires. Celery + Redis, le jour venu.

**4. Le regroupement des courriels internes.**
`document_a_valider`, `titre_a_examiner` et `invitation_salon` partent un par
un. À volume réel, un administrateur en recevra des dizaines par jour et
cessera de les lire. Un résumé quotidien vaudra mieux.

**5. Les tâches planifiées.**
`ndiguel_echeance` n'a aucun déclencheur possible aujourd'hui : il faut une
commande lancée par `cron`.

**6. Domaine d'envoi et délivrabilité.**
Le compte Gmail personnel convient pour les essais. En production, il faudra le
domaine de l'organisation avec SPF, DKIM et DMARC — sans quoi les messages
partiront en indésirables, en particulier chez Gmail et Outlook, qui l'exigent
désormais pour tout envoi en volume.

---

# Configuration

## Variables d'environnement

À renseigner dans `yessal-backend/core/.env.local` (développement) ou dans
l'environnement du service (production). Modèle complet et commenté :
[`core/.env.example`](../core/.env.example).

| Variable | Test (Gmail perso) | Production |
|---|---|---|
| `EMAIL_HOST` | `smtp.gmail.com` | SMTP de l'organisation |
| `EMAIL_PORT` | `587` | `587` |
| `EMAIL_USE_TLS` | `True` | `True` |
| `EMAIL_HOST_USER` | votre adresse Gmail | `no-reply@yessalgui.sn` |
| `EMAIL_HOST_PASSWORD` | **mot de passe d'application** (voir ci-dessous) | — |
| `DEFAULT_FROM_EMAIL` | votre adresse Gmail | `no-reply@yessalgui.sn` |
| `EMAIL_FROM_NAME` | `Yessal Gui` | `Yessal Gui` |
| `EMAIL_REPLY_TO` | votre adresse | `contact@yessalgui.sn` |
| `EMAIL_ENABLED` | `True` | `True` |
| `BASE_URL` — le **front** | `http://localhost:3100` | `https://yessalgui.sn` |
| `EMAIL_ASSETS_URL` — le **backend** | `http://localhost:8000/static/emails/illustrations` | `https://api.yessalgui.sn/static/emails/illustrations` |

`EMAIL_HOST` vide fait basculer Django sur le backend **console** : les
messages s'affichent dans le terminal au lieu de partir. C'est le défaut, et
c'est voulu — un développeur qui clone le dépôt n'envoie rien à personne par
inadvertance.

`EMAIL_ENABLED=False` est un coupe-circuit distinct : le code s'exécute
normalement et journalise ce qui *aurait* été envoyé. À utiliser dès qu'on
travaille sur une copie de la base de production.

**`BASE_URL` et `EMAIL_ASSETS_URL` ne désignent pas la même machine.** Le
premier est le front — c'est lui que suivent les membres depuis un courriel
(`/dashboard`, `/login`). Le second est le backend, qui sert les images. Les
confondre donne soit des liens morts, soit des images absentes.

**Où sont lus les réglages.** `decouple` remonte l'arborescence en cherchant un
fichier nommé `.env` : livré à lui-même, il trouve celui de la RACINE du dépôt
et ignore `core/.env.local`. En conteneur l'illusion tenait — docker-compose
injecte le fichier par `env_file:` — mais en local, aucun réglage n'arrivait.
`settings.py` charge donc `core/.env.local` explicitement, l'environnement
gardant la priorité.

## Gmail : mot de passe d'application obligatoire

Google refuse depuis 2022 le mot de passe du compte pour SMTP. Il faut :

1. activer la **validation en deux étapes** sur le compte ;
2. créer un mot de passe d'application sur
   <https://myaccount.google.com/apppasswords> ;
3. reporter les **16 caractères** obtenus dans `EMAIL_HOST_PASSWORD`
   (les espaces affichés par Google peuvent être retirés).

Sans cela, l'envoi échoue sur `535 Username and Password not accepted`.

**Limite Gmail :** environ 500 destinataires par jour. Suffisant pour les
essais, insuffisant dès la première `fete_date_modifiee` en production.

## Vérifier la configuration

```bash
# Message de diagnostic — n'exige aucun gabarit
python manage.py send_test_email vous@exemple.com

# Envoi d'un vrai gabarit, avec des valeurs d'exemple
python manage.py send_test_email vous@exemple.com --code mot_de_passe_oublie
```

La commande affiche la configuration effective **avant** d'envoyer, et traduit
les deux erreurs SMTP les plus fréquentes (mot de passe refusé, port bloqué) en
message actionnable.
