# AXI Traffic Generator

Python-based AXI Traffic Generator for SoC Multimedia Simulation. Converts high-level CSV scenarios into low-level AXI transaction traces with comprehensive dependency management.

## Features

- ✅ **Flexible Configuration**: Separate IP and dependency configuration files
- ✅ **Multiple Sync Types**: M2M (frame-level) and OTF (line-level) synchronization
- ✅ **Compression Support**: Format-specific alignment (Bayer, YUV) with configurable ratios
- ✅ **LLC Allocation Hints**: Last Level Cache optimization support
- ✅ **Line Delay**: Configurable timing delays for stream synchronization
- ✅ **Group-Based Dependencies**: Simplified dependency management at IP group level
- ✅ **Intra-IP Dependencies**: Rate limiting and outstanding buffer control
- ✅ **Inter-IP Dependencies**: Chain and pipeline dependency support
- ✅ **Automatic Analysis**: Built-in dependency summary with visual graph
- ✅ **Standard Libraries Only**: No external dependencies required

## Quick Start

### 1. Configure IPs
Edit `ip_config.csv`:
```csv
IP,GroupName,In/Out,H size,V size,Color Format,Bit Width,R/W Rate,Outstanding,Comp Mode,Comp Ratio,LLC Enable,Line Delay
CAM_FE,CAM,Out,1920,1080,Bayer,10,1.0,8,Enable,0.5,Disable,100
ISP_FE,INTCAM,In,1920,1080,Bayer,10,1.0,16,Disable,,Enable,0
ISP_WR,INTCAM,Out,1920,1080,YUV,8,0.8,12,Enable,0.6,Enable,50
GPU_WR,GPU,Out,1024,768,RGB,8,0.5,8,Disable,,Disable,0
DISP_RD,DPU,In,1024,768,RGB,8,1.0,16,Disable,,Enable,0
```

### 2. Configure Dependencies (Optional)
Edit `dependency_config.csv`:
```csv
Consumer Group,Producer Group,Sync Type,Delay
INTCAM,CAM,OTF,0
DPU,GPU,M2M,200
```

### 3. Generate Trace
```powershell
python main.py ip_config.csv output.txt dependency_config.csv
```

### 4. Output
- `output.txt` - AXI transaction trace (164,988 transactions)
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
| **Comp Mode** | | Disable | Enable/Disable compression |
| **Comp Ratio** | | - | Compression ratio (e.g., 0.5 for 50%) |
| **LLC Enable** | | Disable | Enable/Disable LLC allocation hint |
| **Line Delay** | | 0 | Initial line delay in cycles |

### Advanced Features

#### Compression Support
- **Comp Mode**: `Enable` or `Disable`
- **Comp Ratio**: Compression ratio (0.0-1.0)
- **Format-Specific Alignment**:
  - **Bayer**: Width aligned to 256 bytes
  - **YUV**: Width to 32 bytes, Height to 4 lines
  - **RGB**: No special alignment

#### LLC Allocation Hints
- `LLC Enable=Enable`: Adds `hint=LLC_ALLOC` to all transactions
- Optimizes Last Level Cache allocation for multimedia workloads

#### Line Delay
- `Line Delay=100`: First transaction delayed by 100 cycles
- Displayed as `req=100` in trace output
- Used for stream synchronization timing

### Dependency Configuration

**Legacy Format (DMA-based)**:
| Field | Description | Example |
|-------|-------------|---------| 
| Consumer | IP waiting for sync | ISP_FE |
| Producer | IP providing sync | CAM_FE |
| Sync Type | M2M (frame) or OTF (line) | OTF |
| Delay | Additional cycles | 100 |

**New Format (Group-based)**:
| Field | Description | Example |
|-------|-------------|---------|
| **Consumer Group** | Group waiting for sync | INTCAM |
| **Producer Group** | Group providing sync | CAM |
| Sync Type | M2M (frame) or OTF (line) | OTF |
| Delay | Additional cycles | 0 |

**Group-based Benefits**:
- Simplified configuration (group-level vs DMA-level)
- M2M: All consumer DMAs wait for last-completing producer DMA
- OTF: Line delay handled automatically via `Line Delay` field

## Dependency Types

### Intra-IP (Within IP)
- **Outstanding Limit**: Controls buffer depth
  - TX N depends on TX (N - outstanding) **request** + 0 cycles
- **Rate Limiting**: Controls bandwidth
  - TX N depends on TX (N-1) request + delay cycles
  - Delay = `(1/rate - 1) × 64 bytes`

### Inter-IP (Between IPs)
- **M2M (Memory-to-Memory)**: Frame-level sync
  - Consumer waits for producer's entire frame completion
  - Example: `DISP_RD => GPU_WR`
  - Group Format: All DPU DMAs wait for last GPU DMA
- **OTF (On-The-Fly)**: Line-level sync
  - Consumer processes line-by-line with producer
  - Example: `CAM_FE -> ISP_FE`
  - Timing controlled by `Line Delay` field

## Output Format

### Trace File
```
id=1 port=CAM_FE type=WriteNoSnoop address=0x80000000 bytes=64 burst=seq req=100
id=9 port=CAM_FE type=WriteNoSnoop address=0x80000200 bytes=64 burst=seq dep=1,req+0
id=21601 port=ISP_FE type=ReadNoSnoop address=0x80152000 bytes=64 burst=seq hint=LLC_ALLOC dep=1,req+0
```

**Key Attributes**:
- `id`: Unique transaction identifier
- `port`: DMA/IP name
- `type`: ReadNoSnoop or WriteNoSnoop
- `address`: Memory address (hex)
- `bytes`: Transfer size (typically 64)
- `burst`: Burst type (seq = sequential)
- `req=<N>`: Line delay (initial timing delay)
- `hint=<H>`: Cache hint (LLC_ALLOC for LLC optimization)
- `dep=<id>,<event>+<offset>`: Dependency on transaction <id>

### Summary File
```
[*] Dependency Graph:
  CAM_FE -> ISP_FE (OTF)
  GPU_WR => DISP_RD (M2M)

[>] CAM_FE
  Outstanding Limit: Active (interval = 8)
  Rate Limiting: None

[>] GPU_WR
  Rate Limiting: Active (delay = 128 cycles)
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
