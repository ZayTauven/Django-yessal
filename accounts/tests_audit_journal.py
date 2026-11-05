"""Le journal d'audit se lit en français, sans cacher ce qu'il ne sait pas nommer."""

import re

from django.contrib.auth import get_user_model
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import get_resolver
from django.urls.resolvers import URLResolver
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import AuditLog, MemberTitle
from accounts.services.audit_presentation import _find_rule
from news.models import NewsPost

User = get_user_model()

# Ces routes s'appellent sans être connecté : le middleware ne les journalise pas.
ANONYMOUS_ROUTES = {
    '/api/auth/register/',
    '/api/auth/login/',
    '/api/auth/refresh/',
    '/api/auth/forgot-password/',
    '/api/auth/reset-password/',
}


def _mutating_routes():
    def walk(patterns, prefix=''):
        for pattern in patterns:
            if isinstance(pattern, URLResolver):
                yield from walk(pattern.url_patterns, prefix + str(pattern.pattern))
            else:
                yield prefix + str(pattern.pattern), pattern.callback

    for route, callback in walk(get_resolver().url_patterns):
        if not route.startswith('api/') or 'format' in route:
            continue
        actions = getattr(callback, 'actions', None)
        view_class = getattr(callback, 'view_class', None)
        for method in ('post', 'put', 'patch', 'delete'):
            if (method in actions) if actions else (view_class and hasattr(view_class, method)):
                path = re.sub(r'<[^>]+>|\(\?P<[^>]+>[^)]+\)', '1', route)
                yield method.upper(), '/' + path.replace('^', '').replace('$', '')


class JournalBase(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email='admin@test.sn', password='Motdepasse123!', role=User.Role.ADMIN,
            status=User.Status.ACTIVE, is_staff=True, first_name='Awa', last_name='Sall',
        )
        self.membre = User.objects.create_user(
            email='membre@test.sn', password='Motdepasse123!', role=User.Role.MEMBER,
            status=User.Status.ACTIVE, first_name='Moussa', last_name='Diop',
        )
        self.client.force_authenticate(self.admin)

    def _journal(self):
        res = self.client.get('/api/audit/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        return res.json()

    def _log(self, action, **kwargs):
        return AuditLog.objects.create(user=self.admin, action=action, **kwargs)


class TraductionTests(JournalBase):
    def test_une_requete_devient_une_phrase_avec_sa_cible(self):
        self._log(f'POST /api/users/{self.membre.pk}/block/')

        [entree] = self._journal()

        self.assertEqual(entree['label'], 'Compte bloqué')
        self.assertEqual(entree['domain'], 'membres')
        self.assertEqual(entree['target_label'], 'Moussa Diop')
        self.assertEqual(entree['target_href'], f'/dashboard/users/{self.membre.pk}')
        self.assertEqual(entree['tone'], 'warning')
        self.assertEqual(entree['actor_name'], 'Awa Sall')
        self.assertFalse(entree['routine'])

    def test_l_entree_brute_reste_intacte(self):
        self._log(f'POST /api/users/{self.membre.pk}/block/', description='brut')

        [entree] = self._journal()

        self.assertEqual(entree['action'], f'POST /api/users/{self.membre.pk}/block/')
        self.assertEqual(entree['description'], 'brut')

    def test_les_gestes_courants_sont_marques_routiniers(self):
        for action in ('POST /api/comms/messages/', 'POST /api/comms/fcm-token/', 'PATCH /api/profile/'):
            self._log(action)

        self.assertTrue(all(e['routine'] for e in self._journal()))

    def test_une_route_inconnue_reste_visible(self):
        self._log('POST /api/nouveau-module/secret/')

        [entree] = self._journal()

        self.assertFalse(entree['routine'])
        self.assertEqual(entree['domain'], 'autre')
        self.assertEqual(entree['target_label'], '/api/nouveau-module/secret/')

    def test_une_action_nommee_garde_sa_description(self):
        self._log('collecte_suspendue', description='Suspension (3 demande(s) annulée(s))')

        [entree] = self._journal()

        self.assertEqual(entree['label'], 'Collecte en espèces suspendue')
        self.assertEqual(entree['domain'], 'reglages')
        self.assertEqual(entree['detail'], 'Suspension (3 demande(s) annulée(s))')

    def test_une_suppression_est_signalee(self):
        self._log(f'DELETE /api/users/{self.membre.pk}/')

        self.assertEqual(self._journal()[0]['tone'], 'danger')

    def test_un_auteur_supprime_n_est_pas_le_systeme(self):
        AuditLog.objects.create(user=None, action='PATCH /api/profile/')

        [entree] = self._journal()

        self.assertIsNone(entree['actor_name'])
        self.assertIsNone(entree['actor_contact'])

    def test_toute_route_d_ecriture_a_son_libelle(self):
        """Une route ajoutée sans libellé s'afficherait en « Action enregistrée »."""
        routes = list(_mutating_routes())
        self.assertGreater(len(routes), 100)  # le parcours des routes a bien marché

        oublis = [
            f'{method} {path}'
            for method, path in routes
            if path not in ANONYMOUS_ROUTES and _find_rule(method, path)[0] is None
        ]
        self.assertEqual(oublis, [])


class CibleCreeeTests(JournalBase):
    def test_une_creation_nomme_ce_qu_elle_a_cree(self):
        res = self.client.post('/api/titles/', {'name': 'Jawriñ'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        entree = next(e for e in self._journal() if e['action'] == 'POST /api/titles/')
        self.assertEqual(entree['label'], 'Titre créé')
        self.assertEqual(entree['target_label'], 'Jawriñ')
        self.assertEqual(entree['entity_id'], res.json()['id'])

    def test_une_ancienne_creation_reste_sans_cible(self):
        self._log('POST /api/titles/')

        self.assertIsNone(self._journal()[0]['target_label'])

    def test_une_actualite_se_retrouve_par_son_numero_comme_par_son_slug(self):
        post = NewsPost.objects.create(title='Le forage est lancé', content='<p>x</p>')
        self._log('POST /api/news/posts/', entity_id=post.pk)
        self._log(f'PATCH /api/news/posts/{post.slug}/')

        for entree in self._journal():
            self.assertEqual(entree['target_label'], 'Le forage est lancé')
            self.assertEqual(entree['target_href'], f'/dashboard/news/{post.slug}')


class CibleSupprimeeTests(JournalBase):
    def test_le_nom_est_releve_avant_la_suppression(self):
        titre = MemberTitle.objects.create(name='Diawrigne')

        res = self.client.delete(f'/api/titles/{titre.pk}/')
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)

        entree = next(e for e in self._journal() if e['action'].startswith('DELETE'))
        self.assertEqual(entree['label'], 'Titre supprimé')
        self.assertEqual(entree['target_label'], 'Diawrigne')
        self.assertIsNone(entree['target_href'])

    def test_une_ancienne_entree_n_a_plus_que_son_numero(self):
        self._log('DELETE /api/users/99999/')

        [entree] = self._journal()

        self.assertEqual(entree['target_label'], 'Compte n°99999 (n’existe plus)')


class AccesEtPerformanceTests(JournalBase):
    def test_reserve_aux_administrateurs(self):
        self.client.force_authenticate(self.membre)

        self.assertEqual(self.client.get('/api/audit/').status_code, status.HTTP_403_FORBIDDEN)

    def test_le_nombre_de_requetes_ne_depend_pas_du_nombre_de_lignes(self):
        self._log(f'POST /api/users/{self.membre.pk}/validate/')
        with CaptureQueriesContext(connection) as peu:
            self._journal()

        for _ in range(20):
            self._log(f'POST /api/users/{self.membre.pk}/validate/')
        with CaptureQueriesContext(connection) as beaucoup:
            self._journal()

        self.assertEqual(len(peu), len(beaucoup))
