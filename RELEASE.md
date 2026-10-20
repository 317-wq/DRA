# 发布清单

面向维护者。目标是：**打一个 tag，GitHub 自动产出 Release；PyPI 可选自动发布。**

## 1. 发布前

```bash
uv sync
uv run ruff check src tests
uv run pytest -q
uv run dra eval --all        # 质量基线（可选，纯本地）
```

## 2. 升版本

两处要一起改（当前未做单源版本号）：

- `pyproject.toml` 的 `version`
- `src/dra/__init__.py` 的 `__version__`

然后：

```bash
uv lock
uv build
```

确认 `dist/` 里出现 `dra-X.Y.Z.tar.gz` 与 `dra-X.Y.Z-py3-none-any.whl`。

## 3. 提交并打 tag

```bash
git add -A
git commit -m "chore: 发布 vX.Y.Z"
git push
git tag vX.Y.Z
git push origin vX.Y.Z
```

tag 推送后 `.github/workflows/release.yml` 会：

1. 构建 sdist + wheel；
2. 创建 GitHub Release 并附上构建产物；
3. 如果仓库变量 `PUBLISH_TO_PYPI=true`，发布到 PyPI。

## 4. PyPI 配置（一次性）

1. 在 <https://pypi.org> 注册项目名 `dra-research`（发行名；命令仍是 `dra`）；
2. 打开项目的 **Publishing → Trusted Publishers**，添加：
   - Owner: `317-wq`
   - Repository: `DRA`
   - Workflow: `release.yml`
   - Environment: `pypi`
3. GitHub 仓库 **Settings → Environments** 新建 `pypi`；
4. **Settings → Variables → Actions** 新建 `PUBLISH_TO_PYPI = true`。

之后每次 tag 都会自动发布，无需 token。

## 5. 手动发布（兜底）

```bash
uv publish --token <PYPI_TOKEN> dist/*.whl dist/*.tar.gz
```

或先发 TestPyPI：

```bash
uv publish --publish-url https://test.pypi.org/legacy/ --token <TEST_TOKEN> dist/*
```

## 6. 发布后验证

```bash
uvx --from dra-research dra version
uv tool install dra-research --force
dra doctor
```

Windows：

```powershell
uvx --from dra-research dra version
uv tool install dra-research --force
dra init
dra doctor
```

## 7. 升级与回滚

- 用户升级：`uv tool upgrade dra`（或 `uvx dra@latest`）
- 回滚：`uv tool install dra==X.Y.Z --force`
- 产物与配置在用户数据目录，升级不会动 `runs/` 与 `kb.sqlite3`。
