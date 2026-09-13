import logging

from django.contrib.auth.models import User
from django.db import models

from common.base_models import (
    ActivePatientManager,
    ClassificationBase,
    ExportBase,
    FolderAccessBase,
    FolderBase,
    TagBase,
    VoiceCaptionBase,
)
from common.models import Modality, Project


logger = logging.getLogger(__name__)


class DermatologyProject(Project):
    """Project proxy bound to the dermatology domain (admin section + forced domain)."""

    class Meta:
        proxy = True
        verbose_name = 'Dermatology project'
        verbose_name_plural = 'Dermatology projects'


class Folder(FolderBase):
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='dermatology_folders_created',
    )
    project = models.ForeignKey(
        'common.Project', on_delete=models.CASCADE,
        related_name='dermatology_folders',
    )

    class Meta:
        db_table = 'dermatology_folder'
        unique_together = ('project', 'name', 'parent')
        ordering = ['name']
        indexes = [
            models.Index(fields=['project']),
            models.Index(fields=['parent']),
            models.Index(fields=['name']),
        ]


class FolderAccess(FolderAccessBase):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='dermatology_folder_access')

    class Meta:
        db_table = 'dermatology_folderaccess'
        unique_together = ('user', 'folder')
        indexes = [
            models.Index(fields=['folder']),
            models.Index(fields=['user']),
            models.Index(fields=['role']),
            models.Index(fields=['folder', 'role']),
            models.Index(fields=['user', 'role']),
        ]


class Tag(TagBase):
    class Meta:
        db_table = 'dermatology_tag'
        ordering = ['name']
        indexes = [
            models.Index(fields=['name']),
        ]


class Patient(models.Model):
    VISIBILITY_CHOICES = [
        ('public', 'Public'),
        ('private', 'Private'),
        ('debug', 'Debug'),
    ]

    patient_id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100, blank=True)
    modalities = models.ManyToManyField(
        Modality, blank=True, related_name='dermatology_patients',
        help_text='Modalities available for this patient',
    )
    folder = models.ForeignKey('Folder', on_delete=models.SET_NULL, null=True, blank=True, related_name='patients')
    project = models.ForeignKey(
        'common.Project', on_delete=models.CASCADE,
        related_name='dermatology_patients',
    )
    tags = models.ManyToManyField('Tag', blank=True, related_name='patients')

    visibility = models.CharField(max_length=10, choices=VISIBILITY_CHOICES, default='private')
    deleted = models.BooleanField(default=False, db_index=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='dermatology_patients_uploaded',
    )

    objects = ActivePatientManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'dermatology_patient'
        ordering = ['-uploaded_at']
        indexes = [
            models.Index(fields=['visibility']),
            models.Index(fields=['uploaded_at']),
            models.Index(fields=['folder']),
            models.Index(fields=['project']),
            models.Index(fields=['name']),
            models.Index(fields=['visibility', 'uploaded_at']),
        ]

    def __str__(self):
        return f"Patient {self.patient_id} - {self.name}"

    @property
    def files(self):
        from common.models import FileRegistry
        return FileRegistry.objects.filter(domain='dermatology', dermatology_patient=self)

    @property
    def jobs(self):
        from common.models import Job
        return Job.objects.filter(domain='dermatology', dermatology_patient=self)

    @property
    def processing_jobs(self):
        from common.models import ProcessingJob
        return ProcessingJob.objects.filter(domain='dermatology', dermatology_patient=self)

    def tag_names(self):
        return list(self.tags.values_list('name', flat=True))

    def save(self, *args, **kwargs):
        creating = self._state.adding
        super().save(*args, **kwargs)
        if creating and (self.name is None or self.name.strip() == ''):
            self.name = f"Patient {self.patient_id}"
            super().save(update_fields=['name'])

    def has_clinical_photos(self):
        try:
            return self.files.filter(file_type='clinical_photo').exists()
        except Exception as exc:
            logger.error('Error checking clinical photos for patient %s: %s', self.patient_id, exc, exc_info=True)
            return False

    def get_clinical_photos(self):
        return self.files.filter(file_type='clinical_photo').order_by('-created_at')


class Classification(ClassificationBase):
    classifier = models.CharField(max_length=10, choices=ClassificationBase.CLASSIFIER_CHOICES, default='manual')
    notes = models.TextField(blank=True)
    annotator = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='dermatology_classifications_authored',
    )

    class Meta:
        db_table = 'dermatology_classification'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['patient', 'classifier']),
            models.Index(fields=['classifier']),
        ]

    def __str__(self):
        return f"Classification {self.id} - {self.get_classifier_display()}"


class VoiceCaption(VoiceCaptionBase):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='dermatology_voice_captions')
    modality = models.CharField(max_length=255, default='', blank=True)
    text_caption = models.TextField(blank=True, null=True)
    original_text_caption = models.TextField(blank=True, null=True)
    is_edited = models.BooleanField(default=False)
    edit_history = models.JSONField(default=list, blank=True)
    processing_status = models.CharField(
        max_length=20, choices=VoiceCaptionBase.PROCESSING_STATUS_CHOICES, default='pending',
    )

    class Meta:
        db_table = 'dermatology_voicecaption'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['patient', 'processing_status']),
            models.Index(fields=['processing_status']),
            models.Index(fields=['user']),
        ]


class Export(ExportBase):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='dermatology_exports')
    query_params = models.JSONField(default=dict)
    query_summary = models.CharField(max_length=500, blank=True)
    file_path = models.CharField(max_length=1000, blank=True)
    file_size = models.BigIntegerField(default=0)
    patient_count = models.IntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    share_mode = models.CharField(max_length=20, choices=ExportBase.SHARE_MODE_CHOICES, default='private')
    share_token = models.CharField(max_length=64, unique=True, null=True, blank=True)
    shared_at = models.DateTimeField(null=True, blank=True)
    progress_message = models.CharField(max_length=255, blank=True)
    progress_percent = models.IntegerField(null=True, blank=True)

    class Meta:
        db_table = 'dermatology_export'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['status', 'created_at']),
        ]

    def __str__(self):
        return f"Export {self.id} - {self.get_status_display()}"

class RegionType(models.Model):
    """Type of annotated region on a lesion photo (e.g. 'Suspicious area', 'Benign nevus')."""
    project = models.ForeignKey(
        'common.Project', on_delete=models.CASCADE, related_name="dermatology_region_types"
    )
    name = models.CharField(max_length=100)
    color = models.CharField(max_length=7, default="#3498db")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'dermatology_regiontype'
        ordering = ["order", "name"]
        unique_together = ("project", "name")

    def __str__(self):
        return f"{self.project} / {self.name}"


class QuadrantType(models.Model):
    """Body region / anatomical area where the photo was taken (e.g. 'Face', 'Left arm', 'Back')."""
    project = models.ForeignKey(
        'common.Project', on_delete=models.CASCADE, related_name="dermatology_quadrant_types"
    )
    name = models.CharField(max_length=100)
    color = models.CharField(max_length=7, default="#e74c3c")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'dermatology_quadranttype'
        ordering = ["order", "name"]
        unique_together = ("project", "name")

    def __str__(self):
        return f"{self.project} / {self.name}"


class RegionTypeUserColor(models.Model):
    region_type = models.ForeignKey(
        RegionType, on_delete=models.CASCADE, related_name="user_colors"
    )
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="dermatology_region_colors"
    )
    color = models.CharField(max_length=7)

    class Meta:
        db_table = 'dermatology_regiontypeusercolor'
        unique_together = ("region_type", "user")


class QuadrantTypeUserColor(models.Model):
    quadrant_type = models.ForeignKey(
        QuadrantType, on_delete=models.CASCADE, related_name="user_colors"
    )
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="dermatology_quadrant_colors"
    )
    color = models.CharField(max_length=7)

    class Meta:
        db_table = 'dermatology_quadranttypeusercolor'
        unique_together = ("quadrant_type", "user")


class QuadrantClassificationMarker(models.Model):
    """Marks which body region (QuadrantType) a given photo belongs to."""
    patient = models.ForeignKey(
        'dermatology.Patient',
        on_delete=models.CASCADE,
        related_name="quadrant_markers",
    )
    file_registry_id = models.IntegerField(
        null=True, blank=True,
        help_text='ID of the FileRegistry row (photo) this marker refers to',
    )
    quadrant_type = models.ForeignKey(
        QuadrantType, on_delete=models.CASCADE, related_name="markers"
    )
    created_by = models.ForeignKey(
        User, null=True, on_delete=models.SET_NULL,
        related_name="created_dermatology_quadrant_markers"
    )
    updated_by = models.ForeignKey(
        User, null=True, on_delete=models.SET_NULL,
        related_name="updated_dermatology_quadrant_markers"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'dermatology_quadrantclassificationmarker'
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["patient", "file_registry_id"],
                name="derma_unique_patient_quadrant_marker_photo",
            )
        ]
        indexes = [
            models.Index(fields=["patient", "file_registry_id"]),
            models.Index(fields=["patient", "quadrant_type"]),
        ]

    def __str__(self):
        return f"Marker {self.id} patient {self.patient_id}"


class RegionAnnotation(models.Model):
    TOOL_CHOICES = [
        ("brush", "Brush"),
        ("eraser", "Eraser"),
        ("polygon", "Polygon"),
    ]

    patient = models.ForeignKey(
        'dermatology.Patient',
        on_delete=models.CASCADE,
        related_name="region_annotations",
    )
    file_registry_id = models.IntegerField(
        null=True, blank=True,
        help_text='ID of the FileRegistry row (photo) this annotation is drawn on',
    )
    region_type = models.ForeignKey(
        RegionType, on_delete=models.CASCADE, related_name="annotations"
    )
    tool = models.CharField(max_length=20, choices=TOOL_CHOICES)
    points = models.JSONField(default=list)
    prompt_points = models.JSONField(default=list, blank=True)
    stroke_width = models.FloatField(default=1.0)
    created_by = models.ForeignKey(
        User, null=True, on_delete=models.SET_NULL,
        related_name="created_dermatology_annotations"
    )
    updated_by = models.ForeignKey(
        User, null=True, on_delete=models.SET_NULL,
        related_name="updated_dermatology_annotations"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'dermatology_regionannotation'
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["patient", "file_registry_id"]),
            models.Index(fields=["patient", "region_type"]),
        ]

    def __str__(self):
        return f"Annotation {self.id} ({self.tool}) on patient {self.patient_id}"