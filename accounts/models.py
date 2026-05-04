from django.db import models
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


class CustomUserManager(BaseUserManager):
    def create_user(self, email=None, password=None, **extra_fields):
        phone = extra_fields.get('phone')
        if not email and not phone:
            raise ValueError(_('Email or phone must be set'))
        if email:
            email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save()
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)

        if not email:
            raise ValueError(_('Superuser must have an email address.'))
        if extra_fields.get('is_staff') is not True:
            raise ValueError(_('Superuser must have is_staff=True.'))
        if extra_fields.get('is_superuser') is not True:
            raise ValueError(_('Superuser must have is_superuser=True.'))
        return self.create_user(email, password, **extra_fields)


class LDD(models.Model):
    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    location = models.CharField(max_length=100, blank=True, null=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.code} - {self.name}"


class Daara(models.Model):
    name = models.CharField(max_length=100)
    ldd = models.ForeignKey(LDD, on_delete=models.CASCADE, related_name='daaras')

    chef = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='daara_managed')

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('name', 'ldd')

    def __str__(self):
        return f"{self.name} - {self.ldd.name}"


class DaaraMembership(models.Model):
    user = models.ForeignKey('accounts.User', on_delete=models.CASCADE)
    daara = models.ForeignKey('accounts.Daara', on_delete=models.CASCADE)

    start_date = models.DateField(auto_now_add=True)
    end_date = models.DateField(null=True, blank=True)

    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.user} - {self.daara.name}"


class MemberTitle(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey('User', on_delete=models.SET_NULL, null=True, related_name='created_titles')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = 'admin', _('Admin')
        CHEF_DAARA = 'chef_daara', _('Chef Daara')
        COLLECTOR = 'collector', _('Collector')
        MEMBER = 'member', _('Member')
        TUTELLE = 'tutelle', _('Tutelle')

    class Status(models.TextChoices):
        PENDING = 'pending', _('Pending')
        ACTIVE = 'active', _('Active')
        INACTIVE = 'inactive', _('Inactive')
        BLOCKED = 'blocked', _('Blocked')

    class Gender(models.TextChoices):
        MALE = 'male', 'Male'
        FEMALE = 'female', 'Female'
        OTHER = 'other', 'Other'

    class MaritalStatus(models.TextChoices):
        SINGLE = 'single', 'Single'
        MARRIED = 'married', 'Married'
        DIVORCED = 'divorced', 'Divorced'
        WIDOWED = 'widowed', 'Widowed'

    class BloodType(models.TextChoices):
        A_POS = 'A+', 'A+'
        A_NEG = 'A-', 'A-'
        B_POS = 'B+', 'B+'
        B_NEG = 'B-', 'B-'
        AB_POS = 'AB+', 'AB+'
        AB_NEG = 'AB-', 'AB-'
        O_POS = 'O+', 'O+'
        O_NEG = 'O-', 'O-'

    username = None
    email = models.EmailField(_('email address'), unique=True, null=True, blank=True)
    phone = models.CharField(max_length=20, unique=True, blank=True, null=True, help_text="Obligatoire si pas d'email")
    two_fa_secret = models.CharField(max_length=255, blank=True, null=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.MEMBER)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    daara = models.ForeignKey(Daara, on_delete=models.SET_NULL, null=True, blank=True, related_name='members')
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
    avatar_url = models.URLField(blank=True, null=True)

    title = models.ForeignKey(MemberTitle, on_delete=models.SET_NULL, null=True, blank=True, related_name='members')
    title_change_count = models.PositiveIntegerField(default=0)
    title_changed_at = models.DateTimeField(null=True, blank=True)
    birth_date = models.DateField(blank=True, null=True)
    gender = models.CharField(max_length=10, choices=Gender.choices, blank=True, null=True)
    residence_country = models.CharField(max_length=100, blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    address = models.CharField(max_length=255, blank=True, null=True)
    state = models.CharField(max_length=100, blank=True, null=True)
    zip_code = models.CharField(max_length=20, blank=True, null=True)
    marital_status = models.CharField(max_length=10, choices=MaritalStatus.choices, blank=True, null=True)
    blood_type = models.CharField(max_length=10, choices=BloodType.choices, blank=True, null=True)

    last_active_at = models.DateTimeField(blank=True, null=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['first_name', 'last_name']

    objects = CustomUserManager()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(email__isnull=False) | models.Q(phone__isnull=False)),
                name='user_email_or_phone_required',
            ),
        ]

    def clean(self):
        email = (self.email or '').strip()
        phone = (self.phone or '').strip()
        if not email and not phone:
            raise ValidationError("Un email ou un numéro de téléphone est requis.")

    @property
    def can_change_title(self) -> bool:
        return self.title_change_count == 0

    def save(self, *args, **kwargs):
        self.email = (self.email or '').strip() or None
        self.phone = (self.phone or '').strip() or None
        super().save(*args, **kwargs)

    def __str__(self):
        return self.email or self.phone or f"user-{self.pk}"


class TitleRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'En attente'
        APPROVED = 'approved', 'Approuvé'
        REFUSED = 'refused', 'Refusé'

    member = models.ForeignKey('User', on_delete=models.CASCADE, related_name='title_requests')
    title = models.ForeignKey('MemberTitle', on_delete=models.CASCADE)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    reviewed_by = models.ForeignKey('User', on_delete=models.SET_NULL, null=True, blank=True, related_name='reviewed_title_requests')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.member} -> {self.title} ({self.status})"


class UserDocument(models.Model):
    class DocType(models.TextChoices):
        NATIONAL_ID = 'national_id', "Carte Nationale d'Identité"
        PASSPORT = 'passport', 'Passeport'
        VOTER_ID = 'voter_id', "Carte d'Électeur"
        DRIVER_LICENSE = 'driver_license', 'Permis de Conduire'

    class ValidationStatus(models.TextChoices):
        PENDING = 'pending', 'En attente'
        VALIDATED = 'validated', 'Validé'
        REJECTED = 'rejected', 'À corriger'

    user = models.ForeignKey('User', on_delete=models.CASCADE, related_name='documents')
    doc_type = models.CharField(max_length=20, choices=DocType.choices)
    image = models.ImageField(upload_to='documents/%Y/%m/')
    image_verso = models.ImageField(upload_to='documents/%Y/%m/', null=True, blank=True)
    doc_number = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=10, choices=ValidationStatus.choices, default=ValidationStatus.PENDING)
    validated_by = models.ForeignKey('User', on_delete=models.SET_NULL, null=True, blank=True, related_name='validated_documents')
    validated_at = models.DateTimeField(null=True, blank=True)
    rejection_note = models.TextField(blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'doc_type')

    def __str__(self):
        return f"{self.user} - {self.doc_type}"


class Tutelle(models.Model):
    tutor = models.ForeignKey(User, on_delete=models.CASCADE, related_name='tutelles')
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    relation = models.CharField(max_length=50)
    linked_user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='tutelle_profile')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.relation})"


class AuditLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='audit_logs')
    action = models.CharField(max_length=255)
    entity = models.CharField(max_length=100, blank=True, null=True)
    entity_id = models.IntegerField(blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    metadata = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.action} by {self.user.email if self.user else 'System'} at {self.created_at}"


class PilotageSettings(models.Model):
    enable_salons = models.BooleanField(default=False)

    def __str__(self):
        return "Paramètres de Pilotage"

    class Meta:
        verbose_name = "Paramètre de Pilotage"
        verbose_name_plural = "Paramètres de Pilotage"

    @classmethod
    def load(cls):
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj

