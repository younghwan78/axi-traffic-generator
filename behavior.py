"""
Behavior Strategies

Implements the Strategy Pattern for DMA traffic generation behaviors:
  - EagerMOStrategy: Immediate burst up to MO limit (Image RDMA/WDMA)
  - AccumulateAndFlushStrategy: Silent until trigger, then burst flush (Stat Data)
"""

from abc import ABC, abstractmethod
from typing import List, Optional, TYPE_CHECKING

from domain_model import AxiTransaction

if TYPE_CHECKING:
    from scheduler import DmaAgent, Scoreboard


class BehaviorStrategy(ABC):
    """
    Base interface for DMA behavioral profiles.

    Each strategy decides how many transactions (if any) to emit on a
    given tick, based on internal buffer state, scoreboard, and
    backpressure.
    """

    @abstractmethod
    def step(self, agent: 'DmaAgent', tick: int,
             scoreboard: 'Scoreboard') -> List[AxiTransaction]:
        """
        Advance one tick and return transactions to emit.

        Args:
            agent: The DmaAgent this strategy is attached to
            tick: Current virtual tick
            scoreboard: Global scoreboard for dependency tracking

        Returns:
            List of transactions to emit (may be empty)
        """


class EagerMOStrategy(BehaviorStrategy):
    """
    Image RDMA/WDMA behaviour: burst up to MO limit eagerly.

    Each tick:
      1. Accumulate PPC * BPP bytes into the virtual internal buffer
      2. While buffer >= BusByte and MO budget remains, emit transactions
      3. If Backpressure_Source is stalled, emit nothing
    """

    def step(self, agent: 'DmaAgent', tick: int,
             scoreboard: 'Scoreboard') -> List[AxiTransaction]:
        # --- Backpressure check ---
        if agent.backpressure_source is not None:
            if agent.backpressure_source.stalled:
                agent.stalled = True
                return []

        # --- Accumulate data ---
        agent.internal_buffer += agent.bytes_per_tick

        # --- Emit transactions ---
        transactions: List[AxiTransaction] = []
        mo_remaining = agent.req_mo

        while agent.internal_buffer >= agent.bus_byte and mo_remaining > 0:
            tx = agent.next_transaction()
            if tx is None:
                agent.tx_finished = True
                break
            tx.tick = tick
            transactions.append(tx)
            agent.internal_buffer -= tx.bytes
            mo_remaining -= 1

        if transactions:
            agent.stalled = False
        else:
            # No transactions emitted despite having data → bus bottleneck
            if agent.internal_buffer >= agent.bus_byte:
                agent.stalled = True

        return transactions


class AccumulateAndFlushStrategy(BehaviorStrategy):
    """
    Stat/metadata DMA behaviour: silent until block processed, then flush.

    Monitors the pipeline progress via Scoreboard.  When enough pixels
    have been processed (Block_Size), emits Flush_Bytes worth of
    transactions in a single burst.

    If progress_source is set, tracks only that specific task's pixel
    progress instead of the entire pipeline group's aggregate.
    """

    def __init__(self, trigger_unit: str = "Block",
                 block_size: Optional[List[int]] = None,
                 flush_bytes: int = 256,
                 pipeline_group: str = "",
                 progress_source: Optional[str] = None):
        self.trigger_unit = trigger_unit
        self.block_pixels = (block_size[0] * block_size[1]) if block_size else 4096
        self.flush_bytes = flush_bytes
        self.pipeline_group = pipeline_group
        self.progress_source = progress_source
        self.last_triggered_at = 0

    def step(self, agent: 'DmaAgent', tick: int,
             scoreboard: 'Scoreboard') -> List[AxiTransaction]:
        # Monitor progress: specific producer task or entire pipeline group
        if self.progress_source:
            progress = scoreboard.get_task_progress(self.progress_source)
        else:
            progress = scoreboard.get_progress(self.pipeline_group)

        if progress - self.last_triggered_at < self.block_pixels:
            return []   # Silent — not enough pixels processed yet

        # Trigger!
        self.last_triggered_at = progress

        # Flush burst
        transactions: List[AxiTransaction] = []
        remaining = self.flush_bytes
        while remaining > 0:
            tx = agent.next_transaction()
            if tx is None:
                agent.tx_finished = True
                break
            tx.tick = tick
            chunk = min(remaining, tx.bytes)
            tx.bytes = chunk
            transactions.append(tx)
            remaining -= chunk

        return transactions
