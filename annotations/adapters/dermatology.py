"""Translating dermatology's region annotations and body-site markers.

Pure: legacy/live values in, descriptor dicts out. Nothing here queries,
resolves a label, or knows whether the target already exists.

A region annotation is drawn on one clinical photograph -- the photograph
itself is the SourceResource, not a frame inside it -- so unlike laparoscopy's
RegionAnnotation there is no time to convert and no selector: the coordinate
system is IMAGE_PIXEL, exactly as intraoral tooth polygons are, because
tooth_segmentation.py already established that a photograph is its own
addressable surface with no slice or frame index needed.

Body-site markers (QuadrantClassificationMarker) name which anatomical region
a photograph was taken of. There is no time either, so this converts to a
single event with no selector, mirroring legacy_laparoscopy.quadrant_marker
minus the millisecond timestamp.
"""

from django.core.exceptions import ValidationError

from annotations.adapters import descriptors
from annotations.constants import CoordinateSystem, Geometry2DType

#: Legacy tool names on RegionAnnotation, mapped to a geometry type. Mirrors
#: laparoscopy's TOOL_GEOMETRY: brush and eraser are both freehand strokes,
#: and which one it was is kept in attributes so a replay can tell them apart.
TOOL_GEOMETRY = {
    "brush": Geometry2DType.FREEHAND,
    "eraser": Geometry2DType.FREEHAND,
    "polygon": Geometry2DType.POLYGON,
}


def _unflatten(points):
    """Konva stores a polyline as a flat ``[x1, y1, x2, y2, ...]`` array."""
    if not isinstance(points, list):
        raise ValidationError("points must be a list")
    if len(points) % 2:
        raise ValidationError(
            f"a flat point array has an even length; got {len(points)} ordinates"
        )
    return [[points[i], points[i + 1]] for i in range(0, len(points), 2)]


def region_annotation(*, tool, points, stroke_width=None, region_name=None):
    """Convert one dermatology ``RegionAnnotation`` row into a 2D descriptor.

    Coordinates are image pixels and are **not** normalised: the photograph
    they were drawn on is the resource, so rescaling them here would make the
    stored form differ from what the clinician drew for no reason a
    cross-check could explain -- the same rule tooth_segmentation.py follows.
    """
    geometry_type = TOOL_GEOMETRY.get(tool)
    if geometry_type is None:
        raise ValidationError(f"unknown annotation tool {tool!r}")

    return [
        descriptors.geometry_2d(
            geometry_type=geometry_type,
            coordinate_system=CoordinateSystem.IMAGE_PIXEL,
            points=_unflatten(points),
            closed=geometry_type == Geometry2DType.POLYGON,
            stroke_width=stroke_width,
            attributes={"tool": tool, "region_name": region_name or ""},
        )
    ]


def quadrant_marker(*, quadrant_name=None):
    """Convert one ``QuadrantClassificationMarker`` into an event descriptor.

    No time_ms: the marker names the body site a whole photograph was taken
    of, not a moment inside a recording, so there is nothing to convert.
    """
    return [
        descriptors.event(
            event_type="quadrant",
            value=quadrant_name or "",
            label_code=quadrant_name,
            attributes={"quadrant": quadrant_name} if quadrant_name else {},
        )
    ]