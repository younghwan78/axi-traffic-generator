"""
Dependency & Traffic Summary Generator

Analyzes trace file and generates comprehensive report including:
  - IP transaction overview with tick statistics (YAML mode)
  - Address range and 64B boundary compliance
  - Bandwidth distribution per IP
  - Behavior pattern analysis (burst vs flush)
  - Dependency analysis (legacy mode)
"""
import re
from collections import defaultdict
from typing import Dict, List, Optional


def generate_summary(trace_file: str, output_file: str = 'dependency_summary.txt',
                     clock_map: Optional[Dict[str, int]] = None,
                     ip_configs: Optional[Dict[str, Dict]] = None) -> None:
    """
    Generate comprehensive summary from trace file.
    Auto-detects YAML mode (tick=...) vs legacy mode (dep=...).

    Args:
        trace_file: Path to trace file
        output_file: Path to output summary file
        clock_map: Optional dict mapping IP name to clock frequency (MHz)
        ip_configs: Optional dict mapping IP name to config dict with keys:
                    dir, bus_byte, ppc, bpp, plane, clock_mhz, access_type,
                    behavior, req_mo, format, resolution
    """
    print(f"Analyzing {trace_file}...")
    transactions = []
    ip_txs = defaultdict(list)
    is_yaml_mode = False

    with open(trace_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            parts = {}
            for token in line.split():
                if '=' in token and not token.startswith('dep='):
                    key, val = token.split('=', 1)
                    parts[key] = val

            tx_id = int(parts.get('id', 0))
            port = parts.get('port', 'UNKNOWN')
            tick = int(parts['tick']) if 'tick' in parts else None
            address = int(parts.get('address', '0x0'), 16)
            nbytes = int(parts.get('bytes', 0))
            tx_type = parts.get('type', '')
            burst = parts.get('burst', 'seq')

            # Detect deps (legacy)
            deps = re.findall(r'dep=(\d+),(req|resp)\+(\d+)', line)

            if tick is not None:
                is_yaml_mode = True

            tx = {
                'id': tx_id,
                'port': port,
                'tick': tick,
                'address': address,
                'bytes': nbytes,
                'type': tx_type,
                'burst': burst,
                'deps': [(int(t), e, int(o)) for t, e, o in deps],
            }
            transactions.append(tx)
            ip_txs[port].append(tx)

    # Get ID ranges
    ip_ranges = {}
    for port, txs in ip_txs.items():
        ids = [tx['id'] for tx in txs]
        ip_ranges[port] = (min(ids), max(ids))

    print(f"Loaded {len(transactions)} transactions from {len(ip_txs)} IPs")
    print(f"Mode: {'YAML (tick-based)' if is_yaml_mode else 'Legacy (dep-based)'}")

    with open(output_file, 'w', encoding='utf-8') as out:
        out.write("=" * 80 + "\n")
        out.write("AXI TRAFFIC SUMMARY REPORT\n")
        out.write("=" * 80 + "\n\n")

        # ====================================================================
        #  Section 1: IP Transaction Overview
        # ====================================================================
        out.write("[*] IP Transaction Overview:\n")
        out.write("-" * 80 + "\n")
        total_bytes = 0

        # Pre-calculate per-port bytes
        port_bytes_map = {}
        for port, txs in ip_txs.items():
            port_bytes_map[port] = sum(tx['bytes'] for tx in txs)
            total_bytes += port_bytes_map[port]

        # Group by IP if ip_configs available
        if ip_configs and is_yaml_mode:
            # Build IP group → DMA list mapping
            ip_groups = defaultdict(list)
            for port in sorted(ip_txs.keys()):
                cfg = ip_configs.get(port, {})
                group = cfg.get('ip_group', port)
                ip_groups[group].append(port)

            for group_name in sorted(ip_groups.keys()):
                ports = ip_groups[group_name]
                out.write(f"  [{group_name}]\n")
                group_bytes = 0
                group_count = 0
                for port in ports:
                    txs = ip_txs[port]
                    count = len(txs)
                    pb = port_bytes_map[port]
                    group_bytes += pb
                    group_count += count
                    rw = txs[0]['type'] if txs else ''
                    dir_label = 'WR' if 'Write' in rw else 'RD'
                    ticks = [tx['tick'] for tx in txs if tx['tick'] is not None]
                    tick_min, tick_max = min(ticks), max(ticks)
                    out.write(
                        f"    {port:20s} [{dir_label}] : {count:7d} tx  "
                        f"({pb / 1024:,.1f} KB)  "
                        f"tick {tick_min:,} ~ {tick_max:,}\n"
                    )
                out.write(
                    f"    {'── Subtotal':20s}       : {group_count:7d} tx  "
                    f"({group_bytes / 1024 / 1024:,.2f} MB)\n\n"
                )
        else:
            for port in sorted(ip_txs.keys()):
                txs = ip_txs[port]
                count = len(txs)
                id_range = ip_ranges[port]
                pb = port_bytes_map[port]
                rw = txs[0]['type'] if txs else ''
                dir_label = 'WR' if 'Write' in rw else 'RD'

                if is_yaml_mode and txs[0]['tick'] is not None:
                    ticks = [tx['tick'] for tx in txs if tx['tick'] is not None]
                    tick_min, tick_max = min(ticks), max(ticks)
                    out.write(
                        f"  {port:20s} [{dir_label}] : {count:7d} tx  "
                        f"({pb / 1024:,.1f} KB)  "
                        f"tick {tick_min:,} ~ {tick_max:,}\n"
                    )
                else:
                    out.write(
                        f"  {port:20s} [{dir_label}] : {count:7d} tx  "
                        f"(ID {id_range[0]:6d} - {id_range[1]:6d})  "
                        f"({pb / 1024:,.1f} KB)\n"
                    )

        out.write(f"  {'TOTAL':20s}       : {len(transactions):7d} tx  "
                  f"({total_bytes / 1024 / 1024:,.2f} MB)\n")

        # ====================================================================
        #  Section 2: DMA Configuration Summary (YAML mode only)
        # ====================================================================
        if ip_configs and is_yaml_mode:
            out.write("\n" + "=" * 80 + "\n")
            out.write("DMA CONFIGURATION SUMMARY\n")
            out.write("=" * 80 + "\n\n")

            # Header
            hdr = (f"  {'DMA Name':20s}  {'IP':12s}  {'Dir':3s}  {'Bus':4s}  {'Burst':6s}"
                   f"  {'PPC':3s}  {'BPP':4s}  {'Pln':3s}  {'Clock':7s}"
                   f"  {'Access':8s}  {'Behavior':18s}  {'MO':3s}"
                   f"  {'Format':22s}  {'Resolution':12s}")
            out.write(hdr + "\n")
            out.write("  " + "-" * (len(hdr) - 2) + "\n")

            # Group by IP
            ip_groups = defaultdict(list)
            for port in sorted(ip_configs.keys()):
                cfg = ip_configs[port]
                ip_groups[cfg.get('ip_group', port)].append(port)

            for group_name in sorted(ip_groups.keys()):
                ports = ip_groups[group_name]
                for port in ports:
                    cfg = ip_configs[port]
                    d = cfg['dir']
                    dir_label = 'WR' if d == 'W' else 'RD'
                    bus = cfg['bus_byte']
                    burst_len = 64 // bus
                    ppc = cfg['ppc']
                    bpp = cfg['bpp']
                    plane = cfg['plane']
                    clock = cfg['clock_mhz']
                    access = cfg['access_type'].replace('-order', '')
                    behavior = cfg['behavior'].replace('_', ' ')
                    mo = cfg['req_mo']
                    fmt = cfg['format']
                    res = cfg['resolution']

                    out.write(
                        f"  {port:20s}  {group_name:12s}  {dir_label:3s}  {bus:3d}B"
                        f"  {burst_len:2d}beat"
                        f"  {ppc:3d}  {bpp:4d}  {plane:3d}"
                        f"  {clock:4d}MHz"
                        f"  {access:8s}  {behavior:18s}  {mo:3d}"
                        f"  {fmt:22s}  {res[0]}x{res[1]}\n"
                    )

        # ====================================================================
        #  Section 3: Tick-based Analysis (YAML mode only)
        # ====================================================================
        if is_yaml_mode:
            out.write("\n" + "=" * 80 + "\n")
            out.write("TICK-BASED TRAFFIC ANALYSIS\n")
            out.write("=" * 80 + "\n")

            all_ticks = [tx['tick'] for tx in transactions if tx['tick'] is not None]
            if all_ticks:
                sim_duration = max(all_ticks) - min(all_ticks) + 1
                out.write(f"\n  Simulation Duration : {sim_duration:,} ticks "
                          f"({min(all_ticks):,} ~ {max(all_ticks):,})\n")
                out.write(f"  Total Transactions  : {len(transactions):,}\n")
                if sim_duration > 0:
                    out.write(f"  Avg TX / tick       : {len(transactions) / sim_duration:.2f}\n")
                    avg_bw_btick = total_bytes / sim_duration
                    out.write(f"  Avg Bandwidth       : "
                              f"{avg_bw_btick:.1f} B/tick\n")

            # ----- Per-DMA bandwidth breakdown -----
            out.write(f"\n[*] Per-DMA Bandwidth Breakdown:\n")
            out.write("-" * 80 + "\n")

            if ip_configs:
                # Grouped by IP
                bw_ip_groups = defaultdict(list)
                for port in sorted(ip_txs.keys()):
                    cfg = ip_configs.get(port, {})
                    bw_ip_groups[cfg.get('ip_group', port)].append(port)

                for group_name in sorted(bw_ip_groups.keys()):
                    ports = bw_ip_groups[group_name]
                    out.write(f"  [{group_name}]\n")
                    group_bytes_total = 0
                    for port in ports:
                        txs = ip_txs[port]
                        port_bytes = port_bytes_map[port]
                        group_bytes_total += port_bytes
                        ticks = [tx['tick'] for tx in txs if tx['tick'] is not None]
                        if ticks:
                            duration = max(ticks) - min(ticks) + 1
                            bw = port_bytes / duration if duration > 0 else 0
                            pct = (port_bytes / total_bytes * 100) if total_bytes > 0 else 0
                            line = f"    {port:20s} : {bw:8.1f} B/tick  ({pct:5.1f}%)  [{port_bytes / 1024 / 1024:.2f} MB]"
                            if clock_map and port in clock_map:
                                mhz = clock_map[port]
                                bw_gbs = bw * mhz / 1000.0
                                line += f"  {bw_gbs:6.2f} GB/s @{mhz}MHz"
                            out.write(line + "\n")
                    # IP subtotal
                    ip_pct = (group_bytes_total / total_bytes * 100) if total_bytes > 0 else 0
                    out.write(
                        f"    {'── Subtotal':20s} :                     "
                        f"({ip_pct:5.1f}%)  [{group_bytes_total / 1024 / 1024:.2f} MB]\n\n"
                    )
            else:
                for port in sorted(ip_txs.keys()):
                    txs = ip_txs[port]
                    port_bytes = port_bytes_map[port]
                    ticks = [tx['tick'] for tx in txs if tx['tick'] is not None]
                    if ticks:
                        duration = max(ticks) - min(ticks) + 1
                        bw = port_bytes / duration if duration > 0 else 0
                        pct = (port_bytes / total_bytes * 100) if total_bytes > 0 else 0
                        line = f"  {port:20s} : {bw:8.1f} B/tick  ({pct:5.1f}%)  [{port_bytes / 1024 / 1024:.2f} MB]"
                        if clock_map and port in clock_map:
                            mhz = clock_map[port]
                            bw_gbs = bw * mhz / 1000.0
                            line += f"  {bw_gbs:6.2f} GB/s @{mhz}MHz"
                        out.write(line + "\n")

            # ----- Address range analysis -----
            out.write(f"\n[*] Address Ranges:\n")
            out.write("-" * 80 + "\n")
            for port in sorted(ip_txs.keys()):
                txs = ip_txs[port]
                addrs = [tx['address'] for tx in txs]
                addr_min, addr_max = min(addrs), max(addrs)
                span = addr_max - addr_min + txs[-1]['bytes']
                out.write(f"  {port:20s} : 0x{addr_min:08x} ~ 0x{addr_max:08x}  "
                          f"(span: {span / 1024:.1f} KB)\n")

            # ----- 64B boundary compliance -----
            out.write(f"\n[*] 64B Boundary Compliance:\n")
            out.write("-" * 80 + "\n")
            violations = 0
            for tx in transactions:
                addr = tx['address']
                size = tx['bytes']
                boundary = ((addr >> 6) + 1) << 6
                if addr + size > boundary:
                    violations += 1
            if violations == 0:
                out.write(f"  ✓ All {len(transactions):,} transactions comply "
                          f"with 64B boundary alignment\n")
            else:
                out.write(f"  ✗ {violations:,} violations found out of "
                          f"{len(transactions):,} transactions\n")

            # ----- Behavior pattern analysis -----
            out.write(f"\n[*] Behavior Pattern Analysis:\n")
            out.write("-" * 80 + "\n")
            for port in sorted(ip_txs.keys()):
                txs = ip_txs[port]
                ticks = [tx['tick'] for tx in txs if tx['tick'] is not None]
                if not ticks:
                    continue

                # Detect burst patterns: group consecutive transactions by tick
                tick_groups = defaultdict(int)
                for tx in txs:
                    if tx['tick'] is not None:
                        tick_groups[tx['tick']] += 1

                max_burst = max(tick_groups.values())
                avg_burst = len(txs) / len(tick_groups) if tick_groups else 0
                active_ticks = len(tick_groups)
                total_ticks = max(ticks) - min(ticks) + 1 if ticks else 1
                duty = (active_ticks / total_ticks * 100) if total_ticks > 0 else 0

                if max_burst > 2 and avg_burst > 1.5:
                    pattern = "Flush-burst"
                elif avg_burst <= 1.1:
                    pattern = "Steady-stream"
                else:
                    pattern = "Eager-MO"

                out.write(
                    f"  {port:20s} : {pattern:15s}  "
                    f"avg_burst={avg_burst:.1f}  max_burst={max_burst}  "
                    f"duty={duty:.1f}%\n"
                )

        # ====================================================================
        #  Section 3: Dependency Analysis (legacy or hybrid)
        # ====================================================================
        has_deps = any(tx['deps'] for tx in transactions)

        if has_deps:
            # Dependency Graph
            out.write("\n" + "=" * 80 + "\n")
            out.write("DEPENDENCY ANALYSIS\n")
            out.write("=" * 80 + "\n")

            out.write("\n[*] Dependency Graph:\n")
            out.write("-" * 80 + "\n")

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
                            'offset': offset,
                        })

            graph_edges = {}
            for (producer, consumer), deps in inter_deps.items():
                graph_edges[(producer, consumer)] = "M2M" if len(deps) == 1 else "OTF"

            if graph_edges:
                all_consumers = set(c for _, c in graph_edges.keys())
                all_producers = set(p for p, _ in graph_edges.keys())
                all_nodes = all_consumers | all_producers
                independent_nodes = all_nodes - all_consumers

                adjacency = defaultdict(list)
                for (producer, consumer), sync_type in graph_edges.items():
                    adjacency[producer].append((consumer, sync_type))

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

                for node in sorted(independent_nodes):
                    print_chain(node)
                for node in sorted(all_nodes):
                    if node not in visited:
                        print_chain(node)
            else:
                out.write("  No inter-IP dependencies\n")

            # Intra-IP Dependencies
            out.write("\n" + "=" * 80 + "\n")
            out.write("INTRA-IP DEPENDENCIES (Internal Control)\n")
            out.write("=" * 80 + "\n")

            for port in sorted(ip_txs.keys()):
                out.write(f"\n[>] {port}\n")
                out.write("-" * 80 + "\n")

                outstanding_deps = []
                rate_deps = []
                for tx in ip_txs[port]:
                    for target_id, event, offset in tx['deps']:
                        if ip_ranges[port][0] <= target_id <= ip_ranges[port][1]:
                            if event == 'resp' and offset == 0:
                                outstanding_deps.append((tx['id'], target_id))
                            elif event == 'req' and offset > 0:
                                rate_deps.append((tx['id'], target_id, offset))

                if outstanding_deps:
                    interval = outstanding_deps[0][0] - outstanding_deps[0][1]
                    out.write(f"  Outstanding Limit:\n    Active (interval = {interval})\n")
                    out.write(f"    Examples:\n")
                    for tx_id, target_id in outstanding_deps[:3]:
                        out.write(f"      TX {tx_id:6d} -> depends on TX {target_id:6d} response\n")
                    if len(outstanding_deps) > 3:
                        out.write(f"      ... ({len(outstanding_deps)} total)\n")
                else:
                    out.write(f"  Outstanding Limit: None\n")

                if rate_deps:
                    delay = rate_deps[0][2]
                    out.write(f"  Rate Limiting:\n    Active (delay = {delay} cycles)\n")
                    out.write(f"    Examples:\n")
                    for tx_id, target_id, d in rate_deps[:3]:
                        out.write(f"      TX {tx_id:6d} -> depends on TX {target_id:6d} request + {d} cycles\n")
                    if len(rate_deps) > 3:
                        out.write(f"      ... ({len(rate_deps)} total)\n")
                else:
                    out.write(f"  Rate Limiting: None\n")

            # Inter-IP Details
            if inter_deps:
                out.write("\n" + "=" * 80 + "\n")
                out.write("INTER-IP DEPENDENCIES (IP Synchronization)\n")
                out.write("=" * 80 + "\n")

                for (producer, consumer), deps in sorted(inter_deps.items()):
                    out.write(f"\n[>] {producer} -> {consumer}\n")
                    out.write("-" * 80 + "\n")
                    sample = deps[0]
                    if len(deps) == 1:
                        out.write(f"  Sync Type: M2M (Memory-to-Memory / Frame Sync)\n")
                        out.write(f"  Consumer's first TX depends on Producer's last TX:\n")
                        out.write(f"    TX {sample['consumer_id']:6d} -> depends on "
                                  f"TX {sample['producer_id']:6d} {sample['event']}+{sample['offset']}\n")
                    else:
                        out.write(f"  Sync Type: OTF (On-The-Fly / Line Sync)\n")
                        out.write(f"  Line-by-line synchronization (delay = {sample['offset']} cycles)\n")
                        out.write(f"  Examples:\n")
                        for dep in deps[:5]:
                            out.write(f"    Consumer TX {dep['consumer_id']:6d} -> "
                                      f"Producer TX {dep['producer_id']:6d} {dep['event']}+{dep['offset']}\n")
                        if len(deps) > 5:
                            out.write(f"    ... ({len(deps)} total line syncs)\n")

        out.write("\n" + "=" * 80 + "\n")
        out.write("END OF REPORT\n")
        out.write("=" * 80 + "\n")

    print(f"Summary written to {output_file}")


if __name__ == "__main__":
    import sys
    trace = sys.argv[1] if len(sys.argv) > 1 else 'trace.txt'
    output = sys.argv[2] if len(sys.argv) > 2 else trace.replace('.txt', '_summary.txt')
    generate_summary(trace, output)
