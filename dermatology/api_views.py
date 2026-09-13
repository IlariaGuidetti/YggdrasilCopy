"""Dermatology processing API endpoints."""
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from common.file_access import streaming_response
from common.models import FileRegistry, Job
from common.permissions import user_is_project_admin


def health_check(request):
    return JsonResponse({"status": "ok", "domain": "dermatology"})


class ProcessingJobListView:
    @classmethod
    def as_view(cls):
        def view(request):
            jobs = Job.objects.filter(domain="dermatology").order_by("-created_at")[:100]
            return JsonResponse({
                "jobs": [
                    {
                        "id": job.id,
                        "modality_slug": job.modality_slug,
                        "status": job.status,
                        "patient_id": job.dermatology_patient_id,
                    }
                    for job in jobs
                ]
            })
        return view


def get_job_status(request, job_id):
    job = Job.objects.filter(id=job_id, domain="dermatology").first()
    if not job:
        return JsonResponse({"error": "Job not found"}, status=404)
    return JsonResponse({"id": job.id, "status": job.status, "output_files": job.output_files})


@require_http_methods(["POST"])
def runner_claim_job(request, job_id):
    return get_job_status(request, job_id)


@require_http_methods(["POST"])
def runner_complete_job(request, job_id):
    job = Job.objects.filter(id=job_id, domain="dermatology").first()
    if not job:
        return JsonResponse({"error": "Job not found"}, status=404)
    job.mark_completed()
    return JsonResponse({"ok": True, "status": job.status})


@require_http_methods(["POST"])
def runner_fail_job(request, job_id):
    job = Job.objects.filter(id=job_id, domain="dermatology").first()
    if not job:
        return JsonResponse({"error": "Job not found"}, status=404)
    job.status = "failed"
    job.save(update_fields=["status"])
    return JsonResponse({"ok": True, "status": job.status})


@login_required
def get_file_registry(request):
    files = FileRegistry.objects.filter(domain="dermatology").order_by("-created_at")[:100]
    return JsonResponse({
        "files": [
            {
                "id": item.id,
                "file_type": item.file_type,
                "file_path": item.file_path,
                "patient_id": item.dermatology_patient_id,
            }
            for item in files
        ]
    })


@login_required
def serve_file(request, file_id):
    file_obj = FileRegistry.objects.filter(id=file_id, domain="dermatology").first()
    if not file_obj:
        return JsonResponse({"error": "File not found"}, status=404)

    patient = file_obj.dermatology_patient
    if patient:
        is_admin_user = user_is_project_admin(request.user, "dermatology")
        is_owner = patient.uploaded_by_id == request.user.id
        can_view = is_admin_user or is_owner or patient.visibility == "public" or patient.folder is not None
        if not can_view:
            return JsonResponse({"error": "Permission denied"}, status=403)

    return streaming_response(
        path_or_key=file_obj.file_path,
        content_type="application/octet-stream",
        filename=file_obj.file_path.rstrip("/").split("/")[-1] or "file",
    )