from django.contrib import admin

from .models import (
    Classification,
    Export,
    Folder,
    FolderAccess,
    Patient,
    Tag,
    VoiceCaption,
)


@admin.register(Classification)
class ClassificationAdmin(admin.ModelAdmin):
    list_display = ['id', 'patient', 'classifier', 'timestamp']
    list_filter = ['classifier', 'timestamp']


@admin.register(VoiceCaption)
class VoiceCaptionAdmin(admin.ModelAdmin):
    list_display = ['id', 'patient', 'user', 'modality', 'processing_status', 'created_at']
    list_filter = ['modality', 'processing_status', 'created_at']


@admin.register(Export)
class ExportAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'status', 'patient_count', 'file_size', 'created_at', 'completed_at']
    list_filter = ['status', 'share_mode', 'created_at', 'completed_at']
    search_fields = ['id', 'user__username', 'query_summary', 'file_path', 'share_token']
    readonly_fields = ['created_at', 'started_at', 'completed_at', 'shared_at']


@admin.register(Folder)
class FolderAdmin(admin.ModelAdmin):
    list_display = ['name', 'parent', 'project', 'created_at', 'created_by']
    list_filter = ['project']
    search_fields = ['name']


@admin.register(FolderAccess)
class FolderAccessAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'folder', 'role', 'created_at']
    list_filter = ['role']
    search_fields = ['user__username', 'folder__name']


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ['name', 'created_at']
    search_fields = ['name']


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ['patient_id', 'name', 'visibility', 'project', 'folder', 'uploaded_at', 'uploaded_by']
    list_filter = ['visibility', 'project', 'uploaded_at']
    search_fields = ['patient_id', 'name']