# !/usr/bin/python
# coding=utf-8
from importlib import import_module, reload

try:
    import pymel.core as pm
    import maya.OpenMaya as om
    import maya.OpenMayaFX as omfx
except ImportError as error:
    print(__file__, error)

try:
    import MASH.api as _MASH_API  # type: ignore
except ImportError:
    _MASH_API = None

# Constants
_RAD_TO_DEG = 57.29577951308232


class _MashToolkitMeta(type):
    """Metaclass to enable lazy attribute forwarding to MASH.api."""

    def __getattr__(cls, name):
        """Lazily forward unknown attributes to MASH.api module."""
        global _MASH_API
        if _MASH_API is None:
            try:
                _MASH_API = import_module("MASH.api")
            except ImportError:
                raise AttributeError(
                    f"{cls.__name__} has no attribute '{name}' (MASH API unavailable)"
                )
        if hasattr(_MASH_API, name):
            return getattr(_MASH_API, name)
        raise AttributeError(f"{cls.__name__} has no attribute '{name}'")


class MashNetworkNodes(object):
    """Lightweight container for the core nodes created per network."""

    __slots__ = ("waiter", "instancer", "distribute")

    def __init__(self, waiter=None, instancer=None, distribute=None):
        self.waiter = waiter
        self.instancer = instancer
        self.distribute = distribute

    def as_tuple(self):
        return self.waiter, self.instancer, self.distribute


class MashToolkit(object, metaclass=_MashToolkitMeta):
    """Thin wrapper around MASH API for building and baking networks.

    This class provides high-level helpers (create_network, bake_instancer) and
    also lazily forwards unknown attributes to MASH.api, so users can access
    MASH classes (e.g., MashToolkit.Network, MashToolkit.Distribute) without
    direct imports. All MASH plugin loading and API resolution happens on-demand.

    Example:
        network, waiter, inst, dist = MashToolkit.create_network(objects=objs)
        # or access MASH.api symbols directly:
        curve_node = network.addNode("MASH_Curve")
    """

    @classmethod
    def __dir__(cls):
        """Include MASH.api attributes in dir() output."""
        global _MASH_API
        if _MASH_API is None:
            try:
                _MASH_API = import_module("MASH.api")
            except ImportError:
                return sorted(super().__dir__())
        return sorted(set(super().__dir__()).union(dir(_MASH_API)))

    @staticmethod
    def ensure_plugin_loaded():
        loaded = int(pm.pluginInfo("MASH", q=True, l=1))
        if not loaded:
            pm.loadPlugin("MASH")

    @classmethod
    def create_network(
        cls,
        network=None,
        objects=None,
        networkName="MASH#",
        geometry="Mesh",
        distType="linearNetwork",
        hideOnCreate=True,
    ):
        """Create (or populate) a MASH network and return it with its core nodes."""
        if not objects:
            raise ValueError("'objects' argument is required to create a MASH network.")

        cls.ensure_plugin_loaded()
        network = cls._resolve_network(network)
        objects_ = cls._filter_objects(objects, geometry)
        if not objects_:
            return pm.mel.MASHinViewMessage(
                pm.mel.getPluginResource("MASH", "kMASHMeshesOnly"), "Error"
            )

        if hideOnCreate:
            for obj in objects_:
                pm.hide(obj)

        waiter = cls._create_waiter(networkName)
        distNode = cls._create_distribute(waiter)
        instancer = cls._create_instancer(waiter, geometry)

        cls._configure_distribution(distNode, distType, len(objects_))
        cls._connect_prototypes(instancer, objects_, geometry)
        cls._refresh_repro(instancer, geometry)

        nodes = MashNetworkNodes(
            waiter=waiter, instancer=instancer, distribute=distNode
        )
        cls._register_nodes(network, nodes)
        return network, nodes.waiter, nodes.instancer, nodes.distribute

    @classmethod
    def bake_instancer(
        cls,
        network,
        instancer,
        bakeTranslate=True,
        bakeRotation=True,
        bakeScale=True,
        bakeAnimation=False,
        bakeVisibility=True,
        bakeToInstances=False,
        upstreamNodes=False,
        _getMObjectFromName=None,
    ):
        """Convert an instancer's points to real geometry."""
        if _getMObjectFromName:
            sel = om.MSelectionList()
            sel.add(_getMObjectFromName)
            thisNode = om.MObject()
            sel.getDependNode(0, thisNode)
            return thisNode

        _instancer = cls._get_instancer(instancer)
        thisNode = cls.bake_instancer(
            network, _instancer, _getMObjectFromName=_instancer.name()
        )
        fnThisNode = om.MFnDependencyNode(thisNode)

        sf, ef = cls._determine_frame_range(bakeAnimation)
        first_frame = int(sf)
        result = []
        for frame in range(int(sf), int(ef)):
            pm.currentTime(frame)
            group = cls._prepare_instance_group(_instancer, frame, first_frame)
            visList = cls._read_visibility_array(fnThisNode, thisNode)
            result.extend(
                cls._bake_frame(
                    _instancer,
                    group,
                    visList,
                    bakeTranslate,
                    bakeRotation,
                    bakeScale,
                    bakeVisibility,
                    bakeAnimation,
                    bakeToInstances,
                    upstreamNodes,
                )
            )
        return result

    # ----------------------------------------------------------------------------------
    # Internal helpers
    # ----------------------------------------------------------------------------------
    @staticmethod
    def _filter_objects(objects, geometry):
        if geometry == "Mesh":
            return pm.ls(objects, lf=1, ni=1, dag=1, type="mesh", l=1)
        return pm.ls(objects)

    @classmethod
    def _resolve_network(cls, network):
        if network is not None:
            return network
        # Network class is now accessible via metaclass __getattr__
        return cls.Network()

    @staticmethod
    def _create_waiter(name):
        waiter = pm.createNode("MASH_Waiter", n=name)
        pm.addAttr(waiter, hidden=True, at="message", longName="instancerMessage")
        return waiter

    @staticmethod
    def _create_distribute(waiter):
        node = pm.createNode("MASH_Distribute", n="{}_Distribute".format(waiter.name()))
        pm.setAttr(node.mapDirection, 4)
        pm.connectAttr(node.outputPoints, waiter.inputPoints, force=1)
        pm.connectAttr(node.waiterMessage, waiter.waiterMessage, f=1)
        return node

    @staticmethod
    def _create_instancer(waiter, geometry):
        if geometry == "Mesh":
            import mash_repro_utils

            reload(mash_repro_utils)
            reproName = "{}_Repro".format(waiter.name())
            instancer = pm.ls(mash_repro_utils.create_mash_repro_node(None, reproName))[
                0
            ]
        else:
            instancerName = "{}_Instancer".format(waiter.name())
            instancer = pm.createNode("instancer", name=instancerName)
        pm.connectAttr(waiter.outputPoints, instancer.inputPoints, force=1)
        pm.addAttr(instancer, hidden=True, at="message", longName="instancerMessage")
        pm.connectAttr(waiter.instancerMessage, instancer.instancerMessage, f=1)
        return instancer

    @staticmethod
    def _configure_distribution(node, distType, object_count):
        if object_count > 1:
            pm.setAttr(node.pointCount, object_count)
        arrangement_values = {
            "radialNetwork": 2,
            "gridNetwork": 6,
            "initialNetwork": 7,
        }
        if distType == "zeroNetwork":
            pm.setAttr(node.amplitudeX, 0.0)
        elif distType in arrangement_values:
            pm.setAttr(node.attr("arrangement"), arrangement_values[distType])

    @staticmethod
    def _connect_prototypes(instancer, objects_, geometry):
        for transform in pm.ls(objects_, transforms=1):
            if geometry == "Mesh":
                import mash_repro_utils

                mash_repro_utils.connect_mesh_group(
                    instancer.name(), transform.name(), new_connection=True
                )
            else:
                pm.mel.eval(
                    "instancer -e -a -obj {} {};".format(transform, instancer.name())
                )

    @staticmethod
    def _refresh_repro(instancer, geometry):
        if geometry != "Mesh":
            return
        import mash_repro_aetemplate

        mash_repro_aetemplate.refresh_all_aetemplates(force=True)

    @staticmethod
    def _register_nodes(network, nodes):
        if not network:
            return
        if not hasattr(network, "waiter"):
            network.waiter = nodes.waiter
        if not hasattr(network, "instancer"):
            network.instancer = nodes.instancer
        if not hasattr(network, "distribute"):
            network.distribute = nodes.distribute

    @staticmethod
    def _get_instancer(instancer):
        nodes = pm.ls(instancer, type="instancer")
        if not nodes:
            raise RuntimeError(
                '"{}" is type: "{}". The required node type is "instancer".'.format(
                    instancer, pm.nodeType(instancer)
                )
            )
        return nodes[0]

    @staticmethod
    def _determine_frame_range(bakeAnimation):
        sf = int(pm.playbackOptions(q=True, min=True)) - 1
        ef = int(pm.playbackOptions(q=True, max=True)) + 2
        if not bakeAnimation:
            sf = pm.currentTime(query=True)
            ef = sf + 1
        return sf, ef

    @staticmethod
    def _prepare_instance_group(instancer, frame, first_frame):
        group_name = "{}_objects".format(instancer.name())
        if frame == first_frame and pm.objExists(group_name):
            pm.delete(group_name)
        if not pm.objExists(group_name):
            pm.createNode("transform", n=group_name)
        return group_name

    @staticmethod
    def _read_visibility_array(fnThisNode, thisNode):
        inPointsAttribute = fnThisNode.attribute("inputPoints")
        inPointsPlug = om.MPlug(thisNode, inPointsAttribute)
        inPointsObj = inPointsPlug.asMObject()
        inputPPData = om.MFnArrayAttrsData(inPointsObj)
        return inputPPData.getDoubleData("visibility")[:]

    @staticmethod
    def _bake_frame(
        instancer,
        group,
        visList,
        bakeTranslate,
        bakeRotation,
        bakeScale,
        bakeVisibility,
        bakeAnimation,
        bakeToIntances,
        upstreamNodes,
    ):
        dp = om.MDagPath()
        sl = om.MSelectionList()
        sl.add(instancer)
        sl.getDagPath(0, dp)
        fni = omfx.MFnInstancer(dp)
        dpa = om.MDagPathArray()
        m = om.MMatrix()
        sa = om.MScriptUtil()
        sa.createFromList([0.0, 0.0, 0.0], 3)
        sp = sa.asDoublePtr()
        result = []
        for particle_index in range(fni.particleCount()):
            visibility = visList[particle_index]
            fni.instancesForParticle(particle_index, dpa, m)
            for i in range(dpa.length()):
                created = MashToolkit._duplicate_instance(
                    instancer,
                    dpa[i],
                    particle_index,
                    bakeToIntances,
                    upstreamNodes,
                )
                MashToolkit._parent_and_key_visibility(
                    created, group, visibility, bakeAnimation
                )
                MashToolkit._apply_transform_keys(
                    created,
                    dpa[i],
                    m,
                    sp,
                    bakeTranslate,
                    bakeRotation,
                    bakeScale,
                    bakeAnimation,
                )
                if bakeVisibility:
                    pm.setAttr(created + ".v", visibility)
                    if bakeAnimation:
                        pm.setKeyframe(created + ".v")
                result.append(created)
        return result

    @staticmethod
    def _duplicate_instance(
        instancer, dag_path, particle_index, bakeToIntances, upstreamNodes
    ):
        # Extract simple name from full path (remove namespace and pipes)
        simple_name = dag_path.partialPathName().rsplit(":", 1)[-1].rsplit("|", 1)[-1]
        name = "{}_{}_".format(instancer.name(), simple_name, particle_index)
        if bakeToIntances:
            return pm.instance(dag_path.fullPathName(), leaf=1, name=name)[0]
        return pm.duplicate(
            dag_path.fullPathName(),
            returnRootsOnly=1,
            upstreamNodes=upstreamNodes,
            name=name,
        )[0]

    @staticmethod
    def _parent_and_key_visibility(node, group, visibility, bakeAnimation):
        parents = pm.listRelatives(node, p=True) or []
        parent_name = parents[0].name() if parents else None
        already_parented = parent_name == group
        if not already_parented:
            try:
                pm.parent(node, group)
            except RuntimeError:
                pass
            pm.setKeyframe(node + ".visibility", v=0, t=pm.currentTime(q=True) - 1)
            pm.setKeyframe(
                node + ".visibility", v=visibility if visibility is not None else 1
            )
        elif bakeAnimation and visibility is not None:
            pm.setKeyframe(node + ".visibility", v=visibility)

    @staticmethod
    def _apply_transform_keys(
        node,
        dag_path,
        matrix,
        scale_ptr,
        bakeTranslate,
        bakeRotation,
        bakeScale,
        bakeAnimation,
    ):
        tm = om.MTransformationMatrix(matrix)
        instancedPathMatrix = dag_path.inclusiveMatrix()
        finalMatrixForPath = instancedPathMatrix * matrix
        finalPoint = om.MPoint.origin * finalMatrixForPath
        if bakeTranslate:
            try:
                pm.setAttr(node + ".t", finalPoint.x, finalPoint.y, finalPoint.z)
                if bakeAnimation:
                    pm.setKeyframe(node + ".t")
            except RuntimeError:
                pass
        if bakeRotation:
            r = tm.eulerRotation().asVector()
            try:
                pm.setAttr(
                    node + ".r",
                    r[0] * _RAD_TO_DEG,
                    r[1] * _RAD_TO_DEG,
                    r[2] * _RAD_TO_DEG,
                )
                if bakeAnimation:
                    pm.setKeyframe(node + ".r")
            except RuntimeError:
                pass
        if bakeScale:
            tm.getScale(scale_ptr, om.MSpace.kWorld)
            sx = om.MScriptUtil().getDoubleArrayItem(scale_ptr, 0)
            sy = om.MScriptUtil().getDoubleArrayItem(scale_ptr, 1)
            sz = om.MScriptUtil().getDoubleArrayItem(scale_ptr, 2)
            om.MTransformationMatrix(dag_path.inclusiveMatrix()).getScale(
                scale_ptr, om.MSpace.kWorld
            )
            sx2 = om.MScriptUtil().getDoubleArrayItem(scale_ptr, 0)
            sy2 = om.MScriptUtil().getDoubleArrayItem(scale_ptr, 1)
            sz2 = om.MScriptUtil().getDoubleArrayItem(scale_ptr, 2)
            try:
                pm.setAttr(node + ".s", sx * sx2, sy * sy2, sz * sz2)
                if bakeAnimation:
                    pm.setKeyframe(node + ".s")
            except RuntimeError:
                pass


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
