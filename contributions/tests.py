from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from contributions.models import Donation, DonationArchive
from events.models import Campaign, Fete
from accounts.models import Daara, LDD

User = get_user_model()


class DonationCreationTests(APITestCase):
    """Test donation creation and payment workflow."""

    def setUp(self):
        self.member = User.objects.create_user(
            email='member@test.com',
            password='Member123!',
            role=User.Role.MEMBER,
            status=User.Status.ACTIVE,
        )
        self.collector = User.objects.create_user(
            email='collector@test.com',
            password='Collector123!',
            role=User.Role.COLLECTOR,
            status=User.Status.ACTIVE,
        )
        self.ldd = LDD.objects.create(code='SEN', name='Senegal')
        self.daara = Daara.objects.create(name='Test Daara', ldd=self.ldd)
        self.fete = Fete.objects.create(
            name='Tabaski',
            date=timezone.now().date(),
            recurrence=Fete.Recurrence.ANNUAL,
        )
        self.campaign = Campaign.objects.create(
            name='Ndiguel Tabaski',
            description='Ndiguel for Tabaski',
            deadline=timezone.now().date(),
            fete=self.fete,
            daara=self.daara,
            created_by=self.member,
        )
        self.client.force_authenticate(self.member)

    def test_create_donation_by_member(self):
        """Test creating a donation as a member."""
        url = reverse('donations-list')
        payload = {
            'campaign': self.campaign.id,
            'donor': self.member.id,
            'amount': 5000.00,
            'is_anonymous': False,
        }
        response = self.client.post(url, payload, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        
        donation = Donation.objects.get(id=response.data['id'])
        self.assertEqual(donation.donor, self.member)
        self.assertEqual(donation.campaign, self.campaign)
        self.assertEqual(donation.amount, 5000.00)
        self.assertEqual(donation.payment_status, Donation.PaymentStatus.PENDING)

    def test_create_donation_by_collector(self):
        """Test creating a donation as a collector with manual payment."""
        self.client.force_authenticate(self.collector)
        
        url = reverse('donations-list')
        payload = {
            'campaign': self.campaign.id,
            'donor': self.member.id,
            'amount': 10000.00,
            'payment_method': Donation.PaymentMethod.MANUAL,
        }
        response = self.client.post(url, payload, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        
        donation = Donation.objects.get(id=response.data['id'])
        self.assertEqual(donation.donor, self.member)
        self.assertEqual(donation.collector, self.collector)
        self.assertEqual(donation.amount, 10000.00)


class DonationPaymentTests(APITestCase):
    """Test donation payment transitions."""

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
        self.ldd = LDD.objects.create(code='SEN', name='Senegal')
        self.daara = Daara.objects.create(name='Test Daara', ldd=self.ldd)
        self.fete = Fete.objects.create(
            name='Tabaski',
            date=timezone.now().date(),
            recurrence=Fete.Recurrence.ANNUAL,
        )
        self.campaign = Campaign.objects.create(
            name='Ndiguel Tabaski',
            deadline=timezone.now().date(),
            fete=self.fete,
            daara=self.daara,
            created_by=self.member,
        )
        self.donation = Donation.objects.create(
            campaign=self.campaign,
            donor=self.member,
            amount=5000.00,
            payment_status=Donation.PaymentStatus.PENDING,
        )

    def test_payment_by_wire_transfer(self):
        """Test payment by wire transfer (virement)."""
        self.client.force_authenticate(self.member)
        
        url = reverse('donations-pay', args=[self.donation.id])
        payload = {
            'payment_method': Donation.PaymentMethod.VIREMENT,
            'wire_reference': 'WIRE123456789',
        }
        response = self.client.post(url, payload, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        self.donation.refresh_from_db()
        self.assertEqual(self.donation.payment_method, Donation.PaymentMethod.VIREMENT)
        self.assertEqual(self.donation.payment_status, Donation.PaymentStatus.PENDING_WIRE)
        self.assertEqual(self.donation.wire_reference, 'WIRE123456789')

    def test_wire_transfer_missing_reference(self):
        """Test wire transfer fails without reference."""
        self.client.force_authenticate(self.member)
        
        url = reverse('donations-pay', args=[self.donation.id])
        payload = {
            'payment_method': Donation.PaymentMethod.VIREMENT,
        }
        response = self.client.post(url, payload, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_confirm_wire_transfer_by_admin(self):
        """Test admin confirming a wire transfer."""
        # First, set donation to pending_wire
        self.donation.payment_method = Donation.PaymentMethod.VIREMENT
        self.donation.payment_status = Donation.PaymentStatus.PENDING_WIRE
        self.donation.wire_reference = 'WIRE123456789'
        self.donation.save()
        
        self.client.force_authenticate(self.admin)
        
        url = reverse('donations-confirm-wire', args=[self.donation.id])
        response = self.client.post(url, {}, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        self.donation.refresh_from_db()
        self.assertEqual(self.donation.payment_status, Donation.PaymentStatus.CONFIRMED)
        self.assertEqual(self.donation.validated_by, self.admin)
        self.assertIsNotNone(self.donation.validated_at)


class DonationArchiveTests(APITestCase):
    """Test donation archive creation and management."""

    def setUp(self):
        self.admin = User.objects.create_user(
            email='admin@test.com',
            password='Admin123!',
            role=User.Role.ADMIN,
            status=User.Status.ACTIVE,
            is_staff=True,
            is_superuser=True,
        )
        self.member1 = User.objects.create_user(
            email='member1@test.com',
            password='Member123!',
            role=User.Role.MEMBER,
            status=User.Status.ACTIVE,
        )
        self.member2 = User.objects.create_user(
            email='member2@test.com',
            password='Member123!',
            role=User.Role.MEMBER,
            status=User.Status.ACTIVE,
        )
        self.ldd = LDD.objects.create(code='SEN', name='Senegal')
        self.daara = Daara.objects.create(name='Test Daara', ldd=self.ldd)
        self.fete = Fete.objects.create(
            name='Magal',
            date=timezone.now().date(),
            recurrence=Fete.Recurrence.ANNUAL,
        )
        self.campaign = Campaign.objects.create(
            name='Ndiguel Magal',
            deadline=timezone.now().date(),
            fete=self.fete,
            daara=self.daara,
            created_by=self.admin,
        )
        
        # Create confirmed donations
        self.donation1 = Donation.objects.create(
            campaign=self.campaign,
            donor=self.member1,
            amount=5000.00,
            payment_status=Donation.PaymentStatus.CONFIRMED,
        )
        self.donation2 = Donation.objects.create(
            campaign=self.campaign,
            donor=self.member2,
            amount=3000.00,
            payment_status=Donation.PaymentStatus.CONFIRMED,
        )
        
        self.client.force_authenticate(self.admin)

    def test_create_archive(self):
        """Test creating an archive of confirmed donations."""
        url = reverse('donations-create-archive')
        payload = {
            'name': 'Archive Magal 2026',
            'description': 'Archive de tous les dons pour le Magal',
        }
        response = self.client.post(url, payload, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['name'], 'Archive Magal 2026')
        self.assertEqual(response.data['total_count'], 2)
        self.assertEqual(float(response.data['total_amount']), 8000.00)
        
        # Check donations are archived
        self.donation1.refresh_from_db()
        self.donation2.refresh_from_db()
        self.assertEqual(self.donation1.archive_id.id, response.data['id'])
        self.assertEqual(self.donation2.archive_id.id, response.data['id'])

    def test_create_archive_no_confirmed_donations(self):
        """Test archive creation fails with no confirmed donations."""
        # Delete confirmed donations
        Donation.objects.all().delete()
        
        url = reverse('donations-create-archive')
        payload = {
            'name': 'Empty Archive',
            'description': 'This should fail',
        }
        response = self.client.post(url, payload, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_list_archives(self):
        """Test listing all archives."""
        # Create an archive
        archive = DonationArchive.objects.create(
            name='Test Archive',
            description='Test description',
            created_by=self.admin,
            total_amount=8000.00,
            total_count=2,
        )
        
        url = reverse('donations-list-archives')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['name'], 'Test Archive')

    def test_list_donations_in_archive(self):
        """Test listing donations in a specific archive."""
        # Create archive and link donations
        archive = DonationArchive.objects.create(
            name='Test Archive',
            created_by=self.admin,
            total_amount=8000.00,
            total_count=2,
        )
        self.donation1.archive_id = archive
        self.donation1.save()
        self.donation2.archive_id = archive
        self.donation2.save()
        
        url = reverse('donations-archive-donations', args=[archive.id])
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)
