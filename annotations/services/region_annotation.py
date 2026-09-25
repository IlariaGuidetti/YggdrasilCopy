"""Saving and reading dermatology region annotations and body-site markers,
through the same shared writer measurements and tooth segmentation use.

A clinical photograph is its own SourceResource (no time, no slice), so one
save covers everything drawn on one photo: the region shapes and, if set,
which body site the photo was taken of.
"""

from annotations.adapters.dermatology import region_annotation, quadrant_marker
from annotations.constants import AnnotationOrigin, CoordinateSystem, ResourceKind
from annotations.services.viewer import save_measurement_groups
from common.models import AnnotationMethod

#: The AnnotationMethod slug that gates this work (None = ungated).
REGION_METHOD_SLUG = "dermatology_region_annotation"

#: This surface's own AnnotationSet.kind -- a region annotation is not a
#: measurement and filing it as one would collide with anything else the
#: patient has under that kind.
REGION_KIND = "dermatology_region_annotation"

IMAGE_ROLE = "image"


def region_method():
    """The AnnotationMethod row, or None if the registry has no such entry."""
    return AnnotationMethod.objects.filter(slug=REGION_METHOD_SLUG).first()


def _domain_field(patient):
    return "dermatology_patient" if patient._meta.app_label == "dermatology" else "patient"


def save_dermatology_annotations(
    patient,
    *,
    file_obj,
    shapes,
    quadrant_name=None,
    author=None,
    expected_revision=None,
    annotation_method=None,
    note="",
    origin=AnnotationOrigin.MANUAL,
):
    """Write one revision holding every shape drawn on ``file_obj``.

    :param file_obj: the ``FileRegistry`` row for the clinical photograph.
    :param shapes: ``[{"tool": "brush"|"eraser"|"polygon", "points": [x1, y1, ...],
        "strokeWidth": float, "regionName": str}]``. An empty list clears the
        photo's annotations (the group is still written, carrying nothing).
    :param quadrant_name: body-site name for this photo, or None to leave it
        unset. Converted to a single event alongside the shapes.
    :returns: the new AnnotationRevision.
    """
    shapes = list(shapes or [])

    def _translate(group):
        descriptors = []
        for shape in group["shapes"]:
            descriptors.extend(
                region_annotation(
                    tool=shape["tool"],
                    points=shape["points"],
                    stroke_width=shape.get("strokeWidth"),
                    region_name=shape.get("regionName"),
                )
            )
        if group["quadrant_name"]:
            descriptors.extend(quadrant_marker(quadrant_name=group["quadrant_name"]))
        return descriptors

    groups = [
        {
            "file_obj": file_obj,
            "file_key": None,
            "annotations": [],
            "shapes": shapes,
            "quadrant_name": quadrant_name,
            "descriptor": {},
            "resource_kind": ResourceKind.FILE,
            "role": IMAGE_ROLE,
            "order": 0,
        }
    ]

    return save_measurement_groups(
        patient,
        groups=groups,
        author=author,
        expected_revision=expected_revision,
        coordinate_system=CoordinateSystem.IMAGE_PIXEL,
        annotation_method=annotation_method or region_method(),
        note=note,
        origin=origin,
        kind=REGION_KIND,
        label_schema=None,
        require_labels=False,
        translate=_translate,
        store_payload=False,
        reclaim_primary=False,
        primary_index=0,
    )
def dermatology_annotation_state(patient):
    """The shapes and quadrant marker recorded for this patient's photograph.

    Returns ``{"revision": int, "setId": int|None, "shapes": [...], "quadrantName": str|None, "updatedAt": datetime|None}``.
    """
    from annotations.models import AnnotationSet, EventAnnotationItem, Geometry2DItem

    annotation_set = (
        AnnotationSet.objects.filter(dermatology_patient=patient, kind=REGION_KIND)
        .order_by("id")
        .first()
    )
    empty = {"revision": 0, "setId": None, "shapes": [], "quadrantName": None, "updatedAt": None}
    if annotation_set is None:
        return empty

    revision = annotation_set.revisions.order_by("-revision_number").first()
    if revision is None:
        return {**empty, "setId": annotation_set.id, "updatedAt": annotation_set.updated_at}

    shapes = []
    for item in Geometry2DItem.objects.filter(revision=revision).order_by("order", "id"):
        attrs = item.attributes if isinstance(item.attributes, dict) else {}
        shapes.append({
            "tool": attrs.get("tool", "brush"),
            "points": [coord for point in item.points for coord in point],
            "strokeWidth": item.stroke_width,
            "regionName": attrs.get("region_name") or None,
        })

    quadrant_name = None
    event = EventAnnotationItem.objects.filter(revision=revision, event_type="quadrant").order_by("-id").first()
    if event is not None:
        quadrant_name = event.value or None

    return {
        "revision": revision.revision_number,
        "setId": annotation_set.id,
        "shapes": shapes,
        "quadrantName": quadrant_name,
        "updatedAt": annotation_set.updated_at,
    }