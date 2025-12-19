import bpy
import json
from mathutils import Euler, Vector
import math
from decimal import Decimal, ROUND_HALF_UP
from bpy.props import StringProperty
from bpy_extras.io_utils import ImportHelper


class BBAnimImporter:
    """Bedrock/Blockbench 动画导入器"""

    def __init__(self, armature_name):
        self.armature_name = armature_name
        self.armature = None
        self.fps = 24
        self.position_scale = 1.0 / 16.0  # Blockbench: 16单位 = 1米
        self.timestamp_precision = 2

    def load_animation_file(self, filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)

    def setup_armature(self):
        self.armature = bpy.data.objects[self.armature_name]
        bpy.context.view_layer.objects.active = self.armature
        bpy.ops.object.mode_set(mode="POSE")

    def reset_pose(self):
        """重置骨骼姿态到默认状态"""
        for bone in self.armature.pose.bones:
            bone.rotation_mode = "QUATERNION"
            bone.rotation_quaternion = (1, 0, 0, 0)
            bone.location = (0, 0, 0)

    def create_action_for_animation(self, animation_name):
        """为动画创建一个新的 Action"""
        if not self.armature.animation_data:
            self.armature.animation_data_create()

        # 创建新 Action，使用动画名称
        action = bpy.data.actions.new(name=animation_name)
        self.armature.animation_data.action = action
        print(f"创建新Action: {action.name}")
        return action

    def degrees_to_radians(self, degrees_array):
        return [math.radians(deg) for deg in degrees_array]

    def t_to_frame(self, time_float):
        time_decimal = Decimal(str(time_float))
        fps_decimal = Decimal(str(self.fps))
        frame_decimal = time_decimal * fps_decimal + Decimal("1")
        frame = int(frame_decimal.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        return frame

    def convert_rotation_axis(self, rotation):
        """将 Blockbench 旋转轴转换为 Blender 坐标系"""
        return [rotation[0], rotation[2], -rotation[1]]

    def convert_position_axis(self, position):
        """将 Blockbench 位置轴转换为 Blender 坐标系（含缩放）"""
        return [
            position[0] * self.position_scale,
            position[2] * self.position_scale,
            position[1] * self.position_scale,
        ]

    def parse_keyframe_value(self, value):
        """解析关键帧值，支持直接数组或带 post/pre 的字典格式"""
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return value.get("post") or value.get("pre") or [0, 0, 0]
        return [0, 0, 0]

    def process_bone_animation_data(self, bone_name, bone_data, animation_length):
        if "rotation" in bone_data:
            rotation_data = bone_data["rotation"]
            if isinstance(rotation_data, list):
                frame = 1
                rotation = rotation_data
                self.set_keyframe(bone_name, frame, rotation=rotation)
                end_frame = self.t_to_frame(animation_length)
                self.set_keyframe(bone_name, end_frame, rotation=rotation)
            elif isinstance(rotation_data, dict):
                for time_str, rotation_value in rotation_data.items():
                    time_float = float(time_str)
                    frame = self.t_to_frame(time_float)
                    rotation = self.parse_keyframe_value(rotation_value)
                    self.set_keyframe(bone_name, frame, rotation=rotation)

        if "position" in bone_data:
            position_data = bone_data["position"]
            if isinstance(position_data, list):
                frame = 1
                position = position_data
                self.set_keyframe(bone_name, frame, position=position)
                end_frame = self.t_to_frame(animation_length)
                self.set_keyframe(bone_name, end_frame, position=position)
            elif isinstance(position_data, dict):
                for time_str, position_value in position_data.items():
                    time_float = float(time_str)
                    frame = self.t_to_frame(time_float)
                    position = self.parse_keyframe_value(position_value)
                    self.set_keyframe(bone_name, frame, position=position)

    def set_keyframe(self, bone_name, frame, rotation=None, position=None):
        if bone_name not in self.armature.pose.bones:
            print(f"警告: 骨骼 {bone_name} 不存在")
            return

        bone = self.armature.pose.bones[bone_name]
        bpy.context.scene.frame_set(frame)

        if rotation is not None:
            bone.rotation_mode = "QUATERNION"
            rot_rad = self.degrees_to_radians(rotation)
            converted_rot = self.convert_rotation_axis(rot_rad)
            euler = Euler(converted_rot, "XZY")
            bone.rotation_quaternion = euler.to_quaternion()

        if position is not None:
            converted_pos = self.convert_position_axis(position)
            bone.location = Vector(converted_pos)

        bpy.context.view_layer.update()

        if rotation is not None:
            bone.keyframe_insert(
                data_path="rotation_quaternion", frame=frame, group=bone_name
            )

        if position is not None:
            bone.keyframe_insert(data_path="location", frame=frame, group=bone_name)

    def import_animation(self, animation_data, animation_name):
        """导入单个动画到一个新的 Action"""
        print(f"开始导入动画: {animation_name}")

        # 重置骨骼姿态
        self.reset_pose()

        # 为这个动画创建新的 Action
        action = self.create_action_for_animation(animation_name)

        animation_length = animation_data.get("animation_length", 1.0)
        end_frame = self.t_to_frame(animation_length)
        print(f"动画长度: {animation_length} 秒, {end_frame} 帧")

        bones_data = animation_data.get("bones", {})
        print(f"处理 {len(bones_data)} 个骨骼的动画数据")

        for bone_name, bone_data in bones_data.items():
            print(f"\n处理骨骼: {bone_name}")
            if bone_name in self.armature.pose.bones:
                self.process_bone_animation_data(bone_name, bone_data, animation_length)
            else:
                print(f"  跳过: 骨骼 {bone_name} 不存在于armature中")

        # 设置 Action 的帧范围
        action.frame_start = 1
        action.frame_end = end_frame

        print(f"动画 {animation_name} 导入完成!")
        return action

    def list_animations(self, filepath):
        data = self.load_animation_file(filepath)
        animations = data.get("animations", {})
        return list(animations.keys())

    def import_specific_animation(self, filepath, animation_name):
        data = self.load_animation_file(filepath)
        animations = data.get("animations", {})
        if animation_name in animations:
            anim_data = animations[animation_name]
            self.import_animation(anim_data, animation_name)
            return True
        return False

    def import_all_animations(self, filepath):
        """导入文件中的所有动画，每个动画作为单独的 Action"""
        data = self.load_animation_file(filepath)
        animations = data.get("animations", {})

        if not animations:
            print("文件中没有找到动画")
            return []

        self.setup_armature()

        imported_actions = []
        animation_names = list(animations.keys())
        print(f"找到 {len(animation_names)} 个动画，开始导入...")

        for animation_name in animation_names:
            anim_data = animations[animation_name]
            action = self.import_animation(anim_data, animation_name)
            imported_actions.append(action)

        # 将最后导入的动画设为当前活动动画
        if imported_actions:
            self.armature.animation_data.action = imported_actions[-1]

        # 重置骨骼姿态
        self.reset_pose()

        print(f"\n全部导入完成！共导入 {len(imported_actions)} 个动画")
        return imported_actions


class IMPORT_OT_bedrock_anim(bpy.types.Operator, ImportHelper):
    """导入 Bedrock/Blockbench 动画文件（导入所有动画到当前选中的骨架）"""

    bl_idname = "import_anim.bedrock"
    bl_label = "Bedrock Animation (.json)"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json;*.animation.json", options={"HIDDEN"})

    def _validate_armature_selection(self, context):
        """验证是否选中了有效的骨架对象，返回 (armature, error_message)"""
        obj = context.active_object
        if obj is None:
            return None, "请先选中一个骨架对象"
        if obj.type != "ARMATURE":
            return None, f"选中的对象 '{obj.name}' 不是骨架，请选中一个骨架对象"
        if not obj.select_get():
            return None, "请先点击选中骨架对象"
        return obj, None

    def draw(self, context):
        layout = self.layout

        # 显示当前选中的骨架
        obj = context.active_object
        if obj and obj.type == "ARMATURE" and obj.select_get():
            layout.label(text=f"目标骨架: {obj.name}", icon="ARMATURE_DATA")
        else:
            layout.label(text="请先选中一个骨架!", icon="ERROR")

        if self.filepath:
            box = layout.box()
            box.label(text="将导入以下动画:")
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                animations = data.get("animations", {})
                if animations:
                    for name in animations.keys():
                        row = box.row()
                        row.label(text=name, icon="ACTION")
                    box.label(text=f"共 {len(animations)} 个动画", icon="INFO")
                else:
                    box.label(text="文件中没有找到动画", icon="ERROR")
            except Exception:
                box.label(text="无法读取文件", icon="ERROR")

    def execute(self, context):
        armature, error = self._validate_armature_selection(context)
        if error:
            self.report({"ERROR"}, error)
            return {"CANCELLED"}

        try:
            importer = BBAnimImporter(armature.name)
            imported_actions = importer.import_all_animations(self.filepath)

            if imported_actions:
                self.report(
                    {"INFO"}, f"成功导入 {len(imported_actions)} 个动画到 Action Editor"
                )
                return {"FINISHED"}
            else:
                self.report({"ERROR"}, "文件中没有找到动画")
                return {"CANCELLED"}
        except KeyError as e:
            self.report({"ERROR"}, f"骨架不存在: {e}")
            return {"CANCELLED"}
        except Exception as e:
            self.report({"ERROR"}, f"导入失败: {e}")
            return {"CANCELLED"}

    def invoke(self, context, event):
        _, error = self._validate_armature_selection(context)
        if error:
            self.report({"WARNING"}, "请先点击选中一个骨架对象再导入动画")
            return {"CANCELLED"}

        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}


def menu_func_import(self, context):
    self.layout.operator(
        IMPORT_OT_bedrock_anim.bl_idname, text="Bedrock Animation"
    )


classes = (IMPORT_OT_bedrock_anim,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
