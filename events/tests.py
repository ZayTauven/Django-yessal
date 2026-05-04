from datetime import date

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import User
from comms.models import Notification
from events.models import Fete


class FeteNotificationTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="admin@yessal.test",
            password="Admin123!",
            first_name="Admin",
            last_name="User",
            role=User.Role.ADMIN,
            is_staff=True,
            is_superuser=True,
            status=User.Status.ACTIVE,
        )
        self.member = User.objects.create_user(
            email="member@yessal.test",
            password="Member123!",
            first_name="Member",
            last_name="One",
            role=User.Role.MEMBER,
            status=User.Status.ACTIVE,
        )
        self.collector = User.objects.create_user(
            email="collector@yessal.test",
            password="Collector123!",
            first_name="Collector",
            last_name="One",
            role=User.Role.COLLECTOR,
            status=User.Status.ACTIVE,
        )
        self.chef = User.objects.create_user(
            email="chef@yessal.test",
            password="Chef123!",
            first_name="Chef",
            last_name="One",
            role=User.Role.CHEF_DAARA,
            status=User.Status.ACTIVE,
        )
        self.tutelle = User.objects.create_user(
            email="tutelle@yessal.test",
            password="Tutelle123!",
            first_name="Tutelle",
            last_name="One",
            role=User.Role.TUTELLE,
            status=User.Status.ACTIVE,
        )
        self.fete = Fete.objects.create(
            name="Tabaski",
            date=date(2026, 5, 1),
            recurrence=Fete.Recurrence.ANNUAL,
            is_active=True,
            created_by=self.admin,
        )
        self.client.force_authenticate(self.admin)

    def test_admin_date_update_creates_notifications(self):
        url = reverse("fetes-detail", args=[self.fete.id])
        payload = {"date": "2026-05-21"}

        response = self.client.patch(url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Notification.objects.count(), 4)
        self.assertTrue(
            Notification.objects.filter(
                title="Prochaine Tabaski",
                message="Prochaine Tabaski le 21/05/2026.",
            ).exists()
        )

    def test_update_without_date_change_does_not_create_notifications(self):
        url = reverse("fetes-detail", args=[self.fete.id])
        payload = {"description": "Mise à jour sans changement de date"}

        response = self.client.patch(url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Notification.objects.count(), 0)
