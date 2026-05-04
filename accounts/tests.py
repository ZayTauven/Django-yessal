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
