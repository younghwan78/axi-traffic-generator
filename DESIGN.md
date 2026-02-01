# AXI Traffic Generator - Design Documentation

## Table of Contents
- [Architecture Overview](#architecture-overview)
- [Data Flow](#data-flow)
- [Module Details](#module-details)
- [Dependency System](#dependency-system)
- [Extension Points](#extension-points)

---

## Architecture Overview

### Design Philosophy

The AXI Traffic Generator follows a **modular, pipeline-based architecture** with clear separation of concerns:

1. **Configuration Layer**: CSV-based declarative configuration
2. **Generation Layer**: Transaction stream creation
3. **Dependency Layer**: Constraint application
4. **Export Layer**: Trace file output
5. **Analysis Layer**: Summary and visualization

```
┌─────────────────────────────────────────────────────────┐
│              Configuration (CSV Files)                   │
│  ┌──────────────────┐    ┌──────────────────────┐      │
│  │  ip_config.csv   │    │ dependency_config.csv│      │
│  └──────────────────┘    └──────────────────────┘      │
└─────────────────┬──────────────────┬──────────────────┘
                  │                  │
                  ▼                  ▼
         ┌────────────────┐  ┌──────────────────┐
         │ IP Generator   │  │ Dependency Rules │
         └────────┬───────┘  └─────────┬────────┘
                  │                    │
                  ▼                    ▼
         ┌──────────────────────────────────────┐
         │      Transaction Streams             │
         │  (AxiTransaction objects)            │
         └─────────┬────────────────────────────┘
                   │
                   ▼
         ┌──────────────────────────────────────┐
         │    Dependency Application            │
         │  - Intra-IP (rate, outstanding)      │
         │  - Inter-IP (M2M, OTF)               │
         └─────────┬────────────────────────────┘
                   │
                   ├─────────────┬────────────────┐
                   ▼             ▼                ▼
         ┌──────────────┐  ┌─────────┐  ┌─────────────┐
         │  trace.txt   │  │ summary │  │   metrics   │
         └──────────────┘  └─────────┘  └─────────────┘
```

---

## Data Flow

### 1. Configuration Parsing

**Input**: CSV files  
**Output**: Job dictionaries with normalized fields

```python
# main.py: load_ip_config()
ip_config.csv → CSV Parser → [
    {
        'IP': 'CAM_FE',
        'GroupName': 'CAM',
        'In/Out': 'Out',
        'H size': 1920,
        'V size': 1080,
        'Color Format': 'Bayer',
        'Bit Width': 10,
        'R/W Rate': 1.0,
        'Outstanding': 8
    },
    ...
]
```

### 2. Stream Generation

**Input**: Job configurations  
**Output**: Transaction streams

```python
# generator.py: StreamGenerator.generate()
For each job:
    1. Calculate total_size = H × V × BPP
    2. Allocate base address (4KB aligned)
    3. Split into 64-byte bursts
    4. Create AxiTransaction objects
    
Result: Stream(ip_name, [tx1, tx2, ..., txN])
```

### 3. ID Assignment

**Input**: Multiple streams  
**Output**: Globally numbered transactions

```python
# main.py: assign_transaction_ids()
Collect all transactions from all streams
Sort by (optional criteria)
Assign sequential IDs: 1, 2, 3, ..., N
```

### 4. Dependency Application

**Input**: Streams with IDs  
**Output**: Transactions with dependency links

```python
# dependency.py: DependencyManager
Intra-IP:
    - apply_rate_limiting() → dep=N-1,req+offset
    - apply_outstanding_limit() → dep=N-k,resp+0
    
Inter-IP:
    - apply_m2m_sync() → dep=producer_last,resp+0
    - apply_otf_sync() → dep=producer_line_k,req+offset
```

### 5. Export & Analysis

**Input**: Final transaction list  
**Output**: Trace file + Summary

```python
# main.py: export_trace()
Write each transaction in format:
    id=X port=P type=T address=A bytes=B burst=seq dep=...

# gen_summary.py: generate_summary()
Analyze trace → Generate summary with graph
```

---

## Module Details

### 1. domain_model.py

**Purpose**: Core data structures

#### AxiTransaction
```python
@dataclass
class AxiTransaction:
    id: int                    # Unique global ID
    port: str                  # IP name (e.g., "CAM_FE")
    type: str                  # ReadNoSnoop / WriteNoSnoop
    address: int               # Memory address (hex)
    bytes: int                 # Transfer size (64)
    burst: str = "seq"         # Burst type
    dep: List[str] = []        # ["target_id,event+offset", ...]
    
    def add_dependency(target_id, event, offset):
        # Adds formatted dependency string
```

**Key Features**:
- Immutable after creation (use `add_dependency()`)
- `__str__()` formats for trace output
- Dependency format: `dep=10,req+100|dep=5,resp+0`

---

### 2. utils.py

**Purpose**: Helper utilities

#### MultimediaUtils
```python
class MultimediaUtils:
    @staticmethod
    def calculate_bpp(color_format: str, bit_width: int) -> float:
        # Bayer: bit_width / 8
        # YUV: 1.5 * bit_width / 8
        # RGB: 3 * bit_width / 8
```

#### AddressAllocator
```python
class AddressAllocator:
    def __init__(self, base=0x80000000):
        self._current = base
    
    def allocate(self, size_bytes: int) -> int:
        # Returns 4KB-aligned address
        # Increments internal pointer
```

**Design Choice**: 4KB alignment prevents cache conflicts

---

### 3. generator.py

**Purpose**: Transaction stream creation

#### Stream
```python
@dataclass
class Stream:
    ip_name: str
    transactions: List[AxiTransaction]
    metadata: Dict  # Optional context
```

#### StreamGenerator
```python
class StreamGenerator:
    def generate(ip_name, start_addr, total_bytes, tx_type):
        BURST_SIZE = 64
        num_bursts = ceil(total_bytes / BURST_SIZE)
        
        transactions = []
        for i in range(num_bursts):
            addr = start_addr + (i * BURST_SIZE)
            tx = AxiTransaction(
                id=0,  # Assigned later
                port=ip_name,
                type=tx_type,
                address=addr,
                bytes=min(BURST_SIZE, remaining)
            )
            transactions.append(tx)
        
        return Stream(ip_name, transactions)
```

**Key Decisions**:
- Fixed BURST_SIZE = 64 bytes (AXI standard)
- IDs assigned globally (not in generator)
- Last transaction may be < 64 bytes

---

### 4. dependency.py

**Purpose**: Dependency constraint application

#### DependencyManager

**Intra-IP Methods**:
```python
def apply_rate_limiting(stream, rate):
    """
    TX[i] depends on TX[i-1] request + delay
    
    delay = (1.0 / rate - 1.0) * BURST_SIZE
    Example: rate=0.5 → delay=128 cycles
    """
    for i, tx in enumerate(stream.transactions[1:], 1):
        prev_tx = stream.transactions[i-1]
        delay = calculate_delay(rate)
        tx.add_dependency(prev_tx.id, "req", delay)

def apply_outstanding_limit(stream, outstanding):
    """
    TX[i] depends on TX[i-outstanding] response
    
    Ensures max 'outstanding' transactions in flight
    """
    for i, tx in enumerate(stream.transactions[outstanding:], outstanding):
        target_tx = stream.transactions[i - outstanding]
        tx.add_dependency(target_tx.id, "resp", 0)
```

**Inter-IP Methods**:
```python
def apply_m2m_sync(producer_stream, consumer_stream, delay):
    """
    Frame-level sync: Consumer waits for producer's last TX
    
    consumer.transactions[0].dep = producer.transactions[-1],resp+delay
    """
    last_producer = producer_stream.transactions[-1]
    first_consumer = consumer_stream.transactions[0]
    first_consumer.add_dependency(last_producer.id, "resp", delay)

def apply_otf_sync(producer_stream, consumer_stream, delay):
    """
    Line-level sync: Consumer[line_k] waits for Producer[line_k]
    
    line_size_bytes = H_size * BPP
    bursts_per_line = line_size_bytes / 64
    
    For each line k:
        consumer_line_start = k * bursts_per_line
        producer_line_start = k * bursts_per_line
        consumer[consumer_line_start].dep = producer[producer_line_start],req+delay
    """
```

**Critical Design**:
- Dependencies reference transaction **IDs**, not indices
- Multiple dependencies use `|` separator
- Events: `req` (request issued), `resp` (response received)

---

### 5. main.py

**Purpose**: Orchestration and workflow

#### AxiTrafficGenerator
```python
class AxiTrafficGenerator:
    def __init__(self):
        self.streams = {}           # {ip_name: {stream, job, group}}
        self.allocator = AddressAllocator()
        self.generator = StreamGenerator()
        self.dep_manager = DependencyManager()
    
    def run(ip_csv, output_path, dep_csv=None):
        # 1. Load configuration
        jobs = load_ip_config(ip_csv)
        dep_config = load_dependency_config(dep_csv) if dep_csv else None
        
        # 2. Generate streams
        generate_streams(jobs)
        
        # 3. Assign IDs (CRITICAL: before dependencies!)
        all_transactions = assign_transaction_ids()
        
        # 4. Apply dependencies
        apply_intra_dependencies()
        apply_inter_dependencies(dep_config)
        
        # 5. Export
        export_trace(all_transactions, output_path)
        generate_summary(output_path, summary_path)
```

**Execution Order Critical**:
```
Stream Generation
       ↓
   ID Assignment  ← Must happen BEFORE dependency application
       ↓
Intra-IP Dependencies (rate, outstanding)
       ↓
Inter-IP Dependencies (M2M, OTF)
       ↓
   Export Trace
```

**Why ID Assignment First?**  
Dependencies reference transaction IDs. Without IDs, `dep=0,resp+0` occurs.

---

### 6. gen_summary.py

**Purpose**: Trace analysis and visualization

#### generate_summary()
```python
def generate_summary(trace_file, output_file):
    # 1. Parse trace
    transactions = parse_trace(trace_file)
    
    # 2. Categorize dependencies
    ip_ranges = calculate_id_ranges()
    intra_deps = find_intra_ip_deps(transactions, ip_ranges)
    inter_deps = find_inter_ip_deps(transactions, ip_ranges)
    
    # 3. Build dependency graph
    graph = build_dependency_graph(inter_deps)
    
    # 4. Write report
    write_summary(output_file, graph, intra_deps, inter_deps)
```

**Graph Algorithm**:
```python
# Topological-style traversal
independent_nodes = all_producers - all_consumers

def print_chain(node):
    if node in adjacency:
        for consumer, sync_type in adjacency[node]:
            arrow = "=>" if sync_type == "M2M" else "->"
            output(f"{node} {arrow} {consumer}")
            print_chain(consumer)

for node in independent_nodes:
    print_chain(node)
```

---

## Dependency System

### Dependency String Format

**Single Dependency**:
```
dep=10,req+100
```
- Target ID: 10
- Event: `req` (request) or `resp` (response)
- Offset: +100 cycles

**Multiple Dependencies**:
```
dep=10,req+100|dep=5,resp+0
```
- Separated by `|`
- Evaluated as logical AND (all must be satisfied)

### Dependency Resolution

**Simulator Interpretation**:
```python
# For TX with dep=10,req+100|dep=5,resp+0
can_issue = (
    TX[10].request_issued + 100 cycles elapsed AND
    TX[5].response_received
)
```

### Dependency Examples

**Outstanding Limit (8)**:
```
TX 1: (no dep)
TX 2: (no dep)
...
TX 8: (no dep)
TX 9: dep=1,resp+0     # Wait for TX 1 response
TX 10: dep=2,resp+0    # Wait for TX 2 response
```

**Rate Limiting (rate=0.5)**:
```
TX 1: (no dep)
TX 2: dep=1,req+128    # 128 = (1/0.5 - 1) * 64
TX 3: dep=2,req+128
```

**M2M Sync**:
```
# GPU_WR last TX: id=117864
# DISP_RD first TX: 
dep=117864,resp+0      # Wait for GPU_WR frame completion
```

**OTF Sync** (line 0, assuming 30 bursts/line):
```
# CAM_FE line 0 starts at TX 1
# ISP_FE line 0 starts at TX 40501
ISP_FE TX 40501: dep=1,req+100     # 100 cycle delay
ISP_FE TX 40531: dep=31,req+100    # Next line
```

---

## Extension Points

### Adding New Color Formats

**Location**: `utils.py → MultimediaUtils.calculate_bpp()`

```python
def calculate_bpp(color_format, bit_width):
    format_map = {
        'Bayer': lambda bw: bw / 8,
        'YUV': lambda bw: 1.5 * bw / 8,
        'RGB': lambda bw: 3 * bw / 8,
        'RGBA': lambda bw: 4 * bw / 8,  # ADD NEW FORMAT
    }
```

### Custom Dependency Types

**Location**: `dependency.py → DependencyManager`

```python
def apply_custom_sync(producer_stream, consumer_stream, params):
    # Implement custom synchronization logic
    for i, tx in enumerate(consumer_stream.transactions):
        # Add custom dependency
        tx.add_dependency(target_id, event, offset)
```

### Transaction Metadata

**Location**: `domain_model.py → AxiTransaction`

```python
@dataclass
class AxiTransaction:
    ...
    metadata: Dict = field(default_factory=dict)  # ADD
```

### Alternative Export Formats

**Location**: `main.py → export_trace()`

```python
def export_json(transactions, output_path):
    import json
    data = [asdict(tx) for tx in transactions]
    with open(output_path, 'w') as f:
        json.dump(data, f, indent=2)
```

---

## Performance Considerations

### Memory Usage

- **Transaction Count**: ~154K for test scenario
- **Memory per TX**: ~200 bytes (Python object overhead)
- **Total**: ~30 MB for typical scenario
- **Scalability**: Linear O(N) with transaction count

### Processing Time

- **Parsing**: O(N) rows in CSV
- **Generation**: O(N) transactions
- **Dependency**: O(N × D) where D = avg dependencies per TX
- **Export**: O(N) transactions
- **Total**: ~1-2 seconds for 150K transactions

### Optimization Opportunities

1. **Batch Processing**: Group transactions by IP for cache locality
2. **Lazy Evaluation**: Generate transactions on-demand
3. **Parallel Generation**: Multi-threaded stream creation
4. **Binary Export**: Use pickle/msgpack for faster I/O

---

## Testing Strategy

### Unit Tests (Recommended)
- `test_bpp_calculation()`: Verify all color formats
- `test_address_alignment()`: Ensure 4KB alignment
- `test_burst_splitting()`: Edge cases (size < 64, size % 64 != 0)
- `test_dependency_ordering()`: ID assignment before dependency
- `test_rate_calculation()`: Verify delay formula

### Integration Tests
- `test_m2m_chain()`: Full chain dependency
- `test_otf_sync()`: Line-by-line verification
- `test_mixed_scenario()`: OTF + M2M combination

### Validation
- `check_deps.py`: Quick sanity check
- `gen_summary.py`: Comprehensive analysis

---

## Known Limitations

1. **Fixed Burst Size**: Always 64 bytes (AXI standard)
2. **Sequential IDs**: No support for transaction reordering
3. **Static Configuration**: No runtime parameter changes
4. **Single Address Space**: No multi-channel support
5. **CSV Comments**: Not supported (use separate scenario files)

---

## Future Enhancements

### Planned Features
- [ ] JSON configuration support
- [ ] Multi-channel address spaces
- [ ] Dynamic dependency insertion
- [ ] Performance metrics (bandwidth, latency)
- [ ] Graphviz visualization
- [ ] SimPy integration for cycle-accurate simulation

### Community Requests
- [ ] GUI configuration tool
- [ ] Real-time trace viewer
- [ ] Regression test suite
- [ ] Docker containerization

---

## Appendix

### Terminology

- **Burst**: Contiguous 64-byte AXI transfer
- **Stream**: Sequence of transactions for single IP
- **M2M**: Memory-to-Memory (frame-level sync)
- **OTF**: On-The-Fly (line-level sync)
- **Outstanding**: Max in-flight transactions
- **BPP**: Bytes Per Pixel
- **Intra-IP**: Within single IP
- **Inter-IP**: Between different IPs

### References

- AXI Protocol Specification (ARM IHI 0022)
- Python Dataclasses (PEP 557)
- CSV Format (RFC 4180)
