from django.shortcuts import redirect
from django.urls import path

from dermatology import api_views, views
from maxillo.views import export as maxillo_export


app_name = "dermatology"

urlpatterns = [
    path("", views.set_dermatology_home, name="home"),
    path("patients/", views.patient_list, name="patient_list"),
    path("upload/", views.upload_patient, name="upload_patient"),
    path("project/<int:project_id>/select/", views.select_project, name="select_project"),
    path("patient/<int:patient_id>/", views.patient_detail, name="patient_detail"),
    path("patient/<int:patient_id>/update-name/", views.update_patient_name, name="update_patient_name"),

    path("patient/<int:patient_id>/voice-caption/", views.upload_voice_caption, name="upload_voice_caption"),
    path("patient/<int:patient_id>/text-caption/", views.upload_text_caption, name="upload_text_caption"),
    path("patient/<int:patient_id>/voice-caption/<int:caption_id>/delete/", views.delete_voice_caption, name="delete_voice_caption"),
    path("patient/<int:patient_id>/voice-caption/<int:caption_id>/edit/", views.edit_voice_caption_transcription, name="edit_voice_caption_transcription"),
    path("patient/<int:patient_id>/voice-caption/<int:caption_id>/update-modality/", views.update_voice_caption_modality, name="update_voice_caption_modality"),

    path("patient/<int:patient_id>/tags/add/", views.add_patient_tag, name="add_patient_tag"),
    path("patient/<int:patient_id>/tags/remove/", views.remove_patient_tag, name="remove_patient_tag"),

    path("patient/<int:patient_id>/delete/", views.delete_patient, name="delete_patient"),
    path("patients/bulk-delete/", views.bulk_delete_patients, name="bulk_delete_patients"),

    path("admin/control-panel/", lambda request: redirect("admin_control_panel"), name="admin_control_panel"),
    path("profile/", views.user_profile, name="user_profile"),
    path("profile/<str:username>/", views.user_profile, name="user_profile_by_username"),

    path("folders/create/", views.create_folder, name="create_folder"),
    path("folders/<int:folder_id>/stats/", views.folder_stats, name="folder_stats"),
    path("folders/<int:folder_id>/rename/", views.rename_folder, name="rename_folder"),
    path("folders/<int:folder_id>/delete/", views.delete_folder, name="delete_folder"),
    path("folders/<int:folder_id>/permissions/", views.folder_permissions, name="folder_permissions"),
    path("folders/<int:folder_id>/permissions/upsert/", views.upsert_folder_permission, name="upsert_folder_permission"),
    path("folders/<int:folder_id>/permissions/<int:user_id>/delete/", views.delete_folder_permission, name="delete_folder_permission"),
    path("folders/move-patients/", views.move_patients_to_folder, name="move_patients_to_folder"),

    path("export/", maxillo_export.export_list, name="export_list"),
    path("export/new/", views.export_new, name="export_new"),
    path("export/preview/", maxillo_export.export_preview, name="export_preview"),
    path("export/<int:export_id>/", maxillo_export.export_status, name="export_status"),
    path("export/<int:export_id>/download/", maxillo_export.export_download, name="export_download"),
    path("export/<int:export_id>/share/", maxillo_export.export_share_update, name="export_share_update"),
    path("export/shared/<str:share_token>/", maxillo_export.export_shared_landing, name="export_shared_landing"),
    path("export/shared/<str:share_token>/download/", maxillo_export.export_shared_download, name="export_shared_download"),
    path("export/<int:export_id>/delete/", maxillo_export.export_delete, name="export_delete"),
    path("export/<int:export_id>/stop/", maxillo_export.export_stop, name="export_stop"),

    # Clinical photo API
    path("api/patient/<int:patient_id>/clinical-photo/", views.patient_clinical_photo_data, name="patient_clinical_photo_data"),

    # Region/quadrant annotation API
    path("api/patient/<int:patient_id>/quadrant-markers/", views.patient_quadrant_markers, name="patient_quadrant_markers"),
    path("api/quadrant-types/", views.quadrant_types, name="quadrant_types"),
    path("api/quadrant-types/<int:pk>/", views.quadrant_type_detail, name="quadrant_type_detail"),
    path("api/patient/<int:patient_id>/annotations/", views.patient_region_annotations, name="patient_region_annotations"),
    path("api/annotations/<int:annotation_id>/", views.region_annotation_detail, name="region_annotation_detail"),
    path("api/region-types/", views.region_types, name="region_types"),
    path("api/region-types/<int:pk>/", views.region_type_detail, name="region_type_detail"),

    # Generic file/processing API
    path("api/processing/health/", api_views.health_check, name="api_health_check"),
    path("api/processing/jobs/", api_views.ProcessingJobListView.as_view(), name="api_processing_jobs"),
    path("api/processing/jobs/<int:job_id>/status/", api_views.get_job_status, name="api_get_job_status"),
    path("api/runner/jobs/<int:job_id>/claim/", api_views.runner_claim_job, name="api_runner_claim_job"),
    path("api/runner/jobs/<int:job_id>/complete/", api_views.runner_complete_job, name="api_runner_complete_job"),
    path("api/runner/jobs/<int:job_id>/fail/", api_views.runner_fail_job, name="api_runner_fail_job"),
    path("api/processing/files/", api_views.get_file_registry, name="api_get_file_registry"),
    path("api/processing/files/serve/<int:file_id>/", api_views.serve_file, name="api_serve_file"),
]