"""
Stream Generator

Generates AXI transaction streams with:
  - 64B Boundary Chopping
  - Raster-order and Z-order access patterns
  - Per-plane stream generation (optional UV interleaving)
"""

from abc import ABC, abstractmethod
from math import ceil
from typing import List, Tuple, Generator as Gen, Optional

from domain_model import AxiTransaction
from format_descriptor import ImageFormatDescriptor, PlaneInfo


# ============================================================================
#  Stream wrapper
# ============================================================================

class Stream:
    """
    Wrapper for a list of transactions with metadata.
    """

    def __init__(self, ip_name: str, transactions: List[AxiTransaction],
                 line_size: int = 0, h_size: int = 0, bpp: float = 0,
                 plane_index: int = 0):
        self.ip_name = ip_name
        self.transactions = transactions
        self.line_size = line_size
        self.h_size = h_size
        self.bpp = bpp
        self.plane_index = plane_index

    def get_first(self) -> AxiTransaction:
        return self.transactions[0] if self.transactions else None

    def get_last(self) -> AxiTransaction:
        return self.transactions[-1] if self.transactions else None

    def get_line_transactions(self, line_idx: int) -> List[AxiTransaction]:
        if self.line_size == 0:
            return []
        burst_size = 64
        txs_per_line = (self.line_size + burst_size - 1) // burst_size
        start_idx = line_idx * txs_per_line
        end_idx = min(start_idx + txs_per_line, len(self.transactions))
        if start_idx >= len(self.transactions):
            return []
        return self.transactions[start_idx:end_idx]

    def get_line_count(self) -> int:
        if self.line_size == 0:
            return 0
        burst_size = 64
        txs_per_line = (self.line_size + burst_size - 1) // burst_size
        if txs_per_line == 0:
            return 0
        return (len(self.transactions) + txs_per_line - 1) // txs_per_line

    def __len__(self) -> int:
        return len(self.transactions)

    def __iter__(self):
        return iter(self.transactions)


# ============================================================================
#  64-Byte Boundary Chopper
# ============================================================================

def chop_at_64b_boundary(addr: int, requested_size: int) -> List[Tuple[int, int]]:
    """
    Split a transfer that crosses 64-byte aligned boundaries.

    Example:
        addr=0x1030, size=64  ->  [(0x1030, 16), (0x1040, 48)]
        addr=0x1000, size=128 ->  [(0x1000, 64), (0x1040, 64)]
        addr=0x1000, size=64  ->  [(0x1000, 64)]

    Args:
        addr: Starting address
        requested_size: Requested transfer size in bytes

    Returns:
        List of (address, size) tuples, each ≤ 64B and not crossing a boundary
    """
    chunks: List[Tuple[int, int]] = []
    remaining = requested_size
    current = addr

    while remaining > 0:
        # Distance to next 64B boundary
        boundary = ((current >> 6) + 1) << 6   # ((current // 64) + 1) * 64
        can_send = min(remaining, 64, boundary - current)
        chunks.append((current, can_send))
        current += can_send
        remaining -= can_send

    return chunks


# ============================================================================
#  Access Pattern Strategies
# ============================================================================

class AccessPattern(ABC):
    """Base class for memory access pattern generators."""

    @abstractmethod
    def generate_addresses(self, plane: PlaneInfo,
                           start_addr: int) -> Gen[Tuple[int, int], None, None]:
        """
        Yield (address, raw_size) pairs for one plane.
        raw_size may exceed 64 bytes; caller applies 64B chopping.
        """


class RasterOrderPattern(AccessPattern):
    """
    Line-by-line linear scan respecting stride.

    Yields one chunk per line (line_bytes), which the caller then chops
    at 64B boundaries.
    """

    def generate_addresses(self, plane: PlaneInfo,
                           start_addr: int) -> Gen[Tuple[int, int], None, None]:
        for line in range(plane.height):
            line_addr = start_addr + line * plane.stride
            remaining = plane.line_bytes
            offset = 0
            while remaining > 0:
                chunk = min(remaining, 64)
                yield (line_addr + offset, chunk)
                offset += chunk
                remaining -= chunk


class ZOrderPattern(AccessPattern):
    """
    Macro-tile scanning (e.g. 64x32 tiles).

    Tiles are laid out linearly in memory (Option A from spec).
    Within each tile, data is read sequentially.
    """

    def __init__(self, tile_w: int = 64, tile_h: int = 32):
        self.tile_w = tile_w
        self.tile_h = tile_h

    def generate_addresses(self, plane: PlaneInfo,
                           start_addr: int) -> Gen[Tuple[int, int], None, None]:
        tiles_x = ceil(plane.width / self.tile_w)
        tiles_y = ceil(plane.height / self.tile_h)
        tile_bytes = int(self.tile_w * self.tile_h * plane.bpp)

        for ty in range(tiles_y):
            for tx in range(tiles_x):
                tile_idx = ty * tiles_x + tx
                tile_base = start_addr + tile_idx * tile_bytes
                remaining = tile_bytes
                offset = 0
                while remaining > 0:
                    chunk = min(remaining, 64)
                    yield (tile_base + offset, chunk)
                    offset += chunk
                    remaining -= chunk


def create_access_pattern(pattern_name: str,
                          tile_w: int = 64,
                          tile_h: int = 32) -> AccessPattern:
    """Factory for access pattern strategies."""
    name = pattern_name.lower().replace("-", "").replace("_", "")
    if name == "zorder":
        return ZOrderPattern(tile_w, tile_h)
    return RasterOrderPattern()


# ============================================================================
#  Stream Generator
# ============================================================================

class StreamGenerator:
    """
    Generates AXI transaction streams from high-level parameters.
    Supports both legacy (flat) mode and new YAML-based mode.
    """

    # ------------------------------------------------------------------
    #  Legacy interface (CSV mode – kept for backward compatibility)
    # ------------------------------------------------------------------
    @staticmethod
    def generate_stream(port: str, tx_type: str, start_addr: int,
                        total_size: int, burst_size: int = 64,
                        line_size: int = 0, h_size: int = 0, bpp: float = 0,
                        llc_enable: bool = False, line_delay: int = 0) -> Stream:
        """
        Generate a stream of AXI transactions (legacy flat mode).

        Args:
            port: Port/IP name
            tx_type: Transaction type ("ReadNoSnoop" or "WriteNoSnoop")
            start_addr: Starting memory address
            total_size: Total transfer size in bytes
            burst_size: Size of each burst in bytes (default 64)
            line_size: Size of one line in bytes (for OTF sync)
            h_size: Horizontal pixel count
            bpp: Bytes per pixel
            llc_enable: Enable LLC allocation hint (default False)
            line_delay: Initial line delay in cycles (default 0)

        Returns:
            Stream object containing transactions
        """
        transactions: List[AxiTransaction] = []
        current_addr = start_addr
        remaining_size = total_size

        while remaining_size > 0:
            # Apply 64B boundary chopping
            transfer_size = min(burst_size, remaining_size)
            chopped = chop_at_64b_boundary(current_addr, transfer_size)

            for (addr, size) in chopped:
                tx = AxiTransaction(
                    id=0,
                    port=port,
                    type=tx_type,
                    address=addr,
                    bytes=size,
                    burst="seq",
                    hint="LLC_ALLOC" if llc_enable else None,
                    rw="R" if "Read" in tx_type else "W",
                )
                if len(transactions) == 0 and line_delay > 0:
                    tx.req_delay = line_delay
                transactions.append(tx)

            current_addr += transfer_size
            remaining_size -= transfer_size

        return Stream(port, transactions, line_size, h_size, bpp)

    # ------------------------------------------------------------------
    #  New YAML-based interface
    # ------------------------------------------------------------------
    @staticmethod
    def generate_plane_stream(port: str,
                              tx_type: str,
                              plane: PlaneInfo,
                              start_addr: int,
                              access_pattern: AccessPattern,
                              plane_index: int = 0) -> Stream:
        """
        Generate a transaction stream for a single image plane.

        Applies 64B boundary chopping to every address chunk produced
        by the access pattern.

        Args:
            port: Port/IP name
            tx_type: "ReadNoSnoop" or "WriteNoSnoop"
            plane: PlaneInfo describing the plane geometry
            start_addr: Base address for this plane
            access_pattern: AccessPattern strategy (Raster / Z-order)
            plane_index: Plane index (0=Y, 1=UV, ...)

        Returns:
            Stream with all chopped transactions
        """
        transactions: List[AxiTransaction] = []
        rw = "R" if "Read" in tx_type else "W"

        for (raw_addr, raw_size) in access_pattern.generate_addresses(plane, start_addr):
            # Apply 64B boundary chopping
            for (addr, size) in chop_at_64b_boundary(raw_addr, raw_size):
                tx = AxiTransaction(
                    id=0,
                    port=port,
                    type=tx_type,
                    address=addr,
                    bytes=size,
                    burst="seq",
                    plane=plane_index,
                    rw=rw,
                )
                transactions.append(tx)

        return Stream(
            ip_name=port,
            transactions=transactions,
            line_size=plane.stride,
            h_size=plane.width,
            bpp=plane.bpp,
            plane_index=plane_index,
        )

    @staticmethod
    def generate_streams_for_task(port: str,
                                  tx_type: str,
                                  format_str: str,
                                  width: int,
                                  height: int,
                                  access_type: str,
                                  base_addr: int,
                                  tile_w: int = 64,
                                  tile_h: int = 32) -> List[Stream]:
        """
        Generate one Stream per plane for a task (YAML mode).

        For multi-plane formats (e.g. NV12) each plane gets its own
        stream, treating each as an independent DMA channel.

        Args:
            port: IP/port name
            tx_type: "ReadNoSnoop" or "WriteNoSnoop"
            format_str: Format string (e.g. "YUV420_8bit_2plane")
            width: Image pixel width
            height: Image pixel height
            access_type: "raster-order" or "Z-order"
            base_addr: Starting memory address
            tile_w: Tile width for Z-order (default 64)
            tile_h: Tile height for Z-order (default 32)

        Returns:
            List of Stream objects (one per plane)
        """
        planes = ImageFormatDescriptor.get_plane_info(format_str, width, height)
        pattern = create_access_pattern(access_type, tile_w, tile_h)
        streams: List[Stream] = []
        addr = base_addr

        for plane in planes:
            stream = StreamGenerator.generate_plane_stream(
                port=port,
                tx_type=tx_type,
                plane=plane,
                start_addr=addr,
                access_pattern=pattern,
                plane_index=plane.index,
            )
            streams.append(stream)
            # Advance address for next plane (aligned to 4KB)
            addr += plane.total_bytes
            remainder = addr % 4096
            if remainder != 0:
                addr += (4096 - remainder)

        return streams
