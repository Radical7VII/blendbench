/**
 * Blender Sync Plugin for Blockbench
 *
 * 与 Blender 的 blendbench 插件进行实时通信
 *
 * 安装方法:
 * 1. 在 Blockbench 中打开 File > Plugins
 * 2. 点击 "Load Plugin from File"
 * 3. 选择此文件
 */

(function() {
    'use strict';

    const PLUGIN_ID = 'blendbench_sync';
    const PLUGIN_VERSION = '1.0.0';
    const DEFAULT_HOST = 'localhost';
    const DEFAULT_PORT = 9876;

    let socket = null;
    let isConnected = false;

    /**
     * 连接到 Blender 服务器
     */
    function connect(host = DEFAULT_HOST, port = DEFAULT_PORT) {
        if (socket) {
            disconnect();
        }

        try {
            const net = require('net');
            socket = new net.Socket();

            socket.connect(port, host, function() {
                isConnected = true;
                Blockbench.showQuickMessage('已连接到 Blender', 2000);
                console.log(`[BlenderSync] 已连接到 ${host}:${port}`);
            });

            let buffer = '';

            socket.on('data', function(data) {
                buffer += data.toString();
                const lines = buffer.split('\n');
                buffer = lines.pop();

                for (const line of lines) {
                    if (line.trim()) {
                        try {
                            const message = JSON.parse(line);
                            handleMessage(message);
                        } catch (e) {
                            console.error('[BlenderSync] JSON 解析错误:', e);
                        }
                    }
                }
            });

            socket.on('close', function() {
                isConnected = false;
                socket = null;
                Blockbench.showQuickMessage('与 Blender 断开连接', 2000);
                console.log('[BlenderSync] 连接已关闭');
            });

            socket.on('error', function(error) {
                console.error('[BlenderSync] 连接错误:', error.message);
                Blockbench.showQuickMessage('连接失败: ' + error.message, 3000);
                isConnected = false;
                socket = null;
            });

        } catch (error) {
            console.error('[BlenderSync] 无法创建连接:', error);
            Blockbench.showQuickMessage('无法连接: ' + error.message, 3000);
        }
    }

    /**
     * 断开连接
     */
    function disconnect() {
        if (socket) {
            socket.destroy();
            socket = null;
        }
        isConnected = false;
    }

    /**
     * 发送消息到 Blender
     */
    function sendMessage(message) {
        if (!socket || !isConnected) {
            console.warn('[BlenderSync] 未连接，无法发送消息');
            return false;
        }

        try {
            const data = JSON.stringify(message) + '\n';
            socket.write(data);
            return true;
        } catch (error) {
            console.error('[BlenderSync] 发送失败:', error);
            return false;
        }
    }

    /**
     * 处理来自 Blender 的消息
     */
    function handleMessage(message) {
        console.log('[BlenderSync] 收到消息:', message);

        switch (message.type) {
            case 'welcome':
                console.log('[BlenderSync] 服务器欢迎消息:', message.message);
                break;

            case 'pong':
                Blockbench.showQuickMessage('Pong!', 1000);
                break;

            case 'test':
                Blockbench.showQuickMessage(
                    `来自 Blender: ${message.message} (帧 ${message.frame})`,
                    3000
                );
                break;

            case 'animation_sync':
                // TODO: 实现动画同步
                console.log('[BlenderSync] 收到动画数据:', message.animation_name);
                Blockbench.showQuickMessage(
                    `收到动画: ${message.animation_name}`,
                    2000
                );
                break;

            default:
                console.log('[BlenderSync] 未知消息类型:', message.type);
        }
    }

    /**
     * 显示连接对话框
     */
    function showConnectDialog() {
        new Dialog({
            id: 'blender_sync_connect',
            title: '连接到 Blender',
            form: {
                host: {
                    label: '主机地址',
                    type: 'text',
                    value: DEFAULT_HOST
                },
                port: {
                    label: '端口',
                    type: 'number',
                    value: DEFAULT_PORT,
                    min: 1024,
                    max: 65535
                }
            },
            onConfirm: function(formData) {
                connect(formData.host, formData.port);
            }
        }).show();
    }

    /**
     * 发送 Ping 测试
     */
    function sendPing() {
        if (sendMessage({ type: 'ping' })) {
            Blockbench.showQuickMessage('Ping 已发送', 1000);
        }
    }

    // ============== 插件注册 ==============

    BBPlugin.register(PLUGIN_ID, {
        title: 'Blender Sync',
        author: 'Blendbench',
        description: '与 Blender 实时同步骨骼动画和姿态',
        icon: 'sync',
        version: PLUGIN_VERSION,
        variant: 'both',

        onload() {
            new Action('blender_sync_connect', {
                name: '连接 Blender...',
                description: '连接到 Blender 同步服务器',
                icon: 'link',
                click: function() {
                    if (isConnected) {
                        Blockbench.showQuickMessage('已经连接', 1500);
                    } else {
                        showConnectDialog();
                    }
                }
            });

            new Action('blender_sync_disconnect', {
                name: '断开 Blender',
                description: '断开与 Blender 的连接',
                icon: 'link_off',
                click: function() {
                    if (isConnected) {
                        disconnect();
                        Blockbench.showQuickMessage('已断开连接', 1500);
                    } else {
                        Blockbench.showQuickMessage('未连接', 1500);
                    }
                }
            });

            new Action('blender_sync_ping', {
                name: 'Ping Blender',
                description: '发送 Ping 测试连接',
                icon: 'speed',
                click: function() {
                    if (isConnected) {
                        sendPing();
                    } else {
                        Blockbench.showQuickMessage('未连接到 Blender', 1500);
                    }
                }
            });

            MenuBar.addAction('blender_sync_connect', 'tools');
            MenuBar.addAction('blender_sync_disconnect', 'tools');
            MenuBar.addAction('blender_sync_ping', 'tools');

            console.log('[BlenderSync] 插件已加载');
        },

        onunload() {
            disconnect();
            console.log('[BlenderSync] 插件已卸载');
        }
    });

})();
