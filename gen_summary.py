"""
Dependency Summary Generator

Analyzes trace file and generates comprehensive dependency report.
"""
import re
from collections import defaultdict
from typing import Dict, List


def generate_summary(trace_file: str, output_file: str = 'dependency_summary.txt') -> None:
    """
    Generate dependency summary from trace file.
    
    Args:
        trace_file: Path to trace file
        output_file: Path to output summary file
    """
    # Parse trace
    print(f"Analyzing {trace_file}...")
    transactions = []
    ip_txs = defaultdict(list)
    
    with open(trace_file, 'r') as f:
        for line in f:
            tx_id = int(re.search(r'id=(\d+)', line).group(1))
            port = re.search(r'port=(\w+)', line).group(1)
            deps = re.findall(r'dep=(\d+),(req|resp)\+(\d+)', line)
            
            tx = {'id': tx_id, 'port': port, 'deps': [(int(t), e, int(o)) for t, e, o in deps]}
            transactions.append(tx)
            ip_txs[port].append(tx)
    
    # Get ID ranges
    ip_ranges = {}
    for port, txs in ip_txs.items():
        ids = [tx['id'] for tx in txs]
        ip_ranges[port] = (min(ids), max(ids))
    
    print(f"Loaded {len(transactions)} transactions from {len(ip_txs)} IPs")
    
    # Write summary
    with open(output_file, 'w') as out:
        out.write("="*80 + "\n")
        out.write("DEPENDENCY SUMMARY REPORT\n")
        out.write("="*80 + "\n\n")
        
        # IP Overview
        out.write("[*] IP Transaction Overview:\n")
        out.write("-"*80 + "\n")
        for port in sorted(ip_txs.keys()):
            count = len(ip_txs[port])
            id_range = ip_ranges[port]
            out.write(f"  {port:12s} : {count:6d} transactions (ID {id_range[0]:6d} - {id_range[1]:6d})\n")
        
        # Build dependency graph
        out.write("\n[*] Dependency Graph:\n")
        out.write("-"*80 + "\n")
        
        # Analyze inter-IP deps for graph
        inter_deps = defaultdict(list)
        for tx in transactions:
            port = tx['port']
            for target_id, event, offset in tx['deps']:
                target_port = None
                for p, (min_id, max_id) in ip_ranges.items():
                    if min_id <= target_id <= max_id:
                        target_port = p
                        break
                
                if target_port and target_port != port:
                    inter_deps[(target_port, port)].append({
                        'consumer_id': tx['id'],
                        'producer_id': target_id,
                        'event': event,
                        'offset': offset
                    })
        
        # Create graph representation
        graph_edges = {}
        for (producer, consumer), deps in inter_deps.items():
            if len(deps) == 1:
                # M2M
                graph_edges[(producer, consumer)] = "M2M"
            else:
                # OTF
                graph_edges[(producer, consumer)] = "OTF"
        
        # Print graph
        if graph_edges:
            # Find independent nodes (no incoming edges)
            all_consumers = set(c for p, c in graph_edges.keys())
            all_producers = set(p for p, c in graph_edges.keys())
            all_nodes = all_consumers | all_producers
            independent_nodes = all_nodes - all_consumers
            
            # Build adjacency list
            adjacency = defaultdict(list)
            for (producer, consumer), sync_type in graph_edges.items():
                adjacency[producer].append((consumer, sync_type))
            
            # Print in topological-ish order
            visited = set()
            
            def print_chain(node, indent="  "):
                if node in visited:
                    return
                visited.add(node)
                
                if node in adjacency:
                    for consumer, sync_type in sorted(adjacency[node]):
                        arrow = "=>" if sync_type == "M2M" else "->"
                        out.write(f"{indent}{node} {arrow} {consumer} ({sync_type})\n")
                        print_chain(consumer, indent + "  ")
            
            # Start from independent nodes
            for node in sorted(independent_nodes):
                print_chain(node)
            
            # Print any remaining (in case of cycles)
            for node in sorted(all_nodes):
                if node not in visited:
                    print_chain(node)
        else:
            out.write("  No inter-IP dependencies\n")
        
        # Intra-IP Dependencies
        out.write("\n" + "="*80 + "\n")
        out.write("INTRA-IP DEPENDENCIES (Internal Control)\n")
        out.write("="*80 + "\n")
        
        for port in sorted(ip_txs.keys()):
            out.write(f"\n[>] {port}\n")
            out.write("-"*80 + "\n")
            
            # Analyze intra-IP deps
            outstanding_deps = []
            rate_deps = []
            
            for tx in ip_txs[port]:
                for target_id, event, offset in tx['deps']:
                    if ip_ranges[port][0] <= target_id <= ip_ranges[port][1]:
                        if event == 'resp' and offset == 0:
                            outstanding_deps.append((tx['id'], target_id))
                        elif event == 'req' and offset > 0:
                            rate_deps.append((tx['id'], target_id, offset))
            
            # Outstanding
            if outstanding_deps:
                interval = outstanding_deps[0][0] - outstanding_deps[0][1]
                out.write(f"  Outstanding Limit:\n")
                out.write(f"    Active (interval = {interval})\n")
                out.write(f"    Examples:\n")
                for i, (tx_id, target_id) in enumerate(outstanding_deps[:3]):
                    out.write(f"      TX {tx_id:6d} -> depends on TX {target_id:6d} response\n")
                if len(outstanding_deps) > 3:
                    out.write(f"      ... ({len(outstanding_deps)} total)\n")
            else:
                out.write(f"  Outstanding Limit: None\n")
            
            # Rate Limiting
            if rate_deps:
                delay = rate_deps[0][2]
                out.write(f"  Rate Limiting:\n")
                out.write(f"    Active (delay = {delay} cycles)\n")
                out.write(f"    Examples:\n")
                for i, (tx_id, target_id, d) in enumerate(rate_deps[:3]):
                    out.write(f"      TX {tx_id:6d} -> depends on TX {target_id:6d} request + {d} cycles\n")
                if len(rate_deps) > 3:
                    out.write(f"      ... ({len(rate_deps)} total)\n")
            else:
                out.write(f"  Rate Limiting: None\n")
        
        # Inter-IP Dependencies
        out.write("\n" + "="*80 + "\n")
        out.write("INTER-IP DEPENDENCIES (IP Synchronization)\n")
        out.write("="*80 + "\n")
        
        if inter_deps:
            for (producer, consumer), deps in sorted(inter_deps.items()):
                out.write(f"\n[>] {producer} -> {consumer}\n")
                out.write("-"*80 + "\n")
                
                sample = deps[0]
                
                if len(deps) == 1:
                    out.write(f"  Sync Type: M2M (Memory-to-Memory / Frame Sync)\n")
                    out.write(f"  Consumer's first TX depends on Producer's last TX:\n")
                    out.write(f"    TX {sample['consumer_id']:6d} -> depends on TX {sample['producer_id']:6d} {sample['event']}+{sample['offset']}\n")
                else:
                    out.write(f"  Sync Type: OTF (On-The-Fly / Line Sync)\n")
                    out.write(f"  Line-by-line synchronization (delay = {sample['offset']} cycles)\n")
                    out.write(f"  Examples:\n")
                    for i, dep in enumerate(deps[:5]):
                        out.write(f"    Consumer TX {dep['consumer_id']:6d} -> Producer TX {dep['producer_id']:6d} {dep['event']}+{dep['offset']}\n")
                    if len(deps) > 5:
                        out.write(f"    ... ({len(deps)} total line syncs)\n")
        else:
            out.write("\n  No inter-IP dependencies found.\n")
        
        out.write("\n" + "="*80 + "\n")
        out.write("END OF REPORT\n")
        out.write("="*80 + "\n")
    
    print(f"Summary written to {output_file}")


if __name__ == "__main__":
    generate_summary('trace.txt', 'dependency_summary.txt')
