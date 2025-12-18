"""
Bedrock/Blockbench 动画导出器
导出选中骨架的关键帧数据为 Minecraft Bedrock Edition 动画格式
"""

import bpy
import json
import math
from decimal import Decimal
from typing import Dict, List, Any, Optional
from bpy.props import StringProperty
from bpy_extras.io_utils import ExportHelper
from mathutils import Quaternion


# 动画时间戳精度（小数位数）
ANIMATION_TIMESTAMP_PRECISION = 4
# Minecraft 缩放因子：Blender 1米 = Minecraft 16单位
MINECRAFT_SCALE_FACTOR = 16.0


def frame_to_timestamp(frame: int, fps: float) -> str:
    """将帧数转换为秒数时间戳字符串（帧1对应时间0）"""
    timestamp = Decimal((frame - 1) / fps)
    result = round(timestamp, ANIMATION_TIMESTAMP_PRECISION)
    # 使用 normalize() 移除尾随零，但确保整数时返回 "0" 而不是科学计数法
    normalized = result.normalize()
    return str(normalized)


def get_vector_json(vec) -> List[float]:
    """将向量转换为 JSON 格式的列表，四舍五入到合理精度"""
    return [round(v, 6) for v in vec]


def convert_location_to_minecraft(loc) -> List[float]:
    """
    将 Blender 位置转换为 Minecraft 坐标系
    Blender: X右, Y前, Z上
    Minecraft: X右, Y上, Z前
    """
    return [
        loc[0] * MINECRAFT_SCALE_FACTOR,  # X -> X
        loc[2] * MINECRAFT_SCALE_FACTOR,  # Z -> Y (上)
        loc[1] * MINECRAFT_SCALE_FACTOR,  # Y -> Z (前)
    ]


def convert_rotation_to_minecraft(rot_euler) -> List[float]:
    """
    将 Blender 欧拉角（弧度，XZY顺序）转换为 Minecraft 旋转（度数）

    导入时的转换 (import_bedrock_anim.py):
        Minecraft [X, Y, Z] -> convert_rotation_axis -> [X, Z, -Y]
        然后用 Euler([X, Z, -Y], "XZY") 创建

        所以 Blender euler 内部:
        euler.x = MC_X
        euler.y = MC_Z  (导入时 rotation[2] 放到位置1)
        euler.z = -MC_Y (导入时 -rotation[1] 放到位置2)

    导出是逆操作:
        MC_X = euler.x
        MC_Y = -euler.z (因为 euler.z = -MC_Y)
        MC_Z = euler.y
    """
    # 将弧度转换为度数
    degrees = [math.degrees(r) for r in rot_euler]
    # 还原 Minecraft 格式 [X, Y, Z]
    return [
        degrees[0],   # euler.x -> Minecraft X
        -degrees[2],  # -euler.z -> Minecraft Y
        degrees[1],   # euler.y -> Minecraft Z
    ]


class BBAnimExporter:
    """Bedrock/Blockbench 动画导出器"""

    def __init__(self, armature):
        self.armature = armature
        self.fps = bpy.context.scene.render.fps / bpy.context.scene.render.fps_base

    def get_bone_keyframes(self, bone_name: str) -> Dict[str, set]:
        """获取指定骨骼的所有关键帧帧号，按变换类型分类"""
        keyframes = {
            'location': set(),
            'rotation': set(),
            'scale': set()
        }

        if not self.armature.animation_data or not self.armature.animation_data.action:
            return keyframes

        action = self.armature.animation_data.action

        for fcurve in action.fcurves:
            # 解析数据路径，例如: pose.bones["bone_name"].location
            # 支持两种引号格式
            if f'pose.bones["{bone_name}"]' not in fcurve.data_path and \
               f"pose.bones['{bone_name}']" not in fcurve.data_path:
                continue

            # 确定变换类型
            transform_type = None
            if '.location' in fcurve.data_path:
                transform_type = 'location'
            elif '.rotation_quaternion' in fcurve.data_path:
                transform_type = 'rotation'
            elif '.rotation_euler' in fcurve.data_path:
                transform_type = 'rotation'
            elif '.rotation_axis_angle' in fcurve.data_path:
                transform_type = 'rotation'
            elif '.scale' in fcurve.data_path:
                transform_type = 'scale'

            if transform_type:
                for keyframe in fcurve.keyframe_points:
                    keyframes[transform_type].add(int(keyframe.co[0]))

        return keyframes

    def sample_bone_transform_at_frame(self, bone_name: str, frame: int) -> Optional[Dict]:
        """在指定帧采样骨骼的变换数据"""
        if bone_name not in self.armature.pose.bones:
            return None

        pose_bone = self.armature.pose.bones[bone_name]

        # 设置当前帧
        bpy.context.scene.frame_set(frame)
        bpy.context.view_layer.update()

        # 获取局部变换
        location = pose_bone.location.copy()

        # 获取旋转（转换为欧拉角，使用 XZY 顺序以匹配 mcblend/Minecraft）
        if pose_bone.rotation_mode == 'QUATERNION':
            rotation = pose_bone.rotation_quaternion.to_euler('XZY')
        elif pose_bone.rotation_mode == 'AXIS_ANGLE':
            # axis_angle 格式: (angle, x, y, z)
            aa = pose_bone.rotation_axis_angle
            axis = (aa[1], aa[2], aa[3])
            angle = aa[0]
            quat = Quaternion(axis, angle)
            rotation = quat.to_euler('XZY')
        else:
            # 如果原本是其他欧拉模式，先转成四元数再转 XZY
            rotation = pose_bone.rotation_euler.to_quaternion().to_euler('XZY')

        scale = pose_bone.scale.copy()

        return {
            'location': location,
            'rotation': rotation,
            'scale': scale
        }

    def export_bone_animation(self, bone_name: str, frame_start: int, frame_end: int) -> Dict[str, Any]:
        """导出单个骨骼的动画数据"""
        bone_data = {}

        keyframes = self.get_bone_keyframes(bone_name)

        # 收集位置关键帧
        if keyframes['location']:
            position_data = {}
            for frame in sorted(keyframes['location']):
                if frame < frame_start or frame > frame_end:
                    continue
                transform = self.sample_bone_transform_at_frame(bone_name, frame)
                if transform:
                    mc_loc = convert_location_to_minecraft(transform['location'])
                    timestamp = frame_to_timestamp(frame, self.fps)
                    position_data[timestamp] = get_vector_json(mc_loc)

            if position_data:
                bone_data['position'] = position_data

        # 收集旋转关键帧
        if keyframes['rotation']:
            rotation_data = {}
            for frame in sorted(keyframes['rotation']):
                if frame < frame_start or frame > frame_end:
                    continue
                transform = self.sample_bone_transform_at_frame(bone_name, frame)
                if transform:
                    mc_rot = convert_rotation_to_minecraft(transform['rotation'])
                    timestamp = frame_to_timestamp(frame, self.fps)
                    rotation_data[timestamp] = get_vector_json(mc_rot)

            if rotation_data:
                bone_data['rotation'] = rotation_data

        # 收集缩放关键帧
        if keyframes['scale']:
            scale_data = {}
            for frame in sorted(keyframes['scale']):
                if frame < frame_start or frame > frame_end:
                    continue
                transform = self.sample_bone_transform_at_frame(bone_name, frame)
                if transform:
                    # 缩放不需要坐标轴转换，但需要重排序
                    mc_scale = [
                        transform['scale'][0],  # X
                        transform['scale'][2],  # Z -> Y
                        transform['scale'][1],  # Y -> Z
                    ]
                    timestamp = frame_to_timestamp(frame, self.fps)
                    scale_data[timestamp] = get_vector_json(mc_scale)

            if scale_data:
                bone_data['scale'] = scale_data

        return bone_data

    def export_animation(self, animation_name: str) -> Dict[str, Any]:
        """导出完整动画"""
        frame_start = bpy.context.scene.frame_start
        frame_end = bpy.context.scene.frame_end

        # 计算动画长度（秒）
        # mcblend 使用 (frame_end - 1) / fps，因为帧1对应时间0
        animation_length = (frame_end - 1) / self.fps

        # 保存当前帧
        original_frame = bpy.context.scene.frame_current

        try:
            # 停止动画播放
            bpy.ops.screen.animation_cancel()

            bones_data = {}

            # 遍历所有骨骼
            for pose_bone in self.armature.pose.bones:
                bone_name = pose_bone.name
                bone_animation = self.export_bone_animation(bone_name, frame_start, frame_end)

                if bone_animation:  # 只添加有动画数据的骨骼
                    bones_data[bone_name] = bone_animation

            # 构建动画数据结构
            animation_data = {
                'animation_length': round(animation_length, ANIMATION_TIMESTAMP_PRECISION),
                'bones': bones_data
            }

            return animation_data

        finally:
            # 恢复原始帧
            bpy.context.scene.frame_set(original_frame)


class EXPORT_OT_bedrock_anim(bpy.types.Operator, ExportHelper):
    """批量导出所有动作为 Bedrock/Blockbench 格式"""

    bl_idname = "export_anim.bedrock"
    bl_label = "Export Bedrock Animations"
    bl_options = {'REGISTER'}
    bl_description = "批量导出骨架的所有动作为 Minecraft Bedrock Edition 动画格式"

    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        """检查是否可以执行导出"""
        obj = context.active_object
        if obj is None:
            return False
        if obj.type != 'ARMATURE':
            return False
        # 检查是否有任何 Action
        return len(bpy.data.actions) > 0

    def invoke(self, context, event):
        """初始化导出对话框"""
        obj = context.active_object
        if obj:
            self.filepath = f"{obj.name}_animations.json"
        else:
            self.filepath = "animations.json"

        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def draw(self, context):
        """绘制导出选项面板"""
        layout = self.layout

        obj = context.active_object
        if obj and obj.type == 'ARMATURE':
            layout.label(text=f"骨架: {obj.name}", icon='ARMATURE_DATA')

        # 显示将要导出的动作列表
        box = layout.box()
        box.label(text="将导出以下动作:")
        for action in bpy.data.actions:
            row = box.row()
            row.label(text=action.name, icon='ACTION')
        box.label(text=f"共 {len(bpy.data.actions)} 个动作", icon='INFO')

    def execute(self, context):
        """执行批量导出"""
        obj = context.active_object

        if obj is None or obj.type != 'ARMATURE':
            self.report({'ERROR'}, "请选中一个骨架对象")
            return {'CANCELLED'}

        if not obj.animation_data:
            obj.animation_data_create()

        try:
            result = {
                'format_version': '1.8.0',
                'animations': {}
            }

            original_action = obj.animation_data.action
            exported_count = 0

            # 导出所有动作
            for action in bpy.data.actions:
                # 设置当前动作
                obj.animation_data.action = action

                # 创建导出器
                exporter = BBAnimExporter(obj)

                # 获取动画名称
                anim_name = action.name
                if anim_name.startswith('animation.'):
                    anim_name = anim_name[10:]

                # 导出动画
                animation_data = exporter.export_animation(anim_name)

                full_name = f'animation.{anim_name}'
                result['animations'][full_name] = animation_data
                exported_count += 1

            # 恢复原始动作
            obj.animation_data.action = original_action

            # 写入文件
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump(result, f, indent=2, ensure_ascii=False)

            self.report({'INFO'}, f"已导出 {exported_count} 个动画到 {self.filepath}")
            return {'FINISHED'}

        except Exception as e:
            self.report({'ERROR'}, f"导出失败: {e}")
            return {'CANCELLED'}


def menu_func_export(self, context):
    self.layout.operator(
        EXPORT_OT_bedrock_anim.bl_idname, text="Bedrock Animation (.json)"
    )


classes = (EXPORT_OT_bedrock_anim,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
