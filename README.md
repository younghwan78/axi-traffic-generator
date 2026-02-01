# AXI Traffic Generator

Python-based AXI Traffic Generator for SoC Multimedia Simulation. Converts high-level CSV scenarios into low-level AXI transaction traces with comprehensive dependency management.

## Features

- ✅ **Flexible Configuration**: Separate IP and dependency configuration files
- ✅ **Multiple Sync Types**: M2M (frame-level) and OTF (line-level) synchronization
- ✅ **Intra-IP Dependencies**: Rate limiting and outstanding buffer control
- ✅ **Inter-IP Dependencies**: Chain and pipeline dependency support
- ✅ **Automatic Analysis**: Built-in dependency summary with visual graph
- ✅ **Standard Libraries Only**: No external dependencies required

## Quick Start

### 1. Configure IPs
Edit `ip_config.csv`:
```csv
IP,GroupName,In/Out,H size,V size,Color Format,Bit Width,R/W Rate,Outstanding
CAM_FE,CAM,Out,1920,1080,Bayer,10,1.0,8
ISP_FE,INTCAM,In,1920,1080,Bayer,10,1.0,16
GPU_WR,GPU,Out,1024,768,RGB,8,0.5,8
DISP_RD,DPU,In,1024,768,RGB,8,1.0,16
```

### 2. Configure Dependencies (Optional)
Edit `dependency_config.csv`:
```csv
Consumer,Producer,Sync Type,Delay
ISP_FE,CAM_FE,OTF,100
DISP_RD,GPU_WR,M2M,0
```

### 3. Generate Trace
```powershell
python main.py ip_config.csv output.txt dependency_config.csv
```

### 4. Output
- `output.txt` - AXI transaction trace (154,728 transactions)
- `output_summary.txt` - Dependency analysis with visual graph

## Project Structure

```
21_MMIP_TG/
├── main.py                    # Main orchestrator
├── domain_model.py            # AxiTransaction dataclass
├── utils.py                   # Multimedia & address utilities
├── generator.py               # Stream generator
├── dependency.py              # Dependency manager
├── gen_summary.py             # Summary generator
├── check_deps.py              # Quick dependency checker
├── ip_config.csv              # IP configuration
├── dependency_config.csv      # Dependency configuration
├── test_scenario.csv          # Legacy format example
├── README.md                  # This file
├── DESIGN.md                  # Architecture documentation
└── README_DEPENDENCY.md       # Dependency scenarios guide
```

## Usage Examples

### Basic Usage
```powershell
# With dependencies
python main.py ip_config.csv trace.txt dependency_config.csv

# Without dependencies (parallel execution)
python main.py ip_config.csv trace.txt

# Legacy format (single CSV)
python main.py test_scenario.csv trace.txt
```

### Quick Dependency Check
```powershell
python check_deps.py trace.txt
```

## Configuration Guide

### IP Configuration Fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| IP | ✓ | - | IP name (e.g., "CAM_FE") |
| GroupName | ✓ | - | Group identifier |
| In/Out | ✓ | - | "In" (Read) or "Out" (Write) |
| H size | ✓ | - | Horizontal resolution (pixels) |
| V size | ✓ | - | Vertical resolution (lines) |
| Color Format | ✓ | - | Bayer, YUV, or RGB |
| Bit Width | ✓ | - | Bits per pixel component |
| R/W Rate | | 1.0 | Bandwidth ratio (0.0-1.0) |
| Outstanding | | 16 | Buffer depth |

### Dependency Configuration

| Field | Description | Example |
|-------|-------------|---------|
| Consumer | IP waiting for sync | ISP_FE |
| Producer | IP providing sync | CAM_FE |
| Sync Type | M2M (frame) or OTF (line) | OTF |
| Delay | Additional cycles | 100 |

## Dependency Types

### Intra-IP (Within IP)
- **Outstanding Limit**: Controls buffer depth
  - TX N depends on TX (N - outstanding) response
- **Rate Limiting**: Controls bandwidth
  - TX N depends on TX (N-1) request + delay cycles

### Inter-IP (Between IPs)
- **M2M (Memory-to-Memory)**: Frame-level sync
  - Consumer waits for producer's entire frame completion
  - Example: `DISP_RD => GPU_WR`
- **OTF (On-The-Fly)**: Line-level sync
  - Consumer processes line-by-line with producer
  - Example: `CAM_FE -> ISP_FE`

## Output Format

### Trace File
```
id=1 port=CAM_FE type=WriteNoSnoop address=0x80000000 bytes=64 burst=seq
id=9 port=CAM_FE type=WriteNoSnoop address=0x80000200 bytes=64 burst=seq dep=1,resp+0
id=40501 port=ISP_FE type=ReadNoSnoop address=0x80279000 bytes=64 burst=seq dep=1,req+100
```

### Summary File
```
[*] Dependency Graph:
  CAM_FE -> ISP_FE (OTF)
  GPU_WR => DISP_RD (M2M)

[>] CAM_FE
  Outstanding Limit: Active (interval = 8)
  Rate Limiting: None
```

## Requirements

- Python 3.9+
- No external libraries required (standard library only)

## Examples

See `README_DEPENDENCY.md` for detailed scenario examples:
- Mixed Pipeline (OTF + M2M)
- Full Chain (sequential M2M)
- Independent (no dependencies)

## Design

See `DESIGN.md` for detailed architecture and module documentation.

## License

MIT License

## Author

SoC Multimedia IP Modeling Team
