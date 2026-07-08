"""Rebuild graph.json with new MAX_EXTERNAL = 500 and fixed type classification."""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitor.state_manager import load_state
from monitor.graph_builder import build_graph, write_graph

state = load_state()
print("Building graph...")
graph = build_graph(state)
print(f"Graph built: {len(graph['nodes'])} nodes, {len(graph['links'])} links")

# Show external nodes
ext_nodes = [n for n in graph['nodes'] if n.get('type') == 'external']
print(f"External nodes: {len(ext_nodes)}")
for n in ext_nodes:
    print(f"  {n['id'][:90]}")

# Show any internal nodes that were previously misclassified as external
int_nodes = [n for n in graph['nodes'] if n.get('type') == 'page' and n['id'] in ('/page/2', '/page/3', '/page/4')]
print(f"\nPreviously-misclassified internal nodes now correct:")
for n in int_nodes:
    print(f"  {n['id']}  type={n['type']}")

write_graph(graph)
print(f"\nWritten! Check sync timestamp at docs/data/graph_sync.json")
