"""Dermatology-specific export modality mappings."""

DERMATOLOGY_EXPORT_MODALITY_FILE_TYPES = {
    "clinical_photo": {
        "raw": ["clinical_photo"],
        "processed": ["clinical_photo"],
    },
}


def install_dermatology_export_mappings():
    """Compatibility hook kept for app startup."""
    return DERMATOLOGY_EXPORT_MODALITY_FILE_TYPES