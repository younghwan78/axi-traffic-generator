"""
Image Format Descriptor

Provides per-format metadata (BPP, sub-sampling, plane count)
and computes per-plane dimensions and strides.
"""

from dataclasses import dataclass
from typing import List
from math import ceil


@dataclass
class PlaneInfo:
    """Describes a single image plane's geometry."""
    index: int          # 0=Y, 1=UV, 2=V
    width: int          # Pixel width of this plane
    height: int         # Pixel height of this plane
    bpp: float          # Bytes per pixel for this plane
    stride: int         # Row stride in bytes (aligned)
    line_bytes: int     # Actual useful bytes per line (unaligned)
    total_bytes: int    # Total plane size = stride * height


# ---------------------------------------------------------------------------
#  Format Database
# ---------------------------------------------------------------------------

# Each entry: bpp_y, bpp_uv (per UV pixel pair), planes, sub_h, sub_v
#   sub_h: horizontal chroma sub-sampling factor (2 = 4:2:0/4:2:2)
#   sub_v: vertical chroma sub-sampling factor   (2 = 4:2:0)
FORMAT_DB = {
    # YUV 4:2:0 ----------------------------------------------------------------
    "YUV420_8bit_2plane":  {"bpp_y": 1.0,  "bpp_uv": 1.0,  "planes": 2, "sub_h": 2, "sub_v": 2},
    "YUV420_10bit_2plane": {"bpp_y": 1.25, "bpp_uv": 1.25, "planes": 2, "sub_h": 2, "sub_v": 2},
    # YUV 4:2:2 ----------------------------------------------------------------
    "YUV422_8bit_2plane":  {"bpp_y": 1.0,  "bpp_uv": 1.0,  "planes": 2, "sub_h": 2, "sub_v": 1},
    "YUV422_10bit_2plane": {"bpp_y": 1.25, "bpp_uv": 1.25, "planes": 2, "sub_h": 2, "sub_v": 1},
    # YUV 4:4:4 ----------------------------------------------------------------
    "YUV444_8bit_3plane":  {"bpp_y": 1.0,  "bpp_uv": 1.0,  "planes": 3, "sub_h": 1, "sub_v": 1},
    # RGB ----------------------------------------------------------------------
    "RGB_8bit":   {"bpp_y": 3.0, "planes": 1, "sub_h": 1, "sub_v": 1},
    "RGB_10bit":  {"bpp_y": 4.0, "planes": 1, "sub_h": 1, "sub_v": 1},
    "RGBA_8bit":  {"bpp_y": 4.0, "planes": 1, "sub_h": 1, "sub_v": 1},
    # Bayer --------------------------------------------------------------------
    "Bayer_8bit":  {"bpp_y": 1.0,  "planes": 1, "sub_h": 1, "sub_v": 1},
    "Bayer_10bit": {"bpp_y": 1.25, "planes": 1, "sub_h": 1, "sub_v": 1},
    "Bayer_12bit": {"bpp_y": 1.5,  "planes": 1, "sub_h": 1, "sub_v": 1},
    # RAW (stat / metadata) ----------------------------------------------------
    "RAW": {"bpp_y": 1.0, "planes": 1, "sub_h": 1, "sub_v": 1},
}


class ImageFormatDescriptor:
    """
    Computes per-plane geometry from a format string and image resolution.

    Usage:
        planes = ImageFormatDescriptor.get_plane_info("YUV420_8bit_2plane", 1920, 1080)
        for p in planes:
            print(p.index, p.width, p.height, p.stride, p.total_bytes)
    """

    @staticmethod
    def get_format_entry(format_str: str) -> dict:
        """
        Look up format metadata.  Falls back to RAW if not found.

        Args:
            format_str: Format string (e.g. "YUV420_8bit_2plane")

        Returns:
            Format entry dictionary
        """
        if format_str in FORMAT_DB:
            return FORMAT_DB[format_str]

        # Fuzzy fallback: try case-insensitive prefix match
        upper = format_str.upper()
        for key, val in FORMAT_DB.items():
            if key.upper() == upper:
                return val

        # Default to single-plane RAW
        return FORMAT_DB["RAW"]

    @staticmethod
    def calculate_stride(width_bytes: int, alignment: int = 64) -> int:
        """
        Align row width to *alignment* bytes.

        Args:
            width_bytes: Actual bytes per row
            alignment: Alignment boundary (default 64B)

        Returns:
            Aligned stride in bytes
        """
        return int(ceil(width_bytes / alignment)) * alignment

    @staticmethod
    def get_plane_info(format_str: str, width: int, height: int,
                       stride_align: int = 64) -> List[PlaneInfo]:
        """
        Compute per-plane dimensions and strides.

        For NV12 (YUV420 2-plane):
          - Plane 0 (Y):  width x height,  bpp = 1.0
          - Plane 1 (UV): width/2 x height/2, bpp = 1.0 per component pair
                          (interleaved U+V → effective width stays same)

        Args:
            format_str: Format name (key in FORMAT_DB)
            width: Image pixel width
            height: Image pixel height
            stride_align: Stride alignment in bytes (default 64)

        Returns:
            List of PlaneInfo, one per plane
        """
        fmt = ImageFormatDescriptor.get_format_entry(format_str)
        planes: List[PlaneInfo] = []
        num_planes = fmt["planes"]
        bpp_y = fmt["bpp_y"]
        sub_h = fmt["sub_h"]
        sub_v = fmt["sub_v"]

        # Plane 0 (Luma / only plane for RGB/Bayer/RAW)
        p0_w = width
        p0_h = height
        p0_bpp = bpp_y
        p0_line = int(p0_w * p0_bpp)
        p0_stride = ImageFormatDescriptor.calculate_stride(p0_line, stride_align)
        planes.append(PlaneInfo(
            index=0, width=p0_w, height=p0_h, bpp=p0_bpp,
            stride=p0_stride, line_bytes=p0_line,
            total_bytes=p0_stride * p0_h,
        ))

        if num_planes >= 2:
            bpp_uv = fmt.get("bpp_uv", bpp_y)
            # NV12-style: UV interleaved → pixel width = width/sub_h * 2 components
            # but stored as pairs so effective bpp doubles relative to single comp.
            p1_w = width // sub_h
            p1_h = height // sub_v
            # For NV12 (2-plane), UV are interleaved → line bytes = width * bpp_uv
            # because each UV pair covers sub_h luma pixels
            p1_line = int(width * bpp_uv) // sub_v if sub_v > 1 else int(width * bpp_uv)
            # More accurate: for NV12, UV line = (width/sub_h) * 2 * byte_per_component
            # Simplification: NV12 UV line bytes = width * bpp_uv (same as luma line)
            # Actually for NV12: UV line = width * 1 byte (U) + width * 1 byte (V) interleaved
            # but sub_h=2 means each pair: (Cb, Cr) for 2 luma pixels
            # So UV line bytes = (width / sub_h) * 2 * (bpp_uv_per_component)
            # For 8-bit NV12: UV line = (width/2)*2*1 = width bytes
            # For 10-bit: UV line = (width/2)*2*1.25 = width*1.25 bytes
            p1_line = int(p1_w * 2 * bpp_uv)  # 2 components (U+V) interleaved
            p1_bpp = bpp_uv * 2  # effective bpp for UV pair
            p1_stride = ImageFormatDescriptor.calculate_stride(p1_line, stride_align)
            planes.append(PlaneInfo(
                index=1, width=p1_w, height=p1_h, bpp=p1_bpp,
                stride=p1_stride, line_bytes=p1_line,
                total_bytes=p1_stride * p1_h,
            ))

        if num_planes >= 3:
            bpp_uv = fmt.get("bpp_uv", bpp_y)
            # 3-plane: separate U and V planes
            p2_w = width // sub_h
            p2_h = height // sub_v
            p2_line = int(p2_w * bpp_uv)
            p2_bpp = bpp_uv
            p2_stride = ImageFormatDescriptor.calculate_stride(p2_line, stride_align)
            planes.append(PlaneInfo(
                index=2, width=p2_w, height=p2_h, bpp=p2_bpp,
                stride=p2_stride, line_bytes=p2_line,
                total_bytes=p2_stride * p2_h,
            ))

        return planes

    @staticmethod
    def get_total_size(format_str: str, width: int, height: int,
                       stride_align: int = 64) -> int:
        """
        Total buffer size across all planes.

        Args:
            format_str: Format name
            width: Image pixel width
            height: Image pixel height
            stride_align: Stride alignment (default 64)

        Returns:
            Total bytes
        """
        planes = ImageFormatDescriptor.get_plane_info(format_str, width, height, stride_align)
        return sum(p.total_bytes for p in planes)
