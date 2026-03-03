"""
BW Chart Generator

Generates an interactive HTML report with bandwidth-over-time plots using
Plotly.js (CDN). Two chart sections:
  1. IP-level BW (Read / Write separated, DMAs summed per IP group)
  2. Per-DMA BW breakdown
"""

from collections import defaultdict
from typing import Dict, List, Optional


def generate_bw_chart(trace_path: str, output_html: str,
                      ip_configs: Optional[Dict] = None,
                      clock_map: Optional[Dict[str, int]] = None,
                      bin_size: int = 1000) -> str:
    """
    Parse trace file and generate an HTML BW chart.

    Args:
        trace_path: Path to trace.txt
        output_html: Output HTML path
        ip_configs: Dict of DMA configs (with ip_group)
        clock_map: Dict {dma_name: clock_mhz}
        bin_size: Number of ticks per time bin

    Returns:
        Output HTML path
    """
    # ── Parse trace ──────────────────────────────────────────────────
    dma_bins: Dict[str, Dict[int, int]] = defaultdict(lambda: defaultdict(int))
    dma_rw: Dict[str, str] = {}
    max_tick = 0

    with open(trace_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            fields = {}
            for token in line.split():
                if '=' in token:
                    k, v = token.split('=', 1)
                    fields[k] = v

            tick = int(fields.get('tick', 0))
            port = fields.get('port', '')
            nbytes = int(fields.get('bytes', 0))
            tx_type = fields.get('type', '')

            bin_idx = tick // bin_size
            dma_bins[port][bin_idx] += nbytes
            max_tick = max(max_tick, tick)

            if port not in dma_rw:
                dma_rw[port] = 'W' if 'Write' in tx_type else 'R'

    max_bin = max_tick // bin_size + 1

    # ── Build IP group mapping ───────────────────────────────────────
    ip_group_map = {}
    if ip_configs:
        for dma, cfg in ip_configs.items():
            ip_group_map[dma] = cfg.get('ip_group', dma)
    else:
        for dma in dma_bins:
            ip_group_map[dma] = dma

    # ── Aggregate IP-level R/W ───────────────────────────────────────
    # Key: (ip_group, rw) → bin → bytes
    ip_rw_bins: Dict[str, Dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for dma, bins in dma_bins.items():
        rw = dma_rw.get(dma, 'R')
        group = ip_group_map.get(dma, dma)
        series_key = f"{group} [{rw}]"
        for b, nbytes in bins.items():
            ip_rw_bins[series_key][b] += nbytes

    # ── Prepare chart data ───────────────────────────────────────────
    x_ticks = [i * bin_size for i in range(max_bin)]

    def series_to_list(bins_dict, max_b):
        """Convert bin dict to list, B/tick values."""
        return [bins_dict.get(i, 0) / bin_size for i in range(max_b)]

    # Colors palette
    colors = [
        '#3498db', '#e74c3c', '#2ecc71', '#f39c12', '#9b59b6',
        '#1abc9c', '#e67e22', '#34495e', '#16a085', '#c0392b',
        '#2980b9', '#8e44ad', '#27ae60', '#d35400', '#7f8c8d',
    ]

    # ── IP-level traces ──────────────────────────────────────────────
    ip_traces = []
    for idx, series_name in enumerate(sorted(ip_rw_bins.keys())):
        y_vals = series_to_list(ip_rw_bins[series_name], max_bin)
        color = colors[idx % len(colors)]
        ip_traces.append({
            'name': series_name,
            'x': x_ticks,
            'y': y_vals,
            'color': color,
        })

    # ── DMA-level traces ─────────────────────────────────────────────
    dma_traces = []
    for idx, dma in enumerate(sorted(dma_bins.keys())):
        rw = dma_rw.get(dma, '?')
        y_vals = series_to_list(dma_bins[dma], max_bin)
        color = colors[idx % len(colors)]
        dma_traces.append({
            'name': f"{dma} [{rw}]",
            'x': x_ticks,
            'y': y_vals,
            'color': color,
        })

    # ── Generate HTML ────────────────────────────────────────────────
    def traces_to_js(traces, var_name):
        lines = [f"var {var_name} = ["]
        for t in traces:
            # Downsample for performance if too many points
            x_data = t['x']
            y_data = t['y']
            lines.append("  {")
            lines.append(f"    name: '{t['name']}',")
            lines.append(f"    x: {x_data},")
            lines.append(f"    y: {y_data},")
            lines.append(f"    type: 'scatter',")
            lines.append(f"    mode: 'lines',")
            lines.append(f"    line: {{color: '{t['color']}', width: 1.5}},")
            lines.append(f"    fill: 'tozeroy',")
            lines.append(f"    fillcolor: '{t['color']}22',")
            lines.append("  },")
        lines.append("];")
        return "\n".join(lines)

    ip_js = traces_to_js(ip_traces, "ipTraces")
    dma_js = traces_to_js(dma_traces, "dmaTraces")

    # Scenario info
    total_tx = sum(len(list(bins.keys())) for bins in dma_bins.values())
    total_tx_str = f"{sum(sum(b.values()) for b in dma_bins.values()) / 1024 / 1024:.2f} MB"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AXI Traffic BW Report</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    background: #0f0f1a;
    color: #e0e0e0;
    padding: 24px;
  }}
  .header {{
    text-align: center;
    padding: 32px 0 24px;
  }}
  .header h1 {{
    font-size: 28px;
    font-weight: 700;
    background: linear-gradient(135deg, #667eea, #764ba2);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin-bottom: 8px;
  }}
  .header .subtitle {{
    color: #888;
    font-size: 14px;
  }}
  .chart-card {{
    background: #1a1a2e;
    border-radius: 12px;
    padding: 24px;
    margin-bottom: 24px;
    border: 1px solid #2a2a3e;
    box-shadow: 0 4px 20px rgba(0,0,0,0.3);
  }}
  .chart-card h2 {{
    font-size: 18px;
    font-weight: 600;
    color: #c0c0d0;
    margin-bottom: 16px;
    padding-left: 12px;
    border-left: 3px solid #667eea;
  }}
  .chart-container {{
    width: 100%;
    height: 400px;
  }}
  .stats-bar {{
    display: flex;
    gap: 24px;
    justify-content: center;
    margin-bottom: 24px;
    flex-wrap: wrap;
  }}
  .stat-item {{
    background: #1a1a2e;
    border-radius: 8px;
    padding: 16px 24px;
    text-align: center;
    border: 1px solid #2a2a3e;
    min-width: 160px;
  }}
  .stat-item .value {{
    font-size: 22px;
    font-weight: 700;
    color: #667eea;
  }}
  .stat-item .label {{
    font-size: 12px;
    color: #888;
    margin-top: 4px;
  }}
</style>
</head>
<body>
<div class="header">
  <h1>AXI Traffic Bandwidth Report</h1>
  <div class="subtitle">Time bin = {bin_size:,} ticks &middot; 1 Frame</div>
</div>

<div class="stats-bar">
  <div class="stat-item">
    <div class="value">{max_tick:,}</div>
    <div class="label">Total Ticks</div>
  </div>
  <div class="stat-item">
    <div class="value">{total_tx_str}</div>
    <div class="label">Total Data</div>
  </div>
  <div class="stat-item">
    <div class="value">{len(dma_bins)}</div>
    <div class="label">DMA Channels</div>
  </div>
  <div class="stat-item">
    <div class="value">{len(ip_rw_bins)}</div>
    <div class="label">IP×R/W Series</div>
  </div>
</div>

<div class="chart-card">
  <h2>IP-Level Bandwidth (Read / Write)</h2>
  <div id="chart-ip" class="chart-container"></div>
</div>

<div class="chart-card">
  <h2>Per-DMA Bandwidth Breakdown</h2>
  <div id="chart-dma" class="chart-container"></div>
</div>

<script>
var plotLayout = {{
  paper_bgcolor: '#1a1a2e',
  plot_bgcolor: '#1a1a2e',
  font: {{ color: '#c0c0d0', family: 'Segoe UI, system-ui, sans-serif', size: 12 }},
  xaxis: {{
    title: 'Tick',
    gridcolor: '#2a2a3e',
    zerolinecolor: '#2a2a3e',
    tickformat: ',d',
  }},
  yaxis: {{
    title: 'B/tick',
    gridcolor: '#2a2a3e',
    zerolinecolor: '#2a2a3e',
  }},
  legend: {{
    orientation: 'h',
    y: -0.15,
    x: 0.5,
    xanchor: 'center',
    bgcolor: 'transparent',
  }},
  margin: {{ l: 60, r: 20, t: 20, b: 60 }},
  hovermode: 'x unified',
}};

var plotConfig = {{
  responsive: true,
  displaylogo: false,
  modeBarButtonsToRemove: ['lasso2d', 'select2d'],
}};

{ip_js}

{dma_js}

Plotly.newPlot('chart-ip', ipTraces, plotLayout, plotConfig);
Plotly.newPlot('chart-dma', dmaTraces, plotLayout, plotConfig);
</script>
</body>
</html>"""

    with open(output_html, 'w', encoding='utf-8') as f:
        f.write(html)

    return output_html
