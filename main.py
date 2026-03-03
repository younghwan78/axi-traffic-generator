"""
AXI Traffic Generator - Main Orchestrator

Converts high-level CSV scenarios to low-level AXI transaction traces.
"""

import csv
import sys
from typing import List, Dict
from pathlib import Path

from domain_model import AxiTransaction
from utils import MultimediaUtils, AddressAllocator
from generator import StreamGenerator, Stream
from dependency import DependencyManager
from gen_summary import generate_summary
from gen_bw_chart import generate_bw_chart


class AxiTrafficGenerator:
    """
    Main orchestrator for AXI traffic generator.
    """
    
    def __init__(self):
        self.streams: Dict[str, Stream] = {}
        self.allocator = AddressAllocator()
        self.utils = MultimediaUtils()
        self.generator = StreamGenerator()
        self.dep_manager = DependencyManager()
    
    # Required IP config fields
    REQUIRED_IP_FIELDS = ['IP', 'GroupName', 'In/Out', 'H size', 'V size', 'Color Format', 'Bit Width']
    
    # Optional IP config fields with defaults
    OPTIONAL_IP_FIELDS = {
        'R/W Rate': '1.0',
        'Outstanding': '16',
        'Comp Mode': 'Disable',
        'Comp Ratio': '',
        'LLC Enable': 'Disable',
        'Line Delay': '0'
    }
    
    # Legacy: Optional dependency fields (for backward compatibility)
    LEGACY_DEP_FIELDS = {
        'Sync Type': 'None',
        'Sync Source': '',
        'Sync Delay': '0'
    }
    
    def load_ip_config(self, csv_path: str) -> List[Dict]:
        """
        Load and parse CSV file, extracting only required fields.
        
        Handles CSV files with additional columns gracefully.
        
        Args:
            csv_path: Path to CSV file
            
        Returns:
            List of job dictionaries with normalized field names
        """
        jobs = []
        
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            
            for row_num, row in enumerate(reader, start=2):  # Start at 2 (row 1 is header)
                # Normalize keys (strip whitespace)
                normalized_row = {k.strip(): v.strip() if v else '' for k, v in row.items()}
                
                # Validate required fields
                missing_fields = [field for field in self.REQUIRED_IP_FIELDS 
                                 if field not in normalized_row or not normalized_row[field]]
                
                if missing_fields:
                    print(f"Warning: Row {row_num} missing required fields: {missing_fields}")
                    continue
                
                # Extract only required and optional fields
                job = {}
                
                # Required fields
                for field in self.REQUIRED_IP_FIELDS:
                    job[field] = normalized_row[field]
                
                # Optional IP fields with defaults
                for field, default in self.OPTIONAL_IP_FIELDS.items():
                    job[field] = normalized_row.get(field, default)
                
                # Legacy: Check for inline dependency fields (backward compatibility)
                for field, default in self.LEGACY_DEP_FIELDS.items():
                    job[field] = normalized_row.get(field, default)
                
                jobs.append(job)
        
        return jobs
    
    def generate_streams(self, jobs: List[Dict]) -> None:
        """
        Generate transaction streams from CSV jobs.
        
        Args:
            jobs: List of job dictionaries from CSV
        """
        for job in jobs:
            ip_name = job['IP']
            group_name = job['GroupName']
            in_out = job['In/Out']
            h_size = int(job['H size'])
            v_size = int(job['V size'])
            color_format = job['Color Format']
            bit_width = int(job['Bit Width'])
            
            # Check compression settings
            comp_mode = job.get('Comp Mode', 'Disable').strip()
            comp_enabled = comp_mode.lower() == 'enable'
            
            # Apply alignment if compression enabled
            if comp_enabled:
                h_size = self.utils.align_width_for_compression(h_size, color_format)
                v_size = self.utils.align_height_for_compression(v_size, color_format)
            
            # Calculate sizes
            total_size = self.utils.calculate_total_size(h_size, v_size, color_format, bit_width)
            
            # Apply compression ratio if enabled
            if comp_enabled and job.get('Comp Ratio'):
                comp_ratio = float(job['Comp Ratio'])
                total_size = self.utils.apply_compression(total_size, comp_ratio)
            
            line_size = self.utils.calculate_line_size(h_size, color_format, bit_width)
            bpp = self.utils.calculate_bpp(color_format, bit_width)
            
            # Get LLC and line delay settings
            llc_enable = job.get('LLC Enable', 'Disable').strip().lower() == 'enable'
            line_delay = int(job.get('Line Delay', '0'))
            
            # Allocate memory address
            start_addr = self.allocator.allocate(total_size, ip_name)
            
            # Determine transaction type
            tx_type = "ReadNoSnoop" if in_out.lower() == "in" else "WriteNoSnoop"
            
            # Generate stream with LLC and line delay support
            stream = self.generator.generate_stream(
                port=ip_name,
                tx_type=tx_type,
                start_addr=start_addr,
                total_size=total_size,
                burst_size=64,
                line_size=line_size,
                h_size=h_size,
                bpp=bpp,
                llc_enable=llc_enable,
                line_delay=line_delay
            )
            
            # Store stream with job metadata (including GroupName for group-based dependencies)
            self.streams[ip_name] = {
                'stream': stream,
                'job': job,
                'group': group_name,
                'llc_enable': llc_enable,
                'line_delay': line_delay
            }
    
    def apply_intra_dependencies(self) -> None:
        """
        Apply intra-IP dependencies (rate limiting, outstanding).
        """
        for ip_name, data in self.streams.items():
            stream = data['stream']
            job = data['job']
            
            # Get rate and outstanding from CSV (with defaults)
            rate = float(job.get('R/W Rate', '1.0'))
            outstanding = int(job.get('Outstanding', '16'))
            
            # Apply rate limiting if needed
            self.dep_manager.apply_rate_limiting(stream, rate)
            
            # Apply outstanding limit
            self.dep_manager.apply_outstanding_limit(stream, outstanding)
    
    def load_dependency_config(self, csv_path: str) -> List[Dict]:
        """
        Load dependency configuration from separate CSV.
        
        Args:
            csv_path: Path to dependency CSV file
            
        Returns:
            List of dependency dictionaries
        """
        dependencies = []
        
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            
            for row_num, row in enumerate(reader, start=2):
                normalized_row = {k.strip(): v.strip() if v else '' for k, v in row.items()}
                
                # Support both old (DMA-based) and new (Group-based) formats
                # New format: 'Consumer Group', 'Producer Group'
                # Old format: 'Consumer', 'Producer'
                if 'Consumer Group' in normalized_row and 'Producer Group' in normalized_row:
                    # New group-based format
                    required = ['Consumer Group', 'Producer Group', 'Sync Type']
                    missing = [f for f in required if f not in normalized_row or not normalized_row[f]]
                    
                    if missing:
                        print(f"Warning: Dependency row {row_num} missing: {missing}")
                        continue
                    
                    dep = {
                        'Consumer': normalized_row['Consumer Group'],
                        'Producer': normalized_row['Producer Group'],
                        'Sync Type': normalized_row['Sync Type'],
                        'Delay': int(normalized_row.get('Delay', '0')),
                        'IsGroupBased': True
                    }
                else:
                    # Legacy DMA-based format
                    required = ['Consumer', 'Producer', 'Sync Type']
                    missing = [f for f in required if f not in normalized_row or not normalized_row[f]]
                    
                    if missing:
                        print(f"Warning: Dependency row {row_num} missing: {missing}")
                        continue
                    
                    dep = {
                        'Consumer': normalized_row['Consumer'],
                        'Producer': normalized_row['Producer'],
                        'Sync Type': normalized_row['Sync Type'],
                        'Delay': int(normalized_row.get('Delay', '0')),
                        'IsGroupBased': False
                    }
                
                dependencies.append(dep)
        
        return dependencies
    
    def apply_inter_dependencies(self, dep_config: List[Dict] = None) -> None:
        """
        Apply inter-IP dependencies (M2M, OTF sync).
        
        Args:
            dep_config: List of dependency configurations from separate file (optional)
        """
        # If separate dependency config provided, use it
        if dep_config:
            for dep in dep_config:
                consumer = dep['Consumer']
                producer = dep['Producer']
                sync_type = dep['Sync Type']
                delay = dep['Delay']
                is_group_based = dep.get('IsGroupBased', False)
                
                if is_group_based:
                    # Group-based dependencies
                    # Find all streams belonging to consumer and producer groups
                    consumer_streams = [data['stream'] for ip, data in self.streams.items() 
                                      if data['group'] == consumer]
                    producer_streams = [data['stream'] for ip, data in self.streams.items() 
                                      if data['group'] == producer]
                    
                    if not consumer_streams:
                        print(f"Warning: No DMAs found for consumer group '{consumer}'")
                        continue
                    
                    if not producer_streams:
                        print(f"Warning: No DMAs found for producer group '{producer}'")
                        continue
                    
                    if sync_type.upper() == 'M2M':
                        self.dep_manager.apply_m2m_group_sync(producer_streams, consumer_streams, delay)
                    elif sync_type.upper() == 'OTF':
                        self.dep_manager.apply_otf_group_sync(producer_streams, consumer_streams)
                else:
                    # Legacy DMA-based dependencies
                    if consumer not in self.streams:
                        print(f"Warning: Consumer '{consumer}' not found")
                        continue
                    
                    if producer not in self.streams:
                        print(f"Warning: Producer '{producer}' not found")
                        continue
                    
                    consumer_stream = self.streams[consumer]['stream']
                    producer_stream = self.streams[producer]['stream']
                    
                    if sync_type.upper() == 'M2M':
                        self.dep_manager.apply_m2m_sync(producer_stream, consumer_stream, delay)
                    elif sync_type.upper() == 'OTF':
                        self.dep_manager.apply_otf_sync(producer_stream, consumer_stream, delay)
        
        # Otherwise, use inline dependency fields (backward compatibility)
        else:
            for ip_name, data in self.streams.items():
                stream = data['stream']
                job = data['job']
                
                sync_type = job.get('Sync Type', 'None').strip()
                sync_source = job.get('Sync Source', '').strip()
                sync_delay = int(job.get('Sync Delay', '0'))
                
                # Skip if no sync or sync source not specified
                if sync_type.lower() == 'none' or not sync_source:
                    continue
                
                # Get producer stream
                if sync_source not in self.streams:
                    print(f"Warning: Sync source '{sync_source}' not found for '{ip_name}'")
                    continue
                
                producer_stream = self.streams[sync_source]['stream']
                consumer_stream = stream
                
                # Apply appropriate sync
                if sync_type.upper() == 'M2M':
                    self.dep_manager.apply_m2m_sync(producer_stream, consumer_stream, sync_delay)
                elif sync_type.upper() == 'OTF':
                    self.dep_manager.apply_otf_sync(producer_stream, consumer_stream, sync_delay)
    
    def assign_transaction_ids(self) -> List[AxiTransaction]:
        """
        Collect all transactions and assign sequential IDs.
        
        Returns:
            List of all transactions with assigned IDs
        """
        all_transactions = []
        
        # Collect all transactions from all streams
        for ip_name, data in self.streams.items():
            stream = data['stream']
            all_transactions.extend(stream.transactions)
        
        # Assign sequential IDs
        for idx, tx in enumerate(all_transactions, start=1):
            tx.id = idx
        
        return all_transactions
    
    def export_trace(self, transactions: List[AxiTransaction], output_path: str) -> None:
        """
        Export transactions to trace file.
        
        Args:
            transactions: List of transactions to export
            output_path: Output file path
        """
        with open(output_path, 'w', encoding='utf-8') as f:
            for tx in transactions:
                f.write(str(tx) + '\n')
        
        print(f"Generated {len(transactions)} transactions")
        print(f"Trace file written to: {output_path}")
    
    def run(self, ip_csv: str, output_path: str = "trace.txt", dep_csv: str = None) -> None:
        """
        Main execution flow.
        
        Args:
            ip_csv: IP configuration CSV file path
            output_path: Output trace file path
            dep_csv: Optional separate dependency configuration CSV file path
        """
        print(f"Loading IP config: {ip_csv}")
        jobs = self.load_ip_config(ip_csv)
        print(f"Found {len(jobs)} DMA jobs")
        
        # Load dependency config if provided
        dep_config = None
        if dep_csv:
            print(f"Loading dependency config: {dep_csv}")
            dep_config = self.load_dependency_config(dep_csv)
            print(f"Found {len(dep_config)} inter-IP dependencies")
        
        print("\nGenerating transaction streams...")
        self.generate_streams(jobs)
        
        print("Assigning transaction IDs...")
        all_transactions = self.assign_transaction_ids()
        
        print("Applying intra-IP dependencies (rate, outstanding)...")
        self.apply_intra_dependencies()
        
        print("Applying inter-IP dependencies (M2M, OTF sync)...")
        self.apply_inter_dependencies(dep_config)
        
        print(f"\nExporting to {output_path}...")
        self.export_trace(all_transactions, output_path)
        
        # Generate dependency summary
        summary_path = output_path.replace('.txt', '_summary.txt')
        print(f"\nGenerating dependency summary...")
        generate_summary(output_path, summary_path)
        
        print("\n✓ AXI Traffic Generation Complete!")
        print(f"  - Trace: {output_path}")
        print(f"  - Summary: {summary_path}")


def run_yaml_mode(ip_spec_path: str, scenario_path: str,
                   output_path: str = "trace.txt") -> None:
    """
    YAML-based pipeline: ConfigParser → Scheduler → Trace output.

    Args:
        ip_spec_path: Path to DMA_IP_Spec.yaml
        scenario_path: Path to Scenario.yaml
        output_path: Output trace file path
    """
    from config_parser import ConfigParser
    from smmu_model import MockSMMU
    from scheduler import build_scheduler

    print(f"Loading IP spec: {ip_spec_path}")
    specs = ConfigParser.load_ip_spec(ip_spec_path)
    print(f"  Found {len(specs)} IP definitions")

    print(f"Loading scenario: {scenario_path}")
    scenario = ConfigParser.load_scenario(scenario_path)
    print(f"  Scenario: {scenario.name}")
    print(f"  Tasks: {len(scenario.tasks)}")

    # Sanity check
    print("\nRunning sanity check...")
    errors = ConfigParser.sanity_check(specs, scenario)
    if errors:
        print("✗ Sanity check FAILED:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)
    print("✓ Sanity check passed")

    # SMMU
    smmu = None
    if scenario.memory_policy.smmu_enable:
        print(f"\nSMMU enabled (CMA ratio: {scenario.memory_policy.cma_ratio})")
        smmu = MockSMMU(
            cma_ratio=scenario.memory_policy.cma_ratio,
            page_size=scenario.memory_policy.page_size,
            enabled=True,
        )

    # Build and run scheduler
    print("\nBuilding scheduler...")
    scheduler = build_scheduler(specs, scenario, smmu)
    print(f"  Registered {len(scheduler.agents)} agents")

    print("Running simulation...")
    all_transactions = scheduler.run()
    print(f"  Generated {len(all_transactions)} transactions in {scheduler.tick + 1} ticks")

    # Assign sequential IDs
    for idx, tx in enumerate(all_transactions, start=1):
        tx.id = idx

    # Export
    print(f"\nExporting to {output_path}...")
    with open(output_path, 'w', encoding='utf-8') as f:
        for tx in all_transactions:
            f.write(str(tx) + '\n')

    print(f"\n✓ AXI Traffic Generation Complete! ({len(all_transactions)} transactions)")
    print(f"  Trace: {output_path}")

    # Build IP → clock frequency map for bandwidth calculation
    ip_clock_map = {}
    for task in scenario.tasks:
        ip_clock_map[task.ip_name] = task.clock

    # Build IP → config dict for DMA Configuration Summary
    ip_configs = {}
    for task in scenario.tasks:
        ip_spec = specs[task.ip_name]
        ip_configs[task.ip_name] = {
            'dir': ip_spec.core.dir,
            'bus_byte': ip_spec.core.bus_byte,
            'ppc': ip_spec.core.ppc,
            'bpp': ip_spec.core.bpp,
            'plane': ip_spec.core.plane,
            'clock_mhz': task.clock,
            'access_type': task.access_type,
            'behavior': task.behavior.type,
            'req_mo': ip_spec.ctrl.req_mo,
            'format': task.format,
            'resolution': task.resolution,
            'ip_group': ip_spec.ip_group,
            'sbwc_ratio': task.sbwc_ratio,
        }

    # Generate summary report
    summary_path = output_path.replace('.txt', '_summary.txt')
    print(f"\nGenerating summary report...")
    generate_summary(output_path, summary_path,
                     clock_map=ip_clock_map, ip_configs=ip_configs)
    print(f"  Summary: {summary_path}")

    # Generate BW chart
    chart_path = output_path.replace('.txt', '_bw.html')
    generate_bw_chart(output_path, chart_path,
                      ip_configs=ip_configs, clock_map=ip_clock_map)
    print(f"  BW Chart: {chart_path}")


def main():
    """Entry point supporting both YAML and legacy CSV modes."""

    # YAML mode: python main.py --yaml DMA_IP_Spec.yaml Scenario.yaml [output.txt]
    if len(sys.argv) >= 2 and sys.argv[1] == "--yaml":
        if len(sys.argv) < 4:
            print("Usage (YAML mode):")
            print("  python main.py --yaml <DMA_IP_Spec.yaml> <Scenario.yaml> [output.txt]")
            sys.exit(1)

        ip_spec_path = sys.argv[2]
        scenario_path = sys.argv[3]
        output_path = sys.argv[4] if len(sys.argv) > 4 else "trace.txt"

        for p in [ip_spec_path, scenario_path]:
            if not Path(p).exists():
                print(f"Error: File not found: {p}")
                sys.exit(1)

        run_yaml_mode(ip_spec_path, scenario_path, output_path)
        return

    # Legacy CSV mode
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python main.py --yaml <DMA_IP_Spec.yaml> <Scenario.yaml> [output.txt]")
        print("  python main.py <ip_config.csv> [output.txt] [dependency.csv]")
        print("\nExamples:")
        print("  python main.py --yaml DMA_IP_Spec.yaml Scenario_4K.yaml trace.txt")
        print("  python main.py ip_config.csv trace.txt dependency_config.csv")
        sys.exit(1)

    ip_csv = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else "trace.txt"
    dep_csv = sys.argv[3] if len(sys.argv) > 3 else None

    if not Path(ip_csv).exists():
        print(f"Error: IP config file not found: {ip_csv}")
        sys.exit(1)

    if dep_csv and not Path(dep_csv).exists():
        print(f"Error: Dependency config file not found: {dep_csv}")
        sys.exit(1)

    generator = AxiTrafficGenerator()
    generator.run(ip_csv, output_path, dep_csv)


if __name__ == "__main__":
    main()

