"""Tests sur la normalisation des numéros de téléphone.

Le numéro est un identifiant de connexion, et sa forme n'était pas contrainte :
un compte créé avec « +221 77 000 00 00 » — l'exemple que proposait le
formulaire, espaces compris — n'était jamais retrouvé par l'écran de connexion,
qui émet « +221770000000 ». Le compte existait ; son propriétaire restait
dehors.

Ce qui se joue ici, dans l'ordre où ça compte :

  · une saisie, quelle qu'en soit la ponctuation, ne donne qu'UNE valeur en
    base — et pas une valeur sénégalaise en dur : une grande partie des membres
    vit à l'étranger ;
  · la connexion retrouve le compte quelle que soit la façon dont le numéro est
    tapé, et continue de marcher par adresse e-mail ;
  · deux écritures du même numéro se heurtent à un MESSAGE, pas à une 500 ;
  · la migration des lignes existantes ne perd aucun compte, même quand elle ne
    peut pas trancher.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import LDD, Daara
from core.phone import (
    looks_like_phone,
    normalize_phone,
    normalize_phone_quietly,
    phone_digits,
)

User = get_user_model()

SANS_LIMITE = override_settings(
    # Les quotas de débit fausseraient les tests : plusieurs connexions
    # successives sont ici la norme, pas un abus.
    REST_FRAMEWORK={
        'DEFAULT_AUTHENTICATION_CLASSES': (
            'accounts.authentication.VersionedJWTAuthentication',
        ),
        'DEFAULT_PERMISSION_CLASSES': (
            'rest_framework.permissions.IsAuthenticated',
        ),
    },
)


class NormalisationTests(TestCase):
    """La fonction seule, sans base ni requête."""

    def test_les_ecritures_d_un_meme_numero_convergent(self):
        """Le cœur du problème : cinq saisies, une seule valeur stockée."""
        for saisie in (
            '+221770000000',
            '+221 77 000 00 00',
            '77 000 00 00',
            '0770000000',
            '00221770000000',
            '(221) 77-000-00-00',
            '  +221 77.000.00.00  ',
        ):
            with self.subTest(saisie=saisie):
                self.assertEqual(normalize_phone(saisie), '+221770000000')

    def test_un_numero_etranger_garde_son_indicatif(self):
        """La contrainte donnée : les membres de la diaspora doivent entrer.

        Rien ici ne doit ramener un numéro français vers +221.
        """
        self.assertEqual(normalize_phone('+33 6 12 34 56 78'), '+33612345678')
        self.assertEqual(normalize_phone('0033612345678'), '+33612345678')
        self.assertEqual(normalize_phone('+1 (212) 555-0147'), '+12125550147')

    def test_les_espaces_insecables_sont_traites(self):
        """Un copier-coller depuis Word ou un iPhone en dépose sans le dire.

        Écrites en points de code : une insécable est invisible dans un
        éditeur, et ce test ne prouverait plus rien si elle se perdait.
        """
        self.assertEqual(
            normalize_phone('+221\u00a077\u202f000\u00a000\u00a000'),
            '+221770000000',
        )

    def test_une_saisie_vide_vaut_pas_de_numero(self):
        """None et non chaîne vide : deux chaînes vides se heurteraient sur
        la contrainte d'unicité dès le deuxième compte sans numéro."""
        for vide in (None, '', '   ', '\t'):
            with self.subTest(vide=repr(vide)):
                self.assertIsNone(normalize_phone(vide))

    def test_une_saisie_inexploitable_est_refusee(self):
        for saisie in ('à demander', '+221 77 abc 00 00', '+0221770000',
                       '+2217700000000000000', '12'):
            with self.subTest(saisie=saisie):
                with self.assertRaises(ValidationError):
                    normalize_phone(saisie)

    def test_le_refus_est_muet_quand_on_le_demande(self):
        self.assertIsNone(normalize_phone_quietly('à demander'))
        self.assertEqual(normalize_phone_quietly('77 000 00 00'), '+221770000000')

    @override_settings(DEFAULT_PHONE_REGION='+33')
    def test_la_region_par_defaut_est_un_reglage(self):
        """+221 est un défaut, pas une règle gravée dans le code."""
        self.assertEqual(normalize_phone('06 12 34 56 78'), '+33612345678')
        # Un numéro qui porte son indicatif ne bouge pas pour autant.
        self.assertEqual(normalize_phone('+221 77 000 00 00'), '+221770000000')

    @override_settings(DEFAULT_PHONE_REGION='221')
    def test_la_region_par_defaut_tolere_l_absence_de_plus(self):
        self.assertEqual(normalize_phone('77 000 00 00'), '+221770000000')

    @override_settings(DEFAULT_PHONE_REGION='Sénégal')
    def test_une_region_par_defaut_absurde_se_signale(self):
        """Une erreur de configuration, pas une saisie : elle doit crier."""
        with self.assertRaises(ImproperlyConfigured):
            normalize_phone('77 000 00 00')

    def test_la_forme_tient_dans_le_champ(self):
        """`User.phone` est un CharField(max_length=20) — vérifié, pas supposé."""
        longueur_champ = User._meta.get_field('phone').max_length
        plus_long = normalize_phone('+' + '9' * 15)
        self.assertEqual(len(plus_long), 16)
        self.assertLessEqual(len(plus_long), longueur_champ)

    def test_une_adresse_n_est_pas_un_numero(self):
        """L'écran de connexion accepte les deux dans le même champ : il faut
        savoir à quoi on a affaire AVANT de normaliser."""
        self.assertFalse(looks_like_phone('bineta@example.com'))
        self.assertFalse(looks_like_phone('Bineta'))
        self.assertTrue(looks_like_phone('+221 77 000 00 00'))
        self.assertTrue(looks_like_phone('770000000'))

    def test_le_fragment_de_recherche_ne_garde_que_les_chiffres(self):
        self.assertEqual(phone_digits('77-000'), '77000')
        self.assertIsNone(phone_digits('Bineta'))


class EcritureEnBaseTests(TestCase):
    """`User.save()` — le filet de sécurité derrière les sérialiseurs."""

    def test_la_creation_normalise(self):
        membre = User.objects.create_user(
            phone='+221 77 000 00 01', password='Test1234!',
            role=User.Role.MEMBER,
        )
        membre.refresh_from_db()
        self.assertEqual(membre.phone, '+221770000001')

    def test_la_mise_a_jour_normalise(self):
        membre = User.objects.create_user(
            email='bineta@example.com', password='Test1234!',
            role=User.Role.MEMBER,
        )
        membre.phone = '77 000 00 02'
        membre.save()
        membre.refresh_from_db()
        self.assertEqual(membre.phone, '+221770000002')

    def test_un_numero_illisible_ne_fait_pas_echouer_l_enregistrement(self):
        """Le filet de sécurité rattrape, il ne casse pas.

        Lever ici empêcherait `revoke_sessions()` ou la mise à jour de
        `last_login` d'aboutir sur un compte hérité — c'est-à-dire empêcherait
        son propriétaire de se connecter, pour un champ sans rapport.
        """
        membre = User.objects.create_user(
            email='moussa@example.com', password='Test1234!',
            role=User.Role.MEMBER,
        )
        membre.phone = 'à demander'
        membre.save()
        membre.refresh_from_db()
        self.assertEqual(membre.phone, 'à demander')


class AdministrationDjangoTests(TestCase):
    """Le formulaire de `/admin/` — l'autre porte d'écriture.

    `save()` normalise trop tard pour lui : le formulaire contrôle l'unicité
    sur ce qui est SAISI. Sans normalisation dans le formulaire, un doublon
    écrit autrement passait le contrôle et se heurtait à l'INSERT.

    Les formulaires sont instanciés avec le seul champ qui nous intéresse : les
    autres manqueront, et leurs erreurs ne nous regardent pas ici.
    """

    def formulaire(self, numero):
        from accounts.admin import UserAdminForm

        form = UserAdminForm(data={'phone': numero})
        form.is_valid()
        return form

    def test_le_numero_saisi_est_normalise(self):
        form = self.formulaire('+221 77 000 00 00')

        self.assertEqual(form.cleaned_data.get('phone'), '+221770000000')

    def test_un_doublon_ecrit_autrement_est_vu_par_le_formulaire(self):
        """Une erreur de champ, pas une page 500."""
        User.objects.create_user(phone='+221770000000', password='Test1234!')

        form = self.formulaire('+221 77 000 00 00')

        self.assertIn('phone', form.errors)


@SANS_LIMITE
class InscriptionTests(APITestCase):
    def setUp(self):
        self.url = reverse('register')
        ldd = LDD.objects.create(code='DKR', name='Dakar')
        self.daara = Daara.objects.create(name='Daara Test', ldd=ldd)

    def corps(self, **surcharges):
        corps = {
            'first_name': 'Bineta',
            'last_name': 'Fall',
            'password': 'MotDePasseSolide2026!',
            'daara_id': self.daara.id,
        }
        corps.update(surcharges)
        return corps

    def test_un_numero_espace_est_stocke_en_e164(self):
        res = self.client.post(
            self.url, self.corps(phone='+221 77 000 00 00'), format='json'
        )

        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(User.objects.get().phone, '+221770000000')

    def test_un_numero_sans_indicatif_recoit_celui_par_defaut(self):
        res = self.client.post(
            self.url, self.corps(phone='77 000 00 03'), format='json'
        )

        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(User.objects.get().phone, '+221770000003')

    def test_un_numero_inexploitable_est_refuse_lisiblement(self):
        res = self.client.post(
            self.url, self.corps(phone='pas un numéro'), format='json'
        )

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('phone', res.data)

    def test_l_inscription_par_email_seul_reste_possible(self):
        """Le numéro est facultatif : rien ne doit exiger sa présence."""
        res = self.client.post(
            self.url, self.corps(email='bineta@example.com'), format='json'
        )

        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(User.objects.get().phone)

    def test_deux_comptes_sans_numero_cohabitent(self):
        """La chaîne vide se heurterait à elle-même sur l'unicité ; NULL non."""
        for adresse in ('a@example.com', 'b@example.com'):
            res = self.client.post(
                self.url, self.corps(email=adresse, phone=''), format='json'
            )
            self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)

        self.assertEqual(User.objects.filter(phone=None).count(), 2)

    def test_le_meme_numero_autrement_ecrit_donne_un_message_pas_une_500(self):
        """C'est tout l'intérêt de normaliser DANS le champ.

        Le contrôle d'unicité de DRF s'applique à ce que renvoie le champ. S'il
        s'appliquait à la saisie brute, ces deux écritures le franchiraient
        toutes deux, puis se heurteraient à l'INSERT : une erreur 500.
        """
        premier = self.client.post(
            self.url, self.corps(phone='+221770000004'), format='json'
        )
        self.assertEqual(premier.status_code, status.HTTP_201_CREATED)

        second = self.client.post(
            self.url, self.corps(phone='+221 77 000 00 04'), format='json'
        )
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('phone', second.data)
        self.assertEqual(User.objects.count(), 1)


@SANS_LIMITE
class ConnexionTests(APITestCase):
    def setUp(self):
        self.url = reverse('login')
        self.mot_de_passe = 'MotDePasseSolide2026!'
        self.membre = User.objects.create_user(
            email='bineta@example.com',
            phone='+221770000000',
            password=self.mot_de_passe,
            role=User.Role.MEMBER,
            status=User.Status.ACTIVE,
        )

    def connexion(self, identifiant, mot_de_passe=None):
        return self.client.post(
            self.url,
            {'identifier': identifiant,
             'password': mot_de_passe or self.mot_de_passe},
            format='json',
        )

    def test_connexion_avec_le_numero_tel_qu_il_est_stocke(self):
        self.assertEqual(self.connexion('+221770000000').status_code, status.HTTP_200_OK)

    def test_connexion_avec_le_numero_saisi_avec_des_espaces(self):
        """Le cas qui échouait : le formulaire propose cette forme-là."""
        self.assertEqual(
            self.connexion('+221 77 000 00 00').status_code, status.HTTP_200_OK
        )

    def test_connexion_avec_le_numero_sans_indicatif(self):
        self.assertEqual(self.connexion('77 000 00 00').status_code, status.HTTP_200_OK)

    def test_connexion_par_email_toujours_possible(self):
        """Le compte administrateur est créé par adresse : elle doit marcher.

        C'est aussi ce qui justifiait le lien « J'ai une adresse e-mail » sur
        l'écran mobile — il ne doit rien casser en disparaissant.
        """
        self.assertEqual(
            self.connexion('bineta@example.com').status_code, status.HTTP_200_OK
        )
        self.assertEqual(
            self.connexion('BINETA@EXAMPLE.COM').status_code, status.HTTP_200_OK
        )

    def test_une_saisie_illisible_ne_dit_pas_pourquoi(self):
        """Sur un écran de connexion, expliquer le refus renseigne surtout
        celui qui cherche des comptes."""
        res = self.connexion('pas un numéro')

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Identifiants invalides.', str(res.data))

    def test_un_numero_inconnu_donne_le_meme_message_qu_un_mot_de_passe_faux(self):
        inconnu = str(self.connexion('+221 77 999 99 99').data)
        faux = str(self.connexion('+221770000000', 'MauvaisMotDePasse!').data)

        self.assertIn('Identifiants invalides.', inconnu)
        self.assertEqual(inconnu, faux)

    def test_un_compte_laisse_non_normalise_peut_encore_entrer(self):
        """Le filet tendu sous les collisions que la migration n'a pas tranchées.

        `update()` court-circuite `save()` — c'est exactement ce que fait la
        migration quand elle renonce.
        """
        User.objects.filter(pk=self.membre.pk).update(phone='+221 77 000 00 00')

        self.assertEqual(
            self.connexion('+221 77 000 00 00').status_code, status.HTTP_200_OK
        )


class MigrationDeDonneesTests(TestCase):
    """La fonction de la migration 0013, appelée directement.

    On la teste sur le modèle réel plutôt que par un `migrate` : ce qui est en
    jeu n'est pas la mécanique de Django, c'est ce que fait la fonction quand
    deux comptes revendiquent le même numéro.
    """

    def executer(self):
        # `import_module` et non `import` : un nom de module commençant par un
        # chiffre n'est pas un identifiant Python valide.
        from importlib import import_module

        from django.apps import apps

        module = import_module(
            'accounts.migrations.0013_normalisation_telephones_e164'
        )
        # `apps` réel plutôt que l'état historique : le modèle `User` n'a pas
        # changé de forme entre 0012 et 0013, et ce qu'on éprouve ici est la
        # décision prise en cas de collision, pas la mécanique de Django.
        module.normaliser_les_numeros(apps, None)

    def planter(self, valeur, **champs):
        """Écrit un numéro brut en base, en contournant `save()`.

        `update()` sur un queryset ne passe pas par le modèle : c'est la seule
        façon de reproduire une ligne écrite avant la normalisation.
        """
        membre = User.objects.create_user(password='Test1234!', **champs)
        User.objects.filter(pk=membre.pk).update(phone=valeur)
        return membre

    def test_les_lignes_anciennes_sont_mises_en_forme(self):
        membre = self.planter('+221 77 000 00 00', email='a@example.com')

        self.executer()

        membre.refresh_from_db()
        self.assertEqual(membre.phone, '+221770000000')

    def test_une_collision_laisse_les_deux_comptes_en_place(self):
        """Deux comptes, un seul numéro : la machine ne tranche pas.

        Fusionner ou supprimer engagerait un historique de dons et des
        adhésions. La migration signale et passe.
        """
        deja_propre = self.planter('+221770000000', email='a@example.com')
        en_conflit = self.planter('+221 77 000 00 00', email='b@example.com')

        with self.assertLogs('accounts.migrations', level='WARNING') as journal:
            self.executer()

        deja_propre.refresh_from_db()
        en_conflit.refresh_from_db()
        self.assertEqual(deja_propre.phone, '+221770000000')
        self.assertEqual(en_conflit.phone, '+221 77 000 00 00')
        self.assertEqual(User.objects.count(), 2)

        # L'avertissement doit nommer de quoi trancher à la main.
        trace = '\n'.join(journal.output)
        self.assertIn('+221770000000', trace)
        self.assertIn(str(deja_propre.pk), trace)
        self.assertIn(str(en_conflit.pk), trace)

    def test_un_numero_illisible_est_signale_et_conserve(self):
        membre = self.planter('à demander', email='a@example.com')

        with self.assertLogs('accounts.migrations', level='WARNING') as journal:
            self.executer()

        membre.refresh_from_db()
        self.assertEqual(membre.phone, 'à demander')
        self.assertIn('illisible', '\n'.join(journal.output))

    def test_repasser_la_migration_ne_change_rien(self):
        """Elle doit être rejouable : un déploiement se reprend parfois."""
        membre = self.planter('77 000 00 00', email='a@example.com')

        self.executer()
        membre.refresh_from_db()
        premier_passage = membre.phone

        self.executer()
        membre.refresh_from_db()
        self.assertEqual(membre.phone, premier_passage)
        self.assertEqual(membre.phone, '+221770000000')
