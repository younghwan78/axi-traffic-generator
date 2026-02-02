"""
Utility classes for multimedia calculations and memory address allocation.
"""

from typing import Dict


class MultimediaUtils:
    """
    Utilities for multimedia format calculations.
    """
    
    @staticmethod
    def calculate_bpp(color_format: str, bit_width: int) -> float:
        """
        Calculate bytes per pixel based on color format and bit width.
        
        Args:
            color_format: Color format type ("Bayer", "YUV", "RGB")
            bit_width: Bit depth per component
            
        Returns:
            Bytes per pixel
        """
        format_upper = color_format.upper()
        
        if "BAYER" in format_upper:
            # Bayer: 1 component per pixel
            return bit_width / 8.0
        elif "YUV" in format_upper:
            # YUV 4:2:0: 1.5 components per pixel on average
            return 1.5 * bit_width / 8.0
        elif "RGB" in format_upper:
            # RGB: 3 components per pixel
            return 3.0 * bit_width / 8.0
        else:
            # Default to single component
            return bit_width / 8.0
    
    @staticmethod
    def calculate_total_size(h_size: int, v_size: int, color_format: str, bit_width: int) -> int:
        """
        Calculate total buffer size in bytes.
        
        Args:
            h_size: Horizontal pixel count
            v_size: Vertical pixel count
            color_format: Color format type
            bit_width: Bit depth per component
            
        Returns:
            Total size in bytes
        """
        bpp = MultimediaUtils.calculate_bpp(color_format, bit_width)
        total_size = int(h_size * v_size * bpp)
        return total_size
    
    @staticmethod
    def calculate_line_size(h_size: int, color_format: str, bit_width: int) -> int:
        """
        Calculate size of one line in bytes (for OTF sync).
        
        Args:
            h_size: Horizontal pixel count
            color_format: Color format type
            bit_width: Bit depth per component
            
        Returns:
            Line size in bytes
        """
        bpp = MultimediaUtils.calculate_bpp(color_format, bit_width)
        line_size = int(h_size * bpp)
        return line_size
    
    @staticmethod
    def apply_compression(total_size: int, comp_ratio: float) -> int:
        """
        Apply compression ratio to total size.
        
        Args:
            total_size: Original size in bytes
            comp_ratio: Compression ratio (e.g., 0.5 for 50% compression)
            
        Returns:
            Compressed size in bytes
        """
        return int(total_size * comp_ratio)
    
    @staticmethod
    def align_width_for_compression(h_size: int, color_format: str) -> int:
        """
        Align width to compression requirements based on format.
        
        Args:
            h_size: Horizontal pixel count
            color_format: Color format type
            
        Returns:
            Aligned horizontal size
        """
        format_upper = color_format.upper()
        if "BAYER" in format_upper:
            # Bayer: Align to 256 bytes
            return ((h_size + 255) // 256) * 256
        elif "YUV" in format_upper:
            # YUV: Align to 32 bytes
            return ((h_size + 31) // 32) * 32
        return h_size
    
    @staticmethod
    def align_height_for_compression(v_size: int, color_format: str) -> int:
        """
        Align height to compression requirements based on format.
        
        Args:
            v_size: Vertical line count
            color_format: Color format type
            
        Returns:
            Aligned vertical size
        """
        format_upper = color_format.upper()
        if "YUV" in format_upper:
            # YUV: Align to 4 lines
            return ((v_size + 3) // 4) * 4
        return v_size


class AddressAllocator:
    """
    Manages memory address allocation with 4KB alignment.
    """
    
    def __init__(self, base_address: int = 0x80000000, alignment: int = 4096):
        """
        Initialize address allocator.
        
        Args:
            base_address: Starting base address (default 0x80000000)
            alignment: Address alignment in bytes (default 4KB = 4096)
        """
        self.alignment = alignment
        # Ensure base_address is aligned (important for ion allocation in Android-based Linux OS)
        remainder = base_address % alignment
        if remainder != 0:
            self.base_address = base_address + (alignment - remainder)
        else:
            self.base_address = base_address
        self.current_address = self.base_address
        self.allocations: Dict[str, tuple] = {}  # ip_name -> (start, size)
    
    def allocate(self, size: int, ip_name: str = "") -> int:
        """
        Allocate a memory region and return aligned start address.
        
        Args:
            size: Size to allocate in bytes
            ip_name: Optional IP name for tracking
            
        Returns:
            Aligned start address
        """
        # Return current address (already aligned)
        start_address = self.current_address
        
        # Track allocation
        if ip_name:
            self.allocations[ip_name] = (start_address, size)
        
        # Move to next aligned address
        self.current_address += size
        # Align to next boundary
        remainder = self.current_address % self.alignment
        if remainder != 0:
            self.current_address += (self.alignment - remainder)
        
        return start_address
    
    def get_allocation(self, ip_name: str) -> tuple:
        """
        Get allocation info for an IP.
        
        Args:
            ip_name: IP name to query
            
        Returns:
            Tuple of (start_address, size) or None if not found
        """
        return self.allocations.get(ip_name)
    
    def reset(self, base_address: int = None) -> None:
        """
        Reset allocator to initial state.
        
        Args:
            base_address: Optional new base address
        """
        if base_address is not None:
            self.base_address = base_address
        self.current_address = self.base_address
        self.allocations.clear()
