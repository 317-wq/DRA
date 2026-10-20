# MVP-1.2 · Windows 桌面便携版

> 基线：PROJECT.md v1.11
> 前置版本：MVP-1.1（后端加固）
> 日期：2026-10-21
> 状态：**已完成**（便携版已在本机构建并启动验证）

## 1. 本版范围

**包含什么**：

1. **桌面入口** `dra.desktop`：本地起服务 + Tk 控制窗口（打开界面 / 退出）。
2. **首次运行配置向导**：没有 Key 时弹窗填写模型端点、模型名、两个 Key，保存到本地 `config.toml`。
3. **便携目录约定**：配置与数据都放在 exe 同级的 `data/`，解压到任意可写目录即可用。
4. **PyInstaller 打包**：`packaging/dra-desktop.spec` + `scripts/build_portable.ps1`，产出 `DRA/` 目录与 zip。
5. **Release 自动构建**：tag 触发的 `release.yml` 增加 `portable-windows` job，把 zip 附到 Release。

**不包含什么**：

- macOS / Linux 桌面包（先解决 Windows 用户）
- 代码签名（SmartScreen 仍会提示未知发布者）
- 自动更新（升级 = 替换程序文件，数据不动）
- Tauri/WebView 壳（Tk 控制窗口已够用，后续再评估）

## 2. 用户路径

> 下载 `DRA-portable-win64.zip` → 解压 → 双击 `DRA.exe`
> → 首次填两个 Key → 自动打开浏览器界面
> → 关闭浏览器不影响后台；控制窗口可重新打开或退出
> → 数据在 `data/`，换机整个文件夹拷走

## 3. 相对基线的增量

- 新模块：`dra/desktop.py`（服务线程 + Tk 窗口 + 配置向导 + 便携环境变量）。
- 打包：`packaging/dra-desktop.spec`、`packaging/PORTABLE-README.txt`、`scripts/build_portable.ps1`。
- 发布：`release.yml` 新增 `portable-windows` job。
- 其他：版本 1.2.0。

## 4. 本版要实现的模块功能

- [x] 桌面入口与本地服务生命周期
- [x] Tk 控制窗口（打开界面 / 退出 / 数据目录）
- [x] 首次运行配置向导（两个 Key）
- [x] 便携数据目录（`data/`）
- [x] PyInstaller 打包（windowed，无控制台）
- [x] 构建脚本与使用说明
- [x] Release 自动构建并上传 zip

## 5. 本版验收标准

- [x] `uv run pyinstaller packaging/dra-desktop.spec` 构建成功
- [x] `DRA.exe --headless --no-browser` 启动后 `/healthz` 返回 ok
- [x] 控制窗口能创建并显示（MainWindowTitle 非空）
- [x] 首次运行弹配置窗口，保存后写入 `data/config.toml`
- [x] 数据目录固定在 exe 同级 `data/`
- [x] 打包体积可控（约 50 MB 目录）
- [x] 101 个离线单测仍通过

## 6. 本版风险与待办

- **SmartScreen**：未签名，首次运行会提示“未知发布者”，说明里已写。
- **Tk 观感**：控制窗口是原生 Tk，不是现代 UI；研究界面仍是浏览器。
- **杀毒误报**：PyInstaller 单目录包偶尔被误判，需用户加白名单。
- **升级**：目前是替换 `DRA/` 程序文件，保留 `data/`；没有自动更新。
- **macOS/Linux**：未做桌面包；CLI + 浏览器仍是这两个平台的方式。

## 7. 实测记录（2026-10-21）

| 项 | 结果 |
|---|---|
| 构建 | PyInstaller 6.22.3 / Python 3.12，约 56 秒 |
| 目录体积 / zip | 49.4 MB / 25.2 MB |
| headless 启动 | `/healthz` → `{"status":"ok","version":"1.2.0"}` |
| 桌面窗口 | 无 Key 时弹出 `DRA · 首次配置` |
| 桌面日志 | `data/desktop.log` 记录启动与监听地址 |
| 单测 | 101 个通过 |
