"""
Bedrock/Blockbench 动画导出器
导出选中骨架的关键帧数据为 Minecraft Bedrock Edition 动画格式
"""

import bpy
import json
import math
from decimal import Decimal
from typing import Dict, List, Any, Optional, Tuple
from bpy.props import StringProperty, BoolProperty, EnumProperty
from bpy_extras.io_utils import ExportHelper


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
    将 Blender 欧拉角（弧度）转换为 Minecraft 旋转（度数）
    Blender: X右, Y前, Z上 (XYZ 欧拉)
    Minecraft: X右, Y上, Z前 (XYZ 度数)
    """
    # 将弧度转换为度数
    degrees = [math.degrees(r) for r in rot_euler]
    # 坐标轴转换: Blender XYZ -> Minecraft XZY (负Y)
    return [
        degrees[0],   # X -> X
        degrees[2],   # Z -> Y
        -degrees[1],  # Y -> -Z
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
            if f'pose.bones["{bone_name}"]' not in fcurve.data_path:
                continue

            # 确定变换类型
            transform_type = None
            if '.location' in fcurve.data_path:
                transform_type = 'location'
            elif '.rotation' in fcurve.data_path:
                transform_type = 'rotation'
            elif '.scale' in fcurve.data_path:
                transform_type = 'scale'

            if transform_type:
                for keyframe in fcurve.keyframe_points:
                    keyframes[transform_type].add(int(keyframe.co[0]))

        return keyframes

    def get_all_keyframes(self) -> set:
        """获取所有骨骼的所有关键帧帧号"""
        all_keyframes = set()

        if not self.armature.animation_data or not self.armature.animation_data.action:
            return all_keyframes

        action = self.armature.animation_data.action

        for fcurve in action.fcurves:
            for keyframe in fcurve.keyframe_points:
                all_keyframes.add(int(keyframe.co[0]))

        return all_keyframes

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

        # 获取旋转（转换为欧拉角）
        if pose_bone.rotation_mode == 'QUATERNION':
            rotation = pose_bone.rotation_quaternion.to_euler('XYZ')
        elif pose_bone.rotation_mode == 'AXIS_ANGLE':
            rotation = pose_bone.rotation_axis_angle.to_euler('XYZ')
        else:
            rotation = pose_bone.rotation_euler.copy()

        scale = pose_bone.scale.copy()

        return {
            'location': location,
            'rotation': rotation,
            'scale': scale
        }

    def get_rest_pose_transform(self, bone_name: str) -> Optional[Dict]:
        """获取骨骼的静止姿态变换"""
        # 设置到第1帧并重置姿态来获取静止姿态
        bpy.context.scene.frame_set(1)
        bpy.context.view_layer.update()

        if bone_name not in self.armature.pose.bones:
            return None

        # 静止姿态的默认值
        return {
            'location': [0.0, 0.0, 0.0],
            'rotation': [0.0, 0.0, 0.0],
            'scale': [1.0, 1.0, 1.0]
        }

    def is_value_at_rest(self, value: List[float], value_type: str) -> bool:
        """检查值是否处于静止状态"""
        threshold = 0.0001
        if value_type == 'scale':
            # 缩放的静止值是 [1, 1, 1]
            return all(abs(v - 1.0) < threshold for v in value)
        else:
            # 位置和旋转的静止值是 [0, 0, 0]
            return all(abs(v) < threshold for v in value)

    def export_bone_animation(self, bone_name: str, frame_start: int, frame_end: int,
                              skip_rest_poses: bool = True) -> Dict[str, Any]:
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
                    if not skip_rest_poses or not self.is_value_at_rest(mc_loc, 'location'):
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
                    if not skip_rest_poses or not self.is_value_at_rest(mc_rot, 'rotation'):
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
                    if not skip_rest_poses or not self.is_value_at_rest(mc_scale, 'scale'):
                        scale_data[timestamp] = get_vector_json(mc_scale)

            if scale_data:
                bone_data['scale'] = scale_data

        return bone_data

    def export_animation(self, animation_name: str, loop: bool = False,
                        skip_rest_poses: bool = True) -> Dict[str, Any]:
        """导出完整动画"""
        frame_start = bpy.context.scene.frame_start
        frame_end = bpy.context.scene.frame_end

        # 计算动画长度（秒）
        animation_length = (frame_end - frame_start) / self.fps

        # 保存当前帧
        original_frame = bpy.context.scene.frame_current

        try:
            # 停止动画播放
            bpy.ops.screen.animation_cancel()

            bones_data = {}

            # 遍历所有骨骼
            for pose_bone in self.armature.pose.bones:
                bone_name = pose_bone.name
                bone_animation = self.export_bone_animation(
                    bone_name, frame_start, frame_end, skip_rest_poses)

                if bone_animation:  # 只添加有动画数据的骨骼
                    bones_data[bone_name] = bone_animation

            # 构建动画数据结构
            animation_data = {
                'animation_length': round(animation_length, ANIMATION_TIMESTAMP_PRECISION),
                'bones': bones_data
            }

            if loop:
                animation_data['loop'] = True

            return animation_data

        finally:
            # 恢复原始帧
            bpy.context.scene.frame_set(original_frame)

    def export_to_json(self, animation_name: str, loop: bool = False,
                      skip_rest_poses: bool = True,
                      existing_data: Optional[Dict] = None) -> Dict[str, Any]:
        """导出为 Minecraft 动画 JSON 格式"""
        # 如果有现有数据，合并到其中
        if existing_data and 'animations' in existing_data:
            result = existing_data
        else:
            result = {
                'format_version': '1.8.0',
                'animations': {}
            }

        # 添加 animation. 前缀（如果没有）
        if not animation_name.startswith('animation.'):
            full_name = f'animation.{animation_name}'
        else:
            full_name = animation_name

        # 导出动画数据
        animation_data = self.export_animation(animation_name, loop, skip_rest_poses)
        result['animations'][full_name] = animation_data

        return result


class EXPORT_OT_bedrock_anim(bpy.types.Operator, ExportHelper):
    """导出选中骨架的动画为 Bedrock/Blockbench 格式"""

    bl_idname = "export_anim.bedrock"
    bl_label = "Export Bedrock Animation"
    bl_options = {'REGISTER'}
    bl_description = "导出选中骨架的动画为 Minecraft Bedrock Edition 动画格式"

    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})

    animation_name: StringProperty(
        name="动画名称",
        description="动画名称（将自动添加 'animation.' 前缀）",
        default="my_animation"
    )

    loop_animation: BoolProperty(
        name="循环播放",
        description="是否设置动画为循环播放",
        default=False
    )

    skip_rest_poses: BoolProperty(
        name="跳过静止姿态",
        description="不导出处于静止状态的骨骼数据",
        default=True
    )

    append_to_file: BoolProperty(
        name="追加到文件",
        description="如果文件已存在，将动画追加到文件中而不是覆盖",
        default=False
    )

    export_action: EnumProperty(
        name="导出动作",
        description="选择要导出的动作",
        items=lambda self, context: self._get_action_items(context)
    )

    def _get_action_items(self, context):
        """获取可用的动作列表"""
        items = []
        obj = context.active_object
        if obj and obj.type == 'ARMATURE' and obj.animation_data:
            # 当前活动的 Action
            if obj.animation_data.action:
                action = obj.animation_data.action
                items.append((action.name, f"当前: {action.name}", "当前活动的动作"))

            # 所有可用的 Actions
            for action in bpy.data.actions:
                if action.name not in [i[0] for i in items]:
                    items.append((action.name, action.name, ""))

        if not items:
            items.append(('NONE', "无可用动作", ""))

        return items

    @classmethod
    def poll(cls, context):
        """检查是否可以执行导出"""
        obj = context.active_object
        if obj is None:
            return False
        if obj.type != 'ARMATURE':
            return False
        if not obj.animation_data:
            return False
        return True

    def invoke(self, context, event):
        """初始化导出对话框"""
        obj = context.active_object

        # 设置默认动画名称为当前 Action 名称
        if obj and obj.animation_data and obj.animation_data.action:
            action_name = obj.animation_data.action.name
            # 如果已经有 animation. 前缀，去掉它
            if action_name.startswith('animation.'):
                action_name = action_name[10:]
            self.animation_name = action_name

        # 设置默认文件名
        self.filepath = f"{self.animation_name}.animation.json"

        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def draw(self, context):
        """绘制导出选项面板"""
        layout = self.layout

        obj = context.active_object
        if obj and obj.type == 'ARMATURE':
            layout.label(text=f"骨架: {obj.name}", icon='ARMATURE_DATA')

            if obj.animation_data and obj.animation_data.action:
                layout.label(text=f"当前动作: {obj.animation_data.action.name}", icon='ACTION')

        layout.separator()

        layout.prop(self, "export_action")
        layout.prop(self, "animation_name")
        layout.prop(self, "loop_animation")
        layout.prop(self, "skip_rest_poses")
        layout.prop(self, "append_to_file")

    def execute(self, context):
        """执行导出"""
        obj = context.active_object

        if obj is None or obj.type != 'ARMATURE':
            self.report({'ERROR'}, "请选中一个骨架对象")
            return {'CANCELLED'}

        if not obj.animation_data:
            self.report({'ERROR'}, "骨架没有动画数据")
            return {'CANCELLED'}

        # 切换到选中的 Action
        if self.export_action and self.export_action != 'NONE':
            if self.export_action in bpy.data.actions:
                obj.animation_data.action = bpy.data.actions[self.export_action]

        if not obj.animation_data.action:
            self.report({'ERROR'}, "没有活动的动作可以导出")
            return {'CANCELLED'}

        try:
            # 读取现有文件（如果需要追加）
            existing_data = None
            if self.append_to_file:
                try:
                    with open(self.filepath, 'r', encoding='utf-8') as f:
                        existing_data = json.load(f)
                except (FileNotFoundError, json.JSONDecodeError):
                    pass

            # 创建导出器
            exporter = BBAnimExporter(obj)

            # 导出动画
            result = exporter.export_to_json(
                self.animation_name,
                loop=self.loop_animation,
                skip_rest_poses=self.skip_rest_poses,
                existing_data=existing_data
            )

            # 写入文件
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump(result, f, indent=2, ensure_ascii=False)

            # 统计导出的骨骼数量
            anim_key = f"animation.{self.animation_name}" if not self.animation_name.startswith('animation.') else self.animation_name
            bones_count = len(result['animations'].get(anim_key, {}).get('bones', {}))

            self.report({'INFO'}, f"动画已导出到 {self.filepath}（{bones_count} 个骨骼）")
            return {'FINISHED'}

        except Exception as e:
            self.report({'ERROR'}, f"导出失败: {e}")
            return {'CANCELLED'}


class EXPORT_OT_bedrock_anim_batch(bpy.types.Operator, ExportHelper):
    """批量导出所有动作为 Bedrock/Blockbench 格式"""

    bl_idname = "export_anim.bedrock_batch"
    bl_label = "Batch Export Bedrock Animations"
    bl_options = {'REGISTER'}
    bl_description = "批量导出骨架的所有动作为 Minecraft Bedrock Edition 动画格式"

    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})

    loop_animation: BoolProperty(
        name="循环播放",
        description="是否设置所有动画为循环播放",
        default=False
    )

    skip_rest_poses: BoolProperty(
        name="跳过静止姿态",
        description="不导出处于静止状态的骨骼数据",
        default=True
    )

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

        layout.separator()
        layout.prop(self, "loop_animation")
        layout.prop(self, "skip_rest_poses")

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
                animation_data = exporter.export_animation(
                    anim_name,
                    loop=self.loop_animation,
                    skip_rest_poses=self.skip_rest_poses
                )

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


def menu_func_export_batch(self, context):
    self.layout.operator(
        EXPORT_OT_bedrock_anim_batch.bl_idname, text="Bedrock Animations (Batch)"
    )


classes = (
    EXPORT_OT_bedrock_anim,
    EXPORT_OT_bedrock_anim_batch,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export_batch)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export_batch)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
