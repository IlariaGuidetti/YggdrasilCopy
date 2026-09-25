import json as _json
import logging
import math
import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from common.file_access import exists as artifact_exists
from common.models import Modality, Project, ProjectAccess
from common.permissions import (
    filter_folders_for_user,
    filter_patients_for_user,
    user_is_project_admin,
    user_can_write_annotations,
    user_can_delete_single_patient,
)
from maxillo.views.helpers import render_with_fallback
from maxillo.views.patient_data import _serve_file_url

from .forms import PatientForm, PatientManagementForm, PatientUploadForm
from .models import Export, Folder, Patient, Tag


logger = logging.getLogger(__name__)


def _get_current_project(request):
    project_id = request.session.get("current_project_id")
    if project_id:
        project = Project.objects.filter(id=project_id, domain="dermatology").first()
        if project:
            return project
    return Project.objects.filter(domain="dermatology", is_active=True).first()


def home(request):
    if request.user.is_authenticated:
        projects = Project.objects.filter(is_active=True)
        if not request.user.is_staff:
            project_ids = ProjectAccess.objects.filter(user=request.user).values_list("project_id", flat=True)
            projects = projects.filter(id__in=project_ids)
        current_project_id = request.session.get("current_project_id")
        current_project_name = None
        if current_project_id:
            current_project = projects.filter(id=current_project_id).first()
            current_project_name = current_project.name if current_project else None
        return render(request, "common/landing.html", {
            "projects": projects.order_by("name"),
            "current_project_id": current_project_id,
            "current_project_name": current_project_name,
            "continue_url": "/dermatology/" if current_project_name else None,
        })
    return render(request, "common/landing.html")


@login_required
def set_dermatology_home(request):
    project = Project.objects.filter(slug="dermatology", domain="dermatology").first()
    if not project:
        project = Project.objects.create(name="Dermatology", slug="dermatology", domain="dermatology")

    if not user_is_project_admin(request.user, "dermatology"):
        has_access = ProjectAccess.objects.filter(user=request.user, project=project).exists()
        if not has_access:
            messages.error(request, "You don't have access to the dermatology project.")
            return redirect("home")

    request.session["current_project_id"] = project.id
    return redirect("dermatology:patient_list")


@login_required
def select_project(request, project_id):
    project = get_object_or_404(Project, id=project_id, is_active=True)
    if not user_is_project_admin(request.user, project):
        has_access = ProjectAccess.objects.filter(user=request.user, project=project).exists()
        if not has_access:
            messages.error(request, f"You don't have access to the {project.name} project.")
            return redirect("home")
    request.session["current_project_id"] = project.id
    messages.success(request, f"Project set to {project.name}")
    return redirect("dermatology:patient_list")


@login_required
def patient_list(request):
    patients = Patient.objects.select_related("uploaded_by", "folder", "project").prefetch_related(
        "voice_captions",
        "voice_captions__user",
        "tags",
        "modalities",
        "files",
        "files__modality",
        "jobs",
    )
    current_project_id = request.session.get("current_project_id")
    if current_project_id:
        patients = patients.filter(project_id=current_project_id)
    patients = filter_patients_for_user(request.user, patients, "dermatology")
    patients_for_folder_counts = patients

    search_query = request.GET.get("search", "").strip()
    if search_query:
        patients = patients.filter(Q(name__icontains=search_query) | Q(patient_id__icontains=search_query))

    folder_id = request.GET.get("folder")
    if folder_id and folder_id != "all":
        if folder_id == "root":
            patients = patients.filter(folder__isnull=True)
        else:
            try:
                patients = patients.filter(folder_id=int(folder_id))
            except ValueError:
                pass

    tags_selected = request.GET.getlist("tags")
    if tags_selected:
        patients = patients.filter(tags__name__in=tags_selected).distinct()

    patients = patients.order_by("-uploaded_at")
    allowed_modalities = []
    if current_project_id:
        project = Project.objects.filter(id=current_project_id).prefetch_related("modalities").first()
        if project:
            allowed_modalities = list(project.modalities.filter(is_active=True))

    patients_with_status = []
    is_admin = user_is_project_admin(request.user, "dermatology")
    for patient in patients:
        voice_captions = list(patient.voice_captions.all())
        patient_files = list(patient.files.all())
        patient_jobs = list(patient.jobs.all()) if hasattr(patient, "jobs") else []
        files_by_modality = {}
        for file_obj in patient_files:
            if file_obj.modality and file_obj.modality.slug:
                files_by_modality.setdefault(file_obj.modality.slug, []).append(file_obj)
        jobs_by_modality = {}
        for job in patient_jobs:
            jobs_by_modality.setdefault(job.modality_slug, []).append(job)
        modality_status_list = []
        for modality in allowed_modalities:
            slug = modality.slug or ""
            if slug in {"rawzip", "voice"}:
                continue
            jobs = jobs_by_modality.get(slug, [])
            status = "absent"
            if any(job.status == "failed" for job in jobs):
                status = "failed"
            elif any(job.status == "processing" for job in jobs):
                status = "processing"
            elif any(job.status in ["pending", "retrying"] for job in jobs):
                status = "pending"
            elif files_by_modality.get(slug):
                status = "processed"
            modality_status_list.append({
                "slug": slug,
                "name": modality.name,
                "icon": modality.icon or "",
                "label": modality.label or "",
                "status": status,
            })
        patients_with_status.append({
            "patient": patient,
            "voice_caption_processing": any(vc.processing_status in ["pending", "processing"] for vc in voice_captions),
            "voice_caption_processed": bool(voice_captions) and all(vc.processing_status == "completed" for vc in voice_captions),
            "voice_caption_count": len(voice_captions),
            "voice_annotators": list({vc.user.username for vc in voice_captions}),
            "tags": patient.tag_names(),
            "folder": patient.folder,
            "available_modalities": [m.slug for m in patient.modalities.all()],
            "modality_statuses": {item["slug"]: item["status"] for item in modality_status_list},
            "modality_status_list": modality_status_list,
            "can_delete": is_admin or patient.uploaded_by_id == request.user.id,
        })

    per_page = int(request.GET.get("per_page", 20))
    page_obj = Paginator(patients_with_status, per_page).get_page(request.GET.get("page"))
    folders_qs = Folder.objects.filter(parent__isnull=True).order_by("name")
    if current_project_id:
        folders_qs = folders_qs.filter(project_id=current_project_id)
    folders = filter_folders_for_user(request.user, folders_qs, "dermatology")
    context = {
        "page_obj": page_obj,
        "current_project_id": current_project_id,
        "search_query": search_query,
        "folder_id": folder_id or "all",
        "selected_tags": tags_selected,
        "folders": [{"folder": folder, "patient_count": patients_for_folder_counts.filter(folder=folder).count()} for folder in folders],
        "all_tags": Tag.objects.all().order_by("name"),
        "per_page": per_page,
        "user_profile": request.user.profile,
        "is_admin_user": is_admin,
        "allowed_modalities": allowed_modalities,
        "status_filters": {},
        "modality_filter_specs": [
            {"slug": m.slug, "name": m.name, "icon": m.icon or "", "label": m.label or "", "value": ""}
            for m in allowed_modalities
            if m.slug != "rawzip"
        ],
    }
    return render_with_fallback(request, "patient_list", context)

@login_required
def upload_patient(request):
    user_profile = request.user.profile
    namespace = "dermatology"

    if not request.user.profile:
        messages.error(request, "You do not have permission to upload scans.")
        return redirect("dermatology:patient_list")

    if not user_profile.can_upload_scans():
        messages.error(request, "You do not have permission to upload scans.")
        return redirect("dermatology:patient_list")

    project = _get_current_project(request)
    if not project:
        messages.error(request, "No active Dermatology project found.")
        return redirect("dermatology:patient_list")

    if request.method == "POST":
        patient_upload_form = PatientUploadForm(request.POST, request.FILES, user=request.user)
        patient_form = PatientForm()

        if patient_upload_form.is_valid():
            patient = patient_upload_form.save(commit=False)
            patient.uploaded_by = request.user
            patient.project = project

            folder = patient_upload_form.cleaned_data.get("folder")
            if folder:
                allowed_folder_ids = set(
                    filter_folders_for_user(
                        request.user,
                        Folder.objects.filter(parent__isnull=True, project=project).only("id"),
                        namespace,
                    ).values_list("id", flat=True)
                )
                if folder.id not in allowed_folder_ids:
                    messages.error(request, "You do not have permission to upload to the selected folder.")
                    allowed_folders = filter_folders_for_user(
                        request.user,
                        Folder.objects.filter(parent__isnull=True, project=project).order_by("name"),
                        namespace,
                    )
                    return render(request, "common/upload/upload.html", {
                        "patient_form": patient_form,
                        "patient_upload_form": patient_upload_form,
                        "folders": allowed_folders,
                    })
                patient.folder = folder

            patient.save()
            patient_upload_form.instance = patient
            patient_upload_form.save(commit=True)

            uploaded_modalities = []
            processing_job_ids = []

            photo_file = request.FILES.get("photo")
            if photo_file:
                try:
                    modality = Modality.objects.get(slug="clinical_photo")
                    patient.modalities.add(modality)

                    from .file_utils import save_clinical_photo_to_dataset
                    file_registry, job = save_clinical_photo_to_dataset(patient, photo_file)
                    if file_registry:
                        uploaded_modalities.append("Clinical Photo")
                        if job:
                            processing_job_ids.append(job.id)
                except Exception as exc:
                    logger.exception("Error saving Clinical Photo")
                    messages.error(request, f"Error saving Clinical Photo: {exc}")

            if uploaded_modalities:
                unique_modalities = list(dict.fromkeys(uploaded_modalities))
                summary_message = (
                    f"Patient uploaded successfully with {len(unique_modalities)} modality(s): "
                    f"{', '.join(unique_modalities)}."
                )
                if processing_job_ids:
                    summary_message += f" Processing jobs: #{', #'.join(str(job_id) for job_id in processing_job_ids)}."
                messages.success(request, summary_message)
            else:
                messages.success(request, "Patient uploaded successfully!")

            return redirect("dermatology:patient_list")
    else:
        patient_form = PatientForm()
        patient_upload_form = PatientUploadForm(user=request.user)

    folders = filter_folders_for_user(
        request.user,
        Folder.objects.filter(parent__isnull=True, project=project).order_by("name"),
        namespace,
    )

    allowed_modalities = list(project.modalities.filter(is_active=True))

    return render(request, "common/upload/upload.html", {
        "patient_form": patient_form,
        "patient_upload_form": patient_upload_form,
        "folders": folders,
        "allowed_modalities": allowed_modalities,
    })


@login_required
def patient_detail(request, patient_id):
    patient = get_object_or_404(Patient, patient_id=patient_id)
    is_admin_user = user_is_project_admin(request.user, "dermatology")
    is_owner = patient.uploaded_by_id == request.user.id
    can_view = is_admin_user or is_owner or patient.visibility == "public" or patient.folder is not None
    if not can_view:
        messages.error(request, "You do not have permission to view this patient.")
        return redirect("dermatology:patient_list")

    management_form = PatientManagementForm(instance=patient, user=request.user)
    can_modify = is_admin_user or bool(patient.folder and user_can_write_annotations(request.user, patient.folder, request))

    if request.method == "POST" and can_modify:
        action = request.POST.get("action")
        if action == "update_management":
            management_form = PatientManagementForm(request.POST, instance=patient, user=request.user)
            if management_form.is_valid():
                management_form.save()
                messages.success(request, "Scan settings updated successfully!")
                return redirect("dermatology:patient_detail", patient_id=patient_id)

    voice_captions = patient.voice_captions.all()
    for caption in voice_captions:
        caption.can_view_content = bool(is_admin_user or caption.user_id == request.user.id)
        caption.can_edit_content = bool(is_admin_user or caption.user_id == request.user.id)
        caption.is_ghost = not caption.can_view_content

    context = {
        "patient": patient,
        "user_profile": request.user.profile,
        "management_form": management_form,
        "can_modify": can_modify,
        "can_create_caption": can_modify,
        "voice_captions": voice_captions,
        "is_admin_user": is_admin_user,
        "allowed_modalities": [],
    }
    return render_with_fallback(request, "patient_detail", context)


@login_required
@require_POST
def update_patient_name(request, patient_id):
    patient = get_object_or_404(Patient, patient_id=patient_id)
    can_modify = user_is_project_admin(request.user, "dermatology") or bool(patient.folder and user_can_write_annotations(request.user, patient.folder, request))
    if not can_modify:
        return JsonResponse({"error": "Permission denied"}, status=403)
    try:
        data = _json.loads(request.body)
    except _json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON payload"}, status=400)
    name = (data.get("name") or "").strip()
    if not name:
        return JsonResponse({"error": "Name cannot be empty"}, status=400)
    patient.name = name[:100]
    patient.save(update_fields=["name"])
    return JsonResponse({"success": True, "name": patient.name})


@login_required
@require_POST
def delete_patient(request, patient_id):
    patient = get_object_or_404(Patient, patient_id=patient_id)
    can_delete = user_is_project_admin(request.user, "dermatology") or bool(patient.folder and user_can_delete_single_patient(request.user, patient.folder, request))
    if not can_delete:
        return JsonResponse(
            {"success": False, "error": "You do not have permission to delete this patient."},
            status=403,
        )
    patient.deleted = True
    patient.save(update_fields=["deleted"])
    return JsonResponse({"success": True, "message": "Patient deleted successfully"})


@login_required
@require_POST
def bulk_delete_patients(request):
    try:
        data = _json.loads(request.body) if request.body else request.POST
    except _json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid JSON payload"}, status=400)

    scan_ids = data.get("scan_ids", [])
    if not isinstance(scan_ids, list) or not scan_ids:
        return JsonResponse({"success": False, "error": "scan_ids list is required"}, status=400)

    if not user_is_project_admin(request.user, "dermatology"):
        return JsonResponse(
            {"success": False, "error": "You do not have permission to bulk delete patients."},
            status=403,
        )

    deleted_count = Patient.objects.filter(patient_id__in=scan_ids).update(deleted=True)
    if not deleted_count:
        return JsonResponse({"success": False, "error": "No valid patients found to delete"}, status=404)

    return JsonResponse(
        {
            "success": True,
            "message": f"Successfully deleted {deleted_count} patient(s).",
            "deleted_count": deleted_count,
        }
    )


@login_required
@require_POST
def add_patient_tag(request, patient_id):
    patient = get_object_or_404(Patient, patient_id=patient_id)
    if not (user_is_project_admin(request.user, "dermatology") or patient.uploaded_by_id == request.user.id):
        return JsonResponse({"success": False, "error": "Permission denied"}, status=403)
    try:
        data = _json.loads(request.body) if request.body else request.POST
    except _json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid JSON payload"}, status=400)
    tag_name = (data.get("tag") or data.get("name") or "").strip()
    if not tag_name:
        return JsonResponse({"success": False, "error": "Tag name required"}, status=400)
    tag, _ = Tag.objects.get_or_create(name=tag_name)
    patient.tags.add(tag)
    return JsonResponse({"success": True, "tags": patient.tag_names()})


@login_required
@require_POST
def remove_patient_tag(request, patient_id):
    patient = get_object_or_404(Patient, patient_id=patient_id)
    if not (user_is_project_admin(request.user, "dermatology") or patient.uploaded_by_id == request.user.id):
        return JsonResponse({"success": False, "error": "Permission denied"}, status=403)
    try:
        data = _json.loads(request.body) if request.body else request.POST
    except _json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid JSON payload"}, status=400)
    tag_name = (data.get("tag") or data.get("name") or "").strip()
    if not tag_name:
        return JsonResponse({"success": False, "error": "Tag name required"}, status=400)
    tag = Tag.objects.filter(name=tag_name).first()
    if not tag:
        return JsonResponse({"success": False, "error": "Tag not found"}, status=404)
    patient.tags.remove(tag)
    return JsonResponse({"success": True, "tags": patient.tag_names()})


@login_required
@require_POST
def create_folder(request):
    try:
        if not user_is_project_admin(request.user, "dermatology"):
            return JsonResponse({"error": "Permission denied"}, status=403)

        project = _get_current_project(request)
        if not project:
            return JsonResponse({"error": "No active project"}, status=400)

        data = _json.loads(request.body) if request.body else request.POST
        name = (data.get("name") or "").strip()
        if not name:
            return JsonResponse({"error": "Folder name is required"}, status=400)

        folder, created = Folder.objects.get_or_create(
            name=name,
            parent=None,
            project=project,
            defaults={"created_by": request.user},
        )
        return JsonResponse(
            {
                "success": True,
                "folder": {
                    "id": folder.id,
                    "name": folder.name,
                    "path": folder.name,
                    "created": created,
                },
            }
        )
    except Exception as exc:
        logger.exception("Error creating dermatology folder")
        return JsonResponse({"error": str(exc)}, status=500)


@login_required
def folder_stats(request, folder_id):
    folder = get_object_or_404(Folder, id=folder_id)
    return JsonResponse(
        {
            "success": True,
            "folder": {"id": folder.id, "name": folder.name},
            "stats": {"patient_count": folder.patients.count()},
        }
    )


@login_required
@require_POST
def rename_folder(request, folder_id):
    if not user_is_project_admin(request.user, "dermatology"):
        return JsonResponse({"success": False, "error": "Permission denied"}, status=403)
    try:
        data = _json.loads(request.body) if request.body else request.POST
    except _json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid JSON payload"}, status=400)
    name = (data.get("name") or "").strip()
    if not name:
        return JsonResponse({"success": False, "error": "Folder name is required"}, status=400)
    folder = get_object_or_404(Folder, id=folder_id)
    folder.name = name
    folder.parent = None
    folder.save(update_fields=["name", "parent"])
    return JsonResponse({"success": True, "folder": {"id": folder.id, "name": folder.name}})


@login_required
@require_http_methods(["DELETE"])
def delete_folder(request, folder_id):
    if not user_is_project_admin(request.user, "dermatology"):
        return JsonResponse({"success": False, "error": "Permission denied"}, status=403)

    folder = get_object_or_404(Folder, id=folder_id)

    patient_count = folder.patients.count()
    force = request.GET.get("force") == "true"
    if patient_count and not force:
        return JsonResponse(
            {
                "success": False,
                "error": (
                    f"Folder still contains {patient_count} patient(s). "
                    "Move or delete them first, or pass ?force=true to delete the folder anyway."
                ),
            },
            status=400,
        )

    folder.delete()
    return JsonResponse({"success": True})


@login_required
def folder_permissions(request, folder_id):
    folder = get_object_or_404(Folder, id=folder_id)
    if not user_is_project_admin(request.user, "dermatology"):
        return JsonResponse({"success": False, "error": "Permission denied"}, status=403)
    return JsonResponse(
        {
            "success": True,
            "folder": {"id": folder.id, "name": folder.name},
            "permissions": [],
            "users": [],
        }
    )


@login_required
@require_POST
def upsert_folder_permission(request, folder_id):
    return JsonResponse({"success": False, "error": "Per-folder permissions are not available for dermatology."}, status=400)


@login_required
@require_http_methods(["DELETE"])
def delete_folder_permission(request, folder_id, user_id):
    return JsonResponse({"success": False, "error": "Per-folder permissions are not available for dermatology."}, status=400)


@login_required
@require_POST
def move_patients_to_folder(request):
    try:
        data = _json.loads(request.body) if request.body else request.POST
    except _json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid JSON payload"}, status=400)
    scan_ids = data.get("scan_ids", [])
    folder_id = data.get("folder_id")
    if not isinstance(scan_ids, list) or not scan_ids:
        return JsonResponse({"success": False, "error": "scan_ids list is required"}, status=400)
    if not user_is_project_admin(request.user, "dermatology"):
        return JsonResponse({"success": False, "error": "Permission denied"}, status=403)
    folder = None
    if folder_id and folder_id not in ("root", "all"):
        folder = get_object_or_404(Folder, id=folder_id)
    updated = Patient.objects.filter(patient_id__in=scan_ids).update(folder=folder)
    return JsonResponse({"success": True, "updated": updated})

@login_required
def upload_voice_caption(request, patient_id):
    return JsonResponse({"error": "Voice captions are handled by text captions for Dermatology."}, status=400)


@login_required
@require_POST
def upload_text_caption(request, patient_id):
    patient = get_object_or_404(Patient, patient_id=patient_id)
    can_modify = user_is_project_admin(request.user, "dermatology") or bool(patient.folder and user_can_write_annotations(request.user, patient.folder, request))
    if not can_modify:
        return JsonResponse({"error": "Permission denied"}, status=403)
    try:
        data = _json.loads(request.body) if request.body else request.POST
    except _json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON payload"}, status=400)
    text = data.get("text") or data.get("caption") or ""
    if not text.strip():
        return JsonResponse({"error": "Caption text is required"}, status=400)
    caption = patient.voice_captions.create(
        user=request.user,
        duration=0,
        text_caption=text.strip(),
        original_text_caption=text.strip(),
        processing_status="completed",
    )
    return JsonResponse(
        {
            "success": True,
            "caption": {
                "id": caption.id,
                "user_username": caption.user.username,
                "display_duration": "Text",
                "quality_color": "success",
                "created_at": caption.created_at.strftime("%b %d, %H:%M"),
                "audio_url": None,
                "is_processed": True,
                "text_caption": caption.text_caption,
                "is_text_caption": True,
            },
        }
    )


@login_required
def delete_voice_caption(request, patient_id, caption_id):
    patient = get_object_or_404(Patient, patient_id=patient_id)
    caption = get_object_or_404(patient.voice_captions, id=caption_id)

    is_owner = caption.user_id == request.user.id
    is_admin = user_is_project_admin(request.user, "dermatology")
    if not is_owner and not is_admin:
        return JsonResponse(
            {"error": "You cannot delete voice captions created by other users.", "code": "not_owner"},
            status=403,
        )

    if is_admin and not is_owner:
        data = _json.loads(request.body) if request.body else {}
        if not data.get("admin_confirmed"):
            return JsonResponse(
                {
                    "error": "Admin confirmation required",
                    "code": "admin_confirmation_required",
                    "message": f"You are about to delete a voice caption created by {caption.user.username}. Please confirm this action.",
                },
                status=403,
            )

    caption.delete()
    return JsonResponse({"success": True})


@login_required
@require_POST
def edit_voice_caption_transcription(request, patient_id, caption_id):
    patient = get_object_or_404(Patient, patient_id=patient_id)
    caption = get_object_or_404(patient.voice_captions, id=caption_id)

    is_owner = caption.user_id == request.user.id
    is_admin = user_is_project_admin(request.user, "dermatology")
    if not is_owner and not is_admin:
        return JsonResponse({"error": "You do not have permission to edit this transcription.", "code": "permission_denied"}, status=403)

    try:
        data = _json.loads(request.body) if request.body else {}
        action = data.get("action")

        if action == "edit":
            new_text = (data.get("text") or "").strip()
            if not new_text:
                return JsonResponse({"error": "Transcription text cannot be empty"}, status=400)
            caption.edit_transcription(new_text, request.user)
        elif action == "revert":
            if not caption.original_text_caption:
                return JsonResponse({"error": "No original transcription to revert to"}, status=400)
            caption.text_caption = caption.original_text_caption
            caption.is_edited = False
            caption.save()
        else:
            return JsonResponse({"error": 'Invalid action. Use "edit" or "revert"'}, status=400)
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)

    return JsonResponse(
        {
            "success": True,
            "caption": {
                "id": caption.id,
                "text_caption": caption.text_caption,
                "is_edited": caption.is_edited,
                "edit_history": caption.edit_history,
            },
        }
    )


@login_required
def update_voice_caption_modality(request, patient_id, caption_id):
    return JsonResponse({"error": "Modality is not used for dermatology voice captions."}, status=400)


@login_required
def user_profile(request, username=None):
    return render(request, "dermatology/profile.html", {"profile_user": request.user})


# ─── clinical photo API ────────────────────────────────────────────────────

@login_required
@require_http_methods(["GET"])
def patient_clinical_photo_data(request, patient_id):
    """API endpoint to serve clinical photo data for a dermatology patient"""
    patient = get_object_or_404(Patient, patient_id=patient_id)
    is_admin_user = user_is_project_admin(request.user, "dermatology")
    is_owner = patient.uploaded_by_id == request.user.id
    can_view = is_admin_user or is_owner or patient.visibility == "public" or patient.folder is not None
    if not can_view:
        return JsonResponse({"error": "Permission denied"}, status=403)

    try:
        files = patient.files.filter(file_type="clinical_photo").order_by(
            "metadata__image_index", "created_at", "id"
        )

        if not files.exists():
            return JsonResponse({"error": "No clinical photos found"}, status=404)

        images_data = []
        for fallback_index, file_obj in enumerate(files, start=1):
            if not artifact_exists(file_obj.file_path):
                continue
            image_index = fallback_index
            if isinstance(file_obj.metadata, dict):
                image_index = file_obj.metadata.get("image_index", fallback_index)
            images_data.append(
                {
                    "id": file_obj.id,
                    "source_file_id": file_obj.id,
                    "index": image_index,
                    "original_filename": (
                        file_obj.metadata.get("original_filename", "")
                        if isinstance(file_obj.metadata, dict)
                        else ""
                    ),
                    "url": _serve_file_url(request, file_obj.id),
                }
            )

        if not images_data:
            return JsonResponse(
                {"error": "No clinical photo files found in storage"},
                status=404,
            )

        return JsonResponse({"images": images_data, "count": len(images_data)})

    except Exception as exc:
        logger.error(f"Error serving clinical photo data: {exc}", exc_info=True)
        return JsonResponse({"error": "Internal server error"}, status=500)


# annotation helpers

def _is_hex_color(value):
    return isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value) is not None


def _next_type_order(model_cls, project):
    last_order = (
        model_cls.objects.filter(project=project)
        .order_by("-order")
        .values_list("order", flat=True)
        .first()
    )
    return 0 if last_order is None else last_order + 1


def _annotation_patient_permissions(request, patient):
    is_admin_user = user_is_project_admin(request.user, "dermatology")
    is_owner = patient.uploaded_by_id == request.user.id
    can_view = is_admin_user or is_owner or patient.visibility == "public" or patient.folder is not None
    can_modify = is_admin_user or bool(patient.folder and user_can_write_annotations(request.user, patient.folder, request))
    return can_view, can_modify


def _normalize_float(value, field_name):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be numeric")
    if not math.isfinite(parsed):
        raise ValueError(f"{field_name} must be finite")
    return parsed


def _normalize_file_registry_id(value, field_name="file_registry_id"):
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be an integer")
    if parsed <= 0:
        raise ValueError(f"{field_name} must be > 0")
    return parsed


def _current_project(request):
    return _get_current_project(request)

def _quadrant_type_delete_hook(request, project, obj):
    from .models import QuadrantClassificationMarker

    markers_qs = QuadrantClassificationMarker.objects.filter(quadrant_type=obj)
    if not markers_qs.exists():
        return None

    try:
        data = _json.loads(request.body) if request.body else {}
    except ValueError:
        return JsonResponse({"error": "Invalid JSON body"}, status=400)

    replacement_id = data.get("replacement_id")
    if replacement_id in [None, ""]:
        return JsonResponse({"error": "replacement_id is required to delete a type in use"}, status=400)
    try:
        replacement_id = int(replacement_id)
    except (TypeError, ValueError):
        return JsonResponse({"error": "replacement_id must be an integer"}, status=400)

    if replacement_id == obj.id:
        return JsonResponse({"error": "replacement_id must differ from the deleted type"}, status=400)

    from .models import QuadrantType
    replacement = QuadrantType.objects.filter(id=replacement_id, project=project).first()
    if replacement is None:
        return JsonResponse({"error": "replacement_id must belong to the active project"}, status=400)

    markers_qs.update(quadrant_type=replacement, updated_by=request.user)
    return None


def _types_payload(project, user, TypeModel, ColorModel, type_fk):
    types = list(TypeModel.objects.filter(project=project).order_by("order", "name"))
    user_colors = {
        getattr(pref, type_fk + "_id"): pref.color
        for pref in ColorModel.objects.filter(**{type_fk + "__project": project}, user=user)
    }
    return [{"id": t.id, "name": t.name, "color": user_colors.get(t.id, t.color)} for t in types]


def _handle_type_list(request, TypeModel, ColorModel, type_fk, default_color):
    project = _current_project(request)
    if not project:
        return JsonResponse({"error": "No active project"}, status=403)
    is_admin_user = user_is_project_admin(request.user, "dermatology")

    if request.method == "GET":
        return JsonResponse(
            {"types": _types_payload(project, request.user, TypeModel, ColorModel, type_fk)}
        )

    if not is_admin_user:
        return JsonResponse({"error": "Administrator access required"}, status=403)

    try:
        data = _json.loads(request.body) if request.body else {}
    except ValueError:
        return JsonResponse({"error": "Invalid JSON body"}, status=400)

    name = (data.get("name") or "").strip()
    if not name:
        return JsonResponse({"error": "name is required"}, status=400)

    color = data.get("color", default_color)
    if not _is_hex_color(color):
        return JsonResponse({"error": "color must be a hex value like " + default_color}, status=400)

    obj, created = TypeModel.objects.get_or_create(
        project=project,
        name=name,
        defaults={"color": color, "order": _next_type_order(TypeModel, project)},
    )
    if not created and obj.color != color:
        obj.color = color
        obj.save(update_fields=["color"])

    return JsonResponse({"id": obj.id, "name": obj.name, "color": obj.color}, status=201 if created else 200)


def _handle_type_detail(request, pk, TypeModel, ColorModel, type_fk, conflict_msg, before_delete=None):
    project = _current_project(request)
    if not project:
        return JsonResponse({"error": "No active project"}, status=403)
    is_admin_user = user_is_project_admin(request.user, "dermatology")

    obj = get_object_or_404(TypeModel, pk=pk, project=project)

    if request.method == "PATCH":
        try:
            data = _json.loads(request.body) if request.body else {}
        except ValueError:
            return JsonResponse({"error": "Invalid JSON body"}, status=400)

        has_name = "name" in data
        has_color = "color" in data
        if not has_name and not has_color:
            return JsonResponse({"error": "At least one of name or color is required"}, status=400)

        if has_name and not is_admin_user:
            return JsonResponse({"error": "Administrator access required for rename"}, status=403)

        if has_name:
            name = (data.get("name") or "").strip()
            if not name:
                return JsonResponse({"error": "name cannot be empty"}, status=400)
            obj.name = name
            try:
                obj.save()
            except IntegrityError:
                return JsonResponse({"error": conflict_msg}, status=400)

        if has_color:
            color = data.get("color")
            if not _is_hex_color(color):
                return JsonResponse({"error": "color must be a hex value like #3498db"}, status=400)
            ColorModel.objects.update_or_create(
                **{type_fk: obj, "user": request.user}, defaults={"color": color}
            )

        effective_color = (
            ColorModel.objects.filter(**{type_fk: obj}, user=request.user)
            .values_list("color", flat=True)
            .first()
            or obj.color
        )
        return JsonResponse({"id": obj.id, "name": obj.name, "color": effective_color})

    if not is_admin_user:
        return JsonResponse({"error": "Administrator access required"}, status=403)

    if before_delete is not None:
        maybe_response = before_delete(request, project, obj)
        if maybe_response is not None:
            return maybe_response

    obj.delete()
    return HttpResponse(status=204)


@login_required
@require_http_methods(["GET", "POST"])
def region_types(request):
    from .models import RegionType, RegionTypeUserColor
    return _handle_type_list(request, RegionType, RegionTypeUserColor, "region_type", "#3498db")


@login_required
@require_http_methods(["PATCH", "DELETE"])
def region_type_detail(request, pk):
    from .models import RegionType, RegionTypeUserColor
    return _handle_type_detail(
        request, pk, RegionType, RegionTypeUserColor, "region_type",
        "A region type with this name already exists",
    )


@login_required
@require_http_methods(["GET", "POST"])
def quadrant_types(request):
    from .models import QuadrantType, QuadrantTypeUserColor
    return _handle_type_list(request, QuadrantType, QuadrantTypeUserColor, "quadrant_type", "#e74c3c")


@login_required
@require_http_methods(["PATCH", "DELETE"])
def quadrant_type_detail(request, pk):
    from .models import QuadrantType, QuadrantTypeUserColor
    return _handle_type_detail(
        request, pk, QuadrantType, QuadrantTypeUserColor, "quadrant_type",
        "A quadrant type with this name already exists",
        before_delete=_quadrant_type_delete_hook,
    )


def _normalize_points(points):
    if not isinstance(points, list):
        raise ValueError("points must be a list")
    if len(points) < 4:
        raise ValueError("points must include at least two vertices")
    if len(points) % 2 != 0:
        raise ValueError("points length must be even")
    normalized = []
    for value in points:
        if isinstance(value, bool):
            raise ValueError("points must contain numeric values")
        normalized.append(_normalize_float(value, "points"))
    return normalized


def _normalize_prompt_points(prompt_points):
    if prompt_points is None:
        return []
    if not isinstance(prompt_points, list):
        raise ValueError("prompt_points must be a list")
    normalized = []
    for idx, raw_point in enumerate(prompt_points):
        prefix = f"prompt_points[{idx}]"
        if not isinstance(raw_point, dict):
            raise ValueError(f"{prefix} must be an object")
        if "x" not in raw_point or "y" not in raw_point:
            raise ValueError(f"{prefix} must include x and y")
        x = _normalize_float(raw_point.get("x"), f"{prefix}.x")
        y = _normalize_float(raw_point.get("y"), f"{prefix}.y")
        if x < 0 or x > 1 or y < 0 or y > 1:
            raise ValueError(f"{prefix} coordinates must be normalized between 0 and 1")
        raw_label = raw_point.get("label", 1)
        if isinstance(raw_label, bool):
            raise ValueError(f"{prefix}.label must be 0 or 1")
        try:
            label = int(raw_label)
        except (TypeError, ValueError):
            raise ValueError(f"{prefix}.label must be an integer")
        if label not in (0, 1):
            raise ValueError(f"{prefix}.label must be 0 or 1")
        normalized.append({"x": x, "y": y, "label": label})
    return normalized


def _annotation_payload(annotation):
    return {
        "id": annotation.id,
        "patient_id": annotation.patient_id,
        "file_registry_id": annotation.file_registry_id,
        "region_type_id": annotation.region_type_id,
        "region_type_name": annotation.region_type.name,
        "tool": annotation.tool,
        "points": annotation.points,
        "prompt_points": annotation.prompt_points,
        "stroke_width": annotation.stroke_width,
        "created_by_id": annotation.created_by_id,
        "created_by_username": annotation.created_by.username if annotation.created_by_id else None,
        "updated_by_id": annotation.updated_by_id,
        "updated_by_username": annotation.updated_by.username if annotation.updated_by_id else None,
        "created_at": annotation.created_at.isoformat() if annotation.created_at else None,
        "updated_at": annotation.updated_at.isoformat() if annotation.updated_at else None,
    }


def _quadrant_marker_payload(marker):
    return {
        "id": marker.id,
        "patient_id": marker.patient_id,
        "quadrant_type_id": marker.quadrant_type_id,
        "file_registry_id": marker.file_registry_id,
    }


def _normalize_quadrant_marker_items(raw_markers, project):
    from .models import QuadrantType

    if not isinstance(raw_markers, list):
        raise ValueError("markers must be a list")

    parsed_items = []
    quadrant_type_ids = set()

    for index, raw in enumerate(raw_markers):
        if not isinstance(raw, dict):
            raise ValueError("each marker must be an object")

        raw_marker_id = raw.get("id")
        marker_id = None
        if raw_marker_id not in [None, ""]:
            try:
                marker_id = int(raw_marker_id)
            except (TypeError, ValueError):
                raise ValueError("marker id must be an integer")
            if marker_id <= 0:
                raise ValueError("marker id must be > 0")

        raw_quadrant_type_id = raw.get("quadrant_type_id")
        if raw_quadrant_type_id in [None, ""]:
            raise ValueError("quadrant_type_id is required")
        try:
            quadrant_type_id = int(raw_quadrant_type_id)
        except (TypeError, ValueError):
            raise ValueError("quadrant_type_id must be an integer")

        file_registry_id = _normalize_file_registry_id(raw.get("file_registry_id"))
        parsed = {
            "order": index,
            "id": marker_id,
            "quadrant_type_id": quadrant_type_id,
            "file_registry_id": file_registry_id,
        }
        parsed_items.append(parsed)
        quadrant_type_ids.add(quadrant_type_id)

    quadrant_types = {
        obj.id: obj
        for obj in QuadrantType.objects.filter(project=project, id__in=quadrant_type_ids)
    }
    if len(quadrant_types) != len(quadrant_type_ids):
        raise ValueError("quadrant_type_id must belong to the active project")

    for item in parsed_items:
        item["quadrant_type"] = quadrant_types[item["quadrant_type_id"]]

    dedup_by_photo = {}
    for item in parsed_items:
        dedup_by_photo[item["file_registry_id"]] = item

    return list(dedup_by_photo.values())


def _replace_patient_quadrant_markers(patient, user, marker_items):
    from .models import QuadrantClassificationMarker

    with transaction.atomic():
        keep_ids = []
        for item in marker_items:
            marker, created = QuadrantClassificationMarker.objects.update_or_create(
                patient=patient,
                file_registry_id=item["file_registry_id"],
                defaults={"quadrant_type": item["quadrant_type"], "updated_by": user},
            )
            if created:
                marker.created_by = user
                marker.save(update_fields=["created_by"])
            keep_ids.append(marker.id)

        stale_qs = QuadrantClassificationMarker.objects.filter(patient=patient)
        if keep_ids:
            stale_qs.exclude(id__in=keep_ids).delete()
        else:
            stale_qs.delete()

    return list(
        QuadrantClassificationMarker.objects.filter(patient=patient)
        .select_related("quadrant_type")
        .order_by("id")
    )


@login_required
@require_http_methods(["GET", "PUT"])
def patient_quadrant_markers(request, patient_id):
    from .models import QuadrantClassificationMarker

    patient = get_object_or_404(Patient, patient_id=patient_id)
    can_view, can_modify = _annotation_patient_permissions(request, patient)
    if not can_view:
        return JsonResponse({"error": "Permission denied"}, status=403)

    if request.method == "GET":
        markers = (
            QuadrantClassificationMarker.objects.filter(patient=patient)
            .select_related("quadrant_type")
            .order_by("id")
        )
        return JsonResponse({"markers": [_quadrant_marker_payload(m) for m in markers]})

    if not can_modify:
        return JsonResponse({"error": "Permission denied"}, status=403)

    project = _current_project(request)
    try:
        data = _json.loads(request.body) if request.body else {}
    except ValueError:
        return JsonResponse({"error": "Invalid JSON body"}, status=400)

    try:
        normalized_items = _normalize_quadrant_marker_items(data.get("markers"), project)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    markers = _replace_patient_quadrant_markers(patient=patient, user=request.user, marker_items=normalized_items)
    return JsonResponse({"markers": [_quadrant_marker_payload(m) for m in markers]})


@login_required
@require_http_methods(["GET", "POST"])
def patient_region_annotations(request, patient_id):
    from .models import RegionAnnotation, RegionType

    patient = get_object_or_404(Patient, patient_id=patient_id)
    can_view, can_modify = _annotation_patient_permissions(request, patient)
    if not can_view:
        return JsonResponse({"error": "Permission denied"}, status=403)

    if request.method == "GET":
        annotations = (
            RegionAnnotation.objects.filter(patient=patient)
            .select_related("region_type", "created_by", "updated_by")
            .order_by("created_at")
        )
        return JsonResponse({"annotations": [_annotation_payload(a) for a in annotations]})

    if not can_modify:
        return JsonResponse({"error": "Permission denied"}, status=403)

    try:
        data = _json.loads(request.body) if request.body else {}
    except ValueError:
        return JsonResponse({"error": "Invalid JSON body"}, status=400)

    region_type_id = data.get("region_type_id")
    if region_type_id in [None, ""]:
        return JsonResponse({"error": "region_type_id is required"}, status=400)
    try:
        region_type_id = int(region_type_id)
    except (TypeError, ValueError):
        return JsonResponse({"error": "region_type_id must be an integer"}, status=400)

    tool = str(data.get("tool") or "").strip().lower()
    allowed_tools = {value for value, _ in RegionAnnotation.TOOL_CHOICES}
    if tool not in allowed_tools:
        return JsonResponse({"error": "tool must be one of brush, eraser, polygon"}, status=400)

    try:
        file_registry_id = _normalize_file_registry_id(data.get("file_registry_id"))
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    try:
        points = _normalize_points(data.get("points"))
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    try:
        prompt_points = _normalize_prompt_points(data.get("prompt_points", []))
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    if tool == "polygon" and len(points) < 6:
        return JsonResponse({"error": "polygon requires at least three vertices"}, status=400)

    try:
        stroke_width = _normalize_float(data.get("stroke_width", 1.0), "stroke_width")
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    if stroke_width <= 0:
        return JsonResponse({"error": "stroke_width must be > 0"}, status=400)

    project = _current_project(request)
    region_type = get_object_or_404(RegionType, id=region_type_id, project=project)

    annotation = RegionAnnotation.objects.create(
        patient=patient, region_type=region_type, tool=tool,
        file_registry_id=file_registry_id, points=points, prompt_points=prompt_points,
        stroke_width=stroke_width, created_by=request.user, updated_by=request.user,
    )
    annotation = RegionAnnotation.objects.select_related(
        "region_type", "created_by", "updated_by"
    ).get(id=annotation.id)
    return JsonResponse(_annotation_payload(annotation), status=201)


@login_required
@require_http_methods(["PATCH", "DELETE"])
def region_annotation_detail(request, annotation_id):
    from .models import RegionAnnotation, RegionType

    annotation = get_object_or_404(
        RegionAnnotation.objects.select_related("patient", "region_type", "created_by", "updated_by"),
        id=annotation_id,
    )
    patient = annotation.patient
    can_view, can_modify = _annotation_patient_permissions(request, patient)
    if not can_view:
        return JsonResponse({"error": "Permission denied"}, status=403)

    if request.method == "DELETE":
        if not can_modify:
            return JsonResponse({"error": "Permission denied"}, status=403)
        annotation.delete()
        return HttpResponse(status=204)

    if not can_modify:
        return JsonResponse({"error": "Permission denied"}, status=403)

    try:
        data = _json.loads(request.body) if request.body else {}
    except ValueError:
        return JsonResponse({"error": "Invalid JSON body"}, status=400)

    has_region = "region_type_id" in data
    has_points = "points" in data
    has_prompt_points = "prompt_points" in data
    has_file_registry = "file_registry_id" in data
    has_stroke_width = "stroke_width" in data

    if not (has_region or has_points or has_prompt_points or has_file_registry or has_stroke_width):
        return JsonResponse(
            {"error": "At least one of region_type_id, points, prompt_points, file_registry_id, stroke_width is required"},
            status=400,
        )

    changes = {}
    project = _current_project(request)

    if has_region:
        try:
            region_type_id = int(data.get("region_type_id"))
        except (TypeError, ValueError):
            return JsonResponse({"error": "region_type_id must be an integer"}, status=400)
        region_type = get_object_or_404(RegionType, id=region_type_id, project=project)
        if region_type.id != annotation.region_type_id:
            annotation.region_type = region_type
            changes["region_type_id"] = region_type.id

    if has_points:
        try:
            points = _normalize_points(data.get("points"))
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        if annotation.tool == "polygon" and len(points) < 6:
            return JsonResponse({"error": "polygon requires at least three vertices"}, status=400)
        annotation.points = points
        changes["points"] = points

    if has_prompt_points:
        try:
            prompt_points = _normalize_prompt_points(data.get("prompt_points"))
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        annotation.prompt_points = prompt_points
        changes["prompt_points"] = prompt_points

    if has_file_registry:
        try:
            file_registry_id = _normalize_file_registry_id(data.get("file_registry_id"))
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        annotation.file_registry_id = file_registry_id
        changes["file_registry_id"] = file_registry_id

    if has_stroke_width:
        try:
            stroke_width = _normalize_float(data.get("stroke_width"), "stroke_width")
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        if stroke_width <= 0:
            return JsonResponse({"error": "stroke_width must be > 0"}, status=400)
        annotation.stroke_width = stroke_width
        changes["stroke_width"] = stroke_width

    if not changes:
        return JsonResponse(_annotation_payload(annotation))

    annotation.updated_by = request.user
    annotation.save()
    annotation.refresh_from_db()
    return JsonResponse(_annotation_payload(annotation))

@login_required
def export_new(request):
    """Create-export page. Reuses the maxillo template with ns='dermatology'."""
    project = _get_current_project(request)
    if project is None:
        messages.error(request, "Select a project before creating an export.")
        return redirect("dermatology:patient_list")

    from common import export_catalog, export_ui
    from common.export_processing import start_export_processing

    if request.method == "POST":
        folder_ids = [int(fid) for fid in request.POST.getlist("folder_ids")]
        artifact_keys = request.POST.getlist("artifacts")
        filters = export_catalog.filters_from_form(request.POST)

        if not folder_ids:
            messages.error(request, "Please select at least one folder.")
            return redirect("dermatology:export_new")

        if Folder.objects.filter(id__in=folder_ids, project=project).count() != len(set(folder_ids)):
            messages.error(request, "Select folders from the current project only.")
            return redirect("dermatology:export_new")

        allowed_keys = export_ui.allowed_artifact_keys("dermatology", project)
        artifact_keys = [key for key in artifact_keys if key in allowed_keys]
        if not artifact_keys:
            messages.error(request, "Please select at least one artifact to export.")
            return redirect("dermatology:export_new")

        artifacts = export_catalog.resolve_artifacts("dermatology", artifact_keys)
        query_params = {
            "domain": "dermatology",
            "project_id": project.id,
            "folder_ids": folder_ids,
            "artifacts": artifact_keys,
            "filters": filters,
            "modality_slugs": sorted(export_catalog.modality_slugs_for(artifacts)),
        }

        summary_parts = [f"{len(folder_ids)} folder{'s' if len(folder_ids) != 1 else ''}"]
        summary_parts.append(", ".join(a.label for a in artifacts) or "nothing")
        described = export_catalog.describe_filters(
            "dermatology", project, [m.slug for m in export_ui.project_modalities(project)], filters
        )
        if described:
            summary_parts.append(", ".join(described))

        export = Export.objects.create(
            user=request.user,
            status="pending",
            query_params=query_params,
            query_summary=", ".join(summary_parts),
        )

        start_export_processing(export.id, "dermatology")
        messages.success(request, f"Export #{export.id} created and processing started.")
        return redirect("dermatology:export_list")

    folders = export_ui.folder_tree(
        filter_folders_for_user(
            request.user,
            Folder.objects.filter(project=project).order_by("name"),
            "dermatology",
        ),
        Patient,
        "dermatology",
    )
    visible_folder_ids = [entry["folder"].id for entry in folders]
    patients_in_scope = Patient.objects.filter(folder_id__in=visible_folder_ids)
    modalities = export_ui.project_modalities(project)

    return render(
        request,
        "maxillo/export_new.html",
        {
            "project": project,
            "folders": folders,
            "modalities": modalities,
            "artifact_groups": export_ui.artifact_groups(
                "dermatology", project, patients_in_scope, patient_fk="dermatology_patient"
            ),
            "filter_groups": export_ui.grouped_filters(
                "dermatology", project, [m.slug for m in modalities]
            ),
            "ns": "dermatology",
        },
    )