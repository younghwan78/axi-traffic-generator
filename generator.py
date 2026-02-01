"""
Stream Generator

Generates AXI transaction streams by splitting total transfer size into burst-sized chunks.
"""

from typing import List
from domain_model import AxiTransaction


class Stream:
    """
    Wrapper for a list of transactions with metadata.
    """
    
    def __init__(self, ip_name: str, transactions: List[AxiTransaction], 
                 line_size: int = 0, h_size: int = 0, bpp: float = 0):
        """
        Initialize stream.
        
        Args:
            ip_name: IP/Port name
            transactions: List of AxiTransaction objects
            line_size: Size of one line in bytes (for OTF sync)
            h_size: Horizontal pixel count
            bpp: Bytes per pixel
        """
        self.ip_name = ip_name
        self.transactions = transactions
        self.line_size = line_size
        self.h_size = h_size
        self.bpp = bpp
    
    def get_first(self) -> AxiTransaction:
        """Get first transaction in stream."""
        return self.transactions[0] if self.transactions else None
    
    def get_last(self) -> AxiTransaction:
        """Get last transaction in stream."""
        return self.transactions[-1] if self.transactions else None
    
    def get_line_transactions(self, line_idx: int) -> List[AxiTransaction]:
        """
        Get all transactions for a specific line.
        
        Args:
            line_idx: Line index (0-based)
            
        Returns:
            List of transactions for this line
        """
        if self.line_size == 0:
            return []
        
        # Calculate transaction indices for this line
        # Assume burst_size = 64 bytes
        burst_size = 64
        txs_per_line = (self.line_size + burst_size - 1) // burst_size
        
        start_idx = line_idx * txs_per_line
        end_idx = min(start_idx + txs_per_line, len(self.transactions))
        
        if start_idx >= len(self.transactions):
            return []
        
        return self.transactions[start_idx:end_idx]
    
    def get_line_count(self) -> int:
        """Get total number of lines."""
        if self.line_size == 0:
            return 0
        
        burst_size = 64
        txs_per_line = (self.line_size + burst_size - 1) // burst_size
        
        if txs_per_line == 0:
            return 0
        
        return (len(self.transactions) + txs_per_line - 1) // txs_per_line
    
    def __len__(self) -> int:
        """Get number of transactions."""
        return len(self.transactions)
    
    def __iter__(self):
        """Iterate over transactions."""
        return iter(self.transactions)


class StreamGenerator:
    """
    Generates AXI transaction streams from high-level parameters.
    """
    
    @staticmethod
    def generate_stream(port: str, tx_type: str, start_addr: int, 
                       total_size: int, burst_size: int = 64,
                       line_size: int = 0, h_size: int = 0, bpp: float = 0) -> Stream:
        """
        Generate a stream of AXI transactions.
        
        Args:
            port: Port/IP name
            tx_type: Transaction type ("ReadNoSnoop" or "WriteNoSnoop")
            start_addr: Starting memory address
            total_size: Total transfer size in bytes
            burst_size: Size of each burst in bytes (default 64)
            line_size: Size of one line in bytes (for OTF sync)
            h_size: Horizontal pixel count
            bpp: Bytes per pixel
            
        Returns:
            Stream object containing transactions
        """
        transactions = []
        current_addr = start_addr
        remaining_size = total_size
        
        while remaining_size > 0:
            # Determine transfer size for this transaction
            transfer_size = min(burst_size, remaining_size)
            
            # Create transaction (ID will be assigned later globally)
            tx = AxiTransaction(
                id=0,  # Placeholder, will be assigned later
                port=port,
                type=tx_type,
                address=current_addr,
                bytes=transfer_size,
                burst="seq"
            )
            
            transactions.append(tx)
            
            # Move to next burst
            current_addr += transfer_size
            remaining_size -= transfer_size
        
        return Stream(port, transactions, line_size, h_size, bpp)
