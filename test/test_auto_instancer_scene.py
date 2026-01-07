import pymel.core as pm
import maya.cmds as cmds
import numpy as np
import unittest
import os
import sys
from collections import defaultdict

# Add test directory to path to import base_test
test_dir = os.path.dirname(__file__)
if test_dir not in sys.path:
    sys.path.append(test_dir)

from base_test import MayaTkTestCase
from mayatk import AutoInstancer
from mayatk.core_utils.instancing.geometry_matcher import GeometryMatcher


class SceneAuditor:
    """Helper class to query and compare scene structures."""

    @staticmethod
    def get_shape_data(shape_node):
        """Returns (center_hash, shape_hash) tuple."""
        # Get world space points
        try:
            pts = cmds.xform(
                f"{shape_node.name()}.vtx[*]",
                query=True,
                translation=True,
                worldSpace=True,
            )
        except Exception as e:
            print(f"DEBUG: Failed to get points for {shape_node}: {e}")
            return (0, 0, 0), (0, 0, 0)

        if not pts:
            return (0, 0, 0), (0, 0, 0)

        pts = np.array(pts).reshape(-1, 3)

        # 1. Center of Mass
        center = np.mean(pts, axis=0)
        center_hash = tuple([round(v, 2) for v in center])

        # 2. PCA Eigenvalues (Shape)
        if len(pts) < 3:
            shape_hash = (0, 0, 0)
        else:
            centered_pts = pts - center
            cov = np.cov(centered_pts, rowvar=False)
            try:
                eigvals = np.linalg.eigvalsh(cov)
                eigvals.sort()
                shape_hash = tuple([round(v, 2) for v in eigvals])
            except:
                shape_hash = (0, 0, 0)

        return center_hash, shape_hash

    @staticmethod
    def get_invariant_hash(shape_node):
        """
        Computes a hash based on PCA Eigenvalues (Shape) and Center of Mass (Position).
        This is invariant to rotation around the center.
        """
        center_hash, shape_hash = SceneAuditor.get_shape_data(shape_node)
        return hash((center_hash, shape_hash))

    @staticmethod
    def get_flat_hierarchy(root_node):
        """
        Returns a flat list of all transform nodes under the root,
        including their world matrices and geometry signatures.
        """
        if not pm.objExists(root_node):
            return {}

        root = pm.PyNode(root_node)
        data = {}

        # Get all transforms (including root if it's a transform)
        transforms = root.listRelatives(allDescendents=True, type="transform")
        if root.nodeType() == "transform":
            transforms.append(root)

        # Filter out intermediate groups if necessary, or keep them to check structure
        # For now, let's focus on the leaf nodes (meshes)
        mesh_transforms = [t for t in transforms if t.getShape()]

        for tf in mesh_transforms:
            shape = tf.getShape()

            # Get geometry signature (vertex count + area + material)
            poly_count = pm.polyEvaluate(shape, vertex=True)
            area = pm.polyEvaluate(shape, worldArea=True)

            # Get material
            shading_groups = shape.listConnections(type="shadingEngine")
            mat = shading_groups[0].name() if shading_groups else "None"

            # Create a signature
            signature = (poly_count, round(area, 4), mat)

            # Get Invariant Hash (Robust Visual Identity + Position)
            geo_hash = SceneAuditor.get_invariant_hash(shape)

            # Store by signature
            if signature not in data:
                data[signature] = []
            data[signature].append(
                {
                    "name": tf.name(),
                    "matrix": [round(v, 4) for v in tf.getMatrix(worldSpace=True)],
                    "geo_hash": geo_hash,
                    "is_instanced": shape.isInstanced(),
                }
            )

        return data

    @staticmethod
    def compare_groups(reference_group, generated_group):
        """
        Compares two groups to see if they contain the same objects
        (geometrically and spatially), ignoring names.
        """
        ref_data = SceneAuditor.get_flat_hierarchy(reference_group)
        gen_data = SceneAuditor.get_flat_hierarchy(generated_group)

        errors = []

        # 1. Compare unique geometry signatures
        ref_sigs = set(ref_data.keys())
        gen_sigs = set(gen_data.keys())

        if ref_sigs != gen_sigs:
            missing = ref_sigs - gen_sigs
            extra = gen_sigs - ref_sigs
            if missing:
                errors.append(f"Missing geometry types: {missing}")
            if extra:
                errors.append(f"Extra geometry types: {extra}")

        # 2. Compare instance counts and transforms for each signature
        for sig in ref_sigs.intersection(gen_sigs):
            ref_objs = ref_data[sig]
            gen_objs = gen_data[sig]

            # Check count
            if len(ref_objs) != len(gen_objs):
                errors.append(
                    f"Count mismatch for signature {sig}: Expected {len(ref_objs)}, Got {len(gen_objs)}"
                )
                continue

            # Check Geometry Hashes (Visual Identity)
            # We need to verify that for every object in Ref, there is a matching object in Gen
            matched_gen_indices = set()

            for ref_obj in ref_objs:
                ref_hash = ref_obj["geo_hash"]
                found = False
                for i, gen_obj in enumerate(gen_objs):
                    if i in matched_gen_indices:
                        continue

                    # Compare Geometry Hashes
                    if ref_hash == gen_obj["geo_hash"]:
                        matched_gen_indices.add(i)
                        found = True

                        # Also check instance state
                        if ref_obj["is_instanced"] != gen_obj["is_instanced"]:
                            errors.append(
                                f"Instance state mismatch for {ref_obj['name']}: Expected {ref_obj['is_instanced']}, Got {gen_obj['is_instanced']}"
                            )
                        break

                if not found:
                    errors.append(
                        f"Missing object (visual match) for signature {sig} (Ref: {ref_obj['name']})"
                    )

        return errors


class TestAutoInstancerScene(MayaTkTestCase):
    """
    Tests AutoInstancer against a defined scene structure.
    Ensures that 'original_combined_mesh' is correctly processed into 'final_instanced_result_GRP'.
    """

    def setUp(self):
        super().setUp()
        self.create_input_scene()

    def create_mats(self):
        if pm.objExists("MatA"):
            mat_a = pm.PyNode("MatA")
            sg_a = pm.PyNode("MatA_SG")
        else:
            mat_a = pm.shadingNode("lambert", asShader=True, name="MatA")
            sg_a = pm.sets(
                renderable=True, noSurfaceShader=True, empty=True, name="MatA_SG"
            )
            mat_a.outColor.connect(sg_a.surfaceShader)

        if pm.objExists("MatB"):
            mat_b = pm.PyNode("MatB")
            sg_b = pm.PyNode("MatB_SG")
        else:
            mat_b = pm.shadingNode("lambert", asShader=True, name="MatB")
            mat_b.color.set(1, 0, 0)  # Red
            sg_b = pm.sets(
                renderable=True, noSurfaceShader=True, empty=True, name="MatB_SG"
            )
            mat_b.outColor.connect(sg_b.surfaceShader)

        return (mat_a, sg_a), (mat_b, sg_b)

    def create_input_scene(self):
        """Generates the input combined mesh."""
        print("Generating input scene...")
        if pm.objExists("original_combined_mesh"):
            pm.delete("original_combined_mesh")

        (mat_a, sg_a), (mat_b, sg_b) = self.create_mats()

        # Create Temp Prototypes
        cube_proto = pm.polyCube(w=1, h=1, d=1)[0]
        pm.sets(sg_a, forceElement=cube_proto)

        sphere_proto = pm.polySphere(r=1)[0]
        pm.sets(sg_b, forceElement=sphere_proto)

        cone_proto = pm.polyCone(r=1, h=2, sx=8, sy=1, sz=0)[0]
        pm.sets(sg_a, forceElement=cone_proto)

        to_combine = []

        # Cubes at (0,0,0), (10,0,0), (20,0,0)
        for i in range(3):
            dup = pm.duplicate(cube_proto)[0]
            pm.move(dup, i * 10, 0, 0)
            to_combine.append(dup)

        # Spheres at (0,0,10), (10,0,10)
        for i in range(2):
            dup = pm.duplicate(sphere_proto)[0]
            pm.move(dup, i * 10, 0, 10)
            to_combine.append(dup)

        # Cones at (0,0,20), (10,0,20)
        for i in range(2):
            dup = pm.duplicate(cone_proto)[0]
            pm.move(dup, i * 10, 0, 20)
            to_combine.append(dup)

        # Combine
        pm.polyUnite(to_combine, name="original_combined_mesh", ch=False)

        # Cleanup
        pm.delete(cube_proto, sphere_proto, cone_proto)
        print("Input scene generated.")

    def create_reference_scene(self):
        """Generates the expected result (Ground Truth)."""
        print("Generating reference scene...")
        (mat_a, sg_a), (mat_b, sg_b) = self.create_mats()

        final_grp = pm.group(em=True, name="final_instanced_result_GRP")

        # Create Prototypes
        cube_proto = pm.polyCube(w=1, h=1, d=1, name="CubeProto")[0]
        pm.sets(sg_a, forceElement=cube_proto)

        sphere_proto = pm.polySphere(r=1, name="SphereProto")[0]
        pm.sets(sg_b, forceElement=sphere_proto)

        cone_proto = pm.polyCone(r=1, h=2, sx=8, sy=1, sz=0, name="ConeProto")[0]
        pm.sets(sg_a, forceElement=cone_proto)

        # Cubes
        cubes = []
        for i in range(3):
            if i == 0:
                inst = cube_proto
            else:
                inst = pm.instance(cube_proto)[0]
                inst.rename(f"CubeProto{i}")
            pm.move(inst, i * 10, 0, 0)
            cubes.append(inst)

        # Spheres
        spheres = []
        for i in range(2):
            if i == 0:
                inst = sphere_proto
            else:
                inst = pm.instance(sphere_proto)[0]
                inst.rename(f"SphereProto{i}")
            pm.move(inst, i * 10, 0, 10)
            spheres.append(inst)

        # Cones
        cones = []
        for i in range(2):
            if i == 0:
                inst = cone_proto
            else:
                inst = pm.instance(cone_proto)[0]
                inst.rename(f"ConeProto{i}")
            pm.move(inst, i * 10, 0, 20)
            cones.append(inst)

        pm.parent(cubes + spheres + cones, final_grp)
        print("Reference scene generated.")

    def test_auto_instancer_scene_match(self):
        """
        Runs AutoInstancer on 'original_combined_mesh' and verifies it matches 'final_instanced_result_GRP'.
        """
        input_mesh = "original_combined_mesh"
        self.assertTrue(pm.objExists(input_mesh), "Input mesh not created")

        # DEBUG: Manually separate to inspect shells and signatures
        print(f"DEBUG: Manually separating {input_mesh} for inspection...")
        shells = pm.polySeparate(input_mesh, ch=False)
        shells = [pm.PyNode(s) for s in shells]

        matcher = GeometryMatcher(verbose=True)
        debug_log = []
        debug_log.append(f"DEBUG: Found {len(shells)} shells. Checking signatures...")

        sigs = []
        for s in shells:
            sig = matcher.get_mesh_signature(s)
            sigs.append((s.name(), sig))
            debug_log.append(
                f"  Shell {s.name()}: V={sig[0]}, A={sig[3]}, PCA={sig[4]}"
            )

        # Check if signatures match for expected groups
        sig_map = defaultdict(list)
        for name, sig in sigs:
            sig_map[sig].append(name)

        debug_log.append(f"DEBUG: Signature Groups found: {len(sig_map)}")
        for sig, names in sig_map.items():
            debug_log.append(f"  Sig {sig[:4]}... : {len(names)} items -> {names}")

        if len(sig_map) != 3:
            debug_log.append(
                f"WARNING: Expected 3 unique signatures, found {len(sig_map)}"
            )

        # Re-combine for the actual test
        pm.delete(shells)
        self.create_input_scene()  # Recreate fresh

        combined_node = pm.PyNode(input_mesh)

        print(f"Running AutoInstancer on {input_mesh}...")
        instancer = AutoInstancer(
            verbose=True,
            is_static=False,
            separate_combined=True,
            combine_assemblies=False,
            check_hierarchy=False,
            require_same_material=False,
            tolerance=0.1,
        )
        results = instancer.run([combined_node])

        # Group the results
        result_grp = pm.group(em=True, name="generated_results_GRP")
        if results:
            pm.parent(results, result_grp)
        else:
            debug_log.append("WARNING: AutoInstancer returned no results!")

        # Create Reference Scene AFTER running instancer
        self.create_reference_scene()
        expected_result_grp = "final_instanced_result_GRP"

        print("Comparing generated results with expected final result...")
        errors = SceneAuditor.compare_groups(expected_result_grp, result_grp)

        if errors:
            # Get debug info
            gen_data = SceneAuditor.get_flat_hierarchy(result_grp)
            ref_data = SceneAuditor.get_flat_hierarchy(expected_result_grp)

            debug_msg = "\nDEBUG INFO:\n" + "\n".join(debug_log) + "\n"
            debug_msg += (
                f"Generated Group ({result_grp}): {len(gen_data)} signatures found.\n"
            )
            for sig, objs in gen_data.items():
                debug_msg += f"  Sig {sig}: {[o['name'] for o in objs]}\n"

            debug_msg += f"Reference Group ({expected_result_grp}): {len(ref_data)} signatures found.\n"
            for sig, objs in ref_data.items():
                debug_msg += f"  Sig {sig}: {[o['name'] for o in objs]}\n"

            self.fail(
                f"AutoInstancer failed to match expected result:\n"
                + "\n".join(errors)
                + debug_msg
            )
        else:
            print("Success! AutoInstancer output matches expected result.")
