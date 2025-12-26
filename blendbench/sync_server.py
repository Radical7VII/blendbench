# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTIBILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

"""
Blender-Blockbench 实时同步服务器模块

提供 TCP 服务器，用于与 Blockbench 进行双向通信。
"""

import bpy
import asyncio
import threading
import json
from bpy.props import IntProperty

# 全局状态
_server_instance = None
_event_loop = None
_server_thread = None
_connected_clients = set()
_auto_sync_enabled = False
_transform_watch = None  # 变换监控器


class SyncServerState:
    """服务器状态管理"""
    is_running: bool = False
    client_count: int = 0
    last_message: str = ""
    auto_sync: bool = False


server_state = SyncServerState()


async def client_handler(reader, writer):
    """处理单个客户端连接"""
    global _connected_clients

    addr = writer.get_extra_info('peername')
    print(f"[Sync] 客户端连接: {addr}")
    _connected_clients.add(writer)
    server_state.client_count = len(_connected_clients)

    # 发送欢迎消息
    welcome = json.dumps({
        "type": "welcome",
        "message": "Connected to Blender Sync Server"
    })
    writer.write(welcome.encode() + b'\n')
    await writer.drain()

    try:
        while True:
            data = await reader.readline()
            if not data:
                break

            try:
                message = json.loads(data.decode().strip())
                server_state.last_message = f"收到: {message.get('type', 'unknown')}"
                print(f"[Sync] 收到消息: {message}")

                # 处理来自 Blockbench 的消息
                await handle_client_message(message, writer)

            except json.JSONDecodeError:
                print(f"[Sync] 无效的 JSON: {data}")

    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"[Sync] 连接错误: {e}")
    finally:
        _connected_clients.discard(writer)
        server_state.client_count = len(_connected_clients)
        writer.close()
        await writer.wait_closed()
        print(f"[Sync] 客户端断开: {addr}")


async def handle_client_message(message: dict, writer):
    """处理来自客户端的消息"""
    msg_type = message.get("type", "")

    if msg_type == "ping":
        response = json.dumps({"type": "pong"})
        writer.write(response.encode() + b'\n')
        await writer.drain()

    elif msg_type == "echo":
        response = json.dumps({
            "type": "echo_reply",
            "data": message.get("data", "")
        })
        writer.write(response.encode() + b'\n')
        await writer.drain()


async def broadcast_message(message: dict):
    """向所有连接的客户端广播消息"""
    if not _connected_clients:
        return

    data = json.dumps(message).encode() + b'\n'
    for writer in list(_connected_clients):
        try:
            writer.write(data)
            await writer.drain()
        except Exception as e:
            print(f"[Sync] 广播失败: {e}")


async def run_server(host: str, port: int):
    """运行服务器"""
    global _server_instance

    server = await asyncio.start_server(
        client_handler, host, port
    )
    _server_instance = server
    server_state.is_running = True

    addr = server.sockets[0].getsockname()
    print(f"[Sync] 服务器启动: {addr[0]}:{addr[1]}")

    async with server:
        await server.serve_forever()


def server_thread_func(host: str, port: int):
    """服务器线程入口"""
    global _event_loop

    _event_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_event_loop)

    try:
        _event_loop.run_until_complete(run_server(host, port))
    except asyncio.CancelledError:
        pass
    finally:
        _event_loop.close()
        _event_loop = None
        server_state.is_running = False


def start_server(host: str = "localhost", port: int = 9876):
    """启动服务器（非阻塞）"""
    global _server_thread

    if server_state.is_running:
        print("[Sync] 服务器已在运行")
        return False

    _server_thread = threading.Thread(
        target=server_thread_func,
        args=(host, port),
        daemon=True
    )
    _server_thread.start()
    return True


def stop_server():
    """停止服务器"""
    global _server_instance, _event_loop, _server_thread

    if not server_state.is_running:
        print("[Sync] 服务器未运行")
        return False

    if _server_instance and _event_loop:
        _event_loop.call_soon_threadsafe(_server_instance.close)

    server_state.is_running = False
    _server_instance = None
    print("[Sync] 服务器已停止")
    return True


def send_message(message: dict):
    """从主线程发送消息到所有客户端"""
    if not _event_loop or not server_state.is_running:
        return False

    asyncio.run_coroutine_threadsafe(
        broadcast_message(message),
        _event_loop
    )
    return True


# ============== 自动同步处理器 ==============

class TransformWatch:
    """
    变换监控器 - 使用定时器 + 防抖机制检测操作确认

    参考 Freebambase 的 UVWatch 实现:
    - 定期检查变换状态
    - 通过哈希值对比检测变化
    - 防抖延迟确保只在操作确认后发送
    """
    PERIOD = 0.1  # 检查周期 (秒)
    DEBOUNCE = 0.3  # 防抖延迟 (秒) - 变化停止后等待这么久才发送

    def __init__(self):
        self.last_transforms = {}  # 上次发送的变换状态
        self.pending_transforms = {}  # 当前检测到的变换状态
        self.idle_time = 0  # 空闲时间计数
        self.has_pending = False  # 是否有待发送的变化
        self.running = False

    def start(self):
        """启动监控"""
        if not self.running:
            self.running = True
            self.last_transforms.clear()
            self.pending_transforms.clear()
            self.idle_time = 0
            self.has_pending = False
            bpy.app.timers.register(self._timer_callback)
            print("[Sync] 变换监控已启动")

    def stop(self):
        """停止监控"""
        if self.running:
            self.running = False
            try:
                bpy.app.timers.unregister(self._timer_callback)
            except ValueError:
                pass
            self.last_transforms.clear()
            self.pending_transforms.clear()
            print("[Sync] 变换监控已停止")

    def _get_transforms_snapshot(self):
        """获取当前所有 MESH/ARMATURE 对象的变换快照"""
        snapshot = {}
        try:
            for obj in bpy.data.objects:
                if obj.type in {'MESH', 'ARMATURE'}:
                    snapshot[obj.name] = {
                        "name": obj.name,
                        "type": obj.type,
                        "location": tuple(round(v, 5) for v in obj.location),
                        "rotation": tuple(round(v, 5) for v in obj.rotation_euler),
                        "scale": tuple(round(v, 5) for v in obj.scale)
                    }
        except Exception:
            pass
        return snapshot

    def _get_hash(self, snapshot):
        """计算快照的哈希值"""
        if not snapshot:
            return 0
        # 将快照转为可哈希的元组
        items = tuple(sorted(
            (k, v["location"], v["rotation"], v["scale"])
            for k, v in snapshot.items()
        ))
        return hash(items)

    def _timer_callback(self):
        """定时器回调"""
        if not self.running:
            return None  # 停止定时器

        if not server_state.is_running or not server_state.auto_sync:
            return self.PERIOD

        if server_state.client_count == 0:
            return self.PERIOD

        # 获取当前变换快照
        current_snapshot = self._get_transforms_snapshot()
        current_hash = self._get_hash(current_snapshot)
        pending_hash = self._get_hash(self.pending_transforms)

        if current_hash != pending_hash:
            # 变换正在变化中，更新 pending 并重置空闲计时
            self.pending_transforms = current_snapshot
            self.idle_time = 0
            self.has_pending = True
        elif self.has_pending:
            # 变换已稳定，累加空闲时间
            self.idle_time += self.PERIOD

            if self.idle_time >= self.DEBOUNCE:
                # 达到防抖阈值，发送变化
                self._send_changes()
                self.has_pending = False
                self.idle_time = 0

        return self.PERIOD  # 继续定时器

    def _send_changes(self):
        """发送变换变化"""
        last_hash = self._get_hash(self.last_transforms)
        pending_hash = self._get_hash(self.pending_transforms)

        if last_hash == pending_hash:
            return  # 没有实际变化

        # 找出变化的对象
        changed_objects = []
        for name, data in self.pending_transforms.items():
            last_data = self.last_transforms.get(name)
            if last_data is None or (
                data["location"] != last_data["location"] or
                data["rotation"] != last_data["rotation"] or
                data["scale"] != last_data["scale"]
            ):
                changed_objects.append({
                    "name": data["name"],
                    "type": data["type"],
                    "location": list(data["location"]),
                    "rotation": list(data["rotation"]),
                    "scale": list(data["scale"])
                })

        if changed_objects:
            message = {
                "type": "transform_update",
                "objects": changed_objects
            }
            print(f"[Sync] 操作确认，发送变换更新: {len(changed_objects)} 个对象")
            send_message(message)

        # 更新 last_transforms
        self.last_transforms = self.pending_transforms.copy()


def enable_auto_sync():
    """启用自动同步"""
    global _auto_sync_enabled, _transform_watch
    if not _auto_sync_enabled:
        _transform_watch = TransformWatch()
        _transform_watch.start()
        _auto_sync_enabled = True
        server_state.auto_sync = True
        print("[Sync] 自动同步已启用")


def disable_auto_sync():
    """禁用自动同步"""
    global _auto_sync_enabled, _transform_watch
    if _auto_sync_enabled:
        if _transform_watch:
            _transform_watch.stop()
            _transform_watch = None
        _auto_sync_enabled = False
        server_state.auto_sync = False
        print("[Sync] 自动同步已禁用")


# ============== Blender 操作符 ==============

class SYNC_OT_start_server(bpy.types.Operator):
    """启动同步服务器"""
    bl_idname = "sync.start_server"
    bl_label = "启动同步服务器"
    bl_description = "启动服务器以与 Blockbench 通信"

    port: IntProperty(
        name="端口",
        default=9876,
        min=1024,
        max=65535
    )

    def execute(self, context):
        if start_server(port=self.port):
            self.report({'INFO'}, f"服务器已启动，端口: {self.port}")
        else:
            self.report({'WARNING'}, "服务器已在运行")
        return {'FINISHED'}


class SYNC_OT_stop_server(bpy.types.Operator):
    """停止同步服务器"""
    bl_idname = "sync.stop_server"
    bl_label = "停止同步服务器"
    bl_description = "停止服务器"

    def execute(self, context):
        if stop_server():
            self.report({'INFO'}, "服务器已停止")
        else:
            self.report({'WARNING'}, "服务器未运行")
        return {'FINISHED'}


class SYNC_OT_send_test(bpy.types.Operator):
    """发送测试消息"""
    bl_idname = "sync.send_test"
    bl_label = "发送测试消息"
    bl_description = "向所有连接的 Blockbench 客户端发送测试消息"

    def execute(self, context):
        if not server_state.is_running:
            self.report({'ERROR'}, "服务器未运行")
            return {'CANCELLED'}

        if server_state.client_count == 0:
            self.report({'WARNING'}, "没有连接的客户端")
            return {'CANCELLED'}

        message = {
            "type": "test",
            "message": "Hello from Blender!",
            "frame": context.scene.frame_current
        }

        if send_message(message):
            self.report({'INFO'}, "测试消息已发送")

        return {'FINISHED'}


class SYNC_OT_toggle_auto_sync(bpy.types.Operator):
    """切换自动同步"""
    bl_idname = "sync.toggle_auto_sync"
    bl_label = "切换自动同步"
    bl_description = "启用/禁用对象变换时自动同步到 Blockbench"

    def execute(self, context):
        if not server_state.is_running:
            self.report({'ERROR'}, "服务器未运行")
            return {'CANCELLED'}

        if server_state.auto_sync:
            disable_auto_sync()
            self.report({'INFO'}, "自动同步已禁用")
        else:
            enable_auto_sync()
            self.report({'INFO'}, "自动同步已启用")

        return {'FINISHED'}


# ============== UI 面板 ==============

class SYNC_PT_main_panel(bpy.types.Panel):
    """同步服务器控制面板"""
    bl_label = "Blockbench 同步"
    bl_idname = "SYNC_PT_main_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Sync'

    def draw(self, context):
        layout = self.layout

        # 状态显示
        box = layout.box()
        row = box.row()
        if server_state.is_running:
            row.label(text="状态: 运行中", icon='PLAY')
        else:
            row.label(text="状态: 已停止", icon='PAUSE')

        row = box.row()
        row.label(text=f"客户端: {server_state.client_count}")

        # 控制按钮
        layout.separator()

        if not server_state.is_running:
            layout.operator("sync.start_server", icon='PLAY')
        else:
            layout.operator("sync.stop_server", icon='PAUSE')

        # 自动同步开关
        layout.separator()
        box = layout.box()
        row = box.row()
        if server_state.auto_sync:
            row.label(text="自动同步: 开启", icon='CHECKBOX_HLT')
        else:
            row.label(text="自动同步: 关闭", icon='CHECKBOX_DEHLT')

        row = layout.row()
        row.enabled = server_state.is_running
        if server_state.auto_sync:
            row.operator("sync.toggle_auto_sync", text="禁用自动同步", icon='PAUSE')
        else:
            row.operator("sync.toggle_auto_sync", text="启用自动同步", icon='PLAY')

        # 测试按钮
        layout.separator()
        row = layout.row()
        row.enabled = server_state.is_running and server_state.client_count > 0
        row.operator("sync.send_test", icon='EXPORT')


# ============== 注册 ==============

classes = (
    SYNC_OT_start_server,
    SYNC_OT_stop_server,
    SYNC_OT_send_test,
    SYNC_OT_toggle_auto_sync,
    SYNC_PT_main_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    # 禁用自动同步
    disable_auto_sync()

    # 停止服务器
    if server_state.is_running:
        stop_server()

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
