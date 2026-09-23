# -*- coding: utf-8 -*-
"""
审查台后端实现 (serve_api/)

`serve_audio.py`（CLI 入口，含 `main()`/参数解析）的实现细节拆到这里，拆分原则同
`prompt_build/`/`audit/` 两个先例——脚本长过一个门槛就落成同名职责清楚的子包，不再堆进
单一文件。各模块职责：

    paths.py    仓库内相对本包的固定路径（工具目录/系统级目录/仓库根/前端静态目录）
    model.py    数据模型 + 目录扫描（Entry、scan/find、部卷容器、部卷章标题与状态推导）
    files.py    单文件级操作：分组列表、读写越界校验、kind/desc/icon 推导
    payload.py  /api/book /api/level /api/backfill* 的响应体拼装
    jobs.py     /api/jobs 后台任务（子进程编排、去重、临时 review 配置）
    feed.py     播客 RSS
    http.py     HTTP Handler、路由分发、`make_handler()`

模块间只单向依赖（model ← files ← payload；model ← jobs；上述全部 ← http），
不循环引用。
"""
