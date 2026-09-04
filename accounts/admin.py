from django import forms
from django.contrib import admin

from core.phone import normalize_phone

from .models import (
    User,
    LDD,
    Daara,
    MemberTitle,
    TitleRequest,
    UserDocument,
)


class UserAdminForm(forms.ModelForm):
    """Normalise le numéro AVANT que le formulaire ne contrôle l'unicité.

    `User.save()` normalise aussi, mais trop tard pour l'administration : le
    formulaire vérifie l'unicité sur la valeur SAISIE, puis `save()` la ramène
    en E.164. Taper « +221 77 000 00 00 » alors que « +221770000000 » existe
    passait donc le contrôle du formulaire pour se heurter à l'INSERT — une
    page d'erreur 500 au lieu du « ce numéro existe déjà » que Django sait
    afficher.

    Même raison d'être que `PhoneField` côté API : normaliser au moment où la
    valeur est encore vérifiable.
    """

    class Meta:
        model = User
        fields = '__all__'

    def clean_phone(self):
        return normalize_phone(self.cleaned_data.get('phone'))


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    form = UserAdminForm
    search_fields = ('email', 'phone', 'first_name', 'last_name')
    list_display = ('id', 'email', 'phone', 'first_name', 'last_name', 'role', 'status', 'daara', 'title')
    list_filter = ('role', 'status', 'daara', 'title')


@admin.register(LDD)
class LDDAdmin(admin.ModelAdmin):
    list_display = ('code', 'name')
    search_fields = ('code', 'name')


@admin.register(Daara)
class DaaraAdmin(admin.ModelAdmin):
    list_display = ('name', 'ldd', 'chef', 'is_active', 'created_at')
    list_filter = ('ldd', 'is_active')
    search_fields = ('name', 'ldd__code', 'ldd__name')
    autocomplete_fields = ('ldd', 'chef')


@admin.register(MemberTitle)
class MemberTitleAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active', 'created_by', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('name',)


@admin.register(TitleRequest)
class TitleRequestAdmin(admin.ModelAdmin):
    list_display = ('member', 'title', 'status', 'reviewed_by', 'reviewed_at', 'created_at')
    list_filter = ('status', 'title')
    search_fields = ('member__first_name', 'member__last_name', 'member__email', 'member__phone', 'title__name')


@admin.register(UserDocument)
class UserDocumentAdmin(admin.ModelAdmin):
    list_display = ('user', 'doc_type', 'status', 'validated_by', 'validated_at', 'submitted_at')
    list_filter = ('doc_type', 'status')
    search_fields = ('user__first_name', 'user__last_name', 'user__email', 'user__phone', 'doc_number')
