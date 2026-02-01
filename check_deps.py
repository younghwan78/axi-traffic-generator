"""
Generate dependency summary for specific trace file
"""
import sys
import re
from collections import defaultdict

if len(sys.argv) < 2:
    trace_file = 'trace.txt'
else:
    trace_file = sys.argv[1]

# Parse trace
print(f"Parsing {trace_file}...")
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

print(f"Loaded {len(transactions)} transactions from {len(ip_txs)} IPs\n")

# Analyze inter-IP dependencies
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

print("="*60)
print("INTER-IP DEPENDENCY SUMMARY")
print("="*60)

for port in sorted(ip_txs.keys()):
    id_range = ip_ranges[port]
    print(f"{port:12s} : ID {id_range[0]:6d} - {id_range[1]:6d}")

print("\n" + "="*60)
print("DEPENDENCY CHAIN")
print("="*60)

if inter_deps:
    for (producer, consumer), deps in sorted(inter_deps.items()):
        print(f"\n{producer} -> {consumer}")
        sample = deps[0]
        
        if len(deps) == 1:
            print(f"  Type: M2M (Frame Sync)")
            print(f"  TX {sample['consumer_id']:6d} -> TX {sample['producer_id']:6d} {sample['event']}+{sample['offset']}")
        else:
            print(f"  Type: OTF (Line Sync, {len(deps)} lines)")
            print(f"  First: TX {deps[0]['consumer_id']:6d} -> TX {deps[0]['producer_id']:6d}")
else:
    print("\nNo inter-IP dependencies found.")

print("\n" + "="*60)
