# 2RTK NTRIP Caster Windows 启动说明

本说明介绍如何在 Windows 10/11 上启动 NTRIP Caster，包含 **Docker 方式** 和 **本地开发环境直接启动** 两种方式。

---

## 方式一：使用 Docker 启动（推荐）

### 前置要求
- Windows 10/11 专业版或企业版（推荐），或 Windows 11 家庭版（需 WSL 2 支持）
- 安装 [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/)
- 在 Docker Desktop 中启用 **WSL 2 后端**

### 1. 启动容器（最简单）

```powershell
# 在项目目录中执行
docker run -d `
  --name ntrip-caster `
  --restart unless-stopped `
  -p 2101:2101 `
  -p 5757:5757 `
  2rtk/ntripcaster:latest
```

> 说明：如需使用 Web 管理端口 `5757`，请确保防火墙放行该端口。NTRIP 服务使用 `2101` 端口。

### 2. 使用 Docker Compose 启动（推荐生产/测试）

```powershell
# 1. 进入项目目录
cd NTRIPcaster

# 2. 复制环境变量配置（可选）
copy .env.example .env

# 3. 启动服务
docker-compose up -d

# 4. 查看状态
docker-compose ps

# 5. 查看日志
docker-compose logs -f ntrip-caster
```

> 如需启用 Nginx 反向代理，请加上 `--profile nginx`：
> ```powershell
> docker-compose --profile nginx up -d
> ```

### 3. 停止与重启

```powershell
# 停止
docker-compose down

# 停止并删除数据卷（会删除日志和数据库，请谨慎）
docker-compose down -v

# 重启
docker-compose restart
```

---

## 方式二：本地开发环境直接启动

### 前置要求
- **Python 3.8 或更高版本**（推荐 3.11）
- **pip** 包管理工具
- 可选：Git（用于克隆项目）

### 1. 安装 Python 环境

1. 从 [python.org](https://www.python.org/downloads/) 下载 Windows 安装程序。
2. 安装时勾选 **Add Python to PATH**。
3. 验证安装：

```powershell
python --version
pip --version
```

### 2. 获取项目代码

```powershell
# 使用 Git 克隆（推荐）
git clone https://github.com/fuuhoo/NTRIPcaster.git
cd NTRIPcaster

# 或者直接在 GitHub 页面下载 ZIP 并解压
```

### 3. 创建虚拟环境（推荐）

```powershell
python -m venv venv

# 激活虚拟环境
.\venv\Scripts\activate

# 激活成功后，命令行提示符前会显示 (venv)
```

> 注意：Windows 上如果执行策略限制，可能出现 `无法加载脚本` 错误。可临时执行：
> ```powershell
> Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
> ```
> 然后重新激活虚拟环境。

### 4. 安装依赖

```powershell
# 确保处于虚拟环境（提示符前有 (venv)）
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
```

### 5. 准备配置文件

项目目录中已有 `config.ini`，可直接使用。如需自定义，可复制 `config.ini.example` 为 `config.ini` 并修改：

```powershell
copy config.ini.example config.ini
```

常见需要修改的配置项：

```ini
[ntrip]
port = 2101              # NTRIP 服务端口

[web]
port = 5757              # Web 管理端口

[database]
path = 2rtk.db           # SQLite 数据库路径，默认使用相对路径

[logging]
log_dir = logs           # 日志目录

[admin]
username = admin
password = admin123      # 生产环境请修改默认密码

[security]
allow_anonymous = false  # 是否允许匿名访问（无需认证即可连接 NTRIP）
```

> 注意：Windows 下路径建议使用相对路径（如 `2rtk.db`、`logs`）或正斜杠绝对路径（如 `D:/ntrip/2rtk.db`），避免反斜杠转义问题。

### 6. 启动程序

```powershell
# 使用默认配置文件（当前目录下的 config.ini）
python main.py

# 或者指定配置文件路径
python main.py --config D:/ntrip/config.ini
```

启动成功后会显示横幅信息，包含 NTRIP 端口和 Web 端口。首次启动时若看到 `RequestsDependencyWarning`（关于 urllib3 版本不匹配），属于环境警告，不影响程序正常运行。

### 7. 访问服务

- Web 管理界面：以 `config.ini` 中 `[web] port` 为准，默认是 `http://localhost:5757`
- NTRIP 服务：`ntrip://localhost:2101`（默认端口）
- 默认账号：`admin` / `admin123`

> Docker 部署时，`docker-compose.yml` 和官方镜像默认将 Web 管理端口映射为 `5757`，因此 Docker 方式访问 `http://localhost:5757`。

---

## 常见问题

### 1. 端口被占用或无法绑定

如果启动时报错 `NTRIP端口 2101 无法绑定` 或类似信息，可能原因：

- 端口被其他程序占用（如另一个 NTRIP Caster 实例）
- Windows 系统保留范围（如 Hyper-V、WSL2、Docker Desktop 会预留部分端口）

解决方法：

```powershell
# 查看端口占用
netstat -ano | findstr :2101
netstat -ano | findstr :5757

# 检查 Windows 系统是否预留了该端口（常见原因）
netsh int ipv4 show excludedportrange protocol=tcp

# 结束占用进程（请确认 PID 后再执行）
taskkill /PID <PID> /F
```

若发现端口被系统预留（Hyper-V 等），可：
1. 临时关闭 Hyper-V 预留：`netsh int ipv4 add excludedportrange protocol=tcp startport=2101 numberofports=1`（需要管理员权限）
2. 或修改 `config.ini` 中的端口为其他值（如 8090、9000）再启动。

> 提示：Docker 部署时，如果修改了 `config.ini` 内的端口，请同步修改 `docker-compose.yml` 或 `docker run` 的端口映射。

### 2. 防火墙拦截

Windows 防火墙首次启动 Python 程序时可能会拦截网络访问。请在弹出的提示中选择 **允许访问**，或手动在防火墙设置中放行对应端口。

### 3. Python 版本过低

项目需要 **Python 3.8+**。如果提示 `需要Python 3.7或更高版本`，请升级 Python。

### 4. 依赖安装失败

某些依赖（如 `pyproj`）需要编译环境。如果安装失败，可尝试下载 Windows 预编译 wheel：

```powershell
pip install pyproj --only-binary :all:
```

或者使用 [Christoph Gohlke 的预编译包](https://www.lfd.uci.edu/~gohlke/pythonlibs/)（如可用）。

### 5. 命令行窗口关闭后服务停止

本地开发环境直接启动时，如果关闭 PowerShell/CMD 窗口，程序会随之停止。若需要长期在后台运行，可考虑：
- 使用 Windows 任务计划程序或 NSSM 将 `python main.py` 注册为 Windows 服务
- 使用 PowerShell 在后台启动：

```powershell
Start-Process python -ArgumentList "main.py" -WindowStyle Hidden
```

### 6. 日志或数据库目录不存在

程序启动时会自动创建 `logs` 目录和 `2rtk.db` 文件。如果因权限问题失败，请确保当前用户对项目目录有写入权限。

---

## 快速命令参考

```powershell
# 本地开发环境启动
python -m venv venv
.\venv\Scripts\activate
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
python main.py

# 再次启动时只需激活虚拟环境并运行
# .\venv\Scripts\activate
# python main.py

# Docker 一键启动（PowerShell，反引号 ` 为换行符）
docker run -d `
  --name ntrip-caster `
  --restart unless-stopped `
  -p 2101:2101 `
  -p 5757:5757 `
  2rtk/ntripcaster:latest

# Docker 一键启动（CMD，单行命令）
docker run -d --name ntrip-caster --restart unless-stopped -p 2101:2101 -p 5757:5757 2rtk/ntripcaster:latest

# Docker Compose 启动
docker-compose up -d
```

---

## 相关文档

- [Docker 安装和使用教程](DOCKER-TUTORIAL.md)
- [Linux 系统原生安装教程](INSTALL-TUTORIAL.md)
- [README](README.md)
