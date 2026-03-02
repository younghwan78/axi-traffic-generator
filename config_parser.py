"""
Configuration Parser

Loads and validates YAML-based configuration files:
  - DMA_IP_Spec.yaml: Static hardware specifications per IP
  - Scenario.yaml: Dynamic use-case scenarios (clock, resolution, dependency, behavior)
"""

import yaml
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any


# ---------------------------------------------------------------------------
#  Data classes for DMA_IP_Spec.yaml
# ---------------------------------------------------------------------------

@dataclass
class CoreSpec:
    """Core hardware properties of a DMA IP."""
    dir: str            # "R" or "W"
    bus_byte: int       # Bus width in bytes (e.g., 16, 32)
    ppc: int            # Pixels Per Clock
    bpp: int            # Bits Per Pixel (raw spec value)
    plane: int          # Number of planes supported

@dataclass
class CtrlSpec:
    """Control/QoS properties."""
    votf: bool = False
    votf_stall: bool = False
    qurgent: bool = False
    req_mo: int = 8     # Maximum Outstanding requests

@dataclass
class BufferSpec:
    """Internal buffer properties."""
    fifo: int = 1024
    cts: int = 128
    axid: int = 4
    usr_w: int = 8
    hwapg: bool = False
    fro: int = 2

@dataclass
class DmaIpSpec:
    """Complete hardware specification for a single DMA IP."""
    name: str
    core: CoreSpec
    access: List[str] = field(default_factory=lambda: ["raster-order"])
    ctrl: CtrlSpec = field(default_factory=CtrlSpec)
    buffer: BufferSpec = field(default_factory=BufferSpec)


# ---------------------------------------------------------------------------
#  Data classes for Scenario.yaml
# ---------------------------------------------------------------------------

@dataclass
class DependencyConfig:
    """Task dependency specification."""
    wait_for: str           # Producer task name
    granularity: str = "Line"   # "Line", "Tile", "Frame"
    margin: int = 0

@dataclass
class BehaviorProfile:
    """Behavior strategy profile for a task."""
    type: str = "Eager_MO_Burst"
    pipeline_group: str = ""
    backpressure_source: Optional[str] = None
    # Accumulate_and_Flush specific
    trigger_unit: Optional[str] = None
    block_size: Optional[List[int]] = None
    flush_bytes: Optional[int] = None

@dataclass
class TaskConfig:
    """Single task (DMA job) within a scenario."""
    task_name: str
    ip_name: str
    clock: str
    format: str
    resolution: List[int]       # [width, height]
    access_type: str = "raster-order"
    dependency: List[DependencyConfig] = field(default_factory=list)
    behavior: BehaviorProfile = field(default_factory=BehaviorProfile)

@dataclass
class MemoryPolicy:
    """Memory allocation policy."""
    smmu_enable: bool = False
    cma_ratio: float = 0.3
    page_size: int = 4096

@dataclass
class ScenarioConfig:
    """Complete scenario configuration."""
    name: str
    clock_domains: Dict[str, int]   # domain_name -> MHz
    memory_policy: MemoryPolicy
    tasks: List[TaskConfig]


# ---------------------------------------------------------------------------
#  Parser
# ---------------------------------------------------------------------------

class ConfigParser:
    """Loads and validates YAML configuration files."""

    @staticmethod
    def load_ip_spec(yaml_path: str) -> Dict[str, DmaIpSpec]:
        """
        Load DMA_IP_Spec.yaml.

        Args:
            yaml_path: Path to the YAML file

        Returns:
            Dictionary mapping IP name to DmaIpSpec
        """
        with open(yaml_path, 'r', encoding='utf-8') as f:
            raw = yaml.safe_load(f)

        specs: Dict[str, DmaIpSpec] = {}

        for ip_name, ip_data in raw.items():
            core_raw = ip_data.get('Core', {})
            core = CoreSpec(
                dir=core_raw.get('Dir', 'W'),
                bus_byte=core_raw.get('BusByte', 16),
                ppc=core_raw.get('PPC', 1),
                bpp=core_raw.get('BPP', 8),
                plane=core_raw.get('Plane', 1),
            )

            access = ip_data.get('Access', ['raster-order'])

            ctrl_raw = ip_data.get('Ctrl', {})
            ctrl = CtrlSpec(
                votf=ctrl_raw.get('VOTF', False),
                votf_stall=ctrl_raw.get('VOTF_stall', False),
                qurgent=ctrl_raw.get('Qurgent', False),
                req_mo=ctrl_raw.get('req_MO', 8),
            )

            buf_raw = ip_data.get('Buffer', {})
            buffer = BufferSpec(
                fifo=buf_raw.get('Fifo', 1024),
                cts=buf_raw.get('CTS', 128),
                axid=buf_raw.get('AXID', 4),
                usr_w=buf_raw.get('usr_w', 8),
                hwapg=buf_raw.get('HWAPG', False),
                fro=buf_raw.get('FRO', 2),
            )

            specs[ip_name] = DmaIpSpec(
                name=ip_name,
                core=core,
                access=access,
                ctrl=ctrl,
                buffer=buffer,
            )

        return specs

    @staticmethod
    def load_scenario(yaml_path: str) -> ScenarioConfig:
        """
        Load Scenario.yaml.

        Args:
            yaml_path: Path to the YAML file

        Returns:
            ScenarioConfig object
        """
        with open(yaml_path, 'r', encoding='utf-8') as f:
            raw = yaml.safe_load(f)

        # Scenario info
        info = raw.get('Scenario_Info', {})
        name = info.get('Name', 'Unnamed')

        # Clock domains
        clock_domains = raw.get('Clock_Domains', {})

        # Memory policy
        mem_raw = raw.get('Memory_Policy', {})
        memory_policy = MemoryPolicy(
            smmu_enable=mem_raw.get('SMMU_Enable', False),
            cma_ratio=mem_raw.get('CMA_Ratio', 0.3),
            page_size=mem_raw.get('Page_Size', 4096),
        )

        # Tasks
        tasks: List[TaskConfig] = []
        for task_raw in raw.get('Tasks', []):
            # Parse dependencies
            deps: List[DependencyConfig] = []
            for dep_raw in task_raw.get('Dependency', []) or []:
                if dep_raw is None:
                    continue
                deps.append(DependencyConfig(
                    wait_for=dep_raw.get('Wait_For', ''),
                    granularity=dep_raw.get('Granularity', 'Line'),
                    margin=dep_raw.get('Margin', 0),
                ))

            # Parse behavior profile
            bp_raw = task_raw.get('Behavior_Profile', {}) or {}
            behavior = BehaviorProfile(
                type=bp_raw.get('Type', 'Eager_MO_Burst'),
                pipeline_group=bp_raw.get('Pipeline_Group', ''),
                backpressure_source=bp_raw.get('Backpressure_Source'),
                trigger_unit=bp_raw.get('Trigger_Unit'),
                block_size=bp_raw.get('Block_Size'),
                flush_bytes=bp_raw.get('Flush_Bytes'),
            )

            tasks.append(TaskConfig(
                task_name=task_raw['TaskName'],
                ip_name=task_raw['IP_Name'],
                clock=task_raw.get('Clock', ''),
                format=task_raw.get('Format', ''),
                resolution=task_raw.get('Resolution', [0, 0]),
                access_type=task_raw.get('AccessType', 'raster-order'),
                dependency=deps,
                behavior=behavior,
            ))

        return ScenarioConfig(
            name=name,
            clock_domains=clock_domains,
            memory_policy=memory_policy,
            tasks=tasks,
        )

    @staticmethod
    def sanity_check(specs: Dict[str, DmaIpSpec], scenario: ScenarioConfig) -> List[str]:
        """
        Validate scenario against IP specifications.

        Checks:
          1. Referenced IP exists in spec
          2. AccessType is supported by the IP
          3. Clock domain exists
          4. Dependency targets exist as task names

        Args:
            specs: IP spec dictionary
            scenario: Scenario configuration

        Returns:
            List of error messages (empty if valid)
        """
        errors: List[str] = []
        task_names = {t.task_name for t in scenario.tasks}

        for task in scenario.tasks:
            # 1. IP exists
            if task.ip_name not in specs:
                errors.append(
                    f"Task '{task.task_name}': IP '{task.ip_name}' not found in DMA_IP_Spec"
                )
                continue  # Skip further checks for this task

            ip_spec = specs[task.ip_name]

            # 2. AccessType supported
            if task.access_type not in ip_spec.access:
                errors.append(
                    f"Task '{task.task_name}': AccessType '{task.access_type}' "
                    f"not supported by IP '{task.ip_name}' "
                    f"(supported: {ip_spec.access})"
                )

            # 3. Clock domain exists
            if task.clock and task.clock not in scenario.clock_domains:
                errors.append(
                    f"Task '{task.task_name}': Clock domain '{task.clock}' "
                    f"not defined in Clock_Domains"
                )

            # 4. Dependency targets exist
            for dep in task.dependency:
                if dep.wait_for and dep.wait_for not in task_names:
                    errors.append(
                        f"Task '{task.task_name}': Dependency Wait_For "
                        f"'{dep.wait_for}' is not a defined task"
                    )

            # 5. Backpressure source exists
            if task.behavior.backpressure_source:
                if task.behavior.backpressure_source not in task_names:
                    errors.append(
                        f"Task '{task.task_name}': Backpressure_Source "
                        f"'{task.behavior.backpressure_source}' is not a defined task"
                    )

        return errors
