# AXI Traffic Generator — Design Documentation

## Table of Contents
- [Architecture Overview](#architecture-overview)
- [Module Details](#module-details)
- [Data Flow](#data-flow)
- [Key Algorithms](#key-algorithms)
- [Dependency System](#dependency-system)
- [Appendix: Legacy CSV Mode](#appendix-legacy-csv-mode)

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                     Configuration Layer                       │
│  ┌──────────────────┐    ┌──────────────────────┐            │
│  │ DMA_IP_Spec.yaml │    │    Scenario.yaml      │            │
│  │ (IP group,       │    │ (Clock MHz, SBWC,     │            │
│  │  Instances)      │    │  Dependencies)        │            │
│  └────────┬─────────┘    └──────────┬───────────┘            │
│           └────────────┬────────────┘                        │
│                ┌───────┴───────┐                             │
│                │ ConfigParser  │  ← Sanity Check             │
│                │ (Instances    │    + Instance Expansion      │
│                │  expansion)   │                             │
│                └───────┬───────┘                             │
├──────────────────────────────────────────────────────────────┤
│                     Model Layer                               │
│  ┌──────────────────┐  ┌──────────────────┐                  │
│  │ FormatDescriptor │  │  AxiTransaction   │                  │
│  │ (PlaneInfo, BPP) │  │ (tick,plane,rw,   │                  │
│  │ + SbwcDescriptor │  │  cache)           │                  │
│  └────────┬─────────┘  └──────────────────┘                  │
├──────────────────────────────────────────────────────────────┤
│                     Generation Layer                          │
│  ┌────────────────────────────────────────────────────────┐  │
│  │                   StreamGenerator                      │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌────────────┐   │  │
│  │  │ 64B Chopper  │  │ RasterOrder  │  │  Z-Order   │   │  │
│  │  └──────────────┘  │   Pattern    │  │  Pattern   │   │  │
│  │  ┌──────────────┐  └──────────────┘  └────────────┘   │  │
│  │  │ SBWC Header/ │                                     │  │
│  │  │ Payload Gen  │                                     │  │
│  │  └──────────────┘                                     │  │
│  └────────────────────────────┬───────────────────────────┘  │
├──────────────────────────────────────────────────────────────┤
│                     Scheduling Layer                          │
│  ┌──────────────────────────────────────────────────────┐    │
│  │          VirtualTickScheduler (clock-proportional)    │    │
│  │  ┌────────────┐  ┌────────────┐  ┌──────────────┐   │    │
│  │  │  DmaAgent  │  │ Scoreboard │  │  MockSMMU    │   │    │
│  │  │  +Strategy │  │ (deps,     │  │ (IOVA→PA,    │   │    │
│  │  │  +ClkAccum │  │  progress) │  │  PTW inject) │   │    │
│  │  └────────────┘  └────────────┘  └──────────────┘   │    │
│  └────────────────────────────┬──────────────────────────┘   │
├──────────────────────────────────────────────────────────────┤
│                     Output Layer                              │
│  ┌────────────────┐  ┌──────────────────┐  ┌─────────────┐  │
│  │   trace.txt    │  │ trace_summary.txt │  │ trace_bw    │  │
│  │  (tick-based)  │  │ (IP-grouped BW,  │  │ .html       │  │
│  │               │  │  DMA config)      │  │ (Plotly)    │  │
│  └────────────────┘  └──────────────────┘  └─────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

---

## Module Details

### 1. config_parser.py

**Purpose**: YAML Configuration Parser + Validation + Instance Expansion

#### Data Classes

| Class | Description |
|-------|-------------|
| `CoreSpec` | Dir, BusByte, PPC, BPP, Plane |
| `CtrlSpec` | VOTF, Qurgent, req_MO |
| `BufferSpec` | Fifo, CTS, AXID, HWAPG, FRO |
| `DmaIpSpec` | Complete IP spec (Core + Access + Ctrl + Buffer + **ip_group**) |
| `DependencyConfig` | wait_for, granularity, margin |
| `BehaviorProfile` | type, pipeline_group, backpressure_source, block_size, flush_bytes |
| `TaskConfig` | task_name, ip_name, clock (MHz), format, resolution, **sbwc_ratio**, dependency, behavior |
| `ScenarioConfig` | name, memory_policy, tasks |

#### Instance Expansion

When `load_ip_spec()` encounters an entry with `Instances: [A, B, C]`, it:
1. Clones the spec for each instance name
2. Sets `ip_group` from the `IP` field (defaults to entry name)
3. Removes the template entry — only expanded instances remain

#### Sanity Check Validations

1. Referenced IP exists in spec (after instance expansion)
2. AccessType is supported by the IP
3. Clock frequency is a positive integer
4. Dependency targets exist as task names
5. Backpressure source exists as a task

---

### 2. format_descriptor.py

**Purpose**: Image format database, per-plane geometry, and SBWC layout computation

#### FORMAT_DB

| Format Family | Variants | Planes | Sub-sampling |
|---------------|----------|--------|-------------|
| YUV 4:2:0 | 8bit, 10bit (2-plane) | 2 | H:2, V:2 |
| YUV 4:2:2 | 8bit, 10bit (2-plane) | 2 | H:2, V:1 |
| YUV 4:4:4 | 8bit (3-plane) | 3 | H:1, V:1 |
| RGB | 8bit, 10bit, RGBA | 1 | None |
| Bayer | 8/10/12bit | 1 | None |
| RAW | - | 1 | None |
| **SBWC YUV** | 420_8/10bit, 422_8bit | 2 | Same as base |
| **SBWC Bayer** | 10/12bit | 1 | None |

#### SBWC_BLOCK_DB

Format-specific block alignment for SBWC compression:

| Family | block_w | block_h | Usage |
|--------|---------|---------|-------|
| YUV | 32 | 4 | 32×4 pixel blocks |
| Bayer | 256 | 1 | 256×1 pixel rows |

#### SbwcDescriptor

Computes SBWC memory layout:
- **Header**: `ceil(W/block_w) × ceil(H/block_h) × 16B`, aligned to 32B
- **Payload**: `original_bytes × comp_ratio`, aligned to 128B
- Layout returns per-plane header/payload sizes for address allocation

---

### 3. domain_model.py

**Purpose**: Core data model for AXI transactions

```python
@dataclass
class AxiTransaction:
    id: int                    # Unique ID (assigned by scheduler)
    port: str                  # DMA port name
    type: str                  # "ReadNoSnoop" | "WriteNoSnoop"
    address: int               # Physical address (hex formatted)
    bytes: int                 # Transfer size (≤ 64)
    burst: str = "seq"
    tick: Optional[int]        # Virtual Tick (YAML mode)
    plane: int = 0             # Plane index (0=Y, 1=UV)
    rw: str = "W"              # Internal R/W flag
    iova: Optional[int]        # SMMU: pre-translation address
    cache: str = "Normal"      # "Normal" or "SBWC_Alloc"
```

Output format:
```
tick=5 id=1 port=CAM_ISP_WR_0 type=WriteNoSnoop address=0x80000000 bytes=64 burst=seq cache=SBWC_Alloc
```

---

### 4. generator.py

**Purpose**: Transaction stream generation with 64B chopping, access patterns, and SBWC

#### 64B Boundary Chopper

```python
chop_at_64b_boundary(addr=0x1030, size=64)
# → [(0x1030, 16), (0x1040, 48)]
```

#### Access Patterns (Strategy Pattern)

| Pattern | Description | Memory Layout |
|---------|-------------|---------------|
| `RasterOrderPattern` | Line-by-line scan respecting stride | Sequential rows |
| `ZOrderPattern` | Macro-tile scan (default 64×32) | Tiled blocks, linearized |

#### SBWC Stream Generation

When `sbwc_ratio > 0`:
1. `_generate_sbwc_header_stream()` — 32B granularity sequential access, `cache=SBWC_Alloc`
2. `_generate_sbwc_payload_stream()` — Compressed data, 64B max burst, `cache=SBWC_Alloc`
3. Header and payload at separate 4KB-aligned addresses per plane

#### Stream Generation Pipeline

```
Task Config → SBWC check:
  ├── SBWC ON:  SbwcDescriptor.get_layout() → per-plane:
  │     Header stream (32B) + Payload stream (compressed)
  │     All tagged cache=SBWC_Alloc
  └── SBWC OFF: get_plane_info() → per-plane:
        AccessPattern.generate_addresses(plane, start_addr)
          → chop_at_64b_boundary → AxiTransaction → Stream
```

---

### 5. scheduler.py

**Purpose**: Tick-based multi-agent simulation with clock-proportional scheduling

#### Clock-Proportional Scheduling

```python
max_clock = max(agent.clock_mhz for agent in agents)

for tick in range(max_ticks):
    for agent in agents:
        agent._clock_accum += agent.clock_mhz
        if agent._clock_accum < max_clock:
            continue                          # Skip: not this agent's turn
        agent._clock_accum -= max_clock       # Step: agent's clock edge
        txs = agent.step(tick, scoreboard)
```

Effect: 800MHz agent steps ~1.5× more often than 533MHz agent.

#### DmaAgent

| Field | Source | Description |
|-------|--------|-------------|
| `bytes_per_tick` | PPC × BPP/8 | Data generated per clock |
| `clock_mhz` | Task config | Clock frequency for scheduling |
| `bus_byte` | IP Spec | Bus width for burst threshold |
| `req_mo` | IP Spec | Max Outstanding requests |
| `_clock_accum` | Runtime | Clock accumulator for proportional scheduling |

#### Scoreboard

```python
scoreboard.update("ISP_Write", completed_line=540)
scoreboard.can_proceed("ISP_Write", required_line=530, margin=10)  # → True
```

---

### 6. behavior.py

**Purpose**: DMA behavioral modeling via Strategy Pattern

#### EagerMOStrategy (Image RDMA/WDMA)

```
Each tick:
  1. Check backpressure → if upstream stalled, emit nothing
  2. internal_buffer += PPC × BPP (bytes_per_tick)
  3. While buffer ≥ BusByte AND MO budget > 0:
       emit next transaction, decrement buffer and MO
```

#### AccumulateAndFlushStrategy (Stat/Metadata DMA)

```
Each tick:
  1. Check pipeline pixel progress via Scoreboard
  2. If progress < Block_Size (e.g., 64×64 = 4096 pixels): silent
  3. If triggered: burst Flush_Bytes / 64 transactions at once
```

---

### 7. smmu_model.py

**Purpose**: Mock IOVA → PA translation (optional, default disabled)

| Mode | Behavior |
|------|----------|
| CMA | Contiguous pages from reserved region (0x4000_0000+) |
| SG | Random pages from general pool (0x8000_0000+) |

PTW Injection: on TLB miss, 64B `ReadNoSnoop` injected before actual transaction.

---

### 8. gen_summary.py

**Purpose**: Trace analysis + IP-grouped hierarchical report

All sections display data grouped by IP with subtotals:

| Section | Content |
|---------|---------|
| IP Overview | TX count, data volume, tick range per DMA, IP subtotals |
| DMA Config | IP group, Dir, BusByte, BurstLen, PPC, BPP, Clock, Format, SBWC |
| Simulation Stats | Duration, avg TX/tick, avg bandwidth |
| BW Breakdown | Per-DMA B/tick with IP subtotals, GB/s @MHz |
| Address Ranges | Start/end address, span per DMA |
| 64B Compliance | Boundary violation count |
| Behavior Analysis | Pattern classification, burst stats, duty cycle |

---

### 9. gen_bw_chart.py

**Purpose**: Interactive HTML bandwidth chart using Plotly.js

Two chart sections:
1. **IP-Level BW** — DMA traffic summed per IP group, Read/Write separated
2. **Per-DMA BW** — Individual DMA channel bandwidth over time

Features: 1000-tick time bins, hover tooltips, zoom/pan, dark theme.

---

## Data Flow

### YAML Mode Pipeline

```
DMA_IP_Spec.yaml + Scenario.yaml
    │
    ▼
ConfigParser.load_ip_spec()     ← Instance expansion (Instances → clones)
ConfigParser.load_scenario()    ← Clock MHz, SBWC_Ratio parsing
    │
    ▼
ConfigParser.sanity_check()
    │
    ▼
build_scheduler()
  ├── ImageFormatDescriptor / SbwcDescriptor
  ├── StreamGenerator.generate_streams_for_task()
  │     ├── SBWC: header + payload streams (cache=SBWC_Alloc)
  │     └── Normal: per-plane streams (64B chopping)
  ├── BehaviorStrategy selection (Eager / Accumulate)
  ├── DmaAgent creation (per task, clock_mhz set)
  └── VirtualTickScheduler setup
    │
    ▼
VirtualTickScheduler.run()
  ├── Clock-proportional accumulator gating
  ├── Dependency gating (Scoreboard)
  ├── Strategy.step() → transactions
  ├── MockSMMU.translate() (if enabled)
  └── Scoreboard.update()
    │
    ▼
Assign sequential IDs → Export trace.txt
    │
    ├── generate_summary() → trace_summary.txt (IP-grouped)
    └── generate_bw_chart() → trace_bw.html (Plotly)
```

---

## Key Algorithms

### 64B Boundary Chopping

AXI protocol requires transactions not to cross 64-byte aligned boundaries.

```
Input:  addr=0x1030, size=64
Step 1: Next boundary = 0x1040
Step 2: First chunk = (0x1030, 16)   ← fills to boundary
Step 3: Second chunk = (0x1040, 48)  ← within next boundary
Output: [(0x1030, 16), (0x1040, 48)]
```

### Clock-Proportional Scheduling

```
Agents: A (800MHz), B (533MHz)
max_clock = 800

Tick 0: A.accum=800≥800 → step; B.accum=533<800 → skip
Tick 1: A.accum=800≥800 → step; B.accum=1066≥800 → step (accum=266)
Tick 2: A.accum=800≥800 → step; B.accum=799<800 → skip
Tick 3: A.accum=800≥800 → step; B.accum=1332≥800 → step (accum=532)

Ratio: A steps 4/4, B steps 2/4 = 50% → 533/800 ≈ 66.6% (converges over time)
```

### SBWC Header/Payload Layout

```
Per-plane memory layout (SBWC_YUV420_8bit, 3840×2160):

Header region:
  blocks_x = ceil(3840/32) = 120
  blocks_y = ceil(2160/4)  = 540
  header_size = 120 × 540 × 16B = 1,036,800B → 32B aligned

Payload region (50% compression):
  original = stride × height = 3840 × 2160 = 8,294,400B
  payload = 8,294,400 × 0.5 = 4,147,200B → 128B aligned

Total SBWC = header + payload ≈ 5.2MB (vs 7.9MB original = 34% saving)
```

### Behavior Strategy Selection

| Behavior_Profile.Type | Strategy Class | Trigger |
|----------------------|----------------|---------|
| `Eager_MO_Burst` | `EagerMOStrategy` | Every tick (buffer ≥ BusByte) |
| `Accumulate_and_Flush` | `AccumulateAndFlushStrategy` | Block_Size pixels processed |

---

## Dependency System

### Scoreboard-Based (YAML Mode)

```yaml
Dependency:
  - Wait_For: "ISP_Write"
    Granularity: "Line"
    Margin: 10
```

Semantics: Consumer can process line `L` only when producer has completed line `L - margin`.

### Backpressure Propagation

```
ISP_Read.Backpressure_Source = "ISP_Write"

Tick N:   ISP_Write.stalled → ISP_Read detects → emits nothing
Tick N+1: ISP_Write resumes → ISP_Read resumes
```

---

## Appendix: Legacy CSV Mode

### ip_config.csv Fields

| Field | Description |
|-------|-------------|
| IP | IP name |
| GroupName | Group identifier for dependency matching |
| In/Out | Direction (In=Read, Out=Write) |
| H size, V size | Resolution |
| Color Format | Bayer, YUV, or RGB |
| Bit Width | Bits per pixel component |
| R/W Rate | Bandwidth ratio (0.0-1.0) |
| Outstanding | Buffer depth |
| Comp Mode/Ratio | Compression enable + ratio |
| LLC Enable | LLC allocation hint |
| Line Delay | Initial line delay (cycles) |

### dependency_config.csv Fields

| Field | Description |
|-------|-------------|
| Consumer Group | Group waiting for sync |
| Producer Group | Group providing sync |
| Sync Type | M2M (frame) or OTF (line) |
| Delay | Additional cycles |
