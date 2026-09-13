"""Dermatology-specific file handling helpers."""
import hashlib
import os

from django.utils import timezone

from common.models import FileRegistry, Job, Modality
from common.object_storage import get_object_storage


def _detect_extension_and_format(filename_lower: str):
    if filename_lower.endswith((".jpg", ".jpeg")):
        return os.path.splitext(filename_lower)[1], "jpeg"
    if filename_lower.endswith(".png"):
        return ".png", "png"
    if filename_lower.endswith(".bmp"):
        return ".bmp", "bmp"
    return os.path.splitext(filename_lower)[1] or ".bin", "unknown"


def _upload_uploaded_file_to_storage(key, uploaded_file):
    uploaded_file.seek(0)
    hasher = hashlib.sha256()
    for chunk in uploaded_file.chunks():
        hasher.update(chunk)
    file_size = uploaded_file.size
    file_hash = hasher.hexdigest()
    uploaded_file.seek(0)
    get_object_storage().upload_fileobj(
        uploaded_file,
        key=key,
        content_type=getattr(uploaded_file, "content_type", None),
        metadata={
            "original_filename": getattr(uploaded_file, "name", ""),
            "sha256": file_hash,
        },
    )
    return key, file_size, file_hash


def save_clinical_photo_to_dataset(patient, uploaded_file):
    """Save a Dermatology clinical photo. No background processing needed —
    the job is marked completed immediately since the photo is ready to view
    and annotate as soon as it's uploaded."""
    original_name = uploaded_file.name
    extension, file_format = _detect_extension_and_format(original_name.lower())
    filename = f"clinical_photo_patient_{patient.patient_id}{extension}"
    key = f"dermatology/patients/{patient.patient_id}/raw/clinical_photo/{filename}"

    key, file_size, file_hash = _upload_uploaded_file_to_storage(key, uploaded_file)

    modality = Modality.objects.filter(slug="clinical_photo").first()
    file_registry = FileRegistry.objects.create(
        domain="dermatology",
        dermatology_patient=patient,
        file_type="clinical_photo",
        file_path=key,
        file_size=file_size,
        file_hash=file_hash,
        modality=modality,
        metadata={
            "original_filename": original_name,
            "uploaded_at": timezone.now().isoformat(),
            "file_format": file_format,
            "modality_slug": "clinical_photo",
        },
    )

    job = Job.objects.create(
        domain="dermatology",
        dermatology_patient=patient,
        modality_slug="clinical_photo",
        input_files={"input": key},
        status="completed",
        output_files={"input_format": file_format, "file_path": key},
    )
    job.started_at = job.started_at or timezone.now()
    job.completed_at = timezone.now()
    job.save(update_fields=["started_at", "completed_at"])

    return file_registry, job