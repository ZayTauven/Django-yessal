"""Jeux de variables d'exemple, un par courriel.

Servent à DEUX usages qui, séparés, divergeraient aussitôt :

  · `core.tests_templates_mail` rend chaque gabarit avec ces valeurs pour
    vérifier qu'aucune variable ne manque ;
  · `manage.py send_test_email --code <code>` s'en sert pour envoyer un
    aperçu fidèle, coordonnées bancaires et montants compris.

Les valeurs sont volontairement plausibles — « 150 000 » FCFA, un IBAN
sénégalais, un Ndiguel nommé — parce qu'un aperçu rempli de « xxx » ne dit rien
de la mise en page réelle.
"""

# ── Contextes de test, calqués sur docs/EMAILS.md ──────────────────────────
_BASE = {'prenom': 'Bineta', 'daara': 'Daara de Yoff'}
_MEMBRE = 'Bineta Sow'

CONTEXTES = {
    'inscription_recue':       _BASE,
    'compte_valide':           _BASE,
    'compte_bloque':           {**_BASE, 'motif': 'Compte en double'},
    'mot_de_passe_oublie':     {**_BASE, 'duree_validite': '24 heures',
                                'lien_reinitialisation': 'https://yessalgui.sn/reset?t=abc'},
    'mot_de_passe_provisoire': {**_BASE, 'auteur': 'Souleymane Sy'},
    'mot_de_passe_modifie':    {**_BASE, 'date': '3 septembre 2026', 'heure': '01h48'},

    'document_recu':           {**_BASE, 'type_document': "Carte Nationale d'Identité"},
    'document_valide':         {**_BASE, 'type_document': "Carte Nationale d'Identité"},
    'document_a_corriger':     {**_BASE, 'type_document': "Carte Nationale d'Identité",
                                'motif': 'La photo est floue sur le numéro.'},
    'document_a_valider':      {**_BASE, 'membre': _MEMBRE,
                                'type_document': "Carte Nationale d'Identité"},

    'titre_approuve':          {**_BASE, 'titre': 'Serigne', 'note': 'Validé par le bureau.'},
    'titre_refuse':            {**_BASE, 'titre': 'Serigne',
                                'note': 'Merci de joindre une attestation.'},
    'titre_attribue':          {**_BASE, 'titre': 'Serigne', 'auteur': 'Souleymane Sy'},
    'titre_a_examiner':        {**_BASE, 'membre': _MEMBRE, 'titre': 'Serigne'},

    'jef_enregistre':          {**_BASE, 'montant': '25 000', 'campagne': 'Ndiguel Magal 2027',
                                'mode_paiement': 'Wave', 'statut': 'En attente'},
    'jef_a_collecter':         {**_BASE, 'membre': _MEMBRE, 'montant': '25 000',
                                'campagne': 'Ndiguel Magal 2027',
                                'telephone': '+221 77 900 00 01'},
    'virement_instructions':   {**_BASE, 'montant': '150 000', 'campagne': 'Ndiguel Magal 2027',
                                'banque': 'BICIS', 'titulaire': 'Association Yessal Gui',
                                'iban': 'SN28 1234 5678 9012 3456 7890 123',
                                'bic': 'BICISSND', 'reference': 'YG-4821-20260903'},
    'virement_confirme':       {**_BASE, 'montant': '150 000', 'campagne': 'Ndiguel Magal 2027',
                                'date_confirmation': '3 septembre 2026'},
    'paiement_confirme':       {**_BASE, 'montant': '25 000', 'campagne': 'Ndiguel Magal 2027',
                                'mode_paiement': 'Orange Money',
                                'reference': 'don_4821_a3f9c2'},
    'paiement_echoue':         {**_BASE, 'montant': '25 000', 'campagne': 'Ndiguel Magal 2027'},

    'ndiguel_responsable':     {**_BASE, 'campagne': 'Ndiguel Magal 2027',
                                'objectif': '5 000 000', 'echeance': '31 décembre 2026'},
    'ndiguel_echeance':        {**_BASE, 'campagne': 'Ndiguel Magal 2027',
                                'delai': 'dans 5 jours', 'collecte': '3 200 000',
                                'objectif': '5 000 000', 'pourcentage': '64'},
    'fete_date_modifiee':      {**_BASE, 'fete': 'Magal de Touba', 'date': '14 août 2027'},

    'promotion_collecteur':    {**_BASE, 'auteur': 'Souleymane Sy'},
    'invitation_salon':        {**_BASE, 'auteur': 'Souleymane Sy',
                                'salon': 'Collecteurs — Yoff'},
    'annonce':                 {**_BASE, 'titre': 'Fermeture exceptionnelle du bureau',
                                'contenu': 'Le bureau sera fermé lundi 7 septembre.',
                                'urgence': 'critical'},
}
