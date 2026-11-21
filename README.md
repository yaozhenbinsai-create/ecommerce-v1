# 电商系统 1 · 商品创作与刊登工作台

这套工作台把供应商资料、SKU、研究证据、设计版本和刊登草稿串在一起。运营人员可以从一条商品记录进入设计修订，查看每次任务和对应资产，再按目标平台模板检查导出字段。本仓库对应系统的重建 1.0 代码。

## 在 Windows 上运行

准备 Python 3.14、MySQL Community Server 8.4 的程序文件。请在克隆目录打开 PowerShell。启动器会为本项目建立独立的 MySQL 数据目录，可通过 `-MySql` 指定 `mysqld.exe` 的绝对路径。

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m studio.admin_auth
.\start.ps1 -MySql "C:\mysql\bin\mysqld.exe"
```

第三条命令让你在终端输入两次自选密码。打开 `http://127.0.0.1:8770`，在浏览器登录窗口输入 `ADMIN` 和刚设置的密码。工作台只有这一位应用用户。重新执行同一条命令可以在本机更换密码。应用默认监听回环地址，MySQL 默认使用 13320 端口。`-Port` 和 `-DatabasePort` 可以改用空闲端口。

默认的 `data/` 保存商品、媒体、数据库和本机管理员密码校验值。要将数据放在其他目录，请先设置 `$env:ECOMMERCE1_DATA_DIR`，再执行管理员设置和启动命令。MySQL 初始化时生成的数据库服务凭据也保存在这个私有目录。可用 `-Prepare` 单独创建数据库结构。

## 开始一项工作

1. 打开商品资料页，建立供应商和商品，填写销售规格、来源与图片。
2. 在研究与设计页面核对证据、创建任务、查看生成状态和设计版本。图像和文本模型需要使用者配置。
3. 在刊登草稿中选择目标、检查资料和模板，再导出到本机。平台模板版本及具体字段组合需要分别验证。工程内的 Amazon US PILLOW 适配可以作为新增模板的例子。

## 配置自己的模型和研究模块

首次安装时模型、执行方案和备用列表均为空。商品资料可以直接使用。需要模型时，在本项目的数据目录手动建立 `model-connections.json`，其起始结构为 `{"models": [], "plans": []}`。每条模型记录填写正整数 `id`、显示名 `name`、实际模型标识 `model_identifier`、`capability`（`文本` 或 `图片`）、`protocol` 以及自己的 `base_url`。文本可选择 `chat_completions`、`responses` 或 `anthropic`，图片可选择 `images`。

模型的密钥使用本项目单独的环境变量 `STUDIO_MODEL_KEY_<编号>`。例如，若你自己分配的模型编号是 1，请在启动服务的 PowerShell 中执行以下命令，按提示输入自己的密钥。网页只展示连接状态。

```powershell
$credential = [System.Net.NetworkCredential]::new('', (Read-Host '模型密钥' -AsSecureString))
$env:STUDIO_MODEL_KEY_1 = $credential.Password
```

在「设置 → 任务分工」选择刚添加的模型。若希望按顺序使用自己选择的模型，可在 `plans` 中添加 `plan_type` 和有序的 `model_ids` 数组。文本支持「商品文案」和「通用」，图片支持「图片生成」和「图片精修」。参数文件和凭据都由此部署的维护者提供。

使用官方客户端时选择 `codex` 协议，并将 `STUDIO_CODEX_BRIDGE_SOURCE` 指向电商 2 公开副本的源码目录。官方客户端使用本项目的 `data/official-client/` 保存授权，可在本机以此目录为 `CODEX_HOME` 运行 `codex login`。需要研究功能时，将 `STUDIO_RESEARCH_SOURCE` 指向电商 3 公开副本的源码目录。研究模块直接读取公开来源。公网图片需要单独配置发布地址和素材。

## 参与完善

欢迎通过 Issue 提交平台模板差异、字段映射问题，以及研究或任务恢复的复现步骤。适合第一个 PR 的方向包括增加一个脱敏模板的映射测试、补充导入的字段提示、改善设计版本的回读显示。请用自行构造的样例说明预期结果，附上所改模块和验证命令。代码在 `studio/`，前端在 `studio/static/`。
