# Dependency Configuration Examples

This directory contains example dependency configurations.

## dependency_config.csv

The main dependency configuration file supports multiple scenarios by editing the file:

### Scenario 1: Mixed Pipeline (Default)
```csv
Consumer,Producer,Sync Type,Delay
ISP_FE,CAM_FE,OTF,100
DISP_RD,GPU_WR,M2M,0
```
- Two independent pipelines
- CAM_FE -> ISP_FE (line-by-line, 1350 dependencies)
- GPU_WR => DISP_RD (frame-level, 1 dependency)

### Scenario 2: Full Chain
```csv
Consumer,Producer,Sync Type,Delay
ISP_FE,CAM_FE,M2M,0
GPU_WR,ISP_FE,M2M,0
DISP_RD,GPU_WR,M2M,0
```
- Sequential: CAM_FE => ISP_FE => GPU_WR => DISP_RD
- Each IP waits for previous frame completion

### Scenario 3: No Dependencies
Delete dependency_config.csv or use empty CSV to run all IPs independently.

## Usage

```powershell
# Use dependency file
python main.py ip_config.csv trace.txt dependency_config.csv

# No dependencies
python main.py ip_config.csv trace.txt
```
