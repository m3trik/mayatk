import heapq
from math import inf

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)


class Graph:
    def __init__(self):
        """Initializes a new instance of the Graph class.

        Attributes:
            nodes (dict): A dictionary containing adjacency lists representing graph connectivity.
            data (dict): A dictionary storing arbitrary data associated with each node.
        """
        self.nodes = {}
        self.data = {}

    def add_node(self, node, data=None):
        """Adds a node to the graph along with its associated data.

        Parameters:
            node (hashable): The node identifier which should be a hashable type.
            data (Any, optional): Data associated with the node, typically positional data.

        Raises:
            ValueError: If no data is provided for the node.
        """
        if data is None:
            raise ValueError(f"No data provided for node {node}")
        self.nodes[node] = {}
        self.data[node] = data

    def add_edge(self, node1, node2, weight=1):
        """Adds an edge between two specified nodes with an optional weight.

        Parameters:
            node1 (hashable): The identifier of the first node.
            node2 (hashable): The identifier of the second node.
            weight (numeric, optional): The weight of the edge. Defaults to 1.

        Raises:
            ValueError: If one or both of the nodes do not exist in the graph.
        """
        if node1 not in self.nodes or node2 not in self.nodes:
            raise ValueError(
                "One or more nodes do not exist - cannot add edge without nodes."
            )
        self.nodes[node1][node2] = weight
        self.nodes[node2][node1] = weight

    def heuristic(self, node1, node2):
        """Calculates the default heuristic between two nodes. Should be overridden in subclasses.

        Parameters:
            node1 (hashable): The identifier of the first node.
            node2 (hashable): The identifier of the second node.

        Returns:
            int: The default heuristic value (0).
        """
        return 0

    def find_path(self, start, goal, algorithm="a_star"):
        """Finds a path from start node to goal node using the specified algorithm.

        Parameters:
            start (hashable): The starting node identifier.
            goal (hashable): The goal node identifier.
            algorithm (str, optional): The name of the algorithm to use ('a_star' or 'dijkstra').

        Returns:
            list: A list of nodes representing the path from start to goal.

        Raises:
            ValueError: If the specified algorithm is not supported.
        """
        if algorithm == "a_star":
            return self.a_star(start, goal)
        elif algorithm == "dijkstra":
            return self.dijkstra(start, goal)
        else:
            raise ValueError("Unsupported pathfinding algorithm.")

    def a_star(self, start, goal):
        """Implements the A* algorithm to find the shortest path from start to goal node.

        Parameters:
            start (hashable): The starting node identifier.
            goal (hashable): The goal node identifier.

        Returns:
            list: A list of nodes representing the path from start to goal.
        """
        open_set = []
        heapq.heappush(open_set, (0, start, []))
        g_score = {node: inf for node in self.nodes}
        g_score[start] = 0

        while open_set:
            _, current, path = heapq.heappop(open_set)

            if current == goal:
                return path + [goal]

            for neighbor, weight in self.nodes[current].items():
                tentative_g_score = g_score[current] + weight
                if tentative_g_score < g_score[neighbor]:
                    g_score[neighbor] = tentative_g_score
                    f_score = tentative_g_score + self.heuristic(current, neighbor)
                    heapq.heappush(open_set, (f_score, neighbor, path + [current]))

        return []

    def dijkstra(self, start, goal):
        """Implements Dijkstra's algorithm to find the shortest path from start to goal node.

        Parameters:
            start (hashable): The starting node identifier.
            goal (hashable): The goal node identifier.

        Returns:
            list: A list of nodes representing the path from start to goal.
        """
        distances = {node: inf for node in self.nodes}
        distances[start] = 0
        priority_queue = [(0, start)]
        previous = {node: None for node in self.nodes}

        while priority_queue:
            current_distance, current = heapq.heappop(priority_queue)

            if current == goal:
                path = []
                while previous[current] is not None:
                    path.insert(0, current)
                    current = previous[current]
                return [start] + path

            for neighbor, weight in self.nodes[current].items():
                distance = current_distance + weight
                if distance < distances[neighbor]:
                    distances[neighbor] = distance
                    previous[neighbor] = current
                    heapq.heappush(priority_queue, (distance, neighbor))

        return []


class MeshGraph(Graph):
    def __init__(self, mesh_name):
        super().__init__()
        self.mesh = pm.PyNode(mesh_name)
        self.heuristic_cache = {}
        self.build_graph()

    def build_graph(self):
        """Efficiently builds graph based on the mesh's topology."""
        # Retrieve all vertex positions at once
        vertices = list(self.mesh.vtx)  # Convert to list for indexed access
        positions = {vtx.index(): vtx.getPosition(space="world") for vtx in vertices}

        # Add nodes with their positions in one go
        for vtx_index, position in positions.items():
            self.add_node(vtx_index, data=position)

        # Efficiently add edges by checking connected vertices once
        for vtx in vertices:
            vtx_index = vtx.index()
            for cv in vtx.connectedVertices():
                cv_index = cv.index()
                if (
                    cv_index > vtx_index
                ):  # Prevent double handling and ensure both nodes exist
                    if cv_index in positions:
                        weight = (positions[vtx_index] - positions[cv_index]).length()
                        self.add_edge(vtx_index, cv_index, weight)

    def heuristic(self, node1, node2):
        if (node1, node2) in self.heuristic_cache:
            return self.heuristic_cache[(node1, node2)]
        pos1 = self.data[node1]
        pos2 = self.data[node2]
        heuristic_value = (pos1 - pos2).length()
        self.heuristic_cache[(node1, node2)] = heuristic_value
        self.heuristic_cache[
            (node2, node1)
        ] = heuristic_value  # Since distance is symmetric
        return heuristic_value


if __name__ == "__main__":
    from mayatk.core_utils.components import Components

    # Name of the mesh in Maya
    mesh_name = "Decimated"  # Replace "Decimated" with the actual name of your mesh

    # Instantiate the MeshGraph with the name of the mesh
    mesh_graph = MeshGraph(mesh_name)

    # Vertex indices for which to find the shortest path
    selection = pm.selected()
    start_vertex, end_vertex = Components.convert_component_type(
        selection[:2], "vtx", "int"
    )
    print(0, start_vertex, end_vertex)

    # Call the A* algorithm to find the shortest path from start_vertex to end_vertex
    path = mesh_graph.a_star(start_vertex, end_vertex)

    # Format the path for output by converting vertex indices back to Maya's vertex notation
    maya_path = Components.convert_int_to_component(mesh_name, path, "vtx")

    # Print the shortest path
    print(
        f"Shortest path from vertex {start_vertex} to vertex {end_vertex} is:",
        maya_path,
    )

    # Optionally, select the path in Maya
    pm.select(maya_path)
