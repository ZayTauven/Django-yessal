from datetime import date
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase, APIClient

from accounts.models import MemberTitle, TitleRequest, UserDocument, Daara, LDD
from accounts.services.title_service import approve_title_request, refuse_title_request
from comms.models import Notification

User = get_user_model()


class AuthLoginIdentifierTests(APITestCase):
    """Test authentication with email and phone identifiers."""

    def setUp(self):
        self.client = APIClient()
        self.login_url = reverse('login')
        self.user_email = 'john@example.com'
        self.user_phone = '+221771234567'
        self.password = 'TestPassword123!'
        
        self.user = User.objects.create_user(
            email=self.user_email,
            phone=self.user_phone,
            password=self.password,
            first_name='John',
            last_name='Doe',
            role=User.Role.MEMBER,
            status=User.Status.ACTIVE,
        )

    def test_login_with_email(self):
        """Test successful login using email as identifier."""
        payload = {
            'identifier': self.user_email,
            'password': self.password,
        }
        response = self.client.post(self.login_url, payload, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)
        self.assertEqual(response.data['role'], User.Role.MEMBER)

    def test_login_with_phone(self):
        """Test successful login using phone as identifier."""
        payload = {
            'identifier': self.user_phone,
            'password': self.password,
        }
        response = self.client.post(self.login_url, payload, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)

    def test_login_with_wrong_password(self):
        """Test login fails with wrong password."""
        payload = {
            'identifier': self.user_email,
            'password': 'WrongPassword123!',
        }
        response = self.client.post(self.login_url, payload, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_login_with_inactive_user(self):
        """Test login fails for inactive users."""
        self.user.status = User.Status.INACTIVE
        self.user.save()
        
        payload = {
            'identifier': self.user_email,
            'password': self.password,
        }
        response = self.client.post(self.login_url, payload, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_login_with_nonexistent_identifier(self):
        """Test login fails with nonexistent email/phone."""
        payload = {
            'identifier': 'nonexistent@example.com',
            'password': self.password,
        }
        response = self.client.post(self.login_url, payload, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_login_case_insensitive_email(self):
        """Test that email comparison is case-insensitive."""
        payload = {
            'identifier': self.user_email.upper(),
            'password': self.password,
        }
        response = self.client.post(self.login_url, payload, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class TitleRequestApprovalTests(APITestCase):
    """Test title request approval and refusal."""

    def setUp(self):
        self.admin = User.objects.create_user(
            email='admin@test.com',
            password='Admin123!',
            role=User.Role.ADMIN,
            status=User.Status.ACTIVE,
            is_staff=True,
            is_superuser=True,
        )
        self.member = User.objects.create_user(
            email='member@test.com',
            password='Member123!',
            role=User.Role.MEMBER,
            status=User.Status.ACTIVE,
        )
        self.title = MemberTitle.objects.create(
            name='Dignitaire',
            description='Titre de dignité',
            created_by=self.admin,
        )
        self.title_request = TitleRequest.objects.create(
            member=self.member,
            title=self.title,
            status=TitleRequest.Status.PENDING,
        )

    def test_approve_title_request(self):
        """Test approving a title request."""
        result = approve_title_request(self.title_request, self.admin, note='Approuvé')
        
        self.assertEqual(result.status, TitleRequest.Status.APPROVED)
        self.assertEqual(result.reviewed_by, self.admin)
        self.assertIsNotNone(result.reviewed_at)
        
        # Check member title updated
        self.member.refresh_from_db()
        self.assertEqual(self.member.title, self.title)
        self.assertEqual(self.member.title_change_count, 1)
        
        # Check notification created
        notification = Notification.objects.filter(user=self.member).first()
        self.assertIsNotNone(notification)
        self.assertIn('approuvée', notification.title.lower())

    def test_refuse_title_request(self):
        """Test refusing a title request."""
        result = refuse_title_request(self.title_request, self.admin, note='Refusé')
        
        self.assertEqual(result.status, TitleRequest.Status.REFUSED)
        self.assertEqual(result.reviewed_by, self.admin)
        self.assertIsNotNone(result.reviewed_at)
        
        # Check member title NOT updated
        self.member.refresh_from_db()
        self.assertIsNone(self.member.title)
        
        # Check notification created
        notification = Notification.objects.filter(user=self.member).first()
        self.assertIsNotNone(notification)
        self.assertIn('refusée', notification.title.lower())

    def test_approve_title_request_only_once(self):
        """Test that a member can only change title once."""
        # First approval
        approve_title_request(self.title_request, self.admin)
        
        # Create another title request
        title2 = MemberTitle.objects.create(
            name='Dignitaire Senior',
            created_by=self.admin,
        )
        title_request2 = TitleRequest.objects.create(
            member=self.member,
            title=title2,
        )
        
        # Second approval should fail
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            approve_title_request(title_request2, self.admin)


class DocumentValidationTests(APITestCase):
    """Test document validation workflow."""

    def setUp(self):
        self.admin = User.objects.create_user(
            email='admin@test.com',
            password='Admin123!',
            role=User.Role.ADMIN,
            status=User.Status.ACTIVE,
            is_staff=True,
        )
        self.user = User.objects.create_user(
            email='user@test.com',
            password='User123!',
            role=User.Role.MEMBER,
            status=User.Status.ACTIVE,
        )
        self.client.force_authenticate(self.admin)

    def test_document_pending_status_on_creation(self):
        """Test that documents start in PENDING status."""
        from io import BytesIO
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        
        # Create a simple image
        image = Image.new('RGB', (100, 100), color='red')
        image_io = BytesIO()
        image.save(image_io, 'JPEG')
        image_io.seek(0)
        
        image_file = SimpleUploadedFile(
            'test_doc.jpg',
            image_io.read(),
            content_type='image/jpeg'
        )
        
        doc = UserDocument.objects.create(
            user=self.user,
            doc_type=UserDocument.DocType.NATIONAL_ID,
            image=image_file,
            doc_number='12345678',
        )
        
        self.assertEqual(doc.status, UserDocument.ValidationStatus.PENDING)
        self.assertIsNone(doc.validated_by)
        self.assertIsNone(doc.validated_at)

    def test_document_validation(self):
        """Test validating a document."""
        from io import BytesIO
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        
        image = Image.new('RGB', (100, 100), color='red')
        image_io = BytesIO()
        image.save(image_io, 'JPEG')
        image_io.seek(0)
        
        image_file = SimpleUploadedFile(
            'test_doc.jpg',
            image_io.read(),
            content_type='image/jpeg'
        )
        
        doc = UserDocument.objects.create(
            user=self.user,
            doc_type=UserDocument.DocType.NATIONAL_ID,
            image=image_file,
            doc_number='12345678',
        )
        
        # Validate the document
        doc.status = UserDocument.ValidationStatus.VALIDATED
        doc.validated_by = self.admin
        from django.utils import timezone
        doc.validated_at = timezone.now()
        doc.save()
        
        doc.refresh_from_db()
        self.assertEqual(doc.status, UserDocument.ValidationStatus.VALIDATED)
        self.assertEqual(doc.validated_by, self.admin)
        self.assertIsNotNone(doc.validated_at)

    def test_document_rejection_with_note(self):
        """Test rejecting a document with a rejection note."""
        from io import BytesIO
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        
        image = Image.new('RGB', (100, 100), color='red')
        image_io = BytesIO()
        image.save(image_io, 'JPEG')
        image_io.seek(0)
        
        image_file = SimpleUploadedFile(
            'test_doc.jpg',
            image_io.read(),
            content_type='image/jpeg'
        )
        
        doc = UserDocument.objects.create(
            user=self.user,
            doc_type=UserDocument.DocType.NATIONAL_ID,
            image=image_file,
            doc_number='12345678',
        )
        
        # Reject the document
        doc.status = UserDocument.ValidationStatus.REJECTED
        doc.validated_by = self.admin
        doc.rejection_note = 'Document insuffisamment lisible'
        from django.utils import timezone
        doc.validated_at = timezone.now()
        doc.save()
        
        doc.refresh_from_db()
        self.assertEqual(doc.status, UserDocument.ValidationStatus.REJECTED)
        self.assertEqual(doc.rejection_note, 'Document insuffisamment lisible')


class DaaraListFilterTests(APITestCase):
    """La liste des Daaras servie au formulaire d'inscription.

    Deux défauts trouvés le 2026-09-05, tous deux invisibles à la lecture :

      · `?ldd_id=` n'était lu NULLE PART — ni `filter_backends`, ni
        `filterset_fields`, ni `get_queryset`. Choisir sa localité renvoyait les
        376 Daaras de la confrérie au lieu des cinq de la zone, et un membre
        pouvait rattacher son compte à un Daara de l'autre bout du pays.
      · `get_queryset` était DÉFINIE DEUX FOIS dans `DaaraViewSet` : la seconde
        écrasait silencieusement la première, dont le docstring décrivait donc
        du code mort.
    """

    def setUp(self):
        self.client = APIClient()
        self.ldd_a = LDD.objects.create(code='DS S1', name='Zone A')
        self.ldd_b = LDD.objects.create(code='DS S2', name='Zone B')
        self.a1 = Daara.objects.create(name='Daara A1', ldd=self.ldd_a)
        self.a2 = Daara.objects.create(name='Daara A2', ldd=self.ldd_a)
        self.ferme = Daara.objects.create(
            name='Daara A3 fermé', ldd=self.ldd_a, is_active=False
        )
        self.b1 = Daara.objects.create(name='Daara B1', ldd=self.ldd_b)

    def _ids(self, response):
        data = response.data
        rows = data if isinstance(data, list) else data.get('results', [])
        return {row['id'] for row in rows}

    def test_filtre_par_ldd(self):
        """Seuls les Daaras de la LDD demandée reviennent."""
        response = self.client.get('/api/daara/', {'ldd_id': self.ldd_a.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self._ids(response), {self.a1.id, self.a2.id})

    def test_aucun_daara_d_une_autre_ldd(self):
        """C'est le cœur du défaut : un Daara voisin ne doit pas fuiter."""
        response = self.client.get('/api/daara/', {'ldd_id': self.ldd_b.id})
        self.assertNotIn(self.a1.id, self._ids(response))
        self.assertEqual(self._ids(response), {self.b1.id})

    def test_anonyme_ne_voit_pas_les_daaras_fermes(self):
        response = self.client.get('/api/daara/', {'ldd_id': self.ldd_a.id})
        self.assertNotIn(self.ferme.id, self._ids(response))

    def test_administrateur_voit_les_daaras_fermes(self):
        """Un administrateur doit pouvoir rouvrir ce qu'il a fermé."""
        admin = User.objects.create_user(
            email='admin@example.com', password='TestPassword123!',
            role=User.Role.ADMIN, status=User.Status.ACTIVE, is_staff=True,
        )
        self.client.force_authenticate(user=admin)
        response = self.client.get('/api/daara/', {'ldd_id': self.ldd_a.id})
        self.assertIn(self.ferme.id, self._ids(response))

    def test_ldd_id_illisible_ne_rend_rien(self):
        """Une liste vide se voit et se corrige ; une liste complète passe pour
        un résultat."""
        response = self.client.get('/api/daara/', {'ldd_id': 'abc'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self._ids(response), set())

    def test_ldd_id_inconnu_ne_rend_rien(self):
        response = self.client.get('/api/daara/', {'ldd_id': 999999})
        self.assertEqual(self._ids(response), set())

    def test_sans_ldd_id_la_liste_reste_entiere(self):
        """Le paramètre est facultatif : sans lui, rien ne change."""
        response = self.client.get('/api/daara/')
        self.assertEqual(self._ids(response), {self.a1.id, self.a2.id, self.b1.id})


class RegistrationFeedbackTests(APITestCase):
    """Ce que l'inscription RÉPOND — le retour au membre, pas seulement le compte.

    Trois défauts trouvés le 2026-09-05 en éprouvant le parcours :

      · le message d'unicité de l'adresse était celui du modèle Django (« Un
        objet user avec ce champ adresse électronique existe déjà »), qui parle
        d'un « objet user » et ne dit pas quoi faire ;
      · `first_name` et `last_name` étaient FACULTATIFS côté API — le modèle les
        déclare `blank=True` pour l'administration, DRF en déduisait un champ
        optionnel, et un compte pouvait naître sans nom. Seul le formulaire
        mobile l'empêchait, c'est-à-dire personne ;
      · le compte est créé en `pending` et rien ne le disait à l'inscrit.
    """

    def setUp(self):
        self.client = APIClient()
        self.ldd = LDD.objects.create(code='DS S9', name='Zone test')
        self.daara = Daara.objects.create(name='Daara test', ldd=self.ldd)
        self.url = '/api/auth/register/'

    def _payload(self, **overrides):
        base = {
            'first_name': 'Awa',
            'last_name': 'Ndiaye',
            'email': 'awa@example.com',
            'password': 'TestPassword123!',
            'daara_id': self.daara.id,
        }
        base.update(overrides)
        return base

    def test_compte_cree_en_attente_de_validation(self):
        """L'écran de confirmation promet une validation : elle doit être vraie."""
        response = self.client.post(self.url, self._payload(), format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        membre = User.objects.get(email='awa@example.com')
        self.assertEqual(membre.status, User.Status.PENDING)
        self.assertEqual(membre.daara, self.daara)

    def test_adresse_deja_prise_dit_quoi_faire(self):
        self.client.post(self.url, self._payload(), format='json')
        response = self.client.post(self.url, self._payload(), format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        message = response.data['email'][0]
        self.assertIn('existe déjà', message)
        self.assertNotIn('objet user', message)

    def test_prenom_vide_refuse(self):
        response = self.client.post(self.url, self._payload(first_name=''), format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('prénom', response.data['first_name'][0].lower())
        self.assertFalse(User.objects.filter(email='awa@example.com').exists())

    def test_nom_absent_refuse(self):
        payload = self._payload()
        payload.pop('last_name')
        response = self.client.post(self.url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('nom', response.data['last_name'][0].lower())

    def test_numero_deja_pris_dit_quoi_faire(self):
        User.objects.create_user(
            email=None, phone='+221781112233', password='TestPassword123!',
            first_name='Deja', last_name='La', daara=self.daara,
        )
        payload = self._payload(email=None, phone='+221 78 111 22 33')
        response = self.client.post(self.url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('déjà associé', response.data['phone'][0])

    def test_inscription_par_numero_international(self):
        """Le sélecteur d'indicatif compose l'E.164 : il doit passer tel quel."""
        payload = self._payload(email=None, phone='+33612345678')
        response = self.client.post(self.url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(User.objects.get(last_name='Ndiaye').phone, '+33612345678')
