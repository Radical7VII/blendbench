"""
Bedrock/Blockbench 模型导入器
基于 mcblend 项目实现，用于将 Minecraft Bedrock Edition 模型导入到 Blender
"""

from __future__ import annotations

import math
import json
from typing import Dict, List, Any, Optional, Tuple, cast
from dataclasses import dataclass, field

import bpy
from bpy.types import Object, Armature, Mesh, MeshUVLoopLayer, ArmatureEditBones
from bpy.props import StringProperty
from bpy_extras.io_utils import ImportHelper
import mathutils


# ============================================================================
# 常量定义
# ============================================================================

MINECRAFT_SCALE_FACTOR = 16.0
"""Minecraft 缩放因子: 16 单位 = 1 米"""


# ============================================================================
# 类型别名
# ============================================================================

Vector3d = Tuple[float, float, float]
Vector2d = Tuple[float, float]


# ============================================================================
# 异常定义
# ============================================================================

class ImporterException(Exception):
    """导入器异常"""
    pass


# ============================================================================
# UV 坐标转换器
# ============================================================================

class CoordinatesConverter:
    """
    UV 坐标转换器
    将 Minecraft UV 坐标（基于纹理尺寸）转换为 Blender UV 坐标（0-1 范围）
    """
    def __init__(self, texture_width: int, texture_height: int):
        self.texture_width = texture_width
        self.texture_height = texture_height

    def convert(self, uv: Tuple[float, float]) -> Tuple[float, float]:
        """将 Minecraft UV 坐标转换为 Blender UV 坐标"""
        u = uv[0] / self.texture_width
        v = 1.0 - (uv[1] / self.texture_height)  # Blender UV Y 轴翻转
        return (u, v)


# ============================================================================
# 数据类定义
# ============================================================================

@dataclass
class ImportLocator:
    """表示 Minecraft 定位器"""
    name: str
    position: Vector3d
    rotation: Vector3d
    blend_empty: Optional[Object] = None


@dataclass
class ImportCube:
    """表示 Minecraft 方块"""
    origin: Vector3d
    size: Vector3d
    pivot: Vector3d
    rotation: Vector3d
    inflate: float
    mirror: bool
    uv: Dict[str, Any]
    blend_cube: Optional[Object] = None


@dataclass
class ImportBone:
    """表示 Minecraft 骨骼"""
    name: str
    parent: Optional[str]
    pivot: Vector3d
    rotation: Vector3d
    mirror: bool
    cubes: List[ImportCube] = field(default_factory=list)
    locators: List[ImportLocator] = field(default_factory=list)
    blend_empty: Optional[Object] = None


# ============================================================================
# 模型加载器
# ============================================================================

class ModelLoader:
    """
    Minecraft 模型 JSON 文件加载器
    支持 format_version 1.8.0, 1.12.0, 1.16.0
    """

    def __init__(self, data: Dict[str, Any], geometry_name: str = ""):
        self.data = data
        self.format_version = self._detect_format_version()
        self.geometry = self._find_geometry(geometry_name)
        self.description = self._load_description()
        self.bones = self._load_bones()

    def _detect_format_version(self) -> str:
        """检测模型格式版本"""
        if "format_version" in self.data:
            version = self.data["format_version"]
            # 解析版本号并选择合适的解析器
            if isinstance(version, str):
                parts = version.split(".")
                if len(parts) >= 2:
                    major, minor = int(parts[0]), int(parts[1])
                    if major >= 1 and minor >= 16:
                        return "1.16.0"
                    elif major >= 1 and minor >= 12:
                        return "1.12.0"
            return "1.8.0"
        elif "minecraft:geometry" in self.data:
            return "1.16.0"
        else:
            return "1.8.0"

    def _find_geometry(self, geometry_name: str) -> Dict[str, Any]:
        """查找指定名称的几何体"""
        if self.format_version in ("1.16.0", "1.12.0"):
            geometries = self.data.get("minecraft:geometry", [])
            if not isinstance(geometries, list):
                raise ImporterException("minecraft:geometry 不是列表")

            # 尝试查找匹配的几何体
            for full_name in [geometry_name, f"geometry.{geometry_name}"]:
                for geometry in geometries:
                    if not isinstance(geometry, dict):
                        continue
                    desc = geometry.get("description", {})
                    identifier = desc.get("identifier", "")
                    if full_name == "" or full_name == identifier:
                        return geometry

            # 如果没有指定名称，返回第一个有效的几何体
            if geometry_name == "" and geometries:
                for geometry in geometries:
                    if isinstance(geometry, dict) and "bones" in geometry:
                        return geometry

            raise ImporterException(f"找不到几何体: {geometry_name}")

        elif self.format_version == "1.8.0":
            # 1.8.0 格式：几何体直接在根对象中
            for full_name in [geometry_name, f"geometry.{geometry_name}"]:
                for key, value in self.data.items():
                    if key in ("format_version", "debug"):
                        continue
                    if full_name == "" or full_name == key:
                        if isinstance(value, dict):
                            return {"description": {"identifier": key}, "bones": value.get("bones", [])}

            raise ImporterException(f"找不到几何体: {geometry_name}")

        raise ImporterException(f"不支持的格式版本: {self.format_version}")

    def _load_description(self) -> Dict[str, Any]:
        """加载模型描述信息"""
        result = {
            "identifier": "geometry.unknown",
            "texture_width": 64,
            "texture_height": 64,
            "visible_bounds_offset": [0.0, 0.0, 0.0],
            "visible_bounds_width": 1.0,
            "visible_bounds_height": 1.0,
        }

        if self.format_version in ("1.16.0", "1.12.0"):
            desc = self.geometry.get("description", {})
            if "identifier" in desc:
                result["identifier"] = desc["identifier"]
            if "texture_width" in desc:
                result["texture_width"] = int(desc["texture_width"])
            if "texture_height" in desc:
                result["texture_height"] = int(desc["texture_height"])
            if "visible_bounds_offset" in desc:
                result["visible_bounds_offset"] = desc["visible_bounds_offset"]
            if "visible_bounds_width" in desc:
                result["visible_bounds_width"] = float(desc["visible_bounds_width"])
            if "visible_bounds_height" in desc:
                result["visible_bounds_height"] = float(desc["visible_bounds_height"])

        elif self.format_version == "1.8.0":
            desc = self.geometry.get("description", {})
            result["identifier"] = desc.get("identifier", "geometry.unknown")
            # 1.8.0 使用 texturewidth/textureheight
            if "texturewidth" in self.geometry:
                result["texture_width"] = int(self.geometry["texturewidth"])
            if "textureheight" in self.geometry:
                result["texture_height"] = int(self.geometry["textureheight"])

        return result

    def _load_bones(self) -> List[Dict[str, Any]]:
        """加载骨骼列表"""
        bones_data = self.geometry.get("bones", [])
        if not isinstance(bones_data, list):
            return []

        result = []
        for bone in bones_data:
            if isinstance(bone, dict):
                result.append(self._load_bone(bone))
        return result

    def _load_bone(self, bone: Dict[str, Any]) -> Dict[str, Any]:
        """加载单个骨骼"""
        result = {
            "name": bone.get("name", "unnamed"),
            "parent": bone.get("parent"),
            "pivot": bone.get("pivot", [0, 0, 0]),
            "rotation": bone.get("rotation", [0, 0, 0]),
            "mirror": bone.get("mirror", False),
            "inflate": bone.get("inflate", 0.0),
            "cubes": [],
            "locators": {},
        }

        # 加载方块
        if "cubes" in bone:
            cubes = bone["cubes"]
            if isinstance(cubes, list):
                for cube in cubes:
                    if isinstance(cube, dict):
                        result["cubes"].append(
                            self._load_cube(cube, result["mirror"], result["inflate"])
                        )

        # 加载定位器
        if "locators" in bone:
            locators = bone["locators"]
            if isinstance(locators, dict):
                result["locators"] = self._load_locators(locators)

        return result

    def _load_cube(
        self, cube: Dict[str, Any], default_mirror: bool, default_inflate: float
    ) -> Dict[str, Any]:
        """加载单个方块"""
        size = tuple(cube.get("size", [0, 0, 0]))
        mirror = cube.get("mirror", default_mirror)

        result = {
            "origin": tuple(cube.get("origin", [0, 0, 0])),
            "size": size,
            "pivot": tuple(cube.get("pivot", [0, 0, 0])),
            "rotation": tuple(cube.get("rotation", [0, 0, 0])),
            "inflate": cube.get("inflate", default_inflate),
            "mirror": mirror,
            "uv": self._load_cube_uv(cube, size, mirror),
        }
        return result

    def _load_cube_uv(
        self, cube: Dict[str, Any], size: Tuple, mirror: bool
    ) -> Dict[str, Any]:
        """加载方块的 UV 映射"""
        if "uv" not in cube:
            return self._create_default_uv(size, mirror, (0, 0))

        uv = cube["uv"]
        if isinstance(uv, list) and len(uv) >= 2:
            # 简单 UV 格式: [u, v]
            return self._create_default_uv(size, mirror, tuple(uv[:2]))
        elif isinstance(uv, dict):
            # 详细 UV 格式: {north: {...}, south: {...}, ...}
            return self._load_per_face_uv(uv, size)

        return self._create_default_uv(size, mirror, (0, 0))

    def _create_default_uv(
        self, size: Tuple, mirror: bool, uv: Tuple[float, float]
    ) -> Dict[str, Any]:
        """创建默认的 UV 映射（标准 Minecraft 方块 UV 布局）"""
        width, height, depth = int(size[0]), int(size[1]), int(size[2])

        def _face(uv_size: Tuple, uv_pos: Tuple) -> Dict:
            return {"uv_size": uv_size, "uv": uv_pos}

        face1 = _face((depth, height), (uv[0], uv[1] + depth))
        face2 = _face((width, height), (uv[0] + depth, uv[1] + depth))
        face3 = _face((depth, height), (uv[0] + depth + width, uv[1] + depth))
        face4 = _face((width, height), (uv[0] + 2 * depth + width, uv[1] + depth))
        face5 = _face((width, depth), (uv[0] + depth, uv[1]))
        face6 = _face((width, -depth), (uv[0] + depth + width, uv[1] + depth))

        if mirror:
            face_west, face_east = face1, face3
        else:
            face_east, face_west = face1, face3

        return {
            "north": face2,
            "south": face4,
            "east": face_east,
            "west": face_west,
            "up": face5,
            "down": face6,
        }

    def _load_per_face_uv(self, uv: Dict[str, Any], size: Tuple) -> Dict[str, Any]:
        """加载每面独立的 UV 映射"""
        width, height, depth = size[0], size[1], size[2]

        default_sizes = {
            "north": (width, height),
            "south": (width, height),
            "east": (depth, height),
            "west": (depth, height),
            "up": (width, depth),
            "down": (width, depth),
        }

        result = {}
        for face in ["north", "south", "east", "west", "up", "down"]:
            if face in uv and isinstance(uv[face], dict):
                face_data = uv[face]
                result[face] = {
                    "uv": face_data.get("uv", [0, 0]),
                    "uv_size": face_data.get("uv_size", default_sizes[face]),
                }
            else:
                result[face] = {"uv": [0, -1], "uv_size": [0, 0]}  # 不可见面

        return result

    def _load_locators(self, locators: Dict[str, Any]) -> Dict[str, Any]:
        """加载定位器"""
        result = {}
        for name, locator in locators.items():
            if isinstance(locator, list):
                result[name] = {"offset": locator, "rotation": [0, 0, 0]}
            elif isinstance(locator, dict):
                result[name] = {
                    "offset": locator.get("offset", [0, 0, 0]),
                    "rotation": locator.get("rotation", [0, 0, 0]),
                }
        return result


# ============================================================================
# 几何体导入器
# ============================================================================

class ImportGeometry:
    """
    Minecraft 几何体导入器
    负责将加载的模型数据转换为 Blender 对象
    """

    def __init__(self, loader: ModelLoader):
        self.identifier = loader.description["identifier"]
        self.texture_width = loader.description["texture_width"]
        self.texture_height = loader.description["texture_height"]
        self.visible_bounds_offset = loader.description["visible_bounds_offset"]
        self.visible_bounds_width = loader.description["visible_bounds_width"]
        self.visible_bounds_height = loader.description["visible_bounds_height"]

        self.uv_converter = CoordinatesConverter(
            self.texture_width, self.texture_height
        )

        # 解析骨骼
        self.bones: Dict[str, ImportBone] = {}
        for bone_data in loader.bones:
            bone = self._create_import_bone(bone_data)
            self.bones[bone.name] = bone

    def _create_import_bone(self, data: Dict[str, Any]) -> ImportBone:
        """创建 ImportBone 对象"""
        cubes = []
        for cube_data in data.get("cubes", []):
            cubes.append(
                ImportCube(
                    origin=tuple(cube_data["origin"]),
                    size=tuple(cube_data["size"]),
                    pivot=tuple(cube_data["pivot"]),
                    rotation=tuple(cube_data["rotation"]),
                    inflate=cube_data["inflate"],
                    mirror=cube_data["mirror"],
                    uv=cube_data["uv"],
                )
            )

        locators = []
        for name, loc_data in data.get("locators", {}).items():
            locators.append(
                ImportLocator(
                    name=name,
                    position=tuple(loc_data["offset"]),
                    rotation=tuple(loc_data["rotation"]),
                )
            )

        return ImportBone(
            name=data["name"],
            parent=data["parent"],
            pivot=tuple(data["pivot"]),
            rotation=tuple(data["rotation"]),
            mirror=data["mirror"],
            cubes=cubes,
            locators=locators,
        )

    def build_with_armature(self, context: bpy.types.Context) -> Object:
        """
        构建 Blender 骨架模型
        """
        # 1. 先用空对象构建结构
        armature = self._build_with_empties(context)

        # 2. 转换为骨架
        assert isinstance(armature.data, Armature)
        context.view_layer.objects.active = armature
        bpy.ops.object.mode_set(mode="EDIT")
        edit_bones = armature.data.edit_bones

        # 创建骨骼
        for bone in self.bones.values():
            self._add_bone(edit_bones, 0.3, bone)

        # 设置骨骼父子关系
        for bone in self.bones.values():
            if bone.parent is not None and bone.parent in self.bones:
                parent_bone = self.bones[bone.parent]
                edit_bones[bone.name].parent = edit_bones[parent_bone.name]

        bpy.ops.object.mode_set(mode="OBJECT")

        # 将对象重新绑定到骨骼
        for bone in self.bones.values():
            for cube in bone.cubes:
                if cube.blend_cube is not None:
                    self._parent_to_bone(context, cube.blend_cube, armature, bone.name)

            for locator in bone.locators:
                if locator.blend_empty is not None:
                    self._parent_to_bone(
                        context, locator.blend_empty, armature, bone.name
                    )

            # 删除临时空对象
            if bone.blend_empty is not None:
                bpy.data.objects.remove(bone.blend_empty)

        return armature

    def _build_with_empties(self, context: bpy.types.Context) -> Object:
        """使用空对象构建骨骼结构"""
        # 创建空骨架
        bpy.ops.object.armature_add(enter_editmode=True, align="WORLD", location=[0, 0, 0])
        bpy.ops.armature.select_all(action="SELECT")
        bpy.ops.armature.delete()
        bpy.ops.object.mode_set(mode="OBJECT")

        armature: Object = cast(Object, context.object)

        # 创建骨骼对应的空对象和方块
        for bone in self.bones.values():
            # 创建骨骼空对象
            bpy.ops.object.empty_add(type="SPHERE", location=[0, 0, 0], radius=0.2)
            bone_obj: Object = cast(Object, context.object)
            bone.blend_empty = bone_obj
            self._mc_pivot(bone_obj, bone.pivot)
            bone_obj.name = bone.name

            # 创建方块
            for cube in bone.cubes:
                bpy.ops.mesh.primitive_cube_add(size=1, enter_editmode=False, location=[0, 0, 0])
                cube_obj: Object = cast(Object, context.object)
                cube.blend_cube = cube_obj

                # 设置 UV
                if cube_obj.data.uv_layers.active is not None:
                    self._set_cube_uv(cube_obj, cube)

                # 设置大小和位置
                self._mc_set_size(cube_obj, cube.size, cube.inflate)
                self._mc_pivot(cube_obj, cube.pivot)
                self._mc_translate(cube_obj, cube.origin, cube.size, cube.pivot)

            # 创建定位器
            for locator in bone.locators:
                bpy.ops.object.empty_add(type="SPHERE", location=[0, 0, 0], radius=0.1)
                locator_obj: Object = cast(Object, context.object)
                locator.blend_empty = locator_obj
                self._mc_pivot(locator_obj, locator.position)
                self._mc_rotate(locator_obj, locator.rotation)
                locator_obj.name = locator.name

        # 设置父子关系（保持变换）
        for bone in self.bones.values():
            assert bone.blend_empty is not None
            bone_obj = bone.blend_empty

            # 骨骼父子关系
            if bone.parent is not None and bone.parent in self.bones:
                parent_obj = cast(Object, self.bones[bone.parent].blend_empty)
                context.view_layer.update()
                bone_obj.parent = parent_obj
                bone_obj.matrix_parent_inverse = parent_obj.matrix_world.inverted()

            # 方块父子关系
            for cube in bone.cubes:
                cube_obj = cast(Object, cube.blend_cube)
                context.view_layer.update()
                cube_obj.parent = bone_obj
                cube_obj.matrix_parent_inverse = bone_obj.matrix_world.inverted()

            # 定位器父子关系
            for locator in bone.locators:
                locator_obj = cast(Object, locator.blend_empty)
                context.view_layer.update()
                locator_obj.parent = bone_obj
                locator_obj.matrix_parent_inverse = bone_obj.matrix_world.inverted()

        # 应用旋转
        for bone in self.bones.values():
            bone_obj = cast(Object, bone.blend_empty)
            context.view_layer.update()
            self._mc_rotate(bone_obj, bone.rotation)
            for cube in bone.cubes:
                cube_obj = cast(Object, cube.blend_cube)
                self._mc_rotate(cube_obj, cube.rotation)

        return armature

    def _add_bone(
        self, edit_bones: ArmatureEditBones, length: float, import_bone: ImportBone
    ):
        """添加骨骼到骨架"""
        if import_bone.blend_empty is None:
            raise ValueError("骨骼空对象不存在")

        matrix_world = import_bone.blend_empty.matrix_world
        bone = edit_bones.new(import_bone.name)
        bone.head = cast(List[float], (0.0, 0.0, 0.0))
        bone.tail = cast(List[float], (0.0, length, 0.0))
        bone.matrix = matrix_world

    def _parent_to_bone(
        self, context: bpy.types.Context, obj: Object, armature: Object, bone_name: str
    ):
        """将对象绑定到骨骼"""
        context.view_layer.update()
        parent_inverse = obj.matrix_parent_inverse.copy()

        obj.parent = armature
        obj.parent_bone = bone_name
        obj.parent_type = "BONE"
        obj.matrix_parent_inverse = parent_inverse

        # 修正骨骼尾部偏移
        context.view_layer.update()
        blend_bone = armature.pose.bones[bone_name]
        correction = mathutils.Matrix.Translation(blend_bone.head - blend_bone.tail)
        obj.matrix_world = correction @ obj.matrix_world

    # ==========================================================================
    # 坐标变换函数
    # ==========================================================================

    @staticmethod
    def _swap_yz(vec: Vector3d) -> Tuple[float, float, float]:
        """将 Minecraft 坐标 [X, Y, Z] 转换为 Blender 坐标 [X, Z, Y]"""
        return (vec[0], vec[2], vec[1])

    def _mc_translate(
        self, obj: Object, mc_translation: Vector3d, mc_size: Vector3d, mc_pivot: Vector3d
    ):
        """应用 Minecraft 坐标平移"""
        assert isinstance(obj.data, Mesh)
        # 转换坐标系并缩放
        pivot = self._swap_yz(mc_pivot)
        size = self._swap_yz(mc_size)
        trans = self._swap_yz(mc_translation)

        pivot_offset = mathutils.Vector((
            pivot[0] / MINECRAFT_SCALE_FACTOR,
            pivot[1] / MINECRAFT_SCALE_FACTOR,
            pivot[2] / MINECRAFT_SCALE_FACTOR,
        ))
        size_offset = mathutils.Vector((
            size[0] / 2 / MINECRAFT_SCALE_FACTOR,
            size[1] / 2 / MINECRAFT_SCALE_FACTOR,
            size[2] / 2 / MINECRAFT_SCALE_FACTOR,
        ))
        translation = mathutils.Vector((
            trans[0] / MINECRAFT_SCALE_FACTOR,
            trans[1] / MINECRAFT_SCALE_FACTOR,
            trans[2] / MINECRAFT_SCALE_FACTOR,
        ))
        for vertex in obj.data.vertices:
            vertex.co += translation - pivot_offset + size_offset

    def _mc_set_size(self, obj: Object, mc_size: Vector3d, inflate: float = 0.0):
        """设置立方体大小"""
        assert isinstance(obj.data, Mesh)
        effective_inflate = inflate / MINECRAFT_SCALE_FACTOR

        # 转换坐标系: [X, Y, Z] -> [X, Z, Y]
        size = self._swap_yz(mc_size)
        dx = size[0] / 2 / MINECRAFT_SCALE_FACTOR + effective_inflate
        dy = size[1] / 2 / MINECRAFT_SCALE_FACTOR + effective_inflate
        dz = size[2] / 2 / MINECRAFT_SCALE_FACTOR + effective_inflate

        vertices = obj.data.vertices
        # 0. ---; 1. --+; 2. -+-; 3. -++; 4. +--; 5. +-+; 6. ++-; 7. +++
        vertices[0].co = mathutils.Vector((-dx, -dy, -dz))
        vertices[1].co = mathutils.Vector((-dx, -dy, dz))
        vertices[2].co = mathutils.Vector((-dx, dy, -dz))
        vertices[3].co = mathutils.Vector((-dx, dy, dz))
        vertices[4].co = mathutils.Vector((dx, -dy, -dz))
        vertices[5].co = mathutils.Vector((dx, -dy, dz))
        vertices[6].co = mathutils.Vector((dx, dy, -dz))
        vertices[7].co = mathutils.Vector((dx, dy, dz))

    def _mc_pivot(self, obj: Object, mc_pivot: Vector3d):
        """设置枢轴点"""
        pivot = self._swap_yz(mc_pivot)
        translation = mathutils.Vector((
            pivot[0] / MINECRAFT_SCALE_FACTOR,
            pivot[1] / MINECRAFT_SCALE_FACTOR,
            pivot[2] / MINECRAFT_SCALE_FACTOR,
        ))
        obj.location += translation

    def _mc_rotate(self, obj: Object, mc_rotation: Vector3d):
        """应用 Minecraft 旋转"""
        # 转换坐标系: [X, Y, Z] -> [X, Z, -Y]
        rot_rad = (
            mc_rotation[0] * math.pi / 180,
            mc_rotation[2] * math.pi / 180,
            -mc_rotation[1] * math.pi / 180,
        )
        rotation = mathutils.Euler(rot_rad, "XZY")
        obj.rotation_euler.rotate(rotation)

    # ==========================================================================
    # UV 设置
    # ==========================================================================

    def _set_cube_uv(self, cube_obj: Object, cube: ImportCube):
        """设置立方体的 UV 映射"""
        mesh = cube_obj.data
        if not isinstance(mesh, Mesh):
            return

        uv_layer = mesh.uv_layers.active
        if uv_layer is None:
            return

        # 获取立方体的多边形面
        # Blender 立方体面的顺序: 前(+Y), 后(-Y), 右(+X), 左(-X), 上(+Z), 下(-Z)
        # Minecraft 面的顺序: north(-Y), south(+Y), east(+X), west(-X), up(+Z), down(-Z)
        face_mapping = {
            0: "south",  # +Y
            1: "north",  # -Y
            2: "east",   # +X
            3: "west",   # -X
            4: "up",     # +Z
            5: "down",   # -Z
        }

        for poly_idx, polygon in enumerate(mesh.polygons):
            if poly_idx >= 6:
                break

            face_name = face_mapping.get(poly_idx)
            if face_name is None or face_name not in cube.uv:
                continue

            uv_data = cube.uv[face_name]
            uv_pos = uv_data.get("uv", [0, 0])
            uv_size = uv_data.get("uv_size", [0, 0])

            # 计算四个角的 UV 坐标
            # 左下, 右下, 右上, 左上
            corners = [
                (uv_pos[0], uv_pos[1] + uv_size[1]),
                (uv_pos[0] + uv_size[0], uv_pos[1] + uv_size[1]),
                (uv_pos[0] + uv_size[0], uv_pos[1]),
                (uv_pos[0], uv_pos[1]),
            ]

            # 应用 UV 到循环
            for i, loop_idx in enumerate(polygon.loop_indices):
                if i < 4:
                    uv_layer.data[loop_idx].uv = self.uv_converter.convert(corners[i])


# ============================================================================
# 导入函数
# ============================================================================

def import_bedrock_model(
    data: Dict[str, Any], geometry_name: str, context: bpy.types.Context
) -> Object:
    """
    导入 Bedrock 模型

    :param data: JSON 数据
    :param geometry_name: 几何体名称（可为空）
    :param context: Blender 上下文
    :returns: 骨架对象
    """
    model_loader = ModelLoader(data, geometry_name)
    geometry = ImportGeometry(model_loader)
    armature = geometry.build_with_armature(context)

    # 设置骨架名称
    if geometry.identifier.startswith("geometry."):
        armature.name = geometry.identifier[9:]
    else:
        armature.name = geometry.identifier

    return armature


# ============================================================================
# Blender 操作符
# ============================================================================

class IMPORT_OT_bedrock_model(bpy.types.Operator, ImportHelper):
    """导入 Bedrock/Blockbench 模型文件"""

    bl_idname = "import_mesh.bedrock_model"
    bl_label = "Import Bedrock Model bench"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".json"
    filter_glob: StringProperty(
        default="*.json;*.geo.json",
        options={"HIDDEN"},
    )

    geometry_name: StringProperty(
        name="Geometry Name",
        description="要导入的几何体名称（留空则导入第一个）",
        default="",
    )

    def execute(self, context):
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                data = json.load(f)

            armature = import_bedrock_model(data, self.geometry_name, context)
            self.report({"INFO"}, f"成功导入模型: {armature.name}")
            return {"FINISHED"}

        except ImporterException as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        except json.JSONDecodeError as e:
            self.report({"ERROR"}, f"JSON 解析错误: {e}")
            return {"CANCELLED"}
        except Exception as e:
            self.report({"ERROR"}, f"导入失败: {e}")
            return {"CANCELLED"}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}


# ============================================================================
# 菜单和注册
# ============================================================================

def menu_func_import(self, context):
    self.layout.operator(
        IMPORT_OT_bedrock_model.bl_idname, text="Bedrock Model (.json)"
    )


classes = (IMPORT_OT_bedrock_model,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
