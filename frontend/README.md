# Wuwa Pull Planner 前端

Vue 3 + TypeScript + Vite 的匿名使用界面。默认把账号资产和偏好保存到浏览器 `localStorage`，不会持久化到服务端。角色通过目录选择，共鸣链和专武精炼由用户手动维护；四星角色默认按 6 链处理。

```powershell
npm.cmd ci
```

上面的安装命令仅用于首次运行或依赖变更。执行前先在本项目的 Vite 终端按 `Ctrl+C` 停止服务，避免 Windows 锁住 `esbuild.exe` 导致 `EPERM`。安装失败后应停止服务并重新执行 `npm.cmd ci`，恢复完整依赖后再启动。

日常启动，在 `frontend` 目录单独执行：

```powershell
npm.cmd run dev -- --host 127.0.0.1
```

开发服务器会把 `/api` 代理到 `http://localhost:8000`。生产镜像由 `Dockerfile` 构建，Nginx 同源代理 `/api` 到 Compose 中名为 `backend` 的服务。

前端通过 `/api/catalog` 获取可选角色目录，通过 `/api/reference-dps` 显示全配队 DPS 原图；优化请求只发送当前可见角色的名称、共鸣链和专武精炼。
