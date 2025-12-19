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


class SyncServerState:
    """服务器状态管理"""
    is_running: bool = False
    client_count: int = 0
    last_message: str = ""


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
    SYNC_PT_main_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    # 停止服务器
    if server_state.is_running:
        stop_server()

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
