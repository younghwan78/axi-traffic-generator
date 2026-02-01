"""
AXI Transaction Domain Model

Defines the core AxiTransaction dataclass representing individual AXI transactions
with proper formatting for trace file output.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class AxiTransaction:
    """
    Represents a single AXI transaction.
    
    Attributes:
        id: Unique transaction identifier
        port: DMA port name (e.g., "DMA1", "CAM_FE")
        type: Transaction type ("ReadNoSnoop" or "WriteNoSnoop")
        address: Memory address in hexadecimal
        bytes: Number of bytes to transfer
        burst: Burst type (typically "seq" for sequential)
        hint: Optional hint field
        dep: List of dependencies in format "target_id,event+offset" (e.g., "10,req+100")
        deadline: Optional deadline field
    """
    id: int
    port: str
    type: str  # "ReadNoSnoop" or "WriteNoSnoop"
    address: int  # Will be formatted as hex
    bytes: int
    burst: str = "seq"
    hint: Optional[str] = None
    dep: List[str] = field(default_factory=list)
    deadline: Optional[int] = None
    
    def __str__(self) -> str:
        """
        Format transaction as a trace file line.
        
        Format: id=1 port=DMA1 type=ReadNoSnoop address=0x80001000 bytes=64 burst=seq dep=1,req+10
        
        Returns:
            Formatted transaction string
        """
        parts = [
            f"id={self.id}",
            f"port={self.port}",
            f"type={self.type}",
            f"address={self.address:#x}",  # Format as 0x...
            f"bytes={self.bytes}",
            f"burst={self.burst}"
        ]
        
        # Add optional hint
        if self.hint:
            parts.append(f"hint={self.hint}")
        
        # Add dependencies if present (separated by |)
        if self.dep:
            dep_parts = [f"dep={dep}" for dep in self.dep]
            parts.append("|".join(dep_parts))
        
        # Add optional deadline
        if self.deadline:
            parts.append(f"deadline={self.deadline}")
        
        return " ".join(parts)
    
    def add_dependency(self, target_id: int, event: str, offset: int = 0) -> None:
        """
        Add a dependency to this transaction.
        
        Args:
            target_id: ID of the transaction this depends on
            event: Event type ("req" or "resp")
            offset: Cycle offset from the event (default 0)
        """
        dep_str = f"{target_id},{event}+{offset}"
        self.dep.append(dep_str)
