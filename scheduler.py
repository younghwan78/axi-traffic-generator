"""
Virtual Tick Scheduler & Scoreboard

Provides tick-based simulation of multiple DMA agents running concurrently:
  - Scoreboard: tracks producer progress for dependency resolution
  - DmaAgent: wraps one DMA task with its behavior strategy and state
  - VirtualTickScheduler: advances ticks, activates agents via weighted
    round-robin, collects transactions
"""

from typing import Dict, List, Optional, Set

from domain_model import AxiTransaction
from config_parser import DmaIpSpec, TaskConfig, ScenarioConfig
from format_descriptor import ImageFormatDescriptor
from generator import (
    StreamGenerator, Stream, chop_at_64b_boundary,
    create_access_pattern,
)
from smmu_model import MockSMMU
from behavior import BehaviorStrategy, EagerMOStrategy, AccumulateAndFlushStrategy


# ============================================================================
#  Scoreboard
# ============================================================================

class Scoreboard:
    """
    Tracks producer progress so consumers can check dependencies.

    Records:
      - completed lines/tiles per task
      - total processed pixels per pipeline group
    """

    def __init__(self):
        self.completed_units: Dict[str, int] = {}   # task_name → line/tile #
        self.pixel_progress: Dict[str, int] = {}    # pipeline_group → pixels
        self.task_pixels: Dict[str, int] = {}        # task_name → pixels

    def update(self, task_name: str, completed_unit: int) -> None:
        """Record that *task_name* has completed up to *completed_unit*."""
        self.completed_units[task_name] = max(
            self.completed_units.get(task_name, 0), completed_unit
        )

    def update_pixels(self, pipeline_group: str, pixels: int,
                      task_name: str = "") -> None:
        """Accumulate processed pixels for a pipeline group and task."""
        self.pixel_progress[pipeline_group] = (
            self.pixel_progress.get(pipeline_group, 0) + pixels
        )
        if task_name:
            self.task_pixels[task_name] = (
                self.task_pixels.get(task_name, 0) + pixels
            )

    def can_proceed(self, wait_for: str, required_unit: int,
                    margin: int = 0) -> bool:
        """
        Check if a consumer can proceed.

        Returns True when the producer (*wait_for*) has completed
        at least (*required_unit* - *margin*).
        """
        done = self.completed_units.get(wait_for, -1)
        return done >= (required_unit - margin)

    def get_progress(self, pipeline_group: str) -> int:
        """Return total processed pixels for a pipeline group."""
        return self.pixel_progress.get(pipeline_group, 0)

    def get_task_progress(self, task_name: str) -> int:
        """Return total processed pixels for a specific task."""
        return self.task_pixels.get(task_name, 0)


# ============================================================================
#  DMA Agent
# ============================================================================

class DmaAgent:
    """
    Represents a single DMA task during simulation.

    Wraps the pre-generated address iterator with behavioral state
    (internal buffer, stall, backpressure).
    """

    def __init__(self, task: TaskConfig, ip_spec: DmaIpSpec,
                 strategy: BehaviorStrategy,
                 transactions: List[AxiTransaction],
                 clock_mhz: int):
        self.task = task
        self.ip_spec = ip_spec
        self.strategy = strategy
        self.clock_mhz = clock_mhz

        # Pre-generated transaction pool
        self._tx_pool = transactions
        self._tx_idx = 0

        # Internal state
        self.bus_byte: int = ip_spec.core.bus_byte
        self.ppc: int = ip_spec.core.ppc
        self.bpp_bytes: float = ip_spec.core.bpp / 8.0
        self.req_mo: int = ip_spec.ctrl.req_mo
        self.bytes_per_tick: float = self.ppc * self.bpp_bytes
        self.internal_buffer: float = 0.0
        self.stalled: bool = False
        self.finished: bool = False
        self.tx_finished: bool = False  # Transaction pool exhausted

        # Total frame pixels for pixel progress tracking
        self.total_frame_pixels: int = task.resolution[0] * task.resolution[1]

        # Line progress tracking for dependency gating
        from format_descriptor import ImageFormatDescriptor
        planes = ImageFormatDescriptor.get_plane_info(
            task.format, task.resolution[0], task.resolution[1])
        self.stride: int = planes[0].stride if planes else (task.resolution[0])
        self._bytes_emitted: int = 0
        self._line_progress: int = 0  # Current image line (bytes_emitted / stride)

        # Bandwidth weight for scheduler (bytes/tick proportional)
        self.bandwidth = clock_mhz * self.ppc * self.bpp_bytes

        # Backpressure (linked at registration time)
        self.backpressure_source: Optional['DmaAgent'] = None

        # Dependency tracking
        self._lines_emitted: int = 0
        self._pixels_processed: int = 0

    @property
    def name(self) -> str:
        return self.task.task_name

    def next_transaction(self) -> Optional[AxiTransaction]:
        """Pop the next pre-generated transaction, or None if done."""
        if self._tx_idx >= len(self._tx_pool):
            return None
        tx = self._tx_pool[self._tx_idx]
        self._tx_idx += 1
        return tx

    def flush(self, nbytes: int, tick: int) -> List[AxiTransaction]:
        """Emit up to *nbytes* worth of transactions (for flush strategy)."""
        txs: List[AxiTransaction] = []
        remaining = nbytes
        while remaining > 0:
            tx = self.next_transaction()
            if tx is None:
                self.tx_finished = True
                break
            tx.tick = tick
            chunk = min(remaining, tx.bytes)
            tx.bytes = chunk
            txs.append(tx)
            remaining -= chunk
        return txs

    def step(self, tick: int, scoreboard: Scoreboard) -> List[AxiTransaction]:
        """Delegate one tick to the behavior strategy."""
        if self.tx_finished:
            return []
        return self.strategy.step(self, tick, scoreboard)


# ============================================================================
#  Virtual Tick Scheduler
# ============================================================================

class VirtualTickScheduler:
    """
    Clock-domain based virtual time scheduler.

    Activates agents in weighted round-robin order based on their
    bandwidth (Clock * PPC * BPP).  Runs until all agents finish.
    """

    def __init__(self, scoreboard: Scoreboard, smmu: Optional[MockSMMU] = None):
        self.tick: int = 0
        self.scoreboard = scoreboard
        self.smmu = smmu
        self.agents: Dict[str, DmaAgent] = {}
        self._agent_list: List[DmaAgent] = []

    def register_agent(self, agent: DmaAgent) -> None:
        """Register a DmaAgent for scheduling."""
        self.agents[agent.name] = agent
        self._agent_list.append(agent)

    def link_backpressure(self) -> None:
        """
        Resolve Backpressure_Source references between agents.
        Must be called after all agents are registered.
        """
        for agent in self._agent_list:
            bp_src = agent.task.behavior.backpressure_source
            if bp_src and bp_src in self.agents:
                agent.backpressure_source = self.agents[bp_src]

    def _check_dependency(self, agent: DmaAgent) -> bool:
        """
        Check whether *agent* can proceed given its dependencies.

        - Frame granularity: consumer waits until producer finishes
          the entire frame (tx_finished).
        - Line granularity: consumer can process line N only if
          producer has completed at least line (N + margin).

        Returns True if all dependencies are satisfied.
        """
        for dep in agent.task.dependency:
            if not dep.wait_for:
                continue
            producer = self.agents.get(dep.wait_for)
            if producer is None:
                continue

            if dep.granularity == 'Frame':
                # Frame sync: producer must finish entirely
                if not producer.tx_finished:
                    return False
            else:
                # Line sync: producer must be 'margin' lines ahead
                consumer_line = agent._line_progress
                required = consumer_line + dep.margin
                if producer._line_progress < required:
                    return False
        return True

    def run(self, max_ticks: int = 10_000_000) -> List[AxiTransaction]:
        """
        Run simulation until all agents finish or *max_ticks* reached.

        Uses clock-proportional scheduling: agents with higher clock
        frequencies step more often.  Each tick, every agent accumulates
        its clock_mhz; when the accumulator reaches max_clock the agent
        is allowed to execute one step.

        Returns:
            All emitted transactions in tick order
        """
        self.link_backpressure()
        all_transactions: List[AxiTransaction] = []

        # Determine max clock for accumulator threshold
        max_clock = max(a.clock_mhz for a in self._agent_list)

        # Initialise per-agent clock accumulators
        for agent in self._agent_list:
            agent._clock_accum = 0

        for tick in range(max_ticks):
            self.tick = tick
            any_active = False

            for agent in self._agent_list:
                if agent.finished:
                    continue
                any_active = True

                # Clock-proportional gating
                agent._clock_accum += agent.clock_mhz
                if agent._clock_accum < max_clock:
                    continue
                agent._clock_accum -= max_clock

                # Dependency gate
                if not self._check_dependency(agent):
                    continue

                # Execute one tick
                txs = agent.step(tick, self.scoreboard)

                # SMMU translation (if enabled)
                if self.smmu and self.smmu.enabled:
                    txs = self._apply_smmu(txs, agent)

                all_transactions.extend(txs)

                # Update scoreboard: pixel progress every scheduled tick
                # (ISP processes PPC pixels per clock regardless of emission)
                if agent._pixels_processed < agent.total_frame_pixels:
                    pixels = min(agent.ppc,
                                 agent.total_frame_pixels - agent._pixels_processed)
                    agent._pixels_processed += pixels
                    pg = agent.task.behavior.pipeline_group
                    if pg:
                        self.scoreboard.update_pixels(
                            pg, pixels, task_name=agent.name)

                # Update line/unit tracking only on actual emissions
                if txs:
                    agent._lines_emitted += len(txs)
                    tx_bytes = sum(t.bytes for t in txs)
                    agent._bytes_emitted += tx_bytes
                    agent._line_progress = agent._bytes_emitted // agent.stride
                    self.scoreboard.update(agent.name, agent._line_progress)

                # Mark finished when both: tx pool exhausted AND all pixels counted
                if agent.tx_finished and agent._pixels_processed >= agent.total_frame_pixels:
                    agent.finished = True

            if not any_active:
                break

        return all_transactions

    def _apply_smmu(self, transactions: List[AxiTransaction],
                    agent: DmaAgent) -> List[AxiTransaction]:
        """
        Apply SMMU translation to a list of transactions.
        Injects PTW reads before TLB-miss accesses.
        """
        result: List[AxiTransaction] = []
        for tx in transactions:
            segments = self.smmu.translate(tx.address, tx.bytes)
            for seg in segments:
                if seg.is_new_page:
                    # Inject PTW read before the actual access
                    ptw = self.smmu.generate_ptw_transaction(
                        tx.port,
                        self.smmu._page_base(tx.address),
                    )
                    ptw.tick = tx.tick
                    result.append(ptw)

                # Create translated transaction
                new_tx = AxiTransaction(
                    id=0,
                    port=tx.port,
                    type=tx.type,
                    address=seg.pa,
                    bytes=seg.size,
                    burst=tx.burst,
                    tick=tx.tick,
                    plane=tx.plane,
                    rw=tx.rw,
                    iova=tx.address,
                )
                result.append(new_tx)

        return result


# ============================================================================
#  Factory: build scheduler from config
# ============================================================================

def build_scheduler(specs: Dict[str, DmaIpSpec],
                    scenario: ScenarioConfig,
                    smmu: Optional[MockSMMU] = None) -> VirtualTickScheduler:
    """
    Construct a ready-to-run VirtualTickScheduler from parsed configs.

    Steps:
      1. For each task, generate per-plane transaction pools
      2. Create a DmaAgent with the appropriate BehaviorStrategy
      3. Register agents in the scheduler

    Args:
        specs: IP specification dictionary
        scenario: Scenario configuration
        smmu: Optional MockSMMU (pass None to skip SMMU)

    Returns:
        Configured VirtualTickScheduler
    """
    from utils import AddressAllocator

    scoreboard = Scoreboard()
    scheduler = VirtualTickScheduler(scoreboard, smmu)
    allocator = AddressAllocator()

    for task in scenario.tasks:
        ip_spec = specs[task.ip_name]
        clock_mhz = task.clock
        tx_type = "ReadNoSnoop" if ip_spec.core.dir == "R" else "WriteNoSnoop"

        # --- Generate transaction pool ---
        all_txs: List[AxiTransaction] = []

        # Allocate memory (SBWC needs extra for header region)
        alloc_size = ImageFormatDescriptor.get_total_size(
            task.format, task.resolution[0], task.resolution[1])
        if task.sbwc_ratio > 0:
            # Add ~20% overhead for header regions
            alloc_size = int(alloc_size * (task.sbwc_ratio + 0.2))

        streams = StreamGenerator.generate_streams_for_task(
            port=task.ip_name,
            tx_type=tx_type,
            format_str=task.format,
            width=task.resolution[0],
            height=task.resolution[1],
            access_type=task.access_type,
            base_addr=allocator.allocate(alloc_size, task.ip_name),
            sbwc_ratio=task.sbwc_ratio,
        )
        for s in streams:
            all_txs.extend(s.transactions)

        # --- Choose behavior strategy ---
        bp = task.behavior
        if bp.type == "Accumulate_and_Flush":
            strategy = AccumulateAndFlushStrategy(
                trigger_unit=bp.trigger_unit or "Block",
                block_size=bp.block_size,
                flush_bytes=bp.flush_bytes or 256,
                pipeline_group=bp.pipeline_group,
                progress_source=bp.progress_source,
            )
            # Limit transaction pool to actual needed size
            block_w = bp.block_size[0] if bp.block_size else 64
            block_h = bp.block_size[1] if bp.block_size else 64
            num_blocks = ((task.resolution[0] + block_w - 1) // block_w) * \
                         ((task.resolution[1] + block_h - 1) // block_h)
            max_bytes = num_blocks * (bp.flush_bytes or 256)
            # Trim pool to needed size
            trimmed: List[AxiTransaction] = []
            acc = 0
            for tx in all_txs:
                if acc >= max_bytes:
                    break
                trimmed.append(tx)
                acc += tx.bytes
            all_txs = trimmed
        else:
            # Default: Eager_MO_Burst
            strategy = EagerMOStrategy()

        agent = DmaAgent(
            task=task,
            ip_spec=ip_spec,
            strategy=strategy,
            transactions=all_txs,
            clock_mhz=clock_mhz,
        )
        scheduler.register_agent(agent)

    return scheduler
