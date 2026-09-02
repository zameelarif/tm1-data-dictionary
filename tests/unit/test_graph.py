"""Unit tests for building the data-flow graph and rendering it to HTML."""

from __future__ import annotations

from tm1_data_dictionary.graph import (
    EDGE_READ,
    EDGE_TRIGGER,
    EDGE_WRITE,
    NODE_CUBE,
    NODE_PROCESS,
    build_graph,
    graph_to_dict,
    render_html,
)
from tm1_data_dictionary.parser.chain_rollup import ChainRow
from tm1_data_dictionary.parser.references import Role
from tm1_data_dictionary.parser.rollup import CubeLineageRow


def _cube(process: str, cube: str, role: Role, count: int = 1) -> CubeLineageRow:
    return CubeLineageRow(
        process=process, cube=cube, role=role, count=count, first_block="Data", first_line=1
    )


def _chain(caller: str, callee: str, count: int = 1) -> ChainRow:
    return ChainRow(caller=caller, callee=callee, count=count, first_block="Epilog", first_line=1)


# --------------------------------------------------------------------------- #
# build_graph
# --------------------------------------------------------------------------- #


def test_empty_graph() -> None:
    g = build_graph([], [])
    assert g.node_count == 0
    assert g.edge_count == 0


def test_write_creates_process_and_cube_nodes() -> None:
    g = build_graph([_cube("P", "GL", Role.CUBE_WRITE, 5)], [])
    assert g.node_count == 2
    assert g.process_ids() == {"process:P"}
    assert g.cube_ids() == {"cube:GL"}
    edge = next(iter(g.edges.values()))
    assert edge.source == "process:P"  # process -> cube for a write
    assert edge.target == "cube:GL"
    assert edge.kind == EDGE_WRITE
    assert edge.count == 5


def test_read_edge_direction_is_cube_to_process() -> None:
    g = build_graph([_cube("P", "FX", Role.CUBE_READ, 3)], [])
    edge = next(iter(g.edges.values()))
    assert edge.source == "cube:FX"  # cube -> process for a read
    assert edge.target == "process:P"
    assert edge.kind == EDGE_READ


def test_chain_creates_process_to_process_edge() -> None:
    g = build_graph([], [_chain("A", "B", 2)])
    assert g.process_ids() == {"process:A", "process:B"}
    edge = next(iter(g.edges.values()))
    assert edge.source == "process:A"
    assert edge.target == "process:B"
    assert edge.kind == EDGE_TRIGGER
    assert edge.count == 2


def test_shared_cube_node_deduplicated() -> None:
    # Two processes touch the same cube -> one cube node, two edges.
    g = build_graph([_cube("P1", "GL", Role.CUBE_WRITE), _cube("P2", "GL", Role.CUBE_READ)], [])
    assert g.cube_ids() == {"cube:GL"}
    assert g.node_count == 3  # P1, P2, GL
    assert g.edge_count == 2


def test_duplicate_edges_merge_and_sum_counts() -> None:
    # Same process writing the same cube twice (shouldn't happen post-rollup, but be safe).
    g = build_graph(
        [_cube("P", "GL", Role.CUBE_WRITE, 3), _cube("P", "GL", Role.CUBE_WRITE, 4)], []
    )
    assert g.edge_count == 1
    assert next(iter(g.edges.values())).count == 7


def test_read_and_write_same_pair_are_separate_edges() -> None:
    # A process both reads and writes the same cube -> two distinct edges (different kind).
    g = build_graph([_cube("P", "GL", Role.CUBE_WRITE), _cube("P", "GL", Role.CUBE_READ)], [])
    assert g.edge_count == 2
    kinds = {e.kind for e in g.edges.values()}
    assert kinds == {EDGE_WRITE, EDGE_READ}


def test_process_appears_as_both_caller_and_target() -> None:
    g = build_graph([], [_chain("A", "B"), _chain("B", "C")])
    assert g.process_ids() == {"process:A", "process:B", "process:C"}
    assert g.edge_count == 2


def test_realistic_mixed_graph() -> None:
    cube_rows = [
        _cube("CUB.Sales", "Food_Weekly_Sales", Role.CUBE_WRITE, 140),
        _cube("CUB.Sales", "DW_Mapping", Role.CUBE_READ, 122),
    ]
    chain_rows = [_chain("CUB.Sales", f"Step.{i}") for i in range(3)]
    g = build_graph(cube_rows, chain_rows)
    # Nodes: CUB.Sales, 2 cubes, 3 step processes = 6
    assert g.node_count == 6
    # Edges: 1 write + 1 read + 3 triggers = 5
    assert g.edge_count == 5


# --------------------------------------------------------------------------- #
# graph_to_dict / render_html
# --------------------------------------------------------------------------- #


def test_graph_to_dict_shape() -> None:
    g = build_graph([_cube("P", "GL", Role.CUBE_WRITE, 2)], [])
    d = graph_to_dict(g)
    assert set(d) == {"nodes", "edges"}
    assert len(d["nodes"]) == 2
    assert len(d["edges"]) == 1
    edge = d["edges"][0]
    assert edge["from"] == "process:P"
    assert edge["to"] == "cube:GL"
    assert edge["label"] == "2"  # count > 1 shown


def test_graph_to_dict_count_one_has_no_label() -> None:
    g = build_graph([_cube("P", "GL", Role.CUBE_WRITE, 1)], [])
    d = graph_to_dict(g)
    assert d["edges"][0]["label"] == ""  # count == 1 -> no label clutter


def test_node_kinds_have_distinct_shapes() -> None:
    g = build_graph([_cube("P", "GL", Role.CUBE_WRITE)], [])
    d = graph_to_dict(g)
    shapes = {n["group"]: n["shape"] for n in d["nodes"]}
    assert shapes[NODE_PROCESS] != shapes[NODE_CUBE]


def test_render_html_is_selfcontained() -> None:
    g = build_graph(
        [_cube("CUB.Sales", "GL", Role.CUBE_WRITE, 5)], [_chain("CUB.Sales", "Rebuild")]
    )
    out = render_html(g, title="My Model")
    assert "<!DOCTYPE html>" in out
    assert "My Model" in out
    assert "CUB.Sales" in out  # data embedded
    assert "vis.Network" in out  # uses vis-network
    # By default the CDN script is referenced.
    assert "vis-network.min.js" in out


def test_render_html_inlines_vis_when_provided() -> None:
    g = build_graph([_cube("P", "GL", Role.CUBE_WRITE)], [])
    out = render_html(g, vis_js="/* FAKE VIS LIB */")
    assert "/* FAKE VIS LIB */" in out
    assert "unpkg.com" not in out  # no CDN when inlined


def test_render_html_escapes_title() -> None:
    g = build_graph([], [])
    out = render_html(g, title="A & B <script>")
    assert "A &amp; B &lt;script&gt;" in out


def test_datasource_node_and_feeds_edge() -> None:
    from tm1_data_dictionary.graph import EDGE_FEEDS, NODE_DATASOURCE
    from tm1_data_dictionary.parser.datasource_rollup import DatasourceRow

    ds = [DatasourceRow(process="Cube.GL.Load", source_type="File", source_name="gl.csv")]
    g = build_graph([_cube("Cube.GL.Load", "GL", Role.CUBE_WRITE)], [], ds)
    # process, cube, datasource = 3 nodes
    assert g.node_count == 3
    assert g.datasource_ids() == {"datasource:gl.csv"}
    # Edges: datasource feeds process, process writes cube = 2
    assert g.edge_count == 2
    feeds = [e for e in g.edges.values() if e.kind == EDGE_FEEDS][0]
    assert feeds.source == "datasource:gl.csv"  # source -> process
    assert feeds.target == "process:Cube.GL.Load"
    d = graph_to_dict(g)
    ds_node = [n for n in d["nodes"] if n["group"] == NODE_DATASOURCE][0]
    assert ds_node["label"] == "gl.csv"


def test_full_flow_source_to_cube() -> None:
    from tm1_data_dictionary.parser.datasource_rollup import DatasourceRow

    ds = [DatasourceRow(process="Loader", source_type="File", source_name="in.csv")]
    cube = [_cube("Loader", "SalesCube", Role.CUBE_WRITE, 10)]
    g = build_graph(cube, [], ds)
    # in.csv --feeds--> Loader --writes--> SalesCube
    assert g.datasource_ids() == {"datasource:in.csv"}
    assert g.process_ids() == {"process:Loader"}
    assert g.cube_ids() == {"cube:SalesCube"}
    assert g.edge_count == 2


def test_datasource_optional_backwards_compatible() -> None:
    # Old 2-arg call still works (no datasource rows).
    g = build_graph([_cube("P", "GL", Role.CUBE_WRITE)], [])
    assert g.datasource_ids() == set()


def test_render_html_has_node_picker() -> None:
    g = build_graph([_cube("CUB.Sales", "GL", Role.CUBE_WRITE)], [_chain("CUB.Sales", "Rebuild")])
    out = render_html(g)
    # The dropdown element and its population logic are present.
    assert 'id="picker"' in out
    assert "picker.appendChild" in out
    # Nodes are still embedded so the dropdown can be built client-side.
    assert "CUB.Sales" in out
    assert "GL" in out
