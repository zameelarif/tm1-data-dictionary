"""Build an interactive data-flow graph from cube, chain, and datasource lineage.

The whole-model extraction produces :class:`CubeLineageRow`s (process reads/writes a cube),
:class:`ChainRow`s (process triggers another process), and :class:`DatasourceRow`s (a source
feeds a process). This module turns those into a **graph** - nodes (processes, cubes,
datasources) and edges (writes / reads / triggers / feeds) - and renders it as a single,
self-contained **offline HTML** file the developer can open in any browser to *see* and
click through the model's data flow.

Two clean halves (separation of concerns):

- :func:`build_graph` - pure data. Consumes the rollup rows and returns a :class:`GraphData`
  of deduplicated nodes and edges. Fully unit-tested, no TM1, no I/O.
- :func:`render_html` - wraps a :class:`GraphData` into an HTML document using the
  vis-network graph library. The graph JSON is embedded directly in the page, so the file is
  self-contained. By default the vis-network library is pulled from a CDN (cached after first
  load); pass ``vis_js`` with the contents of ``vis-network.min.js`` to inline it for a fully
  offline file.

Edge direction follows the flow:
    datasource --feeds--> process   (solid)      [data enters the process from the source]
    process    --writes-> cube      (solid)
    cube       --reads--> process   (dashed)     [data flows from cube into the process]
    process    --triggers-> process (solid, arrow)
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass, field

from tm1_data_dictionary.parser.chain_rollup import ChainRow
from tm1_data_dictionary.parser.datasource_rollup import DatasourceRow
from tm1_data_dictionary.parser.references import Role
from tm1_data_dictionary.parser.rollup import CubeLineageRow

# Node kinds.
NODE_PROCESS = "process"
NODE_CUBE = "cube"
NODE_DATASOURCE = "datasource"

# Edge kinds.
EDGE_WRITE = "writes"
EDGE_READ = "reads"
EDGE_TRIGGER = "triggers"
EDGE_FEEDS = "feeds"


@dataclass(frozen=True)
class GraphNode:
    """A node in the data-flow graph."""

    node_id: str  # unique id, e.g. "process:CUB.Sales" or "cube:GL"
    label: str  # display label (the bare name)
    kind: str  # NODE_PROCESS | NODE_CUBE | NODE_DATASOURCE


@dataclass(frozen=True)
class GraphEdge:
    """A directed edge in the data-flow graph."""

    source: str  # source node_id
    target: str  # target node_id
    kind: str  # EDGE_WRITE | EDGE_READ | EDGE_TRIGGER | EDGE_FEEDS
    count: int  # how many underlying references (edge weight / label)


@dataclass
class GraphData:
    """A deduplicated set of nodes and edges."""

    nodes: dict[str, GraphNode] = field(default_factory=dict)
    edges: dict[tuple[str, str, str], GraphEdge] = field(default_factory=dict)

    def _add_node(self, node_id: str, label: str, kind: str) -> None:
        if node_id not in self.nodes:
            self.nodes[node_id] = GraphNode(node_id=node_id, label=label, kind=kind)

    def _add_edge(self, source: str, target: str, kind: str, count: int) -> None:
        key = (source, target, kind)
        existing = self.edges.get(key)
        if existing is None:
            self.edges[key] = GraphEdge(source=source, target=target, kind=kind, count=count)
        else:
            # Merge duplicate edges by summing counts.
            self.edges[key] = GraphEdge(
                source=source, target=target, kind=kind, count=existing.count + count
            )

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        return len(self.edges)

    def process_ids(self) -> set[str]:
        return {n.node_id for n in self.nodes.values() if n.kind == NODE_PROCESS}

    def cube_ids(self) -> set[str]:
        return {n.node_id for n in self.nodes.values() if n.kind == NODE_CUBE}

    def datasource_ids(self) -> set[str]:
        return {n.node_id for n in self.nodes.values() if n.kind == NODE_DATASOURCE}


def _process_id(name: str) -> str:
    return f"{NODE_PROCESS}:{name}"


def _cube_id(name: str) -> str:
    return f"{NODE_CUBE}:{name}"


def _datasource_id(name: str) -> str:
    return f"{NODE_DATASOURCE}:{name}"


def build_graph(
    cube_rows: list[CubeLineageRow],
    chain_rows: list[ChainRow],
    datasource_rows: list[DatasourceRow] | None = None,
) -> GraphData:
    """Build a :class:`GraphData` from cube-, chain-, and datasource-lineage rows.

    Processes, cubes, and datasources become nodes; reads/writes/triggers/feeds become
    edges. Duplicate edges (same source, target, and kind) are merged and counts summed.
    Datasource rows are optional so existing callers keep working.
    """
    graph = GraphData()

    for cube_row in cube_rows:
        p_id = _process_id(cube_row.process)
        c_id = _cube_id(cube_row.cube)
        graph._add_node(p_id, cube_row.process, NODE_PROCESS)
        graph._add_node(c_id, cube_row.cube, NODE_CUBE)
        if cube_row.role is Role.CUBE_WRITE:
            graph._add_edge(p_id, c_id, EDGE_WRITE, cube_row.count)
        elif cube_row.role is Role.CUBE_READ:
            graph._add_edge(c_id, p_id, EDGE_READ, cube_row.count)

    for chain_row in chain_rows:
        caller_id = _process_id(chain_row.caller)
        callee_id = _process_id(chain_row.callee)
        graph._add_node(caller_id, chain_row.caller, NODE_PROCESS)
        graph._add_node(callee_id, chain_row.callee, NODE_PROCESS)
        graph._add_edge(caller_id, callee_id, EDGE_TRIGGER, chain_row.count)

    for ds_row in datasource_rows or []:
        p_id = _process_id(ds_row.process)
        d_id = _datasource_id(ds_row.source_name)
        graph._add_node(p_id, ds_row.process, NODE_PROCESS)
        graph._add_node(d_id, ds_row.source_name, NODE_DATASOURCE)
        # Data flows from the source INTO the process.
        graph._add_edge(d_id, p_id, EDGE_FEEDS, 1)

    return graph


# --------------------------------------------------------------------------- #
# HTML rendering
# --------------------------------------------------------------------------- #

# Visual styling per node/edge kind (fed to vis-network).
_NODE_STYLE: dict[str, dict[str, object]] = {
    NODE_PROCESS: {"shape": "ellipse", "color": "#4C78A8"},
    NODE_CUBE: {"shape": "box", "color": "#F58518"},
    NODE_DATASOURCE: {"shape": "database", "color": "#72B7B2"},
}
_EDGE_STYLE: dict[str, dict[str, object]] = {
    EDGE_WRITE: {"color": "#54A24B", "dashes": False},
    EDGE_READ: {"color": "#B279A2", "dashes": True},
    EDGE_TRIGGER: {"color": "#E45756", "dashes": False},
    EDGE_FEEDS: {"color": "#72B7B2", "dashes": False},
}

_CDN_VIS = "https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"


def graph_to_dict(graph: GraphData) -> dict:
    """Return a plain dict of nodes/edges (for embedding as JSON or testing)."""
    nodes: list[dict[str, object]] = []
    for n in graph.nodes.values():
        style = _NODE_STYLE.get(n.kind, {})
        nodes.append(
            {
                "id": n.node_id,
                "label": n.label,
                "group": n.kind,
                "shape": style.get("shape", "ellipse"),
                "color": style.get("color", "#888888"),
            }
        )
    edges: list[dict[str, object]] = []
    for e in graph.edges.values():
        style = _EDGE_STYLE.get(e.kind, {})
        edges.append(
            {
                "from": e.source,
                "to": e.target,
                "label": str(e.count) if e.count > 1 else "",
                "title": f"{e.kind} ({e.count})",
                "color": style.get("color", "#888888"),
                "dashes": style.get("dashes", False),
                "arrows": "to",
            }
        )
    return {"nodes": nodes, "edges": edges}


def render_html(graph: GraphData, *, title: str = "TM1 Data Flow", vis_js: str = "") -> str:
    """Render the graph as a self-contained interactive HTML page.

    Args:
        graph: the graph to render.
        title: page title / heading.
        vis_js: optional contents of ``vis-network.min.js`` to inline for a fully offline
            file. If empty, the page loads vis-network from a CDN (cached after first load).
    """
    data_json = json.dumps(graph_to_dict(graph))
    safe_title = html.escape(title)
    n_proc = len(graph.process_ids())
    n_cube = len(graph.cube_ids())
    n_src = len(graph.datasource_ids())

    vis_script = f"<script>{vis_js}</script>" if vis_js else f'<script src="{_CDN_VIS}"></script>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{safe_title}</title>
{vis_script}
<style>
  body {{ font-family: Segoe UI, Arial, sans-serif; margin: 0; }}
  #header {{ padding: 10px 16px; background: #2b3a55; color: #fff; }}
  #header h1 {{ margin: 0; font-size: 18px; }}
  #header .meta {{ font-size: 12px; opacity: 0.85; }}
  #legend {{ padding: 8px 16px; font-size: 12px; background: #f3f4f6; }}
  #legend span {{ margin-right: 16px; }}
  .swatch {{ display: inline-block; width: 12px; height: 12px; border-radius: 2px;
             vertical-align: middle; margin-right: 4px; }}
  #controls {{ padding: 8px 16px; }}
  #picker {{ padding: 4px 8px; max-width: 340px; }}
  #search {{ padding: 4px 8px; width: 220px; }}
  #graph {{ width: 100%; height: calc(100vh - 150px); border-top: 1px solid #ddd; }}
</style>
</head>
<body>
<div id="header">
  <h1>{safe_title}</h1>
  <div class="meta">{n_proc} processes &middot; {n_cube} cubes &middot; {n_src} datasources
       &middot; {graph.edge_count} relationships</div>
</div>
<div id="legend">
  <span><span class="swatch" style="background:#4C78A8"></span>Process</span>
  <span><span class="swatch" style="background:#F58518"></span>Cube</span>
  <span><span class="swatch" style="background:#72B7B2"></span>Datasource</span>
  <span><span class="swatch" style="background:#54A24B"></span>writes</span>
  <span><span class="swatch" style="background:#B279A2"></span>reads</span>
  <span><span class="swatch" style="background:#E45756"></span>triggers</span>
  <span><span class="swatch" style="background:#72B7B2"></span>feeds</span>
</div>
<div id="controls">
  <select id="picker">
    <option value="">— jump to a node —</option>
  </select>
  <input id="search" type="text" placeholder="…or type to search, then Enter">
  <button id="fit">Fit</button>
  <button id="reset">Reset highlight</button>
</div>
<div id="graph"></div>
<script>
  const graphData = {data_json};
  const nodes = new vis.DataSet(graphData.nodes);
  const edges = new vis.DataSet(graphData.edges);
  const container = document.getElementById("graph");
  const network = new vis.Network(container, {{ nodes, edges }}, {{
    physics: {{ stabilization: true, barnesHut: {{ gravitationalConstant: -8000,
                springLength: 140 }} }},
    interaction: {{ hover: true, tooltipDelay: 120 }},
    nodes: {{ font: {{ size: 12 }} }},
    edges: {{ smooth: {{ type: "dynamic" }}, font: {{ size: 10, align: "middle" }} }}
  }});

  function highlightNode(sel) {{
    const connected = new Set([sel]);
    edges.forEach(function (e) {{
      if (e.from === sel) connected.add(e.to);
      if (e.to === sel) connected.add(e.from);
    }});
    nodes.forEach(function (n) {{
      nodes.update({{ id: n.id, opacity: connected.has(n.id) ? 1.0 : 0.15 }});
    }});
  }}

  function focusNode(id) {{
    network.focus(id, {{ scale: 1.2, animation: true }});
    network.selectNodes([id]);
    highlightNode(id);
  }}

  network.on("click", function (params) {{
    if (params.nodes.length) highlightNode(params.nodes[0]);
  }});

  const picker = document.getElementById("picker");
  const sorted = graphData.nodes.slice().sort(function (a, b) {{
    if (a.group !== b.group) return a.group < b.group ? -1 : 1;
    return a.label.toLowerCase() < b.label.toLowerCase() ? -1 : 1;
  }});
  sorted.forEach(function (n) {{
    const opt = document.createElement("option");
    opt.value = n.id;
    const tag = n.group === "cube" ? "[cube] " :
                (n.group === "datasource" ? "[data] " : "[proc] ");
    opt.text = tag + n.label;
    picker.appendChild(opt);
  }});
  picker.addEventListener("change", function () {{
    if (picker.value) focusNode(picker.value);
  }});

  document.getElementById("reset").onclick = function () {{
    nodes.forEach(function (n) {{ nodes.update({{ id: n.id, opacity: 1.0 }}); }});
  }};
  document.getElementById("fit").onclick = function () {{ network.fit(); }};

  const search = document.getElementById("search");
  search.addEventListener("keydown", function (ev) {{
    if (ev.key !== "Enter") return;
    const q = search.value.trim().toLowerCase();
    if (!q) return;
    const match = graphData.nodes.find(function (n) {{
      return n.label.toLowerCase().indexOf(q) !== -1;
    }});
    if (match) {{
      picker.value = match.id;
      focusNode(match.id);
    }}
  }});
</script>
</body>
</html>
"""
