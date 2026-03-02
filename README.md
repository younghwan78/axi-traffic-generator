# AXI Traffic Generator

Python-based AXI Traffic Generator for SoC Multimedia Simulation. Generates realistic AXI transaction traces with tick-based scheduling, 64B boundary chopping, multi-plane DMA modeling, and optional SMMU simulation.

## Features

- ✅ **YAML Configuration**: DMA IP hardware spec + dynamic scenario definition
- ✅ **64B Boundary Chopping**: All transactions respect 64-byte AXI boundary
- ✅ **Access Patterns**: Raster-order and Z-order (tiled) memory access
- ✅ **Multi-Plane DMA**: Per-plane independent streams (Y/UV separation)
- ✅ **Virtual Tick Scheduler**: Clock-domain aware multi-agent simulation
- ✅ **Behavior Strategies**: Eager_MO_Burst (image DMA) and Accumulate_and_Flush (stat data)
- ✅ **SMMU Modeling**: Optional IOVA→PA translation with CMA/SG fragmentation and PTW injection
- ✅ **Dependency & Backpressure**: Scoreboard-based line/tile/frame dependency gating
- ✅ **Traffic Analysis**: Bandwidth breakdown, address range, 64B compliance, behavior pattern detection
- ✅ **Legacy CSV Support**: Backward compatibility with existing CSV-based workflows

## Quick Start

### YAML Mode (Recommended)

**1. Define IP Hardware Spec** — `DMA_IP_Spec.yaml`:
```yaml
CAM_ISP_WR_0:
  Core:   { Dir: W, BusByte: 32, PPC: 4, BPP: 12, Plane: 2 }
  Access: [ raster-order, Z-order ]
  Ctrl:   { VOTF: true, Qurgent: true, req_MO: 8 }
  Buffer: { Fifo: 2048, CTS: 256, AXID: 4, HWAPG: true, FRO: 4 }
```

**2. Define Scenario** — `Scenario_4K.yaml`:
```yaml
Scenario_Info:
  Name: "4K_Camera_to_Display"
Clock_Domains:
  MM: 800
  UD: 533
Memory_Policy:
  SMMU_Enable: false
  CMA_Ratio: 0.3
Tasks:
  - TaskName: "ISP_Write"
    IP_Name: "CAM_ISP_WR_0"
    Clock: "MM"
    Format: "YUV420_8bit_2plane"
    Resolution: [3840, 2160]
    AccessType: "raster-order"
    Behavior_Profile:
      Type: "Eager_MO_Burst"
      Pipeline_Group: "CAM_FE_PIPE"
```

**3. Generate Trace**:
```powershell
python main.py --yaml DMA_IP_Spec.yaml Scenario_4K.yaml trace.txt
```

**4. Output**:
- `trace.txt` — Tick-based AXI transaction trace
- `trace_summary.txt` — Traffic analysis report

### Legacy CSV Mode

```powershell
python main.py ip_config.csv trace.txt dependency_config.csv
```

## Project Structure

```
21_MMIP_TG/
├── main.py                # Main orchestrator (YAML + CSV modes)
├── config_parser.py       # YAML parser + sanity check
├── format_descriptor.py   # Image format DB + plane geometry
├── domain_model.py        # AxiTransaction dataclass
├── generator.py           # 64B chopping + access patterns + per-plane streams
├── scheduler.py           # Virtual Tick scheduler + Scoreboard + DmaAgent
├── behavior.py            # Behavior strategies (Eager MO / Accumulate & Flush)
├── smmu_model.py          # Mock SMMU (IOVA→PA, PTW injection)
├── dependency.py          # Legacy inter/intra-IP dependency manager
├── gen_summary.py         # Traffic & dependency summary generator
├── utils.py               # Address allocation + multimedia utilities
├── check_deps.py          # Quick dependency checker
├── DMA_IP_Spec.yaml       # Example IP hardware spec
├── Scenario_4K.yaml       # Example 4K scenario
├── ip_config.csv          # Legacy IP config (CSV)
├── dependency_config.csv  # Legacy dependency config (CSV)
├── README.md              # This file
├── DESIGN.md              # Architecture documentation
└── 개선사항.md              # Improvement requirements spec
```

## Output Format

### YAML Mode (tick-based)
```
tick=5 id=1 port=CAM_ISP_WR_0 type=WriteNoSnoop address=0x80000000 bytes=64 burst=seq
tick=517 id=119 port=CAM_STAT_WR_0 type=WriteNoSnoop address=0x817bc000 bytes=64 burst=seq
```

### Legacy CSV Mode (dep-based)
```
id=1 port=CAM_FE type=WriteNoSnoop address=0x80000000 bytes=64 burst=seq req=100
id=21601 port=ISP_FE type=ReadNoSnoop address=0x80152000 bytes=64 burst=seq dep=1,req+0
```

### Summary Report (YAML mode)
```
[*] IP Transaction Overview:
  CAM_ISP_WR_0         [WR] :  194400 tx  (12,150.0 KB)  tick 5 ~ 2,073,594
  CAM_STAT_WR_0        [WR] :   16904 tx  (1,056.5 KB)  tick 517 ~ 2,073,583

[*] Per-IP Bandwidth Breakdown:
  CAM_ISP_WR_0         :      6.0 B/tick  ( 32.4%)  [11.87 MB]

[*] 64B Boundary Compliance:
  ✓ All 600,104 transactions comply with 64B boundary alignment

[*] Behavior Pattern Analysis:
  CAM_ISP_WR_0         : Steady-stream    avg_burst=1.0  max_burst=1  duty=9.4%
  CAM_STAT_WR_0        : Flush-burst      avg_burst=4.0  max_burst=4  duty=0.2%
```

## Configuration Reference

### DMA_IP_Spec.yaml

| Section | Field | Description |
|---------|-------|-------------|
| **Core** | Dir | R (Read) or W (Write) |
| | BusByte | Bus width in bytes (16, 32) |
| | PPC | Pixels Per Clock |
| | BPP | Bits Per Pixel |
| | Plane | Number of planes (1, 2, 3) |
| **Access** | - | List: `raster-order`, `Z-order` |
| **Ctrl** | VOTF | Virtual OTF enable |
| | Qurgent | Quality-Urgent signaling |
| | req_MO | Max Outstanding requests |
| **Buffer** | Fifo | Internal FIFO size (bytes) |
| | CTS | Client Transaction Size |

### Scenario.yaml

| Section | Field | Description |
|---------|-------|-------------|
| **Clock_Domains** | \<name\>: MHz | Clock domain frequency |
| **Memory_Policy** | SMMU_Enable | Enable SMMU simulation |
| | CMA_Ratio | Contiguous memory ratio (0.0-1.0) |
| **Tasks[]** | IP_Name | Reference to DMA_IP_Spec entry |
| | Format | Image format (e.g., `YUV420_8bit_2plane`) |
| | Resolution | [width, height] |
| | AccessType | `raster-order` or `Z-order` |
| | Dependency[] | Wait_For, Granularity, Margin |
| | Behavior_Profile | Type, Pipeline_Group, Backpressure_Source |

### Supported Image Formats

`YUV420_8bit_2plane`, `YUV420_10bit_2plane`, `YUV422_8bit_2plane`, `YUV422_10bit_2plane`, `YUV444_8bit_3plane`, `RGB_8bit`, `RGB_10bit`, `RGBA_8bit`, `Bayer_8bit`, `Bayer_10bit`, `Bayer_12bit`, `RAW`

## Requirements

- Python 3.9+
- PyYAML (`pip install pyyaml`)

## License

MIT License

## Author

SoC Multimedia IP Modeling Team
