from django.contrib import admin

from .models import (
    User,
    LDD,
    Daara,
    MemberTitle,
    TitleRequest,
    UserDocument,
)


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
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
