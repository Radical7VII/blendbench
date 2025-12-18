# Blendbench

一个用于打通 Blender 与 Blockbench 工作流的 Blender 插件，专注于 Minecraft Bedrock Edition 模型和动画的导入/导出。

## 功能特性

- **Bedrock 模型导入** - 将 Minecraft Bedrock Edition 模型（.json）导入到 Blender
- **Bedrock 动画导入** - 从 Blockbench 动画文件导入动画到 Blender
- **Bedrock 动画导出** - 将 Blender 骨架动画导出为 Blockbench 兼容的 JSON 格式

### 支持的插值类型

| Blender | Blockbench |
|---------|------------|
| CONSTANT | step |
| LINEAR | linear |
| BEZIER | linear (通过烘焙转换) |

## 安装

### 系统要求

- Blender 4.2.0 或更高版本

### 安装步骤

1. 下载本仓库的 ZIP 文件
2. 打开 Blender，进入 `编辑` > `偏好设置` > `扩展`
3. 点击右上角下拉菜单，选择 `从磁盘安装...`
4. 选择下载的 ZIP 文件
5. 启用 `Blendbench` 插件

## 使用方法

### 导入模型

1. 在 Blender 中，选择 `文件` > `导入` > `Bedrock Model (.json)`
2. 选择你的 Minecraft Bedrock 模型文件
3. 模型将自动导入并创建对应的骨架结构

### 导入动画

1. 选择已导入的模型骨架
2. 选择 `文件` > `导入` > `Bedrock Animation (.json)`
3. 选择动画文件，动画将应用到当前骨架

### 导出动画

1. 选择要导出动画的骨架
2. 选择 `文件` > `导出` > `Bedrock Animation (.json)`
3. 设置导出选项并保存
