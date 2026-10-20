# MVP-1.0 · 分发与发布

> 基线：PROJECT.md v1.9
> 前置版本：MVP-0.8（质量约束）
> 日期：2026-10-21
> 状态：**v1.0.0 的 GitHub Release 已成功**；PyPI 因 `dra` 名称被占用，1.0.1 改用发行名 `dra-research`

## 1. 本版范围

**包含什么**：

1. **可发布元数据**：PyPI 所需的 license、authors、classifiers、keywords、项目 URL。
2. **许可证与变更记录**：MIT `LICENSE`、`CHANGELOG.md`（0.1 → 1.0）。
3. **Release 工作流**：推 `v*` tag → 自动构建 sdist/wheel → 创建 GitHub Release；
   仓库变量 `PUBLISH_TO_PYPI=true` 时用 Trusted Publishing 发到 PyPI。
4. **安装与升级文档**：`uv tool install dra`、`uvx dra`、GitHub 安装、Windows 步骤、升级/回滚。
5. **发布清单**：`RELEASE.md`（版本号、tag、PyPI 配置、手动兜底、发布后验证）。
6. **CI 增加构建检查**：每次 push 都跑 `uv build`，避免“测试过了但包坏了”。

**不包含什么**：

- 桌面安装包（`.exe` / `.dmg`）——当前形态是 CLI + 本地网页，PyPI 足够
- 自动更新器（升级走 `uv tool upgrade`）
- 代码签名（Windows SmartScreen 可能对 uv 安装的 exe 提示）

## 2. 用户路径

> 第一次：`uv tool install dra` → `dra init` → `dra doctor` → `dra run "..."`
> 想看界面：`dra ui`
> 升级：`uv tool upgrade dra`
> 回滚：`uv tool install dra==1.0.0 --force`

## 3. 相对基线的增量

- 打包 / 分发：新增 `LICENSE`、`CHANGELOG.md`、`RELEASE.md`、`.github/workflows/release.yml`；
  `pyproject.toml` 补全 PyPI 元数据；CI 增加 `uv build`。
- 接口：无变化（CLI 命令与数据格式保持兼容）。
- 其他：版本 1.0.0；README 安装章节改为 PyPI 优先。

## 4. 本版要实现的模块功能

- [x] **PyPI 元数据**：license / authors / classifiers / keywords / urls
- [x] **MIT LICENSE**
- [x] **CHANGELOG**（0.1 → 1.0）
- [x] **Release workflow**：tag 构建 + GitHub Release + 可选 PyPI
- [x] **RELEASE.md**：版本、tag、Trusted Publisher、手动发布、验证
- [x] **README 安装章节**：PyPI / uvx / GitHub 三条路径 + Windows + 升级回滚
- [x] **CI 构建检查**

## 5. 本版验收标准

- [x] `uv build` 产出 `dra-1.0.0.tar.gz` 与 `dra-1.0.0-py3-none-any.whl`
- [x] wheel 的 METADATA 含 version / license / urls / classifiers
- [x] wheel 内含 LICENSE
- [x] `uv tool install dist/dra-1.0.0-py3-none-any.whl` 后 `dra version` 输出 1.0.0
- [x] `uvx --from dist/...whl dra version` 可用
- [x] GitHub Actions release workflow 实跑（v1.0.0 已产出 Release）
- [ ] PyPI Trusted Publisher 配置并首次发布 `dra-research`

## 6. 本版风险与待办

- **首次 PyPI 发布**：必须先在 PyPI 建项目并配置 Trusted Publisher，否则 `publish-pypi` 会被变量关掉。
- **版本号两处维护**：`pyproject.toml` 与 `src/dra/__init__.py` 需同时改；已在 `RELEASE.md` 标注，后续可做单源。
- **Windows SmartScreen**：uv 安装的入口是 shim，一般无提示；若出现，选择“仍要运行”。
- **无桌面壳**：双击体验仍依赖终端；如确有需要，可在 1.1 评估 Tauri 壳。

## 7. 实测记录（2026-10-21）

| 项 | 结果 |
|---|---|
| `uv build` | sdist + wheel 构建成功 |
| wheel 元数据 | name=dra · version=1.0.0 · License=MIT · 项目 URL 齐全 |
| 从 wheel 安装 | `dra version` → `dra 1.0.0` |
| 单测 | 90 个通过 |
| CI | 增加 `uv build` 步骤 |
| v1.0.0 Release | GitHub Actions 成功，资产含 wheel 与 sdist |
| PyPI 名称 | `dra`、`deep-research-agent` 已被占用 → 1.0.1 改为 `dra-research` |
