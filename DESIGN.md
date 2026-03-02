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
│  └────────┬─────────┘    └──────────┬───────────┘            │
│           └────────────┬────────────┘                        │
│                ┌───────┴───────┐                             │
│                │ ConfigParser  │  ← Sanity Check             │
│                └───────┬───────┘                             │
├──────────────────────────────────────────────────────────────┤
│                     Model Layer                               │
│  ┌──────────────────┐  ┌──────────────────┐                  │
│  │ FormatDescriptor │  │  AxiTransaction   │                  │
│  │ (PlaneInfo, BPP) │  │ (tick,plane,rw,..)│                  │
│  └────────┬─────────┘  └──────────────────┘                  │
├──────────────────────────────────────────────────────────────┤
│                     Generation Layer                          │
│  ┌────────────────────────────────────────────────────────┐  │
│  │                   StreamGenerator                      │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌────────────┐   │  │
│  │  │ 64B Chopper  │  │ RasterOrder  │  │  Z-Order   │   │  │
│  │  └──────────────┘  │   Pattern    │  │  Pattern   │   │  │
│  │                    └──────────────┘  └────────────┘   │  │
│  └────────────────────────────┬───────────────────────────┘  │
├──────────────────────────────────────────────────────────────┤
│                     Scheduling Layer                          │
│  ┌──────────────────────────────────────────────────────┐    │
│  │              VirtualTickScheduler                     │    │
│  │  ┌────────────┐  ┌────────────┐  ┌──────────────┐   │    │
│  │  │  DmaAgent  │  │ Scoreboard │  │  MockSMMU    │   │    │
│  │  │  +Strategy │  │ (deps,     │  │ (IOVA→PA,    │   │    │
│  │  │  (per task)│  │  progress) │  │  PTW inject) │   │    │
│  │  └────────────┘  └────────────┘  └──────────────┘   │    │
│  └────────────────────────────┬──────────────────────────┘   │
├──────────────────────────────────────────────────────────────┤
│                     Output Layer                              │
│  ┌────────────────┐  ┌──────────────────────┐                │
│  │   trace.txt    │  │  trace_summary.txt   │                │
│  │  (tick-based)  │  │ (BW, addr, behavior) │                │
│  └────────────────┘  └──────────────────────┘                │
└──────────────────────────────────────────────────────────────┘
```

---

## Module Details

### 1. config_parser.py

**Purpose**: YAML Configuration Parser + Validation

#### Data Classes

| Class | Description |
|-------|-------------|
| `CoreSpec` | Dir, BusByte, PPC, BPP, Plane |
| `CtrlSpec` | VOTF, Qurgent, req_MO |
| `BufferSpec` | Fifo, CTS, AXID, HWAPG, FRO |
| `DmaIpSpec` | Complete IP spec (Core + Access + Ctrl + Buffer) |
| `DependencyConfig` | wait_for, granularity, margin |
| `BehaviorProfile` | type, pipeline_group, backpressure_source, block_size, flush_bytes |
| `TaskConfig` | task_name, ip_name, clock, format, resolution, dependency, behavior |
| `ScenarioConfig` | name, clock_domains, memory_policy, tasks |

#### Sanity Check Validations

1. Referenced IP exists in spec
2. AccessType is supported by the IP
3. Clock domain is defined
4. Dependency targets exist as task names
5. Backpressure source exists as a task

---

### 2. format_descriptor.py

**Purpose**: Image format database and per-plane geometry computation

#### FORMAT_DB

Supported formats with sub-sampling metadata:

| Format Family | Variants | Planes | Sub-sampling |
|---------------|----------|--------|-------------|
| YUV 4:2:0 | 8bit, 10bit (2-plane) | 2 | H:2, V:2 |
| YUV 4:2:2 | 8bit, 10bit (2-plane) | 2 | H:2, V:1 |
| YUV 4:4:4 | 8bit (3-plane) | 3 | H:1, V:1 |
| RGB | 8bit, 10bit, RGBA | 1 | None |
| Bayer | 8/10/12bit | 1 | None |
| RAW | - | 1 | None |

#### PlaneInfo Computation

```python
ImageFormatDescriptor.get_plane_info("YUV420_8bit_2plane", 1920, 1080)
# → [PlaneInfo(0, 1920, 1080, 1.0, stride=1920, total=2073600),   # Y
#    PlaneInfo(1,  960,  540, 2.0, stride=1920, total=1036800)]    # UV
```

Key properties:
- `stride` = ceil(line_bytes / 64) × 64  (64B aligned)
- UV plane: interleaved U+V → `bpp = bpp_component × 2`
- Plane total = stride × height

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
    # Legacy fields: hint, dep, req_delay, deadline
```

Output format:
```
tick=5 id=1 port=CAM_ISP_WR_0 type=WriteNoSnoop address=0x80000000 bytes=64 burst=seq
```

---

### 4. generator.py

**Purpose**: Transaction stream generation with 64B chopping and access patterns

#### 64B Boundary Chopper

```python
chop_at_64b_boundary(addr=0x1030, size=64)
# → [(0x1030, 16), (0x1040, 48)]
# First chunk: 16B to reach next 64B boundary
# Second chunk: remaining 48B within the next 64B boundary
```

#### Access Patterns (Strategy Pattern)

| Pattern | Description | Memory Layout |
|---------|-------------|---------------|
| `RasterOrderPattern` | Line-by-line scan respecting stride | Sequential rows |
| `ZOrderPattern` | Macro-tile scan (default 64×32) | Tiled blocks, linearized |

#### Stream Generation Pipeline

```
Task Config → get_plane_info() → per-plane:
  AccessPattern.generate_addresses(plane, start_addr)
    → yields (raw_addr, raw_size)
      → chop_at_64b_boundary(raw_addr, raw_size)
        → AxiTransaction(addr, chopped_size, plane=idx)
          → Stream
```

---

### 5. scheduler.py

**Purpose**: Tick-based multi-agent simulation

#### Scoreboard

Tracks producer progress for consumer dependency gating:

```python
scoreboard.update("ISP_Write", completed_line=540)
scoreboard.can_proceed("ISP_Write", required_line=530, margin=10)  # → True
```

#### DmaAgent

Per-task simulation state:

| Field | Source | Description |
|-------|--------|-------------|
| `bytes_per_tick` | PPC × BPP/8 | Data generated per clock |
| `bus_byte` | IP Spec | Bus width for burst threshold |
| `req_mo` | IP Spec | Max Outstanding requests |
| `internal_buffer` | Accumulated | Virtual internal data buffer |
| `stalled` | Runtime | Backpressure indicator |
| `backpressure_source` | Scenario | Linked upstream agent |

#### VirtualTickScheduler.run()

```
for tick in 0..max_ticks:
    for agent in agents (round-robin):
        if agent.finished: skip
        if not dependency_satisfied: skip
        txs = agent.strategy.step(tick, scoreboard)
        if smmu.enabled: txs = apply_smmu(txs)
        update scoreboard
    if all finished: break
```

#### build_scheduler() Factory

Wires everything together:
1. Parse configs → generate per-plane transaction pools
2. Select BehaviorStrategy per task
3. Create DmaAgent per task
4. Register all agents in scheduler

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

Typical pattern: **Steady-stream** (1 tx per active tick)

#### AccumulateAndFlushStrategy (Stat/Metadata DMA)

```
Each tick:
  1. Check pipeline pixel progress via Scoreboard
  2. If progress < Block_Size (e.g., 64×64 = 4096 pixels): silent
  3. If triggered: burst Flush_Bytes / 64 transactions at once
```

Typical pattern: **Flush-burst** (4 tx per trigger, low duty cycle)

---

### 7. smmu_model.py

**Purpose**: Mock IOVA → PA translation (optional, default disabled)

#### PhysicalAddressPool

| Mode | Behavior |
|------|----------|
| CMA | Contiguous pages from reserved region (0x4000_0000+) |
| SG | Random pages from general pool (0x8000_0000+) |

Decision: `random() < cma_ratio` → CMA, else SG

#### MockSMMU.translate(iova, size)

1. Split range into 4KB pages
2. For each page: map if unmapped (CMA or SG)
3. Track TLB: first access → `is_new_page=True`
4. Return list of `TranslationResult(pa, size, is_new_page)`

#### PTW Injection

On TLB miss, a 64B `ReadNoSnoop` is injected before the actual transaction:

```
ptw_addr = PT_BASE + (page_num % 0x10000) × 64
```

---

### 8. gen_summary.py

**Purpose**: Trace analysis + comprehensive report generation

Auto-detects mode (tick-based YAML vs dep-based Legacy) and generates appropriate sections:

**YAML Mode Sections**:

| Section | Content |
|---------|---------|
| IP Overview | TX count, data volume, tick range per IP |
| Simulation Stats | Duration, avg TX/tick, avg bandwidth |
| BW Breakdown | Per-IP B/tick, percentage, total MB |
| Address Ranges | Start/end address, span per IP |
| 64B Compliance | Boundary violation count |
| Behavior Analysis | Pattern classification (Steady/Flush/Eager), burst stats, duty cycle |

**Legacy Mode Sections**:
- Dependency graph, intra-IP deps (outstanding, rate), inter-IP deps (OTF, M2M)

---

## Data Flow

### YAML Mode Pipeline

```
DMA_IP_Spec.yaml + Scenario.yaml
    │
    ▼
ConfigParser.load_ip_spec() + load_scenario()
    │
    ▼
ConfigParser.sanity_check()
    │
    ▼
build_scheduler()
  ├── ImageFormatDescriptor.get_plane_info()
  ├── StreamGenerator.generate_streams_for_task()  ← per-plane, 64B chopping
  ├── BehaviorStrategy selection (Eager / Accumulate)
  ├── DmaAgent creation (per task)
  └── VirtualTickScheduler setup
    │
    ▼
VirtualTickScheduler.run()
  ├── Tick loop (round-robin agents)
  ├── Dependency gating (Scoreboard)
  ├── Strategy.step() → transactions
  ├── MockSMMU.translate() (if enabled)
  └── Scoreboard.update()
    │
    ▼
Assign sequential IDs → Export trace.txt
    │
    ▼
generate_summary() → trace_summary.txt
```

### Legacy CSV Mode Pipeline

```
ip_config.csv + dependency_config.csv
    │
    ▼
AxiTrafficGenerator.load_ip_config()
    │
    ▼
generate_streams() → per-IP Stream objects
    │
    ▼
assign_transaction_ids()
    │
    ▼
apply_intra_dependencies() (rate, outstanding)
    │
    ▼
apply_inter_dependencies() (M2M, OTF)
    │
    ▼
export_trace() → trace.txt + summary
```

---

## Key Algorithms

### 64B Boundary Chopping

AXI protocol requires transactions not to cross 64-byte aligned boundaries.

```
Input:  addr=0x1030, size=64
Step 1: Next boundary = 0x1040
Step 2: First chunk = (0x1030, 16)   ← fills to boundary
Step 3: Remaining = 48 bytes
Step 4: Second chunk = (0x1040, 48)  ← within next boundary
Output: [(0x1030, 16), (0x1040, 48)]
```

### Z-Order Tiling

Tiles are laid out sequentially in memory (each tile = `tile_w × tile_h × bpp` bytes):

```
Image: 1920×1080, Tile: 64×32
tiles_x = ⌈1920/64⌉ = 30
tiles_y = ⌈1080/32⌉ = 34
Total tiles = 1020

Tile[ty][tx] base address = start + (ty × tiles_x + tx) × tile_bytes
Within tile: sequential 64B reads/writes
```

### Behavior Strategy Selection

| Behavior_Profile.Type | Strategy Class | Trigger |
|----------------------|----------------|---------|
| `Eager_MO_Burst` | `EagerMOStrategy` | Every tick (buffer ≥ BusByte) |
| `Accumulate_and_Flush` | `AccumulateAndFlushStrategy` | Block_Size pixels processed |

### Backpressure Propagation

```
ISP_Read.Backpressure_Source = "ISP_Write"

Tick N:
  ISP_Write.stalled = True (no buffer space)
  → ISP_Read detects upstream stall → emits nothing
  → ISP_Read.stalled = True

Tick N+1:
  ISP_Write resumes → ISP_Read resumes
```

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

### dep-Based (Legacy Mode)

| Type | Format | Meaning |
|------|--------|---------|
| Intra-IP rate | `dep=N-1,req+128` | Wait 128 cycles after previous TX request |
| Intra-IP outstanding | `dep=N-8,resp+0` | Wait for TX N-8 response |
| Inter-IP OTF | `dep=K,req+0` | Wait for producer TX K request |
| Inter-IP M2M | `dep=K,resp+200` | Wait for producer last TX response + 200 cycles |

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
