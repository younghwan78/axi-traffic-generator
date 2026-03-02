"""
Mock SMMU & PA Translator

Models I/O virtual address (IOVA) to physical address (PA) translation
with configurable fragmentation (CMA vs Scatter-Gather) and
PTW (Page Table Walk) traffic injection.

Default: disabled (bypass mode, IOVA == PA).
"""

import random
from dataclasses import dataclass
from typing import Dict, List, Set, Optional

from domain_model import AxiTransaction


@dataclass
class TranslationResult:
    """Result of translating one segment of an IOVA range."""
    pa: int             # Physical address
    size: int           # Segment size in bytes
    is_new_page: bool   # True if this is a TLB-miss page (needs PTW)


class PhysicalAddressPool:
    """
    Manages physical page allocation.

    CMA pages are allocated contiguously from a reserved region.
    SG (Scatter-Gather) pages are allocated from random positions.
    """

    def __init__(self, cma_base: int = 0x4000_0000,
                 sg_base: int = 0x8000_0000,
                 page_size: int = 4096):
        self.page_size = page_size
        # CMA: contiguous region
        self.cma_next = cma_base
        # SG: random pages (simulate fragmentation)
        self.sg_base = sg_base
        self.sg_allocated: Set[int] = set()
        self._rng = random.Random(42)   # deterministic for reproducibility

    def allocate_cma_page(self) -> int:
        """Allocate next contiguous physical page (CMA)."""
        pa = self.cma_next
        self.cma_next += self.page_size
        return pa

    def allocate_sg_page(self) -> int:
        """Allocate a random physical page (Scatter-Gather)."""
        # Generate random page-aligned address
        while True:
            page_num = self._rng.randint(0, 0xFFFFF)  # 4GB / 4KB = 1M pages
            pa = self.sg_base + page_num * self.page_size
            if pa not in self.sg_allocated:
                self.sg_allocated.add(pa)
                return pa


class MockSMMU:
    """
    Simulates IOVA → PA translation with fragmentation.

    When enabled:
      - Maintains a page table (IOVA page → PA page)
      - Allocates physical pages using CMA or SG based on cma_ratio
      - Tracks TLB; first access to a page triggers PTW traffic

    When disabled (default):
      - Pass-through (PA == IOVA), no PTW
    """

    def __init__(self, cma_ratio: float = 0.3,
                 page_size: int = 4096,
                 enabled: bool = False):
        self.enabled = enabled
        self.cma_ratio = cma_ratio
        self.page_size = page_size
        self.page_table: Dict[int, int] = {}   # IOVA_page_base → PA_page_base
        self.tlb_cache: Set[int] = set()        # Pages already in TLB
        self.pa_pool = PhysicalAddressPool(page_size=page_size)
        self._rng = random.Random(42)
        # PTW reads go to a fixed PT base
        self._pt_base = 0x3FF0_0000

    def _page_base(self, addr: int) -> int:
        """Get page-aligned base address."""
        return (addr // self.page_size) * self.page_size

    def _page_offset(self, addr: int) -> int:
        """Get offset within a page."""
        return addr % self.page_size

    def _ensure_page_mapped(self, iova_page: int) -> int:
        """
        Ensure IOVA page is mapped; allocate PA page if not.

        Returns:
            Physical page base address
        """
        if iova_page in self.page_table:
            return self.page_table[iova_page]

        # Decide CMA vs SG
        if self._rng.random() < self.cma_ratio:
            pa_page = self.pa_pool.allocate_cma_page()
        else:
            pa_page = self.pa_pool.allocate_sg_page()

        self.page_table[iova_page] = pa_page
        return pa_page

    def translate(self, iova: int, size: int) -> List[TranslationResult]:
        """
        Translate an IOVA range to PA segments.

        The range may span multiple 4KB pages, producing multiple
        segments with potentially different PA bases.

        Args:
            iova: Starting I/O virtual address
            size: Transfer size in bytes

        Returns:
            List of TranslationResult segments
        """
        if not self.enabled:
            # Bypass mode
            return [TranslationResult(pa=iova, size=size, is_new_page=False)]

        results: List[TranslationResult] = []
        remaining = size
        current_iova = iova

        while remaining > 0:
            iova_page = self._page_base(current_iova)
            offset_in_page = self._page_offset(current_iova)
            bytes_in_page = min(remaining, self.page_size - offset_in_page)

            # Ensure page is mapped
            pa_page = self._ensure_page_mapped(iova_page)
            pa = pa_page + offset_in_page

            # Check TLB
            is_new = iova_page not in self.tlb_cache
            if is_new:
                self.tlb_cache.add(iova_page)

            results.append(TranslationResult(
                pa=pa,
                size=bytes_in_page,
                is_new_page=is_new,
            ))

            current_iova += bytes_in_page
            remaining -= bytes_in_page

        return results

    def generate_ptw_transaction(self, port: str,
                                 iova_page: int) -> AxiTransaction:
        """
        Create a 64-byte Read transaction representing a Page Table Walk.

        Args:
            port: DMA port name (PTW reads appear on the same port)
            iova_page: The IOVA page that triggered the PTW

        Returns:
            A ReadNoSnoop transaction (64 bytes) from the PT region
        """
        # PT address is deterministic from IOVA page number
        page_num = iova_page // self.page_size
        pt_addr = self._pt_base + (page_num % 0x10000) * 64

        return AxiTransaction(
            id=0,
            port=port,
            type="ReadNoSnoop",
            address=pt_addr,
            bytes=64,
            burst="seq",
            rw="R",
        )
