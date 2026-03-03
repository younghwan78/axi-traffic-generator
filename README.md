# AXI Traffic Generator

Python-based AXI Traffic Generator for SoC Multimedia Simulation. Generates realistic AXI transaction traces with tick-based scheduling, 64B boundary chopping, multi-plane DMA modeling, SBWC compression, and optional SMMU simulation.

## Features

- ✅ **YAML Configuration**: DMA IP hardware spec + dynamic scenario definition
- ✅ **IP-DMA Hierarchy**: `IP` grouping with `Instances` auto-replication
- ✅ **64B Boundary Chopping**: All transactions respect 64-byte AXI boundary
- ✅ **Access Patterns**: Raster-order and Z-order (tiled) memory access
- ✅ **Multi-Plane DMA**: Per-plane independent streams (Y/UV separation)
- ✅ **SBWC Compression**: Header/Payload separated streams with format-specific block alignment
- ✅ **Clock-Proportional Scheduling**: Higher-frequency agents step more often
- ✅ **Behavior Strategies**: Eager_MO_Burst (image DMA) and Accumulate_and_Flush (stat data)
- ✅ **SMMU Modeling**: Optional IOVA→PA translation with CMA/SG fragmentation and PTW injection
- ✅ **Dependency & Backpressure**: Scoreboard-based line/tile/frame dependency gating
- ✅ **Traffic Analysis**: Summary report + interactive BW chart (Plotly HTML)
- ✅ **Legacy CSV Support**: Backward compatibility with existing CSV-based workflows

## Quick Start

### YAML Mode (Recommended)

**1. Define IP Hardware Spec** — `DMA_IP_Spec.yaml`:
```yaml
CAM_ISP_WR:
  IP: CAM_ISP                                      # IP group name
  Core:   { Dir: W, BusByte: 32, PPC: 4, BPP: 12, Plane: 2 }
  Access: [ raster-order, Z-order ]
  Ctrl:   { VOTF: true, Qurgent: true, req_MO: 8 }
  Buffer: { Fifo: 2048, CTS: 256, AXID: 4, HWAPG: true, FRO: 4 }
  Instances: [CAM_ISP_WR_0, CAM_ISP_WR_1]          # Auto-replicate
```

**2. Define Scenario** — `Scenario_4K.yaml`:
```yaml
Scenario_Info:
  Name: "4K_Camera_to_Display"
Memory_Policy:
  SMMU_Enable: false
  CMA_Ratio: 0.3
Tasks:
  - TaskName: "ISP_Write_Y"
    IP_Name: "CAM_ISP_WR_0"
    Clock: 800                    # MHz (direct frequency)
    Format: "SBWC_YUV420_8bit"    # SBWC compressed format
    Resolution: [3840, 2160]
    AccessType: "raster-order"
    SBWC_Ratio: 0.5               # 50% compression
    Behavior_Profile:
      Type: "Eager_MO_Burst"
      Pipeline_Group: "CAM_FE_PIPE"
```

**3. Generate Trace**:
```powershell
python main.py --yaml DMA_IP_Spec.yaml Scenario_4K.yaml output/trace.txt
```

**4. Output**:
- `trace.txt` — Tick-based AXI transaction trace
- `trace_summary.txt` — Traffic analysis report (IP-grouped, hierarchical)
- `trace_bw.html` — Interactive bandwidth chart (Plotly)

### Legacy CSV Mode

```powershell
python main.py ip_config.csv trace.txt dependency_config.csv
```

## Project Structure

```
21_MMIP_TG/
├── main.py                # Main orchestrator (YAML + CSV modes)
├── config_parser.py       # YAML parser + sanity check
├── format_descriptor.py   # Image format DB + SBWC descriptor
├── domain_model.py        # AxiTransaction dataclass
├── generator.py           # 64B chopping + access patterns + SBWC streams
├── scheduler.py           # Virtual Tick scheduler (clock-proportional)
├── behavior.py            # Behavior strategies (Eager MO / Accumulate & Flush)
├── smmu_model.py          # Mock SMMU (IOVA→PA, PTW injection)
├── dependency.py          # Legacy inter/intra-IP dependency manager
├── gen_summary.py         # Traffic summary report (IP-grouped)
├── gen_bw_chart.py        # BW chart HTML report (Plotly.js)
├── utils.py               # Address allocation + multimedia utilities
├── DMA_IP_Spec.yaml       # Example IP hardware spec
├── Scenario_4K.yaml       # Example 4K scenario (SBWC enabled)
├── Scenario_4K_SMMU.yaml  # Example SMMU-enabled scenario
├── WORKLOG.md             # Development work log
├── README.md              # This file
└── DESIGN.md              # Architecture documentation
```

## Output Format

### YAML Mode (tick-based)
```
tick=5 id=1 port=CAM_ISP_WR_0 type=WriteNoSnoop address=0x80000000 bytes=64 burst=seq cache=SBWC_Alloc
tick=517 id=119 port=CAM_STAT_WR_0 type=WriteNoSnoop address=0x817bc000 bytes=64 burst=seq
```

### Summary Report (IP-grouped, hierarchical)
```
[*] IP Transaction Overview:
  [CAM_ISP]
    CAM_ISP_RD_0         [RD] :  137700 tx  (7,340.6 KB)
    CAM_ISP_WR_0         [WR] :  137700 tx  (7,340.6 KB)
    CAM_ISP_WR_1         [WR] :   34425 tx  (1,835.2 KB)
    ── Subtotal                :  309825 tx  (16.13 MB)
  [DISP]
    DISP_RD_0            [RD] :  137700 tx  (7,340.6 KB)
    ── Subtotal                :  137700 tx  (7.17 MB)

[*] Per-DMA Bandwidth Breakdown:
  [CAM_ISP]
    CAM_ISP_WR_0         :      6.0 B/tick  ( 29.9%)  4.80 GB/s @800MHz
```

## Configuration Reference

### DMA_IP_Spec.yaml

| Section | Field | Description |
|---------|-------|-------------|
| *(top)* | **IP** | IP group name (for hierarchical summary) |
| *(top)* | **Instances** | List of DMA names to auto-replicate from template |
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
| **Tasks[]** | Clock | Clock frequency in MHz (e.g., 800) |
| | IP_Name | Reference to DMA_IP_Spec entry (expanded instance name) |
| | Format | Image format (e.g., `SBWC_YUV420_8bit`) |
| | Resolution | [width, height] |
| | AccessType | `raster-order` or `Z-order` |
| | **SBWC_Ratio** | Compression ratio (0 = off, 0.5 = 50%) |
| | Dependency[] | Wait_For, Granularity, Margin |
| | Behavior_Profile | Type, Pipeline_Group, Backpressure_Source |
| **Memory_Policy** | SMMU_Enable | Enable SMMU simulation |
| | CMA_Ratio | Contiguous memory ratio (0.0-1.0) |

### Supported Image Formats

**Standard:** `YUV420_8bit_2plane`, `YUV420_10bit_2plane`, `YUV422_8bit_2plane`, `YUV422_10bit_2plane`, `YUV444_8bit_3plane`, `RGB_8bit`, `RGB_10bit`, `RGBA_8bit`, `Bayer_8bit`, `Bayer_10bit`, `Bayer_12bit`, `RAW`

**SBWC Compressed:** `SBWC_YUV420_8bit`, `SBWC_YUV420_10bit`, `SBWC_YUV422_8bit`, `SBWC_Bayer_10bit`, `SBWC_Bayer_12bit`

| SBWC Family | Block Alignment | Header Align | Payload Align |
|-------------|----------------|--------------|---------------|
| YUV | 32 × 4 pixels | 32B | 128B |
| Bayer | 256 × 1 pixels | 32B | 128B |

## Requirements

- Python 3.9+
- PyYAML (`pip install pyyaml`)

## License

MIT License

## Author

SoC Multimedia IP Modeling Team
